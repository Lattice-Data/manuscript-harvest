"""Offline end-to-end self-test for the acquisition stage (no network, no browser).

Mirrors the mock style of `selftest.py`: a fake HTTP client and a fake browser page
stand in for the real services, with fixtures built from responses observed against
the live APIs.

The checks that matter most are the ones about *honesty*, not happy paths:

- a paywall page served as `application/pdf` must be rejected, not saved
- `hasSuppl=Y` with nothing retrieved must report `expected_but_missing`, never
  `none_listed`
- an unreadable supplement section must report `page_not_parsed`, never zero files
- a `max_files` cap must be recorded, never silently truncate

    python selftest_fetch.py
"""

import io
import json
import tarfile
import tempfile
import zipfile
from pathlib import Path

import fitz

from curation.fetch import fetcher, store
from curation.fetch.adapters import adapter_for
from curation.fetch.adapters.base import looks_like_supplement, url_without_fragment
from curation.fetch.http import Response
from curation.fetch.identifiers import Identifiers, doi_slug, normalize_doi
from curation.fetch.sources.pmc_oa import _classify, ftp_to_https
from curation.fetch.validate import classify_denial, validate_pdf

DOI = "10.1038/s41586-021-03852-1"
PMCID = "PMC8426186"


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

def make_pdf(pages=3, text="Methods. TP53 knockout was generated using CRISPR-Cas9. " * 20):
    doc = fitz.open()
    for _ in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), text, fontsize=9)
    data = doc.tobytes()
    doc.close()
    return data


def make_scanned_pdf(pages=2):
    """A PDF that parses but carries essentially no extractable text."""
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page()
    data = doc.tobytes()
    doc.close()
    return data


def make_paywall_pdf():
    """The short stub some publishers serve instead of the article."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Purchase this article to view the full text.", fontsize=12)
    data = doc.tobytes()
    doc.close()
    return data


PAYWALL_HTML = (
    b"<html><body><h1>Access Denied</h1>"
    b"<p>Please sign in to read the full article, or purchase this article.</p>"
    b"</body></html>"
)

EZPROXY_HTML = (
    b"<html><body><h1>Oops!</h1><p>It looks like you have attempted to view a page "
    b"that has not been configured for access.</p></body></html>"
)

POW_HTML = (
    b"<html><head><title>Preparing to download ...</title></head>"
    b"<body><script src='/assets/pow-o51sQKbL.js'></script></body></html>"
)

SSO_HTML = b"<html><body><h1>Stanford Login</h1><p>Enter your SUNet ID</p></body></html>"


def europepmc_search_json(**overrides):
    record = {
        "id": "34497389", "source": "MED", "pmid": "34497389", "pmcid": PMCID,
        "doi": DOI, "title": "A test article", "pubYear": 2021,
        "journalInfo": {"journal": {"title": "Nature"}},
        "isOpenAccess": "Y", "inEPMC": "Y", "inPMC": "Y",
        "hasPDF": "Y", "hasSuppl": "Y", "license": "cc by",
        "fullTextUrlList": {"fullTextUrl": [
            {"availability": "Open access", "availabilityCode": "OA",
             "documentStyle": "pdf", "site": "Europe_PMC",
             "url": "https://example.org/article.pdf"},
        ]},
    }
    record.update(overrides)
    return json.dumps({"hitCount": 1, "resultList": {"result": [record]}}).encode()


OA_XML_OK = (
    b'<OA><records returned-count="1"><record id="' + PMCID.encode() + b'" license="CC BY">'
    b'<link format="tgz" href="ftp://ftp.ncbi.nlm.nih.gov/pub/pmc/oa_package/34/e8/'
    + PMCID.encode() + b'.tar.gz" />'
    b'</record></records></OA>'
)

OA_XML_ERROR = (
    b'<OA><error code="idIsNotOpenAccess">identifier is not Open Access</error></OA>'
)


def make_zip(names_and_bytes):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in names_and_bytes:
            archive.writestr(name, content)
    return buffer.getvalue()


def make_tgz(names_and_bytes):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, content in names_and_bytes:
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


class FakeHttp:
    """Routes URLs to canned responses by substring, and records the calls."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, params=None, accept=None, allow_redirects=True):
        self.calls.append(url)
        for fragment, response in self.routes.items():
            if fragment in url:
                status, content, content_type = response
                return Response(url=url, status=status, content=content,
                                content_type=content_type)
        return Response(url=url, status=404, content=b"", content_type="")

    def resolve_redirect(self, url):
        return url


class FakePage:
    """The slice of the Playwright page API the adapters actually use."""

    def __init__(self, metas=None, links=None):
        self.metas = metas or {}
        self.links = links or []
        self.url = "https://www.nature.com/articles/x"

    def get_attribute(self, selector, attribute, timeout=None):
        for name, value in self.metas.items():
            if f'name="{name}"' in selector:
                return value
        raise RuntimeError(f"no element for {selector}")

    def eval_on_selector_all(self, selector, script):
        return list(self.links)


def base_config(corpus_dir, tiers):
    return {"fetch": {"corpus_dir": str(corpus_dir), "tiers": tiers,
                      "min_interval_seconds": 0, "max_files": 50}}


# --------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------

def check_doi_normalisation():
    for raw in [DOI, DOI.upper(), f"https://doi.org/{DOI}", f"doi:{DOI}",
                f"  https://dx.doi.org/{DOI}  ", f"{DOI}."]:
        assert normalize_doi(raw) == DOI, f"normalize_doi failed for {raw!r}"

    assert doi_slug(DOI) == "10.1038_s41586-021-03852-1", doi_slug(DOI)
    # Distinct DOIs must never collide on one directory, even when very long.
    long_a = "10.1234/" + "a" * 300
    long_b = "10.1234/" + "a" * 299 + "b"
    assert doi_slug(long_a) != doi_slug(long_b), "long DOIs collided"
    assert len(doi_slug(long_a)) <= 150
    assert "/" not in doi_slug("10.1234/a/b/c")

    for bad in ["", "not a doi", "10.x/y", "https://example.org/paper"]:
        try:
            normalize_doi(bad)
        except ValueError:
            continue
        raise AssertionError(f"normalize_doi accepted {bad!r}")
    print("  ok  DOI normalisation, slugs, and rejection of non-DOIs")


def check_version_suffix_fallback(tmp):
    """A versioned DOI must fall back to its unversioned form -- and only it.

    eLife reviewed preprints (10.7554/eLife.104978.2) are frequently absent from
    indexes while the unversioned DOI is present. The danger is over-stripping:
    10.1016/j.cell.2021.01.053 also ends in dot-digits and must be left alone.
    """
    from curation.fetch.identifiers import unversioned_doi

    assert unversioned_doi("10.7554/elife.104978.2") == "10.7554/elife.104978"
    for keep in ["10.1016/j.cell.2021.01.053", "10.1101/2025.07.21.666016",
                 "10.1126/science.aax6234", "10.1182/bloodadvances.2023011445"]:
        assert unversioned_doi(keep) is None, f"over-stripped {keep}"

    # End to end: the versioned DOI 404s, the base resolves, and the corpus
    # directory still uses the DOI the caller asked for.
    versioned = "10.7554/elife.104978.2"
    corpus = tmp / "versioned"
    calls = []

    class VersionAwareHttp(FakeHttp):
        def get(self, url, params=None, accept=None, allow_redirects=True):
            query = (params or {}).get("query", "")
            calls.append(query)
            if "/webservices/rest/search" in url:
                if "elife.104978.2" in query:      # versioned: no record
                    body = json.dumps({"hitCount": 0, "resultList": {"result": []}}).encode()
                else:                               # unversioned: found
                    body = europepmc_search_json(doi="10.7554/elife.104978", hasSuppl="N")
                return Response(url=url, status=200, content=body,
                                content_type="application/json")
            return super().get(url, params, accept, allow_redirects)

    http = VersionAwareHttp({
        "example.org/article.pdf": (200, make_pdf(), "application/pdf"),
        "/fullTextXML": (404, b"", ""),
    })
    record = fetcher.fetch_publication(versioned, base_config(corpus, ["europepmc"]), http=http)
    assert record["identifiers"]["lookup_doi"] == "10.7554/elife.104978", record["identifiers"]
    assert record["fulltext"]["status"] == "ok", record["fulltext"]
    assert record["slug"] == "10.7554_elife.104978.2", record["slug"]
    assert any("unversioned DOI" in p for p in record["problems"]), record["problems"]
    print("  ok  versioned DOI falls back to unversioned (and article numbers are not stripped)")


def check_filename_sanitisation():
    assert store.sanitize_filename("a b/c.xlsx") == "c.xlsx"
    assert store.sanitize_filename("../../etc/passwd") == "passwd"
    assert store.sanitize_filename("file.pdf?download=true") == "file.pdf"
    assert store.sanitize_filename("données_supplémentaires.csv").endswith(".csv")
    assert store.sanitize_filename("") == "file"
    assert store.sanitize_filename("/") == "file"
    long_name = "x" * 400 + ".xlsx"
    cleaned = store.sanitize_filename(long_name)
    assert len(cleaned) <= 120 and cleaned.endswith(".xlsx"), cleaned
    assert store.supplement_filename(3, "t.xls") == "03_t.xls"
    print("  ok  filename sanitisation (traversal, query strings, unicode, length)")


def check_denial_detection():
    # An HTML paywall served with a PDF content type must not be accepted.
    accepted, status, _ = validate_pdf(PAYWALL_HTML * 20, content_type="application/pdf",
                                       url="https://publisher.example/article")
    assert not accepted and status == "paywalled", status

    accepted, status, _ = validate_pdf(EZPROXY_HTML * 20, content_type="text/html", url="x")
    assert not accepted and status == "proxy_not_configured", status

    accepted, status, _ = validate_pdf(SSO_HTML * 20, content_type="text/html",
                                       url="https://login.stanford.edu/idp")
    assert not accepted and status == "session_expired", status

    # A real but tiny "purchase this article" PDF stub.
    accepted, status, _ = validate_pdf(make_paywall_pdf(), content_type="application/pdf", url="x")
    assert not accepted and status == "paywalled", status

    # A genuine article PDF passes.
    accepted, status, meta = validate_pdf(make_pdf(), content_type="application/pdf", url="x")
    assert accepted and status == "ok", (status, meta)

    # A scanned PDF is kept but flagged, because pdf_loader will get nothing from it.
    accepted, status, _ = validate_pdf(make_scanned_pdf(), content_type="application/pdf", url="x")
    assert accepted and status == "scanned_pdf_suspected", status

    # Truncated / empty downloads.
    accepted, status, _ = validate_pdf(b"", url="x")
    assert not accepted and status == "download_failed", status
    accepted, status, _ = validate_pdf(b"%PDF-1.4 but truncated" * 100, url="x")
    assert not accepted and status == "not_a_pdf", status

    assert classify_denial("x", POW_HTML) == "javascript_challenge"
    print("  ok  denial detection (paywall-as-PDF, stub PDF, EZproxy, SSO, JS challenge)")


def check_adapter_selection_and_links():
    assert adapter_for("https://www.nature.com/articles/x").name == "nature"
    assert adapter_for("https://onlinelibrary.wiley.com/doi/10.1/x").name == "wiley"
    assert adapter_for("https://www.sciencedirect.com/science/article/pii/X").name == "elsevier"
    assert adapter_for("https://pmc.ncbi.nlm.nih.gov/articles/PMC1/").name == "pmc"
    assert adapter_for("https://journals.plos.org/plosone/article?id=1").name == "generic"

    # The regression this guards: `#MOESM4` anchors are page fragments, not files.
    article = "https://www.nature.com/articles/s41586-021-03852-1"
    assert url_without_fragment(article + "#MOESM4") == article
    assert not looks_like_supplement({"url": article + "#MOESM4", "text": "1"})
    assert looks_like_supplement(
        {"url": "https://static-content.springer.com/esm/art%3A10.1038%2Fx/"
                "MediaObjects/41586_2021_3852_MOESM1_ESM.pdf", "text": "Supplementary Information"}
    )

    page = FakePage(
        metas={"citation_pdf_url": "https://www.nature.com/articles/x.pdf"},
        links=[
            {"url": article + "#MOESM4", "text": "4"},
            {"url": article + "#MOESM5", "text": "5"},
            {"url": "https://static-content.springer.com/esm/art%3Ax/MediaObjects/"
                    "41586_2021_3852_MOESM1_ESM.pdf", "text": "Supplementary Information"},
            {"url": "https://static-content.springer.com/esm/art%3Ax/MediaObjects/"
                    "41586_2021_3852_MOESM2_ESM.xlsx", "text": "Supplementary Table 1"},
            {"url": "https://www.nature.com/articles/x/metrics", "text": "Metrics"},
        ],
    )
    adapter = adapter_for("https://www.nature.com/articles/x")
    assert adapter.find_pdf_url(page, DOI).endswith(".pdf")
    links, parsed = adapter.find_supplements(page, DOI)
    assert parsed is True
    urls = [item["url"] for item in links]
    assert len(urls) == 2, urls          # the two real files, not the anchors
    assert all("MediaObjects" in u for u in urls), urls

    # An empty page means "could not read", which must not look like "none exist".
    links, parsed = adapter.find_supplements(FakePage(), DOI)
    assert parsed is False and links == [], (links, parsed)
    print("  ok  adapter selection; fragment anchors excluded; page_not_parsed distinguished")


def check_pmc_oa_helpers():
    assert ftp_to_https(
        "ftp://ftp.ncbi.nlm.nih.gov/pub/pmc/oa_package/34/e8/x.tar.gz"
    ) == "https://ftp.ncbi.nlm.nih.gov/pub/pmc/oa_package/34/e8/x.tar.gz"

    members = [
        ("PMC1/main.nxml", b"<article/>"),
        ("PMC1/gkr715.pdf", b"%PDF-1.4 article"),
        ("PMC1/gkr715f1.jpg", b"\xff\xd8image"),
        ("PMC1/gkr715_supp_table_s1.xlsx", b"xlsx-bytes"),
        ("PMC1/nar-01234-MOESM2_ESM.pdf", b"%PDF supp"),
    ]
    supplements, media, xml_member, pdf_member = _classify(members)
    assert xml_member[0] == "PMC1/main.nxml"
    assert pdf_member[0] == "PMC1/gkr715.pdf", pdf_member
    assert [n for n, _ in media] == ["PMC1/gkr715f1.jpg"], media
    supplement_names = sorted(n for n, _ in supplements)
    assert supplement_names == [
        "PMC1/gkr715_supp_table_s1.xlsx", "PMC1/nar-01234-MOESM2_ESM.pdf"
    ], supplement_names

    # A tarball must not be able to write outside the corpus directory.
    from curation.fetch.sources.pmc_oa import _unpack_tgz
    unpacked = _unpack_tgz(make_tgz([("../../evil.txt", b"x"), ("/abs/also_evil.txt", b"y")]),
                           max_files=10, max_file_bytes=1024)
    assert [n for n, _ in unpacked] == ["evil.txt", "also_evil.txt"], unpacked
    print("  ok  ftp->https rewrite, package classification, tar path-traversal guard")


def check_happy_path(tmp):
    corpus = tmp / "happy"
    http = FakeHttp({
        "/webservices/rest/search": (200, europepmc_search_json(), "application/json"),
        "/fullTextXML": (200, b"<article><body/></article>", "application/xml"),
        "/supplementaryFiles": (200, make_zip([
            ("41586_2021_3852_MOESM1_ESM.pdf", b"%PDF supp one"),
            ("41586_2021_3852_MOESM2_ESM.xlsx", b"xlsx supp two"),
        ]), "application/zip"),
        "example.org/article.pdf": (200, make_pdf(), "application/pdf"),
    })
    record = fetcher.fetch_publication(
        DOI, base_config(corpus, ["europepmc"]), http=http
    )
    assert record["status"] == "complete", record["status"]
    assert record["fulltext"]["status"] == "ok"
    assert record["supplementary_status"] == "fetched"
    assert len(record["supplementary"]) == 2, record["supplementary"]
    assert record["fulltext_xml"]["path"] == "fulltext.nxml"
    assert record["identifiers"]["pmcid"] == PMCID

    directory = Path(record["_directory"])
    assert (directory / "fulltext.pdf").exists()
    assert (directory / "supplementary").is_dir()
    names = sorted(p.name for p in (directory / "supplementary").iterdir())
    assert names == ["01_41586_2021_3852_MOESM1_ESM.pdf",
                     "02_41586_2021_3852_MOESM2_ESM.xlsx"], names

    # Manifest round-trips and the provenance survives.
    on_disk = store.read_manifest(directory)
    assert on_disk["fulltext"]["tier"] == "europepmc"
    assert on_disk["supplementary"][0]["sha256"]

    # Idempotence: a second call must not re-request anything.
    calls_before = len(http.calls)
    again = fetcher.fetch_publication(DOI, base_config(corpus, ["europepmc"]), http=http)
    assert again.get("cached") is True, "second fetch was not served from cache"
    assert len(http.calls) == calls_before, "cached fetch still hit the network"

    # --force must re-fetch.
    forced = fetcher.fetch_publication(
        DOI, base_config(corpus, ["europepmc"]), force=True, http=http
    )
    assert not forced.get("cached") and len(http.calls) > calls_before
    print("  ok  happy path: PDF + XML + 2 supplements, manifest, idempotence, --force")


def check_expected_but_missing(tmp):
    """hasSuppl=Y and nothing retrieved is the case the taxonomy exists for."""
    corpus = tmp / "missing"
    http = FakeHttp({
        "/webservices/rest/search": (200, europepmc_search_json(), "application/json"),
        "example.org/article.pdf": (200, make_pdf(), "application/pdf"),
        "/supplementaryFiles": (404, b"", ""),          # Europe PMC has no archive
        "/fullTextXML": (404, b"", ""),
    })
    record = fetcher.fetch_publication(DOI, base_config(corpus, ["europepmc"]), http=http)
    assert record["supplementary_status"] == "expected_but_missing", record["supplementary_status"]
    assert record["status"] == "partial", record["status"]
    assert record["fulltext"]["status"] == "ok"
    print("  ok  hasSuppl=Y with nothing retrieved -> expected_but_missing (not none_listed)")


def check_none_listed(tmp):
    """hasSuppl=N must be reported as a fact, and must not be probed for."""
    corpus = tmp / "nosuppl"
    http = FakeHttp({
        "/webservices/rest/search":
            (200, europepmc_search_json(hasSuppl="N"), "application/json"),
        "example.org/article.pdf": (200, make_pdf(), "application/pdf"),
        "/fullTextXML": (404, b"", ""),
    })
    record = fetcher.fetch_publication(DOI, base_config(corpus, ["europepmc"]), http=http)
    assert record["supplementary_status"] == "none_listed", record["supplementary_status"]
    assert record["status"] == "complete", record["status"]
    assert not any("supplementaryFiles" in url for url in http.calls), \
        "asked for supplements even though hasSuppl=N"
    print("  ok  hasSuppl=N -> none_listed, and the endpoint is not called at all")


def check_unknown_supplements(tmp):
    """No record anywhere: we must not claim there are no supplements."""
    corpus = tmp / "unknown"
    http = FakeHttp({
        "/webservices/rest/search":
            (200, json.dumps({"hitCount": 0, "resultList": {"result": []}}).encode(),
             "application/json"),
        "api.crossref.org": (200, json.dumps({"message": {
            "publisher": "Test", "title": ["T"], "container-title": ["J"],
            "resource": {"primary": {"URL": "https://publisher.example/article"}}}}).encode(),
         "application/json"),
    })
    record = fetcher.fetch_publication(DOI, base_config(corpus, ["europepmc"]), http=http)
    assert record["supplementary_status"] == "unknown_none_found", record["supplementary_status"]
    assert record["status"] == "failed", record["status"]
    assert record["fulltext"]["status"] in {"not_found", "download_failed"}
    print("  ok  no metadata anywhere -> unknown_none_found + failed (never a false 'none')")


def check_not_in_oa_subset(tmp):
    """oa.fcgi <error> is routing information, and must fall through cleanly."""
    corpus = tmp / "authorms"
    http = FakeHttp({
        "/webservices/rest/search":
            (200, europepmc_search_json(hasPDF="N", inEPMC="N", hasSuppl="N"), "application/json"),
        "oa.fcgi": (200, OA_XML_ERROR, "application/xml"),
        "/fullTextXML": (404, b"", ""),
    })
    record = fetcher.fetch_publication(
        DOI, base_config(corpus, ["europepmc", "pmc_oa"]), http=http
    )
    assert "pmc_oa" in record["tiers_tried"], record["tiers_tried"]
    assert record["fulltext"]["status"] == "not_in_oa_subset", record["fulltext"]["status"]
    assert any("not in the PMC Open Access subset" in p for p in record["problems"]), \
        record["problems"]
    print("  ok  oa.fcgi <error> -> not_in_oa_subset, recorded and fallen through")


def check_paywall_not_saved(tmp):
    """A refusal must never be written to disk as fulltext.pdf."""
    corpus = tmp / "paywalled"
    http = FakeHttp({
        "/webservices/rest/search":
            (200, europepmc_search_json(hasSuppl="N"), "application/json"),
        "example.org/article.pdf": (200, PAYWALL_HTML * 20, "application/pdf"),
        "/fullTextXML": (404, b"", ""),
    })
    record = fetcher.fetch_publication(DOI, base_config(corpus, ["europepmc"]), http=http)
    assert record["fulltext"]["status"] == "paywalled", record["fulltext"]["status"]
    assert record["fulltext"]["path"] is None
    assert record["status"] == "failed"
    assert not (Path(record["_directory"]) / "fulltext.pdf").exists(), \
        "a paywall page was saved as fulltext.pdf"
    print("  ok  paywall response rejected and NOT written as fulltext.pdf")


def check_cap_is_reported(tmp):
    """A max_files cap must be recorded, not silently applied."""
    corpus = tmp / "capped"
    many = [(f"supp_{i:02d}.xlsx", f"contents of file {i}".encode()) for i in range(12)]
    http = FakeHttp({
        "/webservices/rest/search": (200, europepmc_search_json(), "application/json"),
        "example.org/article.pdf": (200, make_pdf(), "application/pdf"),
        "/supplementaryFiles": (200, make_zip(many), "application/zip"),
        "/fullTextXML": (404, b"", ""),
    })
    config = base_config(corpus, ["europepmc"])
    config["fetch"]["max_files"] = 5
    record = fetcher.fetch_publication(DOI, config, http=http)
    assert len(record["supplementary"]) == 5, len(record["supplementary"])
    capped = [a for a in record["attempts"]
              if a.get("action") == "supplements" and a.get("count") == 5]
    assert capped, "the cap was applied without recording how many files were taken"
    print("  ok  max_files cap applied and recorded (5 of 12)")


def check_later_tier_rescues(tmp):
    """An earlier tier failing must not poison a later tier's success.

    Regression guard: the status used to OR every tier's verdict together, so a
    complete set of supplements still reported `partial_failure` because an
    earlier route had failed first.
    """
    from curation.fetch.fetcher import _supplement_status

    ids = Identifiers(doi=DOI, doi_raw=DOI, has_suppl=True)
    # tier A failed, tier B fetched everything it attempted
    assert _supplement_status(ids, True, 4, ["partial_failure", "fetched"]) == "fetched"
    assert _supplement_status(ids, True, 4, ["page_not_parsed", "fetched"]) == "fetched"
    # nobody reported a clean fetch, so partial is the honest answer
    assert _supplement_status(ids, True, 2, ["partial_failure"]) == "partial_failure"
    # and nothing retrieved at all is still the bug case
    assert _supplement_status(ids, True, 0, ["partial_failure"]) == "expected_but_missing"
    print("  ok  a later tier's success is not poisoned by an earlier tier's failure")


def check_preprint_hassuppl_not_trusted(tmp):
    """hasSuppl=N is authoritative for journal articles, but NOT for preprints.

    Regression guard for real silent data loss: Europe PMC reports hasSuppl=N for
    10.1101/2025.07.21.666016, whose bioRxiv page carries media-1.pdf and
    media-2.zip (72 MB together). Trusting the flag reported a confident
    `none_listed` and dropped both files.
    """
    from curation.fetch.fetcher import _supplement_status

    journal = Identifiers(doi=DOI, doi_raw=DOI, has_suppl=False)
    preprint = Identifiers(doi="10.1101/2025.07.21.666016",
                           doi_raw="10.1101/2025.07.21.666016", has_suppl=False)
    assert preprint.is_preprint and not journal.is_preprint

    # An indexed journal article may be taken at its word.
    assert _supplement_status(journal, True, 0, []) == "none_listed"
    # A preprint may not: the flag must never produce a bare "none_listed".
    assert _supplement_status(preprint, True, 0, []) == "unknown_none_found"
    # ...but the preprint's own server IS authoritative when it says none.
    assert _supplement_status(preprint, True, 0, ["none_listed"]) == "none_listed"
    # ...and files found despite hasSuppl=N are simply fetched.
    assert _supplement_status(preprint, True, 2, ["fetched"]) == "fetched"
    print("  ok  preprints are checked at source even when the index says hasSuppl=N")


def check_dedup(tmp):
    """Same name AND same bytes is a duplicate. Same bytes alone is not.

    Distinct supplements legitimately share content (empty templates, repeated
    controls). Deduplicating on bytes alone would silently drop one of them, so the
    key is (bytes, name) and a rare duplicate file is the accepted cost.
    """
    corpus = tmp / "dedup"
    archive = make_zip([
        ("shared.xlsx", b"identical-bytes"),
        ("shared.xlsx", b"identical-bytes"),      # true duplicate -> collapses
        ("other_name.xlsx", b"identical-bytes"),  # same bytes, different file -> kept
    ])
    http = FakeHttp({
        "/webservices/rest/search": (200, europepmc_search_json(), "application/json"),
        "example.org/article.pdf": (200, make_pdf(), "application/pdf"),
        "/supplementaryFiles": (200, archive, "application/zip"),
        "/fullTextXML": (404, b"", ""),
    })
    record = fetcher.fetch_publication(DOI, base_config(corpus, ["europepmc"]), http=http)
    names = sorted(e["original_name"] for e in record["supplementary"])
    assert names == ["other_name.xlsx", "shared.xlsx"], names
    print("  ok  dedup on (bytes, name): true duplicate collapsed, same-bytes file kept")


def main():
    with tempfile.TemporaryDirectory() as raw_tmp:
        tmp = Path(raw_tmp)
        print("acquisition self-test (offline)")
        check_doi_normalisation()
        check_version_suffix_fallback(tmp)
        check_filename_sanitisation()
        check_denial_detection()
        check_adapter_selection_and_links()
        check_pmc_oa_helpers()
        check_happy_path(tmp)
        check_expected_but_missing(tmp)
        check_none_listed(tmp)
        check_unknown_supplements(tmp)
        check_not_in_oa_subset(tmp)
        check_paywall_not_saved(tmp)
        check_cap_is_reported(tmp)
        check_later_tier_rescues(tmp)
        check_preprint_hassuppl_not_trusted(tmp)
        check_dedup(tmp)
        print("SELFTEST_FETCH PASSED")


if __name__ == "__main__":
    main()
