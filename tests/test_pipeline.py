"""Tier orchestration end to end, with fake HTTP and no browser.

The status taxonomy is what these tests defend. An empty result and a failed one
look identical downstream unless something names them apart, and the trap here is
an empty `supplementary/` directory. Every assertion about a status is really an
assertion that the pipeline does not lie about what it got.
"""


import pytest

from manuscript_harvest.fetch import fetcher, store
from manuscript_harvest.fetch.fetcher import _best_pdf_status, _supplement_status, suppl_flag_is_authoritative
from manuscript_harvest.fetch.identifiers import Identifiers
from tests.fakes import (
    DOI,
    EUROPEPMC_EMPTY,
    OA_XML_ERROR,
    PAYWALL_HTML,
    PMCID,
    FakeHttp,
    crossref_json,
    europepmc_search_json,
    fetch_config,
    make_pdf,
    make_zip,
)

SEARCH = "/webservices/rest/search"
SUPPL = "/supplementaryFiles"
XML = "/fullTextXML"
PDF_URL = "example.org/article.pdf"


def _http(routes=None, **search_overrides):
    base = {
        SEARCH: (200, europepmc_search_json(**search_overrides), "application/json"),
        PDF_URL: (200, make_pdf(), "application/pdf"),
        XML: (404, b"", ""),
    }
    base.update(routes or {})
    return FakeHttp(base)


# -- happy path --------------------------------------------------------------

def test_complete_fetch_writes_everything_and_records_provenance(tmp_path):
    http = _http({
        XML: (200, b"<article><body/></article>", "application/xml"),
        SUPPL: (200, make_zip([("a_MOESM1_ESM.pdf", b"%PDF one"),
                               ("a_MOESM2_ESM.xlsx", b"xlsx two")]), "application/zip"),
    })
    record = fetcher.fetch_publication(DOI, fetch_config(tmp_path, ["europepmc"]), http=http)

    assert record["status"] == "complete"
    assert record["fulltext"]["status"] == "ok"
    assert record["supplementary_status"] == "fetched"
    assert len(record["supplementary"]) == 2
    assert record["identifiers"]["pmcid"] == PMCID

    directory = tmp_path / store.doi_slug(DOI)
    assert (directory / "fulltext.pdf").exists()
    assert (directory / "fulltext.nxml").exists()
    assert sorted(p.name for p in (directory / "supplementary").iterdir()) == [
        "01_a_MOESM1_ESM.pdf", "02_a_MOESM2_ESM.xlsx"]

    on_disk = store.read_manifest(directory)
    assert on_disk["fulltext"]["tier"] == "europepmc"
    assert on_disk["fulltext"]["sha256"] and on_disk["supplementary"][0]["sha256"]


def test_second_fetch_is_cached_and_force_overrides(tmp_path):
    http = _http({SUPPL: (200, make_zip([("a.pdf", b"%PDF")]), "application/zip")})
    config = fetch_config(tmp_path, ["europepmc"])
    fetcher.fetch_publication(DOI, config, http=http)

    calls = len(http.calls)
    again = fetcher.fetch_publication(DOI, config, http=http)
    assert again.get("cached") is True
    assert len(http.calls) == calls, "a cached fetch must not touch the network"

    forced = fetcher.fetch_publication(DOI, config, force=True, http=http)
    assert not forced.get("cached") and len(http.calls) > calls


# -- the supplement taxonomy -------------------------------------------------

def test_hassuppl_yes_with_nothing_retrieved_is_the_bug_case(tmp_path):
    """Never `none_listed`: the publisher says the files exist."""
    http = _http({SUPPL: (404, b"", "")})
    record = fetcher.fetch_publication(DOI, fetch_config(tmp_path, ["europepmc"]), http=http)
    assert record["supplementary_status"] == "expected_but_missing"
    assert record["status"] == "partial"


def test_hassuppl_no_is_believed_only_when_pmc_holds_the_article(tmp_path):
    http = _http(hasSuppl="N")
    record = fetcher.fetch_publication(DOI, fetch_config(tmp_path, ["europepmc"]), http=http)
    assert record["supplementary_status"] == "none_listed"
    assert record["status"] == "complete"
    assert http.called_matching(SUPPL) == 0, "must not probe when the flag is trusted"


def test_hassuppl_no_is_not_believed_for_an_unheld_article(tmp_path):
    """Measured on 10.1016/j.stem.2023.12.013 and 10.1038/s41591-018-0269-2:
    inEPMC=N, inPMC=N, hasSuppl=N says only that Europe PMC has nothing. The
    latter turned out to have 3 supplements."""
    http = _http(hasSuppl="N", inEPMC="N", inPMC="N")
    record = fetcher.fetch_publication(DOI, fetch_config(tmp_path, ["europepmc"]), http=http)
    assert record["supplementary_status"] != "none_listed"
    assert http.called_matching(SUPPL) >= 1, "must still look"


def test_preprint_flag_is_never_believed(tmp_path):
    """Europe PMC reports hasSuppl=N for 10.1101/2025.07.21.666016, which has
    media-1.pdf and media-2.zip (72 MB together)."""
    preprint = "10.1101/2025.07.21.666016"
    http = FakeHttp({
        SEARCH: (200, europepmc_search_json(doi=preprint, source="PPR", hasSuppl="N",
                                            pmcid=None, inPMC="N"), "application/json"),
        PDF_URL: (200, make_pdf(), "application/pdf"),
    })
    record = fetcher.fetch_publication(preprint, fetch_config(tmp_path, ["europepmc"]), http=http)
    assert record["supplementary_status"] != "none_listed"


def test_no_metadata_anywhere_never_claims_none(tmp_path):
    http = FakeHttp({SEARCH: (200, EUROPEPMC_EMPTY, "application/json"),
                     "api.crossref.org": (200, crossref_json(), "application/json")})
    record = fetcher.fetch_publication(DOI, fetch_config(tmp_path, ["europepmc"]), http=http)
    assert record["supplementary_status"] == "unknown_none_found"
    assert record["status"] == "failed"


@pytest.mark.parametrize("reported,collected,expected", [
    (["partial_failure", "fetched"], 4, "fetched"),      # a later tier rescued it
    (["page_not_parsed", "fetched"], 4, "fetched"),
    (["partial_failure"], 2, "partial_failure"),
    (["partial_failure"], 0, "expected_but_missing"),
    (["none_listed"], 0, "none_listed"),                 # the source owns the content
    # An unpacked deposit archive outranks a scrape: it is the stronger evidence,
    # not merely the better news. Whichever order the tiers ran in.
    (["fetched_unverified", "fetched"], 4, "fetched"),
    (["fetched", "fetched_unverified"], 4, "fetched"),
    # A scrape alone can never earn plain `fetched`.
    (["fetched_unverified"], 12, "fetched_unverified"),
    (["partial_failure", "fetched_unverified"], 4, "fetched_unverified"),
])
def test_supplement_status_precedence(reported, collected, expected):
    ids = Identifiers(doi=DOI, doi_raw=DOI, has_suppl=True, in_pmc=True)
    assert _supplement_status(ids, True, collected, reported) == expected


def test_losing_every_listed_file_is_not_the_same_as_nobody_looking():
    """Reported on 10.1016/j.oraloncology.2021.105348, whose row read
    `suppl=unknown_none_found files=0`. A tier that listed supplement links and came
    away with none of them *looked*; `unknown_none_found` means nobody did. Reporting
    both the same way is the exact ambiguity this taxonomy exists to prevent.

    Not expressible in `test_supplement_status_precedence` above: that parametrization
    hardcodes `has_suppl=True, in_pmc=True`, and `expected_but_missing` correctly wins
    for a paper the publisher says has supplements. This is the case where the index
    knows nothing.
    """
    ids = Identifiers(doi=DOI, doi_raw=DOI)          # has_suppl unknown
    assert _supplement_status(ids, True, 0, ["partial_failure"]) == "partial_failure"
    assert _supplement_status(ids, True, 0, []) == "unknown_none_found"


def test_a_publisher_that_says_files_exist_still_outranks_partial_failure():
    """Why the check sits where it does. `expected_but_missing` is the stronger claim
    -- it says the publisher's own metadata contradicts our empty result -- so a tier
    reporting `partial_failure` must not demote it."""
    ids = Identifiers(doi=DOI, doi_raw=DOI, has_suppl=True, in_pmc=True)
    assert _supplement_status(ids, True, 0, ["partial_failure"]) == "expected_but_missing"


def test_a_source_that_owns_the_content_still_outranks_partial_failure():
    """bioRxiv reporting `none_listed` for its own preprint is authoritative, and a
    second tier failing to scrape the same paper does not overturn it."""
    ids = Identifiers(doi="10.1101/2022.01.02.474723", doi_raw="x")
    assert _supplement_status(
        ids, True, 0, ["none_listed", "partial_failure"]) == "none_listed"


def test_partial_failure_does_not_make_a_record_look_complete():
    """It must stay outside `SUPPL_SETTLED`, or a paper that lost every supplement
    would never be re-tried."""
    assert "partial_failure" not in store.SUPPL_SETTLED


def test_unverified_is_settled_so_batches_do_not_thrash(tmp_path):
    """`fetched_unverified` must count as settled, or every batch re-downloads.

    The set is unbounded, not incomplete, and a re-run would scrape the same page
    and reach the same answer. Leaving it out of `store.SUPPL_SETTLED` would make
    the article never reach `complete`, so `manifest_is_complete` would be False
    forever and each batch would re-fetch it and thrash against the size budget --
    the same trap `evicted` exists to avoid.
    """
    assert "fetched_unverified" in store.SUPPL_SETTLED
    (tmp_path / "fulltext.pdf").write_bytes(b"%PDF")
    record = {
        "_directory": str(tmp_path),
        "fulltext": {"status": "ok", "path": "fulltext.pdf"},
        "supplementary": [],
        "supplementary_status": "fetched_unverified",
    }
    store.finalize_status(record)
    assert record["status"] == "complete"
    assert store.manifest_is_complete(record) is True

    # And it is genuinely a distinct claim, not an alias.
    assert "fetched_unverified" != "fetched"


def test_suppl_flag_authority_matrix():
    def ids(**kw):
        return Identifiers(doi=kw.pop("doi", DOI), doi_raw="x", **kw)
    assert suppl_flag_is_authoritative(ids(has_suppl=False, in_pmc=True)) is True
    assert suppl_flag_is_authoritative(ids(has_suppl=False, in_epmc=True)) is True
    assert suppl_flag_is_authoritative(ids(has_suppl=False)) is False        # not held
    assert suppl_flag_is_authoritative(ids(has_suppl=None, in_pmc=True)) is False
    assert suppl_flag_is_authoritative(ids(has_suppl=True, in_pmc=True)) is False
    assert suppl_flag_is_authoritative(
        ids(doi="10.1101/x", has_suppl=False, in_pmc=True)) is False          # preprint


# -- the PDF taxonomy --------------------------------------------------------

def test_paywall_response_is_never_written_as_fulltext(tmp_path):
    http = _http({PDF_URL: (200, PAYWALL_HTML * 20, "application/pdf")}, hasSuppl="N")
    record = fetcher.fetch_publication(DOI, fetch_config(tmp_path, ["europepmc"]), http=http)
    assert record["fulltext"]["status"] == "paywalled"
    assert record["fulltext"]["path"] is None
    assert not (tmp_path / store.doi_slug(DOI) / "fulltext.pdf").exists()


@pytest.mark.parametrize("reported,expected", [
    (["download_failed", "not_in_oa_subset", "not_a_pdf"], "not_a_pdf"),  # last real attempt
    (["download_failed", "not_in_oa_subset"], "not_in_oa_subset"),
    (["not_a_pdf", "paywalled"], "paywalled"),                            # diagnosis wins
    (["not_in_oa_subset", "publisher_stub_page"], "publisher_stub_page"),
    # Tier order is configurable (`--tiers`), so the browser tier is not always
    # the last to speak. 10.1016/j.xgen.2026.101304's resolver error names the
    # cause -- the proxy sent us to a platform that does not carry this journal
    # -- and must still beat a later tier's generic miss.
    (["link_resolver_error", "not_in_oa_subset"], "link_resolver_error"),
    (["not_in_oa_subset", "ok"], "ok"),
    ([], "not_found"),
])
def test_pdf_status_prefers_the_last_real_attempt(reported, expected):
    """A static ranking made 10.1002/path.5751 report `not_in_oa_subset` when the
    real cause was Wiley serving an HTML viewer."""
    assert _best_pdf_status(reported) == expected


def test_not_in_oa_subset_is_recorded_and_fallen_through(tmp_path):
    http = _http({"oa.fcgi": (200, OA_XML_ERROR, "application/xml"),
                  PDF_URL: (404, b"", "")}, hasPDF="N", hasSuppl="N")
    record = fetcher.fetch_publication(
        DOI, fetch_config(tmp_path, ["europepmc", "pmc_oa"]), http=http)
    assert "pmc_oa" in record["tiers_tried"]
    assert any("not in the PMC Open Access subset" in p for p in record["problems"])


# -- caps, dedup, resilience -------------------------------------------------

def test_max_files_cap_is_recorded(tmp_path):
    members = [(f"supp_{i:02d}.xlsx", f"file {i}".encode()) for i in range(12)]
    http = _http({SUPPL: (200, make_zip(members), "application/zip")})
    config = fetch_config(tmp_path, ["europepmc"], max_files=5)
    record = fetcher.fetch_publication(DOI, config, http=http)
    assert len(record["supplementary"]) == 5
    assert any(a.get("action") == "supplements" and a.get("count") == 5
               for a in record["attempts"])


def test_dedup_on_bytes_and_name(tmp_path):
    """Same bytes under a different name is a different file: dropping it would be
    the silent loss this pipeline exists to prevent."""
    archive = make_zip([("shared.xlsx", b"identical"), ("shared.xlsx", b"identical"),
                        ("other.xlsx", b"identical")])
    http = _http({SUPPL: (200, archive, "application/zip")})
    record = fetcher.fetch_publication(DOI, fetch_config(tmp_path, ["europepmc"]), http=http)
    assert sorted(e["original_name"] for e in record["supplementary"]) == \
        ["other.xlsx", "shared.xlsx"]


def test_a_raising_tier_is_recorded_not_fatal(tmp_path, monkeypatch):
    from manuscript_harvest.fetch.sources.europepmc import EuropePmcSource

    def explode(self, ids, need_pdf, need_supplements):
        raise RuntimeError("tier exploded")

    monkeypatch.setattr(EuropePmcSource, "fetch", explode)
    record = fetcher.fetch_publication(DOI, fetch_config(tmp_path, ["europepmc"]), http=_http())
    assert record["status"] == "failed"
    assert any("tier europepmc raised RuntimeError" in p for p in record["problems"])


def test_versioned_doi_falls_back_but_keeps_the_requested_slug(tmp_path):
    versioned = "10.7554/elife.104978.2"

    class VersionAware(FakeHttp):
        def get(self, url, params=None, accept=None, allow_redirects=True):
            if SEARCH in url and "104978.2" in (params or {}).get("query", ""):
                self.calls.append(url)
                from manuscript_harvest.fetch.http import Response
                return Response(url=url, status=200, content=EUROPEPMC_EMPTY,
                                content_type="application/json")
            return super().get(url, params, accept, allow_redirects)

    http = VersionAware({
        SEARCH: (200, europepmc_search_json(doi="10.7554/elife.104978", hasSuppl="N"),
                 "application/json"),
        PDF_URL: (200, make_pdf(), "application/pdf"),
        XML: (404, b"", ""),
    })
    record = fetcher.fetch_publication(versioned, fetch_config(tmp_path, ["europepmc"]), http=http)
    assert record["identifiers"]["lookup_doi"] == "10.7554/elife.104978"
    assert record["fulltext"]["status"] == "ok"
    assert record["slug"] == "10.7554_elife.104978.2", "corpus keyed on the requested DOI"
    assert any("unversioned DOI" in p for p in record["problems"])


# -- size budget integration -------------------------------------------------

def test_budget_evicts_during_a_fetch_and_says_so(tmp_path):
    config = fetch_config(tmp_path, ["europepmc"], max_corpus_gb=0.000001)  # ~1 KB
    old = tmp_path / "10.1_older"
    old.mkdir(parents=True)
    (old / "fulltext.pdf").write_bytes(b"x" * 5000)
    store.write_manifest(old, {"doi": "10.1/older", "status": "complete",
                               "fetched_at": "2020-01-01T00:00:00Z",
                               "fulltext": {"path": "fulltext.pdf"}, "supplementary": []})

    http = _http(hasSuppl="N")
    record = fetcher.fetch_publication(DOI, config, http=http)

    assert record["fulltext"]["status"] == "ok", "the new article is still fetched"
    assert not (old / "fulltext.pdf").exists(), "the older article should be evicted"
    assert store.read_manifest(old)["status"] == "evicted"
    assert any("corpus budget" in p for p in record["problems"])


def test_no_budget_means_no_eviction(tmp_path):
    config = fetch_config(tmp_path, ["europepmc"])
    old = tmp_path / "10.1_older"
    old.mkdir(parents=True)
    (old / "fulltext.pdf").write_bytes(b"x" * 5000)
    store.write_manifest(old, {"doi": "10.1/older", "status": "complete",
                               "fetched_at": "2020-01-01T00:00:00Z",
                               "fulltext": {"path": "fulltext.pdf"}, "supplementary": []})
    fetcher.fetch_publication(DOI, config, http=_http(hasSuppl="N"))
    assert (old / "fulltext.pdf").exists()
