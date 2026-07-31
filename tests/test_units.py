"""Unit coverage for the deterministic pieces: identifiers, validation, store,
adapters, HTTP politeness, and config merging.

Most of these pin a rule that a live batch proved wrong at least once. Where that
is the case the docstring names the DOI, so the test explains itself when it
fails.
"""


import pytest

from manuscript_harvest.fetch import store
from manuscript_harvest.fetch.adapters import adapter_for
from manuscript_harvest.fetch.adapters.base import (
    dedupe_by_target,
    is_file_url,
    is_supplement_url,
    looks_like_supplement,
    url_without_fragment,
)
from manuscript_harvest.fetch.adapters.publishers import ElsevierAdapter, WileyAdapter
from manuscript_harvest.fetch.cli import DEFAULT_FETCH_CONFIG, _merge, load_config
from manuscript_harvest.fetch.http import Http
from manuscript_harvest.fetch.identifiers import (
    Identifiers,
    doi_slug,
    is_preprint_doi,
    normalize_doi,
    unversioned_doi,
)
from manuscript_harvest.fetch.sources.pmc_oa import _classify, _unpack_tgz, ftp_to_https
from manuscript_harvest.fetch.validate import classify_denial, looks_like_pdf, validate_pdf
from tests.fakes import (
    CLINICALKEY_ARTICLE_PDF,
    CLINICALKEY_SUPPLEMENT_LINKS,
    DOI,
    DUO_PROMPT_HTML,
    DUO_PROMPT_URL,
    EZPROXY_HTML,
    PAYWALL_HTML,
    POW_HTML,
    RESOURCE_NOT_FOUND_XML,
    SCIENCE_ARTICLE_LINKS,
    SSO_HTML,
    FakePage,
    make_paywall_pdf,
    make_pdf,
    make_scanned_pdf,
    make_tgz,
)


# -- DOI handling ------------------------------------------------------------

@pytest.mark.parametrize("raw", [
    DOI, DOI.upper(), f"https://doi.org/{DOI}", f"http://dx.doi.org/{DOI}",
    f"doi:{DOI}", f"  {DOI}  ", f"{DOI}.", f"info:doi/{DOI}",
])
def test_normalize_doi_accepts_common_forms(raw):
    assert normalize_doi(raw) == DOI


@pytest.mark.parametrize("bad", ["", "   ", "not a doi", "10.x/y",
                                 "https://example.org/paper", "doi:", "10.1234"])
def test_normalize_doi_rejects_non_dois(bad):
    with pytest.raises(ValueError):
        normalize_doi(bad)


def test_doi_slug_is_filesystem_safe_and_collision_free():
    assert doi_slug(DOI) == "10.1038_s41586-021-03852-1"
    assert "/" not in doi_slug("10.1234/a/b/c")
    long_a, long_b = "10.1234/" + "a" * 300, "10.1234/" + "a" * 299 + "b"
    assert doi_slug(long_a) != doi_slug(long_b)
    assert len(doi_slug(long_a)) <= 150


def test_version_suffix_stripped_only_when_it_is_a_version():
    """eLife reviewed preprints are indexed unversioned; article numbers are not
    versions. 10.7554/eLife.104978.2 has no Europe PMC record while
    10.7554/eLife.104978 resolves to PMC12893711."""
    assert unversioned_doi("10.7554/elife.104978.2") == "10.7554/elife.104978"
    for keep in ["10.1016/j.cell.2021.01.053", "10.1101/2025.07.21.666016",
                 "10.1126/science.aax6234", "10.1182/bloodadvances.2023011445"]:
        assert unversioned_doi(keep) is None, keep


def test_preprint_prefixes_include_openrxiv():
    """bioRxiv migrated to openRxiv's 10.64898, so gating on 10.1101 silently
    skipped the preprint tier for every newly posted preprint."""
    assert is_preprint_doi("10.1101/2025.07.21.666016")
    assert is_preprint_doi("10.64898/2026.02.15.704933")
    assert not is_preprint_doi(DOI)
    assert Identifiers(doi="10.21203/x", doi_raw="x", epmc_source="PPR").is_preprint


def test_open_access_pdf_urls_filters_by_availability():
    ids = Identifiers(doi=DOI, doi_raw=DOI, full_text_urls=[
        {"documentStyle": "pdf", "availabilityCode": "OA", "url": "https://a/1.pdf"},
        {"documentStyle": "pdf", "availabilityCode": "S", "url": "https://a/paywalled.pdf"},
        {"documentStyle": "html", "availabilityCode": "OA", "url": "https://a/page"},
    ])
    assert ids.open_access_pdf_urls() == ["https://a/1.pdf"]


# -- validation --------------------------------------------------------------

def test_real_pdf_accepted():
    accepted, status, meta = validate_pdf(make_pdf(), content_type="application/pdf")
    assert accepted and status == "ok" and meta["pages"] == 3


def test_scanned_pdf_kept_but_flagged():
    """Kept because it is the article, flagged because pdf_loader gets nothing."""
    accepted, status, _ = validate_pdf(make_scanned_pdf())
    assert accepted and status == "scanned_pdf_suspected"


@pytest.mark.parametrize("body,content_type,url,expected", [
    (PAYWALL_HTML * 20, "application/pdf", "https://p/a", "paywalled"),
    (EZPROXY_HTML * 20, "text/html", "https://p/a", "proxy_not_configured"),
    (SSO_HTML * 20, "text/html", "https://login.stanford.edu/idp", "session_expired"),
    (DUO_PROMPT_HTML, "text/html", DUO_PROMPT_URL, "session_expired"),
    (POW_HTML * 20, "text/html", "https://pmc/bin/x", "javascript_challenge"),
    (RESOURCE_NOT_FOUND_XML, "text/xml",
     "https://www-clinicalkey-com.stanford.idm.oclc.org/content/playBy/pii/"
     "?v=S2666979X26001667", "link_resolver_error"),
    (b"", "application/pdf", "https://p/a", "download_failed"),
    (b"%PDF-1.4 truncated" * 50, "application/pdf", "https://p/a", "not_a_pdf"),
])
def test_denials_are_named_precisely(body, content_type, url, expected):
    """Content-Type lies: a paywall page served as application/pdf must not be
    stored. Magic bytes decide."""
    accepted, status, _ = validate_pdf(body, content_type=content_type, url=url)
    assert not accepted and status == expected


def test_duo_is_an_expired_session_by_host_alone():
    """An expired proxy session lands on Duo, not on Stanford's login page.

    The host is the durable signal: Duo can restyle its prompt, but a request
    that ends up at `duosecurity.com` is a dead session either way. Checked
    separately from the body so neither route can rot unnoticed.
    """
    assert classify_denial(DUO_PROMPT_URL, b"<html><body>anything</body></html>") \
        == "session_expired"
    # ... and by wording alone, for the frameless prompt embedded elsewhere.
    assert classify_denial("https://www-science-org.stanford.idm.oclc.org/doi/10.1/x",
                           DUO_PROMPT_HTML) == "session_expired"


def test_link_resolver_error_is_not_a_page_we_failed_to_parse():
    """10.1016/j.xgen.2026.101304: the proxy routed an Elsevier DOI to
    ClinicalKey, which does not carry Cell Genomics and answered HTTP 200 with
    `<ServiceErrorResponse><status>RESOURCE_NOT_FOUND</status>`. Unnamed, that
    reached the generic adapter and came back as `no_pdf_link`."""
    assert classify_denial("https://www-clinicalkey-com.stanford.idm.oclc.org/x",
                           RESOURCE_NOT_FOUND_XML) == "link_resolver_error"
    # An article that merely discusses resolvers is not one.
    assert classify_denial("https://p/a", b"<html><body>We used a link resolver to "
                                          b"locate each cited article.</body></html>") is None


def test_small_paywall_pdf_stub_rejected():
    accepted, status, _ = validate_pdf(make_paywall_pdf(), content_type="application/pdf")
    assert not accepted and status == "paywalled"


def test_looks_like_pdf_ignores_leading_whitespace():
    assert looks_like_pdf(b"\n  %PDF-1.7 ...")
    assert not looks_like_pdf(b"<html>%PDF</html>")


def test_classify_denial_returns_none_for_a_real_page():
    assert classify_denial("https://x", b"<html><body>An article about TP53</body></html>") is None


# -- store: layout and manifests --------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("a b/c.xlsx", "c.xlsx"),
    ("../../etc/passwd", "passwd"),
    ("file.pdf?download=true", "file.pdf"),
    ("", "file"),
    ("/", "file"),
])
def test_sanitize_filename(raw, expected):
    assert store.sanitize_filename(raw) == expected


def test_sanitize_filename_keeps_extension_when_truncating():
    cleaned = store.sanitize_filename("x" * 400 + ".xlsx")
    assert len(cleaned) <= 120 and cleaned.endswith(".xlsx")


def test_supplement_filename_is_order_stable():
    assert store.supplement_filename(3, "t.xls") == "03_t.xls"


def test_manifest_round_trip(tmp_path):
    record = {"doi": DOI, "status": "complete", "supplementary": []}
    store.write_manifest(tmp_path, record)
    assert store.read_manifest(tmp_path) == record


def test_read_manifest_tolerates_corruption(tmp_path):
    (tmp_path / store.MANIFEST_NAME).write_text("{not json")
    assert store.read_manifest(tmp_path) is None


def test_incomplete_when_a_recorded_file_is_missing(tmp_path):
    record = {"status": "complete", "_directory": str(tmp_path),
              "fulltext": {"path": "fulltext.pdf"}, "supplementary": []}
    assert not store.manifest_is_complete(record)
    (tmp_path / "fulltext.pdf").write_bytes(b"%PDF")
    assert store.manifest_is_complete(record)


# -- store: size budget ------------------------------------------------------

def _article(root, slug, when, size):
    directory = root / slug
    (directory / "supplementary").mkdir(parents=True)
    (directory / "fulltext.pdf").write_bytes(b"x" * size)
    store.write_manifest(directory, {"doi": f"10.1/{slug}", "status": "complete",
                                     "fetched_at": when,
                                     "fulltext": {"path": "fulltext.pdf"},
                                     "supplementary": []})
    return directory


def test_corpus_usage_is_oldest_first(tmp_path):
    _article(tmp_path, "b", "2026-02-01T00:00:00Z", 200)
    _article(tmp_path, "a", "2026-01-01T00:00:00Z", 100)
    usage = store.corpus_usage(tmp_path)
    assert [e["slug"] for e in usage] == ["a", "b"]
    assert usage[0]["bytes"] > 100  # includes the manifest


def test_enforce_budget_evicts_oldest_and_keeps_the_newest(tmp_path):
    _article(tmp_path, "old", "2026-01-01T00:00:00Z", 5000)
    _article(tmp_path, "new", "2026-03-01T00:00:00Z", 5000)
    outcome = store.enforce_budget(tmp_path, max_bytes=6000)
    assert [e["slug"] for e in outcome["evicted"]] == ["old"]
    assert not (tmp_path / "old" / "fulltext.pdf").exists()
    assert (tmp_path / "new" / "fulltext.pdf").exists(), "newest must survive"


def test_eviction_keeps_the_manifest_and_marks_it(tmp_path):
    """A corpus that forgets what it deleted is worse than one that never had it."""
    directory = _article(tmp_path, "x", "2026-01-01T00:00:00Z", 4000)
    freed = store.evict_article(directory)
    assert freed >= 4000
    record = store.read_manifest(directory)
    assert record["status"] == "evicted"
    assert record["evicted_at"] and record["evicted_bytes"] >= 4000
    assert record["fulltext"]["evicted"] is True
    assert (directory / store.MANIFEST_NAME).exists()


def test_evicted_articles_are_not_refetched(tmp_path):
    """Otherwise the next batch re-downloads everything the budget just freed and
    thrashes against the cap forever."""
    directory = _article(tmp_path, "x", "2026-01-01T00:00:00Z", 100)
    store.evict_article(directory)
    record = store.read_manifest(directory)
    record["_directory"] = str(directory)
    assert store.manifest_is_complete(record) is True


def test_dry_run_frees_nothing(tmp_path):
    _article(tmp_path, "old", "2026-01-01T00:00:00Z", 5000)
    _article(tmp_path, "new", "2026-03-01T00:00:00Z", 5000)
    outcome = store.enforce_budget(tmp_path, max_bytes=1000, dry_run=True)
    assert outcome["evicted"]
    assert (tmp_path / "old" / "fulltext.pdf").exists()


def test_budget_noop_when_under_or_unset(tmp_path):
    _article(tmp_path, "a", "2026-01-01T00:00:00Z", 100)
    assert store.enforce_budget(tmp_path, max_bytes=None)["evicted"] == []
    assert store.enforce_budget(tmp_path, max_bytes=10 ** 9)["evicted"] == []


def test_budget_reports_when_it_cannot_reach_the_target(tmp_path):
    _article(tmp_path, "only", "2026-01-01T00:00:00Z", 5000)
    outcome = store.enforce_budget(tmp_path, max_bytes=10)
    assert "note" in outcome  # the newest is never evicted


@pytest.mark.parametrize("count,expected", [(0, "0B"), (2048, "2.0KB"),
                                            (5 * 1024 ** 2, "5.0MB"),
                                            (3 * 1024 ** 3, "3.0GB")])
def test_human_bytes(count, expected):
    assert store.human_bytes(count) == expected


# -- PMC OA package ----------------------------------------------------------

def test_ftp_to_https():
    assert ftp_to_https("ftp://ftp.ncbi.nlm.nih.gov/pub/pmc/x.tar.gz") == \
        "https://ftp.ncbi.nlm.nih.gov/pub/pmc/x.tar.gz"
    assert ftp_to_https("https://already/x") == "https://already/x"


def test_package_members_split_by_kind():
    supplements, media, xml, pdf = _classify([
        ("PMC1/main.nxml", b"<article/>"),
        ("PMC1/gkr715.pdf", b"%PDF article"),
        ("PMC1/gkr715f1.jpg", b"\xff\xd8fig"),
        ("PMC1/gkr715_supp_table_s1.xlsx", b"xlsx"),
        ("PMC1/nar-MOESM2_ESM.pdf", b"%PDF supp"),
    ])
    assert xml[0].endswith("main.nxml")
    assert pdf[0].endswith("gkr715.pdf")
    assert [n for n, _ in media] == ["PMC1/gkr715f1.jpg"]
    assert sorted(n for n, _ in supplements) == [
        "PMC1/gkr715_supp_table_s1.xlsx", "PMC1/nar-MOESM2_ESM.pdf"]


def test_tar_cannot_escape_the_corpus_directory():
    unpacked = _unpack_tgz(make_tgz([("../../evil.txt", b"x"), ("/abs/evil2.txt", b"y")]),
                           max_files=10, max_file_bytes=1024)
    assert [n for n, _ in unpacked] == ["evil.txt", "evil2.txt"]


def test_tar_member_over_cap_raises():
    with pytest.raises(ValueError, match="over the"):
        _unpack_tgz(make_tgz([("big.bin", b"x" * 5000)]), max_files=10, max_file_bytes=100)


# -- adapters ----------------------------------------------------------------

def test_fragment_anchors_are_not_files():
    """Nature carries one `#MOESM<n>` anchor per supplement; treating those as
    downloads saved 26 copies of the article page."""
    article = "https://www.nature.com/articles/s41586-021-03852-1"
    assert url_without_fragment(article + "#MOESM4") == article
    assert not is_file_url(article + "#MOESM4")
    assert not looks_like_supplement({"url": article + "#MOESM4", "text": "4"})


def test_real_supplement_link_recognised():
    assert looks_like_supplement({
        "url": "https://static-content.springer.com/esm/art%3A10.1038%2Fx/"
               "MediaObjects/41586_2021_3852_MOESM1_ESM.pdf",
        "text": "Supplementary Information"})


def test_aaas_abbreviates_supplement_to_suppl():
    """10.1126/science.adt8307 serves its three supplements from
    `/doi/suppl/<doi>/suppl_file/<name>`. Requiring the whole word `supplement`
    made all three invisible and reported `unknown_none_found` for an article
    that has them."""
    base = "https://www-science-org.stanford.idm.oclc.org/doi/suppl/10.1126/science.adt8307"
    assert looks_like_supplement(
        {"url": f"{base}/suppl_file/science.adt8307_sm.pdf", "text": "Download"})
    assert looks_like_supplement(
        {"url": f"{base}/suppl_file/science.adt8307_tables_s1_to_s28.zip", "text": "Download"})


def test_elsevier_names_supplements_mmc_not_supplement():
    """10.1016/j.xgen.2026.101304. All twelve supplements are listed on the page,
    but eleven are captioned "Table S1. ..." or "Document S1. ..." -- no word the
    hint knew. Only `mmc12` matched, and only because its caption happens to read
    "Article plus supplemental information". One of twelve, reported `fetched`."""
    found = [link for link in CLINICALKEY_SUPPLEMENT_LINKS if looks_like_supplement(link)]
    assert len(found) == 12, "every mmc<n> is a supplement, caption or no caption"
    # The one that used to work must not have regressed.
    assert looks_like_supplement(CLINICALKEY_SUPPLEMENT_LINKS[-1])
    # Elsevier's other form, served straight off the CDN with no caption at all.
    assert looks_like_supplement({
        "url": "https://ars.els-cdn.com/content/image/1-s2.0-S2666979X26001667-mmc1.pdf",
        "text": ""})


@pytest.mark.parametrize("url", [
    # The article's own PDF, which the fallback in `find_pdf_url` must still reach.
    CLINICALKEY_ARTICLE_PDF,
    "https://www.sciencedirect.com/science/article/pii/S2666979X26001667/pdfft?download=true",
    "https://www-science-org.stanford.idm.oclc.org/doi/pdf/10.1126/science.adt8307",
    "https://www-science-org.stanford.idm.oclc.org/doi/10.1126/science.adt8307",
    # `suppl` is a prefix of ordinary words, which is why the path segment is
    # anchored rather than matched as a bare substring.
    "https://x.example/files/supplier-list.pdf",
    "https://x.example/supply/chain.pdf",
    "https://x.example/supplication.pdf",
])
def test_suppl_is_anchored_to_a_path_segment(url):
    assert not is_supplement_url(url)
    assert not looks_like_supplement({"url": url, "text": "Download PDF"})


def test_dedupe_by_target_collapses_fragments():
    links = [{"url": "https://x/a#1"}, {"url": "https://x/a#2"}, {"url": "https://x/b"}]
    assert [l["url"] for l in dedupe_by_target(links)] == ["https://x/a", "https://x/b"]


def test_nature_adapter_finds_files_not_anchors():
    article = "https://www.nature.com/articles/x"
    page = FakePage(url=article, metas={"citation_pdf_url": article + ".pdf"}, links=[
        {"url": article + "#MOESM4", "text": "4"},
        {"url": "https://static-content.springer.com/esm/art%3Ax/MediaObjects/a_MOESM1_ESM.pdf",
         "text": "Supplementary Information"},
        {"url": "https://static-content.springer.com/esm/art%3Ax/MediaObjects/a_MOESM2_ESM.xlsx",
         "text": "Supplementary Table 1"},
        {"url": article + "/metrics", "text": "Metrics"},
    ])
    adapter = adapter_for(article)
    assert adapter.find_pdf_url(page, DOI).endswith(".pdf")
    links, parsed = adapter.find_supplements(page, DOI)
    assert parsed and len(links) == 2
    assert all("MediaObjects" in l["url"] for l in links)


def test_empty_page_is_unparsed_not_empty():
    """`parsed=False` is what makes a publisher redesign loud instead of silent."""
    links, parsed = adapter_for("https://www.nature.com/articles/x").find_supplements(
        FakePage(links=[]), DOI)
    assert links == [] and parsed is False


def test_wiley_viewer_url_is_rewritten_to_pdfdirect():
    """`/doi/pdf/` is an HTML viewer -- 46 KB of text/html for 10.1002/path.5751."""
    page = FakePage(url="https://onlinelibrary-wiley-com.stanford.idm.oclc.org/doi/10.1002/path.5751",
                    metas={"citation_pdf_url":
                           "https://onlinelibrary-wiley-com.stanford.idm.oclc.org/doi/pdf/10.1002/path.5751"})
    url = WileyAdapter().find_pdf_url(page, "10.1002/path.5751")
    assert "/doi/pdfdirect/" in url and "/doi/pdf/" not in url
    # The proxied hostname must survive, or entitlement is lost.
    assert "stanford.idm.oclc.org" in url


def test_wiley_constructs_on_current_origin_without_a_meta_tag():
    page = FakePage(url="https://onlinelibrary-wiley-com.stanford.idm.oclc.org/doi/10.1002/x")
    url = WileyAdapter().find_pdf_url(page, "10.1002/x")
    assert url == ("https://onlinelibrary-wiley-com.stanford.idm.oclc.org"
                   "/doi/pdfdirect/10.1002/x")


def test_elsevier_constructs_pdfft_from_the_pii():
    """ScienceDirect pages carry no citation_pdf_url and no PDF href at all."""
    page = FakePage(
        url="https://www-sciencedirect-com.stanford.idm.oclc.org/science/article/pii/S1934590923004435?via%3Dihub")
    url = ElsevierAdapter().find_pdf_url(page, "10.1016/j.stem.2023.12.013")
    assert url.endswith("/pii/S1934590923004435/pdfft?isDTMRedir=true&download=true")
    assert "stanford.idm.oclc.org" in url


def test_elsevier_stub_detection():
    stub = FakePage(url="https://www-sciencedirect-com.x/science/article/pii/X",
                    title="ScienceDirect", links=[])
    real = FakePage(url="https://www-sciencedirect-com.x/science/article/pii/X",
                    title="A real article title",
                    links=[{"url": "https://x/a.pdf", "text": "View PDF"}])
    assert ElsevierAdapter().looks_blocked(stub) is True
    assert ElsevierAdapter().looks_blocked(real) is False


def test_generic_adapter_uses_citation_pdf_url():
    page = FakePage(url="https://journals.plos.org/plosone/article?id=1",
                    metas={"citation_pdf_url": "https://journals.plos.org/x.pdf"})
    assert adapter_for(page.url).name == "generic"
    assert adapter_for(page.url).find_pdf_url(page, DOI) == "https://journals.plos.org/x.pdf"


def _science_page() -> FakePage:
    """The AAAS page for 10.1126/science.adt8307: no citation_pdf_url, and every
    supplement anchor ahead of both article-PDF anchors."""
    return FakePage(
        url="https://www-science-org.stanford.idm.oclc.org/doi/10.1126/science.adt8307",
        metas={}, links=SCIENCE_ARTICLE_LINKS)


def test_pdf_fallback_never_returns_a_supplement():
    """10.1126/science.adt8307. With no `citation_pdf_url` the fallback takes the
    first `.pdf` anchor -- and on this page that is
    `/doi/suppl/<doi>/suppl_file/science.adt8307_sm.pdf`, so the Supplementary
    Materials PDF was written to `fulltext.pdf` and reported `ok` while the real
    19-page article was never fetched.

    An identity check does not catch this: the SM PDF carries the DOI as well, so
    it passed as "the right paper". Only 29 pages against 19 gave it away. Link
    order is the publisher's to change, so the exclusion has to be explicit."""
    page = _science_page()
    adapter = adapter_for(page.url)
    assert adapter.name == "generic"
    url = adapter.find_pdf_url(page, "10.1126/science.adt8307")
    assert "/suppl_file/" not in url and "_sm.pdf" not in url
    assert url == ("https://www-science-org.stanford.idm.oclc.org"
                   "/doi/pdf/10.1126/science.adt8307?download=true")


def test_generic_adapter_finds_the_aaas_supplements():
    """The same page's three real supplements, and nothing else: not the two
    article-PDF anchors, and not the in-page `#supplementary-materials` jump --
    which has the words but is a section of the page, not a file."""
    page = _science_page()
    links, parsed = adapter_for(page.url).find_supplements(page, "10.1126/science.adt8307")
    assert parsed
    assert [link["url"].rsplit("/", 1)[-1] for link in links] == [
        "science.adt8307_sm.pdf",
        "science.adt8307_tables_s1_to_s28.zip",
        "science.adt8307_mdar_reproducibility_checklist.pdf",
    ]


# -- HTTP politeness ---------------------------------------------------------

def test_user_agent_identifies_the_tool_and_contact():
    http = Http(contact_email="a@b.edu")
    assert "manuscript-harvest" in http._session.headers["User-Agent"]
    assert "a@b.edu" in http._session.headers["User-Agent"]


def test_ncbi_calls_carry_tool_and_email():
    """NCBI asks callers to identify themselves; other hosts get nothing extra."""
    http = Http(contact_email="a@b.edu", ncbi_api_key="key123")
    params = http._ncbi_params("https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/", {})
    assert params["tool"] == "manuscript-harvest"
    assert params["email"] == "a@b.edu" and params["api_key"] == "key123"
    assert http._ncbi_params("https://www.ebi.ac.uk/x", {"a": 1}) == {"a": 1}


def test_per_host_interval_is_enforced(monkeypatch):
    slept = []
    monkeypatch.setattr("manuscript_harvest.fetch.http.time.sleep", lambda s: slept.append(s))
    clock = {"t": 100.0}
    monkeypatch.setattr("manuscript_harvest.fetch.http.time.monotonic", lambda: clock["t"])

    http = Http(min_interval_seconds=3.0)
    http._wait_for_host("https://a.example/1")   # first call: no wait
    http._wait_for_host("https://a.example/2")   # same host: must wait
    http._wait_for_host("https://b.example/1")   # different host: no wait
    assert slept and slept[0] == pytest.approx(3.0)
    assert len(slept) == 1


# -- config ------------------------------------------------------------------

def test_merge_is_recursive_and_non_destructive():
    merged = _merge({"a": 1, "b": {"c": 2, "d": 3}}, {"b": {"c": 9}})
    assert merged == {"a": 1, "b": {"c": 9, "d": 3}}


def test_load_config_fills_fetch_defaults(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("llm:\n  model: x\nfetch:\n  corpus_dir: mine\n")
    config = load_config(path)
    assert config["fetch"]["corpus_dir"] == "mine"
    assert config["fetch"]["proxy"]["prefix"] == DEFAULT_FETCH_CONFIG["proxy"]["prefix"]
    assert config["llm"]["model"] == "x"


def test_load_config_survives_a_missing_file(tmp_path):
    config = load_config(tmp_path / "nope.yaml")
    assert config["fetch"]["corpus_dir"] == "corpus"
