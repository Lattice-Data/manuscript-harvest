"""The proxy_browser tier, offline.

This is the module that needed tests most: it is the largest in the package, the
most fragile, and until now every bug in it was found by running real DOIs
against real publishers. Each test below pins a behaviour that a live run
actually got wrong at some point, so a regression shows up here instead of in a
half-empty corpus directory weeks later.
"""

import json

import pytest

from curation.fetch.adapters import adapter_for, candidate_hosts
from curation.fetch.sources import proxy_browser as pb
from curation.fetch.sources.base import ROLE_PDF, ROLE_SUPPLEMENT, SourceResult
from tests.fakes import (
    POW_HTML,
    RECAPTCHA_HTML,
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
])
def test_filename_for(url, headers, expected):
    assert pb._filename_for(url, headers) == expected


# -- download paths ----------------------------------------------------------

def _source(**config):
    # challenge_wait_seconds: 0 keeps the suite fast; the live default is 8.
    base = {"max_files": 50, "max_file_mb": 200,
            "browser": {"headless": True, "challenge_wait_seconds": 0}}
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
    assert any("--headed" in p for p in result.problems)


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
