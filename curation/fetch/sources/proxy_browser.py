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

import os
import re
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


@contextmanager
def browser_context(fetch_cfg: dict, headless: Optional[bool] = None):
    """A persistent Playwright context rooted at the configured profile dir.

    Persistence is the whole point: it is what carries the hand-made SSO session
    (and any proof-of-work cookies) from one run to the next.
    """
    sync_playwright = _import_playwright()
    browser_cfg = fetch_cfg.get("browser") or {}
    profile = Path(os.path.expanduser(browser_cfg.get("profile_dir", "~/.curation-harness/chrome-profile")))
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
        "check_url", "https://www.nature.com/articles/s41586-021-03852-1"
    )


def interactive_login(fetch_cfg: dict, probe_url: Optional[str] = None) -> int:
    """Open a headed browser so the user can complete Stanford SSO by hand."""
    target = proxied_url(probe_url or _default_check_url(fetch_cfg), fetch_cfg)
    print(
        "Opening a browser window.\n"
        "  1. Complete Stanford login (SUNet ID, Cardinal Key / Duo).\n"
        "  2. Wait until you can see the article page.\n"
        "  3. Return here and press Enter.\n"
        f"Target: {target}\n"
    )
    with browser_context(fetch_cfg, headless=False) as context:
        page = context.new_page()
        try:
            page.goto(target, wait_until="domcontentloaded")
        except Exception as e:
            print(f"navigation warning: {e}")
        input("Press Enter once you are logged in and the article is visible... ")
        final = page.url
    print(f"Saved session for profile; last URL was {final}")
    return 0


def check_session(fetch_cfg: dict, probe_url: Optional[str] = None) -> Tuple[bool, str]:
    """Probe a known article and report whether the stored session still works."""
    target = proxied_url(probe_url or _default_check_url(fetch_cfg), fetch_cfg)
    try:
        with browser_context(fetch_cfg) as context:
            page = context.new_page()
            page.goto(target, wait_until="domcontentloaded")
            url, body = page.url, page.content().encode("utf-8", "replace")
            denial = classify_denial(url, body)
            adapter = adapter_for(url)
            pdf_url = adapter.find_pdf_url(page, "")
    except ImportError as e:
        return False, str(e)
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"

    if denial:
        return False, f"{denial} at {url}"
    if pdf_url:
        return True, f"reached {url} and found a PDF link via the {adapter.name} adapter"
    return False, f"reached {url} but found no PDF link (adapter={adapter.name})"


class ProxyBrowserSource(Source):
    name = "proxy_browser"

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
            links, parsed = adapter_for(page.url).find_supplements(page, ids.doi)
            referer = page.url
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
            final_url = page.url
            body = page.content().encode("utf-8", "replace")
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
            result.pdf_status = "download_failed"
            result.note("pdf", url=pdf_url, status="download_failed", http_status=status_code)
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

    def _download_one(self, context, url: str, referer: str, result: SourceResult, via: str,
                      allow_page_fallback: bool = True):
        """Fetch one file, falling back to real navigation for JS challenges.

        `context.request` shares the browser's cookies but does not execute
        JavaScript, so it cannot clear NCBI's proof-of-work page. When that is what
        comes back, the URL is opened in a real page and the resulting download is
        captured instead.
        """
        try:
            response = context.request.get(url, headers={"Referer": referer})
            content, status_code, headers = response.body(), response.status, response.headers
        except Exception as e:
            result.note("supplement_file", url=url, via=via, status="request_failed",
                        error=f"{type(e).__name__}: {e}")
            return None, None, None, "request_failed"

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
        """Open a URL in a real page and capture the download it triggers."""
        timeout_ms = int((self.config.get("browser") or {}).get("download_timeout_seconds", 20)) * 1000
        page = context.new_page()
        try:
            with page.expect_download(timeout=timeout_ms) as info:
                try:
                    page.goto(url, wait_until="commit")
                except Exception:
                    # A navigation that turns into a download raises; the
                    # expect_download context is what actually matters.
                    pass
            download = info.value
            path = download.path()
            if not path:
                result.note("supplement_file", url=url, via=via, status="download_empty")
                return None, None, "download_empty"
            content = Path(path).read_bytes()
            filename = download.suggested_filename or _filename_for(url, {})
            result.note("supplement_file", url=url, via=via, status="ok_via_page",
                        bytes=len(content), filename=filename)
            return content, filename, None
        except Exception as e:
            result.note("supplement_file", url=url, via=via, status="javascript_challenge",
                        detail=f"no download captured: {type(e).__name__}")
            return None, None, "javascript_challenge"
        finally:
            try:
                page.close()
            except Exception:
                pass


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
