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
from urllib.parse import unquote, urlparse

from ..validate import classify_denial, validate_pdf
from ..adapters import adapter_for
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
        browser_cfg.get("profile_dir", "~/.curation-harness/chrome-profile")
    ))


def state_path(fetch_cfg: dict) -> Path:
    """Where the cookie snapshot lives, next to the browser profile."""
    return _profile_dir(fetch_cfg).parent / "storage_state.json"


def save_state(context, fetch_cfg: dict) -> Optional[Path]:
    """Snapshot cookies, including session cookies, to disk.

    This exists because a persistent Chrome profile is NOT sufficient on its own.
    Measured: after `login` succeeded, the profile held three `.idm.oclc.org`
    cookies, and they were gone after the next browser start -- EZproxy issues
    *session* cookies, which Chrome discards on restart. Playwright's
    `storage_state()` captures them (with `expires: -1`) so they can be re-injected
    into the next run, which is what actually keeps a library-proxy login alive.
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


def stable_content(page, attempts: int = 4) -> bytes:
    """`page.content()` that tolerates a page still navigating.

    EZproxy bounces through a couple of client-side redirects before landing on
    the publisher, and calling `content()` mid-flight raises
    "the page is navigating and changing the content". Retrying is the fix.
    """
    last_error = None
    for attempt in range(attempts):
        try:
            return page.content().encode("utf-8", "replace")
        except Exception as e:
            last_error = e
            try:
                page.wait_for_load_state("load", timeout=10000)
            except Exception:
                time.sleep(1 + attempt)
    raise RuntimeError(f"could not read page content: {last_error}")


def settle_page(page, rounds: int = 3, timeout_ms: int = 15000) -> str:
    """Wait out client-side redirects and return the settled URL.

    Necessary because several hops in this path are JS redirects rather than HTTP
    ones: EZproxy bounces to the rewritten host, and a DOI on an Elsevier journal
    resolves to `linkinghub.elsevier.com`, which is a redirect shim. Reading the
    DOM at `domcontentloaded` captures a page titled "Redirecting" with no article
    content in it -- which is what made the first Elsevier fetch report
    `page_not_parsed`.
    """
    previous = None
    for _ in range(rounds):
        current = page.url
        try:
            page.wait_for_load_state("networkidle", timeout=timeout_ms)
        except Exception:
            try:
                page.wait_for_load_state("load", timeout=5000)
            except Exception:
                time.sleep(1)
        if page.url == current == previous:
            break
        previous = current
    return page.url


def _authenticated_yet(page, context=None) -> Tuple[bool, str]:
    """Is the article actually reachable, not merely advertised?

    Finding a PDF link is NOT evidence of access: publishers emit
    `citation_pdf_url` on paywalled articles too, because Google Scholar requires
    it. So when a context is available the PDF is downloaded and validated -- the
    only signal that distinguishes entitlement from a purchase page.
    """
    try:
        url = page.url
        body = stable_content(page)
    except Exception as e:
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
            succeeded, detail = _authenticated_yet(live, context)
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
        print("Verify any time with:  python -m curation.fetch.cli check")
        return 0
    print(f"\nCould not confirm access: {detail}", flush=True)
    if saved_to:
        print(f"Cookies were still snapshotted to {saved_to}; try: "
              "python -m curation.fetch.cli check")
    return 1


def check_session(fetch_cfg: dict, probe_url: Optional[str] = None) -> Tuple[bool, str]:
    """Probe a known article and report whether the stored session still works."""
    target = proxied_url(probe_url or _default_check_url(fetch_cfg), fetch_cfg)
    try:
        with browser_context(fetch_cfg) as context:
            page = context.new_page()
            page.goto(target, wait_until="domcontentloaded")
            settle_page(page)
            return _authenticated_yet(page, context)
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
            referer = settle_page(page)
            body = stable_content(page)

            # Measured: NCBI serves headless Chrome a reCAPTCHA interstitial
            # ("Checking your browser") with no article content, while plain HTTP
            # gets the page fine. A visible browser passes. Say that plainly
            # instead of reporting an empty supplement list.
            if classify_denial(referer, body) == "javascript_challenge":
                result.suppl_status = "page_not_parsed"
                result.problems.append(
                    "PMC served a bot check to the headless browser; re-run with "
                    "--headed to collect these supplementary files"
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
            result.suppl_status = "fetched"
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
            final_url = settle_page(page)
            body = stable_content(page)
        except Exception as e:
            result.note("landing", url=target, status="navigation_failed",
                        error=f"{type(e).__name__}: {e}")
            if need_pdf:
                result.pdf_status = "download_failed"
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
        if denial in {"proxy_not_configured", "session_expired"}:
            # No point asking the adapter to parse a login page.
            result.problems.append(f"{denial} at {final_url}")
            result.note("landing", url=target, final_url=final_url, status=denial)
            if need_pdf:
                result.pdf_status = denial
            try:
                page.close()
            except Exception:
                pass
            return

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
            elif not links:
                result.note("supplements", status="none_listed_on_page", adapter=adapter.name)
            else:
                fetched, attempted = self._download_all(
                    context, links, final_url, result, adapter.name
                )
                result.suppl_status = (
                    "fetched" if fetched and fetched == attempted else "partial_failure"
                )
                result.note("supplements", status=result.suppl_status, listed=len(links),
                            attempted=attempted, fetched=fetched, adapter=adapter.name)

        try:
            page.close()
        except Exception:
            pass

    def _fetch_pdf(self, context, page, adapter, ids, referer, result, denial) -> None:
        pdf_url = adapter.find_pdf_url(page, ids.doi)
        if not pdf_url:
            result.pdf_status = denial or "not_found"
            result.note("pdf", status="no_pdf_link", adapter=adapter.name, denial=denial)
            return

        try:
            response = context.request.get(pdf_url, headers={"Referer": referer})
            content = response.body()
            status_code, headers = response.status, response.headers
        except Exception as e:
            result.pdf_status = "download_failed"
            result.note("pdf", url=pdf_url, status="download_failed",
                        error=f"{type(e).__name__}: {e}")
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

    # -- shared download helpers -------------------------------------------

    def _download_all(self, context, links: List[dict], referer: str,
                      result: SourceResult, via: str) -> Tuple[int, int]:
        """Download each link. Returns (fetched, attempted).

        Returning `attempted` rather than comparing against the full list is what
        keeps the `max_files` cap from masquerading as a partial failure -- and any
        links dropped by the cap are recorded, not silently discarded.
        """
        attempted = links[: self.max_files]
        dropped = len(links) - len(attempted)
        if dropped > 0:
            result.problems.append(
                f"{dropped} supplementary link(s) not fetched: max_files cap "
                f"({self.max_files}) reached"
            )
            result.note("cap", via=via, status="truncated", dropped=dropped,
                        max_files=self.max_files)

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
            result.problems.append(
                f"{url.rsplit('/', 1)[-1]} not fetched: {oversize} MB exceeds the "
                f"{self.config.get('max_file_mb', 200)} MB cap (fetch.max_file_mb)"
            )
            result.note("supplement_file", url=url, via=via, status="too_large",
                        megabytes=oversize)
            return None, None, None, "too_large"

        try:
            response = context.request.get(url, headers={"Referer": referer})
            content, status_code, headers = response.body(), response.status, response.headers
        except Exception as e:
            # Playwright's Node driver marshals bodies as strings, so anything
            # near V8's ~512 MB limit fails here regardless of the cap.
            transport_limit = "longer than 0x1fffffe8" in str(e)
            status = "too_large_for_transport" if transport_limit else "request_failed"
            if transport_limit:
                result.problems.append(
                    f"{url.rsplit('/', 1)[-1]} exceeds what the browser transport can "
                    "return (~512 MB); fetch it manually if it is needed"
                )
            result.note("supplement_file", url=url, via=via, status=status,
                        error=f"{type(e).__name__}: {e}")
            return None, None, None, status

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
            result.problems.append(
                f"{url.rsplit('/', 1)[-1]} not fetched: {oversize} MB exceeds the "
                f"{self.config.get('max_file_mb', 200)} MB cap (fetch.max_file_mb)"
            )
            result.note("supplement_file", url=url, via=via, status="too_large",
                        megabytes=oversize)
            return None, None, "too_large"

        try:
            response = context.request.get(url)
            content, status_code = response.body(), response.status
            headers = response.headers
        except Exception as e:
            transport_limit = "longer than 0x1fffffe8" in str(e)
            status = "too_large_for_transport" if transport_limit else "request_failed"
            result.note("supplement_file", url=url, via=via, status=status,
                        error=f"{type(e).__name__}: {e}")
            return None, None, status

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
    """Best available filename: Content-Disposition, then the URL path."""
    disposition = ""
    try:
        disposition = headers.get("content-disposition") or ""
    except Exception:
        disposition = ""
    match = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)\"?", disposition, re.IGNORECASE)
    if match:
        return unquote(match.group(1).strip())
    base = urlparse(url).path.rsplit("/", 1)[-1]
    return unquote(base) if base else "supplement"
