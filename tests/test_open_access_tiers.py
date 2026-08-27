"""The open-access tiers, at the level of the whole tier rather than its helpers.

Two of the five reach a file list by pattern-matching rendered HTML rather than by
reading an enumeration, and those two had almost no offline coverage -- 14% and 17%
-- which is the worst place for it. A markup change on either page shrinks the list
silently, and the whole point of the status taxonomy is that a shrunk list must not
read as "this paper has no supplements".

So what these tests defend is mostly the *naming* of outcomes:

- `none_listed` when the page is authoritative and empty,
- `page_not_parsed` when it is not,
- `fetched_unverified` when a regex over HTML is all the evidence there is,
- and plain `fetched` only where something really does bound the set: the OA
  package, whose tarball is the deposit, and `pmc_s3`'s object listing, which is
  the deposit's index. Both of those have tests for every way the bound can come
  off, because that is what turns the strongest word in the taxonomy into a lie.

Since `fetch.text_bearing_only` landed, a tier's default is to refuse the files no
text can be extracted from before spending the request. Tests whose subject is that
refusal use the default; tests whose subject is an article *figure* -- the role
split, the cap ordering, the role-aware verdict -- pass `EVERYTHING` below, because
a figure is only fetched at all with the policy off. Both directions are the point:
the second group is what makes `text_bearing_only: false` a promise.

`test_units.py` covers the pure helpers these tiers call (`_classify`,
`_unpack_tgz`, `ftp_to_https`); this file covers what the tier decides.
"""

import json
import os
from pathlib import Path
from unittest import mock

import pytest
import yaml

from manuscript_harvest.fetch import store
from manuscript_harvest.fetch.fetcher import _best_pdf_status, _with_env_credentials
from manuscript_harvest.fetch.http import HttpError, Response
from manuscript_harvest.fetch.identifiers import Identifiers
from manuscript_harvest.fetch.sources import DEFAULT_TIERS, OA_TIERS
from manuscript_harvest.fetch.sources.elsevier_tdm import ElsevierTdmSource
from manuscript_harvest.fetch.sources.biorxiv import (
    BiorxivSource,
    _media_links,
    _version_key,
)
from manuscript_harvest.fetch.sources.europepmc import RENDER_PDF, EuropePmcSource
from manuscript_harvest.fetch.sources.pmc_oa import PmcOaSource
from manuscript_harvest.fetch.sources.pmc_s3 import PmcS3Source
from manuscript_harvest.fetch.sources.pmc_supplements import (
    PmcSupplementsSource,
    _springer_url,
)
from manuscript_harvest.fetch.validate import PDF_DIAGNOSES, better_pdf_failure

#: `fetch.text_bearing_only` is on by default, and it refuses every extension
#: `pmc_oa.supplement_or_media` routes to `media/` -- they are all image extensions.
#: So the tests below that assert on article figures at all have to say which run
#: they are describing, and this is that run: the one that fetches everything, which
#: is what this tool did before the policy existed. They keep their value twice over.
#: The role split they pin is what makes the *filter* per-role -- a refused figure
#: must not read as a missing supplement -- and pinning them here is what makes
#: `text_bearing_only: false` a promise rather than a claim.
EVERYTHING = {"text_bearing_only": False}

from tests.fakes import (
    DOI,
    PAYWALL_HTML,
    PMCID,
    POW_HTML,
    S3_HOST,
    S3_NS,
    FakeHttp,
    FakeS3Http,
    biorxiv_details_json,
    make_pdf,
    make_tgz,
    make_zip,
    s3_http,
    s3_listing,
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
        def get(self, url, params=None, accept=None, allow_redirects=True,
                headers=None):
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
        def get(self, url, params=None, accept=None, allow_redirects=True,
                headers=None):
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
        def get(self, url, params=None, accept=None, allow_redirects=True,
                headers=None):
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
        def get(self, url, params=None, accept=None, allow_redirects=True,
                headers=None):
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
        def get(self, url, params=None, accept=None, allow_redirects=True,
                headers=None):
            if url.endswith("media-2.zip"):
                self.calls.append(url)
                return Response(url=url, status=404, content=b"", content_type="")
            return super().get(url, params, accept, allow_redirects)

    http = OneDead(_biorxiv_http().routes)
    result = BiorxivSource(http).fetch(_preprint_ids(), need_pdf=False, need_supplements=True)

    assert result.suppl_status == "partial_failure"
    assert len(result.files) == 1


def test_a_rate_limited_supplement_is_a_named_problem_not_a_silent_partial():
    """`partial_failure` blocks `complete` all the way through `extract`, and this
    branch used to reach it while leaving `problems: []`. Four articles in this
    corpus sat in exactly that state: permanently short of supplements, with the
    reason recorded only in `attempts` and nothing in the place a reader looks
    first. 429 is named apart because it says nothing is wrong with the file --
    bioRxiv throttled us and every one of those files is still there."""
    class RateLimited(FakeHttp):
        def get(self, url, params=None, accept=None, allow_redirects=True,
                headers=None):
            if url.endswith("media-2.zip"):
                return Response(url=url, status=429, content=b"", content_type="")
            return super().get(url, params, accept, allow_redirects)

    http = RateLimited(_biorxiv_http().routes)
    result = BiorxivSource(http).fetch(_preprint_ids(), need_pdf=False,
                                       need_supplements=True)

    assert result.suppl_status == "partial_failure"
    assert result.problems, "a partial_failure with no problem is unreadable"
    assert any("429" in p and "re-fetch later" in p for p in result.problems)


def test_a_supplement_http_error_that_is_not_429_says_the_status():
    """Same requirement, without the 429 wording: whatever went wrong has to reach
    `problems`, because that is what the manifest shows."""
    class Gone(FakeHttp):
        def get(self, url, params=None, accept=None, allow_redirects=True,
                headers=None):
            if url.endswith("media-2.zip"):
                return Response(url=url, status=403, content=b"", content_type="")
            return super().get(url, params, accept, allow_redirects)

    http = Gone(_biorxiv_http().routes)
    result = BiorxivSource(http).fetch(_preprint_ids(), need_pdf=False,
                                       need_supplements=True)

    assert result.suppl_status == "partial_failure"
    assert any("media-2.zip failed" in p and "HTTP 403" in p
               for p in result.problems)
    assert not any("429" in p for p in result.problems)


def test_links_found_but_none_retrievable_is_page_not_parsed_not_none_listed():
    """The page named files and we got none: the one case where an empty
    `supplementary/` directory must never read as "there are none"."""
    http = _biorxiv_http({MEDIA: (403, b"", "")})
    result = BiorxivSource(http).fetch(_preprint_ids(), need_pdf=False, need_supplements=True)

    assert result.suppl_status == "page_not_parsed"
    assert result.files == []


def test_a_supplement_transport_failure_is_a_problem_not_an_abort():
    class OneExplodes(FakeHttp):
        def get(self, url, params=None, accept=None, allow_redirects=True,
                headers=None):
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
        def get(self, url, params=None, accept=None, allow_redirects=True,
                headers=None):
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
        def get(self, url, params=None, accept=None, allow_redirects=True,
                headers=None):
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
    # The obstacle is a problem and stays in the manifest; "use the browser tier"
    # is advice, which `fetch_publication` drops if a later tier gets the files.
    assert any("2 supplementary file(s) are behind NCBI's proof-of-work page" in p
               for p in result.problems)
    assert any("browser tier is required" in a for a in result.suppl_advice)
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
        def get(self, url, params=None, accept=None, allow_redirects=True,
                headers=None):
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
        def get(self, url, params=None, accept=None, allow_redirects=True,
                headers=None):
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
        def get(self, url, params=None, accept=None, allow_redirects=True,
                headers=None):
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
    result = PmcOaSource(http, {**EVERYTHING, "try_oa_package": True}).fetch(
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
        def get(self, url, params=None, accept=None, allow_redirects=True,
                headers=None):
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
    result = PmcOaSource(http, {**EVERYTHING, "try_oa_package": True}).fetch(
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


# -- PMC S3: the Open Access bucket ------------------------------------------
#
# The only supplement route in this file whose file list is neither an archive nor a
# regex over a page. What these tests defend is that the enumeration is read whole --
# every version, every page -- because `fetched` is claimed on the strength of it.

V1 = f"{PMCID}.1"
V2 = f"{PMCID}.2"

#: PMC8941949's deposit, which is where every measured fact about the layout in
#: `pmc_s3`'s docstring comes from, with its PMCID swapped for this file's. The two
#: JPEGs are the point of the fixture: one is supplementary material and one is an
#: article figure, and only the name says so.
DEPOSIT = [
    (f"{V1}/{V1}.pdf", 6368896),
    (f"{V1}/{V1}.xml", 120000),
    (f"{V1}/{V1}.txt", 90000),
    (f"{V1}/{V1}.json", 4000),
    (f"{V1}/NIHMS1758707-supplement-1.jpg", 200000),
    (f"{V1}/NIHMS1758707-supplement-10.xlsx", 26835),
    (f"{V1}/nihms-1758707-f0001.jpg", 300000),
]


def _s3_http(*, deposit=None, pages=None, routes=None) -> FakeS3Http:
    """`fakes.s3_http` with this file's deposit as the default listing.

    The bucket fake itself lives in `tests.fakes` because `test_pipeline` drives the
    same listing through `fetch_publication`, and one `ListObjectsV2` fixture with
    two copies is one fixture that will stop matching the service.
    """
    return s3_http(pages=pages, routes=routes,
                   deposit=DEPOSIT if deposit is None else deposit)


def test_pmc_s3_needs_a_pmcid():
    """The prefix *is* the PMCID; there is nothing else to list by."""
    source = PmcS3Source(FakeHttp())
    assert source.applies(_pmc_ids()) is True
    assert source.applies(Identifiers(doi=DOI, doi_raw=DOI)) is False


def test_the_listing_asks_for_the_prefix_with_its_trailing_dot():
    """Without the dot, `prefix=PMC1002` also matches `PMC10020035.2/...` and another
    article's deposit arrives as this one's supplements."""
    http = _s3_http()
    PmcS3Source(http).fetch(_pmc_ids(), need_pdf=False, need_supplements=True)

    assert http.calls[0] == f"https://{S3_HOST}/"
    assert http.params[0]["prefix"] == f"{PMCID}."
    assert http.params[0]["list-type"] == "2"


def test_the_highest_version_wins_and_says_which_it_took():
    """Both versions really are in the bucket -- 95 keys of a `PMC1002*` sample sit
    under a non-1 version -- and taking v1 of a revised article would file superseded
    text and a superseded supplement set under a DOI that now means something else."""
    deposit = [
        (f"{V1}/{V1}.pdf", 100), (f"{V1}/old-supplement-1.xlsx", 100),
        (f"{V2}/{V2}.pdf", 200), (f"{V2}/new-supplement-1.xlsx", 200),
    ]
    http = _s3_http(deposit=deposit)
    result = PmcS3Source(http).fetch(_pmc_ids(), need_pdf=True, need_supplements=True)

    note = next(a for a in result.attempts if a["action"] == "version")
    assert (note["chosen"], note["available"]) == (2, [1, 2])
    assert [f.name for f in result.by_role("supplement")] == ["new-supplement-1.xlsx"]
    assert f"{V2}/{V2}.pdf" in result.pdf.url
    assert all(f"/{V1}/" not in url for url in http.calls), "v1 must not be requested"


def test_a_version_holding_only_metadata_sidecars_is_passed_over():
    """`max(version)` is not the rule, because a version directory is not always a
    deposit. Measured live on PMC8494648 and PMC8828466, both in the local corpus:
    `.1` is the publisher's version of record -- 29 and 21 objects, a CC BY article
    PDF, 25 and 17 payload files -- while `.2` holds exactly `<prefix>.json`, `.txt`
    and `.xml`, the NIHMS author-manuscript record with no PDF and no payload at all,
    unchanged in the bucket for two months.

    Taking `.2` reported `not_found` for a PDF named in the same response and "none
    listed" over 25 files, for two DOIs whose own manifests show `europepmc`
    answering 500 and the PDF arriving only through a headed browser on an
    institutional proxy -- the case this tier exists to remove.
    """
    deposit = [
        (f"{V1}/{V1}.pdf", 21305885), (f"{V1}/{V1}.xml", 100),
        (f"{V1}/{V1}.txt", 100), (f"{V1}/{V1}.json", 100),
        (f"{V1}/supplement-1.xlsx", 100),
        (f"{V2}/{V2}.json", 100), (f"{V2}/{V2}.txt", 100), (f"{V2}/{V2}.xml", 100),
    ]
    http = _s3_http(deposit=deposit)
    result = PmcS3Source(http).fetch(_pmc_ids(), need_pdf=True, need_supplements=True)

    assert result.pdf_status == "ok" and f"{V1}/{V1}.pdf" in result.pdf.url
    assert [f.name for f in result.by_role("supplement")] == ["supplement-1.xlsx"]
    note = next(a for a in result.attempts if a["action"] == "version")
    assert (note["chosen"], note["available"], note["passed_over"]) == (1, [1, 2], [2])
    assert "sidecars only" in note["passed_over_reason"], "say why, or it reads as the bug"


def test_a_version_that_merely_holds_fewer_files_is_still_the_current_one():
    """The rule is narrow on purpose. PMC10901738's highest version holds 29 payload
    objects against v1's 31, and that is a revision withdrawing supplements, not
    damage -- reading it as damage is how versions would get merged by the back
    door."""
    deposit = [
        (f"{V1}/{V1}.pdf", 100), (f"{V1}/old-supplement-1.xlsx", 100),
        (f"{V1}/old-supplement-2.xlsx", 100),
        (f"{V2}/{V2}.pdf", 100), (f"{V2}/new-supplement-1.xlsx", 100),
    ]
    result = PmcS3Source(_s3_http(deposit=deposit)).fetch(
        _pmc_ids(), need_pdf=True, need_supplements=True)

    assert [f.name for f in result.by_role("supplement")] == ["new-supplement-1.xlsx"]
    note = next(a for a in result.attempts if a["action"] == "version")
    assert (note["chosen"], "passed_over" in note) == (2, False)


def test_the_deposit_is_split_by_name_not_by_file_type():
    """PMC8941949 deposits `NIHMS1758707-supplement-1.jpg` beside
    `nihms-1758707-f0001.jpg`. Same file type; only the name says one is
    supplementary material and the other is one of the article's own figures, which
    is why `pmc_oa.supplement_or_media` tests the markers before the extension."""
    http = _s3_http()
    result = PmcS3Source(http, EVERYTHING).fetch(
        _pmc_ids(), need_pdf=True, need_supplements=True)

    assert [f.name for f in result.by_role("supplement")] == [
        "NIHMS1758707-supplement-1.jpg", "NIHMS1758707-supplement-10.xlsx"]
    assert [f.name for f in result.by_role("media")] == ["nihms-1758707-f0001.jpg"]
    assert result.pdf_status == "ok" and result.pdf.name == "fulltext.pdf"
    assert [f.name for f in result.by_role("fulltext_xml")] == ["fulltext.nxml"]


def test_the_text_and_metadata_sidecars_are_skipped_and_recorded():
    """`<prefix>.txt` and `<prefix>.json` are derived from the PDF and JATS this tier
    already stores. Silently dropping them would leave a manifest in which they look
    like files that were missed."""
    http = _s3_http()
    result = PmcS3Source(http).fetch(_pmc_ids(), need_pdf=True, need_supplements=True)

    note = next(a for a in result.attempts if a["action"] == "deposit")
    assert note["skipped"] == [f"{V1}.txt", f"{V1}.json"]
    assert http.called_matching(".txt") == 0 and http.called_matching(".json") == 0


def test_an_oversize_supplement_is_refused_from_the_listing_alone():
    """The one thing only this tier can do: `<Size>` arrives *with* the key, so the
    cap is enforced before a byte moves. The assertion that matters is the request
    that never happened -- every other tier has to ask, or download, to find out."""
    deposit = [(f"{V1}/{V1}.pdf", 100),
               (f"{V1}/huge-supplement-1.zip", 900 * 1024 ** 2)]
    http = _s3_http(deposit=deposit)
    result = PmcS3Source(http, {"max_file_mb": 200}).fetch(
        _pmc_ids(), need_pdf=False, need_supplements=True)

    assert http.called_matching("huge-supplement-1.zip") == 0
    assert result.by_role("supplement") == []
    assert _statuses(result, "supplement_file") == ["too_large"]
    assert any("900.0 MB exceeds the 200 MB cap" in p for p in result.problems)
    assert result.suppl_status == "partial_failure"


def test_an_oversize_article_pdf_is_refused_the_same_way():
    http = _s3_http(deposit=[(f"{V1}/{V1}.pdf", 500 * 1024 ** 2)])
    result = PmcS3Source(http, {"max_file_mb": 200}).fetch(
        _pmc_ids(), need_pdf=True, need_supplements=False)

    assert result.pdf_status == "too_large" and result.pdf is None
    assert http.called_matching(f"{V1}.pdf") == 0


def test_an_unparseable_size_falls_back_to_checking_what_arrived():
    """None means unknown, not zero. Reading a malformed `<Size>` as 0 would wave an
    oversize object straight through the cap, so the pre-check is skipped and the
    body is measured instead."""
    http = _s3_http(
        deposit=[(f"{V1}/supplement-1.bin", "not-a-number")],
        routes={"supplement-1.bin": (200, b"x" * 5000, "application/octet-stream")})
    result = PmcS3Source(http, {"max_file_mb": 0}).fetch(
        _pmc_ids(), need_pdf=False, need_supplements=True)

    assert http.called_matching("supplement-1.bin") == 1, "it had to be fetched to be judged"
    assert result.by_role("supplement") == []
    note = next(a for a in result.attempts if a["action"] == "supplement_file")
    assert note["status"] == "too_large" and "no usable <Size>" in note["detail"]


def test_the_listing_follows_the_continuation_token():
    """A page holds 1000 keys and a big deposit runs past that. Reading only the
    first page would report a truncated set as the whole deposit -- and then claim
    `fetched` over it, which is the one thing this tier's status must never do."""
    pages = {
        None: s3_listing((f"{V1}/{V1}.pdf", 100), (f"{V1}/supplement-1.xlsx", 100),
                          token="1/opaque+token="),
        "1/opaque+token=": s3_listing((f"{V1}/supplement-2.xlsx", 100)),
    }
    http = _s3_http(pages=pages)
    result = PmcS3Source(http).fetch(_pmc_ids(), need_pdf=False, need_supplements=True)

    assert "continuation-token" not in http.params[0]
    assert http.params[1]["continuation-token"] == "1/opaque+token="
    assert [f.name for f in result.by_role("supplement")] == ["supplement-1.xlsx",
                                                             "supplement-2.xlsx"]
    assert result.suppl_status == "fetched"
    note = next(a for a in result.attempts if a["action"] == "listing")
    assert (note["status"], note["pages"], note["keys"]) == ("ok", 2, 3)


def test_a_complete_listing_that_all_arrives_earns_plain_fetched():
    """The strongest word the taxonomy has, and an object listing is what licenses
    it. `pmc_supplements` regexes PMC's HTML for these same files and has to settle
    for `fetched_unverified`; here the store that holds the bytes enumerated them,
    and there is no markup that could change and shrink the list silently."""
    result = PmcS3Source(_s3_http()).fetch(_pmc_ids(), need_pdf=False,
                                           need_supplements=True)
    assert result.suppl_status == "fetched"
    assert _statuses(result, "supplements") == ["fetched"]


def test_the_cap_truncating_the_deposit_costs_the_fetched_claim():
    """`fetched` means "they exist and we have them". Over a set the cap shortened,
    that is exactly the lie `fetched_unverified` was introduced to stop.

    But it costs `fetched` without costing the article its *settlement*. A count cap
    is deterministic -- the bucket's key order does not change between runs -- so
    `partial_failure` here would keep the article out of `store.SUPPL_SETTLED` and
    make every batch from now on re-list and re-download the whole deposit to drop
    the identical tail again. `europepmc._unpack_zip` stops at the same cap and its
    caller still reports plain `fetched`, and `proxy_browser._download_all` says in
    as many words that this must not masquerade as a partial failure;
    `fetched_unverified` is the word that agrees with both without claiming the set
    is whole.
    """
    deposit = [(f"{V1}/supplement-{n}.xlsx", 100) for n in range(1, 6)]
    http = _s3_http(deposit=deposit)
    result = PmcS3Source(http, {"max_files": 3}).fetch(
        _pmc_ids(), need_pdf=False, need_supplements=True)

    assert len(result.by_role("supplement")) == 3
    assert result.suppl_status == "fetched_unverified"
    assert result.suppl_status in store.SUPPL_SETTLED, "a cap must not churn forever"
    assert _statuses(result, "cap") == ["truncated"]
    assert any("2 supplementary file(s) not fetched" in p for p in result.problems), \
        "'file(s)', not 'link(s)': S3 listed these, so a dropped one is a known file"


def test_a_size_refusal_stays_on_the_failure_side_of_that_line():
    """The counterpart, and the reason the two caps are not one rule. `max_file_mb`
    refuses one named file over a size the listing states: that is a fact about that
    file rather than a budget decision about the article, the reader can act on it by
    raising the cap, and it is what every other tier does with an oversize member --
    `europepmc._unpack_zip` raises and costs the whole archive its status."""
    deposit = [(f"{V1}/supplement-1.xlsx", 100),
               (f"{V1}/huge-supplement-2.zip", 900 * 1024 ** 2)]
    result = PmcS3Source(_s3_http(deposit=deposit), {"max_file_mb": 200}).fetch(
        _pmc_ids(), need_pdf=False, need_supplements=True)

    assert [f.name for f in result.by_role("supplement")] == ["supplement-1.xlsx"]
    assert result.suppl_status == "partial_failure"
    assert result.suppl_status not in store.SUPPL_SETTLED


def test_the_cap_is_spent_on_supplements_before_article_figures():
    """The measured regression: `max_files` bounds the whole payload, and S3 lists
    keys in binary order, so `Fig1_HTML.jpg` arrives before `MOESM3_ESM.pdf` and the
    figures took the slots.

    This key list is PMC10232368's shape (10.1038/s41590-023-01504-2, in the local
    corpus): Springer Nature interleaves the article's own `Fig<n>_HTML.jpg` with the
    supplementary `MOESM<n>_ESM.*`, so the figures sit *inside* the cap rather than
    after it. At the shipped cap that article lost 8 supplementary tables -- MOESM3
    to MOESM9 and MOESM40, two of them over 9 MB -- to 8 JPEGs of its own figures,
    while holding exactly as many supplements as the cap allows. Nothing downstream
    reads `media/` at all (`extract/extractor.py` iterates `record["supplementary"]`),
    so every figure kept at the cap was a strict trade down.
    """
    stem = "41590_2023_1504"
    figures = [(f"{V1}/{stem}_Fig{n}_HTML.jpg", 100) for n in range(1, 5)]
    tables = [(f"{V1}/{stem}_MOESM{n}_ESM.xlsx", 100) for n in range(3, 7)]
    # The bucket's own order: 'F' sorts before 'M'.
    http = _s3_http(deposit=figures + tables)
    result = PmcS3Source(http, {**EVERYTHING, "max_files": 4}).fetch(
        _pmc_ids(), need_pdf=False, need_supplements=True)

    assert [f.name for f in result.by_role("supplement")] == \
        [f"{stem}_MOESM{n}_ESM.xlsx" for n in range(3, 7)], "every table, none dropped"
    assert result.by_role("media") == [], "the figures are what the cap dropped"
    assert result.suppl_status == "fetched", "the supplement set really is whole"
    assert len(http.calls) == 1 + 4, "and the request budget is still max_files"
    assert any("4 article figure(s) not fetched" in p for p in result.problems), \
        "'article figure(s)': the count and the noun have to agree"
    assert _statuses(result, "cap") == ["truncated_media"]


def test_a_refused_figure_never_spends_a_cap_slot_a_table_needed():
    """`keep_text_bearing` runs *before* `apply_files_cap`, always -- and this is the
    tier where that ordering was measured.

    The displacement above is the same one, one policy earlier: PMC10232368 lost 8
    supplementary tables, two of them over 9 MB, to 8 JPEGs of its own figures. The
    cap is a *request* budget, so spending a slot on a file that is then refused is
    the same trade down with an extra step, and `_ESM.jpg` names make it worse than
    the `media/` case -- `supplement_or_media` classifies those as supplements, so a
    figure refused after the cap has taken a slot from a table *and* leaves
    `dropped_supplements` set, which lands the article on `partial_failure`. That is
    outside `store.SUPPL_SETTLED`, so every later batch re-lists the deposit and
    re-loses the same tables.

    A cap this small is what makes the ordering visible at all: every other
    text-bearing test here runs far below the shipped `max_files`, where both orders
    give the same answer. The number is passed in rather than read from config so
    raising the shipped cap cannot quietly stop this from testing anything.
    """
    stem = "41590_2023_1504"
    figures = [(f"{V1}/{stem}_Fig{n}_ESM.jpg", 100) for n in range(1, 3)]
    tables = [(f"{V1}/{stem}_MOESM{n}_ESM.xlsx", 100) for n in range(3, 5)]
    http = _s3_http(deposit=figures + tables)
    result = PmcS3Source(http, {"max_files": 2}).fetch(
        _pmc_ids(), need_pdf=False, need_supplements=True)

    assert [f.name for f in result.by_role("supplement")] == \
        [f"{stem}_MOESM{n}_ESM.xlsx" for n in range(3, 5)], \
        "the cap's slots went to the files something downstream can read"
    assert result.suppl_status == "fetched"
    assert result.suppl_status in store.SUPPL_SETTLED, "or every batch re-lists it"
    assert not any("not fetched" in p for p in result.problems), \
        "nothing was dropped by the cap: the JPEGs never entered it"
    assert _statuses(result, "cap") == [], "and no cap note either"
    assert len(http.calls) == 1 + 2


def test_a_lost_article_figure_does_not_demote_the_supplement_verdict():
    """`suppl_status` is a sentence about supplementary material. Counting a lost
    figure against it reported `partial_failure` over a supplement set that arrived
    whole -- and then, because that word is not settled, re-downloaded every object
    in the deposit on every future run. The role is decided by the same
    `supplement_or_media` policy that decides which directory the file lands in."""
    deposit = [(f"{V1}/NIHMS1758707-supplement-1.xlsx", 100),
               (f"{V1}/nihms-1758707-f0001.jpg", 100)]
    http = _s3_http(deposit=deposit, routes={"f0001.jpg": (500, b"", "")})
    result = PmcS3Source(http, EVERYTHING).fetch(
        _pmc_ids(), need_pdf=False, need_supplements=True)

    assert [f.name for f in result.by_role("supplement")] == \
        ["NIHMS1758707-supplement-1.xlsx"]
    assert result.suppl_status == "fetched"
    assert any("1 of 2 file(s) listed in the PMC S3 deposit" in p for p in result.problems), \
        "the loss is still reported; it just answers a different question"


def test_an_oversize_article_figure_costs_the_verdict_nothing_either():
    """The other refusal path, and the reason both are role-aware: a 900 MB TIFF of
    one of the article's own panels is refused from the listing alone, and a
    supplement set that arrived whole still says so. Where a *supplement* is refused
    on size the verdict does move -- `test_a_size_refusal_stays_on_the_failure_side_
    of_that_line` is that case."""
    deposit = [(f"{V1}/NIHMS1758707-supplement-1.xlsx", 100),
               (f"{V1}/nihms-1758707-f0001.tif", 900 * 1024 ** 2)]
    http = _s3_http(deposit=deposit)
    result = PmcS3Source(http, {**EVERYTHING, "max_file_mb": 200}).fetch(
        _pmc_ids(), need_pdf=False, need_supplements=True)

    assert http.called_matching("f0001.tif") == 0
    assert result.suppl_status == "fetched"
    assert any("900.0 MB exceeds the 200 MB cap" in p for p in result.problems)


def test_a_listing_that_stops_half_way_cannot_earn_fetched():
    """Same rule from the other direction: the files that did arrive are kept, but an
    unread page could have held anything, so nothing bounds the set.

    And it says so out loud. A non-200 mid-walk was the one early exit in
    `_list_objects` that recorded an attempt and no problem line -- its `HttpError`,
    `ParseError` and page-guard siblings all add one -- so the run's summary showed
    nothing at all for the page that decides both the version and the bound.
    """
    pages = {None: s3_listing((f"{V1}/supplement-1.xlsx", 100), token="tok"),
             "tok": (503, b"", "")}
    http = _s3_http(pages=pages)
    result = PmcS3Source(http).fetch(_pmc_ids(), need_pdf=False, need_supplements=True)

    assert [f.name for f in result.by_role("supplement")] == ["supplement-1.xlsx"]
    assert result.suppl_status == "partial_failure"
    assert any("page 2 returned HTTP 503" in p for p in result.problems)


def test_a_listing_that_stops_half_way_says_so_even_with_nothing_kept():
    """The same rule where the text-bearing filter emptied the payload instead.

    `complete` is what licenses this tier's `fetched`, and the `if not kept` branch
    used to be the one place that never asked: silence there was safe while `kept == 0`
    meant "no key in the deposit looked like a supplement at all", because every word
    the fetcher then reached was unsettled and the article came back next batch. With
    the filter on, `kept == 0` is also what a deposit of figures produces -- and the
    fetcher's word for that is `none_text_bearing`, which is settled. So a truncated
    listing whose read page happened to hold only figures would freeze `complete` over
    a continuation page nobody read, and `partial_failure` is what stops it. The
    fetcher cannot: no field on a `SourceResult` carries `complete`.
    """
    pages = {None: s3_listing((f"{V1}/{V1}.pdf", 100),
                              (f"{V1}/NIHMS1758707-supplement-1.jpg", 100),
                              token="tok"),
             "tok": (503, b"", "")}
    http = _s3_http(pages=pages)
    result = PmcS3Source(http).fetch(_pmc_ids(), need_pdf=False, need_supplements=True)

    assert result.by_role("supplement") == [], "the JPEG was refused, as it should be"
    assert http.called_matching("supplement-1.jpg") == 0
    assert result.suppl_status == "partial_failure"
    assert any("page 2 returned HTTP 503" in p for p in result.problems)
    note = next(a for a in result.attempts if a["action"] == "supplements")
    assert (note["not_text_bearing"], note["complete_listing"]) == (1, False), \
        "both halves of the reason are in the record"


def test_a_listing_that_stops_half_way_hands_over_no_article_files_either():
    """The same rule, on the half of the record that could not express it.

    Keys sort lexicographically, so `PMC....1/...` is served before `PMC....2/...`
    and a walk that dies on page 2 holds *systematically* the oldest version. Taking
    the PDF off it stored v1 as `fulltext.pdf` with `pdf_status: ok` and noted
    `available: [1]` -- a positive claim about a bucket the same object knew it had
    not finished reading. The fetcher stops asking for a PDF once one arrives, so with
    the supplements settled anywhere else the record reaches `complete` and is never
    revisited: superseded text frozen under a DOI that now means something else. The
    identity check cannot catch it either, since v1 and v2 carry the same DOI and
    title.
    """
    pages = {None: s3_listing((f"{V1}/{V1}.pdf", 100), (f"{V1}/{V1}.xml", 100),
                              (f"{V1}/old-supplement-1.xlsx", 100), token="tok"),
             "tok": (503, b"", "")}
    http = _s3_http(pages=pages)
    result = PmcS3Source(http).fetch(_pmc_ids(), need_pdf=True, need_supplements=True)

    assert result.pdf_status is None, "the question stays open for a re-run"
    assert result.pdf is None and result.by_role("fulltext_xml") == []
    assert http.called_matching(f"{V1}.pdf") == 0
    assert _statuses(result, "pdf") == ["listing_incomplete"]
    assert any("did not complete" in p for p in result.problems)
    note = next(a for a in result.attempts if a["action"] == "version")
    assert note["complete_listing"] is False, "and the version note says so"
    assert [f.name for f in result.by_role("supplement")] == ["old-supplement-1.xlsx"], \
        "the payload is still taken: there the status can say it is unsettled"


def test_a_listing_that_stops_half_way_cannot_call_the_pdf_absent():
    """The mirror image, and the more reachable half: `<prefix>.pdf` sorts *after*
    `NIHMS1758707-supplement-1.jpg`, so a truncation inside one version's own keys
    leaves the article's PDF on the page that never arrived.

    `not_found` there -- "we read the enumeration and it names none" -- is a false
    absence over an enumeration nobody read to the end, and it is the one claim the
    `not_in_oa_subset` branch is careful to gate on the same flag.
    """
    pages = {None: s3_listing((f"{V1}/NIHMS1758707-supplement-1.xlsx", 100), token="tok"),
             "tok": (503, b"", "")}
    result = PmcS3Source(_s3_http(pages=pages)).fetch(
        _pmc_ids(), need_pdf=True, need_supplements=True)

    assert result.pdf_status is None, "not `not_found`, which claims the bucket names none"
    note = next(a for a in result.attempts if a["action"] == "pdf")
    assert (note["status"], note["listed_pdf"]) == ("listing_incomplete", False)


def test_one_lost_object_is_partial_failure_not_fetched():
    deposit = [(f"{V1}/supplement-1.xlsx", 100), (f"{V1}/supplement-2.xlsx", 100)]
    http = _s3_http(deposit=deposit, routes={"supplement-2.xlsx": (403, b"", "")})
    result = PmcS3Source(http).fetch(_pmc_ids(), need_pdf=False, need_supplements=True)

    assert [f.name for f in result.by_role("supplement")] == ["supplement-1.xlsx"]
    assert result.suppl_status == "partial_failure"
    assert any("1 of 2 file(s) listed in the PMC S3 deposit" in p for p in result.problems)


def test_a_transport_failure_on_one_object_does_not_sink_the_rest():
    deposit = [(f"{V1}/supplement-1.xlsx", 100), (f"{V1}/supplement-2.xlsx", 100)]

    class Exploding(FakeS3Http):
        def get(self, url, params=None, accept=None, allow_redirects=True,
                headers=None):
            if "supplement-1.xlsx" in url:
                raise HttpError("connection reset")
            return super().get(url, params, accept, allow_redirects)

    http = _s3_http(deposit=deposit)
    result = PmcS3Source(Exploding(http.pages, http.routes)).fetch(
        _pmc_ids(), need_pdf=False, need_supplements=True)

    assert [f.name for f in result.by_role("supplement")] == ["supplement-2.xlsx"]
    assert result.suppl_status == "partial_failure"
    assert _statuses(result, "supplement_file") == ["request_failed", "ok"]


def test_a_deposit_of_nothing_but_figures_claims_no_supplements():
    """Same judgement as `pmc_oa`'s `if supplements:`. The listing does bound the
    set, but "no key looked like a supplement" is a filename policy, not a statement
    about what the publisher deposited -- the fetcher weighs the silence against
    `hasSuppl` instead."""
    deposit = [(f"{V1}/f0001.jpg", 100), (f"{V1}/f0002.png", 100)]
    result = PmcS3Source(_s3_http(deposit=deposit), EVERYTHING).fetch(
        _pmc_ids(), need_pdf=False, need_supplements=True)

    assert result.suppl_status is None
    assert [f.role for f in result.files] == ["media", "media"]


def test_a_key_the_layout_did_not_predict_never_sinks_the_tier():
    """One unreadable key must not cost the article its other twenty, and must not
    cost it `fetched` either: an S3 prefix can carry a zero-byte directory marker
    (`PMC....1/`), and demoting on that would mark every article partial forever."""
    deposit = [("index.html", 10), (f"{PMCID}.x/weird.bin", 10), (f"{V1}/", 0),
               (f"{V1}/supplement-1.xlsx", 100)]
    http = _s3_http(deposit=deposit)
    result = PmcS3Source(http).fetch(_pmc_ids(), need_pdf=False, need_supplements=True)

    assert [f.name for f in result.by_role("supplement")] == ["supplement-1.xlsx"]
    assert result.suppl_status == "fetched"
    note = next(a for a in result.attempts if a["action"] == "key_shape")
    assert note["count"] == 3


def test_keys_that_are_all_unreadable_are_a_missing_pdf_not_a_missing_article():
    """The bucket answered for this prefix, so `not_in_oa_subset` -- "it is not in
    there" -- would be false. `not_found` is what the enumeration actually supports:
    we read it and it names no PDF."""
    http = _s3_http(deposit=[(f"{PMCID}.x/weird.bin", 10)])
    result = PmcS3Source(http).fetch(_pmc_ids(), need_pdf=True, need_supplements=True)

    assert result.pdf_status == "not_found"
    assert result.suppl_status is None and result.files == []


def test_another_articles_keys_are_never_adopted():
    """Belt and braces behind the trailing dot in the prefix: even if the bucket
    answered with a key for a different article, it is not this article's deposit."""
    deposit = [(f"{V1}/supplement-1.xlsx", 100),
               ("PMC9999999.1/supplement-2.xlsx", 100)]
    result = PmcS3Source(_s3_http(deposit=deposit)).fetch(
        _pmc_ids(), need_pdf=False, need_supplements=True)

    assert [f.name for f in result.by_role("supplement")] == ["supplement-1.xlsx"]


def test_an_empty_listing_is_an_authoritative_absence():
    """The bucket *is* the Open Access subset -- 322 of this corpus's 393 articles
    are in it -- so a complete listing with no keys is the same routing fact `oa.fcgi`
    reports with an `<error>`, and the fetcher should move on rather than read a
    broken download."""
    http = _s3_http(deposit=[])
    result = PmcS3Source(http).fetch(_pmc_ids(), need_pdf=True, need_supplements=True)

    assert result.pdf_status == "not_in_oa_subset"
    assert result.suppl_status is None, "the publisher may still hold supplements"
    assert len(http.calls) == 1, "an empty deposit must not trigger a download"


def test_a_deposit_without_a_pdf_reports_not_found():
    """An enumeration that names no PDF is a real absence, not a pattern that failed
    to match one."""
    http = _s3_http(deposit=[(f"{V1}/{V1}.xml", 100)])
    result = PmcS3Source(http).fetch(_pmc_ids(), need_pdf=True, need_supplements=True)

    assert result.pdf_status == "not_found"
    assert _statuses(result, "pdf") == ["not_listed"]


@pytest.mark.parametrize("page,status", [
    ((503, b"", ""), "http_error"),
    ((404, b"<Error><Code>NoSuchBucket</Code></Error>", "application/xml"), "http_error"),
    ((200, b"<ListBucketResult", "application/xml"), "unparseable_xml"),
])
def test_an_unreadable_listing_claims_nothing(page, status):
    """Deliberately unlike `pmc_oa`, which answers its own broken lookup with
    `not_in_oa_subset`: there the routing signal is the only thing the service
    produces, whereas a listing we could not read leaves what the bucket holds
    genuinely open, and a re-run or a later tier can still answer it."""
    http = _s3_http(pages={None: page})
    result = PmcS3Source(http).fetch(_pmc_ids(), need_pdf=True, need_supplements=True)

    assert result.pdf_status is None and result.suppl_status is None
    assert _statuses(result, "listing") == [status]
    assert result.files == []


def test_a_transport_failure_on_the_listing_is_a_recorded_problem():
    class Exploding(FakeHttp):
        def get(self, url, params=None, accept=None, allow_redirects=True,
                headers=None):
            raise HttpError("connection reset")

    result = PmcS3Source(Exploding()).fetch(_pmc_ids(), need_pdf=True,
                                            need_supplements=True)
    assert result.pdf_status is None and result.suppl_status is None
    assert any("pmc s3 listing failed" in p for p in result.problems)


def test_need_pdf_false_leaves_the_articles_own_files_alone():
    """The fetcher only asks for what is still missing, so a supplements-only pass
    must not spend a 6.3 MB transfer on a PDF another tier already delivered."""
    http = _s3_http()
    result = PmcS3Source(http).fetch(_pmc_ids(), need_pdf=False, need_supplements=True)

    assert result.pdf_status is None and result.by_role("fulltext_xml") == []
    assert http.called_matching(f"{V1}.pdf") == 0
    assert http.called_matching(f"{V1}.xml") == 0


def test_need_supplements_false_fetches_nothing_from_the_payload():
    http = _s3_http()
    result = PmcS3Source(http).fetch(_pmc_ids(), need_pdf=True, need_supplements=False)

    assert result.suppl_status is None
    assert [f.role for f in result.files] == ["fulltext_pdf", "fulltext_xml"]
    assert http.called_matching("supplement-1") == 0
    assert _statuses(result, "cap") == [], "a cap cannot drop files nobody asked for"


def test_contents_without_a_size_is_unknown_not_zero():
    """`<Size>` is not guaranteed by anything but observation, and an absent one must
    not read as 0 -- that is the value that walks an oversize object past the cap."""
    body = (f'<ListBucketResult xmlns="{S3_NS}"><IsTruncated>false</IsTruncated>'
            f"<Contents><Key>{V1}/supplement-1.xlsx</Key></Contents>"
            f"</ListBucketResult>").encode()
    result = PmcS3Source(_s3_http(pages={None: body})).fetch(
        _pmc_ids(), need_pdf=False, need_supplements=True)

    assert [f.name for f in result.by_role("supplement")] == ["supplement-1.xlsx"]
    assert result.suppl_status == "fetched"


def test_a_listing_that_never_stops_paginating_is_cut_off_and_said_so():
    """The page guard. 10 pages is 10,000 objects for one article, far past anything
    measured, so reaching it means the token never resolves -- and a recorded
    incomplete listing, which costs this tier its `fetched`, beats a loop."""

    class _EndlessPages(dict):
        """A bucket whose every page names one more file and one more token."""

        served = 0

        def __getitem__(self, token):
            self.served += 1
            return s3_listing((f"{V1}/supplement-{self.served}.xlsx", 100),
                               token=f"page-{self.served}")

    pages = _EndlessPages()
    result = PmcS3Source(_s3_http(pages=pages)).fetch(
        _pmc_ids(), need_pdf=False, need_supplements=True)

    assert pages.served == 10, "the walk is bounded"
    assert len(result.by_role("supplement")) == 10, "and keeps what it did read"
    assert _statuses(result, "listing") == ["still_truncated"]
    assert any("still truncated after 10 pages" in p for p in result.problems)
    assert result.suppl_status == "partial_failure"


def test_an_oversize_jats_is_refused_without_costing_the_pdf():
    http = _s3_http(deposit=[(f"{V1}/{V1}.pdf", 100), (f"{V1}/{V1}.xml", 900 * 1024 ** 2)])
    result = PmcS3Source(http, {"max_file_mb": 200}).fetch(
        _pmc_ids(), need_pdf=True, need_supplements=False)

    assert result.pdf_status == "ok"
    assert result.by_role("fulltext_xml") == []
    assert http.called_matching(f"{V1}.xml") == 0
    assert _statuses(result, "xml") == ["too_large"]


def test_a_transport_failure_on_the_s3_xml_is_recorded():
    class Exploding(FakeS3Http):
        def get(self, url, params=None, accept=None, allow_redirects=True,
                headers=None):
            if f"{V1}.xml" in url:
                raise HttpError("read timeout")
            return super().get(url, params, accept, allow_redirects)

    http = _s3_http()
    result = PmcS3Source(Exploding(http.pages, http.routes)).fetch(
        _pmc_ids(), need_pdf=True, need_supplements=False)

    assert result.pdf_status == "ok"
    assert _statuses(result, "xml") == ["request_failed"]


def test_a_failed_xml_never_costs_the_pdf():
    http = _s3_http(routes={f"{V1}.xml": (500, b"", "")})
    result = PmcS3Source(http).fetch(_pmc_ids(), need_pdf=True, need_supplements=False)

    assert result.pdf_status == "ok"
    assert _statuses(result, "xml") == ["http_error"]


def test_the_cap_can_never_spend_the_articles_own_pdf():
    """What `_split_deposit`'s hold-out is for, in the key order the bucket really
    serves: `NIHMS...` sorts before `PMC....pdf` (N < P), so on the measured
    14-supplement deposit the supplements genuinely do precede the article's PDF.
    Capping the version's whole key list and splitting afterwards passes every other
    test in this file and loses the full text."""
    deposit = [(f"{V1}/NIHMS1758707-supplement-{n}.xlsx", 100) for n in range(1, 6)]
    deposit.append((f"{V1}/{V1}.pdf", 100))
    http = _s3_http(deposit=deposit)
    result = PmcS3Source(http, {"max_files": 1}).fetch(
        _pmc_ids(), need_pdf=True, need_supplements=True)

    assert result.pdf_status == "ok" and http.called_matching(f"{V1}.pdf") == 1
    assert len(result.by_role("supplement")) == 1, "the cap still bounds the payload"


def test_a_publisher_filename_goes_out_percent_encoded():
    """A deposited filename is publisher-supplied and a space or a `#` is a legal S3
    key character: sent raw, one truncates the request at the fragment and the other
    is not a valid URL at all. `safe="/"` keeps the version directory a path
    separator and encodes the rest."""
    http = _s3_http(deposit=[(f"{V1}/supplement 1 final#2.xlsx", 100)])
    result = PmcS3Source(http).fetch(_pmc_ids(), need_pdf=False, need_supplements=True)

    requested = [url for url in http.calls if "supplement" in url]
    assert requested == [f"https://{S3_HOST}/{V1}/supplement%201%20final%232.xlsx"]
    assert [f.name for f in result.by_role("supplement")] == ["supplement 1 final#2.xlsx"], \
        "the name on disk is the publisher's, only the request is encoded"


def test_no_key_the_bucket_serves_can_name_a_file_outside_the_article():
    """What `_KEY_RX` claims at the tier level. `fetcher._write_group` routes every
    name through `store.sanitize_filename` as well, so this is defence in depth
    rather than the only guard -- but the claim is made here, so it is pinned here."""
    http = _s3_http(deposit=[(f"{V1}/../../evil-supplement-1.sh", 100)])
    result = PmcS3Source(http).fetch(_pmc_ids(), need_pdf=False, need_supplements=True)

    assert [f.name for f in result.by_role("supplement")] == ["evil-supplement-1.sh"]


def test_pmc_s3_is_tried_after_europepmc_and_before_pmc_supplements():
    """The one placement in `OA_TIERS` that `sources/__init__` argues for rather than
    inherits, and it is load-bearing twice over: behind `europepmc`, whose single ZIP
    is still the cheapest whole answer, and ahead of `pmc_supplements`, which is the
    tier that walks into PMC's proof-of-work page and then 403s.

    Moving it to the end of the list leaves the whole suite green otherwise, and the
    shipped `config.yaml` -- the list a real run actually uses -- is checked against
    the same order, because nothing else compares the two and they can drift apart
    without a single test noticing.
    """
    assert OA_TIERS.index("europepmc") < OA_TIERS.index("pmc_s3") < \
        OA_TIERS.index("pmc_supplements")

    shipped = yaml.safe_load(
        (Path(__file__).resolve().parent.parent / "config.yaml").read_text())
    assert shipped["fetch"]["tiers"] == list(DEFAULT_TIERS), \
        "config.yaml and OA_TIERS have to name the same order"


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
        def get(self, url, params=None, accept=None, allow_redirects=True,
                headers=None):
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
        def get(self, url, params=None, accept=None, allow_redirects=True,
                headers=None):
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
        def get(self, url, params=None, accept=None, allow_redirects=True,
                headers=None):
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

def test_a_size_refusal_outranks_a_later_tiers_generic_miss():
    """`pmc_s3` is the first tier to report `too_large` as a `pdf_status`, and it was
    not in `PDF_DIAGNOSES`, so the ranking could not keep it.

    Both tiers key on nothing but `ids.pmcid` and `pmc_oa` comes next in the shipped
    order, so whenever the size cap refuses a PDF, `pmc_oa` always runs and always
    assigns a status: `not_in_oa_subset` for any `oa.fcgi` error, `not_found`
    otherwise. Folding two unranked words keeps the later one, so the manifest read
    `not_in_oa_subset` for an article whose S3 listing had just proved it *is* in the
    subset -- one tier's answer overwritten with another tier's contradiction, which
    is the thing this shared table exists to prevent. It also made the fifteenth
    `fulltext.status` the README documents unreachable in every shipped tier list.
    """
    assert _best_pdf_status(["too_large", "not_in_oa_subset"]) == "too_large"
    assert _best_pdf_status(["too_large", "not_found"]) == "too_large"
    assert better_pdf_failure("not_a_pdf", "too_large") == "too_large"
    assert _best_pdf_status(["too_large", "ok"]) == "ok", \
        "a cap is not a verdict: a tier that got the file still wins"


#: Every word a tier can report for a failed PDF. The diagnoses are read from
#: `PDF_DIAGNOSES` rather than restated, so a new one is cross-ranked by the test
#: below on the day it is added -- `too_large` was added without one and the
#: exhaustive test never saw it.
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
        def get(self, url, params=None, accept=None, allow_redirects=True,
                headers=None):
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


# -- refusing what no text can come out of, tier by tier ---------------------
#
# `fetch.text_bearing_only` is applied twice on purpose: centrally in
# `fetcher.fetch_publication` as the guarantee, and again in each tier that knows a
# filename before it spends the request. These are the second half -- and the
# assertion that carries them is `called_matching(...) == 0`, because a refusal that
# still costs the request saves only disk, and disk is not what this is for. The
# archive tiers cannot do that (one blob, one transfer) and are tested for the other
# saving: the member is never decompressed, written or recorded as a file. "Never
# decompressed" is the one claim a tier test cannot make -- it is not in the return
# value -- so it is pinned against `_unpack_zip` and `_unpack_tgz` directly in
# `tests/test_units.py`, together with the ordering that keeps a refused member out
# of the `max_files` count.


def test_pmc_s3_refuses_a_figure_from_the_listing_alone():
    """The tier that pays most for this. It spends one request per object, and the
    listing names every one of them, so an illustrated article's figures cost nothing
    at all -- not a request, not a cap slot, not a manifest entry."""
    deposit = [(f"{V1}/NIHMS1758707-supplement-1.xlsx", 100),
               (f"{V1}/NIHMS1758707-supplement-2.jpg", 100),
               (f"{V1}/nihms-1758707-f0001.jpg", 100)]
    http = _s3_http(deposit=deposit)

    result = PmcS3Source(http).fetch(_pmc_ids(), need_pdf=False, need_supplements=True)

    assert [f.name for f in result.by_role("supplement")] == \
        ["NIHMS1758707-supplement-1.xlsx"]
    assert result.by_role("media") == []
    assert http.called_matching("supplement-2.jpg") == 0
    assert http.called_matching("f0001.jpg") == 0
    assert len(http.calls) == 1 + 1, "the listing, then the one file worth fetching"
    assert result.suppl_status == "fetched", \
        "the deposit was enumerated and every readable file in it arrived"
    assert [(s["name"], s["role"], s["reason"]) for s in result.skipped_not_text_bearing] \
        == [("NIHMS1758707-supplement-2.jpg", "supplement", "image"),
            ("nihms-1758707-f0001.jpg", "media", "image")]


def test_pmc_s3_leaves_an_all_figure_deposit_for_the_fetcher_to_name():
    """No status at all, which is the same silence a figures-only deposit already
    produced. Naming it here would be this tier claiming something about supplementary
    material; `fetcher._supplement_status` is the one place that sees every tier's
    refusals at once, and it calls this `none_text_bearing`."""
    deposit = [(f"{V1}/NIHMS1758707-supplement-{n}.jpg", 100) for n in (1, 2)]
    http = _s3_http(deposit=deposit)

    result = PmcS3Source(http).fetch(_pmc_ids(), need_pdf=False, need_supplements=True)

    assert result.suppl_status is None and result.files == []
    assert len(result.skipped_not_text_bearing) == 2
    note = next(a for a in result.attempts if a["action"] == "supplements")
    assert note["status"] == "none"


def test_pmc_supplements_never_asks_for_a_figure():
    """Worth more here than anywhere else: every `/bin/` URL this tier falls back to
    costs a proof-of-work page it cannot clear, so an article of figures spent a wall
    of requests to earn a wall of 403s."""
    http = _pmc_http(None, MOESM1, "41586_2021_3852_Fig1_HTML.jpg")

    result = PmcSupplementsSource(http).fetch(
        _pmc_ids(), need_pdf=False, need_supplements=True)

    assert [f.name for f in result.files] == [MOESM1]
    assert http.called_matching("Fig1_HTML.jpg") == 0
    assert result.suppl_status == "fetched_unverified"
    note = next(a for a in result.attempts if a["action"] == "text_bearing_filter")
    assert note["files"] == ["41586_2021_3852_Fig1_HTML.jpg"]
    assert note["where"] == "before_download"


def test_a_pmc_page_listing_only_figures_is_not_none_listed():
    """PMC listed files and we declined them, which is not the same statement as the
    publisher having none. `none_listed` there would be a false absence over a page
    that told us exactly what it holds."""
    http = _pmc_http(None, "fig1.jpg", "fig2.tif")

    result = PmcSupplementsSource(http).fetch(
        _pmc_ids(), need_pdf=False, need_supplements=True)

    assert result.suppl_status is None and result.files == []
    assert http.called_matching("/bin/") == 0
    note = next(a for a in result.attempts if a["action"] == "supplements")
    assert note["status"] == "none_text_bearing" and note["listed"] == 2


def test_biorxiv_refuses_a_movie_from_the_anchor():
    """`media-<n>` names the embed slot, not the file type: 10.1101/2025.07.21.666016
    serves `media-1.pdf` and `media-2.zip` through it. So the extension decides, and
    a `.mp4` in the same slot is never requested."""
    http = _biorxiv_http({SUPPL_PAGE: (200, _supplement_page("media-1.pdf",
                                                            "media-2.mp4"), "text/html")})

    result = BiorxivSource(http).fetch(
        _preprint_ids(), need_pdf=False, need_supplements=True)

    assert [f.url.endswith("media-1.pdf") for f in result.files] == [True]
    assert http.called_matching("media-2.mp4") == 0
    assert result.suppl_status == "fetched_unverified"


def test_a_biorxiv_page_of_nothing_but_movies_is_not_page_not_parsed():
    """Two false words are available here and both had to be refused. The page parsed
    perfectly, so `page_not_parsed` is wrong; bioRxiv is the authority on its own
    preprint's supplements and it listed two, so the `none_listed` branch above the
    filter is wrong too -- which is why the filter runs after it."""
    http = _biorxiv_http({SUPPL_PAGE: (200, _supplement_page("media-1.mp4",
                                                            "media-2.mov"), "text/html")})

    result = BiorxivSource(http).fetch(
        _preprint_ids(), need_pdf=False, need_supplements=True)

    assert result.suppl_status is None and result.files == []
    assert http.called_matching("/embed/media-") == 0
    note = [a for a in result.attempts if a["action"] == "supplements"][-1]
    assert note["status"] == "none_text_bearing" and note["found"] == 2


def test_the_europepmc_zip_is_filtered_on_unpack_not_on_the_wire():
    """One request, one ZIP: the transfer is paid before any member has a name, so
    there is no request to save. What the filter saves is the disk write, the manifest
    entry and an extraction record whose only content would be `image_no_text` -- and
    the member is never decompressed, which matters for the 487.8 MB supplement this
    corpus holds. That last part is invisible from here and is pinned in
    `tests/test_units.py::test_a_refused_zip_member_is_never_read_and_never_spends_a_cap_slot`."""
    http = _epmc_http({EPMC_SUPPL: (200, make_zip([("a_MOESM1_ESM.xlsx", b"xlsx"),
                                                   ("a_MOESM2_ESM.jpg", b"\xff\xd8fig"),
                                                   ("movie1.mp4", b"\x00\x00\x00 ftyp")]),
                                    "application/zip")})

    result = EuropePmcSource(http).fetch(
        _epmc_ids(), need_pdf=False, need_supplements=True)

    assert [f.name for f in result.files] == ["a_MOESM1_ESM.xlsx"]
    assert result.suppl_status == "fetched", "the member list still bounded the set"
    note = next(a for a in result.attempts if a["action"] == "text_bearing_filter")
    assert note["where"] == "on_unpack"
    assert note["files"] == ["a_MOESM2_ESM.jpg", "movie1.mp4"]
    assert note["roles"] == {"supplement": 2}, \
        "every ZIP member is a supplement here; `_unpack_zip` makes no media split"


def test_an_archive_of_nothing_but_figures_is_not_an_empty_zip():
    """`empty_zip` says the deposit is empty. This deposit has three files in it."""
    http = _epmc_http({EPMC_SUPPL: (200, make_zip([("f1.jpg", b"\xff\xd8"),
                                                   ("f2.png", b"\x89PNG"),
                                                   ("f3.tif", b"II*\x00")]),
                                    "application/zip")})

    result = EuropePmcSource(http).fetch(
        _epmc_ids(), need_pdf=False, need_supplements=True)

    assert result.suppl_status is None and result.files == []
    note = [a for a in result.attempts if a["action"] == "supplements"][-1]
    assert note["status"] == "none_text_bearing" and note["not_text_bearing"] == 3


def test_the_oa_package_figures_never_reach_disk_or_the_record():
    """The archive where the filter drops the most: a package carries the article's
    own figure images beside its supplements, so with the policy on `_classify` has
    nothing left to sort into `media/`. Each refused name is still put through the
    same `supplement_or_media` policy, so a figure is recorded as one."""
    http = _oa_http({OA_TGZ: (200, _package(*PACKAGE_MEMBERS), "application/gzip")})

    result = PmcOaSource(http, {"try_oa_package": True}).fetch(
        _pmc_ids(), need_pdf=False, need_supplements=True)

    assert [f.name for f in result.by_role("supplement")] == ["gkr715_supp_table_s1.xlsx"]
    assert result.by_role("media") == []
    assert result.suppl_status == "fetched"
    note = next(a for a in result.attempts if a["action"] == "text_bearing_filter")
    assert note["where"] == "on_unpack" and note["files"] == ["gkr715f1.jpg"]
    assert note["roles"] == {"media": 1}, "an article figure, recorded as one"


def test_fetching_everything_leaves_no_filter_and_no_note():
    """`text_bearing_only: false` has to be this tool's behaviour before the key
    existed -- not merely the same files, but the same requests and no note at all."""
    deposit = [(f"{V1}/NIHMS1758707-supplement-1.jpg", 100)]
    http = _s3_http(deposit=deposit)

    result = PmcS3Source(http, EVERYTHING).fetch(
        _pmc_ids(), need_pdf=False, need_supplements=True)

    assert [f.name for f in result.by_role("supplement")] == \
        ["NIHMS1758707-supplement-1.jpg"]
    assert http.called_matching("supplement-1.jpg") == 1
    assert result.skipped_not_text_bearing == []
    assert not [a for a in result.attempts if a["action"] == "text_bearing_filter"]


# -- Elsevier TDM: the only route to a Cell Press supplement ------------------
#
# The tier exists because Cloudflare serves `proxy_browser` a challenge on
# ScienceDirect and Cell Press, so for a `10.1016` article these files have no other
# automated route at all -- which is why the tests below lean on the *silent* failure
# modes. Every trap here was measured live on 2026-08-26 (see the module docstring of
# `sources/elsevier_tdm.py`), and each one fails by reporting an article as having no
# supplements rather than by raising, which is the shape that survives a test suite.

ELS_DOI = "10.1016/j.cell.2021.11.031"
ELS_KEY = "els-key-that-must-never-be-recorded"
ELS_LISTING = "api.elsevier.com/content/object/doi/"
ELS_OBJECT = "https://api.elsevier.com/content/object/eid/1-s2.0-X-"


def _els_config(**overrides) -> dict:
    config = {"elsevier_api_key": ELS_KEY}
    config.update(overrides)
    return config


def _els_ids(doi: str = ELS_DOI, publisher: str = "Elsevier BV") -> Identifiers:
    return Identifiers(doi=doi, doi_raw=doi, publisher=publisher, has_suppl=True)


def _att(ref="mmc1", filename=None, type="APPLICATION", size=64,
         mimetype="application/octet-stream"):
    """One `view=META` attachment entry, as Elsevier spells it."""
    filename = filename or f"{ref}.xlsx"
    return {
        "ref": ref,
        "filename": filename,
        "type": type,
        "mimetype": mimetype,
        "size": str(size) if size is not None else None,
        "prism:url": ELS_OBJECT + filename,
    }


def _els_meta(*attachments) -> bytes:
    """The envelope, with `attachment` as a list."""
    body = {"attachment-metadata-response": {"attachment": list(attachments)}}
    return json.dumps(body).encode()


def _els_http(*attachments, listing=None, routes=None, bodies=None) -> FakeHttp:
    """The listing plus one object route per attachment, sized to match its `size`.

    Sizes agree by default so that the integrity check is not what a test trips over
    by accident; `bodies` overrides the bytes for one filename, which is how the
    mismatch case is set up.
    """
    base = {ELS_LISTING: listing or (200, _els_meta(*attachments), "application/json")}
    for entry in attachments:
        declared = int(entry["size"]) if entry.get("size") else 8
        payload = (bodies or {}).get(entry["filename"], b"x" * declared)
        base[entry["prism:url"]] = (200, payload, entry["mimetype"])
    base.update(routes or {})
    return FakeHttp(base)


def _els_fetch(*attachments, config=None, need_pdf=False, need_supplements=True,
               ids=None, **http_kwargs):
    http = _els_http(*attachments, **http_kwargs)
    source = ElsevierTdmSource(http, _els_config(**(config or {})))
    result = source.fetch(ids or _els_ids(), need_pdf=need_pdf,
                          need_supplements=need_supplements)
    return http, result


# -- gating -------------------------------------------------------------------

def test_applies_needs_a_key_and_an_elsevier_doi():
    """The key gate is what makes a credentialed tier safe to ship in `OA_TIERS`:
    without one this tier is a no-op, so a user who never registered gets no requests
    and no failed attempts, and `--oa-only` keeps meaning what it says."""
    with_key = ElsevierTdmSource(FakeHttp(), _els_config())
    assert with_key.applies(_els_ids()) is True
    assert with_key.applies(_els_ids(doi=DOI, publisher="Springer Nature")) is False

    assert ElsevierTdmSource(FakeHttp(), {}).applies(_els_ids()) is False
    assert ElsevierTdmSource(FakeHttp(), {"elsevier_api_key": "   "}).applies(
        _els_ids()) is False, "whitespace is not a key; an empty header 401s"


def test_a_publisher_naming_elsevier_applies_where_the_prefix_is_unknown():
    """`ELSEVIER_DOI_PREFIXES` is a measured fast path, not the definition -- Elsevier
    owns prefixes this list does not name, and a missing one should cost a slower
    route rather than the files."""
    source = ElsevierTdmSource(FakeHttp(), _els_config())
    unlisted = _els_ids(doi="10.9999/unknown.prefix", publisher="Elsevier Inc.")
    assert source.applies(unlisted) is True


def test_nothing_needed_costs_no_request():
    """The listing is the only thing this tier can ask for, so asking for it to learn
    nothing is the whole cost of getting this wrong."""
    http, result = _els_fetch(_att(), need_pdf=False, need_supplements=False)
    assert http.calls == []
    assert result.suppl_status is None and result.pdf_status is None


# -- the two shapes that fail silently ---------------------------------------

def test_a_single_attachment_arrives_as_a_dict_not_a_list():
    """Measured live. An article with exactly one attachment returns `attachment` as a
    **dict**, and iterating a dict yields its *keys* -- so the tier builds attachments
    out of the strings "ref" and "filename" and fetches nothing, for precisely the
    articles that have one supplement."""
    only = _att(ref="mmc1", filename="mmc1.xlsx")
    body = json.dumps(
        {"attachment-metadata-response": {"attachment": only}}).encode()
    http = _els_http(only, listing=(200, body, "application/json"))
    result = ElsevierTdmSource(http, _els_config()).fetch(
        _els_ids(), need_pdf=False, need_supplements=True)

    assert [f.name for f in result.by_role("supplement")] == ["mmc1.xlsx"]
    assert result.suppl_status == "fetched"


def test_a_video_supplement_survives_where_a_type_filter_would_drop_it():
    """Trap 1, and the reason the filter is on `ref` rather than `type`. Supplements
    arrive as `VIDEO` and `VIDEO-FLASH` as well as `APPLICATION`, so filtering on
    `type == "APPLICATION"` silently loses all four video supplements of
    10.1016/j.cell.2020.11.028.

    Asserted with the policy off, because a video is only fetched at all in a
    `text_bearing_only: false` run -- the point here is that it was *enumerated*.
    """
    _http, result = _els_fetch(
        _att(ref="mmc1", filename="mmc1.xlsx"),
        _att(ref="mmc2", filename="mmc2.mp4", type="VIDEO"),
        _att(ref="mmc3", filename="mmc3.flv", type="VIDEO-FLASH"),
        config=EVERYTHING,
    )
    assert [f.name for f in result.by_role("supplement")] == ["mmc1.xlsx"]
    assert [f.name for f in result.by_role("media")] == ["mmc2.mp4", "mmc3.flv"], \
        "audio/video is the media role, and it is decided on the extension"


def test_a_video_is_refused_by_policy_and_named_rather_than_dropped():
    """The same two videos under the shipped default. They are refused, and the
    manifest still says what a `text_bearing_only: false` run would have fetched --
    which is the difference between this and the `type` filter above: both come away
    with one file, and only one of them leaves a record."""
    _http, result = _els_fetch(
        _att(ref="mmc1", filename="mmc1.xlsx"),
        _att(ref="mmc2", filename="mmc2.mp4", type="VIDEO"),
    )
    assert [f.name for f in result.by_role("media")] == []
    assert [e["name"] for e in result.skipped_not_text_bearing] == ["mmc2.mp4"]
    filter_notes = [a for a in result.attempts if a.get("action") == "text_bearing_filter"]
    assert filter_notes and filter_notes[0]["files"] == ["mmc2.mp4"]
    assert result.suppl_status == "fetched", \
        "a policy refusal is not a loss and must not demote the verdict"


def test_the_video_poster_frames_are_excluded_and_recorded():
    """`IMAGE-MMC-THUMBNAIL` and `IMAGE-MMC-DOWNSAMPLED` carry `mmc` refs, so the
    Trap 1 filter would take them -- and they are derived renditions of a video, not
    supplements the article has. Counting them would inflate the article's supplement
    count. Excluded, but *recorded*: nothing this tool drops is silent.

    Asserted with the policy off, which is the run where it matters -- with it on
    these would be refused as images anyway.
    """
    _http, result = _els_fetch(
        _att(ref="mmc1", filename="mmc1.mp4", type="VIDEO"),
        _att(ref="mmc1", filename="mmc1.jpg", type="IMAGE-MMC-THUMBNAIL"),
        _att(ref="mmc1", filename="mmc1-small.jpg", type="IMAGE-MMC-DOWNSAMPLED"),
        config=EVERYTHING,
    )
    assert [f.name for f in result.files] == ["mmc1.mp4"]
    derived = [a for a in result.attempts
               if a.get("action_detail") == "derived_rendition"]
    assert derived and derived[0]["skipped"] == 2
    assert sorted(derived[0]["files"]) == ["mmc1-small.jpg", "mmc1.jpg"]


# -- the free integrity check --------------------------------------------------

def test_a_declared_size_mismatch_is_never_written_to_disk():
    """The listing declares `size` and it matched the bytes received on every file the
    probe measured, so a truncated transfer is detectable here for free -- something
    `pmc_s3` cannot do, because S3's `<Size>` is sometimes absent. A short body must
    not reach the corpus: it would look like a valid spreadsheet."""
    entry = _att(ref="mmc1", filename="mmc1.xlsx", size=1024)
    _http, result = _els_fetch(entry, bodies={"mmc1.xlsx": b"truncated"})

    assert result.files == []
    mismatch = [a for a in result.attempts if a.get("status") == "size_mismatch"]
    assert mismatch and mismatch[0]["declared"] == 1024
    assert mismatch[0]["received"] == len(b"truncated")
    assert any("declared 1024 bytes and sent 9" in p for p in result.problems)


def test_an_oversize_attachment_is_refused_without_being_downloaded():
    """The pre-check the listing pays for: `size` is known before the request, so the
    cap costs no transfer at all."""
    entry = _att(ref="mmc1", filename="mmc1.xlsx", size=300 * 1024 * 1024)
    http, result = _els_fetch(entry, config={"max_file_mb": 200})

    assert http.called_matching("mmc1.xlsx") == 0, "nothing may be transferred"
    assert result.files == []
    assert any("exceeds the 200 MB cap" in p for p in result.problems)


# -- the cap, and the ordering the codebase requires pinned per tier ----------

def test_an_elsevier_refused_figure_never_spends_a_cap_slot_a_table_needed():
    """`keep_text_bearing` before `apply_files_cap`, always. Its docstring asks each
    tier to pin this rather than trust the sentence, and the measurement behind the
    rule is `pmc_s3`'s: eight figures took cap slots from eight supplementary tables.

    Both files here are the *supplement* role -- a `.jpg` is an image, not audio or
    video -- so they compete for the same one slot, and the wrong order loses the
    spreadsheet to a figure that would then be refused anyway.
    """
    http, result = _els_fetch(
        _att(ref="mmc1", filename="mmc1.jpg", type="IMAGE"),
        _att(ref="mmc2", filename="mmc2.xlsx"),
        config={"max_files": 1},
    )
    assert [f.name for f in result.by_role("supplement")] == ["mmc2.xlsx"]
    assert http.called_matching("mmc1.jpg") == 0
    assert not any(a.get("action") == "cap" for a in result.attempts), \
        "the figure left by policy, so the cap was never reached"


def test_the_cap_counts_files_not_links():
    """"file", not "link": the API enumerated these, so a dropped one is a known file
    rather than an anchor that may not have been distinct -- the division
    `apply_files_cap` keeps a parameter for."""
    _http, result = _els_fetch(
        _att(ref="mmc1", filename="mmc1.xlsx"),
        _att(ref="mmc2", filename="mmc2.xlsx"),
        config={"max_files": 1},
    )
    assert any("supplementary file(s) not fetched" in p for p in result.problems)
    cap = [a for a in result.attempts if a.get("action") == "cap"]
    assert cap and cap[0]["via"] == "object_api" and cap[0]["dropped"] == 1
    assert result.suppl_status == "fetched_unverified", \
        "the cap stopped us short of a file the listing did name"


# -- what the statuses claim ---------------------------------------------------

def test_the_publishers_own_index_earns_fetched():
    """`fetched`, not `fetched_unverified`. The argument is `fetcher`'s own for
    `pmc_s3`: this is the deposit's index served by the party holding the bytes, not a
    regex over a rendered page -- and here that party is the publisher."""
    _http, result = _els_fetch(
        _att(ref="mmc1", filename="mmc1.xlsx"),
        _att(ref="mmc2", filename="mmc2.docx"),
    )
    assert result.suppl_status == "fetched"
    assert len(result.by_role("supplement")) == 2


def test_an_empty_attachment_list_is_the_publisher_saying_none():
    """Measured: 10.1016/j.coi.2022.102188, a Current Opinion review, genuinely has
    none and the hand-fetched corpus entry has none either. Elsevier is the publisher,
    so its own empty attachment list is `none_listed` by definition.

    Settled, and safe to claim: `fetcher`'s loop clears `need_supplements` only when
    files arrived or every named file was policy-refused, so this does not stop
    `pmc_supplements` or the browser tier from having their turn.
    """
    _http, result = _els_fetch()
    assert result.suppl_status == "none_listed"


def test_a_body_with_no_attachment_list_never_claims_none_listed():
    """The guard that separates "the publisher says none" from "this code could not
    read the response". Claiming the settled `none_listed` over an unparsed body is
    how a shape change at Elsevier would quietly empty the Elsevier half of a corpus
    -- so an unreadable envelope records its keys and claims nothing."""
    body = json.dumps({"service-error": {"status": {"statusText": "x"}}}).encode()
    http = _els_http(listing=(200, body, "application/json"))
    result = ElsevierTdmSource(http, _els_config()).fetch(
        _els_ids(), need_pdf=False, need_supplements=True)

    assert result.suppl_status is None, "no claim on a body we could not read"
    shape = [a for a in result.attempts if a.get("status") == "payload_shape"]
    assert shape and shape[0]["keys"] == ["service-error"], \
        "record the keys, so a live run can name the real envelope"


@pytest.mark.parametrize("status,expected", [
    (401, "auth_failed"),
    (403, "not_entitled"),
    (404, "no_object_record"),
    (429, "rate_limited"),
])
def test_the_client_errors_stay_four_different_answers(status, expected):
    """Four codes, four operator actions: the key is wrong, the key is real but
    unlicensed, Elsevier has no object record for this DOI, and the unpublished quota
    is reached. Folding them into one `download_failed` makes the only one a user can
    fix indistinguishable from the three they cannot.

    None of them claims a supplement status: a 404 in particular is not an
    authoritative "no supplements", so the later tiers keep their turn.
    """
    http = _els_http(listing=(status, b"", "application/json"))
    result = ElsevierTdmSource(http, _els_config()).fetch(
        _els_ids(), need_pdf=False, need_supplements=True)

    assert [a["status"] for a in result.attempts] == [expected]
    assert result.suppl_status is None


def test_a_failed_download_is_a_partial_failure_not_an_empty_deposit():
    entry = _att(ref="mmc1", filename="mmc1.xlsx")
    http = _els_http(entry, routes={entry["prism:url"]: (500, b"", "text/plain")})
    result = ElsevierTdmSource(http, _els_config()).fetch(
        _els_ids(), need_pdf=False, need_supplements=True)

    assert result.suppl_status == "partial_failure"
    assert any("could not be fetched" in p for p in result.problems)


# -- the accepted author manuscript ------------------------------------------

def test_the_author_manuscript_is_accepted_as_the_article_pdf():
    """`type == "AAM-PDF"`, `ref == "am"`, present on 8 of the 14 sampled articles.
    It is the accepted manuscript rather than the typeset version of record, which is
    recorded in the attempt -- but it is article PDF that Cloudflare otherwise puts
    out of reach entirely, and for text extraction the difference is cosmetic."""
    aam = _att(ref="am", filename="am.pdf", type="AAM-PDF", size=None,
               mimetype="application/pdf")
    http = _els_http(aam, bodies={"am.pdf": make_pdf()})
    result = ElsevierTdmSource(http, _els_config()).fetch(
        _els_ids(), need_pdf=True, need_supplements=False)

    assert [f.name for f in result.by_role("fulltext_pdf")] == ["fulltext.pdf"]
    rendition = [a for a in result.attempts if a.get("action") == "author_manuscript"]
    assert rendition and rendition[0]["rendition"] == "accepted_author_manuscript", \
        "the manifest has to say which rendition this is"


def test_the_author_manuscript_is_not_counted_as_a_supplement():
    """`ref == "am"` does not begin `mmc`, so it must not reach the supplement list --
    otherwise every article offering an AAM reports one extra supplement it does not
    have, and an article with only an AAM stops reading as `none_listed`."""
    aam = _att(ref="am", filename="am.pdf", type="AAM-PDF", size=None,
               mimetype="application/pdf")
    http = _els_http(aam, bodies={"am.pdf": make_pdf()})
    result = ElsevierTdmSource(http, _els_config()).fetch(
        _els_ids(), need_pdf=False, need_supplements=True)

    assert result.by_role("supplement") == []
    assert result.suppl_status == "none_listed"


# -- secret hygiene: the argument `Http.get`'s `headers` exists for -----------

def test_the_key_travels_as_a_header_on_every_request():
    entry = _att(ref="mmc1", filename="mmc1.xlsx")
    http, _result = _els_fetch(entry)

    assert len(http.calls) == 2, "one listing, one object"
    for sent in http.headers:
        assert sent["X-ELS-APIKey"] == ELS_KEY


def test_the_key_reaches_no_recorded_url_attempt_or_problem():
    """The test that makes `Http.get`'s design decision enforceable rather than
    merely argued. Elsevier accepts `apiKey` as a query parameter, and every tier
    records the URL it asked for into `corpus/*/manifest.json` -- so the query-string
    spelling would copy this secret onto disk once per Elsevier article, recoverable
    only by rewriting every manifest.

    Asserted over everything that gets persisted, including the params the tier sent,
    because a key added to `params` would never show up in a `url` field.
    """
    aam = _att(ref="am", filename="am.pdf", type="AAM-PDF", size=None,
               mimetype="application/pdf")
    entry = _att(ref="mmc1", filename="mmc1.xlsx")
    http = _els_http(entry, aam, bodies={"am.pdf": make_pdf()})
    result = ElsevierTdmSource(http, _els_config()).fetch(
        _els_ids(), need_pdf=True, need_supplements=True)

    recorded = json.dumps({"attempts": result.attempts,
                           "problems": result.problems,
                           "files": [f.url for f in result.files]})
    assert ELS_KEY not in recorded, "the key must never reach a manifest"
    assert not any(ELS_KEY in url for url in http.calls), "nor a requested URL"
    assert not any(ELS_KEY in json.dumps(p) for p in http.params), \
        "nor the query string, which is the spelling Http.get refused"


def test_the_environment_overrides_a_config_file_key():
    """`config.yaml` is tracked in git and ships `elsevier_api_key: null`, so a
    file-wins rule would let that committed null blank out a real key on every run --
    and it would surface as a 401 from Elsevier rather than as a precedence bug."""
    from_file = {"elsevier_api_key": None, "corpus_dir": "corpus"}
    with mock.patch.dict(os.environ,
                         {"MANUSCRIPT_HARVEST_ELSEVIER_API_KEY": ELS_KEY}):
        merged = _with_env_credentials(from_file)
    assert merged["elsevier_api_key"] == ELS_KEY
    assert from_file["elsevier_api_key"] is None, \
        "a copy, never a mutation: callers reuse one config across a batch"

    with mock.patch.dict(os.environ, {}, clear=True):
        assert _with_env_credentials({"elsevier_api_key": "from-file"})[
            "elsevier_api_key"] == "from-file"


# -- placement -----------------------------------------------------------------

def test_elsevier_tdm_is_tried_before_the_proof_of_work_wall():
    """The second placement in `OA_TIERS` that is an argument rather than an
    accident. `pmc_supplements` walks into PMC's proof-of-work page and then 403s, so
    a tier that can settle the supplements without it goes first -- and this one is
    the *only* automated route to a Cell Press supplement, because Cloudflare
    challenges the browser tier on those hosts.

    It is in `OA_TIERS` despite needing a credential: `--oa-only` promises "never
    open a browser", and `ncbi_api_key` is already an optional key two of these tiers
    send. `applies` returns False without a key, so every tier here still works with
    no credentials at all.
    """
    assert OA_TIERS.index("elsevier_tdm") < OA_TIERS.index("pmc_supplements")
    assert "elsevier_tdm" in OA_TIERS, \
        "a browserless tier belongs in the --oa-only set; see sources/__init__"

    shipped = yaml.safe_load(
        (Path(__file__).resolve().parent.parent / "config.yaml").read_text())
    assert shipped["fetch"]["tiers"] == list(DEFAULT_TIERS)
    assert "elsevier_api_key" in shipped["fetch"], \
        "the key has to be declared, or applies() cannot find it absent"
    assert shipped["fetch"]["min_interval_overrides"].get("api.elsevier.com"), \
        "one request per attachment against an API documented at 10 req/s"


def test_the_live_envelope_and_field_names_are_the_ones_the_tier_reads():
    """The shape as measured through this tier against 10.1016/j.cell.2021.11.031 --
    `coredata` beside `attachment`, and not one of the fields `@`-prefixed.

    This is what let the two-spelling tolerance come out of `_field`. It is pinned
    here because the failure it guards is silent: a renamed field yields `""` for
    every `ref`, and the tier then reports every Elsevier article as having no
    supplements rather than raising.
    """
    body = json.dumps({"attachment-metadata-response": {
        "coredata": {"prism:doi": ELS_DOI},
        "attachment": [{
            "@_fa": "true",
            "eid": "1-s2.0-S0092867421013246-mmc1.xlsx",
            "ref": "mmc1", "filename": "mmc1.xlsx", "type": "APPLICATION",
            "mimetype": "application/excel", "size": "8",
            "prism:url": ELS_OBJECT + "mmc1.xlsx",
        }],
    }}).encode()
    http = FakeHttp({
        ELS_LISTING: (200, body, "application/json"),
        ELS_OBJECT + "mmc1.xlsx": (200, b"x" * 8, "application/excel"),
    })
    result = ElsevierTdmSource(http, _els_config()).fetch(
        _els_ids(), need_pdf=False, need_supplements=True)

    assert result.suppl_status == "fetched"
    stored = result.by_role("supplement")
    assert [f.name for f in stored] == ["mmc1.xlsx"]
    assert stored[0].content_type == "application/excel", "mimetype is read, not guessed"


def test_the_article_figure_renditions_are_left_on_the_wire():
    """Measured: 52 of the 59 attachments on 10.1016/j.cell.2021.11.031 are article
    figures in three renditions plus inline equations -- ~38 MB of JPEG. None of them
    carries an `mmc` ref, so the `ref` filter excludes them before a request is spent.

    Pinned because the cost of getting this wrong is not a wrong answer but a silent
    one: ~60 requests and tens of megabytes per article, for files no text comes out
    of. Asserted with the policy *off*, so it is the `ref` filter being tested here
    and not `text_bearing_only` doing the work.
    """
    _http, result = _els_fetch(
        _att(ref="gr1", filename="gr1_lrg.jpg", type="IMAGE-HIGH-RES"),
        _att(ref="figs1", filename="figs1.sml", type="IMAGE-THUMBNAIL"),
        _att(ref="fx1", filename="fx1.jpg", type="IMAGE-DOWNSAMPLED"),
        _att(ref="si1", filename="si1.gif", type="ALTIMG"),
        _att(ref="mmc1", filename="mmc1.xlsx"),
        config=EVERYTHING,
    )
    assert [f.name for f in result.files] == ["mmc1.xlsx"], \
        "only the mmc ref is supplementary material"
    assert result.suppl_status == "fetched"


def test_an_article_holding_only_figures_is_none_listed_and_this_is_the_risk():
    """A response listing 52 figure renditions and no `mmc` attachment is the normal
    shape of an Elsevier *review*, and it reads as `none_listed`.

    **This diverges from `pmc_s3`, which leaves the same case unset, and the
    divergence is deliberate.** That tier's objection is that
    `supplement_or_media` is a *filename heuristic*, so calling its silence
    `none_listed` would promote a guess into a statement about what the publisher
    deposited. Here the filter reads `ref`, which is Elsevier's own field -- the same
    one Trap 1 says to trust over `type` -- and Elsevier is the publisher, so the
    objection does not transfer.

    **The consequence a reader has to know: `none_listed` sits directly above the
    `hasSuppl` alarm in `_supplement_status`, so this suppresses
    `expected_but_missing`.** That is the intended direction. `hasSuppl` comes from
    Europe PMC's index, which for an Elsevier article is frequently a metadata-only
    record (`inEPMC=N`) -- the least reliable source in play -- and letting the index
    override the publisher's own attachment list would be backwards. It is the case
    the `none_listed` precedence was written for: "a source that owns the content can
    state authoritatively that there are none, even when the index disagrees."

    The exposure, stated plainly: if Elsevier ever spells a supplement with a ref
    that does not begin `mmc`, this suppresses the alarm for that article. Two
    articles measured live (7 and 6 supplements) used `mmc` throughout. The guard
    that keeps this honest is that the claim needs a *readable* attachment list --
    an unparsed body claims nothing, which
    `test_a_body_with_no_attachment_list_never_claims_none_listed` pins.
    """
    _http, result = _els_fetch(
        _att(ref="gr1", filename="gr1_lrg.jpg", type="IMAGE-HIGH-RES"),
        config=EVERYTHING,
    )
    assert result.files == []
    assert result.suppl_status == "none_listed", \
        "the publisher's own list held no multimedia component"


# -- the error paths, each of which has to stay distinguishable ---------------

def test_a_transport_failure_on_the_listing_claims_nothing():
    """A listing that never completed has learned nothing about either artifact, so
    both statuses stay None and the later tiers keep their turn."""
    class Exploding:
        def get(self, url, params=None, accept=None, allow_redirects=True,
                headers=None):
            raise HttpError("boom")

    result = ElsevierTdmSource(Exploding(), _els_config()).fetch(
        _els_ids(), need_pdf=False, need_supplements=True)

    assert result.suppl_status is None and result.pdf_status is None
    assert [a["status"] for a in result.attempts] == ["request_failed"]
    assert any("boom" in p for p in result.problems)


def test_a_server_error_on_the_listing_is_its_own_status():
    """5xx is not one of the four client errors and must not borrow one of their
    words: `auth_failed` would send someone to dev.elsevier.com over an Elsevier
    outage."""
    http = _els_http(listing=(500, b"", "text/plain"))
    result = ElsevierTdmSource(http, _els_config()).fetch(
        _els_ids(), need_pdf=False, need_supplements=True)

    assert [a["status"] for a in result.attempts] == ["http_error"]
    assert result.suppl_status is None
    assert any("HTTP 500" in p for p in result.problems)


def test_a_listing_body_that_is_not_json_claims_nothing():
    http = _els_http(listing=(200, b"<html>not json</html>", "text/html"))
    result = ElsevierTdmSource(http, _els_config()).fetch(
        _els_ids(), need_pdf=False, need_supplements=True)

    assert [a["status"] for a in result.attempts] == ["unreadable_payload"]
    assert result.suppl_status is None


@pytest.mark.parametrize("payload,why", [
    ([], "a JSON list, not an object"),
    ({"attachment-metadata-response": "text"}, "the envelope is not an object"),
    ({"attachment-metadata-response": {"coredata": {}}}, "no attachment key"),
    ({"attachment-metadata-response": {"attachment": "mmc1"}},
     "attachment is neither a dict nor a list"),
])
def test_no_shape_but_the_measured_one_yields_an_attachment_list(payload, why):
    """`_attachment_entries` returns None for anything it does not recognise, and None
    is what stops `none_listed` being claimed. Each of these would otherwise be a way
    to read zero attachments and call it "the publisher says none" -- see
    `test_a_body_with_no_attachment_list_never_claims_none_listed` for why that
    distinction is the one that matters."""
    from manuscript_harvest.fetch.sources.elsevier_tdm import _attachment_entries
    assert _attachment_entries(payload) is None, why


def test_a_transport_failure_on_one_file_loses_only_that_file():
    entry_ok = _att(ref="mmc1", filename="mmc1.xlsx")
    entry_bad = _att(ref="mmc2", filename="mmc2.xlsx")

    class OneExplodes(FakeHttp):
        def get(self, url, params=None, accept=None, allow_redirects=True,
                headers=None):
            if "mmc2.xlsx" in url:
                raise HttpError("connection reset")
            return super().get(url, params, accept, allow_redirects, headers)

    http = OneExplodes({
        ELS_LISTING: (200, _els_meta(entry_ok, entry_bad), "application/json"),
        entry_ok["prism:url"]: (200, b"x" * 64, "application/octet-stream"),
    })
    result = ElsevierTdmSource(http, _els_config()).fetch(
        _els_ids(), need_pdf=False, need_supplements=True)

    assert [f.name for f in result.by_role("supplement")] == ["mmc1.xlsx"]
    assert result.suppl_status == "partial_failure", \
        "one file kept and one lost is exactly what partial_failure names"
    failed = [a for a in result.attempts if a.get("status") == "request_failed"]
    assert failed and failed[0]["ref"] == "mmc2"


def test_the_cap_goes_to_readable_files_before_supplementary_video():
    """`max_files` is a request budget, and a video cannot be read -- so when the cap
    binds, the spreadsheets get it and the dropped videos are reported under their own
    wording rather than `apply_files_cap`'s "supplementary file(s)", whose count
    would then disagree with its noun.

    Needs the policy off: with it on the videos are refused before the cap is reached.
    """
    _http, result = _els_fetch(
        _att(ref="mmc1", filename="mmc1.xlsx"),
        _att(ref="mmc2", filename="mmc2.mp4", type="VIDEO"),
        _att(ref="mmc3", filename="mmc3.mov", type="VIDEO"),
        config={"max_files": 1, "text_bearing_only": False},
    )
    assert [f.name for f in result.by_role("supplement")] == ["mmc1.xlsx"]
    assert result.by_role("media") == [], "no slots left after the readable file"
    truncated = [a for a in result.attempts
                 if a.get("status") == "truncated_media"]
    assert truncated and truncated[0]["dropped"] == 2
    assert any("supplementary video(s) not fetched" in p for p in result.problems)
