"""The proxy_browser tier, offline.

This is the module that needed tests most: it is the largest in the package, the
most fragile, and until now every bug in it was found by running real DOIs
against real publishers. Each test below pins a behaviour that a live run
actually got wrong at some point, so a regression shows up here instead of in a
half-empty corpus directory weeks later.
"""

import json
import time

import pytest

from manuscript_harvest.fetch.adapters import adapter_for, candidate_hosts
from manuscript_harvest.fetch.sources import proxy_browser as pb
from manuscript_harvest.fetch.sources.base import SourceResult
from manuscript_harvest.fetch.validate import classify_denial
from tests.fakes import (
    CELL_PRESS_ARTICLE_LINKS,
    CELL_PRESS_ARTICLE_PDF,
    CELL_PRESS_ARTICLE_URL,
    CELL_PRESS_SUPPLEMENTS,
    CELL_PRESS_TITLE,
    CLOUDFLARE_HTML,
    CLOUDFLARE_LINKS,
    DUO_PROMPT_HTML,
    DUO_PROMPT_URL,
    EZPROXY_HTML,
    PAYWALL_HTML,
    POW_HTML,
    RECAPTCHA_HTML,
    RESOURCE_NOT_FOUND_XML,
    SAML_REDIRECT_TITLE,
    SAML_REDIRECT_URL,
    SSO_HTML,
    FakeContext,
    FakePage,
    FakeRequest,
    FakeResponse,
    make_pdf,
)

PROXY = {"enabled": True, "prefix": "https://stanford.idm.oclc.org/login?url="}


# -- URL rewriting -----------------------------------------------------------

def test_proxied_url_wraps_and_is_idempotent():
    cfg = {"proxy": PROXY}
    target = pb.proxied_url("https://www.nature.com/articles/x", cfg)
    assert target == PROXY["prefix"] + "https://www.nature.com/articles/x"
    # Wrapping twice would produce a nested prefix that resolves to nothing.
    assert pb.proxied_url(target, cfg) == target


def test_proxied_url_respects_disabled_proxy():
    cfg = {"proxy": {"enabled": False, "prefix": PROXY["prefix"]}}
    assert pb.proxied_url("https://x.example/a", cfg) == "https://x.example/a"


def test_ezproxy_hostname_rewriting_selects_the_right_adapter():
    """EZproxy turns dots into hyphens; without undoing that every proxied page
    fell through to the generic adapter, which is what happened on the first
    authenticated fetch."""
    cases = {
        "https://www-nature-com.stanford.idm.oclc.org/articles/x": "nature",
        "https://www-sciencedirect-com.stanford.idm.oclc.org/science/article/pii/X": "elsevier",
        "https://onlinelibrary-wiley-com.stanford.idm.oclc.org/doi/10.1/x": "wiley",
        "https://pmc-ncbi-nlm-nih-gov.stanford.idm.oclc.org/articles/PMC1/": "pmc",
        "https://www.nature.com/articles/x": "nature",
        "https://journals.plos.org/x": "generic",
    }
    for url, expected in cases.items():
        assert adapter_for(url).name == expected, url
    assert "www.nature.com" in candidate_hosts(
        "https://www-nature-com.stanford.idm.oclc.org/articles/x"
    )


# -- page settling -----------------------------------------------------------

def test_stable_content_retries_while_navigating():
    """`page.content()` raises mid-navigation; EZproxy and linkinghub both hop
    client-side, so one attempt is not enough."""

    class Flaky(FakePage):
        def __init__(self):
            super().__init__()
            self.attempts = 0

        def content(self):
            self.attempts += 1
            if self.attempts < 3:
                raise RuntimeError("the page is navigating and changing the content")
            return "<html>ok</html>"

    page = Flaky()
    assert pb.stable_content(page) == b"<html>ok</html>"
    assert page.attempts == 3


def test_stable_content_gives_up_loudly():
    page = FakePage(content=RuntimeError("still navigating"))
    with pytest.raises(RuntimeError, match="could not read page content"):
        pb.stable_content(page, attempts=2)


def test_stable_content_stops_at_its_deadline():
    """An expired session's SAML2 POST form never stops navigating, so every
    attempt raises and every wait times out. Without an overall deadline this
    burned minutes in total silence -- `check` prints only afterwards, so the log
    stayed empty and it read as a hang rather than a dead session."""
    page = FakePage(content=RuntimeError("the page is navigating and changing the content"),
                    load_state_error=RuntimeError("timeout waiting for load"))
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="could not read page content"):
        pb.stable_content(page, attempts=40, deadline_seconds=0.3)
    elapsed = time.monotonic() - started
    # 40 attempts with the old escalating back-off would run for minutes.
    assert elapsed < 3.0, f"took {elapsed:.1f}s despite a 0.3s deadline"


class NeverReadable(FakePage):
    """A document that never stops navigating: `content()` does not return.

    Not merely slow -- measured 2026-07-30, `page.content()` on an expired
    session had not returned after 88s despite a 12s deadline and a 4s page
    default, because neither governs it. So the fake blocks rather than raising:
    a test that only made it raise would pass against code that still hangs in
    production.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.content_calls = 0

    def content(self):
        self.content_calls += 1
        time.sleep(30)  # never reached if the guard works; fails the test if not
        raise AssertionError("unreachable")


def test_an_identifiable_page_is_never_read():
    """The deadline cannot save us here, so the body must not be asked for.

    `page.content()` is uninterruptible on a perpetually navigating document --
    no timeout argument, and `set_default_timeout` does not govern it either.
    The only defence is to recognise the page from `url` and `title`, which
    answer instantly, and never call `content()` at all. If someone reorders
    this so the body is read first, the multi-minute hang comes straight back.
    """
    page = NeverReadable(url=SAML_REDIRECT_URL, title=SAML_REDIRECT_TITLE)
    denial, url = pb.denial_before_reading(page)
    assert denial == "session_expired"
    assert page.content_calls == 0, "the body must not be touched"

    # A healthy article page is not caught by the guard, so it still gets read.
    healthy = FakePage(url="https://www-nature-com.stanford.idm.oclc.org/articles/x",
                       title="Epigenetic and 3D genome reprogramming during ageing",
                       content=b"<html>the article</html>")
    assert pb.denial_before_reading(healthy)[0] is None


def test_settle_page_stops_at_its_deadline():
    """A page that never settles has to be given up on, not waited out. On an
    expired session no round reaches `networkidle`, so each pays its full timeout
    twice over -- measured at 31s, all of it ahead of the first byte anyone could
    classify."""
    page = FakePage(url="https://stanford.idm.oclc.org/login?url=x",
                    load_state_error=RuntimeError("timeout"))
    started = time.monotonic()
    pb.settle_page(page, rounds=10, timeout_ms=15000, deadline_seconds=0.3)
    elapsed = time.monotonic() - started
    assert elapsed < 3.0, f"took {elapsed:.1f}s despite a 0.3s deadline"


def test_a_navigating_page_is_still_identifiable():
    """`content()` and `evaluate()` hang on a perpetually navigating document but
    `title()` answers instantly, and on an expired session the title names the
    IdP. That is the whole diagnosis, and it was being thrown away with the
    exception."""
    page = FakePage(url=SAML_REDIRECT_URL, title=SAML_REDIRECT_TITLE,
                    content=RuntimeError("the page is navigating"))
    marker_url, body = pb.navigation_marker(page)
    # The IdP is in the title only -- the URL is still EZproxy's own.
    assert "login.stanford.edu" not in page.url
    assert classify_denial(marker_url, body) == "session_expired"


def test_an_unreadable_landing_page_reports_the_session_not_a_failure():
    """End to end: the tier must turn the wedge into `session_expired`, which
    tells you to run `login`, rather than `download_failed`, which tells you
    nothing."""
    # The body genuinely blocks, as it does in production -- if the tier reads it
    # before classifying, this test hangs, which is the failure we want.
    class Wedged(ProxyRedirectPage):
        def content(self):
            time.sleep(30)
            raise AssertionError("unreachable")

    page = Wedged(SAML_REDIRECT_URL, title=SAML_REDIRECT_TITLE)
    source = _source(proxy=PROXY)
    result = SourceResult(tier="proxy_browser")

    class Ids:
        doi = "10.1038/s41586-026-10510-x"
        landing_url = "https://www.nature.com/articles/s41586-026-10510-x"

    source._publisher_page(FakeContext(pages=[page]), Ids(), result,
                           need_pdf=True, need_supplements=True)
    assert result.pdf_status == "session_expired"
    assert result.suppl_status == "page_not_parsed"
    assert any("session_expired" in p for p in result.problems)


def test_settle_page_returns_the_final_url():
    page = FakePage(url="https://linkinghub.elsevier.com/retrieve/pii/X")
    assert pb.settle_page(page, rounds=2) == "https://linkinghub.elsevier.com/retrieve/pii/X"


# -- cookie snapshot ---------------------------------------------------------

def test_state_round_trip_restores_cookies(tmp_path):
    """A persistent Chrome profile drops EZproxy's session cookies on restart, so
    the snapshot is what actually keeps a library login alive."""
    cfg = {"browser": {"profile_dir": str(tmp_path / "profile")}}
    context = FakeContext()
    saved = pb.save_state(context, cfg)
    assert saved and saved.exists()
    assert json.loads(saved.read_text())["cookies"]

    restored = FakeContext()
    assert pb._restore_state(restored, cfg) == 1
    assert restored.added_cookies[0]["name"] == "a"


def test_restore_state_tolerates_a_missing_or_corrupt_snapshot(tmp_path):
    cfg = {"browser": {"profile_dir": str(tmp_path / "profile")}}
    assert pb._restore_state(FakeContext(), cfg) == 0
    pb.state_path(cfg).parent.mkdir(parents=True, exist_ok=True)
    pb.state_path(cfg).write_text("{not json")
    assert pb._restore_state(FakeContext(), cfg) == 0


# -- filenames ---------------------------------------------------------------

@pytest.mark.parametrize("url,headers,expected", [
    ("https://x/a/file.xlsx", {}, "file.xlsx"),
    ("https://x/a/file.xlsx?download=1", {}, "file.xlsx"),
    ("https://x/a/", {"content-disposition": 'attachment; filename="real name.pdf"'},
     "real name.pdf"),
    ("https://x/a/%20odd%20.csv", {}, " odd .csv"),
    ("https://x/", {}, "supplement"),
    # ClinicalKey routes every supplement of 10.1016/j.xgen.2026.101304 through
    # one endpoint and names it in the query, sending no Content-Disposition.
    # On the path alone all twelve are called `url`: they collide on disk and
    # lose the extension the extractor picks its parser by.
    ("https://www-clinicalkey-com.stanford.idm.oclc.org/ui/service/content/url"
     "?section=static%2fimage&eid=1-s2.0-S2666979X26001667"
     "&path=2666979X%2FS2666979XXXXXXXXX%2FS2666979X26001667%2Fmmc4.xlsx", {}, "mmc4.xlsx"),
    # A real path still wins over anything in the query.
    ("https://x/a/real.xlsx?path=decoy%2Fwrong.pdf", {}, "real.xlsx"),
    # Nothing filename-shaped anywhere: unchanged behaviour, not a bad guess.
    ("https://x/ui/service/url?section=static&eid=1-s2.0-X", {}, "url"),
])
def test_filename_for(url, headers, expected):
    assert pb._filename_for(url, headers) == expected


# -- download paths ----------------------------------------------------------

def _source(**config):
    # challenge_wait_seconds: 0 keeps the suite fast; the live default is 8.
    base = {"max_files": 50, "max_file_mb": 200,
            "browser": {"headless": True, "challenge_wait_seconds": 0,
                        # 0 keeps the suite fast; the live default is 25.
                        "content_deadline_seconds": 0}}
    base.update(config)
    return pb.ProxyBrowserSource(None, base)


def test_html_is_never_stored_as_a_supplement():
    """26 copies of one article page were once saved this way."""
    source = _source()
    result = SourceResult(tier="proxy_browser")
    context = FakeContext(request=FakeRequest({
        "file.pdf": FakeResponse(200, b"<html>a page</html>",
                                 {"content-type": "text/html; charset=utf-8"}),
    }))
    content, _name, _ctype, why = source._download_one(
        context, "https://x/file.pdf", "https://x", result, "test"
    )
    assert content is None and why == "html_not_a_file"


def test_oversized_file_is_refused_before_transfer():
    """One Science supplement is a 487.8 MB gzip: checking size only after the
    transfer wasted the download and then died inside Playwright."""
    source = _source(max_file_mb=1)
    result = SourceResult(tier="proxy_browser")
    context = FakeContext(request=FakeRequest({
        "big.gz": FakeResponse(200, b"x" * 10, {"content-length": str(500 * 1024 ** 2)}),
    }))
    content, _n, _c, why = source._download_one(
        context, "https://x/big.gz", "https://x", result, "test"
    )
    assert content is None and why == "too_large"
    # Refused on the HEAD, so the body was never requested.
    assert context.request.heads and not context.request.gets
    assert any("exceeds the 1 MB cap" in p for p in result.problems)


def test_transport_limit_is_named_not_swallowed():
    """Playwright's Node driver marshals bodies as strings and dies near V8's
    ~512 MB limit regardless of the configured cap."""
    source = _source()
    result = SourceResult(tier="proxy_browser")
    boom = Exception("Cannot create a string longer than 0x1fffffe8 characters")
    context = FakeContext(request=FakeRequest({"huge": FakeResponse(200, boom)}))
    content, _n, _c, why = source._download_one(
        context, "https://x/huge", "https://x", result, "test"
    )
    assert content is None and why == "too_large_for_transport"
    assert any("fetch it manually" in p for p in result.problems)


def test_the_page_route_names_the_transport_limit_too():
    """The two download paths had drifted: only `_download_one` told the user the
    file has to be fetched by hand. `_download_all` still appended its aggregate
    "N of M could not be fetched" line either way, so what was lost was the advice,
    not the fact -- and raising fetch.max_file_mb cannot help, which is exactly why
    the advice is the useful part."""
    source = _source()
    result = SourceResult(tier="proxy_browser")
    boom = Exception("Cannot create a string longer than 0x1fffffe8 characters")
    context = FakeContext(request=FakeRequest({"huge": FakeResponse(200, boom)}))
    content, _name, why = source._download_via_page(
        context, "https://x/huge", result, "test"
    )
    assert content is None and why == "too_large_for_transport"
    assert any("fetch it manually" in p for p in result.problems)


def test_the_page_route_refuses_an_oversize_file_with_the_cap_named():
    """The same sharing, for the size cap: before the challenge is cleared
    Content-Length describes the challenge page, so this path re-checks and has to
    report the refusal the same way."""
    source = _source(max_file_mb=1)
    result = SourceResult(tier="proxy_browser")
    context = FakeContext(request=FakeRequest(
        {"big.gz": FakeResponse(200, b"x", {"content-length": str(9 * 1024 ** 2)})}))
    content, _name, why = source._download_via_page(
        context, "https://x/big.gz", result, "test"
    )
    assert content is None and why == "too_large"
    assert any("exceeds the 1 MB cap" in p for p in result.problems)


def test_challenge_is_cleared_once_then_reused():
    """The proof-of-work challenge is per-session: navigating one URL sets the
    cookies, and every later file then fetches normally."""
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            return FakeResponse(200, POW_HTML, {"content-type": "text/html"})
        return FakeResponse(200, b"real bytes", {"content-type": "application/octet-stream"})

    source = _source()
    result = SourceResult(tier="proxy_browser")
    context = FakeContext(request=FakeRequest({"f1": flaky}))
    content, name, _c, why = source._download_one(
        context, "https://pmc.ncbi.nlm.nih.gov/f1.xlsx", "https://pmc", result, "pmc"
    )
    assert content == b"real bytes" and why is None
    assert source._challenge_cleared is True

    # A second file must not pay for the challenge again.
    context2 = FakeContext(request=FakeRequest({
        "f2": FakeResponse(200, b"more", {"content-type": "application/octet-stream"})}))
    content2, _n, _c, _w = source._download_one(
        context2, "https://pmc.ncbi.nlm.nih.gov/f2.xlsx", "https://pmc", result, "pmc"
    )
    assert content2 == b"more"
    assert len(context2.pages) == 0, "cleared challenge should not reopen a page"


def test_cookies_are_never_cleared():
    """Clearing NCBI cookies to force a fresh challenge was tried and reverted: it
    fixed nothing and cost the warm state that gets a headless browser past
    reCAPTCHA, regressing a paper from 4/4 supplements to a bot check."""
    source = _source()
    result = SourceResult(tier="proxy_browser")
    context = FakeContext(request=FakeRequest({
        "f": FakeResponse(403, b"denied", {"content-type": "text/html"})}))
    source._download_one(context, "https://pmc.ncbi.nlm.nih.gov/f.xlsx",
                         "https://pmc", result, "pmc")
    assert context.cleared_domains == []


def test_max_files_cap_is_recorded_not_silent():
    source = _source(max_files=2)
    result = SourceResult(tier="proxy_browser")
    context = FakeContext(request=FakeRequest({
        "f": FakeResponse(200, b"bytes", {"content-type": "application/pdf"})}))
    links = [{"url": f"https://x/f{i}.pdf", "label": None} for i in range(5)]
    fetched, attempted = source._download_all(context, links, "https://x", result, "test")
    assert (fetched, attempted) == (2, 2)
    assert any("3 supplementary link(s) not fetched" in p for p in result.problems)


# -- landing page handling ---------------------------------------------------

class ProxyRedirectPage(FakePage):
    """A page that lands on EZproxy's rewritten hostname, as the real proxy does.

    Without this the fake stays on `stanford.idm.oclc.org`, which selects the
    generic adapter and hides publisher-specific behaviour.
    """

    def __init__(self, rewritten_url, **kwargs):
        super().__init__(url=rewritten_url, **kwargs)
        self.rewritten_url = rewritten_url

    def goto(self, url, wait_until=None, timeout=None):
        self.visited.append(url)
        self.url = self.rewritten_url


def test_publisher_stub_page_is_reported():
    """ScienceDirect answers automation with a shell page; without this it looks
    like an article that simply has no PDF and no supplements."""
    page = ProxyRedirectPage(
        "https://www-sciencedirect-com.stanford.idm.oclc.org/science/article/pii/X",
        title="ScienceDirect", links=[], content=b"<html></html>")
    source = _source(proxy=PROXY)
    result = SourceResult(tier="proxy_browser")

    class Ids:
        doi = "10.1016/j.stem.2023.12.013"
        landing_url = "https://www.sciencedirect.com/science/article/pii/X"

    source._publisher_page(FakeContext(pages=[page]), Ids(), result,
                           need_pdf=True, need_supplements=True)
    assert result.pdf_status == "publisher_stub_page"
    assert result.suppl_status == "page_not_parsed"
    assert any("stub page" in p for p in result.problems)


_SD_STUB_URL = ("https://www-sciencedirect-com.stanford.idm.oclc.org"
                "/science/article/pii/S0092867421005730?via%3Dihub")


class CellPressRetryPage(FakePage):
    """ScienceDirect's stub first, then whatever cell.com answers on the retry.

    One page object across both navigations, because that is what the tier does:
    it reuses the page it already has rather than opening a second one.
    """

    def __init__(self, retry_url=CELL_PRESS_ARTICLE_URL, retry_title=CELL_PRESS_TITLE,
                 retry_links=None, retry_content=b"<html>the article</html>",
                 retry_metas=None):
        super().__init__(url=_SD_STUB_URL, title="ScienceDirect", links=[],
                         content=b"<html></html>")
        self.retry = {
            "url": retry_url, "title": retry_title,
            "links": retry_links if retry_links is not None else CELL_PRESS_ARTICLE_LINKS,
            "content": retry_content,
            "metas": retry_metas if retry_metas is not None
            else {"citation_pdf_url": CELL_PRESS_ARTICLE_PDF},
        }

    def goto(self, url, wait_until=None, timeout=None):
        self.visited.append(url)
        if "cell.com" in url:
            self.url = self.retry["url"]
            self._title = self.retry["title"]
            self.links = self.retry["links"]
            self._content = self.retry["content"]
            self.metas = self.retry["metas"]
        else:
            self.url = _SD_STUB_URL


class _ElsevierIds:
    doi = "10.1016/j.cell.2021.04.038"
    landing_url = "https://linkinghub.elsevier.com/retrieve/pii/S0092867421005730"


def test_a_stubbed_elsevier_page_is_retried_at_cell_press():
    """10.1016/j.cell.2021.04.038 and 10.1016/j.ccell.2021.03.007 both came back
    empty against the hand-fetched copies: EZproxy sends the linkinghub DOI to
    ScienceDirect, which serves automation a shell page, and the papers ended as
    `failed` with 0 of 6 supplements. cell.com -- where the human downloaded them --
    renders the same article and the existing adapter finds all six there."""
    page = CellPressRetryPage()
    source = _source(proxy=PROXY)
    result = SourceResult(tier="proxy_browser")
    request = FakeRequest({
        "/cell/pdf/": FakeResponse(200, make_pdf(pages=35),
                                   {"content-type": "application/pdf"}),
        ".pdf": FakeResponse(200, make_pdf(pages=2), {"content-type": "application/pdf"}),
        ".xls": FakeResponse(200, b"\xd0\xcf\x11\xe0a workbook",
                             {"content-type": "application/vnd.ms-excel"}),
    })

    source._publisher_page(FakeContext(pages=[page], request=request), _ElsevierIds(),
                           result, need_pdf=True, need_supplements=True)

    assert result.pdf_status == "ok"
    names = sorted(f.name for f in result.files if f.role == "supplement")
    assert names == [name for name, _ in CELL_PRESS_SUPPLEMENTS]
    assert result.suppl_status == "fetched_unverified"
    # The fallback is visible in the record, and the page kept for debugging is the
    # one that was actually parsed rather than the stub it replaced.
    retry = next(a for a in result.attempts if a["action"] == "landing_retry")
    assert retry["status"] == "loaded" and "cell.com" in retry["url"]
    # And the stub is still recorded, in front of it. A recovery that erases what it
    # recovered from reads as though the DOI resolved to cell.com to begin with, and
    # the route that is actually broken disappears from the record.
    assert [a["status"] for a in result.attempts if a["action"] == "landing"] == [
        "publisher_stub_page", "loaded"]
    landing = next(f for f in result.files if f.role == "landing_html")
    assert landing.url == CELL_PRESS_ARTICLE_URL


def test_the_cell_press_retry_never_invents_an_article_page():
    """cell.com carries Cell Press, not all of Elsevier. For
    10.1016/j.jhep.2019.01.003 it redirects to the journal's own host --
    journal-of-hepatology.eu, which is outside the proxy and so answers with
    Cloudflare's interstitial. That must leave the stub diagnosis standing rather
    than report a page we read."""
    page = CellPressRetryPage(
        retry_url="https://www.journal-of-hepatology.eu/article/S0168-8278(19)30012-1/fulltext",
        retry_title="Just a moment...", retry_links=CLOUDFLARE_LINKS,
        retry_content=CLOUDFLARE_HTML, retry_metas={})
    source = _source(proxy=PROXY)
    result = SourceResult(tier="proxy_browser")

    source._publisher_page(FakeContext(pages=[page]), _ElsevierIds(), result,
                           need_pdf=True, need_supplements=True)

    assert result.pdf_status == "publisher_stub_page"
    assert result.suppl_status == "page_not_parsed"
    retry = next(a for a in result.attempts if a["action"] == "landing_retry")
    assert retry["status"] == "javascript_challenge"
    # The stub is what we looked at, so the stub is what gets kept.
    assert next(f for f in result.files if f.role == "landing_html").url == _SD_STUB_URL


def test_a_challenge_page_never_reads_as_no_supplements():
    """Cloudflare's interstitial parses perfectly and lists nothing, so
    `find_supplements` returns `(parsed=True, [])` -- indistinguishable from an
    article that really has none, and `looks_blocked` is False because the title is
    not the publisher's own shell. `hasSuppl: Y` hides this for anything Europe PMC
    indexes; 10.1126/sciimmunol.aba4163 has no PMCID and is reached by this tier
    alone, so for that shape the fetcher would have settled on
    `unknown_none_found` for a page nobody ever read."""
    page = ProxyRedirectPage("https://www.cell.com/cell/fulltext/S0092867421005730",
                             title="Just a moment...", links=CLOUDFLARE_LINKS,
                             content=CLOUDFLARE_HTML)
    source = _source(proxy=PROXY)
    result = SourceResult(tier="proxy_browser")

    source._publisher_page(FakeContext(pages=[page]), _ElsevierIds(), result,
                           need_pdf=True, need_supplements=True)

    assert result.suppl_status == "page_not_parsed"
    assert result.pdf_status == "javascript_challenge"
    supplements = next(a for a in result.attempts if a["action"] == "supplements")
    assert supplements["status"] == "page_not_parsed"


def test_pmc_bot_check_says_use_headed():
    """NCBI serves headless Chrome a reCAPTCHA while plain HTTP gets the page."""
    page = FakePage(url="https://pmc.ncbi.nlm.nih.gov/articles/PMC1/",
                    title="Checking your browser - reCAPTCHA", content=RECAPTCHA_HTML)
    source = _source()
    result = SourceResult(tier="proxy_browser")

    class Ids:
        doi = "10.1/x"
        pmcid = "PMC1"

    source._pmc_supplements(FakeContext(pages=[page]), Ids(), result)
    assert result.suppl_status == "page_not_parsed"
    # What happened is a problem; what to do about it is advice, and only the
    # advice is dropped when a later tier gets the files anyway.
    assert any("bot check" in p for p in result.problems)
    assert any("--headed" in a for a in result.suppl_advice)
    assert not any("--headed" in p for p in result.problems)


def test_link_resolver_error_is_reported_not_parsed():
    """10.1016/j.xgen.2026.101304. The proxy routed this Elsevier DOI via
    linkinghub to ClinicalKey, which does not carry Cell Genomics and returned
    2562 bytes of `<ServiceErrorResponse><status>RESOURCE_NOT_FOUND</status>`
    with HTTP 200. The tier called that `loaded`, saved it as landing.html and
    let the generic adapter conclude `no_pdf_link` and `page_not_parsed` -- a
    knowable cause reported as "we could not find a PDF link on the page"."""
    resolver = ProxyRedirectPage(
        "https://www-clinicalkey-com.stanford.idm.oclc.org/content/playBy/pii/"
        "?v=S2666979X26001667",
        title="", links=[], content=RESOURCE_NOT_FOUND_XML)
    source = _source(proxy=PROXY)
    result = SourceResult(tier="proxy_browser")

    class Ids:
        doi = "10.1016/j.xgen.2026.101304"
        landing_url = "https://linkinghub.elsevier.com/retrieve/pii/S2666979X26001667"

    source._publisher_page(FakeContext(pages=[resolver]), Ids(), result,
                           need_pdf=True, need_supplements=True)
    assert result.pdf_status == "link_resolver_error"
    assert any("link_resolver_error" in p for p in result.problems)
    assert [a["status"] for a in result.attempts] == ["link_resolver_error"]
    # Never `unknown_none_found`: no supplement list was ever looked at, so
    # nothing licenses the claim that this article has none.
    assert result.suppl_status == "page_not_parsed"
    # The bytes are still kept -- they are the only way to debug the next one.
    assert [f.name for f in result.files if f.role == "landing_html"] == ["landing.html"]


def test_duo_landing_is_an_expired_session_not_a_missing_pdf():
    """An expired proxy session lands on Duo's prompt, which carries none of
    Stanford's login wording. Unrecognised, the guard below never fired and the
    2FA page was handed to the generic adapter, which said `no_pdf_link`."""
    duo = ProxyRedirectPage(DUO_PROMPT_URL, title="Duo Security", links=[],
                            content=DUO_PROMPT_HTML)
    source = _source(proxy=PROXY)
    result = SourceResult(tier="proxy_browser")

    class Ids:
        doi = "10.1126/science.adt8307"
        landing_url = "https://www.science.org/doi/10.1126/science.adt8307"

    source._publisher_page(FakeContext(pages=[duo]), Ids(), result,
                           need_pdf=True, need_supplements=True)
    assert result.pdf_status == "session_expired"
    assert result.suppl_status == "page_not_parsed"
    assert any("session_expired" in p for p in result.problems)


def test_session_expired_carries_the_command_that_fixes_it():
    """Naming a cause is half an answer. Until the remedy travelled with it, a
    batch printed `session_expired` once per DOI and never said which command
    fixes it -- and the user reached for `--headed`, which shows the browser but
    never waits for a login."""
    duo = ProxyRedirectPage(DUO_PROMPT_URL, title="Duo Security", links=[],
                            content=DUO_PROMPT_HTML)
    source = _source(proxy=PROXY)
    result = SourceResult(tier="proxy_browser")

    class Ids:
        doi = "10.1126/science.adt8307"
        landing_url = "https://www.science.org/doi/10.1126/science.adt8307"

    source._publisher_page(FakeContext(pages=[duo]), Ids(), result,
                           need_pdf=True, need_supplements=True)
    assert any("manuscript-fetch login" in p for p in result.problems)


def test_only_a_dead_session_gets_the_login_advice():
    """A paywalled or misrouted page is not fixed by logging in again, so the
    remedy must not be stapled to every refusal."""
    assert pb.denial_problem("session_expired", "https://x/") .endswith(pb.SESSION_REMEDY)
    for denial in ("paywalled", "proxy_not_configured", "link_resolver_error"):
        assert pb.denial_problem(denial, "https://x/") == f"{denial} at https://x/"


def test_unconfigured_proxy_retries_the_publisher_directly():
    """EZproxy having no stanza often means the host needs no proxy: Frontiers is
    fully open access yet failed outright as proxy_not_configured."""
    from tests.fakes import EZPROXY_HTML

    class TwoStep(FakePage):
        """Proxy hop returns the EZproxy error; the direct hop returns an article."""

        def __init__(self):
            super().__init__(title="An article")
            self.stage = 0

        def goto(self, url, wait_until=None, timeout=None):
            self.visited.append(url)
            self.url = url
            self.stage += 1

        def content(self):
            return EZPROXY_HTML.decode() if self.stage == 1 else "<html>article</html>"

    page = TwoStep()
    source = _source(proxy=PROXY)
    result = SourceResult(tier="proxy_browser")

    class Ids:
        doi = "10.3389/fdmed.2021.806294"
        landing_url = "https://www.frontiersin.org/articles/10.3389/fdmed.2021.806294/full"

    source._publisher_page(FakeContext(pages=[page]), Ids(), result,
                           need_pdf=True, need_supplements=False)
    assert len(page.visited) == 2, "should retry without the proxy"
    assert page.visited[1] == Ids.landing_url
    assert result.pdf_status != "proxy_not_configured"


def test_a_scraped_supplement_set_is_never_plain_fetched():
    """This is the site that reported `fetched` for 10.1016/j.xgen.2026.101304
    while holding 1 of its 12 supplements.

    Both links here are found and both download, so the old rule said `fetched` --
    "they exist and we have them". But `attempted` counts the anchors
    `looks_like_supplement` matched, and a heuristic cannot know what it missed,
    which is exactly how eleven files went unreported. Nothing about this page
    bounds the set, so the honest answer is `fetched_unverified`.
    """
    article = "https://www.nature.com/articles/x"
    page = ProxyRedirectPage(article, content=b"<html>real</html>", links=[
        {"url": "https://static-content.springer.com/esm/a_MOESM1_ESM.pdf",
         "text": "Supplementary Information"},
        {"url": "https://static-content.springer.com/esm/a_MOESM2_ESM.xlsx",
         "text": "Supplementary Table 1"},
    ])
    context = FakeContext(pages=[page], request=FakeRequest({
        "_ESM": FakeResponse(200, b"bytes", {"content-type": "application/pdf"})}))
    source = _source(proxy={"enabled": False})
    result = SourceResult(tier="proxy_browser")

    class Ids:
        doi = "10.1038/x"
        landing_url = article

    source._publisher_page(context, Ids(), result, need_pdf=False, need_supplements=True)
    assert result.suppl_status == "fetched_unverified"
    # Every file it identified did arrive -- this is not a failure report.
    note = next(a for a in result.attempts if a.get("action") == "supplements")
    assert (note["listed"], note["attempted"], note["fetched"]) == (2, 2, 2)


def test_landing_html_is_kept_for_debugging():
    page = FakePage(url="https://www.nature.com/articles/x", content=b"<html>real</html>",
                    metas={"citation_pdf_url": "https://www.nature.com/x.pdf"})
    context = FakeContext(pages=[page], request=FakeRequest({
        "x.pdf": FakeResponse(200, make_pdf(), {"content-type": "application/pdf"})}))
    source = _source(proxy={"enabled": False})
    result = SourceResult(tier="proxy_browser")

    class Ids:
        doi = "10.1038/x"
        landing_url = "https://www.nature.com/articles/x"

    source._publisher_page(context, Ids(), result, need_pdf=True, need_supplements=False)
    assert result.pdf_status == "ok"
    assert result.pdf and result.pdf.content[:4] == b"%PDF"
    assert [f.name for f in result.files if f.role == "landing_html"] == ["landing.html"]


def test_a_broken_tier_does_not_raise():
    """`fetch` must degrade to a recorded problem, never a traceback."""
    source = _source()

    class Ids:
        doi = "10.1/x"
        pmcid = None
        landing_url = None
        is_preprint = False

    def explode(*_a, **_k):
        raise RuntimeError("playwright died")

    source_ctx = pb.browser_context
    pb.browser_context = explode
    try:
        result = source.fetch(Ids(), need_pdf=True, need_supplements=True)
    finally:
        pb.browser_context = source_ctx
    assert any("browser tier failed" in p for p in result.problems)


# -- what `manuscript-fetch check` reports -----------------------------------
#
# `_authenticated_yet` produces the one line a user reads when the proxy stops
# working, and the remedy they reach for depends on which words are in it. Every
# case below is a different real answer that has to be told apart from the others:
# a dead session needs `login`, an unconfigured proxy stanza does not, and a page
# that is merely slow needs neither.

def _fetch_cfg(**overrides):
    cfg = {"proxy": dict(PROXY), "browser": {}}
    cfg.update(overrides)
    return cfg


def _pdf_context(pdf=None, status=200, headers=None):
    body = pdf if pdf is not None else make_pdf()
    return FakeContext(request=FakeRequest({
        "": FakeResponse(status, body, headers or {"content-type": "application/pdf"}),
    }))


def test_a_validated_pdf_is_the_only_thing_that_proves_access():
    """Not "the page loaded" and not "there is a PDF link": both are true on a
    publisher's paywall shell. The proof is bytes that parse as the article."""
    page = FakePage(url="https://www-nature-com.stanford.idm.oclc.org/articles/x",
                    metas={"citation_pdf_url": "https://www-nature-com.x/articles/x.pdf"},
                    content=b"<html><body>A real article about islets</body></html>")
    alive, detail = _authenticated(page, _pdf_context())

    assert alive is True
    assert "downloaded and validated the PDF" in detail
    assert "3 pages" in detail and "nature adapter" in detail


def _authenticated(page, context=None, cfg=None):
    return pb._authenticated_yet(page, context, cfg or _fetch_cfg())


def test_an_expired_session_is_named_before_the_body_is_ever_read():
    """The ordering that makes `check` work at all: on an expired session Stanford's
    self-submitting SAML form never stops navigating, so `content()` hangs forever.
    `page.title()` answers instantly, so the diagnosis has to come from there."""
    page = FakePage(url=SAML_REDIRECT_URL, title=SAML_REDIRECT_TITLE,
                    content=RuntimeError("content() would hang here"))
    alive, detail = _authenticated(page, _pdf_context())

    assert alive is False
    assert detail.startswith("session_expired at ")


def test_a_page_that_cannot_be_read_is_diagnosed_from_its_navigation_marker():
    """`stable_content` gave up. Rather than "not readable yet", look at where the
    browser actually is -- which on a dead session is Duo."""
    page = FakePage(url=DUO_PROMPT_URL, title="Duo Security",
                    content=RuntimeError("still navigating"))
    alive, detail = _authenticated(page, _pdf_context())

    assert alive is False
    assert "session_expired" in detail


def test_an_unreadable_page_with_no_diagnosis_says_only_that():
    """A page that is simply slow must not be blamed on the session, or `check`
    sends people to re-run `login` for a network hiccup."""
    page = FakePage(url="https://www-nature-com.stanford.idm.oclc.org/articles/x",
                    title="Nature", content=RuntimeError("timeout"))
    alive, detail = _authenticated(page, _pdf_context())

    assert alive is False
    assert "page not readable yet (RuntimeError)" in detail
    assert "session_expired" not in detail


def test_a_denial_in_the_body_is_reported_with_the_url():
    """EZproxy's "not been configured for access" is a proxy-stanza problem, not a
    login problem, and the URL is what identifies the host that is missing one."""
    page = FakePage(url="https://www-example-com.stanford.idm.oclc.org/x",
                    content=EZPROXY_HTML * 20)
    alive, detail = _authenticated(page, _pdf_context())

    assert alive is False
    assert detail.startswith("proxy_not_configured at https://www-example-com")


def test_a_page_with_no_pdf_link_names_the_adapter_that_looked():
    """Which adapter ran is the first thing to check when a publisher redesigns, so
    it goes in the line rather than needing a re-run with logging."""
    page = FakePage(url="https://www-nature-com.stanford.idm.oclc.org/articles/x",
                   content=b"<html><body>An article page with no PDF anywhere</body></html>")
    alive, detail = _authenticated(page, _pdf_context())

    assert alive is False
    assert "no PDF link yet (adapter=nature)" in detail


def test_an_adapter_that_raises_is_treated_as_finding_nothing():
    """A selector that throws is the adapter's problem, not evidence about the
    session, and `check` must still print a line rather than a traceback."""
    class Exploding(FakePage):
        def get_attribute(self, selector, attribute, timeout=None):
            raise RuntimeError("selector engine crashed")

        def eval_on_selector_all(self, selector, script):
            raise RuntimeError("selector engine crashed")

    page = Exploding(url="https://www-nature-com.stanford.idm.oclc.org/articles/x",
                     content=b"<html><body>An article about islets</body></html>")
    alive, detail = _authenticated(page, _pdf_context())

    assert alive is False
    assert "no PDF link yet" in detail


def test_a_pdf_link_with_no_context_is_honest_about_not_testing_it():
    """`check_session` always passes a context; the guard exists because finding a
    link is not access, and saying "found one but could not test it" is the only
    truthful answer without one."""
    page = FakePage(url="https://www-nature-com.stanford.idm.oclc.org/articles/x",
                    metas={"citation_pdf_url": "https://www-nature-com.x/articles/x.pdf"},
                    content=b"<html><body>An article about islets</body></html>")
    alive, detail = _authenticated(page, context=None)

    assert alive is False
    assert "could not test it" in detail


def _linked_page():
    return FakePage(url="https://www-nature-com.stanford.idm.oclc.org/articles/x",
                    metas={"citation_pdf_url": "https://www-nature-com.x/articles/x.pdf"},
                    content=b"<html><body>An article about islets</body></html>")


def test_a_download_that_raises_is_reported_with_its_exception_type():
    context = FakeContext(request=FakeRequest({
        "": FakeResponse(200, RuntimeError("connection reset")),
    }))
    alive, detail = _authenticated(_linked_page(), context)

    assert alive is False
    assert "download failed (RuntimeError: connection reset)" in detail


def test_an_http_error_on_the_pdf_names_the_status_and_the_url():
    alive, detail = _authenticated(_linked_page(), _pdf_context(status=403))

    assert alive is False
    assert "returned HTTP 403" in detail
    assert "articles/x.pdf" in detail


def test_a_pdf_that_is_really_a_paywall_page_is_named_as_such():
    """The case the whole function exists for: HTTP 200, `application/pdf`, and a
    body that is a sign-in page. Reporting `alive` here is how a batch of 53 papers
    stores 53 paywall stubs."""
    alive, detail = _authenticated(_linked_page(), _pdf_context(pdf=PAYWALL_HTML * 20))

    assert alive is False
    assert "PDF rejected as 'paywalled'" in detail


# -- check_session and interactive_login -------------------------------------

def _stub_browser_context(monkeypatch, context):
    """Replace the Playwright-backed context manager with a canned context."""
    from contextlib import contextmanager

    @contextmanager
    def fake(fetch_cfg, headless=None, restore=True):
        yield context

    monkeypatch.setattr(pb, "browser_context", fake)


def test_check_session_reports_a_live_session(monkeypatch, tmp_path):
    context = _pdf_context()
    context._queued = [_linked_page()]
    _stub_browser_context(monkeypatch, context)

    alive, detail = pb.check_session(_fetch_cfg(browser={"profile_dir": str(tmp_path)}))
    assert alive is True and "validated the PDF" in detail


def test_a_missing_playwright_is_a_check_answer_not_a_crash(monkeypatch, tmp_path):
    """`check` is the command someone runs *because* something is wrong, so it has
    to survive the tier's own dependency being absent."""
    from contextlib import contextmanager

    @contextmanager
    def missing(fetch_cfg, headless=None, restore=True):
        raise ImportError("The proxy_browser tier needs Playwright")
        yield  # pragma: no cover

    monkeypatch.setattr(pb, "browser_context", missing)
    alive, detail = pb.check_session(_fetch_cfg(browser={"profile_dir": str(tmp_path)}))

    assert alive is False
    assert "needs Playwright" in detail


def test_any_other_failure_in_check_is_named_by_type(monkeypatch, tmp_path):
    from contextlib import contextmanager

    @contextmanager
    def broken(fetch_cfg, headless=None, restore=True):
        raise RuntimeError("chromium failed to launch")
        yield  # pragma: no cover

    monkeypatch.setattr(pb, "browser_context", broken)
    alive, detail = pb.check_session(_fetch_cfg(browser={"profile_dir": str(tmp_path)}))

    assert alive is False
    assert detail == "RuntimeError: chromium failed to launch"


def test_login_detects_success_on_its_own_and_snapshots_the_cookies(monkeypatch, tmp_path,
                                                                   capsys):
    """The promise the printed instructions make: nothing to do after signing in.
    The snapshot has to happen before the context closes, because session cookies
    die with it."""
    context = _pdf_context()
    context._queued = [_linked_page()]
    _stub_browser_context(monkeypatch, context)
    monkeypatch.setattr(pb.time, "sleep", lambda _s: None)

    cfg = _fetch_cfg(browser={"profile_dir": str(tmp_path / "chrome")})
    assert pb.interactive_login(cfg, timeout_seconds=10) == 0

    out = capsys.readouterr().out
    assert "Logged in." in out
    assert "manuscript-fetch check" in out
    assert pb.state_path(cfg).exists(), "cookies must be snapshotted inside the context"


def test_login_gives_up_at_its_deadline_and_still_saves_what_it_has(monkeypatch, tmp_path,
                                                                   capsys):
    """A user who never finishes Duo should still get a usable snapshot -- and a
    message that points at `check` rather than claiming failure outright."""
    context = _pdf_context()
    # A page with no PDF link never authenticates, so the loop runs to the deadline.
    context._queued = [FakePage(url="https://login.stanford.edu/idp", title="Stanford Login",
                                content=SSO_HTML * 20)]
    _stub_browser_context(monkeypatch, context)

    clock = {"t": 0.0}
    monkeypatch.setattr(pb.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(pb.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))

    cfg = _fetch_cfg(browser={"profile_dir": str(tmp_path / "chrome")})
    assert pb.interactive_login(cfg, timeout_seconds=6) == 1

    out = capsys.readouterr().out
    assert "Could not confirm access" in out
    assert "timed out after 6s" in out
    assert "try: manuscript-fetch check" in out


def test_login_notices_the_window_being_closed(monkeypatch, tmp_path, capsys):
    """Closing the window is how people signal "done" or "give up". Either way it
    is not a timeout, and saying so avoids a 600-second wait on an empty browser."""
    context = _pdf_context()
    context._queued = [_linked_page()]
    _stub_browser_context(monkeypatch, context)

    real_new_page = context.new_page

    def new_page_then_vanish():
        page = real_new_page()
        context.pages.clear()          # the user closed it
        return page

    context.new_page = new_page_then_vanish
    monkeypatch.setattr(pb.time, "sleep", lambda _s: None)

    cfg = _fetch_cfg(browser={"profile_dir": str(tmp_path / "chrome")})
    assert pb.interactive_login(cfg, timeout_seconds=10) == 1
    assert "browser window closed by user" in capsys.readouterr().out


def test_a_navigation_failure_at_login_is_a_warning_not_an_abort(monkeypatch, tmp_path,
                                                                capsys):
    """The proxy may be slow to answer the very first hop; the browser is open and
    the user can still log in, so this must not end the command."""
    context = _pdf_context()
    context._queued = [FakePage(url="https://stanford.idm.oclc.org/login",
                                goto_error=RuntimeError("net::ERR_TIMED_OUT"),
                                content=SSO_HTML * 20)]
    _stub_browser_context(monkeypatch, context)
    monkeypatch.setattr(pb.time, "sleep", lambda _s: None)

    clock = {"t": 0.0}
    monkeypatch.setattr(pb.time, "monotonic", lambda: clock["t"])

    cfg = _fetch_cfg(browser={"profile_dir": str(tmp_path / "chrome")})
    pb.interactive_login(cfg, timeout_seconds=0)
    assert "navigation warning: net::ERR_TIMED_OUT" in capsys.readouterr().out


# -- a PDF failure has to reach the terminal, not just the manifest ----------
#
# `SourceResult.note()` fills `attempts`, which reaches `manifest.json`.
# `result.problems` is what `cli._report` prints under the summary line. `_fetch_pdf`
# used to write only the former, so a real batch over 55 DOIs reported
#
#     10.1016/j.oraloncology.2021.105348  failed  pdf=download_failed  ...  tiers=proxy_browser
#         ! ncbi idconv: Identifier not found in PMC
#
# where the only "!" line came from identifier resolution and nothing said what the
# browser tier had actually hit. The diagnosis was in the manifest the whole time --
# `http_status: 403` -- which is worse than losing it, because the summary looks
# complete. Every branch below is one the reported failure could have come from.

SD_HOST = "https://www-sciencedirect-com.stanford.idm.oclc.org"
SD_ARTICLE = f"{SD_HOST}/science/article/pii/S1368837521002293"


class _ElsevierPdfIds:
    doi = "10.1016/j.oraloncology.2021.105348"
    landing_url = "https://www.sciencedirect.com/science/article/pii/S1368837521002293"


def _run_pdf(response, *, metas=None, links=None, denial=None, adapter_url=SD_ARTICLE):
    result = SourceResult(tier="proxy_browser")
    context = FakeContext(request=FakeRequest({"": response})) if response else FakeContext()
    page = FakePage(url=SD_ARTICLE, metas=metas or {}, links=links or [],
                    content=b"<html><body>an article page</body></html>")
    _source()._fetch_pdf(context, page, adapter_for(adapter_url), _ElsevierPdfIds(),
                         SD_ARTICLE, result, denial)
    return result


PDF_META = {"citation_pdf_url": f"{SD_ARTICLE}/pdfft"}


def test_an_http_error_on_the_pdf_says_which_status(capsys):
    """The reported case. 403 from ScienceDirect's `pdfft` is a specific, actionable
    fact and it was going only into the manifest."""
    result = _run_pdf(FakeResponse(403, b""), metas=PDF_META)

    assert result.pdf_status == "download_failed"
    assert len(result.problems) == 1
    assert "HTTP 403" in result.problems[0]
    assert "page navigation did not help either" in result.problems[0]
    assert f"{SD_ARTICLE}/pdfft" in result.problems[0]


def test_a_transport_failure_on_the_pdf_names_the_exception():
    result = _run_pdf(FakeResponse(200, RuntimeError("net::ERR_ABORTED")), metas=PDF_META)

    assert result.pdf_status == "download_failed"
    assert "RuntimeError: net::ERR_ABORTED" in result.problems[0]


# A host with no adapter of its own and no `citation_pdf_url` on the page. Not an
# Elsevier URL: `ElsevierAdapter.find_pdf_url` constructs a `pdfft` URL from the PII
# whether or not the page links one, so the no-link branch is unreachable there.
PLAIN_HOST = "https://journals.example.org/article/12345"


def test_no_pdf_link_says_which_adapter_looked():
    """Which adapter ran is the first thing to check when a publisher redesigns."""
    result = _run_pdf(None, adapter_url=PLAIN_HOST)

    assert result.pdf_status == "not_found"
    assert "no PDF link found on" in result.problems[0]
    assert "adapter=" in result.problems[0]


def test_a_denial_reaching_the_pdf_path_is_named_once():
    """`paywalled` and `javascript_challenge` fall through to here -- only the three
    hard denials return earlier -- so this branch owns reporting them."""
    result = _run_pdf(None, denial="javascript_challenge", adapter_url=PLAIN_HOST)

    assert result.pdf_status == "javascript_challenge"
    assert result.problems == ["javascript_challenge at " + SD_ARTICLE]


def test_a_paywall_served_as_a_pdf_reports_its_size():
    """`paywalled` at 2 KB is a stub page; the same status at 900 KB would mean the
    heuristics rejected a real article. The byte count is what tells them apart."""
    result = _run_pdf(
        FakeResponse(200, PAYWALL_HTML * 20, {"content-type": "application/pdf"}),
        metas=PDF_META)

    assert result.pdf_status == "paywalled"
    assert "rejected as 'paywalled'" in result.problems[0]
    assert "bytes" in result.problems[0]


def test_a_pdf_that_arrives_reports_no_problem_at_all():
    """The guard against over-reporting: a success must stay quiet, or every batch
    line grows a "!" and the ones that matter stop standing out."""
    result = _run_pdf(FakeResponse(200, make_pdf(), {"content-type": "application/pdf"}),
                      metas=PDF_META)

    assert result.pdf_status == "ok"
    assert result.problems == []
    assert result.pdf is not None


def test_the_manifest_still_carries_what_the_terminal_now_shows():
    """The fix surfaces the diagnosis; it must not move it. `attempts` is what a
    curator reads months later, and it keeps the structured fields."""
    result = _run_pdf(FakeResponse(403, b""), metas=PDF_META)

    pdf_notes = [a for a in result.attempts if a["action"] == "pdf"]
    assert pdf_notes and pdf_notes[-1]["http_status"] == 403
    assert pdf_notes[-1]["status"] == "download_failed"


# -- a non-Cell-Press Elsevier paper -----------------------------------------
#
# The wall reported on 10.1016/j.jhep.2020.05.039, and on 10.1016/j.jhep.2019.01.003
# a year before it. cell.com carries Cell Press; for any other Elsevier journal the
# retry redirects to the journal's own host, which is outside the proxy and answers
# with Cloudflare. Two things were wrong with how that ended:
#
#   - the user was told to "try --headed", which cannot possibly help. The obstacle
#     is which host holds the article, not whether a browser is visible.
#   - the tier gave up having made zero download attempts -- verified, no HTTP
#     requests at all between the failed retry and the verdict -- even though the
#     PII is in the stub's own URL and `/pdfft` is a different endpoint.

JHEP_HOST = "https://www.journal-of-hepatology.eu"
JHEP_ARTICLE = f"{JHEP_HOST}/article/S0168-8278(19)30012-1/fulltext"
SD_PDFFT = ("https://www-sciencedirect-com.stanford.idm.oclc.org"
            "/science/article/pii/S0092867421005730/pdfft?isDTMRedir=true&download=true")


def _off_cell_press_page():
    """The stub, then a retry that lands off the proxy on the journal's own host."""
    return CellPressRetryPage(
        retry_url=JHEP_ARTICLE, retry_title="Just a moment...",
        retry_links=CLOUDFLARE_LINKS, retry_content=CLOUDFLARE_HTML, retry_metas={})


def _run_stub(pdfft_response=None, page=None):
    page = page or _off_cell_press_page()
    routes = {"pdfft": pdfft_response} if pdfft_response else {}
    context = FakeContext(pages=[page], request=FakeRequest(routes))
    result = SourceResult(tier="proxy_browser")
    _source(proxy=PROXY)._publisher_page(context, _ElsevierIds(), result,
                                         need_pdf=True, need_supplements=True)
    return result, context


def test_a_journal_cell_com_does_not_carry_is_not_blamed_on_headless():
    """`--headed` is the wrong advice here and naming it wastes the user's next run.
    Say which host the redirect went to instead -- that is the actual fact."""
    result, _ = _run_stub()

    problem = next(p for p in result.problems if "stub page" in p)
    assert "--headed" not in problem
    assert "cell.com carries Cell Press only" in problem
    assert "journal-of-hepatology.eu" in problem
    assert "no proxied route to this journal" in problem


def test_a_cell_press_paper_that_still_stubs_keeps_the_headed_hint():
    """The distinction the message turns on: staying on cell.com and being unreadable
    is the ordinary stub, where showing the browser is at least worth a try."""
    page = CellPressRetryPage(retry_url=CELL_PRESS_ARTICLE_URL, retry_title="ScienceDirect",
                              retry_links=[], retry_content=b"<html></html>", retry_metas={})
    result, _ = _run_stub(page=page)

    problem = next(p for p in result.problems if "stub page" in p)
    assert "--headed" in problem
    assert "cell.com carries Cell Press only" not in problem


def test_the_pdf_endpoint_is_tried_before_giving_up():
    """The attempt that did not exist. Whether ScienceDirect serves it is unmeasured;
    that it is now asked at all is the point, and the manifest records the answer."""
    result, context = _run_stub(
        pdfft_response=FakeResponse(200, make_pdf(), {"content-type": "application/pdf"}))

    assert result.pdf_status == "ok"
    assert result.pdf is not None and result.pdf.name == "fulltext.pdf"
    assert any("pdfft" in url for url in context.request.gets), "the endpoint was asked"

    note = next(a for a in result.attempts if a.get("via") == "stub_pdf_attempt")
    assert note["status"] == "ok"

    # Supplements are still lost -- only the article page lists those.
    assert result.suppl_status == "page_not_parsed"
    problem = next(p for p in result.problems if "stub page" in p)
    assert "PDF was still recovered from the PDF endpoint" in problem


def test_a_refused_pdf_endpoint_leaves_the_stub_diagnosis_standing():
    """The likely outcome, and it must not make things worse: the verdict stays
    `publisher_stub_page` and the failed attempt is recorded rather than hidden."""
    result, _ = _run_stub(pdfft_response=FakeResponse(403, b""))

    assert result.pdf_status == "publisher_stub_page"
    assert result.pdf is None
    assert any(a.get("status") == "stub_pdf_attempt_failed" for a in result.attempts)
    assert "PDF was still recovered" not in next(p for p in result.problems if "stub page" in p)


def test_the_attempt_is_built_on_the_proxied_origin_not_the_journal_host():
    """By the time this runs the page has navigated to journal-of-hepatology.eu, so
    reading the origin off the live page would drop the proxy and the entitlement
    with it. It is constructed from the stub URL for that reason."""
    result, context = _run_stub(pdfft_response=FakeResponse(404, b""))

    asked = [u for u in context.request.gets if "pdfft" in u]
    assert asked, "the endpoint should have been asked"
    assert "stanford.idm.oclc.org" in asked[0]
    assert "journal-of-hepatology.eu" not in asked[0]


def test_sciencedirect_pdf_url_matches_what_the_adapter_builds():
    """Two places construct this shape. If they drift, the stub attempt asks for a
    URL the working path never validated."""
    assert pb.sciencedirect_pdf_url(_SD_STUB_URL, "S0092867421005730") == SD_PDFFT


def test_supplements_that_were_listed_and_lost_say_so():
    """Same split `_fetch_pdf` had: every per-file refusal went to `attempts` and none
    of it reached the terminal, so a page listing twelve supplements and delivering
    none printed a bare count. The cap and the challenge give-up already reported;
    this covers the ordinary refusal."""
    source = _source()
    result = SourceResult(tier="proxy_browser")
    context = FakeContext(request=FakeRequest({"": FakeResponse(403, b"")}))
    links = [{"url": f"https://ars.els-cdn.com/mmc{n}.xlsx", "text": f"Table S{n}"}
             for n in range(1, 4)]

    fetched, attempted = source._download_all(context, links, "https://x", result, "elsevier")

    assert (fetched, attempted) == (0, 3)
    problem = next(p for p in result.problems if "could not be fetched" in p)
    assert "3 of 3 supplementary file(s)" in problem
    assert "elsevier" in problem


def test_a_partial_supplement_set_names_the_shortfall():
    class OneWorks(FakeRequest):
        def get(self, url, headers=None):
            self.gets.append(url)
            if url.endswith("mmc1.xlsx"):
                return FakeResponse(200, b"real bytes", {"content-type": "application/vnd.ms-excel"})
            return FakeResponse(403, b"")

    result = SourceResult(tier="proxy_browser")
    links = [{"url": f"https://ars.els-cdn.com/mmc{n}.xlsx", "text": f"T{n}"} for n in (1, 2)]
    fetched, attempted = _source()._download_all(
        FakeContext(request=OneWorks()), links, "https://x", result, "elsevier")

    assert (fetched, attempted) == (1, 2)
    assert any("1 of 2 supplementary file(s)" in p for p in result.problems)


def test_a_complete_supplement_set_reports_no_problem():
    """The over-reporting guard, same as for the PDF: a clean fetch stays quiet."""
    result = SourceResult(tier="proxy_browser")
    context = FakeContext(request=FakeRequest({
        "": FakeResponse(200, b"real bytes", {"content-type": "application/vnd.ms-excel"})}))
    links = [{"url": "https://ars.els-cdn.com/mmc1.xlsx", "text": "T1"}]

    fetched, attempted = _source()._download_all(context, links, "https://x", result, "elsevier")
    assert (fetched, attempted) == (1, 1)
    assert result.problems == []
