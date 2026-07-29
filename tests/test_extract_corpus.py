"""Regression tests against the real corpus, skipped when it is not there.

`corpus/` is gitignored -- the bytes belong to the publishers -- so these skip in
a clean checkout and run on a machine that has fetched the papers. They exist
because every bug this stage has had so far was found by pointing it at real
files: a strict-conformance workbook, a header on row 4, a caption nested inside
`<media>`, a 23 MB "paragraph". Synthetic fixtures pin those shapes in
`test_extract_units.py`; these confirm the actual files still parse.

Run them after fetching:  python -m pytest tests/test_extract_corpus.py -q
"""

import json
from pathlib import Path

import pytest

from harvest.extract import extractor, jats, spreadsheet
from harvest.extract.limits import Limits

CORPUS = Path("corpus")
L = Limits()

pytestmark = pytest.mark.skipif(not CORPUS.exists(), reason="no local corpus")


def _needs(relative: str) -> Path:
    path = CORPUS / relative
    if not path.exists():
        pytest.skip(f"{relative} not in this corpus")
    return path


def _extractions():
    records = []
    for path in sorted(CORPUS.glob("*/extracted/extraction.json")):
        try:
            records.append(json.loads(path.read_text()))
        except ValueError:
            pytest.fail(f"unreadable extraction record: {path}")
    if not records:
        pytest.skip("nothing extracted yet; run `python -m harvest.extract.cli all`")
    return records


# -- invariants over whatever has been extracted -----------------------------

def test_every_status_is_in_the_taxonomy():
    """An unrecognised status means a code path invented one, and the `status`
    report would silently stop counting it."""
    articles = {"complete", "partial", "failed", "no_manifest"}
    files = {extractor.OK, extractor.NO_TEXT, extractor.SCANNED, extractor.IMAGE_NO_TEXT,
             extractor.MEDIA_NO_TEXT, extractor.DATA_SKIPPED, extractor.UNSUPPORTED,
             extractor.TOO_LARGE, extractor.MISSING, extractor.UNREADABLE}
    for record in _extractions():
        assert record["status"] in articles, record["slug"]
        assert (record["main_text"] or {}).get("status") in files | {None}
        for entry in record["supplementary"]:
            assert entry["status"] in files, (record["slug"], entry)


def test_nothing_claims_ok_while_producing_no_text():
    """`ok` with zero blocks is the exact failure this stage exists to prevent."""
    for record in _extractions():
        for entry in [record["main_text"]] + record["supplementary"]:
            if entry.get("status") == extractor.OK:
                assert entry["blocks"] > 0 and entry["chars"] > 0, \
                    (record["slug"], entry["path"])


def test_no_block_is_absurdly_long():
    """A 23 MB single "paragraph" from a headerless data dump is data, not prose."""
    for record in _extractions():
        for entry in [record["main_text"]] + record["supplementary"]:
            if entry.get("blocks"):
                average = entry["chars"] / entry["blocks"]
                assert average <= L.max_paragraph_chars, (record["slug"], entry["path"])


def test_completed_articles_have_a_main_text_source():
    for record in _extractions():
        if record["status"] == "complete":
            assert (record["main_text"] or {}).get("source") in {"jats", "pdf"}


# -- the specific files that taught this stage its rules ---------------------

def test_the_strict_ooxml_supplement_still_reads():
    """10.1016/j.cell.2021.01.053, mmc7.xlsx: three worksheets openpyxl reports as
    zero. It holds sampleID, Age, Sex and CoVID-19 severity."""
    path = _needs("10.1016_j.cell.2021.01.053/supplementary/04_mmc7.xlsx")
    cards, status, meta = spreadsheet.cards_from_xlsx(path.read_bytes(), str(path), L)
    assert status == "ok" and meta.get("strict_ooxml") is True
    headers = {name.lower() for card in cards for name in card.header}
    assert {"age", "sex"} <= headers


def test_the_dimensionless_workbook_still_reads():
    """10.1038/s44161-025-00612-6, MOESM5: worksheets with no declared dimensions."""
    path = _needs("10.1038_s44161-025-00612-6/supplementary/05_44161_2025_612_MOESM5_ESM.xlsx")
    cards, status, _ = spreadsheet.cards_from_xlsx(path.read_bytes(), str(path), L)
    assert status == "ok" and cards


def test_the_row_four_header_is_still_found():
    """10.1038/s41591-018-0269-2, MOESM1: title, caption, blank, then the header."""
    path = _needs("10.1038_s41591-018-0269-2/supplementary/01_41591_2018_269_MOESM1_ESM.xlsx")
    cards, status, _ = spreadsheet.cards_from_xlsx(path.read_bytes(), str(path), L)
    assert status == "ok"
    first = cards[0]
    assert first.header_row == 3
    sex = next(c for c in first.columns if c["name"].lower() == "sex")
    assert sex["values"] == ["F", "M"]


def test_springer_supplement_labels_are_still_joined():
    """The caption lives on a nested `<media>`; reading only direct children found
    labels for none of the 40 XML files here."""
    path = _needs("10.1038_s41467-023-40505-5/fulltext.nxml")
    labels = jats.supplement_labels(path.read_bytes())
    assert labels, "no supplementary-material hrefs found"
    assert any("MOESM" in name for name in labels)
    assert any(entry.get("caption") for entry in labels.values())


def test_every_jats_file_in_the_corpus_parses():
    """Publisher XML carries DOCTYPEs and named entities the stdlib parser will not
    resolve on its own."""
    files = sorted(CORPUS.glob("*/fulltext.nxml"))
    if not files:
        pytest.skip("no JATS XML in this corpus")
    failures = []
    for path in files:
        _, status, meta = jats.blocks_from_jats(path.read_bytes(), str(path), L)
        if status == "unreadable":
            failures.append((path.parent.name, meta.get("reason")))
    assert not failures, failures
