"""Guards for the supplement ledger, one per way it reported the wrong thing.

The module answers two questions and keeps them apart: did the declared file
arrive (a fetcher gap), and did text come out of it (an extraction gap). Every
test here is a mistake the first version actually made:

  * calling an item missing when the publisher simply used a different naming
    scheme -- 176 of 217 unmatched corpus items look like this, so the naive
    reading was wrong four times in five;
  * counting a movie the policy would delete on arrival as lost evidence, which
    made the actionable list twice as long as the work in it;
  * reporting "nothing to read" for an article holding eleven supplements,
    because nothing had declared them;
  * a staleness check that read one field name from two records and therefore
    called every article stale.

The corpus tests skip without a local corpus, the way the rest of this suite
does. Run: python -m pytest tests/test_supplements.py -q
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from manuscript_harvest import supplements
from manuscript_harvest.supplements import (
    FETCHED,
    NOT_FETCHED,
    NOT_RECONCILED,
    REMOVED_BY_POLICY,
    Ledger,
    describe,
    reconcile,
    stale_reason,
    write_sidecar,
)

CORPUS = Path(__file__).resolve().parent.parent / "corpus"

JATS = """<?xml version="1.0"?>
<article xmlns:xlink="http://www.w3.org/1999/xlink"><back>{items}</back></article>"""

#: `<label>` and `<caption>` are separate in JATS and the module keeps them
#: separate. Elsevier ships the item name inside `<caption><title>`, Springer
#: puts it in `<label>`; the fixture uses `<label>` so tests can assert on it.
ITEM = """<supplementary-material id="s{n}"><label>{title}</label>
<media xlink:href="{href}"/></supplementary-material>"""


def _article(tmp_path, declared=(), entries=(), extracted=(), with_jats=True):
    """Build a minimal article directory: JATS + manifest + extraction record."""
    directory = tmp_path / "10.1000_x"
    (directory / "extracted").mkdir(parents=True)
    if with_jats:
        body = "".join(ITEM.format(n=i, title=title, href=href)
                       for i, (href, title) in enumerate(declared))
        (directory / "fulltext.nxml").write_text(JATS.format(items=body),
                                                 encoding="utf-8")
    (directory / "manifest.json").write_text(json.dumps({
        "doi": "10.1000/x",
        "supplementary_status": "fetched",
        "supplementary": list(entries),
    }), encoding="utf-8")
    (directory / "extracted" / "extraction.json").write_text(json.dumps({
        "extraction_key": "key1",
        "source_manifest_sha256": "sha1",
        "supplementary": list(extracted),
    }), encoding="utf-8")
    return directory


def _entry(path, original=None, **over):
    record = {"path": path, "original_name": original or Path(path).name}
    record.update(over)
    return record


def test_a_declared_item_that_arrived_is_fetched(tmp_path):
    directory = _article(
        tmp_path,
        declared=[("mmc1.xlsx", "Table S1")],
        entries=[_entry("supplementary/01_mmc1.xlsx", "mmc1.xlsx")],
        extracted=[{"path": "supplementary/01_mmc1.xlsx", "status": "ok", "chars": 900}],
    )
    ledger = reconcile(directory)
    assert [item.state for item in ledger.items] == [FETCHED]
    assert ledger.read == 1
    assert ledger.items[0].label == "Table S1"


def test_the_retrieval_prefix_is_not_read_as_an_item_number(tmp_path):
    """`03_mmc7.xlsx` is retrieval order. It changes on re-fetch and means nothing."""
    directory = _article(
        tmp_path,
        declared=[("mmc7.xlsx", "Table S3")],
        entries=[_entry("supplementary/03_mmc7.xlsx", "mmc7.xlsx")],
        extracted=[{"path": "supplementary/03_mmc7.xlsx", "status": "ok", "chars": 10}],
    )
    assert reconcile(directory).items[0].state == FETCHED


def test_an_extension_rewrite_still_matches(tmp_path):
    """PNAS declares `sd02.xlsx` and ships `sd02.csv`. Four corpus items do this."""
    directory = _article(
        tmp_path,
        declared=[("pnas.123.sd02.xlsx", "Dataset S2")],
        entries=[_entry("supplementary/01_pnas.123.sd02.csv", "pnas.123.sd02.csv")],
        extracted=[{"path": "supplementary/01_pnas.123.sd02.csv",
                    "status": "ok", "chars": 50}],
    )
    item = reconcile(directory).items[0]
    assert item.state == FETCHED
    assert item.matched_by == "stem"


def test_a_missing_item_is_only_called_missing_when_nothing_else_could_be_it(tmp_path):
    """A genuine fetch gap: declared, absent, and every fetched file is claimed."""
    directory = _article(
        tmp_path,
        declared=[("mmc1.xlsx", "Table S1"), ("mmc2.xlsx", "Table S2")],
        entries=[_entry("supplementary/01_mmc1.xlsx", "mmc1.xlsx")],
        extracted=[{"path": "supplementary/01_mmc1.xlsx", "status": "ok", "chars": 10}],
    )
    ledger = reconcile(directory)
    assert ledger.count(NOT_FETCHED) == 1
    assert ledger.count(NOT_RECONCILED) == 0
    assert ledger.evidence_lost == 1


def test_a_naming_mismatch_is_unreconciled_rather_than_missing(tmp_path):
    """The 176-item defect.

    PMC author-manuscript names against publisher names: the item is declared,
    unmatched, and a fetched file sits unclaimed. Which file is which is not
    knowable from the manifest, so it must not be reported as never retrieved.
    """
    directory = _article(
        tmp_path,
        declared=[("NIHMS1-supplement-Table_S1.xlsx", "Table S1")],
        entries=[_entry("supplementary/01_mmc1.xlsx", "mmc1.xlsx")],
        extracted=[{"path": "supplementary/01_mmc1.xlsx", "status": "ok", "chars": 10}],
    )
    ledger = reconcile(directory)
    assert ledger.count(NOT_RECONCILED) == 1
    assert ledger.count(NOT_FETCHED) == 0
    assert ledger.unclaimed_files == 1
    # It is not evidence loss, because the evidence may well be the file we have.
    assert ledger.evidence_lost == 0


def test_a_policy_removal_is_not_a_fetch_gap(tmp_path):
    """2,295 corpus entries are drop_media removals. They have no `path`."""
    directory = _article(
        tmp_path,
        declared=[("fig.tif", "S1 Fig")],
        entries=[{"name": "supplementary/01_fig.tif", "original_name": "fig.tif",
                  "removed": "not_text_bearing", "removed_reason": "image"}],
    )
    ledger = reconcile(directory)
    assert ledger.count(REMOVED_BY_POLICY) == 1
    assert ledger.count(NOT_FETCHED) == 0
    assert ledger.items[0].removed_reason == "image"


def test_a_missing_movie_is_not_counted_as_lost_evidence(tmp_path):
    """19 of the 41 corpus gaps are media the text pipeline refuses anyway."""
    directory = _article(
        tmp_path,
        declared=[("mmc4.mp4", "Video S1"), ("mmc5.xlsx", "Table S1")],
        entries=[],
    )
    ledger = reconcile(directory)
    assert ledger.count(NOT_FETCHED) == 2
    assert ledger.evidence_lost == 1
    lost = [item.href for item in ledger.items if item.evidence_lost]
    assert lost == ["mmc5.xlsx"]


def test_a_fetched_file_that_yielded_no_text_is_a_read_gap_not_a_fetch_gap(tmp_path):
    directory = _article(
        tmp_path,
        declared=[("scan.pdf", "Table S1")],
        entries=[_entry("supplementary/01_scan.pdf", "scan.pdf")],
        extracted=[{"path": "supplementary/01_scan.pdf",
                    "status": "no_text_scanned_pdf", "chars": 0}],
    )
    ledger = reconcile(directory)
    assert ledger.count(FETCHED) == 1
    assert ledger.read == 0
    assert ledger.evidence_lost == 0
    stages = dict(describe(ledger))
    assert "1 did not" in stages["read"]
    assert "no_text_scanned_pdf" in stages["read"]


def test_ocr_success_is_not_read_as_a_failure(tmp_path):
    """68 corpus files are `ok_via_ocr`. A `status == 'ok'` test miscounts them."""
    directory = _article(
        tmp_path,
        declared=[("scan.pdf", "Table S1")],
        entries=[_entry("supplementary/01_scan.pdf", "scan.pdf")],
        extracted=[{"path": "supplementary/01_scan.pdf",
                    "status": "ok_via_ocr", "chars": 4000}],
    )
    assert reconcile(directory).read == 1


# ---------------------------------------------------------------- describe


def test_every_stage_is_named_in_every_report(tmp_path):
    """A stage a reader has to infer from its absence is the whole defect."""
    for directory in (
        _article(tmp_path / "a", declared=[("mmc1.xlsx", "T")], entries=[]),
        _article(tmp_path / "b", with_jats=False, entries=[]),
        _article(tmp_path / "c", declared=[], entries=[]),
    ):
        stages = [stage for stage, _ in describe(reconcile(directory))]
        assert stages == ["declaration", "fetch", "read"]


def test_no_clause_needs_its_position_to_be_understood(tmp_path):
    directory = _article(
        tmp_path,
        declared=[("mmc1.xlsx", "Table S1")],
        entries=[_entry("supplementary/01_mmc1.xlsx", "mmc1.xlsx")],
        extracted=[{"path": "supplementary/01_mmc1.xlsx", "status": "ok", "chars": 5}],
    )
    for _, text in describe(reconcile(directory)):
        assert text.strip()
        # A bare status token standing alone is the `partial`-in-column-three
        # failure. Every clause has to be a phrase.
        assert " " in text.strip()


def test_an_unenumerated_deposit_does_not_read_as_a_clean_one(tmp_path):
    """`fetched_unverified` means the set was never bounded, not that it is whole."""
    directory = _article(tmp_path, with_jats=False, entries=[])
    manifest = directory / "manifest.json"
    record = json.loads(manifest.read_text())
    record["supplementary_status"] = "fetched_unverified"
    manifest.write_text(json.dumps(record))
    stages = dict(describe(reconcile(directory)))
    assert "never enumerated" in stages["fetch"]
    # The raw token stays reachable rather than being paraphrased away.
    assert "fetched_unverified" in stages["fetch"]


def test_an_undeclared_article_reports_its_files_rather_than_silence(tmp_path):
    """124 corpus articles have no JATS. Saying 'nothing to read' would be false."""
    directory = _article(
        tmp_path, with_jats=False,
        entries=[_entry("supplementary/01_a.xlsx")],
        extracted=[{"path": "supplementary/01_a.xlsx", "status": "ok", "chars": 700}],
    )
    ledger = reconcile(directory)
    assert not ledger.measurable
    stages = dict(describe(ledger))
    assert "no JATS" in stages["declaration"]
    assert "1 of 1 file on disk yielded text" in stages["read"]


def test_no_declared_item_matching_is_not_the_same_as_no_file(tmp_path):
    directory = _article(
        tmp_path,
        declared=[("NIHMS1-supplement-1.xlsx", "Table S1")],
        entries=[_entry("supplementary/01_mmc1.xlsx", "mmc1.xlsx")],
        extracted=[{"path": "supplementary/01_mmc1.xlsx", "status": "ok", "chars": 5}],
    )
    stages = dict(describe(reconcile(directory)))
    assert "no declared item matched a file" in stages["read"]
    assert "1 of 1 file on disk yielded text" in stages["read"]


# ---------------------------------------------------------------- sidecar


def test_a_fresh_sidecar_is_not_stale(tmp_path):
    directory = _article(tmp_path, declared=[("mmc1.xlsx", "T")], entries=[])
    write_sidecar(directory)
    assert stale_reason(directory) is None


def test_a_missing_sidecar_is_named_rather_than_assumed_fresh(tmp_path):
    directory = _article(tmp_path, declared=[], entries=[])
    assert "no sidecar" in stale_reason(directory)


@pytest.mark.parametrize("field", ["extraction_key", "source_manifest_sha256"])
def test_a_re_extraction_makes_the_sidecar_stale(tmp_path, field):
    """The bug this catches: reading one field name from two different records,
    which made every freshly written sidecar report itself out of date."""
    directory = _article(tmp_path, declared=[("mmc1.xlsx", "T")], entries=[])
    write_sidecar(directory)
    path = directory / "extracted" / "extraction.json"
    record = json.loads(path.read_text())
    record[field] = "changed"
    path.write_text(json.dumps(record))
    reason = stale_reason(directory)
    assert reason and field in reason


def test_a_sidecar_from_an_older_version_is_stale(tmp_path):
    directory = _article(tmp_path, declared=[], entries=[])
    path = write_sidecar(directory)
    stored = json.loads(path.read_text())
    stored["sidecar_version"] = supplements.SIDECAR_VERSION - 1
    path.write_text(json.dumps(stored))
    assert "sidecar version" in stale_reason(directory)


def test_the_sidecar_lands_beside_the_extraction_it_describes(tmp_path):
    directory = _article(tmp_path, declared=[], entries=[])
    assert write_sidecar(directory).parent.name == "extracted"


def test_the_ledger_survives_an_article_with_nothing_in_it(tmp_path):
    directory = tmp_path / "empty"
    directory.mkdir()
    ledger = reconcile(directory)
    assert isinstance(ledger, Ledger)
    assert not ledger.measurable
    assert [stage for stage, _ in describe(ledger)] == \
        ["declaration", "fetch", "read"]


# ---------------------------------------------------------------- corpus


@pytest.mark.skipif(not CORPUS.is_dir(), reason="no local corpus")
def test_the_corpus_totals_hold():
    """The numbers the design was approved on. A drift here is worth reading.

    Recomputed from the corpus, so a re-fetch legitimately moves them -- but
    silently moving is the thing to avoid, since the actionable list is small
    enough that doubling it would go unnoticed.
    """
    ledgers = [led for led in supplements.survey(CORPUS)
               if led.measurable]
    assert len(ledgers) == 259
    assert sum(led.declared for led in ledgers) == 2303
    assert sum(led.count(FETCHED) for led in ledgers) == 2015
    assert sum(led.count(NOT_RECONCILED) for led in ledgers) == 176
    assert sum(led.evidence_lost for led in ledgers) == 22
    assert len([led for led in ledgers if led.evidence_lost]) == 12


@pytest.mark.skipif(not CORPUS.is_dir(), reason="no local corpus")
def test_a_known_fetch_gap_is_still_found():
    """17 declared, 14 on disk, and the fetcher calls the article complete."""
    ledger = reconcile(CORPUS / "10.1038_s41586-020-03182-8")
    assert ledger.fetch_status == "fetched"
    assert ledger.evidence_lost == 3
    assert {item.label for item in ledger.items if item.evidence_lost} == {
        "Supplementary Table 1", "Supplementary Table 2", "Supplementary Table 9"}


@pytest.mark.skipif(not CORPUS.is_dir(), reason="no local corpus")
def test_the_one_file_holding_fifteen_items_reads_as_complete():
    """A file-count check reports 15 missing here. The declared set says zero."""
    ledger = reconcile(CORPUS / "10.1002_ctm2.1356")
    assert ledger.declared == 1
    assert ledger.evidence_lost == 0
    assert ledger.read == 1
