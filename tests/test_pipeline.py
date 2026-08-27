"""Tier orchestration end to end, with fake HTTP and no browser.

The status taxonomy is what these tests defend. An empty result and a failed one
look identical downstream unless something names them apart, and the trap here is
an empty `supplementary/` directory. Every assertion about a status is really an
assertion that the pipeline does not lie about what it got.
"""


import json

import pytest

from manuscript_harvest.fetch import fetcher, store
from manuscript_harvest.fetch.fetcher import _best_pdf_status, _supplement_status, suppl_flag_is_authoritative
from manuscript_harvest.fetch.identifiers import Identifiers
from manuscript_harvest.fetch.sources import OA_TIERS
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
    make_scanned_pdf,
    make_zip,
    s3_http,
    s3_listing,
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
        XML: (200, b'<article><front><article-meta><article-id pub-id-type="doi">'
                   + DOI.encode() + b"</article-id></article-meta></front><body/></article>",
              "application/xml"),
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
    `suppl=unknown_none_found files=0`. A tier that tried and came away with nothing
    *looked*; `unknown_none_found` means nobody did. Reporting both the same way is
    the exact ambiguity this taxonomy exists to prevent.

    Not expressible in `test_supplement_status_precedence` above: that parametrization
    hardcodes `has_suppl=True, in_pmc=True`, and `expected_but_missing` correctly wins
    for a paper the publisher says has supplements. This is the case where the index
    knows nothing.
    """
    ids = Identifiers(doi=DOI, doi_raw=DOI)          # has_suppl unknown
    assert _supplement_status(ids, True, 0, ["partial_failure"]) == "none_retrieved"
    assert _supplement_status(ids, True, 0, []) == "unknown_none_found"


def test_losing_everything_is_not_reported_as_a_partial_success():
    """`partial_failure` is documented in the module legend and the README as "some
    arrived; at least one failed", and it is the only way a consumer can tell from
    the status alone that a file made it. d09d7b2 returned it for the zero-file case
    too, putting two facts under one name -- the same defect that commit set out to
    fix in `unknown_none_found`.

    The word still has to mean what it says at both ends: files present or not.
    """
    ids = Identifiers(doi=DOI, doi_raw=DOI)
    assert _supplement_status(ids, True, 0, ["partial_failure"]) == "none_retrieved"
    assert _supplement_status(ids, True, 3, ["partial_failure"]) == "partial_failure"


def test_losing_everything_outranks_never_reading_the_page():
    """Reachable when Europe PMC's archive endpoint answers with a non-archive and
    the browser tier then cannot read the publisher's page. `none_retrieved` claims
    more -- something was there to retrieve and we lost it -- where `page_not_parsed`
    says we never learned whether anything was."""
    ids = Identifiers(doi=DOI, doi_raw=DOI)
    for reported in (["partial_failure", "page_not_parsed"],
                     ["page_not_parsed", "partial_failure"]):
        assert _supplement_status(ids, True, 0, reported) == "none_retrieved"
    assert _supplement_status(ids, True, 0, ["page_not_parsed"]) == "page_not_parsed"


def test_a_refused_file_is_evidence_and_outranks_every_other_empty_word():
    """Where `none_text_bearing` sits, and why. A file the policy refused is a file
    some tier *saw*: that outranks `none_listed`, which is a claim that none exist,
    and it explains away the `hasSuppl` alarm rather than being overruled by it."""
    claimed = Identifiers(doi=DOI, doi_raw=DOI, has_suppl=True, in_pmc=True)
    assert _supplement_status(claimed, True, 0, [], 3) == "none_text_bearing"
    unknown = Identifiers(doi=DOI, doi_raw=DOI)
    assert _supplement_status(unknown, True, 0, [], 1) == "none_text_bearing"
    assert _supplement_status(unknown, True, 0, ["none_listed"], 1) == "none_text_bearing"
    # And a file that arrived still outranks it: the set on disk is what it describes.
    assert _supplement_status(claimed, True, 2, ["fetched"], 3) == "fetched"


def test_a_refusal_beside_a_real_loss_does_not_settle_the_article():
    """The guard that makes the word safe. `none_text_bearing` is in `SUPPL_SETTLED`,
    so claiming it over a run that also lost a readable file would freeze that loss
    into the manifest and no later batch would look again. Re-running *can* change
    those two, which is exactly what settled means."""
    ids = Identifiers(doi=DOI, doi_raw=DOI, has_suppl=True, in_pmc=True)
    assert _supplement_status(ids, True, 0, ["partial_failure"], 3) == \
        "expected_but_missing"
    assert _supplement_status(ids, True, 0, ["page_not_parsed"], 3) == \
        "expected_but_missing"


def test_a_refused_supplement_set_needs_no_further_fetching():
    """The other half: in `SUPPL_SETTLED`, or every batch re-lists and re-refuses the
    138 articles in this corpus that hold such a file, forever."""
    assert "none_text_bearing" in store.SUPPL_SETTLED
    assert store.finalize_status(
        {"fulltext": {"status": "ok"}, "supplementary_status": "none_text_bearing"}
    )["status"] == "complete"


def test_only_a_refused_supplement_speaks_to_the_supplement_verdict():
    """An article figure is not supplementary material, so refusing one says nothing
    here -- the same division `pmc_s3._fetch_payload` draws when it decides whether a
    lost download cost the article its `fetched`. The fetcher counts by role before
    it asks."""
    ids = Identifiers(doi=DOI, doi_raw=DOI, has_suppl=True, in_pmc=True)
    assert _supplement_status(ids, True, 0, [], 0) == "expected_but_missing"


def test_losing_everything_does_not_make_a_record_look_complete():
    """The same guard `partial_failure` has: outside `SUPPL_SETTLED`, or a paper that
    lost every supplement would never be re-tried."""
    assert "none_retrieved" not in store.SUPPL_SETTLED


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


# -- a row that tried nothing still has to say why ---------------------------

def _unreachable_http() -> FakeHttp:
    """10.1016/j.oraloncology.2021.105348's shape: indexed in Europe PMC as a MED
    record, so the lookup succeeds, but with no PMCID, no open-access PDF URL and
    nothing in PMC to convert to."""
    return FakeHttp({
        SEARCH: (200, europepmc_search_json(
            pmcid=None, isOpenAccess="N", inEPMC="N", inPMC="N", hasPDF="N",
            hasSuppl="N", fullTextUrlList={"fullTextUrl": []}), "application/json"),
        "idconv": (200, json.dumps({"records": [
            {"status": "error", "errmsg": "Identifier not found in PMC"}]}).encode(),
            "application/json"),
        "api.crossref.org": (200, crossref_json(), "application/json"),
    })


def test_a_run_where_no_tier_applied_still_explains_itself(tmp_path):
    """The `--oa-only` hole d09d7b2 opened. Every OA tier keys on a PMCID or a
    Europe PMC open-access URL; this paper has neither, so nothing ran and the row
    read `failed pdf=not_found suppl=unknown_none_found files=0 tiers=-` with nothing
    after it.

    Demoting the idconv miss out of `problems` was right on its own -- "no PMC
    deposit" is the normal answer for a paywalled paper -- but it was the only line
    that row had, and the problem lines the same commit added to compensate live in
    `europepmc._fetch_pdf` and `proxy_browser._download_all`, neither of which runs
    here. This explanation has to survive every tier list, because it is the case
    where no tier ran to produce any other.
    """
    # Read from `OA_TIERS` rather than listed here: the claim is about every tier
    # list, and a hand-written copy stops covering the set the day one is added --
    # this one had already missed `pmc_s3`.
    record = fetcher.fetch_publication(
        DOI, fetch_config(tmp_path, OA_TIERS), http=_unreachable_http())

    assert record["tiers_tried"] == []
    assert len(record["problems"]) == 1
    problem = record["problems"][0]
    assert "no configured tier could try this paper" in problem
    assert "pmcid=none" in problem, "name the fact that decided it"
    assert "browser tier" in problem, "and the tier that would not have needed it"


def test_the_browser_tier_being_configured_changes_the_advice(tmp_path):
    """Telling someone the browser tier is missing when they already asked for it
    sends them at the wrong obstacle -- the same reasoning as `_cell_press_retry`'s
    two failure reasons.

    Checked against `_no_tier_applied`'s own message rather than a bare "browser
    tier" substring: `proxy_browser` being configured also means it gets *tried*,
    and where Playwright is not installed -- true of the CI environment, not this
    one -- that failure is reported as `tier proxy_browser raised ImportError: ...
    needs Playwright`, which contains "browser tier" too and is not what this test
    is about.
    """
    record = fetcher.fetch_publication(
        DOI, fetch_config(tmp_path, ["europepmc", "proxy_browser"]),
        http=_unreachable_http())

    assert record["tiers_tried"] == ["proxy_browser"]
    assert not any("no configured tier could try this paper" in p
                   for p in record["problems"])


def test_a_tier_that_ran_is_left_to_speak_for_itself(tmp_path):
    """The line is for the case where nothing ran. A tier that tried and failed has
    already said why, and a second generic line above it would bury that."""
    http = _http({PDF_URL: (404, b"", ""), SUPPL: (404, b"", "")})
    record = fetcher.fetch_publication(DOI, fetch_config(tmp_path, ["europepmc"]), http=http)

    assert record["tiers_tried"] == ["europepmc"]
    assert not any("no configured tier" in p for p in record["problems"])


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


def test_suppl_flag_is_not_authoritative_for_a_paywalled_indexed_article():
    """A record Europe PMC holds without the files cannot deny the files exist.

    10.1038/s41586-026-10510-x came back inPMC=Y, hasSuppl=N, isOpenAccess=N and
    was recorded `none_listed` with zero files -- while the landing page the
    browser tier had already saved for the PDF listed MOESM1..MOESM13. Outside the
    Open Access subset Europe PMC has the metadata and none of the supplements, so
    hasSuppl=N is a true statement about Europe PMC and a false one about the
    article.

    The open-access row is the no-regression half: an article whose files Europe
    PMC does hold still gets to say there are none, so this does not turn every
    `none_listed` into a browser-tier search.
    """
    def ids(**kw):
        return Identifiers(doi=kw.pop("doi", DOI), doi_raw="x", **kw)
    assert suppl_flag_is_authoritative(
        ids(has_suppl=False, in_pmc=True, is_open_access=False)) is False
    assert suppl_flag_is_authoritative(
        ids(has_suppl=False, in_pmc=True, is_open_access=True)) is True
    # Unknown is not a measured N: absent isOpenAccess must not revoke authority.
    assert suppl_flag_is_authoritative(
        ids(has_suppl=False, in_pmc=True, is_open_access=None)) is True


# -- the PDF taxonomy --------------------------------------------------------

def test_paywall_response_is_never_written_as_fulltext(tmp_path):
    http = _http({PDF_URL: (200, PAYWALL_HTML * 20, "application/pdf")}, hasSuppl="N")
    record = fetcher.fetch_publication(DOI, fetch_config(tmp_path, ["europepmc"]), http=http)
    assert record["fulltext"]["status"] == "paywalled"
    assert record["fulltext"]["path"] is None
    assert not (tmp_path / store.doi_slug(DOI) / "fulltext.pdf").exists()


# -- the document we accepted is not the paper we asked for -------------------
#
# Two papers in `corpus/` reached `status: complete` over a document that is not
# the requested article. Neither was an error; both are why these tests exist.


def test_a_wrong_document_is_kept_but_never_reported_complete(tmp_path):
    """10.1126/science.adf1226: a 10x Genomics Visium user guide, stored as
    `fulltext.pdf` and recorded `ok`, because nothing asked which paper it was.

    Kept on disk deliberately. It was the only file any tier produced, and it is
    the evidence for the problem line -- deleting it would leave a reader with a
    verdict and nothing to check it against.
    """
    manual = make_pdf(text="10xGenomics.com CG000239 Rev F USER GUIDE Visium "
                           "Spatial Gene Expression Reagent Kits " * 8)
    http = _http({PDF_URL: (200, manual, "application/pdf")}, hasSuppl="N")

    record = fetcher.fetch_publication(DOI, fetch_config(tmp_path, ["europepmc"]),
                                       http=http)

    assert record["fulltext"]["status"] == "identity_unverified"
    assert record["status"] != "complete"
    assert (tmp_path / store.doi_slug(DOI) / "fulltext.pdf").exists()
    assert record["fulltext"]["path"] == "fulltext.pdf"
    problem = " ".join(record["problems"])
    assert "10xGenomics.com" in problem and "not the requested article" in problem
    # And the attempt says which question was asked of which tier.
    assert any(a.get("action") == "identify_pdf" and a["status"] == "unverified"
               for a in record["attempts"])


def test_a_later_tier_still_gets_a_chance_after_a_wrong_document(tmp_path):
    """The reason an unidentifiable PDF is held rather than adopted.

    Adopting the first PDF that parses ends the search -- `need_pdf` goes false and
    no later tier is asked. So the wrong document is kept as a fallback and only
    written if every tier fails to produce the paper.
    """
    manual = make_pdf(text="10xGenomics.com USER GUIDE Visium Reagent Kits " * 12)
    real = make_pdf(text=f"TP53 knockout via CRISPR-Cas9. doi:{DOI} " * 12)
    oa_xml = (f'<OA><records returned-count="1"><record id="{PMCID}" license="CC BY">'
              f'<link format="pdf" href="ftp://ftp.ncbi.nlm.nih.gov/pub/pmc/'
              f'oa_pdf/34/e8/x.{PMCID}.pdf"/></record></records></OA>').encode()
    http = _http({
        PDF_URL: (200, manual, "application/pdf"),
        "oa.fcgi": (200, oa_xml, "application/xml"),
        "oa_pdf": (200, real, "application/pdf"),
    }, hasSuppl="N")

    record = fetcher.fetch_publication(
        DOI, fetch_config(tmp_path, ["europepmc", "pmc_oa"]), http=http)

    assert record["fulltext"]["status"] == "ok"
    assert record["fulltext"]["tier"] == "pmc_oa"
    assert record["status"] == "complete"


def test_a_correction_notice_is_never_the_article(tmp_path):
    """10.1038/s41586-024-08560-0: an Author Correction for
    10.1038/s41586-024-08150-0, fetched as a valid one-page PDF and valid JATS and
    recorded `complete`. Both files carry the *correction's* own DOI and title, so
    the identity check above passes on them -- correctly, and uselessly.

    The manifest has to end up naming the DOI to fetch instead. A rejection sends a
    reader back to the DOI list; an instruction ends the job.
    """
    http = _http({
        SEARCH: (200, europepmc_search_json(
            title="Author Correction: Progressive plasticity during colorectal "
                  "cancer metastasis",
            pubTypeList={"pubType": ["Published Erratum", "correction"]},
            commentCorrectionList={"commentCorrection": [
                {"type": "Erratum for",
                 "reference": "Nature. 2025 Jan;637(8047):947-954. "
                              "doi: 10.1038/s41586-024-08150-0."}]},
            hasSuppl="N"), "application/json"),
    })

    record = fetcher.fetch_publication(DOI, fetch_config(tmp_path, ["europepmc"]),
                                       http=http)

    assert record["fulltext"]["status"] == "not_research_article"
    assert record["status"] != "complete"
    problem = " ".join(record["problems"])
    assert "published erratum" in problem
    assert "10.1038/s41586-024-08150-0" in problem and "fetch instead" in problem


def test_a_scanned_article_is_not_downgraded_by_the_identity_check(tmp_path):
    """A scanned PDF has no text to identify, and "cannot tell" is not "wrong".

    `scanned_pdf_suspected` already says extraction will get nothing out of this
    file. Replacing it with `identity_unverified` would claim we compared the
    document against the DOI and found a mismatch, which is not what happened.
    """
    http = _http({PDF_URL: (200, make_scanned_pdf(), "application/pdf")},
                 hasSuppl="N")

    record = fetcher.fetch_publication(DOI, fetch_config(tmp_path, ["europepmc"]),
                                       http=http)

    assert record["fulltext"]["status"] == "scanned_pdf_suspected"
    assert record["status"] == "complete"


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


def _advising_tier(monkeypatch, *, hands_over_a_file: bool):
    """Patch the first tier to hit an obstacle it has advice about.

    `hands_over_a_file` is the whole variable: a later tier getting the
    supplements is what makes the advice stale.
    """
    from manuscript_harvest.fetch.sources.base import FetchedFile, ROLE_SUPPLEMENT
    from manuscript_harvest.fetch.sources.europepmc import EuropePmcSource

    def blocked(self, ids, need_pdf, need_supplements):
        from manuscript_harvest.fetch.sources.base import SourceResult
        result = SourceResult(tier="europepmc")
        result.problems.append("2 supplementary file(s) are behind NCBI's proof-of-work page")
        result.suppl_advice.append("the browser tier is required for them")
        if hands_over_a_file:
            result.suppl_status = "fetched_unverified"
            result.files.append(FetchedFile(role=ROLE_SUPPLEMENT, name="mmc1.pdf",
                                            content=b"x", url="http://x/mmc1.pdf"))
        else:
            result.suppl_status = "partial_failure"
        return result

    monkeypatch.setattr(EuropePmcSource, "fetch", blocked)


def test_advice_is_dropped_once_another_tier_got_the_supplements(tmp_path, monkeypatch):
    """"Re-run with --headed" must not outlive the obstacle it describes.

    10.1016/j.cell.2021.04.038 finished `fetched_unverified` with all 6 of its
    supplements -- the count `manual_fetch.yaml` records from the publisher by
    hand -- and still printed "13 supplementary file(s) are behind NCBI's
    proof-of-work page; the browser tier is required for them" and "re-run with
    --headed to collect these supplementary files". Both were true of a tier that
    ran; neither was true of the finished article. Acting on them costs a headed
    run over files already on disk.

    What happened still has to survive, so the obstacle stays in `problems`
    either way. Only the instruction is conditional.
    """
    _advising_tier(monkeypatch, hands_over_a_file=True)
    record = fetcher.fetch_publication(DOI, fetch_config(tmp_path, ["europepmc"]), http=_http())

    assert record["supplementary_status"] in store.SUPPL_SETTLED
    assert any("behind NCBI's proof-of-work page" in p for p in record["problems"])
    assert not any("browser tier is required" in p for p in record["problems"])


def test_advice_survives_when_the_supplements_really_are_missing(tmp_path, monkeypatch):
    """The other half: silencing advice on an unresolved obstacle would be worse
    than repeating it, because then nothing tells the user what to do next."""
    _advising_tier(monkeypatch, hands_over_a_file=False)
    record = fetcher.fetch_publication(DOI, fetch_config(tmp_path, ["europepmc"]), http=_http())

    assert record["supplementary_status"] not in store.SUPPL_SETTLED
    assert any("behind NCBI's proof-of-work page" in p for p in record["problems"])
    assert any("browser tier is required" in p for p in record["problems"])


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
        def get(self, url, params=None, accept=None, allow_redirects=True,
                headers=None):
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
    store.write_manifest(old, {"doi": "10.1038/older", "status": "complete",
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
    store.write_manifest(old, {"doi": "10.1038/older", "status": "complete",
                               "fetched_at": "2020-01-01T00:00:00Z",
                               "fulltext": {"path": "fulltext.pdf"}, "supplementary": []})
    fetcher.fetch_publication(DOI, config, http=_http(hasSuppl="N"))
    assert (old / "fulltext.pdf").exists()


# -- the S3 tier, through the orchestrator -----------------------------------
#
# Everything above about `pmc_s3` is asserted against the tier in
# `test_open_access_tiers.py`. These two are here because three of its effects are
# not the tier's to produce: it is the first tier ever to emit `ROLE_MEDIA`, so
# `_write_group(directory, store.MEDIA_DIR, ...)` and `record["media"]` only became
# live code when it landed; its `suppl_status` decides whether the record settles,
# and an unsettled record re-lists and re-downloads the whole deposit on every future
# run; and its own cap decides which files exist to settle. None of that is visible
# from `PmcS3Source.fetch`.

def _s3_pipeline_http(deposit=(), routes=None, pages=None):
    """Europe PMC answering with the PDF and no archive, over an S3 bucket.

    `supplementaryFiles` 404s because that is what the corpus records for the
    articles these deposits come from -- a ReadTimeout for one, HTTP 500 for the
    others -- and it is the only case in which `pmc_s3` is reached for supplements at
    all. The PDF still arrives from Europe PMC, as it does today, so the S3 tier is
    asked for the payload alone.

    `pages` is for the walk that dies half way, where the whole point is *which* keys
    were never named -- so the listing has to be given page by page rather than as a
    deposit `s3_http` would serve in one.
    """
    base = {SEARCH: (200, europepmc_search_json(), "application/json"),
            PDF_URL: (200, make_pdf(), "application/pdf"),
            XML: (404, b"", ""),
            SUPPL: (404, b"", "")}
    base.update(routes or {})
    return s3_http(deposit=deposit, routes=base, pages=pages)


def test_the_s3_tier_writes_article_media_and_settles_the_article(tmp_path):
    """PMC8941949's deposit shape, which is where the tier's measured facts come
    from: two supplements and one of the article's own figures, told apart by name
    alone. Nothing had ever written a file under `media/` before this tier, and the
    article has to finish settled -- `complete`, cached on the next run -- or every
    batch re-lists the bucket and re-downloads all of it.

    `text_bearing_only: false`, which is this tool's behaviour before that key
    existed: `media/` and the JPEG supplement only exist to be written when the
    policy is off, and this is the test that says so. The default over the same
    deposit is `test_the_default_run_takes_only_what_text_can_come_out_of` below."""
    version = f"{PMCID}.1"
    deposit = [
        (f"{version}/{version}.pdf", 100), (f"{version}/{version}.xml", 100),
        (f"{version}/{version}.txt", 100), (f"{version}/{version}.json", 100),
        (f"{version}/NIHMS1758707-supplement-1.jpg", 100),
        (f"{version}/NIHMS1758707-supplement-10.xlsx", 100),
        (f"{version}/nihms-1758707-f0001.jpg", 100),
    ]
    http = _s3_pipeline_http(deposit)
    config = fetch_config(tmp_path, ["europepmc", "pmc_s3"], text_bearing_only=False)
    record = fetcher.fetch_publication(DOI, config, http=http)

    assert record["status"] == "complete"
    assert record["supplementary_status"] == "fetched"
    assert [e["original_name"] for e in record["supplementary"]] == [
        "NIHMS1758707-supplement-1.jpg", "NIHMS1758707-supplement-10.xlsx"]
    assert [e["original_name"] for e in record["media"]] == ["nihms-1758707-f0001.jpg"]
    assert [e["tier"] for e in record["media"]] == ["pmc_s3"]
    directory = tmp_path / "10.1038_s41586-021-03852-1"
    assert (directory / "media" / "01_nihms-1758707-f0001.jpg").exists()
    assert not (directory / "supplementary" / "03_nihms-1758707-f0001.jpg").exists(), \
        "a figure in supplementary/ is what the split exists to prevent"

    calls = len(http.calls)
    again = fetcher.fetch_publication(DOI, config, http=http)
    assert again.get("cached") is True
    assert len(http.calls) == calls, "a settled article must not re-list the bucket"


def test_article_figures_do_not_spend_the_cap_a_supplementary_table_needed(tmp_path):
    """PMC8494637's real deposit (10.1038/s41586-021-03604-1, in this corpus), whose
    57 payload objects are 29 `MOESM*` files and 28 `Fig*` JPEGs -- of which the 23
    named `_ESM` are supplementary figures and only the 5 `_HTML` are the article's
    own. Its recorded manifest shows `europepmc` timing out on the archive, which is
    exactly when this tier answers.

    S3 lists keys in binary order, so `Fig*` precedes `MOESM*`, and capping the raw
    payload kept 28 figures and 22 tables: MOESM3 (14 MB), MOESM4, MOESM5 (a 22 MB
    zip) and MOESM6-9 were dropped, the article went from `complete` to `partial`,
    and every later run re-downloaded 50 objects to drop them again. Charging the
    supplements first keeps 50 of the 52 supplement-classified objects -- two really
    are past the cap, which is the cap doing its job -- and spends nothing on files
    `extract/` never reads.

    Pinned with `text_bearing_only: false`, the run that fetches figures at all. With
    the policy on the cap is never reached: the 28 JPEGs are refused from the listing
    and all 29 tables fit. Ordering by role is what makes that refusal per-role, and
    this test is what keeps `text_bearing_only: false` meaning what it did before the
    key existed.
    """
    version = f"{PMCID}.1"
    stem = "41586_2021_3604"
    deposit = [(f"{version}/{version}.{ext}", 100)
               for ext in ("pdf", "xml", "txt", "json")]
    deposit += [(f"{version}/{stem}_MOESM{n}_ESM.xlsx", 100) for n in range(1, 30)]
    deposit += [(f"{version}/{stem}_Fig{n}_ESM.jpg", 100) for n in range(1, 24)]
    deposit += [(f"{version}/{stem}_Fig{n}_HTML.jpg", 100) for n in range(1, 6)]
    # The order the bucket serves, not the order they were written above.
    deposit.sort()

    http = _s3_pipeline_http(deposit)
    config = fetch_config(tmp_path, ["europepmc", "pmc_s3", "pmc_supplements"],
                          text_bearing_only=False)
    record = fetcher.fetch_publication(DOI, config, http=http)

    names = [entry["original_name"] for entry in record["supplementary"]]
    assert len(names) == 50, "the whole cap went to supplementary files"
    assert not any("_HTML.jpg" in name for name in names), \
        "no article figure took a slot from a table"
    assert {f"{stem}_MOESM{n}_ESM.xlsx" for n in range(3, 8)} <= set(names), \
        "MOESM3-7 are the files raw key order dropped"
    assert record.get("media", []) == [], \
        "the figures are what the cap dropped instead -- no media key, none written"
    assert any("5 article figure(s) not fetched" in p for p in record["problems"])
    assert any("2 supplementary file(s) not fetched" in p for p in record["problems"]), \
        "MOESM8 and MOESM9 really are past the cap; that is the cap, not displacement"

    assert record["supplementary_status"] == "fetched_unverified"
    assert record["status"] == "complete", "a cap truncation must not read as a failure"
    assert http.called_matching("/articles/PMC") == 0, \
        "and PMC's proof-of-work page was never needed"

    calls = len(http.calls)
    again = fetcher.fetch_publication(DOI, config, http=http)
    assert again.get("cached") is True
    assert len(http.calls) == calls, "or every batch re-downloads the whole deposit"


# -- fetching only what text can come out of ---------------------------------
#
# `fetch.text_bearing_only` is on by default. The tiers refuse what they can name in
# advance and `fetch_publication` refuses whatever else arrives, so the tests that
# belong here are the ones only the orchestrator can answer: what the corpus ends up
# holding, what the record says was refused, and -- the one that matters -- what
# `supplementary_status` calls an article whose supplements are all figures. Get that
# word wrong and every batch either re-fetches the article forever or reports an alarm
# over a paper with nothing wrong with it.


def _filter_notes(record):
    """The `text_bearing_filter` attempts, which is where every skip is named.

    A list, not one note: `pmc_s3` refuses per role -- its supplements and its own
    figures are different questions and `suppl_status` is decided on the difference
    -- so it records a pass for each.
    """
    return [a for a in record["attempts"] if a["action"] == "text_bearing_filter"]


def _skipped(record):
    """Every filename the policy refused, in the order the notes recorded them."""
    return [name for note in _filter_notes(record) for name in note["files"]]


def test_the_default_run_takes_only_what_text_can_come_out_of(tmp_path):
    """PMC8941949's deposit again -- the same one
    `test_the_s3_tier_writes_article_media_and_settles_the_article` pins with the
    policy off, so the pair is the whole difference the key makes. The JPEG
    supplement and the article figure are refused from the listing, neither costs a
    request, and the article still finishes settled on the spreadsheet."""
    version = f"{PMCID}.1"
    deposit = [
        (f"{version}/{version}.pdf", 100), (f"{version}/{version}.xml", 100),
        (f"{version}/NIHMS1758707-supplement-1.jpg", 100),
        (f"{version}/NIHMS1758707-supplement-10.xlsx", 100),
        (f"{version}/nihms-1758707-f0001.jpg", 100),
    ]
    http = _s3_pipeline_http(deposit)
    config = fetch_config(tmp_path, ["europepmc", "pmc_s3"])
    record = fetcher.fetch_publication(DOI, config, http=http)

    assert [e["original_name"] for e in record["supplementary"]] == [
        "NIHMS1758707-supplement-10.xlsx"]
    assert record.get("media", []) == [], "and media/ has nothing left to hold"
    assert not (tmp_path / "10.1038_s41586-021-03852-1" / "media").exists()
    assert record["supplementary_status"] == "fetched", \
        "the deposit was enumerated and every readable file in it arrived"
    assert record["status"] == "complete"

    assert http.called_matching("supplement-1.jpg") == 0, "the request is never spent"
    assert http.called_matching("f0001.jpg") == 0

    notes = _filter_notes(record)
    assert all(n["where"] == "before_download" for n in notes)
    assert [n["roles"] for n in notes] == [{"supplement": 1}, {"media": 1}], \
        "the two are separate passes because the verdict is decided on the difference"
    assert all(n["reasons"] == {"image": 1} for n in notes)
    assert _skipped(record) == ["NIHMS1758707-supplement-1.jpg",
                               "nihms-1758707-f0001.jpg"], \
        "named, not counted: a reader has to see what a different setting would fetch"


def test_a_deposit_of_nothing_but_figures_settles_instead_of_alarming(tmp_path):
    """The taxonomy question this change had to answer.

    Europe PMC says `hasSuppl: Y` and it is right -- PMC8941949-shaped deposits like
    this one really do hold supplementary files. They are three JPEGs, the filter
    refuses all three, and the tier comes away with nothing. `expected_but_missing`
    is what `_supplement_status` used to reach there: "the state the whole taxonomy
    exists to expose", raised over an article where nothing is missing and nothing
    failed. It also blocks `complete`, and it is not settled, so every later batch
    would re-list the bucket to refuse the same three files again.
    """
    version = f"{PMCID}.1"
    deposit = [(f"{version}/{version}.pdf", 100)] + [
        (f"{version}/NIHMS1758707-supplement-{n}.jpg", 100) for n in range(1, 4)]
    http = _s3_pipeline_http(deposit)
    config = fetch_config(tmp_path, ["europepmc", "pmc_s3"])
    record = fetcher.fetch_publication(DOI, config, http=http)

    assert record["identifiers"]["has_suppl"] is True, "the publisher does claim them"
    assert record["supplementary_status"] == "none_text_bearing"
    assert record["supplementary_status"] in store.SUPPL_SETTLED
    assert record["status"] == "complete"
    assert record["supplementary"] == []
    assert len(_skipped(record)) == 3

    calls = len(http.calls)
    again = fetcher.fetch_publication(DOI, config, http=http)
    assert again.get("cached") is True
    assert len(http.calls) == calls, "re-running refuses the same three files"


def test_an_all_figure_deposit_stops_the_tier_loop_where_a_fetch_would_have(tmp_path):
    """The refusal must not send the run hunting.

    `fetch_publication` stops asking for supplements as soon as one arrives, so a run
    that *kept* those JPEGs would have stopped at `pmc_s3` too. Carrying on instead
    would walk `pmc_supplements` into PMC's proof-of-work page and then ask for a
    browser, to find the same figures -- and a `page_not_parsed` on the way would
    block `none_text_bearing` and land the article on the alarm.
    """
    version = f"{PMCID}.1"
    deposit = [(f"{version}/{version}.pdf", 100),
               (f"{version}/NIHMS1758707-supplement-1.tif", 100)]
    http = _s3_pipeline_http(deposit)
    record = fetcher.fetch_publication(
        DOI, fetch_config(tmp_path, ["europepmc", "pmc_s3", "pmc_supplements"]),
        http=http)

    assert record["supplementary_status"] == "none_text_bearing"
    assert http.called_matching("/articles/PMC") == 0, \
        "PMC's proof-of-work page was never needed"


def test_a_lost_spreadsheet_beside_a_refused_figure_still_raises_the_alarm(tmp_path):
    """The guard that makes the new word safe. `none_text_bearing` is settled, so
    claiming it over a run that also lost a readable file would freeze that loss into
    the manifest and no later batch would look again."""
    version = f"{PMCID}.1"
    deposit = [(f"{version}/{version}.pdf", 100),
               (f"{version}/NIHMS1758707-supplement-1.jpg", 100),
               (f"{version}/NIHMS1758707-supplement-2.xlsx", 100)]
    http = _s3_pipeline_http(deposit, routes={"supplement-2.xlsx": (500, b"", "")})
    record = fetcher.fetch_publication(
        DOI, fetch_config(tmp_path, ["europepmc", "pmc_s3"]), http=http)

    assert record["supplementary_status"] == "expected_but_missing"
    assert record["supplementary_status"] not in store.SUPPL_SETTLED
    assert len(_skipped(record)) == 1, "and the refusal is still recorded"


def test_an_enumeration_that_stopped_half_way_cannot_be_settled_by_a_refusal(tmp_path):
    """`none_text_bearing` says "the supplements were named". A listing that stopped
    half way named no deposit.

    S3 page 1 holds the PDF and `supplement-1.jpg` and carries a continuation token;
    page 2 answers 503. The filter refuses the JPEG, so nothing was kept, nothing was
    lost and nothing was dropped by the cap -- and `pmc_s3` used to stay silent there,
    which sent the fetcher a refusal count with no reported status and got the settled
    word back. `complete` on the record, `manifest_is_complete` true, cached on the
    next run: an unread continuation page frozen into the manifest, and keys sort
    lexicographically so `supplement-1.jpg` precedes `supplement-10.xlsx` -- the
    readable names are exactly the ones on the page nobody read.

    `text_bearing_only: false` over the same listing has always been re-tried, which
    is the measurement that makes this a regression rather than a rough edge: the same
    bytes from the bucket must not get opposite verdicts from the filter.
    """
    version = f"{PMCID}.1"
    pages = {None: s3_listing((f"{version}/{version}.pdf", 100),
                              (f"{version}/NIHMS1758707-supplement-1.jpg", 100),
                              token="tok"),
             "tok": (503, b"", "")}
    http = _s3_pipeline_http(pages=pages)
    config = fetch_config(tmp_path, ["europepmc", "pmc_s3"])
    record = fetcher.fetch_publication(DOI, config, http=http)

    assert any("the enumeration is incomplete" in p for p in record["problems"])
    assert record["supplementary_status"] == "expected_but_missing"
    assert record["supplementary_status"] not in store.SUPPL_SETTLED
    assert record["status"] == "partial"
    assert len(_skipped(record)) == 1, "and the refusal is still recorded"

    again = fetcher.fetch_publication(DOI, config, http=http)
    assert again.get("cached") is not True, "or no batch ever reads the rest of it"


def test_a_refused_figure_does_not_give_up_the_tier_the_lost_table_needs(tmp_path):
    """The other half of the exit at `elif refused_supplements`, and the expensive
    half: the early exit is only honest when the tier accounted for the whole set.

    Same deposit as the test above, one tier further. `pmc_s3` refuses the JPEG from
    the listing and the S3 copy of `supplement-2.xlsx` answers 500 -- so this tier
    both refused a file and lost one. Stopping here on the strength of the refusal
    abandons `pmc_supplements`, which serves that very file from PMC's `/bin/` route,
    and the article ends `expected_but_missing`: not settled, so every later batch
    repeats the identical truncated run and loses the same spreadsheet again. The
    refusal must not be read as "the set is accounted for" when the tier said
    `partial_failure` (`fetcher.SUPPL_RECOVERABLE`).

    The file at stake is a `.xlsx`, which is what makes this the worst shape the
    policy can produce: refusing a figure loses a supplementary table.
    """
    version = f"{PMCID}.1"
    bin_url = "/articles/instance/8426186/bin/NIHMS1758707-supplement-2.xlsx"
    page = f'<html><body><a href="{bin_url}">Table S2</a></body></html>'.encode()
    deposit = [(f"{version}/{version}.pdf", 100),
               (f"{version}/NIHMS1758707-supplement-1.jpg", 100),
               (f"{version}/NIHMS1758707-supplement-2.xlsx", 100)]
    # Keyed on the S3 path, not the bare filename: `FakeHttp` matches by substring
    # and PMC's `/bin/` URL ends in the same name, so a bare fragment would 500 the
    # rescue route as well and the test would pass for the wrong reason.
    http = _s3_pipeline_http(deposit, routes={
        f"{version}/NIHMS1758707-supplement-2.xlsx": (500, b"", ""),
        f"/articles/{PMCID}/": (200, page, "text/html"),
        bin_url: (200, b"PK\x03\x04 spreadsheet", "application/vnd.ms-excel"),
    })
    record = fetcher.fetch_publication(
        DOI, fetch_config(tmp_path, ["europepmc", "pmc_s3", "pmc_supplements"]),
        http=http)

    assert record["tiers_tried"] == ["europepmc", "pmc_s3", "pmc_supplements"], \
        "the refusal must not cost the article the tier that holds the file"
    assert [e["original_name"] for e in record["supplementary"]] == [
        "NIHMS1758707-supplement-2.xlsx"]
    assert record["supplementary_status"] == "fetched_unverified"
    assert record["supplementary_status"] in store.SUPPL_SETTLED
    assert record["status"] == "complete"
    assert len(_skipped(record)) == 1, "and the JPEG still cost no request"
    assert http.called_matching("supplement-1.jpg") == 0


def _later_tier_recording_what_it_was_asked(monkeypatch, *files):
    """Patch `pmc_oa` to record what it was asked for, and hand back `files`.

    Standing in for `proxy_browser`, which is the tier the case below actually needs
    and cannot be driven from here: it wants Playwright, and the obstacle under test
    is NCBI's proof-of-work page -- the one wall a real browser is the only way
    through. The assertion is about the hand-off, and every tier receives it through
    the same two arguments. `pmc_oa` is the next tier after `pmc_supplements` in the
    shipped order, so it also stands in the right place.
    """
    from manuscript_harvest.fetch.sources.base import SourceResult
    from manuscript_harvest.fetch.sources.pmc_oa import PmcOaSource

    asked = []

    def recorded(self, ids, need_pdf, need_supplements):
        asked.append({"pdf": need_pdf, "supplements": need_supplements})
        result = SourceResult(tier="pmc_oa")
        if need_supplements:
            result.files.extend(files)
            result.suppl_status = "fetched_unverified" if files else "page_not_parsed"
        return result

    monkeypatch.setattr(PmcOaSource, "fetch", recorded)
    return asked


def test_a_refusal_beside_a_wall_still_reaches_the_tier_that_can_clear_it(
        tmp_path, monkeypatch):
    """The shape `pmc_supplements` was written for, which is where the premature exit
    costs the most.

    PMC's page lists `fig1.jpg` and `supplement-2.xlsx`; the filter refuses the JPEG
    before the request, and the `/bin/` URL for the spreadsheet answers NCBI's
    proof-of-work page -- the *normal* case for a publisher whose static host this
    tier cannot construct, per its own module docstring. So the tier reports
    `partial_failure` and advises "the browser tier is required for them", and the
    run then has to actually reach a browser-capable tier. Giving up on the refusal
    instead made the manifest print advice for a tier the same run had made
    unreachable, on every batch forever, since `expected_but_missing` never settles.
    """
    from manuscript_harvest.fetch.sources.base import ROLE_SUPPLEMENT, FetchedFile
    from tests.fakes import POW_HTML

    asked = _later_tier_recording_what_it_was_asked(
        monkeypatch,
        FetchedFile(role=ROLE_SUPPLEMENT, name="NIHMS1758707-supplement-2.xlsx",
                    content=b"PK\x03\x04 spreadsheet",
                    url="https://pmc.example/supplement-2.xlsx"))
    listing = "/articles/instance/8426186/bin/"
    page = (f'<html><body><a href="{listing}fig1.jpg">Figure 1</a>'
            f'<a href="{listing}NIHMS1758707-supplement-2.xlsx">Table S2</a>'
            f'</body></html>').encode()
    http = _http({SUPPL: (404, b"", ""),
                  f"/articles/{PMCID}/": (200, page, "text/html"),
                  listing: (200, POW_HTML, "text/html")})
    record = fetcher.fetch_publication(
        DOI, fetch_config(tmp_path, ["europepmc", "pmc_supplements", "pmc_oa"]),
        http=http)

    assert asked == [{"pdf": False, "supplements": True}], \
        "the wall is the whole reason the next tier exists"
    assert [e["original_name"] for e in record["supplementary"]] == [
        "NIHMS1758707-supplement-2.xlsx"]
    assert record["supplementary_status"] == "fetched_unverified"
    assert record["status"] == "complete"
    assert http.called_matching("fig1.jpg") == 0, "and the JPEG still cost no request"


def test_a_forced_refetch_does_not_discard_a_swept_articles_record(tmp_path):
    """`drop-media` and `--force` meet here, and the check that decides it is
    `_entry_accounted_for` rather than `_still_on_disk`.

    A removed entry names no file by design. Judged by "is the file there?", one
    removal makes the whole existing set look gone, so a re-fetch that comes away
    empty-handed replaces it with `[]` -- discarding the spreadsheet still on disk
    from the record along with the account of what was removed. Which is the loss
    186b2e4 fixed for a failed re-fetch of a good set, one policy later.
    """
    from manuscript_harvest.fetch.drop_media import drop_media_article

    archive = make_zip([("a_MOESM1_ESM.pdf", b"%PDF one"),
                        ("a_MOESM2_ESM.jpg", b"\xff\xd8two")])
    config = fetch_config(tmp_path, ["europepmc"], text_bearing_only=False)
    fetcher.fetch_publication(DOI, config, http=_http({SUPPL: (200, archive,
                                                               "application/zip")}))
    directory = tmp_path / "10.1038_s41586-021-03852-1"
    assert drop_media_article(directory, apply=True)["removed"] is True

    # The archive endpoint has stopped answering, as it does for several articles in
    # this corpus -- a 500 or a ReadTimeout -- so the re-fetch gets nothing.
    record = fetcher.fetch_publication(
        DOI, config, force=True, http=_http({SUPPL: (404, b"", "")}))

    assert [e.get("path") or e.get("name") for e in record["supplementary"]] == [
        "supplementary/01_a_MOESM1_ESM.pdf", "supplementary/02_a_MOESM2_ESM.jpg"]
    assert record["supplementary"][1]["removed"] == store.NOT_TEXT_BEARING
    assert record["supplementary_status"] == "fetched", "the old verdict still stands"
    assert any("kept the existing set already on disk" in p
               for p in record["problems"]), \
        "and the sentence is true: the PDF really is still there"
    record["_directory"] = str(directory)
    assert store.manifest_is_complete(record) is True


def test_a_refetch_of_a_wholly_swept_set_does_not_claim_files_on_disk(tmp_path):
    """The same branch for the article whose supplements were *all* figures -- the tail
    of the 138 that hold one -- where the sentence beside the kept record stopped being
    true.

    `_entry_accounted_for` deliberately accepts a removal marker, and it has to: the
    alternative discards a swept article's record, which the test above defends. But
    the sentence it unlocked still promised bytes on disk, and after a whole-set sweep
    there are none -- `drop-media` unlinked the last file and `_prune_empty_dirs`
    removed `supplementary/` itself. `problems` is the field a reader trusts to
    describe the corpus, and this one is written into `manifest.json`, so a false
    sentence there outlives the run that wrote it.
    """
    from manuscript_harvest.fetch.drop_media import drop_media_article

    archive = make_zip([("a_MOESM1_ESM.jpg", b"\xff\xd8one"),
                        ("a_MOESM2_ESM.tif", b"II*\x00two")])
    config = fetch_config(tmp_path, ["europepmc"], text_bearing_only=False)
    fetcher.fetch_publication(DOI, config, http=_http({SUPPL: (200, archive,
                                                               "application/zip")}))
    directory = tmp_path / "10.1038_s41586-021-03852-1"
    assert drop_media_article(directory, apply=True)["removed"] is True
    assert not (directory / store.SUPPLEMENT_DIR).exists(), "the directory goes too"

    record = fetcher.fetch_publication(
        DOI, config, force=True, http=_http({SUPPL: (404, b"", "")}))

    assert [e["name"] for e in record["supplementary"]] == [
        "supplementary/01_a_MOESM1_ESM.jpg", "supplementary/02_a_MOESM2_ESM.tif"], \
        "the record is still kept, markers and all"
    assert not any("already on disk" in p for p in record["problems"]), \
        "there is nothing on disk to have kept"
    assert any("a policy sweep had already removed" in p
               for p in record["problems"])


def _tier_handing_back(monkeypatch, *files, pdf_status=None):
    """Patch the first tier to hand back files whose names it never filtered.

    Which is a real shape, not a contrivance: `proxy_browser` reads a supplement's
    name out of `Content-Disposition` *after* the body has arrived, because a
    publisher endpoint may carry no extension at all -- ClinicalKey serves twelve
    supplements from one such URL. The anchor cannot be judged; the answer can.
    """
    from manuscript_harvest.fetch.sources.base import SourceResult
    from manuscript_harvest.fetch.sources.europepmc import EuropePmcSource

    def handed_over(self, ids, need_pdf, need_supplements):
        result = SourceResult(tier="europepmc")
        result.suppl_status = "fetched_unverified"
        result.pdf_status = pdf_status
        result.files.extend(files)
        return result

    monkeypatch.setattr(EuropePmcSource, "fetch", handed_over)


def test_the_fetcher_refuses_a_file_no_tier_could_name_in_advance(tmp_path, monkeypatch):
    """The central guarantee: whatever a tier hands back, this is what decides what
    lands. The tiers save the requests; this decides what the corpus holds, which is
    the only place a tier added later cannot get wrong by omission."""
    from manuscript_harvest.fetch.sources.base import ROLE_SUPPLEMENT, FetchedFile

    _tier_handing_back(
        monkeypatch,
        FetchedFile(role=ROLE_SUPPLEMENT, name="mmc1.xlsx", content=b"xlsx",
                    url="https://host/ui/service/content/url?path=mmc1.xlsx"),
        FetchedFile(role=ROLE_SUPPLEMENT, name="movie-s1.mp4", content=b"mp4 bytes",
                    url="https://host/ui/service/content/url?path=movie-s1.mp4"),
    )
    record = fetcher.fetch_publication(
        DOI, fetch_config(tmp_path, ["europepmc"]), http=_http())

    assert [e["original_name"] for e in record["supplementary"]] == ["mmc1.xlsx"]
    note, = _filter_notes(record)
    assert note["where"] == "after_download", "the request was already spent"
    assert note["files"] == ["movie-s1.mp4"] and note["reasons"] == {"audio_video": 1}
    assert record["supplementary_status"] == "fetched_unverified", \
        "the tier's own verdict about the set it saw is not this policy's business"


def test_the_policy_can_never_refuse_the_article_itself(tmp_path, monkeypatch):
    """Only `supplement` and `media` are asked. The article's PDF, JATS and landing
    page are exempt by role rather than by extension, because the cost of getting
    this wrong is the paper: this corpus already holds one article whose only PDF
    came off a third-party CDN under a path nobody would have predicted."""
    from manuscript_harvest.fetch.sources.base import ROLE_PDF, FetchedFile

    _tier_handing_back(
        monkeypatch,
        FetchedFile(role=ROLE_PDF, name="figure-viewer.jpg", content=make_pdf(),
                    url="https://cdn.example/figure-viewer.jpg"),
        pdf_status="ok",
    )
    record = fetcher.fetch_publication(
        DOI, fetch_config(tmp_path, ["europepmc"]), http=_http())

    assert record["fulltext"]["status"] == "ok"
    assert record["fulltext"]["path"] == store.FULLTEXT_PDF
    assert (tmp_path / "10.1038_s41586-021-03852-1" / store.FULLTEXT_PDF).exists()


def test_fetching_everything_is_one_key_away(tmp_path):
    """`text_bearing_only: false` has to reproduce today's behaviour exactly, and the
    S3 deposit is where that is most visible: the same listing, the same order, the
    same cap arithmetic, and no filter note at all."""
    version = f"{PMCID}.1"
    deposit = [(f"{version}/{version}.pdf", 100),
               (f"{version}/NIHMS1758707-supplement-1.jpg", 100),
               (f"{version}/NIHMS1758707-supplement-2.xlsx", 100)]
    http = _s3_pipeline_http(deposit)
    record = fetcher.fetch_publication(
        DOI, fetch_config(tmp_path, ["europepmc", "pmc_s3"], text_bearing_only=False),
        http=http)

    assert [e["original_name"] for e in record["supplementary"]] == [
        "NIHMS1758707-supplement-1.jpg", "NIHMS1758707-supplement-2.xlsx"]
    assert record["supplementary_status"] == "fetched"
    assert http.called_matching("supplement-1.jpg") == 1
    assert not [a for a in record["attempts"] if a["action"] == "text_bearing_filter"]


def test_a_pdf_refused_on_size_keeps_that_word_in_the_manifest(tmp_path):
    """The end of the same path, through the orchestrator, because that is where the
    word was being lost. `pmc_s3` reads the size out of the listing and refuses the
    PDF before transferring it; `pmc_oa` then runs -- both tiers apply to any PMCID
    and this is the shipped order -- finds no `oa.fcgi` record here and answers
    `not_in_oa_subset`. Until `too_large` was ranked in `validate.PDF_DIAGNOSES` the
    generic miss won, and the manifest claimed the article is outside the Open Access
    subset over a listing from that very bucket."""
    version = f"{PMCID}.1"
    http = _s3_pipeline_http([(f"{version}/{version}.pdf", 500 * 1024 ** 2)])
    record = fetcher.fetch_publication(
        DOI, fetch_config(tmp_path, ["pmc_s3", "pmc_oa"]), http=http)

    assert record["fulltext"]["status"] == "too_large"
    assert record["fulltext"]["path"] is None
    assert any("500.0 MB exceeds the 200 MB cap" in p for p in record["problems"])
    assert [a["status"] for a in record["attempts"] if a["action"] == "pdf"] == ["too_large"]
