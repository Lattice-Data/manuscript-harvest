"""Correcting a corpus that was fetched before anything asked which paper it was.

`revalidate` exists because the fix to `fetcher` is only a fix for the next fetch.
392 articles were already on disk when the identity checks landed, and two of them
were wrong in exactly the way those checks catch. What these tests defend is that
the correction is honest in both directions: it must name the two, and it must not
touch the 390 -- a pass that rewrote statuses it had no evidence for would be a
worse bug than the one it repairs.
"""

import json

from manuscript_harvest.fetch import store
from manuscript_harvest.fetch.revalidate import (
    apply_report,
    revalidate_article,
    revalidate_corpus,
)
from tests.fakes import DOI, jats_article, make_article, make_pdf, make_scanned_pdf

MANUAL = "10xGenomics.com CG000239 Rev F USER GUIDE Visium Spatial Gene Expression "
REAL = f"TP53 knockout via CRISPR-Cas9. https://doi.org/{DOI} "


def _article(tmp_path, title="TP53 knockout via CRISPR-Cas9", **kwargs):
    directory = make_article(tmp_path / "art", **kwargs)
    record = store.read_manifest(directory)
    record["identifiers"] = {"doi": DOI, "title": title}
    store.write_manifest(directory, record)
    return directory


def test_the_right_paper_is_left_alone(tmp_path):
    directory = _article(tmp_path, fulltext=make_pdf(text=REAL * 12))
    assert revalidate_article(directory)["changed"] is False


def test_a_vendor_manual_is_found_without_touching_the_network(tmp_path):
    """10.1126/science.adf1226, whose only file was the wrong document.

    Re-fetching to discover this would need a live proxy session and a publisher
    request per paper; the bytes already on disk answer it for free.
    """
    directory = _article(
        tmp_path, fulltext=make_pdf(text=MANUAL * 12),
        title="Comprehensive cell atlas of the first-trimester developing human brain")

    report = revalidate_article(directory)

    assert report["changed"] and report["verdict"] == "identity_unverified"
    assert "10xGenomics.com" in " ".join(report["problems"])


def test_a_correction_notice_is_found_from_its_jats(tmp_path):
    directory = _article(tmp_path, fulltext=make_pdf(text=REAL * 12),
                         xml=jats_article(article_type="correction"))

    report = revalidate_article(directory)

    assert report["verdict"] == "not_research_article"
    assert report["xml_verdict"] == "not_research_article"


def test_applying_the_verdict_rewrites_the_status_and_keeps_the_bytes(tmp_path):
    directory = _article(
        tmp_path, fulltext=make_pdf(text=MANUAL * 12),
        title="Comprehensive cell atlas of the first-trimester developing human brain")

    record = apply_report(directory, revalidate_article(directory))

    assert record["fulltext"]["status"] == "identity_unverified"
    # `finalize_status` owns the top-level word; this pass does not set it directly.
    assert record["status"] == "failed"
    assert record["revalidated"]["was"] == "ok"
    assert (directory / "fulltext.pdf").exists()
    # And it survived the round trip to disk.
    assert store.read_manifest(directory)["fulltext"]["status"] == "identity_unverified"


def test_running_twice_does_not_say_the_same_thing_twice(tmp_path):
    directory = _article(
        tmp_path, fulltext=make_pdf(text=MANUAL * 12),
        title="Comprehensive cell atlas of the first-trimester developing human brain")

    first = apply_report(directory, revalidate_article(directory))
    apply_report(directory, revalidate_article(directory))
    again = store.read_manifest(directory)

    assert again["problems"] == first["problems"]
    assert revalidate_article(directory)["changed"] is False


def test_a_missing_file_is_not_a_wrong_one(tmp_path):
    """A paywalled paper's `fulltext.status` is already a true diagnosis.

    Overwriting `paywalled` with `identity_unverified` would swap a fact for a
    vacuous statement about a file that never arrived.
    """
    directory = _article(tmp_path)
    record = store.read_manifest(directory)
    record["fulltext"] = {"status": "paywalled", "path": None}
    store.write_manifest(directory, record)

    report = revalidate_article(directory)
    assert report["changed"] is False and report["note"] == \
        "no full text on disk to identify"


def test_an_evicted_article_keeps_its_record(tmp_path):
    directory = _article(tmp_path, fulltext=make_pdf(text=MANUAL * 12))
    record = store.read_manifest(directory)
    record["status"] = "evicted"
    store.write_manifest(directory, record)

    report = revalidate_article(directory)
    assert report["changed"] is False and "evicted" in report["note"]


def test_a_scanned_article_is_not_condemned_for_having_no_text(tmp_path):
    directory = _article(tmp_path, fulltext=make_scanned_pdf())
    record = store.read_manifest(directory)
    record["fulltext"]["status"] = "scanned_pdf_suspected"
    store.write_manifest(directory, record)

    assert revalidate_article(directory)["changed"] is False


def test_the_corrected_doi_comes_from_the_index_when_the_manifest_has_it(tmp_path):
    """`corrects_doi` is Europe PMC stating the relation; reading the first other
    DOI out of the notice's body is a guess, because a notice cites references too.
    """
    directory = _article(tmp_path, fulltext=make_pdf(text=REAL * 12),
                         title="Author Correction: Progressive plasticity")
    record = store.read_manifest(directory)
    record["identifiers"]["corrects_doi"] = "10.1038/s41586-024-08150-0"
    store.write_manifest(directory, record)

    problem = " ".join(revalidate_article(directory)["problems"])
    assert "10.1038/s41586-024-08150-0" in problem and "fetch instead" in problem
    assert "probably" not in problem


def test_a_corpus_pass_reports_every_article_and_corrects_only_the_bad_ones(tmp_path):
    corpus = tmp_path / "corpus"
    for name, text in (("good", REAL), ("bad", MANUAL)):
        directory = make_article(corpus / name, fulltext=make_pdf(text=text * 12))
        record = store.read_manifest(directory)
        record["identifiers"] = {"doi": DOI, "title": "TP53 knockout via CRISPR-Cas9"}
        store.write_manifest(directory, record)

    reports = revalidate_corpus(corpus, apply=True)

    assert len(reports) == 2
    changed = {r["slug"]: r["verdict"] for r in reports if r["changed"]}
    assert changed == {"bad": "identity_unverified"}
    assert json.loads((corpus / "good" / "manifest.json").read_text())["status"] \
        == "complete"
