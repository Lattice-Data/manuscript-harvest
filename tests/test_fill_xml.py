"""Filling in the JATS for articles fetched before the XML was a need of its own.

What these defend is the narrowness of the pass: it writes `fulltext.nxml` and its
manifest entry and nothing else. 79 of this corpus's 330 PMC articles were the case
-- PDF and supplements complete, JATS absent -- and a `--force` re-fetch would have
re-run the browser for 55 of them.
"""

from manuscript_harvest.fetch import cli, fill_xml, store
from manuscript_harvest.fetch.fill_xml import (
    fill_xml_article,
    fill_xml_corpus,
    identifiers_from_record,
    summarize,
    xml_sources,
)
from manuscript_harvest.fetch.sources.pmc_s3 import PmcS3Source
from tests.fakes import DOI, PMCID, jats_article, make_article, make_pdf, s3_http

XML_KEY = f"{PMCID}.1/{PMCID}.1.xml"
TITLE = "A test article about islets"


def _article(root, name="art", xml=None, pmcid=PMCID, title=TITLE):
    """A complete article -- PDF, one supplement -- with or without a JATS."""
    directory = make_article(root / name, fulltext=make_pdf(), xml=xml,
                             supplements=[("table-s1.xlsx", b"xlsx bytes")])
    record = store.read_manifest(directory)
    record["identifiers"] = {"doi": DOI, "pmcid": pmcid, "title": title}
    record["supplementary_status"] = "fetched"
    store.write_manifest(directory, record)
    return directory


def _bucket(xml=None, deposit=None):
    """An S3 fake whose deposit holds `xml`: a JATS carrying this DOI by default."""
    body = jats_article() if xml is None else xml
    http = s3_http(deposit=[(XML_KEY, len(body))] if deposit is None else deposit,
                   routes={".xml": (200, body, "application/xml")})
    return http, [PmcS3Source(http, {"max_file_mb": 500, "max_files": 300})]


def test_an_article_with_a_jats_is_left_alone_and_costs_nothing(tmp_path):
    directory = _article(tmp_path, xml=jats_article())
    http, sources = _bucket()

    report = fill_xml_article(directory, sources, apply=True)

    assert report["candidate"] is False and report["note"] == "has a JATS"
    assert http.calls == []


def test_report_only_names_the_jats_and_writes_nothing(tmp_path):
    directory = _article(tmp_path)
    before = (directory / "manifest.json").read_bytes()
    http, sources = _bucket()

    report = fill_xml_article(directory, sources, apply=False)

    assert report["candidate"] and report["filled"]
    assert report["tier"] == "pmc_s3" and report["status"] == "ok"
    assert http.called_matching(f"{PMCID}.1.xml") == 1, "the report costs the fetch"
    assert not (directory / store.FULLTEXT_XML).exists()
    assert (directory / "manifest.json").read_bytes() == before


def test_apply_writes_the_jats_and_touches_nothing_else(tmp_path):
    directory = _article(tmp_path)
    before = store.read_manifest(directory)
    http, sources = _bucket()

    report = fill_xml_article(directory, sources, apply=True)
    after = store.read_manifest(directory)

    assert report["filled"]
    assert (directory / store.FULLTEXT_XML).read_bytes() == jats_article()
    entry = after["fulltext_xml"]
    assert entry["path"] == store.FULLTEXT_XML and entry["tier"] == "pmc_s3"
    assert entry["status"] == "ok"
    assert entry["sha256"] == store.sha256_bytes(jats_article())
    assert after["xml_filled"]["tier"] == "pmc_s3"
    assert after["xml_filled"]["tiers_tried"] == ["pmc_s3"]
    assert any(a.get("tier") == "pmc_s3" for a in after["attempts"]), \
        "the tier's own notes travel with the entry"
    # Everything that is not the JATS is byte-for-byte what it was.
    for key in ("fulltext", "supplementary", "supplementary_status", "status",
                "identifiers", "doi", "fetched_at"):
        assert after.get(key) == before.get(key), key
    assert store.sha256_file(directory / store.FULLTEXT_PDF) == before["fulltext"]["sha256"]
    assert http.called_matching(f"{PMCID}.1.pdf") == 0


def test_a_second_pass_finds_nothing_to_do(tmp_path):
    directory = _article(tmp_path)
    http, sources = _bucket()
    fill_xml_article(directory, sources, apply=True)
    calls = len(http.calls)

    report = fill_xml_article(directory, sources, apply=True)

    assert report["note"] == "has a JATS" and len(http.calls) == calls


def test_a_deposit_without_a_jats_is_reported_not_written(tmp_path):
    directory = _article(tmp_path)
    before = (directory / "manifest.json").read_bytes()
    http, sources = _bucket(deposit=[(f"{PMCID}.1/{PMCID}.1.pdf", 100)])

    report = fill_xml_article(directory, sources, apply=True)

    assert report["candidate"] and not report["filled"]
    assert report["note"] == "no JATS at pmc_s3"
    assert (directory / "manifest.json").read_bytes() == before


def test_an_article_no_tier_can_serve_costs_no_request(tmp_path):
    directory = _article(tmp_path, pmcid=None)
    http, sources = _bucket()

    report = fill_xml_article(directory, sources, apply=True)

    assert report["candidate"] and not report["filled"]
    assert report["tiers_tried"] == [] and "no PMCID" in report["note"]
    assert http.calls == []


def test_an_evicted_article_keeps_its_record(tmp_path):
    directory = _article(tmp_path)
    record = store.read_manifest(directory)
    record["status"] = "evicted"
    store.write_manifest(directory, record)
    http, sources = _bucket()

    report = fill_xml_article(directory, sources, apply=True)

    assert not report["candidate"] and "evicted" in report["note"]
    assert http.calls == []


def test_a_jats_that_is_a_notice_is_stored_with_its_own_status(tmp_path):
    """The PDF's verdict is `revalidate`'s to change: this pass never read the PDF."""
    directory = _article(tmp_path)
    http, sources = _bucket(xml=jats_article(article_type="correction"))

    report = fill_xml_article(directory, sources, apply=True)
    after = store.read_manifest(directory)

    assert report["status"] == "not_research_article"
    assert after["fulltext_xml"]["status"] == "not_research_article"
    assert any("revalidate" in line for line in after["problems"])
    assert after["fulltext"]["status"] == "ok"


def test_a_jats_for_another_paper_is_flagged_not_trusted(tmp_path):
    directory = _article(tmp_path, title="TP53 knockout via CRISPR-Cas9")
    other = jats_article(body="<p>" + "Hepatocyte zonation along the lobule. " * 20 + "</p>")
    other = other.replace(DOI.encode(), b"10.9999/another.paper")
    http, sources = _bucket(xml=other)

    report = fill_xml_article(directory, sources, apply=True)

    assert report["status"] == "identity_unverified"
    assert any("not the requested article" in line for line in report["problems"])
    assert store.read_manifest(directory)["fulltext_xml"]["status"] == "identity_unverified"


def test_identifiers_are_rebuilt_from_the_manifest_without_a_lookup():
    record = {"doi": DOI, "doi_raw": DOI,
              "identifiers": {"doi": DOI, "pmcid": PMCID, "title": TITLE,
                              "is_preprint": False, "a_key_from_the_future": 1,
                              "pub_types": ["research-article"]}}

    ids = identifiers_from_record(record)

    assert ids.pmcid == PMCID and ids.title == TITLE
    assert ids.pub_types == ["research-article"] and ids.is_preprint is False


def test_only_tiers_that_can_supply_xml_are_built_and_the_browser_is_not_among_them():
    config = {"fetch": {"tiers": ["europepmc", "pmc_s3", "elsevier_tdm", "pmc_supplements",
                                  "pmc_oa", "biorxiv", "proxy_browser"],
                        "min_interval_seconds": 0}}
    assert [s.name for s in xml_sources(config)] == ["europepmc", "pmc_s3", "biorxiv"]


def test_the_corpus_pass_and_its_summary(tmp_path):
    corpus = tmp_path / "corpus"
    _article(corpus, "has", xml=jats_article())
    _article(corpus, "lacks")
    _article(corpus, "unservable", pmcid=None)
    http, sources = _bucket()

    reports = fill_xml_corpus(corpus, apply=True, sources=sources)

    assert [r["slug"] for r in reports] == ["has", "lacks", "unservable"]
    assert summarize(reports) == {"articles": 3, "candidates": 2, "filled": 1,
                                  "flagged": 0, "remaining": 1, "by_tier": {"pmc_s3": 1}}


def test_the_command_runs_the_pass_and_says_what_it_did(tmp_path, monkeypatch, capsys):
    corpus = tmp_path / "corpus"
    _article(corpus, "lacks")
    http, sources = _bucket()
    monkeypatch.setattr(fill_xml, "xml_sources", lambda config: sources)

    code = cli.main(["--config", str(tmp_path / "none.yaml"), "fill-xml", "--apply",
                     "--corpus-dir", str(corpus)])

    captured = capsys.readouterr()
    assert code == 0
    assert "filled lacks: fulltext.nxml from pmc_s3" in captured.out
    assert "1 without a JATS: 1 filled (1 from pmc_s3), 0 still without one" in captured.err
    assert (corpus / "lacks" / store.FULLTEXT_XML).exists()
