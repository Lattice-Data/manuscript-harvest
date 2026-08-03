"""The open-access tiers, at the level of the whole tier rather than its helpers.

Two of the three reach a file list by pattern-matching rendered HTML rather than by
reading an enumeration, and those two had almost no offline coverage -- 14% and 17%
-- which is the worst place for it. A markup change on either page shrinks the list
silently, and the whole point of the status taxonomy is that a shrunk list must not
read as "this paper has no supplements".

So what these tests defend is mostly the *naming* of outcomes:

- `none_listed` when the page is authoritative and empty,
- `page_not_parsed` when it is not,
- `fetched_unverified` when a regex over HTML is all the evidence there is,
- and plain `fetched` only for the OA package, where unpacking a deposit really
  does bound the set.

`test_units.py` covers the pure helpers these tiers call (`_classify`,
`_unpack_tgz`, `ftp_to_https`); this file covers what the tier decides.
"""

import pytest

from manuscript_harvest.fetch.fetcher import _best_pdf_status
from manuscript_harvest.fetch.http import HttpError, Response
from manuscript_harvest.fetch.identifiers import Identifiers
from manuscript_harvest.fetch.sources.biorxiv import (
    BiorxivSource,
    _media_links,
    _version_key,
)
from manuscript_harvest.fetch.sources.europepmc import RENDER_PDF, EuropePmcSource
from manuscript_harvest.fetch.sources.pmc_oa import PmcOaSource
from manuscript_harvest.fetch.sources.pmc_supplements import (
    PmcSupplementsSource,
    _springer_url,
)
from manuscript_harvest.fetch.validate import PDF_DIAGNOSES, better_pdf_failure
from tests.fakes import (
    DOI,
    PAYWALL_HTML,
    PMCID,
    POW_HTML,
    FakeHttp,
    biorxiv_details_json,
    make_pdf,
    make_tgz,
    make_zip,
)

PREPRINT = "10.1101/2024.01.23.576878"

DETAILS_BIORXIV = "details/biorxiv"
DETAILS_MEDRXIV = "details/medrxiv"
FULL_PDF = ".full.pdf"
SUPPL_PAGE = ".supplementary-material"
MEDIA = "/embed/media-"
JATS = "x.xml"


def _preprint_ids(doi: str = PREPRINT) -> Identifiers:
    return Identifiers(doi=doi, doi_raw=doi, epmc_source="PPR")


def _supplement_page(*names: str) -> bytes:
    """A bioRxiv supplement page, reduced to the anchors that matter.

    The `/DC<n>/embed/media-<n>.<ext>` shape is the only thing that identifies a
    supplement here: the link text is "Download" for every one of them.
    """
    anchors = "".join(
        f'<a href="/content/biorxiv/early/2024/01/25/2024.01.23.576878'
        f'/DC{index}/embed/{name}">Download</a>'
        for index, name in enumerate(names, start=1)
    )
    return f"<html><body><h1>Supplementary Material</h1>{anchors}</body></html>".encode()


def _biorxiv_http(routes=None, **details) -> FakeHttp:
    base = {
        DETAILS_BIORXIV: (200, biorxiv_details_json(**details), "application/json"),
        FULL_PDF: (200, make_pdf(), "application/pdf"),
        JATS: (200, b"<article><body/></article>", "application/xml"),
        SUPPL_PAGE: (200, _supplement_page("media-1.pdf", "media-2.zip"), "text/html"),
        MEDIA: (200, b"supplement payload", "application/octet-stream"),
    }
    base.update(routes or {})
    return FakeHttp(base)


def _statuses(result, action: str):
    return [a["status"] for a in result.attempts if a["action"] == action]


# -- bioRxiv: which DOIs the tier claims -------------------------------------

@pytest.mark.parametrize("ids,expected", [
    (_preprint_ids(), True),
    (Identifiers(doi="10.64898/2026.02.15.704933", doi_raw="x"), True),   # openRxiv
    (Identifiers(doi="10.21203/rs.3.rs-1", doi_raw="x", epmc_source="PPR"), True),
    (Identifiers(doi=DOI, doi_raw=DOI), False),
])
def test_applies_to_preprints_from_any_server(ids, expected):
    """Gating on 10.1101 alone silently skipped every newly posted preprint, and a
    Research Square DOI is a preprint only because Europe PMC says source=PPR."""
    assert BiorxivSource(FakeHttp()).applies(ids) is expected


# -- bioRxiv: the details API ------------------------------------------------

def test_details_drives_the_content_urls_off_the_latest_version():
    """The API lists every version; fetching v1 of a revised preprint would store
    superseded text under a DOI that now means something else."""
    http = _biorxiv_http(version="3")
    result = BiorxivSource(http).fetch(_preprint_ids(), need_pdf=True, need_supplements=False)

    assert f"{PREPRINT}v3.full.pdf" in http.calls[1]
    assert result.pdf_status == "ok"
    assert [f.name for f in result.files] == ["fulltext.pdf", "fulltext.nxml"]


def test_latest_version_wins_regardless_of_listing_order():
    """`max` over the collection, not "the last record" -- the API does not promise
    an order, and a non-numeric version must not crash the comparison."""
    collection = (
        b'{"collection": [{"version": "2", "server": "biorxiv", "jatsxml": "https://x/x.xml"},'
        b' {"version": "10", "server": "biorxiv", "jatsxml": "https://x/x.xml"},'
        b' {"version": null, "server": "biorxiv", "jatsxml": "https://x/x.xml"}]}'
    )
    http = _biorxiv_http({DETAILS_BIORXIV: (200, collection, "application/json")})
    BiorxivSource(http).fetch(_preprint_ids(), need_pdf=True, need_supplements=False)
    assert "v10.full.pdf" in http.calls[1]


def test_medrxiv_is_tried_second_and_sets_its_own_host():
    """A medRxiv preprint answers on the medrxiv endpoint and its files live on
    medrxiv.org -- building medRxiv URLs on the bioRxiv host 404s every artifact."""
    http = _biorxiv_http({
        DETAILS_BIORXIV: (200, b'{"collection": []}', "application/json"),
        DETAILS_MEDRXIV: (200, biorxiv_details_json(server="medrxiv"), "application/json"),
    })
    result = BiorxivSource(http).fetch(_preprint_ids(), need_pdf=True, need_supplements=False)

    assert _statuses(result, "details") == ["no_record", "ok"]
    assert any("www.medrxiv.org" in url for url in http.calls)
    assert result.pdf_status == "ok"


def test_an_unknown_server_falls_back_to_the_biorxiv_host():
    """`CONTENT_BASE.get(server, ...)` -- a server name the map does not carry must
    still produce a URL rather than a KeyError mid-fetch."""
    http = _biorxiv_http({
        DETAILS_BIORXIV: (200, biorxiv_details_json(server="somerxiv"), "application/json"),
    })
    result = BiorxivSource(http).fetch(_preprint_ids(), need_pdf=True, need_supplements=False)
    assert result.pdf_status == "ok"
    assert any("www.biorxiv.org" in url for url in http.calls)


@pytest.mark.parametrize("response,expected", [
    ((200, b'{"collection": []}', "application/json"), "no_record"),
    ((200, b"not json at all", "application/json"), "unparseable_json"),
    ((503, b"", ""), "http_error"),
])
def test_every_details_failure_is_named_in_the_attempts(response, expected):
    """Both servers are asked and both answers are recorded: a manifest that says
    only "not_found" cannot be told apart from one nobody looked for."""
    http = _biorxiv_http({DETAILS_BIORXIV: response, DETAILS_MEDRXIV: response})
    result = BiorxivSource(http).fetch(_preprint_ids(), need_pdf=True, need_supplements=True)

    assert _statuses(result, "details") == [expected, expected]
    assert result.pdf_status == "not_found"
    assert result.problems == ["neither bioRxiv nor medRxiv has a record for this DOI"]
    assert result.files == []


def test_a_transport_failure_on_details_is_recorded_not_raised():
    class Exploding(FakeHttp):
        def get(self, url, params=None, accept=None, allow_redirects=True):
            if "api.biorxiv.org" in url:
                raise HttpError("connection reset")
            return super().get(url, params, accept, allow_redirects)

    result = BiorxivSource(Exploding()).fetch(
        _preprint_ids(), need_pdf=True, need_supplements=False)
    assert _statuses(result, "details") == ["request_failed", "request_failed"]
    assert result.pdf_status == "not_found"


def test_details_is_not_requested_when_nothing_is_needed():
    """`need_pdf=False, need_supplements=False` still costs two API calls, but must
    leave both statuses None: not asked is not the same as asked and missing."""
    http = _biorxiv_http()
    result = BiorxivSource(http).fetch(_preprint_ids(), need_pdf=False, need_supplements=False)
    assert result.pdf_status is None and result.suppl_status is None
    assert result.files == []


@pytest.mark.parametrize("version_value,expected", [
    ("3", 3), (4, 4), (None, 0), ("v2", 0), ("", 0),
])
def test_version_key_never_raises_on_junk(version_value, expected):
    assert _version_key(version_value) == expected


# -- bioRxiv: PDF and JATS ---------------------------------------------------

@pytest.mark.parametrize("response", [(404, b"", ""), (500, b"", "")])
def test_a_missing_pdf_is_download_failed(response):
    http = _biorxiv_http({FULL_PDF: response})
    result = BiorxivSource(http).fetch(_preprint_ids(), need_pdf=True, need_supplements=False)
    assert result.pdf_status == "download_failed"
    assert result.pdf is None


def test_a_pdf_that_is_not_a_pdf_is_rejected_by_name():
    """The URL is constructed rather than discovered, so a server that answers 200
    with an error page would otherwise be stored as the article."""
    http = _biorxiv_http({FULL_PDF: (200, b"<html>error</html>" * 40, "text/html")})
    result = BiorxivSource(http).fetch(_preprint_ids(), need_pdf=True, need_supplements=False)
    assert result.pdf_status == "not_a_pdf"
    assert result.pdf is None


def test_pdf_transport_failure_is_download_failed():
    class Exploding(FakeHttp):
        def get(self, url, params=None, accept=None, allow_redirects=True):
            if FULL_PDF in url:
                raise HttpError("read timeout")
            return super().get(url, params, accept, allow_redirects)

    result = BiorxivSource(Exploding(_biorxiv_http().routes)).fetch(
        _preprint_ids(), need_pdf=True, need_supplements=False)
    assert result.pdf_status == "download_failed"
    assert _statuses(result, "pdf") == ["download_failed"]


@pytest.mark.parametrize("response,expected", [
    ((404, b"", ""), "http_error"),
    ((200, b"", "application/xml"), "http_error"),   # 200 with no body is not XML
])
def test_a_failed_jats_fetch_never_costs_the_pdf(response, expected):
    """The XML is a bonus the details API hands over for free. Losing it must not
    change the PDF's status or drop the PDF from the result."""
    http = _biorxiv_http({JATS: response})
    result = BiorxivSource(http).fetch(_preprint_ids(), need_pdf=True, need_supplements=False)

    assert result.pdf_status == "ok"
    assert [f.name for f in result.files] == ["fulltext.pdf"]
    assert _statuses(result, "jats") == [expected]


def test_jats_is_skipped_when_the_details_record_carries_no_link():
    collection = b'{"collection": [{"version": "1", "server": "biorxiv", "jatsxml": null}]}'
    http = _biorxiv_http({DETAILS_BIORXIV: (200, collection, "application/json")})
    result = BiorxivSource(http).fetch(_preprint_ids(), need_pdf=True, need_supplements=False)
    assert _statuses(result, "jats") == []
    assert [f.name for f in result.files] == ["fulltext.pdf"]


def test_jats_transport_failure_is_recorded():
    class Exploding(FakeHttp):
        def get(self, url, params=None, accept=None, allow_redirects=True):
            if JATS in url:
                raise HttpError("dns failure")
            return super().get(url, params, accept, allow_redirects)

    result = BiorxivSource(Exploding(_biorxiv_http().routes)).fetch(
        _preprint_ids(), need_pdf=True, need_supplements=False)
    assert _statuses(result, "jats") == ["request_failed"]
    assert result.pdf_status == "ok"


# -- bioRxiv: the supplement scrape ------------------------------------------

def test_supplements_that_all_arrive_are_unverified_not_fetched():
    """bioRxiv owns its preprints, but this list is a regex over rendered HTML.
    Owning the content does not make a regex over it an enumeration -- see the
    `none_listed` case below for where bioRxiv's authority does count."""
    http = _biorxiv_http()
    result = BiorxivSource(http).fetch(_preprint_ids(), need_pdf=False, need_supplements=True)

    assert result.suppl_status == "fetched_unverified"
    assert len(result.files) == 2
    assert all(f.label == "bioRxiv supplementary material" for f in result.files)
    assert _statuses(result, "supplements") == ["fetched_unverified"]


def test_a_page_that_loads_and_lists_nothing_is_none_listed():
    """Verified on 10.1101/2022.01.02.474723, whose supplement page carries no
    links. Reporting `page_not_parsed` here would be a false alarm on every
    preprint that genuinely has no supplements -- and the index flag cannot break
    the tie: Europe PMC says hasSuppl=N for 10.1101/2025.07.21.666016, which has
    media-1.pdf and media-2.zip."""
    http = _biorxiv_http({SUPPL_PAGE: (200, b"<html><body>Nothing here</body></html>",
                                       "text/html")})
    result = BiorxivSource(http).fetch(_preprint_ids(), need_pdf=False, need_supplements=True)

    assert result.suppl_status == "none_listed"
    assert result.problems == []


def test_a_missing_supplement_page_leaves_the_status_to_the_fetcher():
    """404 means this preprint has no supplement page at all. Neither claim is
    ours to make from that, so `suppl_status` stays None and the fetcher decides
    with the publisher's flag in hand."""
    http = _biorxiv_http({SUPPL_PAGE: (404, b"", "")})
    result = BiorxivSource(http).fetch(_preprint_ids(), need_pdf=False, need_supplements=True)

    assert result.suppl_status is None
    assert _statuses(result, "supplements") == ["no_page"]


def test_a_broken_supplement_page_is_page_not_parsed():
    http = _biorxiv_http({SUPPL_PAGE: (503, b"", "")})
    result = BiorxivSource(http).fetch(_preprint_ids(), need_pdf=False, need_supplements=True)
    assert result.suppl_status == "page_not_parsed"


def test_supplement_page_transport_failure_says_so_in_the_problems():
    class Exploding(FakeHttp):
        def get(self, url, params=None, accept=None, allow_redirects=True):
            if SUPPL_PAGE in url:
                raise HttpError("connection reset")
            return super().get(url, params, accept, allow_redirects)

    result = BiorxivSource(Exploding(_biorxiv_http().routes)).fetch(
        _preprint_ids(), need_pdf=False, need_supplements=True)
    assert result.suppl_status == "page_not_parsed"
    assert any("supplementary-material page failed" in p for p in result.problems)


def test_some_supplements_arriving_is_partial_failure():
    """Two links found, one 404s. Reporting `fetched_unverified` would hide a real
    loss behind a status that already means "we cannot verify the count"."""
    class OneDead(FakeHttp):
        def get(self, url, params=None, accept=None, allow_redirects=True):
            if url.endswith("media-2.zip"):
                self.calls.append(url)
                return Response(url=url, status=404, content=b"", content_type="")
            return super().get(url, params, accept, allow_redirects)

    http = OneDead(_biorxiv_http().routes)
    result = BiorxivSource(http).fetch(_preprint_ids(), need_pdf=False, need_supplements=True)

    assert result.suppl_status == "partial_failure"
    assert len(result.files) == 1


def test_links_found_but_none_retrievable_is_page_not_parsed_not_none_listed():
    """The page named files and we got none: the one case where an empty
    `supplementary/` directory must never read as "there are none"."""
    http = _biorxiv_http({MEDIA: (403, b"", "")})
    result = BiorxivSource(http).fetch(_preprint_ids(), need_pdf=False, need_supplements=True)

    assert result.suppl_status == "page_not_parsed"
    assert result.files == []


def test_a_supplement_transport_failure_is_a_problem_not_an_abort():
    class OneExplodes(FakeHttp):
        def get(self, url, params=None, accept=None, allow_redirects=True):
            if url.endswith("media-1.pdf"):
                raise HttpError("connection reset")
            return super().get(url, params, accept, allow_redirects)

    http = OneExplodes(_biorxiv_http().routes)
    result = BiorxivSource(http).fetch(_preprint_ids(), need_pdf=False, need_supplements=True)

    assert result.suppl_status == "partial_failure"
    assert [f.name.rsplit("/", 1)[-1] for f in result.files] == ["media-2.zip"]
    assert any("media-1.pdf failed" in p for p in result.problems)


def test_the_max_files_cap_is_a_recorded_problem_not_a_silent_trim():
    http = _biorxiv_http({
        SUPPL_PAGE: (200, _supplement_page(*[f"media-{n}.pdf" for n in range(1, 6)]),
                     "text/html"),
    })
    result = BiorxivSource(http, {"max_files": 2}).fetch(
        _preprint_ids(), need_pdf=False, need_supplements=True)

    assert len(result.files) == 2
    assert any("3 supplementary link(s) not fetched" in p for p in result.problems)
    assert any(a["action"] == "cap" and a["dropped"] == 3 for a in result.attempts)
    # Still `fetched_unverified`: every link we attempted arrived. The cap is
    # reported separately so the count is not mistaken for the whole set.
    assert result.suppl_status == "fetched_unverified"


# -- bioRxiv: link extraction ------------------------------------------------

def test_media_links_are_absolute_and_deduped():
    """Each anchor matches twice -- once by the bare pattern and once via its href
    -- so without the `seen` set every supplement would be fetched and stored
    twice."""
    html = _supplement_page("media-1.pdf", "media-2.zip").decode()
    links = _media_links(html, "https://www.biorxiv.org")

    assert links == [
        "https://www.biorxiv.org/content/biorxiv/early/2024/01/25/2024.01.23.576878"
        "/DC1/embed/media-1.pdf",
        "https://www.biorxiv.org/content/biorxiv/early/2024/01/25/2024.01.23.576878"
        "/DC2/embed/media-2.zip",
    ]


def test_media_links_ignores_everything_that_is_not_a_media_path():
    """A supplement page is a full site shell: nav, citation exports and figure
    links all end in a file extension and none of them are supplements."""
    html = """<html><body>
    <a href="/content/10.1101/2024.01.23.576878v2.full.pdf">Full Text PDF</a>
    <a href="/highwire/filestream/12345/field_highwire_adjunct/0/media-1.pdf">Odd shape</a>
    <a href="/content/early/2024/DC1/embed/media-1.pdf">Real one</a>
    <a href="/about/policies">Policies</a>
    </body></html>"""
    assert _media_links(html, "https://www.biorxiv.org") == [
        "https://www.biorxiv.org/content/early/2024/DC1/embed/media-1.pdf"]


def test_media_links_finds_a_path_that_is_not_inside_an_href():
    """The bare-pattern arm of the search: bioRxiv renders some supplement lists
    from JSON embedded in the page rather than as anchors."""
    html = ('<script>{"files": ["/content/early/2024/DC1/embed/media-1.pdf"]}</script>')
    assert _media_links(html, "https://www.biorxiv.org") == [
        "https://www.biorxiv.org/content/early/2024/DC1/embed/media-1.pdf"]


# -- PMC supplements: which articles the tier claims -------------------------

def test_pmc_supplements_needs_a_pmcid():
    source = PmcSupplementsSource(FakeHttp())
    assert source.applies(Identifiers(doi=DOI, doi_raw=DOI, pmcid=PMCID)) is True
    assert source.applies(Identifiers(doi=DOI, doi_raw=DOI)) is False


def test_pmc_supplements_is_a_supplement_only_tier():
    """It has no PDF path at all: the article page's own PDF link is behind the
    same proof-of-work gate, so asking here would only add a misleading status."""
    http = FakeHttp()
    result = PmcSupplementsSource(http).fetch(
        _pmc_ids(), need_pdf=True, need_supplements=False)

    assert result.files == [] and result.pdf_status is None and result.suppl_status is None
    assert http.calls == [], "must not touch the network when supplements are not wanted"


def _pmc_ids(doi: str = DOI) -> Identifiers:
    return Identifiers(doi=doi, doi_raw=doi, pmcid=PMCID, has_suppl=True, in_pmc=True)


# -- PMC supplements: the listing -------------------------------------------

MOESM1 = "41586_2021_3852_MOESM1_ESM.xlsx"
MOESM2 = "41586_2021_3852_MOESM2_ESM.pdf"
ARTICLE_PAGE = f"/articles/{PMCID}/"
SPRINGER = "static-content.springer.com"
PMC_BIN = "/bin/"


def _pmc_article_page(*filenames: str) -> bytes:
    """PMC's article page, reduced to the `/bin/` hrefs the listing reads.

    Each file appears twice on the real page -- once in the supplementary-material
    section and once in the floating "Data availability" list -- which is why the
    listing dedupes on filename.
    """
    links = "".join(
        f'<a href="/articles/instance/8426186/bin/{name}">{name}</a>' * 2
        for name in filenames
    )
    return f"<html><body><section>{links}</section></body></html>".encode()


def _pmc_http(routes=None, *filenames) -> FakeHttp:
    base = {
        ARTICLE_PAGE: (200, _pmc_article_page(*(filenames or (MOESM1, MOESM2))), "text/html"),
        SPRINGER: (200, b"real supplement bytes", "application/vnd.ms-excel"),
    }
    base.update(routes or {})
    return FakeHttp(base)


def test_the_list_comes_from_pmc_and_the_bytes_from_the_publisher():
    """The split this tier exists for: PMC's page names the files, but its `/bin/`
    URLs are behind a proof-of-work gate, while Springer's static host serves the
    same bytes with no challenge and no credentials."""
    http = _pmc_http()
    result = PmcSupplementsSource(http).fetch(_pmc_ids(), need_pdf=False, need_supplements=True)

    assert result.suppl_status == "fetched_unverified"
    assert [f.name for f in result.files] == [MOESM1, MOESM2]
    assert all(f.label == "listed by PMC" for f in result.files)
    assert http.called_matching(SPRINGER) == 2
    assert http.called_matching(PMC_BIN) == 0, "the /bin/ URL is a fallback, not the first try"


def test_the_pmc_cap_counts_files_where_a_scrape_counts_links():
    """The `max_files` cap is written once in `Source.apply_files_cap`, and the noun
    is deliberately a parameter: PMC *listed* these, so a dropped one is a known
    file, while bioRxiv and the browser tier count anchors matched on a rendered page
    and cannot claim that. Only the browser tier's wording was pinned, so nothing
    stopped a merge from settling on one word and overclaiming here."""
    http = _pmc_http()
    result = PmcSupplementsSource(http, config={"max_files": 1}).fetch(
        _pmc_ids(), need_pdf=False, need_supplements=True)

    assert [f.name for f in result.files] == [MOESM1]
    assert any("1 supplementary file(s) not fetched" in p for p in result.problems), \
        result.problems
    assert any(a["action"] == "cap" and a["dropped"] == 1 for a in result.attempts)


def test_the_listing_dedupes_on_filename():
    """Every file is linked twice on the real page; the same bytes fetched twice
    would be stored as two supplements."""
    http = _pmc_http()
    result = PmcSupplementsSource(http).fetch(_pmc_ids(), need_pdf=False, need_supplements=True)
    assert [f.name for f in result.files] == [MOESM1, MOESM2]
    assert any(a["action"] == "listing" and a["count"] == 2 for a in result.attempts)


def test_a_page_naming_no_files_is_recorded_without_a_status():
    """`no_files_listed` and no `suppl_status`: combined with hasSuppl the fetcher
    can tell whether an empty list is expected, and this tier cannot."""
    http = _pmc_http({ARTICLE_PAGE: (200, b"<html><body>no bin links</body></html>",
                                     "text/html")})
    result = PmcSupplementsSource(http).fetch(_pmc_ids(), need_pdf=False, need_supplements=True)

    assert result.suppl_status is None
    # Both notes are kept: the page was read (`ok`, count 0) *and* the conclusion
    # drawn from it. A single `no_files_listed` would not say the read succeeded.
    assert _statuses(result, "listing") == ["ok", "no_files_listed"]
    assert result.files == []


def test_a_proof_of_work_page_is_page_not_parsed_and_names_the_gate():
    """Measured live at 1817 bytes. The listing itself is normally plain HTML, so
    a challenge here means the tier learned nothing -- not that there is nothing."""
    http = _pmc_http({ARTICLE_PAGE: (200, POW_HTML, "text/html")})
    result = PmcSupplementsSource(http).fetch(_pmc_ids(), need_pdf=False, need_supplements=True)

    assert result.suppl_status == "page_not_parsed"
    assert result.problems == ["pmc article page returned a proof-of-work challenge"]
    assert _statuses(result, "listing") == ["javascript_challenge"]


@pytest.mark.parametrize("response,expected", [
    ((404, b"", ""), "http_error"),
    ((500, b"", ""), "http_error"),
])
def test_an_unreachable_article_page_leaves_the_status_to_the_fetcher(response, expected):
    http = _pmc_http({ARTICLE_PAGE: response})
    result = PmcSupplementsSource(http).fetch(_pmc_ids(), need_pdf=False, need_supplements=True)

    assert result.suppl_status is None
    assert _statuses(result, "listing") == [expected]


def test_a_transport_failure_on_the_article_page_is_a_problem():
    class Exploding(FakeHttp):
        def get(self, url, params=None, accept=None, allow_redirects=True):
            raise HttpError("connection reset")

    result = PmcSupplementsSource(Exploding()).fetch(
        _pmc_ids(), need_pdf=False, need_supplements=True)
    assert any("pmc article page failed" in p for p in result.problems)
    assert _statuses(result, "listing") == ["request_failed"]


# -- PMC supplements: the download fallback chain ---------------------------

_ESM_BASE = ("https://static-content.springer.com/esm/"
             "art%3A10.1038%2Fs41586-021-03852-1/MediaObjects/")


@pytest.mark.parametrize("filename,expected", [
    ("41586_2021_3852_MOESM1_ESM.xlsx", _ESM_BASE + "41586_2021_3852_MOESM1_ESM.xlsx"),
    ("moesm1_esm.xlsx", _ESM_BASE + "moesm1_esm.xlsx"),   # matched case-insensitively
    ("media-1.pdf", None),                                # bioRxiv's shape, not Springer's
    ("table_s1.docx", None),
    ("", None),
])
def test_springer_urls_are_built_only_for_moesm_objects(filename, expected):
    """The DOI is percent-encoded whole -- `safe=""` -- because the path segment is
    `art%3A10.1038%2F...`, so an unescaped slash would split it into two segments
    and the static host would 404. The filename is *not* re-encoded, because it is
    already the literal name Springer serves."""
    assert _springer_url(DOI, filename) == expected


def test_the_bin_url_is_tried_when_the_publisher_pattern_misses():
    """A non-Springer article has no static host to try, so the `/bin/` URL is all
    there is -- and for some publishers it works."""
    http = _pmc_http({PMC_BIN: (200, b"bytes from pmc", "application/pdf")},
                     "table_s1.docx")
    result = PmcSupplementsSource(http).fetch(_pmc_ids(), need_pdf=False, need_supplements=True)

    assert result.suppl_status == "fetched_unverified"
    assert http.called_matching(SPRINGER) == 0
    assert [a["via"] for a in result.attempts if a["action"] == "supplement_file"] == ["pmc_bin"]


def test_a_dead_publisher_host_falls_through_to_the_bin_url():
    http = _pmc_http({SPRINGER: (404, b"", ""),
                      PMC_BIN: (200, b"bytes from pmc", "application/pdf")})
    result = PmcSupplementsSource(http).fetch(_pmc_ids(), need_pdf=False, need_supplements=True)

    assert result.suppl_status == "fetched_unverified"
    assert len(result.files) == 2
    vias = [a["via"] for a in result.attempts if a["action"] == "supplement_file"]
    assert vias == ["springer", "pmc_bin", "springer", "pmc_bin"]


def test_a_transport_failure_on_one_candidate_still_tries_the_next():
    class SpringerDown(FakeHttp):
        def get(self, url, params=None, accept=None, allow_redirects=True):
            if SPRINGER in url:
                raise HttpError("connection reset")
            return super().get(url, params, accept, allow_redirects)

    http = SpringerDown(_pmc_http({PMC_BIN: (200, b"bytes", "application/pdf")}).routes)
    result = PmcSupplementsSource(http).fetch(_pmc_ids(), need_pdf=False, need_supplements=True)

    assert len(result.files) == 2
    assert "request_failed" in _statuses(result, "supplement_file")


def test_everything_behind_the_gate_says_the_browser_tier_is_required():
    """The whole point of this tier's reporting: `partial_failure` plus a problem
    naming the proof-of-work page, rather than an empty directory and no status."""
    http = _pmc_http({SPRINGER: (404, b"", ""), PMC_BIN: (200, POW_HTML, "text/html")})
    result = PmcSupplementsSource(http).fetch(_pmc_ids(), need_pdf=False, need_supplements=True)

    assert result.suppl_status == "partial_failure"
    assert result.files == []
    assert any("proof-of-work page; the browser tier is required" in p
               for p in result.problems)
    assert any(a["action"] == "supplements" and a["javascript_challenge"] == 2
               for a in result.attempts)


def test_a_plain_failure_is_partial_failure_without_blaming_the_gate():
    """Nothing arrived and nothing was challenged. Suggesting the browser tier here
    would send a re-run down a path that cannot help."""
    http = _pmc_http({SPRINGER: (404, b"", ""), PMC_BIN: (404, b"", "")})
    result = PmcSupplementsSource(http).fetch(_pmc_ids(), need_pdf=False, need_supplements=True)

    assert result.suppl_status == "partial_failure"
    assert not any("browser tier" in p for p in result.problems)
    assert any(a["action"] == "supplements" and a["failed"] == 2 for a in result.attempts)


def test_one_gated_file_among_several_is_still_partial_failure():
    class OneGated(FakeHttp):
        def get(self, url, params=None, accept=None, allow_redirects=True):
            if MOESM2 in url:
                self.calls.append(url)
                return Response(url=url, status=200, content=POW_HTML,
                                content_type="text/html")
            return super().get(url, params, accept, allow_redirects)

    http = OneGated(_pmc_http({PMC_BIN: (200, POW_HTML, "text/html")}).routes)
    result = PmcSupplementsSource(http).fetch(_pmc_ids(), need_pdf=False, need_supplements=True)

    assert result.suppl_status == "partial_failure"
    assert [f.name for f in result.files] == [MOESM1]


def test_an_empty_body_is_not_a_file():
    """200 with zero bytes is how a CDN answers for an object it has purged; a
    zero-byte supplement in the corpus is indistinguishable from a real one."""
    http = _pmc_http({SPRINGER: (200, b"", "application/pdf"),
                      PMC_BIN: (200, b"", "application/pdf")})
    result = PmcSupplementsSource(http).fetch(_pmc_ids(), need_pdf=False, need_supplements=True)

    assert result.files == []
    assert result.suppl_status == "partial_failure"


def test_the_pmc_max_files_cap_is_a_recorded_problem():
    names = [f"41586_2021_3852_MOESM{n}_ESM.xlsx" for n in range(1, 7)]
    http = _pmc_http(None, *names)
    result = PmcSupplementsSource(http, {"max_files": 3}).fetch(
        _pmc_ids(), need_pdf=False, need_supplements=True)

    assert len(result.files) == 3
    assert any("3 supplementary file(s) not fetched" in p for p in result.problems)
    assert any(a["action"] == "cap" and a["dropped"] == 3 for a in result.attempts)
    assert result.suppl_status == "fetched_unverified"


# -- PMC OA package: what oa.fcgi answers ------------------------------------

OA_FCGI = "oa.fcgi"
OA_TGZ = "oa_package"
OA_PDF = "oa_pdf"


def _oa_xml(*links: str) -> bytes:
    """An `oa.fcgi` response. The hrefs are `ftp://`, exactly as the service sends
    them -- rewriting those to HTTPS is this tier's job."""
    body = "".join(
        f'<link format="{fmt}" href="ftp://ftp.ncbi.nlm.nih.gov/pub/pmc/{path}"/>'
        for fmt, path in (link.split(" ", 1) for link in links)
    )
    return (f'<OA><records returned-count="1"><record id="{PMCID}" license="CC BY">'
            f"{body}</record></records></OA>").encode()


OA_BOTH_LINKS = _oa_xml(f"tgz oa_package/34/e8/{PMCID}.tar.gz",
                        f"pdf oa_pdf/34/e8/gkr715.{PMCID}.pdf")


def _oa_http(routes=None) -> FakeHttp:
    base = {
        OA_FCGI: (200, OA_BOTH_LINKS, "application/xml"),
        OA_PDF: (200, make_pdf(), "application/pdf"),
    }
    base.update(routes or {})
    return FakeHttp(base)


def test_ftp_hrefs_are_rewritten_to_the_https_mirror():
    """NCBI serves the identical paths over HTTPS. Fetching the `ftp://` href as
    given would need an FTP client the pipeline does not have."""
    http = _oa_http()
    result = PmcOaSource(http).fetch(_pmc_ids(), need_pdf=True, need_supplements=False)

    assert http.calls[1].startswith("https://ftp.ncbi.nlm.nih.gov/pub/pmc/oa_pdf/")
    assert result.pdf_status == "ok"
    assert [f.name for f in result.files] == ["fulltext.pdf"]


def test_the_pmcid_is_the_only_query_parameter_we_add():
    http = _oa_http()
    PmcOaSource(http).fetch(_pmc_ids(), need_pdf=True, need_supplements=False)
    assert http.params[0] == {"id": PMCID}


def test_being_in_pmc_is_not_being_in_the_oa_subset():
    """Author-manuscript deposits live outside the OA subset and `oa.fcgi` answers
    with an `<error>`. That is a routing fact, so the fetcher must be told to move
    on -- not handed a failure that reads like a broken download."""
    error = (b'<OA><error code="idIsNotOpenAccess">identifier is not Open Access'
             b"</error></OA>")
    http = _oa_http({OA_FCGI: (200, error, "application/xml")})
    result = PmcOaSource(http).fetch(_pmc_ids(), need_pdf=True, need_supplements=True)

    assert result.pdf_status == "not_in_oa_subset"
    assert result.suppl_status is None, "this tier learned nothing about supplements"
    assert any("not in the PMC Open Access subset (oa.fcgi: idIsNotOpenAccess)" in p
               for p in result.problems)
    assert len(http.calls) == 1, "an error answer must not trigger a download"


def test_an_error_element_without_a_code_still_names_the_outcome():
    http = _oa_http({OA_FCGI: (200, b"<OA><error>no code attribute</error></OA>",
                               "application/xml")})
    result = PmcOaSource(http).fetch(_pmc_ids(), need_pdf=True, need_supplements=False)
    assert result.pdf_status == "not_in_oa_subset"
    assert any("oa.fcgi: unknown" in p for p in result.problems)


@pytest.mark.parametrize("response,status", [
    ((503, b"", ""), "http_error"),
    ((200, b"<OA><records", "application/xml"), "unparseable_xml"),
])
def test_a_broken_oa_service_is_not_read_as_absence(response, status):
    """`not_in_oa_subset` is the honest answer for any lookup that did not succeed:
    it says "this tier cannot route you", which is true, and lets a later tier and
    the manifest carry the real reason."""
    http = _oa_http({OA_FCGI: response})
    result = PmcOaSource(http).fetch(_pmc_ids(), need_pdf=True, need_supplements=False)

    assert result.pdf_status == "not_in_oa_subset"
    assert _statuses(result, "oa_lookup") == [status]


def test_a_transport_failure_on_oa_fcgi_is_a_recorded_problem():
    class Exploding(FakeHttp):
        def get(self, url, params=None, accept=None, allow_redirects=True):
            raise HttpError("connection reset")

    result = PmcOaSource(Exploding()).fetch(_pmc_ids(), need_pdf=True, need_supplements=False)
    assert result.pdf_status == "not_in_oa_subset"
    assert any("pmc oa service failed" in p for p in result.problems)


def test_a_record_with_no_links_leaves_every_status_alone():
    """A successful lookup that advertises nothing is not a failure and not an
    absence of supplements -- both statuses stay None for the fetcher to resolve."""
    http = _oa_http({OA_FCGI: (200, b'<OA><records><record id="x"/></records></OA>',
                               "application/xml")})
    result = PmcOaSource(http).fetch(_pmc_ids(), need_pdf=True, need_supplements=True)

    assert result.pdf_status is None and result.suppl_status is None
    assert result.files == []
    assert _statuses(result, "oa_lookup") == ["ok"]


def test_a_tgz_only_record_reports_the_pdf_as_not_found():
    """`try_oa_package` is off by default, so a record advertising only the tarball
    yields nothing -- and `not_found` is what says we looked."""
    http = _oa_http({OA_FCGI: (200, _oa_xml(f"tgz oa_package/34/e8/{PMCID}.tar.gz"),
                               "application/xml")})
    result = PmcOaSource(http).fetch(_pmc_ids(), need_pdf=True, need_supplements=True)

    assert result.pdf_status == "not_found"
    assert _statuses(result, "package") == ["skipped"]


@pytest.mark.parametrize("response,expected", [
    ((404, b"", ""), "download_failed"),
    ((200, b"<html>not a pdf</html>" * 40, "text/html"), "not_a_pdf"),
])
def test_the_advertised_pdf_is_still_validated(response, expected):
    """The `oa_package` tree is being retired and its advertised paths already 404,
    so the pdf link is the one artifact this tier usually delivers. Storing whatever
    that URL returns is how an error page becomes `fulltext.pdf`."""
    http = _oa_http({OA_PDF: response})
    result = PmcOaSource(http).fetch(_pmc_ids(), need_pdf=True, need_supplements=False)

    assert result.pdf_status == expected
    assert result.pdf is None


def test_a_transport_failure_on_the_pdf_is_download_failed():
    class Exploding(FakeHttp):
        def get(self, url, params=None, accept=None, allow_redirects=True):
            if OA_PDF in url:
                raise HttpError("read timeout")
            return super().get(url, params, accept, allow_redirects)

    result = PmcOaSource(Exploding(_oa_http().routes)).fetch(
        _pmc_ids(), need_pdf=True, need_supplements=False)
    assert result.pdf_status == "download_failed"


# -- PMC OA package: the tarball --------------------------------------------

def _package(*members) -> bytes:
    return make_tgz(list(members))


PACKAGE_MEMBERS = [
    (f"{PMCID}/main.nxml", b"<article><body/></article>"),
    (f"{PMCID}/gkr715.pdf", make_pdf()),
    (f"{PMCID}/gkr715f1.jpg", b"\xff\xd8figure one"),
    (f"{PMCID}/gkr715_supp_table_s1.xlsx", b"real supplement"),
]


def test_the_package_is_off_by_default_and_says_why():
    """Not silently skipped: the manifest has to record that the tarball was
    deliberately not tried, or a curator reading `supplementary: []` cannot tell
    that from an article with none."""
    http = _oa_http({OA_TGZ: (200, _package(*PACKAGE_MEMBERS), "application/gzip")})
    result = PmcOaSource(http).fetch(_pmc_ids(), need_pdf=False, need_supplements=True)

    assert http.called_matching(OA_TGZ) == 0
    note = next(a for a in result.attempts if a["action"] == "package")
    assert note["status"] == "skipped"
    assert "try_oa_package is off" in note["detail"]
    assert result.suppl_status is None


def test_unpacking_the_deposit_earns_plain_fetched():
    """The strongest supplement evidence the pipeline can have: the OA package *is*
    the deposit, so unpacking it bounds the set. Every scraping tier has to settle
    for `fetched_unverified`."""
    http = _oa_http({OA_TGZ: (200, _package(*PACKAGE_MEMBERS), "application/gzip")})
    result = PmcOaSource(http, {"try_oa_package": True}).fetch(
        _pmc_ids(), need_pdf=False, need_supplements=True)

    assert result.suppl_status == "fetched"
    roles = {f.role: f.name for f in result.files}
    assert roles["fulltext_xml"] == "fulltext.nxml"
    assert roles["supplement"] == "gkr715_supp_table_s1.xlsx"
    assert roles["media"] == "gkr715f1.jpg", "figure images must not land in supplementary/"
    note = next(a for a in result.attempts if a["status"] == "unpacked")
    assert (note["supplements"], note["media"], note["has_xml"]) == (1, 1, True)


def test_the_package_pdf_is_only_taken_when_the_link_did_not_deliver():
    """`need_pdf=need_pdf and result.pdf is None` -- the explicit pdf link is
    authoritative, so re-reading the article out of the tarball would overwrite a
    validated file with an unvalidated one."""
    http = _oa_http({OA_TGZ: (200, _package(*PACKAGE_MEMBERS), "application/gzip")})
    result = PmcOaSource(http, {"try_oa_package": True}).fetch(
        _pmc_ids(), need_pdf=True, need_supplements=True)

    assert result.pdf_status == "ok"
    assert result.pdf.url.endswith(".pdf") and OA_PDF in result.pdf.url
    assert _statuses(result, "pdf_from_package") == []
    assert len(result.by_role("fulltext_pdf")) == 1


def test_the_package_pdf_rescues_a_dead_pdf_link():
    http = _oa_http({OA_PDF: (404, b"", ""),
                     OA_TGZ: (200, _package(*PACKAGE_MEMBERS), "application/gzip")})
    result = PmcOaSource(http, {"try_oa_package": True}).fetch(
        _pmc_ids(), need_pdf=True, need_supplements=False)

    assert result.pdf_status == "ok"
    assert _statuses(result, "pdf_from_package") == ["ok"]
    assert result.pdf.name == "fulltext.pdf"


def test_a_package_pdf_that_fails_validation_does_not_become_the_article():
    http = _oa_http({
        OA_PDF: (404, b"", ""),
        OA_TGZ: (200, _package((f"{PMCID}/main.nxml", b"<article/>"),
                               (f"{PMCID}/gkr715.pdf", b"not a pdf at all" * 30)),
                 "application/gzip"),
    })
    result = PmcOaSource(http, {"try_oa_package": True}).fetch(
        _pmc_ids(), need_pdf=True, need_supplements=False)

    assert result.pdf_status == "not_a_pdf"
    assert result.pdf is None
    assert [f.role for f in result.files] == ["fulltext_xml"]


@pytest.mark.parametrize("response,expected", [
    ((404, b"", ""), "download_failed"),
    ((200, b"this is not gzip", "application/gzip"), "unreadable_archive"),
])
def test_a_package_that_cannot_be_read_is_named_not_swallowed(response, expected):
    """The advertised paths already 404 for every article tested (PMC8426186 and
    PMC3258128), so this is the common case rather than the odd one."""
    http = _oa_http({OA_TGZ: response})
    result = PmcOaSource(http, {"try_oa_package": True}).fetch(
        _pmc_ids(), need_pdf=False, need_supplements=True)

    assert _statuses(result, "package") == [expected]
    assert result.suppl_status is None, "a failed unpack claims nothing about supplements"
    assert result.files == []


def test_a_transport_failure_on_the_package_is_a_recorded_problem():
    class Exploding(FakeHttp):
        def get(self, url, params=None, accept=None, allow_redirects=True):
            if OA_TGZ in url:
                raise HttpError("connection reset")
            return super().get(url, params, accept, allow_redirects)

    result = PmcOaSource(Exploding(_oa_http().routes), {"try_oa_package": True}).fetch(
        _pmc_ids(), need_pdf=False, need_supplements=True)
    assert any("pmc oa package download failed" in p for p in result.problems)


def test_a_package_holding_only_figures_claims_no_supplements():
    """`if supplements:` -- media alone must not set `fetched`, or an article whose
    package holds nothing but figure JPEGs would report its supplements complete."""
    http = _oa_http({OA_TGZ: (200, _package((f"{PMCID}/f1.jpg", b"\xff\xd8one"),
                                            (f"{PMCID}/f2.png", b"\x89PNGtwo")),
                              "application/gzip")})
    result = PmcOaSource(http, {"try_oa_package": True}).fetch(
        _pmc_ids(), need_pdf=False, need_supplements=True)

    assert result.suppl_status is None
    assert [f.role for f in result.files] == ["media", "media"]


def test_the_package_is_skipped_entirely_when_the_pdf_link_answered():
    """`wants_package` is False once the PDF is in hand and supplements are not
    wanted, so a needless multi-hundred-megabyte download never starts."""
    http = _oa_http({OA_TGZ: (200, _package(*PACKAGE_MEMBERS), "application/gzip")})
    result = PmcOaSource(http, {"try_oa_package": True}).fetch(
        _pmc_ids(), need_pdf=True, need_supplements=False)

    assert result.pdf_status == "ok"
    assert http.called_matching(OA_TGZ) == 0
    assert _statuses(result, "package") == []


# -- Europe PMC: the cheapest complete answer -------------------------------

EPMC_XML = "/fullTextXML"
EPMC_SUPPL = "/supplementaryFiles"
EPMC_RENDER = "europepmc.org/articles"
OA_PDF_URL = "https://example.org/oa-article.pdf"


def _epmc_ids(*, pmcid=PMCID, pdf_urls=(OA_PDF_URL,)) -> Identifiers:
    return Identifiers(
        doi=DOI, doi_raw=DOI, pmcid=pmcid,
        full_text_urls=[{"documentStyle": "pdf", "availabilityCode": "OA", "url": url}
                        for url in pdf_urls],
    )


def _epmc_http(routes=None) -> FakeHttp:
    base = {
        OA_PDF_URL: (200, make_pdf(), "application/pdf"),
        EPMC_XML: (200, b"<article><body/></article>", "application/xml"),
        EPMC_SUPPL: (200, make_zip([("a_MOESM1_ESM.pdf", b"%PDF one"),
                                    ("a_MOESM2_ESM.xlsx", b"xlsx two")]), "application/zip"),
    }
    base.update(routes or {})
    return FakeHttp(base)


def test_europepmc_applies_without_a_pmcid_when_a_pdf_url_is_known():
    """The PDF URLs come from the Tier 0 lookup and can exist for an article that
    is not in PMC at all, so requiring a PMCID would skip the tier that can serve
    it."""
    source = EuropePmcSource(FakeHttp())
    assert source.applies(_epmc_ids(pmcid=None)) is True
    assert source.applies(_epmc_ids(pmcid=None, pdf_urls=())) is False
    assert source.applies(_epmc_ids(pdf_urls=())) is True


def test_the_supplement_archive_earns_plain_fetched():
    """One request, one ZIP, no scraping: the archive *is* the deposit, so its
    member list bounds the set. Only this and the OA package earn `fetched`."""
    http = _epmc_http()
    result = EuropePmcSource(http).fetch(_epmc_ids(), need_pdf=False, need_supplements=True)

    assert result.suppl_status == "fetched"
    assert [f.name for f in result.files] == ["a_MOESM1_ESM.pdf", "a_MOESM2_ESM.xlsx"]
    assert all(f.label == "Europe PMC supplementary archive" for f in result.files)


def test_a_404_on_the_archive_is_not_an_absence_of_supplements():
    """Verified live: PMC3258128 returns a 3 MB ZIP while PMC8426186 returns 404
    despite the search API reporting hasSuppl=Y. So 404 means "Europe PMC holds no
    archive", and the status is left for the fetcher to reconcile."""
    http = _epmc_http({EPMC_SUPPL: (404, b"", "")})
    result = EuropePmcSource(http).fetch(_epmc_ids(), need_pdf=False, need_supplements=True)

    assert result.suppl_status is None
    assert _statuses(result, "supplements") == ["not_available"]


def test_a_broken_archive_endpoint_is_partial_failure_not_silence():
    http = _epmc_http({EPMC_SUPPL: (503, b"", "")})
    result = EuropePmcSource(http).fetch(_epmc_ids(), need_pdf=False, need_supplements=True)

    assert result.suppl_status == "partial_failure"
    assert _statuses(result, "supplements") == ["download_failed"]


def test_a_transport_failure_on_the_archive_is_partial_failure():
    class Exploding(FakeHttp):
        def get(self, url, params=None, accept=None, allow_redirects=True):
            raise HttpError("connection reset")

    result = EuropePmcSource(Exploding()).fetch(
        _epmc_ids(), need_pdf=False, need_supplements=True)
    assert result.suppl_status == "partial_failure"
    assert any("europepmc supplementaryFiles failed" in p for p in result.problems)


@pytest.mark.parametrize("body,content_type", [
    (b"<html><body>Service unavailable</body></html>", "text/html"),
    (b"", "application/zip"),
    (b"{}", "application/json"),
])
def test_a_200_that_is_not_an_archive_is_named_not_leaked(body, content_type):
    """The magic bytes are checked before unpacking so the manifest says `not_a_zip`
    rather than surfacing a BadZipFile traceback from inside the tier."""
    http = _epmc_http({EPMC_SUPPL: (200, body, content_type)})
    result = EuropePmcSource(http).fetch(_epmc_ids(), need_pdf=False, need_supplements=True)

    assert result.suppl_status == "partial_failure"
    assert _statuses(result, "supplements") == ["not_a_zip"]
    assert any("returned a non-ZIP body" in p for p in result.problems)


def test_a_corrupt_zip_is_partial_failure_with_the_reason():
    """Passes the PK check and still fails to open -- a truncated download."""
    http = _epmc_http({EPMC_SUPPL: (200, b"PK\x03\x04 truncated here", "application/zip")})
    result = EuropePmcSource(http).fetch(_epmc_ids(), need_pdf=False, need_supplements=True)

    assert result.suppl_status == "partial_failure"
    assert _statuses(result, "supplements") == ["unreadable_zip"]
    assert any("supplement ZIP unreadable" in p for p in result.problems)


def test_an_oversized_member_stops_the_unpack_rather_than_filling_the_disk():
    http = _epmc_http({EPMC_SUPPL: (200, make_zip([("huge.bin", b"x" * 5000)]),
                                    "application/zip")})
    result = EuropePmcSource(http, {"max_file_mb": 0.000001}).fetch(
        _epmc_ids(), need_pdf=False, need_supplements=True)

    assert result.suppl_status == "partial_failure"
    assert _statuses(result, "supplements") == ["unreadable_zip"]
    assert any("over the" in p for p in result.problems)


def test_an_empty_archive_claims_nothing():
    """A ZIP holding only directory entries unpacks to nothing. That is not a
    failure and not a verified absence, so no status is set."""
    http = _epmc_http({EPMC_SUPPL: (200, make_zip([("subdir/", b"")]), "application/zip")})
    result = EuropePmcSource(http).fetch(_epmc_ids(), need_pdf=False, need_supplements=True)

    assert result.suppl_status is None
    assert _statuses(result, "supplements") == ["empty_zip"]
    assert result.files == []


def test_the_archive_member_cap_stops_at_max_files():
    members = [(f"supp_{n:02d}.xlsx", f"file {n}".encode()) for n in range(10)]
    http = _epmc_http({EPMC_SUPPL: (200, make_zip(members), "application/zip")})
    result = EuropePmcSource(http, {"max_files": 4}).fetch(
        _epmc_ids(), need_pdf=False, need_supplements=True)

    assert len(result.files) == 4
    assert result.suppl_status == "fetched"


def test_the_archive_is_not_requested_without_a_pmcid():
    """The endpoint is keyed on the PMCID; there is nothing to ask for without one."""
    http = _epmc_http()
    result = EuropePmcSource(http).fetch(
        _epmc_ids(pmcid=None), need_pdf=False, need_supplements=True)

    assert http.called_matching(EPMC_SUPPL) == 0
    assert result.suppl_status is None


# -- Europe PMC: the PDF candidate chain -------------------------------------

def test_the_render_endpoint_is_the_fallback_after_the_listed_urls():
    """`?pdf=render` is Europe PMC's own renderer, used when fullTextUrlList has no
    PDF entry but the article is in EPMC."""
    http = _epmc_http({OA_PDF_URL: (404, b"", ""),
                       EPMC_RENDER: (200, make_pdf(), "application/pdf")})
    result = EuropePmcSource(http).fetch(_epmc_ids(), need_pdf=False, need_supplements=False)
    assert result.pdf_status is None, "not asked for"

    result = EuropePmcSource(http).fetch(_epmc_ids(), need_pdf=True, need_supplements=False)
    assert result.pdf_status == "ok"
    assert _statuses(result, "pdf") == ["download_failed", "ok"]
    assert result.pdf.url.startswith("https://europepmc.org/articles/")


def test_the_render_fallback_is_not_added_twice():
    """A fullTextUrlList that already names the render URL must not be tried twice:
    two identical requests to the same host cost the throttle interval for nothing."""
    render = f"https://europepmc.org/articles/{PMCID}?pdf=render"
    http = _epmc_http({EPMC_RENDER: (404, b"", "")})
    EuropePmcSource(http).fetch(
        _epmc_ids(pdf_urls=(render,)), need_pdf=True, need_supplements=False)
    assert http.called_matching(EPMC_RENDER) == 1


def test_no_pdf_url_and_no_pmcid_is_not_found_without_a_request():
    http = _epmc_http()
    result = EuropePmcSource(http).fetch(
        _epmc_ids(pmcid=None, pdf_urls=()), need_pdf=True, need_supplements=False)

    assert result.pdf_status == "not_found"
    assert http.calls == []
    assert any(a["detail"] == "no open-access PDF URL known" for a in result.attempts)


def test_a_transport_failure_moves_to_the_next_candidate():
    class FirstExplodes(FakeHttp):
        def get(self, url, params=None, accept=None, allow_redirects=True):
            if OA_PDF_URL in url:
                raise HttpError("connection reset")
            return super().get(url, params, accept, allow_redirects)

    http = FirstExplodes(_epmc_http({EPMC_RENDER: (200, make_pdf(), "application/pdf")}).routes)
    result = EuropePmcSource(http).fetch(_epmc_ids(), need_pdf=True, need_supplements=False)

    assert result.pdf_status == "ok"
    assert _statuses(result, "pdf") == ["download_failed", "ok"]


def test_the_most_informative_failure_survives_the_candidate_loop():
    """A static "last one wins" would report the render endpoint's generic miss and
    hide that the publisher's own URL served a paywall stub -- which is the fact a
    curator needs to decide whether the browser tier is worth trying."""
    http = _epmc_http({OA_PDF_URL: (200, PAYWALL_HTML * 20, "application/pdf"),
                       EPMC_RENDER: (404, b"", "")})
    result = EuropePmcSource(http).fetch(_epmc_ids(), need_pdf=True, need_supplements=False)

    assert result.pdf_status == "paywalled"
    assert result.pdf is None


def test_every_candidate_failing_is_download_failed():
    http = _epmc_http({OA_PDF_URL: (500, b"", ""), EPMC_RENDER: (404, b"", "")})
    result = EuropePmcSource(http).fetch(_epmc_ids(), need_pdf=True, need_supplements=False)
    assert result.pdf_status == "download_failed"


# -- Europe PMC: the JATS XML ------------------------------------------------

def test_the_xml_is_kept_because_it_is_free_and_structured():
    http = _epmc_http()
    result = EuropePmcSource(http).fetch(_epmc_ids(), need_pdf=True, need_supplements=False)

    assert [f.role for f in result.files] == ["fulltext_pdf", "fulltext_xml"]
    assert _statuses(result, "xml") == ["ok"]


@pytest.mark.parametrize("response,expected", [
    ((404, b"", ""), "not_available"),
    ((503, b"", ""), "http_error"),
    ((200, b"", "application/xml"), "http_error"),   # 200 with an empty body
])
def test_a_missing_xml_never_costs_the_pdf(response, expected):
    http = _epmc_http({EPMC_XML: response})
    result = EuropePmcSource(http).fetch(_epmc_ids(), need_pdf=True, need_supplements=False)

    assert result.pdf_status == "ok"
    assert [f.role for f in result.files] == ["fulltext_pdf"]
    assert _statuses(result, "xml") == [expected]


def test_a_transport_failure_on_the_xml_is_recorded():
    class XmlExplodes(FakeHttp):
        def get(self, url, params=None, accept=None, allow_redirects=True):
            if EPMC_XML in url:
                raise HttpError("read timeout")
            return super().get(url, params, accept, allow_redirects)

    result = EuropePmcSource(XmlExplodes(_epmc_http().routes)).fetch(
        _epmc_ids(), need_pdf=True, need_supplements=False)
    assert _statuses(result, "xml") == ["request_failed"]
    assert result.pdf_status == "ok"


def test_the_xml_is_not_requested_without_a_pmcid():
    http = _epmc_http()
    EuropePmcSource(http).fetch(_epmc_ids(pmcid=None), need_pdf=True, need_supplements=False)
    assert http.called_matching(EPMC_XML) == 0


# -- Europe PMC: an advertised free PDF that is not free ---------------------
#
# `europepmc` ran for 10.1016/j.jhep.2020.05.039 despite the paper having no PMCID,
# which under `applies` can only mean Europe PMC advertised an open-access PDF URL.
# It tried that URL over plain HTTP, failed, and said nothing -- the same
# problems/attempts split that `_fetch_pdf` had in the browser tier.

def test_an_advertised_free_pdf_that_fails_is_worth_saying_out_loud():
    """A public index claiming free access to a paywalled article is actionable in a
    way that the `?pdf=render` fallback failing is not: it means the index is wrong
    about this paper, which is why only the advertised URLs earn a problem line."""
    http = _epmc_http({OA_PDF_URL: (200, PAYWALL_HTML * 20, "application/pdf"),
                       EPMC_RENDER: (404, b"", "")})
    result = EuropePmcSource(http).fetch(_epmc_ids(), need_pdf=True, need_supplements=False)

    advertised = [p for p in result.problems if "advertised a free PDF" in p]
    assert len(advertised) == 1, "one line for the advertised URL, none for the fallback"
    assert OA_PDF_URL in advertised[0]
    assert "paywalled" in advertised[0]


def test_the_render_fallback_failing_is_not_a_problem_line():
    """It is Europe PMC's own renderer, not a claim about this article's licence, so
    it failing says nothing a user can act on."""
    http = _epmc_http({OA_PDF_URL: (404, b"", ""), EPMC_RENDER: (404, b"", "")})
    result = EuropePmcSource(http).fetch(
        _epmc_ids(pdf_urls=()), need_pdf=True, need_supplements=False)

    assert result.pdf_status == "download_failed"
    assert result.problems == []


def test_a_pdf_that_arrives_from_europepmc_reports_nothing():
    http = _epmc_http()
    result = EuropePmcSource(http).fetch(_epmc_ids(), need_pdf=True, need_supplements=False)
    assert result.pdf_status == "ok" and result.problems == []


def test_a_named_diagnosis_survives_a_later_generic_failure():
    """The hole in `test_the_most_informative_failure_survives_the_candidate_loop`
    below: that test's second candidate 404s, which `continue`s before the status is
    ever reassigned, so it never exercised the overwrite. Two candidates that both
    return a *body* do -- and `paywalled` was being thrown away for `not_a_pdf`,
    before `fetcher._best_pdf_status` could rank it."""
    second = "https://example.org/viewer.pdf"
    http = _epmc_http({OA_PDF_URL: (200, PAYWALL_HTML * 20, "application/pdf"),
                       second: (200, b"<html>an HTML viewer</html>" * 40, "text/html")})
    result = EuropePmcSource(http).fetch(
        _epmc_ids(pmcid=None, pdf_urls=(OA_PDF_URL, second)),
        need_pdf=True, need_supplements=False)

    assert [a["status"] for a in result.attempts if a["action"] == "pdf"] == \
        ["paywalled", "not_a_pdf"]
    assert result.pdf_status == "paywalled", "the diagnosis outranks the generic miss"


def test_a_diagnosis_arriving_second_still_wins():
    """The mirror of the test above: the generic miss comes first this time, so the
    fix cannot be "keep whichever was seen first"."""
    second = "https://example.org/second.pdf"
    http = _epmc_http({OA_PDF_URL: (200, b"<html>viewer</html>" * 40, "text/html"),
                       second: (200, PAYWALL_HTML * 20, "application/pdf")})
    result = EuropePmcSource(http).fetch(
        _epmc_ids(pmcid=None, pdf_urls=(OA_PDF_URL, second)),
        need_pdf=True, need_supplements=False)
    assert result.pdf_status == "paywalled"


@pytest.mark.parametrize("current,incoming,expected", [
    (None, "not_a_pdf", "not_a_pdf"),
    ("not_a_pdf", "paywalled", "paywalled"),      # a diagnosis beats a generic miss
    ("paywalled", "not_a_pdf", "paywalled"),      # ... in either order
    ("paywalled", "session_expired", "paywalled"),         # PDF_DIAGNOSES order decides
    ("session_expired", "paywalled", "paywalled"),         # ... regardless of arrival
    ("not_a_pdf", "download_failed", "download_failed"),   # neither named: last wins
])
def test_which_pdf_failure_is_kept(current, incoming, expected):
    assert better_pdf_failure(current, incoming) == expected


# -- the tier and the orchestrator must not rank failures differently --------

_FAILURE_VOCABULARY = list(PDF_DIAGNOSES) + [
    "not_a_pdf", "download_failed", "not_in_oa_subset", "not_found",
]


def test_the_tier_and_the_orchestrator_rank_failures_identically():
    """The word a user reads must not depend on whether two statuses came from one
    tier or from two.

    `europepmc` used to carry its own copy of the diagnosis order, and the copy had
    drifted: it kept the *later* diagnosis where `_best_pdf_status` keeps the
    higher-ranked one, so 10 of 64 status pairs resolved differently. Because the
    tier collapses its candidates before the orchestrator sees them, the better word
    was unrecoverable by the time anything could notice.

    Exhaustive rather than exemplary on purpose -- the drift was in the 10 pairs
    nobody had written an example for. Successes are excluded because an accepted
    PDF returns from the candidate loop before this ever runs.
    """
    for a in _FAILURE_VOCABULARY:
        for b in _FAILURE_VOCABULARY:
            assert better_pdf_failure(a, b) == _best_pdf_status([a, b]), f"{a} then {b}"
            for c in _FAILURE_VOCABULARY:
                assert better_pdf_failure(better_pdf_failure(a, b), c) == \
                    _best_pdf_status([a, b, c]), f"{a} then {b} then {c}"


# -- a success says nothing --------------------------------------------------

def test_a_dead_publisher_link_says_nothing_when_the_renderer_delivers():
    """The regression the buffering exists to prevent, and the ordinary shape for a
    PMC-held article: `fullTextUrlList` advertises a publisher URL that is dead, and
    Europe PMC's own renderer serves the PDF a moment later.

    Complaining mid-loop reported `pdf_status='ok'` *and* a `!` line. A success emits
    no problem line -- otherwise every batch row grows one and the real failures stop
    standing out. Same contract as `test_a_pdf_that_arrives_reports_no_problem_at_all`
    in `test_browser_tier.py`.
    """
    dead = "https://publisher.example/dead.pdf"
    http = _epmc_http({dead: (404, b"", ""),
                       EPMC_RENDER: (200, make_pdf(), "application/pdf")})
    result = EuropePmcSource(http).fetch(
        _epmc_ids(pdf_urls=(dead,)), need_pdf=True, need_supplements=False)

    assert result.pdf_status == "ok"
    assert result.problems == [], "a fetch that succeeded has nothing to complain about"


def test_the_renderer_does_not_complain_about_itself_when_epmc_lists_it():
    """For a PMC-held article the render URL is *also* in `fullTextUrlList`, so it
    arrives inside `open_access_pdf_urls()` and looked "advertised" -- earning the
    complaint the code and the commit message both said it was exempt from.

    That shape is the normal one, not a corner case: it is the reason
    `_candidate_pdf_urls` has to dedupe the fallback at all.
    """
    render = RENDER_PDF.format(pmcid=PMCID)
    http = _epmc_http({EPMC_RENDER: (404, b"", "")})
    ids = _epmc_ids(pdf_urls=(render,))

    assert EuropePmcSource(http)._candidate_pdf_urls(ids) == [render], "listed once"
    result = EuropePmcSource(http).fetch(ids, need_pdf=True, need_supplements=False)
    assert result.problems == []


def test_a_url_listed_twice_is_fetched_once_and_reported_once():
    """`fullTextUrlList` repeats a URL across entries that differ only in their
    `availability` wording. Each copy cost a second identical request and printed a
    second identical `!` line for one failure."""
    http = _epmc_http({OA_PDF_URL: (404, b"", ""), EPMC_RENDER: (404, b"", "")})
    ids = _epmc_ids(pmcid=None, pdf_urls=(OA_PDF_URL, OA_PDF_URL))
    result = EuropePmcSource(http).fetch(ids, need_pdf=True, need_supplements=False)

    assert http.called_matching(OA_PDF_URL) == 1
    assert len(result.problems) == 1


def test_a_redirect_is_recorded_because_the_verdict_came_from_where_it_landed():
    """`validate_pdf` judges `resp.url`, not the URL we asked for, so `paywalled` can
    be a statement about a host the advertised link merely pointed at. Without the
    final URL, nothing downstream can check that verdict against the right page."""
    landing = "https://publisher.example/paywall"

    class Redirecting:
        def get(self, url, params=None, accept=None, allow_redirects=True):
            return Response(url=landing, status=200, content=PAYWALL_HTML * 20,
                            content_type="application/pdf")

    result = EuropePmcSource(Redirecting()).fetch(
        _epmc_ids(pmcid=None), need_pdf=True, need_supplements=False)

    attempt = [a for a in result.attempts if a["action"] == "pdf"][0]
    assert attempt["url"] == OA_PDF_URL and attempt["final_url"] == landing
    assert landing in result.problems[0]


def test_a_request_that_did_not_move_records_no_final_url():
    """Only redirects are worth a key in the manifest; the ordinary case stays quiet."""
    http = _epmc_http({OA_PDF_URL: (200, PAYWALL_HTML * 20, "application/pdf")})
    result = EuropePmcSource(http).fetch(
        _epmc_ids(pmcid=None), need_pdf=True, need_supplements=False)

    attempt = [a for a in result.attempts if a["action"] == "pdf"][0]
    assert "final_url" not in attempt
