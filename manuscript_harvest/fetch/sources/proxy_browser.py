"""Last-resort tier: a real browser, optionally through the Stanford library proxy.

Two distinct jobs, both needing a browser but only one needing credentials:

1. **Public files behind a JavaScript challenge.** PMC fronts its `/bin/`
   downloads with a proof-of-work page no plain HTTP client can clear. Collecting
   those needs a browser but no login, and no proxy.
2. **Paywalled articles.** The publisher page is loaded through
   `https://stanford.idm.oclc.org/login?url=<publisher_url>`, relying on a session
   established once by hand.

On the authentication: Cardinal Key is a WebAuthn credential and Duo cannot be
scripted, so there is no honest way to automate the login. `interactive_login`
opens a headed browser, you complete SSO yourself, and the session persists in a
Playwright profile directory. Everything after that reuses it.

The DOI is resolved to a publisher URL *unproxied* first, and only the result is
wrapped in the proxy prefix -- prefixing `doi.org` itself would depend on doi.org
being a configured EZproxy host, which is not guaranteed.

Deliberately absent: any User-Agent spoofing. Publishers fingerprint automation,
and a fake UA on a real browser reads as more suspicious than the real one.
"""

import json
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import parse_qsl, unquote, urlparse

from ..validate import classify_denial, validate_pdf
from ..adapters import adapter_for
from ..adapters.base import FILE_EXTENSION
from ..adapters.publishers import PII_RX
from .base import (
    ROLE_LANDING,
    ROLE_PDF,
    ROLE_SUPPLEMENT,
    FetchedFile,
    Source,
    SourceResult,
)

_IMPORT_HINT = (
    "The proxy_browser tier needs Playwright, which is an optional dependency:\n"
    "    pip install playwright && python -m playwright install chromium\n"
    "To skip this tier entirely, run with --oa-only."
)

PMC_ARTICLE_URL = "https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/"

# Where a Cell Press paper actually lives. The DOI does not resolve here: EZproxy
# sends `linkinghub.elsevier.com/retrieve/pii/<PII>` to ScienceDirect, which serves
# automation a shell page -- and cell.com, the host a human downloads from, renders
# the same paper in full. Measured 2026-07-30 against the hand-fetched copies of
# 10.1016/j.cell.2021.04.038, 10.1016/j.ccell.2021.03.007 and
# 10.1016/j.xgen.2026.101304: the *existing* Elsevier adapter finds 6, 6 and 12
# supplement links there, which are exactly the sets saved by hand, and the article
# PDFs match the hand-fetched ones byte for byte in size.
#
# `/retrieve/pii/<PII>` is used rather than a journal URL because it redirects to
# the canonical `/<journal-slug>/fulltext/<PII>` on its own, so no map from journal
# title to slug has to be maintained. It goes through the proxy like everything
# else: unproxied cell.com answers with Cloudflare's interstitial.
CELL_PRESS_URL = "https://www.cell.com/retrieve/pii/{pii}"

# What to do about a dead session. `session_expired` names the cause and used to
# stop there, which is half an answer: a batch of paywalled papers printed the
# same diagnosis once per DOI and never said which command fixes it. The reported
# case was worse than unhelpful -- the user reached for `--headed`, which shows
# the browser during a fetch but never waits for a human, so Chrome opened on the
# Stanford login page and closed a second later, every time.
SESSION_REMEDY = "run 'manuscript-fetch login' to re-establish the proxy session"


def denial_problem(denial: str, url: str) -> str:
    """The problem line for a named refusal, carrying the remedy where there is one."""
    line = f"{denial} at {url}"
    if denial == "session_expired":
        line += f"; {SESSION_REMEDY}"
    return line


def sciencedirect_pdf_url(stub_url: str, pii: str) -> str:
    """ScienceDirect's PDF endpoint, on the origin the stub was served from.

    The same shape `ElsevierAdapter.find_pdf_url` builds, but constructed from an
    explicit URL rather than from `page.url`: by the time this is needed the page
    has navigated away to the cell.com retry, so the live page no longer carries the
    proxied ScienceDirect origin that keeps the entitlement.
    """
    parts = urlparse(stub_url)
    return (f"{parts.scheme}://{parts.netloc}/science/article/pii/{pii}"
            f"/pdfft?isDTMRedir=true&download=true")


def _stub_problem(adapter_name: str, url: str, why: Optional[str], headless: bool,
                  got_pdf: bool) -> str:
    """What to tell a user whose paper was served a stub.

    The advice has to match the obstacle. `--headed` was suggested for every stub,
    and for a journal cell.com does not carry that is simply the wrong thing to
    reach for -- the redirect left the proxy, and no amount of showing the browser
    changes which host holds the article. Measured on 10.1016/j.jhep.2019.01.003 and
    reported again on 10.1016/j.jhep.2020.05.039.
    """
    if (why or "").startswith("off_cell_press:"):
        host = why.split(":", 1)[1]
        cause = (f"cell.com carries Cell Press only, so the retry redirected to {host}, "
                 f"which is outside the proxy")
        # Only claim there is no route when there turned out not to be one. The PDF
        # endpoint is reached on the ScienceDirect origin, not on this host, so a
        # recovery does not contradict the redirect having left the proxy.
        if not got_pdf:
            cause += ". There is no proxied route to this journal's own site"
    elif why == "not_on_cell_press":
        # Deliberately states the conclusion, not just the observation. Both papers
        # this was measured on are Elsevier titles with no PMC deposit, so once
        # ScienceDirect stubs and cell.com does not carry the journal, no tier in
        # this package has a route left -- and a reader who is told only "HTTP 404"
        # will spend a headed run and a login finding that out.
        cause = ("cell.com carries Cell Press only and did not redirect the PII to a "
                 "journal path, so it does not hold this title. With ScienceDirect "
                 "serving automation a stub, no tier here has a route to this "
                 "article: it needs a download by hand")
    elif why == "no_pii":
        cause = "and carried no Elsevier PII to retry with"
    elif why == "navigation_failed":
        cause = "and the cell.com retry could not be loaded"
    elif headless:
        cause = "try --headed, though ScienceDirect stubs headed runs too"
    else:
        cause = "the page rendered but exposed no article content"

    tail = (". The article PDF was still recovered from the PDF endpoint directly; "
            "supplements were not" if got_pdf else "")
    return f"{adapter_name} served a stub page to this browser at {url}; {cause}{tail}"


def _pdf_rejected(url: str, status: str, meta: dict) -> str:
    """Why a downloaded PDF was not kept, in one line.

    The byte count is the useful part: `paywalled` at 4 KB is a stub page, the same
    status at 900 KB means the heuristics matched an article they should not have.
    """
    size = meta.get("bytes")
    measured = f", {size} bytes" if size is not None else ""
    return f"PDF at {url} rejected as '{status}'{measured}"


def pii_from(url: str) -> Optional[str]:
    """The Elsevier PII in a URL, or None."""
    found = PII_RX.search(url or "")
    return found.group(1) if found else None


def _import_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:  # pragma: no cover - depends on the environment
        raise ImportError(_IMPORT_HINT) from e
    return sync_playwright


def proxied_url(url: str, fetch_cfg: dict) -> str:
    """Wrap a publisher URL in the library proxy prefix, if enabled."""
    proxy = (fetch_cfg.get("proxy") or {})
    if not proxy.get("enabled", True):
        return url
    prefix = proxy.get("prefix") or ""
    if not prefix or url.startswith(prefix):
        return url
    return prefix + url


def _profile_dir(fetch_cfg: dict) -> Path:
    browser_cfg = fetch_cfg.get("browser") or {}
    return Path(os.path.expanduser(
        browser_cfg.get("profile_dir", "~/.manuscript-harvest/chrome-profile")
    ))


def state_path(fetch_cfg: dict) -> Path:
    """Where the cookie snapshot lives, next to the browser profile."""
    return _profile_dir(fetch_cfg).parent / "storage_state.json"


def session_saved(fetch_cfg: dict) -> bool:
    """Has `login` ever run against this profile?

    Only the existence of the snapshot is checked, never its age. EZproxy does not
    publish a session lifetime, so a guess would either nag through a working
    session or stay quiet through a dead one -- and `check` answers the live
    question properly. This exists for the one case it can settle without opening
    a browser: nobody has logged in here at all.
    """
    return state_path(fetch_cfg).exists()


def save_state(context, fetch_cfg: dict) -> Optional[Path]:
    """Snapshot cookies, including session cookies, to disk.

    This exists because a persistent Chrome profile is NOT sufficient on its own.
    Measured: after `login` succeeded, the profile held three `.idm.oclc.org`
    cookies, and they were gone after the next browser start -- EZproxy issues
    *session* cookies, which Chrome discards on restart. Playwright's
    `storage_state()` captures them (with `expires: -1`) so they can be re-injected
    into the next run, which is what actually keeps a library-proxy login alive.

    **The file this writes is a live credential.** Anyone holding it has your
    institutional library session until it expires -- no password, no second factor.
    It is the only secret this package creates, so it is worth saying here rather
    than only in the README: treat it like an SSH key, do not copy it between
    machines, and do not commit it.
    """
    target = state_path(fetch_cfg)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        context.storage_state(path=str(target))
        return target
    except Exception:
        return None


def _restore_state(context, fetch_cfg: dict) -> int:
    """Re-inject a saved cookie snapshot. Returns how many cookies were added."""
    source = state_path(fetch_cfg)
    if not source.exists():
        return 0
    try:
        cookies = json.loads(source.read_text()).get("cookies") or []
        if cookies:
            context.add_cookies(cookies)
        return len(cookies)
    except Exception:
        return 0


@contextmanager
def browser_context(fetch_cfg: dict, headless: Optional[bool] = None,
                    restore: bool = True):
    """A persistent Playwright context rooted at the configured profile dir.

    The on-disk profile carries the durable things (Duo device trust, publisher
    entitlement markers); the cookie snapshot carries the session cookies the
    profile drops. Both are needed.
    """
    sync_playwright = _import_playwright()
    browser_cfg = fetch_cfg.get("browser") or {}
    profile = _profile_dir(fetch_cfg)
    profile.mkdir(parents=True, exist_ok=True)
    if headless is None:
        headless = bool(browser_cfg.get("headless", True))
    channel = browser_cfg.get("channel") or None

    playwright = sync_playwright().start()
    context = None
    try:
        options = {
            "user_data_dir": str(profile),
            "headless": headless,
            "accept_downloads": True,
            "viewport": {"width": 1440, "height": 900},
        }
        try:
            context = playwright.chromium.launch_persistent_context(channel=channel, **options)
        except Exception:
            # Real Chrome is preferred (it clears bot checks that bundled
            # Chromium does not) but is not always installed.
            context = playwright.chromium.launch_persistent_context(**options)
        context.set_default_timeout(int(browser_cfg.get("nav_timeout_seconds", 60)) * 1000)
        if restore:
            _restore_state(context, fetch_cfg)
        yield context
    finally:
        if context is not None:
            try:
                context.close()
            except Exception:
                pass
        playwright.stop()


def _default_check_url(fetch_cfg: dict) -> str:
    return (fetch_cfg.get("browser") or {}).get(
        "check_url", "https://www.nature.com/articles/s41586-026-10510-x"
    )


# How long an unresponsive page is given before it is named rather than waited
# out. Read from config here rather than on the source, because `check_session`
# needs them too and is the command whose silence was the original complaint.
def settle_deadline(fetch_cfg: dict) -> float:
    return float((fetch_cfg.get("browser") or {}).get("settle_deadline_seconds", 20.0))


def content_deadline(fetch_cfg: dict) -> float:
    return float((fetch_cfg.get("browser") or {}).get("content_deadline_seconds", 12.0))


def stable_content(page, attempts: int = 4, deadline_seconds: float = 12.0) -> bytes:
    """`page.content()` that tolerates a page still navigating -- but not forever.

    EZproxy bounces through a couple of client-side redirects before landing on
    the publisher, and calling `content()` mid-flight raises
    "the page is navigating and changing the content". Retrying is the fix.

    The deadline bounds the retries, which is worth having when `content()` fails
    fast -- the ordinary EZproxy case, where it raises and the next attempt
    succeeds.

    It is NOT sufficient on its own, and the reason is worth stating because it
    is not obvious. `page.content()` takes no timeout argument, and measured
    2026-07-30 against a dead session, `page.set_default_timeout()` does not
    govern it either: with a 4s page default and a 12s deadline this still had
    not returned after 88s. On a document that never stops navigating -- which is
    what Stanford's self-submitting SAML2 POST form produces -- a single
    `content()` call is simply uninterruptible from the sync API. No loop around
    it can help.

    So callers must not reach here with a page they could already have named.
    `denial_before_reading` is the guard: `page.url` and `page.title()` answer
    instantly on exactly the pages where `content()` will not return, so the
    refusal is classified before the body is ever asked for.
    """
    deadline = time.monotonic() + deadline_seconds
    last_error = None
    for attempt in range(attempts):
        try:
            return page.content().encode("utf-8", "replace")
        except Exception as e:
            last_error = e
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            page.wait_for_load_state("load", timeout=min(2000.0, remaining * 1000))
        except Exception:
            time.sleep(min(0.5 * (attempt + 1), max(0.0, deadline - time.monotonic())))
    raise RuntimeError(f"could not read page content: {last_error}")


class _AlreadyIdentified(Exception):
    """The page named itself before its body was read.

    Carried as an exception so it unwinds the same `try` that guards navigation,
    rather than duplicating the teardown, and caught separately so a real
    navigation failure is still reported as one.
    """

    def __init__(self, denial: str, url: str):
        super().__init__(f"{denial} at {url}")
        self.denial = denial
        self.url = url


def denial_before_reading(page):
    """Name a refusal from `url` + `title` alone, before asking for the body.

    Returns `(denial, url)` with `denial` None when the page looks like an
    article and the body is worth reading.

    This is a guard, not an optimisation. `page.content()` cannot be interrupted
    on a document that never stops navigating (see `stable_content`), and that is
    exactly what an expired session produces -- so the only way not to hang on one
    is to recognise it without reading it. The two things that still answer are
    the URL and the title, and on an expired session the title is
    "Loading https://login.stanford.edu/idp/profile/SAML2/POST/SSO", which names
    the cause outright.

    Safe against false positives because it is the same `classify_denial` used on
    real bodies, given far less to match on: a healthy article page offers only
    its own URL and headline, neither of which contains an SSO host, a
    proof-of-work marker or a purchase phrase.
    """
    marker_url, marker_body = navigation_marker(page)
    return classify_denial(marker_url, marker_body), marker_url


def navigation_marker(page):
    """What can still be read from a document that will not stop navigating.

    Returns `(url, body)` shaped for `classify_denial`, so a page whose content
    is unreadable can still be named instead of reported as a bare failure.

    `page.url` and `page.title()` are what still answer. Measured on the expired
    session above: `content()` and `evaluate()` both hung indefinitely while
    `title()` came back instantly with
    "Loading https://login.stanford.edu/idp/profile/SAML2/POST/SSO".

    The title is folded into the *url* half deliberately. `classify_denial`
    matches `_SSO_HOSTS` against the URL string, and the URL here is still
    EZproxy's -- `stanford.idm.oclc.org/login?url=...`, which is not an SSO host.
    The IdP appears only in the title, because a navigating document is titled
    for where it is going rather than where it is. Without folding it in, the SSO
    bounce is invisible and the answer degrades to `not_a_pdf`/`navigation_failed`
    for what is really `session_expired` -- the most actionable status there is.
    """
    try:
        url = page.url or ""
    except Exception:
        url = ""
    try:
        title = page.title() or ""
    except Exception:
        title = ""
    body = f"<html><head><title>{title}</title></head><body>{url}</body></html>"
    return f"{url} {title}", body.encode("utf-8", "replace")


def settle_page(page, rounds: int = 3, timeout_ms: int = 15000,
                deadline_seconds: float = 20.0) -> str:
    """Wait out client-side redirects and return the settled URL.

    Necessary because several hops in this path are JS redirects rather than HTTP
    ones: EZproxy bounces to the rewritten host, and a DOI on an Elsevier journal
    resolves to `linkinghub.elsevier.com`, which is a redirect shim. Reading the
    DOM at `domcontentloaded` captures a page titled "Redirecting" with no article
    content in it -- which is what made the first Elsevier fetch report
    `page_not_parsed`.

    A page that never settles has to be given up on, not waited out. Stanford's
    SSO hop is a self-submitting SAML2 POST form, so on an expired session no
    round ever reaches `networkidle` and each one pays its full timeout twice
    over: measured at 31s before this deadline existed, all of it ahead of the
    first byte anyone could classify. The deadline caps the whole loop rather
    than each wait, because it is the total silence that matters.

    Healthy pages are untouched -- they settle in a second or two, well inside it.
    """
    deadline = time.monotonic() + deadline_seconds
    previous = None
    for _ in range(rounds):
        current = page.url
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            page.wait_for_load_state("networkidle", timeout=min(timeout_ms, remaining * 1000))
        except Exception:
            remaining = deadline - time.monotonic()
            if remaining > 0:
                try:
                    page.wait_for_load_state("load", timeout=min(5000.0, remaining * 1000))
                except Exception:
                    time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
        if page.url == current == previous:
            break
        previous = current
    return page.url


def _authenticated_yet(page, context=None, fetch_cfg=None) -> Tuple[bool, str]:
    """Is the article actually reachable, not merely advertised?

    Finding a PDF link is NOT evidence of access: publishers emit
    `citation_pdf_url` on paywalled articles too, because Google Scholar requires
    it. So when a context is available the PDF is downloaded and validated -- the
    only signal that distinguishes entitlement from a purchase page.
    """
    try:
        url = page.url
        cfg = fetch_cfg or {}
        # Ask what the page is before asking for its body: on an expired session
        # `content()` never returns, so reading first means never getting here.
        early, marker_url = denial_before_reading(page)
        if early:
            return False, f"{early} at {marker_url}"
        body = stable_content(page, deadline_seconds=content_deadline(cfg))
    except Exception as e:
        # An unreadable page is usually a page still navigating, and on an expired
        # session it navigates forever. Say which rather than "not readable yet":
        # this is the line `manuscript-fetch check` prints, and "session_expired"
        # tells you to run `login` where the generic wording tells you nothing.
        marker_url, marker_body = navigation_marker(page)
        denial = classify_denial(marker_url, marker_body)
        if denial:
            return False, f"{denial} at {marker_url}"
        return False, f"page not readable yet ({type(e).__name__})"

    denial = classify_denial(url, body)
    if denial:
        return False, f"{denial} at {url}"

    adapter = adapter_for(url)
    try:
        pdf_url = adapter.find_pdf_url(page, "")
    except Exception:
        pdf_url = None
    if not pdf_url:
        return False, f"at {url}, no PDF link yet (adapter={adapter.name})"

    if context is None:
        return False, f"found a PDF link at {url} but could not test it"

    try:
        response = context.request.get(pdf_url, headers={"Referer": url})
        content, status_code = response.body(), response.status
        content_type = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
    except Exception as e:
        return False, f"PDF link found but download failed ({type(e).__name__}: {e})"

    if status_code >= 400:
        return False, f"PDF link found but returned HTTP {status_code}: {pdf_url}"

    accepted, status, meta = validate_pdf(content, content_type=content_type, url=pdf_url)
    if accepted:
        return True, (f"downloaded and validated the PDF ({meta.get('pages')} pages, "
                      f"{meta.get('bytes')} bytes) via the {adapter.name} adapter")
    return False, f"PDF rejected as '{status}' ({meta.get('bytes')} bytes): {pdf_url}"


def interactive_login(fetch_cfg: dict, probe_url: Optional[str] = None,
                      timeout_seconds: int = 600) -> int:
    """Open a headed browser so the user can complete Stanford SSO by hand.

    Deliberately does NOT wait on a keypress. The browser window takes keyboard
    focus during login, so an `input()` prompt in the terminal silently swallows
    the Enter that never arrives. Instead this polls the page until it looks like
    the article, and also stops when you simply close the browser window --
    neither of which competes with the browser for focus.
    """
    target = proxied_url(probe_url or _default_check_url(fetch_cfg), fetch_cfg)
    print(
        "Opening a browser window.\n"
        "  1. Complete Stanford login (SUNet ID, Cardinal Key / Duo).\n"
        "  2. Nothing else to do -- this detects success on its own.\n"
        "     (If it does not, just close the browser window and the session is still saved.)\n"
        f"Target: {target}\n",
        flush=True,
    )

    detail = "no attempt made"
    succeeded = False
    saved_to = None
    with browser_context(fetch_cfg, headless=False) as context:
        page = context.new_page()
        try:
            page.goto(target, wait_until="domcontentloaded")
        except Exception as e:
            print(f"navigation warning: {e}", flush=True)

        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if not context.pages:
                detail = "browser window closed by user"
                break
            live = context.pages[-1]
            succeeded, detail = _authenticated_yet(live, context, fetch_cfg)
            if succeeded:
                break
            time.sleep(2)
        else:
            detail = f"timed out after {timeout_seconds}s ({detail})"

        # Snapshot before the context closes: session cookies die with it.
        saved_to = save_state(context, fetch_cfg)

    if succeeded:
        print(f"\nLogged in. {detail}", flush=True)
        print(f"Cookie snapshot saved to {saved_to}")
        print("Verify any time with:  manuscript-fetch check")
        return 0
    print(f"\nCould not confirm access: {detail}", flush=True)
    if saved_to:
        print(f"Cookies were still snapshotted to {saved_to}; try: "
              "manuscript-fetch check")
    return 1


def check_session(fetch_cfg: dict, probe_url: Optional[str] = None) -> Tuple[bool, str]:
    """Probe a known article and report whether the stored session still works."""
    target = proxied_url(probe_url or _default_check_url(fetch_cfg), fetch_cfg)
    try:
        with browser_context(fetch_cfg) as context:
            page = context.new_page()
            page.goto(target, wait_until="domcontentloaded")
            settle_page(page, deadline_seconds=settle_deadline(fetch_cfg))
            return _authenticated_yet(page, context, fetch_cfg)
    except ImportError as e:
        return False, str(e)
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


class ProxyBrowserSource(Source):
    name = "proxy_browser"

    def __init__(self, http, config: Optional[dict] = None):
        super().__init__(http, config)
        # The proof-of-work challenge is per-session, so clear it at most once.
        self._challenge_cleared = False

    @property
    def _settle_deadline(self) -> float:
        return settle_deadline(self.config)

    @property
    def _content_deadline(self) -> float:
        return content_deadline(self.config)

    def applies(self, ids) -> bool:
        # Useful either for a paywalled publisher page or for PMC's challenge.
        return bool(ids.landing_url or ids.doi or ids.pmcid)

    def fetch(self, ids, need_pdf: bool, need_supplements: bool) -> SourceResult:
        result = SourceResult(tier=self.name)
        try:
            with browser_context(self.config) as context:
                if need_supplements and ids.pmcid:
                    self._pmc_supplements(context, ids, result)
                still_need_supplements = need_supplements and not result.by_role(ROLE_SUPPLEMENT)
                if need_pdf or still_need_supplements:
                    self._publisher_page(
                        context, ids, result,
                        need_pdf=need_pdf,
                        need_supplements=still_need_supplements,
                    )
                # Refresh the snapshot so a working session keeps rolling forward
                # instead of expiring at whatever `login` captured.
                save_state(context, self.config)
        except ImportError as e:
            result.problems.append(str(e))
            result.note("browser", status="playwright_missing", detail=str(e))
            if need_pdf:
                result.pdf_status = "download_failed"
        except Exception as e:
            result.problems.append(f"browser tier failed: {type(e).__name__}: {e}")
            result.note("browser", status="error", error=f"{type(e).__name__}: {e}")
        return result

    # -- PMC (public, no proxy, no credentials) -----------------------------

    def _pmc_supplements(self, context, ids, result: SourceResult) -> None:
        url = PMC_ARTICLE_URL.format(pmcid=ids.pmcid)
        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded")
            referer = settle_page(page, deadline_seconds=self._settle_deadline)
            body = stable_content(page, deadline_seconds=self._content_deadline)

            # Measured: NCBI serves headless Chrome a reCAPTCHA interstitial
            # ("Checking your browser") with no article content, while plain HTTP
            # gets the page fine. A visible browser passes. Say that plainly
            # instead of reporting an empty supplement list.
            if classify_denial(referer, body) == "javascript_challenge":
                result.suppl_status = "page_not_parsed"
                result.problems.append(
                    "PMC served a bot check to the headless browser"
                )
                result.suppl_advice.append(
                    "re-run with --headed to collect the supplementary files PMC "
                    "would not serve headless"
                )
                result.note("pmc_page", url=url, status="bot_check_headless")
                return

            links, parsed = adapter_for(referer).find_supplements(page, ids.doi)
        except Exception as e:
            result.note("pmc_page", url=url, status="error", error=f"{type(e).__name__}: {e}")
            return
        finally:
            try:
                page.close()
            except Exception:
                pass

        if not parsed:
            result.suppl_status = "page_not_parsed"
            result.note("pmc_page", url=url, status="page_not_parsed")
            return
        if not links:
            result.note("pmc_page", url=url, status="no_supplements_listed")
            return

        fetched, attempted = self._download_all(context, links, referer, result, "pmc")
        if fetched and fetched == attempted:
            # `attempted` counts anchors the adapter recognised, not files PMC
            # holds, so this says "we got what we saw". See `store.SUPPL_SETTLED`.
            result.suppl_status = "fetched_unverified"
        else:
            result.suppl_status = "partial_failure"
        result.note("pmc_page", url=url, status=result.suppl_status,
                    listed=len(links), attempted=attempted, fetched=fetched)

    # -- publisher page (proxied) -------------------------------------------

    def _publisher_page(self, context, ids, result, need_pdf: bool, need_supplements: bool) -> None:
        landing = ids.landing_url or f"https://doi.org/{ids.doi}"
        target = proxied_url(landing, self.config)

        page = context.new_page()
        try:
            page.goto(target, wait_until="domcontentloaded")
            # Both EZproxy and Elsevier's linkinghub redirect via JavaScript, so
            # let the page settle before the adapter looks at it.
            final_url = settle_page(page, deadline_seconds=self._settle_deadline)
            # Ask what it is before asking for the body. On an expired session
            # `content()` never returns at all, so reading first means hanging
            # instead of reporting -- see `denial_before_reading`.
            early, marker_url = denial_before_reading(page)
            if early:
                raise _AlreadyIdentified(early, marker_url)
            body = stable_content(page, deadline_seconds=self._content_deadline)
        except _AlreadyIdentified as named:
            result.note("landing", url=target, status=named.denial,
                        final_url=named.url, detail="named without reading the body")
            result.problems.append(denial_problem(named.denial, named.url))
            if need_pdf:
                result.pdf_status = named.denial
            if need_supplements:
                result.suppl_status = "page_not_parsed"
            try:
                page.close()
            except Exception:
                pass
            return
        except Exception as e:
            # Before settling for `download_failed`, ask the page what it is. A
            # document that never stops navigating is unreadable but not
            # anonymous, and on an expired session it is an SSO bounce -- which
            # is a cause the user can act on, unlike "navigation failed".
            marker_url, marker_body = navigation_marker(page)
            denial = classify_denial(marker_url, marker_body)
            result.note("landing", url=target, status=denial or "navigation_failed",
                        final_url=marker_url, error=f"{type(e).__name__}: {e}")
            if denial:
                result.problems.append(denial_problem(denial, marker_url))
            if need_pdf:
                result.pdf_status = denial or "download_failed"
            if need_supplements:
                # Nothing was looked at, so nothing licenses "none".
                result.suppl_status = "page_not_parsed"
            try:
                page.close()
            except Exception:
                pass
            return

        # Keep the page we saw; it is the only way to debug an adapter later.
        result.files.append(
            FetchedFile(role=ROLE_LANDING, name="landing.html", content=body, url=final_url)
        )

        denial = classify_denial(final_url, body)

        if denial == "proxy_not_configured" and target != landing:
            # EZproxy having no stanza for a host frequently means the host needs
            # no proxy at all: Frontiers is fully open access, yet
            # 10.3389/fdmed.2021.806294 failed outright because we insisted on
            # proxying it. Retry the publisher directly before giving up.
            result.note("landing", url=target, status="proxy_not_configured",
                        detail="retrying without the proxy")
            try:
                page.goto(landing, wait_until="domcontentloaded")
                final_url = settle_page(page, deadline_seconds=self._settle_deadline)
                body = stable_content(page, deadline_seconds=self._content_deadline)
                denial = classify_denial(final_url, body)
                result.note("landing", url=landing, final_url=final_url,
                            status="loaded_unproxied", denial=denial)
            except Exception as e:
                result.note("landing", url=landing, status="navigation_failed",
                            error=f"{type(e).__name__}: {e}")

        if denial in {"proxy_not_configured", "session_expired", "link_resolver_error"}:
            # No point asking the adapter to parse a login page or an error
            # document. `link_resolver_error` is here because handing one on is
            # actively misleading: for 10.1016/j.xgen.2026.101304 ClinicalKey's
            # RESOURCE_NOT_FOUND XML was recorded as `loaded` and the generic
            # adapter turned it into `no_pdf_link` -- "we could not find a PDF
            # link on the page" for a resolver saying it has no such article.
            result.problems.append(denial_problem(denial, final_url))
            result.note("landing", url=target, final_url=final_url, status=denial)
            if need_pdf:
                result.pdf_status = denial
            if need_supplements:
                # Never `unknown_none_found` here: nothing was looked at, so
                # nothing licenses the claim that this article has none.
                result.suppl_status = "page_not_parsed"
            try:
                page.close()
            except Exception:
                pass
            return

        adapter = adapter_for(final_url)

        # Some publishers answer automation with a plausible 200-OK shell instead of
        # the article. Without this check it reads as "no PDF, no supplements".
        if adapter.looks_blocked(page):
            headless = bool((self.config.get("browser") or {}).get("headless", True))
            # Recorded before the retry, and whether or not the retry rescues the
            # paper. A successful recovery that leaves no trace of the stub reads as
            # though the DOI resolved to cell.com in the first place, which is not
            # what happened and hides the route that is actually broken.
            result.note("landing", url=target, final_url=final_url,
                        status="publisher_stub_page", adapter=adapter.name,
                        headless=headless)
            recovered, why = self._cell_press_retry(page, adapter, ids, result)
            if recovered is None:
                # Last resort before giving up. Until this existed the tier made
                # *zero* download attempts once the retry failed, even though the
                # PII is sitting in the stub's own URL and `/pdfft` is a different
                # endpoint from the shell that was stubbed.
                #
                # The `elsevier` test restates what `looks_blocked` above already
                # implies -- the base returns False and `ElsevierAdapter` is its only
                # override -- and is kept because it is not interchangeable with the
                # one `_cell_press_retry` used to carry. That one guarded the retry
                # and returned a reason, which lands in `recovered is None` and still
                # arrives here; this one guards a ScienceDirect URL built from an
                # Elsevier PII, which is meaningless for any other publisher. Delete
                # at most one of the two.
                got_pdf = (need_pdf and adapter.name == "elsevier"
                           and self._stub_pdf_attempt(context, ids, final_url, result))
                result.problems.append(
                    _stub_problem(adapter.name, final_url, why, headless, got_pdf))
                if need_pdf and not got_pdf:
                    result.pdf_status = "publisher_stub_page"
                if need_supplements:
                    result.suppl_status = "page_not_parsed"
                try:
                    page.close()
                except Exception:
                    pass
                return
            # From here on the page under the adapter is the one cell.com served.
            final_url, body = recovered
            # Not re-classified: `_cell_press_retry` only hands back a page whose own
            # `classify_denial` was falsy -- that is the first term of its `unreadable`
            # test -- and the function is pure over (url, content), so asking again
            # about the same bytes cannot answer differently. What this line is for is
            # clearing the denial recorded for the *stub*, which no longer describes
            # the page now under the adapter.
            denial = None
            adapter = adapter_for(final_url)

        result.note("landing", url=target, final_url=final_url, status="loaded",
                    adapter=adapter.name, denial=denial)

        if need_pdf:
            self._fetch_pdf(context, page, adapter, ids, final_url, result, denial)
        if need_supplements:
            links, parsed = adapter.find_supplements(page, ids.doi)
            if not parsed:
                result.suppl_status = "page_not_parsed"
                result.note("supplements", status="page_not_parsed", adapter=adapter.name)
            elif not links and denial:
                # A challenge page parses perfectly and lists nothing, so an empty
                # list only means "this article has none" when the page was the
                # article. Cloudflare's interstitial (`<title>Just a moment...`)
                # returns parsed=True with zero anchors and `looks_blocked` False --
                # measured on unproxied cell.com and on journal-of-hepatology.eu.
                #
                # `hasSuppl: Y` rescues this for anything Europe PMC indexes, which
                # is why it went unnoticed. An article it does not hold has no such
                # backstop and the fetcher would settle on `unknown_none_found` for a
                # page nobody ever read. 10.1126/sciimmunol.aba4163 is that shape: no
                # PMCID, reached by this tier alone.
                result.suppl_status = "page_not_parsed"
                result.note("supplements", status="page_not_parsed",
                            adapter=adapter.name, denial=denial)
            elif not links:
                # `suppl_status` is deliberately left unset. Setting it to a name of
                # its own was tried and removed: `_supplement_status` recognises none
                # of the values it could take here, so the record still resolved to
                # `unknown_none_found` and the only effect was a status nothing
                # consumed. And that outcome is right -- a regex over rendered HTML
                # finding no links is not evidence that a paper has no supplements,
                # which is the same reason a scrape can never earn plain `fetched`.
                # The distinction is kept where it is actually readable: in `attempts`.
                result.note("supplements", status="none_listed_on_page", adapter=adapter.name)
            else:
                fetched, attempted = self._download_all(
                    context, links, final_url, result, adapter.name
                )
                # Never plain `fetched` here. This is the site that reported
                # `fetched` for 10.1016/j.xgen.2026.101304 while holding 1 of its
                # 12 supplements: `attempted` counts the anchors
                # `looks_like_supplement` matched, and a heuristic cannot know
                # what it missed. See `store.SUPPL_SETTLED`.
                result.suppl_status = (
                    "fetched_unverified" if fetched and fetched == attempted
                    else "partial_failure"
                )
                result.note("supplements", status=result.suppl_status, listed=len(links),
                            attempted=attempted, fetched=fetched, adapter=adapter.name)

        try:
            page.close()
        except Exception:
            pass

    def _cell_press_retry(self, page, adapter, ids, result: SourceResult):
        """Reload a stubbed Elsevier page at cell.com.

        Returns `((final_url, body), None)` on success, or `(None, reason)` where
        `reason` names why it did not work. The caller needs the reason because the
        advice differs: a Cell Press paper that stubs is worth another try, while a
        journal cell.com does not carry has no proxied route at all, and telling that
        user to reach for `--headed` sends them at the wrong obstacle.

        ScienceDirect is not a route to an Elsevier paper for automation, and the
        README says so; cell.com is. See `CELL_PRESS_URL` for what was measured.

        Only offered as a recovery from the stub, not as the first choice, so a
        publisher route that does work keeps working and the fallback is visible in
        `attempts` when it fires.
        """
        # No adapter-name guard: the only caller reaches this inside
        # `adapter.looks_blocked(page)`, and `ElsevierAdapter` is the one class that
        # overrides it -- the base returns False. Should another publisher start
        # stubbing and override it too, this falls to the PII check below and refuses
        # with `no_pii`, which is the accurate reason for a non-Elsevier page.
        pii = pii_from(ids.landing_url or "") or pii_from(page.url or "")
        if not pii:
            result.note("landing_retry", status="no_pii",
                        detail="no Elsevier PII in the landing or final URL")
            return None, "no_pii"

        target = proxied_url(CELL_PRESS_URL.format(pii=pii), self.config)
        try:
            page.goto(target, wait_until="domcontentloaded")
            landed = settle_page(page, deadline_seconds=self._settle_deadline)
            body = stable_content(page, deadline_seconds=self._content_deadline)
        except Exception as e:
            result.note("landing_retry", url=target, status="navigation_failed",
                        error=f"{type(e).__name__}: {e}")
            return None, "navigation_failed"

        # cell.com answers `/retrieve/pii/<PII>` for a Cell Press article by
        # redirecting to the journal's own path: `/cell-reports/fulltext/<PII>`,
        # `/immunity/fulltext/<PII>`, and so on. Measured across this corpus -- all 46
        # Cell Press articles redirected, and the only two that did not are
        # 10.1016/j.coi.2022.102188 (Current Opinion in Immunology) and
        # 10.1016/j.ydbio.2025.08.025 (Developmental Biology), neither of which is a
        # Cell Press title.
        #
        # Staying on `/retrieve/pii/` is cell.com saying it does not carry this
        # journal, and it says so with HTTP 200 and a full Cell Press shell -- masthead,
        # journal list, a password-reset form. So the checks below all pass: no denial,
        # not blocked, and `ElsevierAdapter` builds a PDF URL out of the PII because
        # that is what it does on a cell.com page. The URL then 404s, and `HTTP 404`
        # was the only thing either manifest said. It does not tell a reader that this
        # route cannot work for this journal, which is the fact that decides what to
        # do next.
        if "/retrieve/pii/" in urlparse(landed).path:
            result.note("landing_retry", url=target, final_url=landed,
                        status="not_carried_by_cell_press")
            return None, "not_on_cell_press"

        denial = classify_denial(landed, body)
        landed_adapter = adapter_for(landed)
        # An article page on cell.com links its own PDF. An Elsevier paper that is
        # *not* Cell Press redirects off cell.com to the journal's own host, which
        # leaves the proxy behind and so answers with Cloudflare's interstitial:
        # measured on 10.1016/j.jhep.2019.01.003, which lands on
        # journal-of-hepatology.eu with denial `javascript_challenge`, no PDF link
        # and zero supplement anchors. Requiring the PDF link is what keeps that
        # from being handed on as a page we successfully read.
        unreadable = (denial or landed_adapter.looks_blocked(page)
                      or not landed_adapter.find_pdf_url(page, ids.doi))
        if unreadable:
            result.note("landing_retry", url=target, final_url=landed,
                        status=denial or "not_an_article_page", adapter=landed_adapter.name)
            # Which of the two failures this is decides what the user is told. A
            # host that is no longer cell.com means the redirect left the proxy
            # because cell.com does not carry this journal -- nothing about the
            # browser can change that. Staying on cell.com and still being
            # unreadable is the ordinary stub, where a retry may yet help.
            left_cell_press = "cell.com" not in urlparse(landed).netloc.replace("-", ".")
            return None, ("off_cell_press:" + urlparse(landed).netloc if left_cell_press
                          else denial or "not_an_article_page")

        result.note("landing_retry", url=target, final_url=landed, status="loaded",
                    adapter=landed_adapter.name, pii=pii)
        # Keep the page that was actually parsed, not the stub it replaced --
        # landing.html is the only way to debug an adapter after the fact.
        for item in result.files:
            if item.role == ROLE_LANDING:
                item.content, item.url = body, landed
        return (landed, body), None

    def _stub_pdf_attempt(self, context, ids, stub_url: str, result: SourceResult) -> bool:
        """Ask ScienceDirect's PDF endpoint directly, after the page came back a stub.

        **Unmeasured, deliberately.** Nothing here establishes that ScienceDirect
        serves `/pdfft` to a client it just handed a shell page to. What *is*
        established is that the tier previously gave up having made no download
        attempt at all -- verified: zero HTTP requests between the failed retry and
        the `publisher_stub_page` verdict -- so this is one request where there were
        none, and it is recorded whether it works or not. A live run on a
        non-Cell-Press Elsevier paper settles whether it is worth keeping.

        Two reasons it is not obviously hopeless: the stub is the article *shell* and
        `/pdfft` is a different endpoint, and `_download_via_page` navigates first,
        which is what the 403 branch of `_fetch_pdf` already records ScienceDirect as
        requiring ("refuses pdfft for anything that is not a real navigation").
        """
        pii = pii_from(ids.landing_url or "") or pii_from(stub_url)
        if not pii:
            return False

        url = sciencedirect_pdf_url(stub_url, pii)
        content, _name, why = self._download_via_page(context, url, result, "pdf")
        if content is None:
            result.note("pdf", url=url, status="stub_pdf_attempt_failed",
                        via="stub_pdf_attempt", detail=why)
            return False

        accepted, status, meta = validate_pdf(content, url=url)
        result.note("pdf", url=url, status=status, via="stub_pdf_attempt", **meta)
        if not accepted:
            result.pdf_status = status
            result.problems.append(_pdf_rejected(url, status, meta))
            return False

        result.pdf_status = status
        result.files.append(
            FetchedFile(role=ROLE_PDF, name="fulltext.pdf", content=content, url=url)
        )
        return True

    def _fetch_pdf(self, context, page, adapter, ids, referer, result, denial) -> None:
        """Fetch the article PDF.

        Every failure here appends to `result.problems`, not only to `attempts`.
        `attempts` reaches `manifest.json`; `problems` is what `cli._report` prints,
        and a batch summary line of `pdf=download_failed` with nothing after it sent a
        user to open manifests by hand to find out that ScienceDirect answered 403.
        The three hard denials (`proxy_not_configured`, `session_expired`,
        `link_resolver_error`) are reported by `_publisher_page` and return before
        reaching this method, so nothing below double-reports them.
        """
        pdf_url = adapter.find_pdf_url(page, ids.doi)
        if not pdf_url:
            result.pdf_status = denial or "not_found"
            result.note("pdf", status="no_pdf_link", adapter=adapter.name, denial=denial)
            result.problems.append(
                denial_problem(denial, referer) if denial else
                f"no PDF link found on {referer} (adapter={adapter.name})"
            )
            return

        try:
            response = context.request.get(pdf_url, headers={"Referer": referer})
            content = response.body()
            status_code, headers = response.status, response.headers
        except Exception as e:
            result.pdf_status = "download_failed"
            result.note("pdf", url=pdf_url, status="download_failed",
                        error=f"{type(e).__name__}: {e}")
            result.problems.append(
                f"PDF download failed at {pdf_url}: {type(e).__name__}: {e}"
            )
            return

        if status_code >= 400:
            # The supplement path has a fallback for this and the PDF path did
            # not. ScienceDirect refuses `pdfft` for anything that is not a real
            # navigation -- 403 even unproxied with a browser User-Agent -- so try
            # once through a page before giving up.
            content, _name, why = self._download_via_page(context, pdf_url, result, "pdf")
            if content is None:
                result.pdf_status = "download_failed"
                result.note("pdf", url=pdf_url, status="download_failed",
                            http_status=status_code, fallback=why)
                result.problems.append(
                    f"PDF download failed at {pdf_url}: HTTP {status_code}, and a page "
                    f"navigation did not help either ({why})"
                )
                return
            content_type = ""
            accepted, status, meta = validate_pdf(content, url=pdf_url)
            result.pdf_status = status
            result.note("pdf", url=pdf_url, status=status, via="page_fallback", **meta)
            if accepted:
                result.files.append(
                    FetchedFile(role=ROLE_PDF, name="fulltext.pdf", content=content,
                                url=pdf_url, content_type=content_type)
                )
            else:
                result.problems.append(_pdf_rejected(pdf_url, status, meta))
            return

        content_type = (headers.get("content-type") or "").split(";")[0].strip().lower()
        accepted, status, meta = validate_pdf(content, content_type=content_type, url=pdf_url)
        result.pdf_status = status
        result.note("pdf", url=pdf_url, status=status, adapter=adapter.name, **meta)
        if accepted:
            result.files.append(
                FetchedFile(role=ROLE_PDF, name="fulltext.pdf", content=content,
                            url=pdf_url, content_type=content_type)
            )
        else:
            result.problems.append(_pdf_rejected(pdf_url, status, meta))

    # -- shared download helpers -------------------------------------------

    def _download_all(self, context, links: List[dict], referer: str,
                      result: SourceResult, via: str) -> Tuple[int, int]:
        """Download each link. Returns (fetched, attempted).

        Returning `attempted` rather than comparing against the full list is what
        keeps the `max_files` cap from masquerading as a partial failure -- and any
        links dropped by the cap are recorded, not silently discarded.
        """
        # "link": scraped anchors, as in biorxiv -- see `Source.apply_files_cap`.
        attempted = self.apply_files_cap(links, result, via=via, noun="link")

        # Clearing a JS challenge costs a page load each, so a site that challenges
        # everything would otherwise burn one timeout per file. After a few
        # consecutive failures, stop trying that fallback and record the rest.
        challenge_failures = 0
        max_challenge_failures = int(self.config.get("max_challenge_failures", 3))

        fetched = 0
        for link in attempted:
            url = link["url"]
            content, filename, content_type, why = self._download_one(
                context, url, referer, result, via,
                allow_page_fallback=challenge_failures < max_challenge_failures,
            )
            if content is None:
                if why == "javascript_challenge":
                    challenge_failures += 1
                    if challenge_failures == max_challenge_failures:
                        result.problems.append(
                            f"gave up clearing JavaScript challenges after "
                            f"{max_challenge_failures} failures; remaining files "
                            f"were not attempted via page navigation"
                        )
                continue
            result.files.append(
                FetchedFile(role=ROLE_SUPPLEMENT, name=filename, content=content, url=url,
                            content_type=content_type, label=link.get("label"))
            )
            fetched += 1

        # The same split `_fetch_pdf` had: every per-file refusal was recorded in
        # `attempts` and none of it reached the terminal, so a page that listed twelve
        # supplements and delivered none printed a bare count. The cap and the
        # challenge give-up already report above; this covers everything else.
        lost = len(attempted) - fetched
        if lost:
            result.problems.append(
                f"{lost} of {len(attempted)} supplementary file(s) listed on the page could "
                f"not be fetched (via {via}); see attempts for the per-file reason"
            )
        return fetched, len(attempted)

    def _oversize_mb(self, context, url: str, referer: str) -> Optional[float]:
        """Content-Length in MB if it exceeds the cap, else None.

        Checked *before* transferring. Measured on
        10.1126/science.aax6234: one supplement is a 487.8 MB gzip, so the old
        post-hoc check both wasted the whole transfer and then died inside
        Playwright, whose Node driver marshals bodies as strings and cannot exceed
        V8's ~512 MB limit ("Cannot create a string longer than 0x1fffffe8").
        """
        try:
            head = context.request.head(url, headers={"Referer": referer})
            length = int(head.headers.get("content-length") or 0)
        except Exception:
            return None
        if length and length > self.max_file_bytes:
            return round(length / 1024 / 1024, 1)
        return None

    def _refuse_oversize(self, url: str, oversize, result: SourceResult, via: str) -> None:
        """Record a file the cap refused. Shared by both download paths.

        They return different arities -- `_download_one` a 4-tuple, `_download_via_page`
        a 3-tuple -- so this records and the caller shapes its own return.
        """
        result.problems.append(
            f"{url.rsplit('/', 1)[-1]} not fetched: {oversize} MB exceeds the "
            f"{self.config.get('max_file_mb', 200)} MB cap (fetch.max_file_mb)"
        )
        result.note("supplement_file", url=url, via=via, status="too_large",
                    megabytes=oversize)

    def _transport_failure(self, url: str, error: Exception, result: SourceResult,
                           via: str) -> str:
        """Name a failed body fetch, and say so out loud when it is the ~512 MB wall.

        Playwright's Node driver marshals bodies as strings, so anything near V8's
        limit fails here whatever `fetch.max_file_mb` says -- raising the cap cannot
        help and the only route left is fetching the file by hand. That instruction
        was in `_download_one` and missing from `_download_via_page`, which is the
        drift this helper removes: the aggregate "N of M could not be fetched" line
        still appeared, so the loss was the advice, not the fact.
        """
        transport_limit = "longer than 0x1fffffe8" in str(error)
        status = "too_large_for_transport" if transport_limit else "request_failed"
        if transport_limit:
            result.problems.append(
                f"{url.rsplit('/', 1)[-1]} exceeds what the browser transport can "
                "return (~512 MB); fetch it manually if it is needed"
            )
        result.note("supplement_file", url=url, via=via, status=status,
                    error=f"{type(error).__name__}: {error}")
        return status

    def _download_one(self, context, url: str, referer: str, result: SourceResult, via: str,
                      allow_page_fallback: bool = True):
        """Fetch one file, falling back to real navigation for JS challenges.

        `context.request` shares the browser's cookies but does not execute
        JavaScript, so it cannot clear NCBI's proof-of-work page. When that is what
        comes back, the URL is opened in a real page and the resulting download is
        captured instead.
        """
        oversize = self._oversize_mb(context, url, referer)
        if oversize is not None:
            self._refuse_oversize(url, oversize, result, via)
            return None, None, None, "too_large"

        try:
            response = context.request.get(url, headers={"Referer": referer})
            content, status_code, headers = response.body(), response.status, response.headers
        except Exception as e:
            return None, None, None, self._transport_failure(url, e, result, via)

        # A 401/403 can also be the bot gate rather than a real refusal: for
        # 10.1084/jem.20232192, PMC answered 403 for four supplementary tables
        # that do exist, and the old code short-circuited here without ever
        # trying to clear the challenge. Treat it as challengeable once.
        if status_code in (401, 403) and allow_page_fallback:
            # A stale proof-of-work cookie is answered with 403 rather than a
            # fresh challenge, so reusing it deadlocks: we cannot re-solve what we
            # are never served. Measured on 10.1084/jem.20232192, where plain curl
            # got the 1.8 KB challenge page while the cookie-bearing browser got
            # 403. Dropping the host's cookies makes NCBI issue a new challenge.
            content, filename, why = self._download_via_page(context, url, result, via)
            if content is not None:
                return content, filename, "", None
            result.note("supplement_file", url=url, via=via, status="http_error",
                        http_status=status_code, detail="still refused after clearing")
            return None, None, None, why or "http_error"

        if status_code >= 400 or not content:
            result.note("supplement_file", url=url, via=via, status="http_error",
                        http_status=status_code)
            return None, None, None, "http_error"

        content_type = (headers.get("content-type") or "").split(";")[0].strip().lower()
        denial = classify_denial(url, content)

        if denial == "javascript_challenge":
            if not allow_page_fallback:
                result.note("supplement_file", url=url, via=via,
                            status="javascript_challenge", detail="page fallback disabled")
                return None, None, None, "javascript_challenge"
            content, filename, why = self._download_via_page(context, url, result, via)
            if content is None:
                return None, None, None, why
            return content, filename, "", None

        if denial:
            result.note("supplement_file", url=url, via=via, status=denial, bytes=len(content))
            return None, None, None, denial

        if content_type.startswith("text/html"):
            # A supplement that arrives as HTML is a page, not a file. This is how
            # 26 copies of one article page previously ended up in a corpus.
            result.note("supplement_file", url=url, via=via, status="html_not_a_file",
                        bytes=len(content))
            return None, None, None, "html_not_a_file"

        if len(content) > self.max_file_bytes:
            result.note("supplement_file", url=url, via=via, status="too_large",
                        bytes=len(content))
            return None, None, None, "too_large"

        filename = _filename_for(url, headers)
        result.note("supplement_file", url=url, via=via, status="ok",
                    bytes=len(content), filename=filename, content_type=content_type)
        return content, filename, content_type, None

    def _download_via_page(self, context, url: str, result: SourceResult, via: str):
        """Clear the proof-of-work challenge, then re-request the file.

        Waiting for a `download` event does NOT work: many of these files are
        media (PMC author-manuscript supplements are often .mp4) which Chrome
        renders inline instead of downloading, so no event ever fires.

        What does work, measured: navigate to the URL once so the proof-of-work
        script executes and sets its cookies, then fetch through
        `context.request` as normal -- a 35 MB video came back on the retry. The
        challenge is per-session, not per-file, so this happens once per fetch.
        """
        # Deliberately NOT clearing cookies here. It was tried: the theory was that
        # a stale proof-of-work cookie draws a 403 instead of a fresh challenge, and
        # dropping it would force a new one. Measured, it fixed nothing (the files
        # for 10.1084/jem.20232192 stayed refused) and it destroyed the warm NCBI
        # state that lets a headless browser through at all -- a paper that had
        # fetched 4/4 supplements regressed to a reCAPTCHA. The cookies are worth
        # more than the retry.
        if not self._challenge_cleared:
            wait_seconds = int((self.config.get("browser") or {}).get("challenge_wait_seconds", 8))
            page = context.new_page()
            try:
                try:
                    page.goto(url, wait_until="domcontentloaded")
                except Exception:
                    pass  # the challenge page may abort the navigation
                time.sleep(wait_seconds)
                self._challenge_cleared = True
                result.note("challenge", url=url, via=via, status="cleared",
                            waited_seconds=wait_seconds)
            finally:
                try:
                    page.close()
                except Exception:
                    pass

        # Re-check the size: before the challenge was cleared, Content-Length
        # described the 1.8 KB challenge page, not the file.
        oversize = self._oversize_mb(context, url, url)
        if oversize is not None:
            self._refuse_oversize(url, oversize, result, via)
            return None, None, "too_large"

        try:
            response = context.request.get(url)
            content, status_code = response.body(), response.status
            headers = response.headers
        except Exception as e:
            return None, None, self._transport_failure(url, e, result, via)

        if status_code >= 400 or not content:
            result.note("supplement_file", url=url, via=via, status="http_error",
                        http_status=status_code)
            return None, None, "http_error"

        if classify_denial(url, content) == "javascript_challenge":
            result.note("supplement_file", url=url, via=via, status="javascript_challenge",
                        detail="still challenged after clearing")
            return None, None, "javascript_challenge"

        filename = _filename_for(url, headers)
        result.note("supplement_file", url=url, via=via, status="ok_after_challenge",
                    bytes=len(content), filename=filename)
        return content, filename, None


def _filename_for(url: str, headers) -> str:
    """Best available filename: Content-Disposition, the query, then the path."""
    try:
        disposition = headers.get("content-disposition") or ""
    except Exception:
        disposition = ""
    match = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)\"?", disposition, re.IGNORECASE)
    if match:
        return unquote(match.group(1).strip())

    parts = urlparse(url)
    base = unquote(parts.path.rsplit("/", 1)[-1])
    if not FILE_EXTENSION.search(base):
        # Some hosts route every file through one endpoint and name it in the
        # query. ClinicalKey serves all twelve supplements of
        # 10.1016/j.xgen.2026.101304 from `/ui/service/content/url` with
        # `path=...%2Fmmc1.pdf`, and it sends no Content-Disposition -- so the
        # path alone names every one of them `url`, colliding on disk and
        # losing the extension the extractor picks its parser by.
        for _key, value in parse_qsl(parts.query):
            candidate = value.rsplit("/", 1)[-1]
            if FILE_EXTENSION.search(candidate):
                return candidate
    return base or "supplement"
