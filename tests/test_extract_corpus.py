"""Regression tests against the real corpus, skipped when it is not there.

`corpus/` is gitignored -- the bytes belong to the publishers -- so these skip in
a clean checkout and run on a machine that has fetched the papers. They exist
because every bug this stage has had so far was found by pointing it at real
files: a strict-conformance workbook, a header on row 4, a caption nested inside
`<media>`, a 23 MB "paragraph". Synthetic fixtures pin those shapes in
`test_extract_units.py`; these confirm the actual files still parse.

Run them after fetching:  python -m pytest tests/test_extract_corpus.py -q
"""

import collections
import json
import re
from pathlib import Path

import pytest

from manuscript_harvest.extract import blocks as blocks_mod
from manuscript_harvest.extract import (extractor, jats, review, section_audit,
                                        spreadsheet, tables)
from manuscript_harvest.extract.blocks import read_blocks
from manuscript_harvest.extract.limits import Limits
from manuscript_harvest.fetch import store

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
        pytest.skip("nothing extracted yet; run `python -m manuscript_harvest.extract.cli all`")
    return records


# -- invariants over whatever has been extracted -----------------------------

def test_every_status_is_in_the_taxonomy():
    """An unrecognised status means a code path invented one, and the `status`
    report would silently stop counting it."""
    articles = {"complete", "partial", "failed", "no_manifest"}
    files = {extractor.OK, extractor.NO_TEXT, extractor.SCANNED, extractor.IMAGE_NO_TEXT,
             extractor.MEDIA_NO_TEXT, extractor.DATA_SKIPPED, extractor.UNSUPPORTED,
             extractor.TOO_LARGE, extractor.MISSING, extractor.UNREADABLE,
             extractor.PARSER_ERROR}
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


def test_header_confidence_is_a_closed_set():
    """A consumer treating `!= "high"` as suspect now sees a third value, and
    `confirmed` is the one a human put there."""
    _extractions()
    for path in sorted(CORPUS.glob("*/extracted/blocks.jsonl")):
        for block in read_blocks(path):
            card = block.get("table")
            if card:
                assert card["header_confidence"] in tables.HEADER_CONFIDENCE, \
                    (path.parent.parent.name, block.get("locator"))


def test_role_is_a_closed_three_value_set():
    """`non_evidence` is what a human's "this file is not article evidence"
    checkbox writes, and it changes what `cmd_show --role` and every downstream
    filter mean."""
    _extractions()
    for path in sorted(CORPUS.glob("*/extracted/blocks.jsonl")):
        for block in read_blocks(path):
            assert block["role"] in blocks_mod.ROLES, block["role"]


def test_every_review_key_is_unique_over_the_corpus():
    """An answer is matched to a question by its key. Two questions with one key
    would let a human's judgement about one card silently apply to another."""
    for directory in sorted(p for p in CORPUS.glob("*") if p.is_dir()):
        extraction = extractor.read_extraction(directory)
        if extraction is None:
            continue
        queue = review.queue_for(extraction,
                                 directory / extractor.EXTRACT_DIR / "blocks.jsonl",
                                 L, store.read_manifest(directory))
        keys = [review.answer_key(i["kind"], i["key"]) for i in queue]
        assert len(keys) == len(set(keys)), directory.name


def test_no_extracted_block_carries_an_invisible_or_symbol_glyph():
    """Measured before the fix: 79 damaged codepoints in
    10.1126/sciimmunol.aba4163 alone -- 21x U+00AD, 41x U+F067 (Adobe Symbol
    gamma), 5x U+200B -- so `interleukin-<U+00AD> 17A` and `interferon- (IFN-)`
    reached a model where the paper says `interleukin-17A` and
    `interferon-γ (IFN-γ)`."""
    _extractions()
    damaged = collections.Counter()
    for path in sorted(CORPUS.glob("*/extracted/blocks.jsonl")):
        for block in read_blocks(path):
            for char in block["text"]:
                point = ord(char)
                if point in (0x00AD, 0x200B, 0x200C, 0x200D, 0xFEFF) \
                        or 0xF000 <= point <= 0xF0FF:
                    damaged[(path.parent.parent.name, f"U+{point:04X}")] += 1
    assert not damaged, dict(damaged)


def test_no_table_card_fuses_two_numbers_into_one_value():
    """`_inline_text` had no boundary between a cell's block-level children, so
    10.1038/s41467-023-40505-5 Table 1's SNP PIP column read `0.7980.15` and its
    dtype flipped from number to mixed."""
    _extractions()
    fused = re.compile(r"^\d+\.\d+\d\.\d")
    offenders = []
    for path in sorted(CORPUS.glob("*/extracted/blocks.jsonl")):
        for block in read_blocks(path):
            for column in ((block.get("table") or {}).get("columns") or []):
                for value in (column.get("values") or []) + (column.get("examples") or []):
                    if fused.match(str(value)):
                        offenders.append((path.parent.parent.name, column["name"], value))
    assert not offenders, offenders[:10]


def test_no_card_value_is_only_citation_punctuation():
    """The key resources table of 10.1016/j.cell.2021.01.053 is the one table
    this pipeline exists to read, and 10 of its 29 SOURCE cells were reduced to
    `()` or `;` by the rule that drops citation markers from prose."""
    _extractions()
    offenders = []
    for path in sorted(CORPUS.glob("*/extracted/blocks.jsonl")):
        for block in read_blocks(path):
            for column in ((block.get("table") or {}).get("columns") or []):
                for value in (column.get("values") or []) + (column.get("examples") or []):
                    if str(value).strip() in {"()", ";", ",", "(;)", "(,)"}:
                        offenders.append((path.parent.parent.name,
                                          block.get("locator"), column["name"], value))
    assert not offenders, offenders[:10]


def test_every_blocks_line_is_json_that_is_not_only_pythons_dialect():
    """Line 520 of 10.1038/s41467-023-40505-5's blocks.jsonl carried
    `"max": Infinity`, from `float("Inf")` in the `neg. log10-pval` column of
    sheet `Supplementary Data 3`. Python reads it; serde_json, Go's
    encoding/json, PostgreSQL jsonb and DuckDB do not."""
    _extractions()
    for path in sorted(CORPUS.glob("*/extracted/blocks.jsonl")):
        with path.open(encoding="utf-8") as handle:
            for number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                json.loads(line, parse_constant=lambda c: pytest.fail(
                    f"{path}:{number} is not JSON: {c}"))


def test_the_second_reviewers_remarks_are_still_in_the_peer_review_file():
    """The count-only running-head rule deleted
    `Reviewer #2 (Remarks to the Author):` from all three pages it appears on, so
    that article's blocks held reviewers 1, 3, 1, 3 and 4 and no reviewer 2 --
    their remarks reading as a continuation of reviewer 1's."""
    _needs("10.1038_s41467-023-40505-5/supplementary/"
           "22_41467_2023_40505_MOESM2_ESM.pdf")
    path = _needs("10.1038_s41467-023-40505-5/extracted/blocks.jsonl")
    reviewers = [b["text"] for b in read_blocks(path)
                 if b["text"].startswith("Reviewer #")]
    assert sum(1 for t in reviewers if t.startswith("Reviewer #2")) == 3, reviewers


def test_no_supplement_label_is_shared_by_two_files():
    """A label two files share is not a per-file name. `Download` was on 1,989 of
    2,076 blocks in 10.1126/science.aat5031 and `Europe PMC supplementary
    archive` on 347 of 536 in 10.1038/s41467-023-40505-5."""
    for record in _extractions():
        labels = [e["label"] for e in record["supplementary"] if e.get("label")]
        repeated = [name for name, n in collections.Counter(labels).items() if n > 1]
        assert not repeated, (record["slug"], repeated)


def test_every_label_source_is_in_the_closed_set():
    for record in _extractions():
        for entry in record["supplementary"]:
            assert entry.get("label_source") in extractor.LABEL_SOURCES, \
                (record["slug"], entry["path"], entry.get("label_source"))


def test_every_jats_locator_resolves_to_the_element_it_names():
    """`[n]` in XPath counts children of that tag. Counting every child left only
    76 of the 168 body/back locators in 10.1038/s41467-023-40505-5 resolving at
    all, and 153 of them pointing at a different element."""
    import xml.etree.ElementTree as ET

    for xml_path in sorted(CORPUS.glob("*/fulltext.nxml")):
        blocks_path = xml_path.parent / "extracted" / "blocks.jsonl"
        if not blocks_path.exists():
            continue
        root = ET.fromstring(jats._prepare(xml_path.read_bytes()))
        article = root if jats._tag(root) == "article" else next(
            (e for e in root.iter() if jats._tag(e) == "article"), None)
        for element in article.iter():
            element.tag = jats._tag(element)
        unresolved = []
        for block in read_blocks(blocks_path):
            locator = block.get("locator") or ""
            if block.get("origin") != "jats" or "[" not in locator:
                continue
            if article.find("./" + locator) is None:
                unresolved.append(locator)
        assert not unresolved, (xml_path.parent.name, unresolved[:10])


def test_block_ids_are_unique_over_the_corpus():
    """`index` is positional: insert one block and every downstream reference
    moves. A human confirmation recorded against index 148 must not silently
    become a statement about a different paragraph."""
    _extractions()
    for path in sorted(CORPUS.glob("*/extracted/blocks.jsonl")):
        ids = [b["block_id"] for b in read_blocks(path)]
        assert all(ids), path
        repeated = [i for i, n in collections.Counter(ids).items() if n > 1]
        assert not repeated, (path.parent.parent.name, repeated[:5])


# -- the section labeller as a regression metric -----------------------------

EXPECTED_SCORES = Path("tests/expected_section_scores.json")


def test_no_article_regressed_against_the_recorded_section_scores():
    """`section_audit.py` scores the PDF labeller against JATS, which declares
    its sections. It was a thing someone remembered to run; this makes it a gate.

    A legitimate improvement updates `tests/expected_section_scores.json` in the
    same commit, so the diff shows the gain.
    """
    baseline = json.loads(EXPECTED_SCORES.read_text())
    regressions = []
    for directory in sorted(p for p in CORPUS.glob("*") if p.is_dir()):
        report = section_audit.audit_article(directory)
        if report is None or report.get("skipped"):
            continue
        recorded = baseline.get(report["slug"])
        if recorded is None:
            continue
        if report["correct"] < recorded["correct"]:
            regressions.append((report["slug"], "correct",
                                recorded["correct"], report["correct"]))
        for name, was in recorded["precision"].items():
            now = (report["sections"].get(name) or {}).get("precision")
            if now is not None and now < was:
                regressions.append((report["slug"], name, was, now))
    assert not regressions, regressions


def test_pdf_reading_order_is_not_improved_by_sorting():
    """PyMuPDF's `get_text("blocks", sort=True)` is the obvious fix for two-column
    reading order and it is measurably worse. Measured once and pinned here so the
    next person does not "fix" it: insertion order gives 10 backward steps in 98
    aligned paragraphs, and sorting makes both statistics worse.
    """
    import fitz

    pairs = [(p.parent / store.FULLTEXT_XML, p)
             for p in sorted(CORPUS.glob("*/" + store.FULLTEXT_PDF))]
    pairs = [(x, p) for x, p in pairs if x.is_file()]
    if not pairs:
        pytest.skip("no article here has both renditions")

    worse = []
    for xml_path, pdf_path in pairs:
        jats_blocks, status, _ = jats.blocks_from_jats(xml_path.read_bytes(),
                                                       str(xml_path), L)
        if status != "ok":
            continue
        scores = {}
        for sort in (False, True):
            document = fitz.open(pdf_path)
            texts = []
            for page in document:
                for raw in page.get_text("blocks", sort=sort):
                    if len(raw) >= 7 and raw[6] != 0:
                        continue
                    cleaned = " ".join((raw[4] or "").split())
                    if cleaned:
                        texts.append(cleaned)
            document.close()
            scores[sort] = section_audit.reading_order_score(jats_blocks, texts)
        if scores[True]["inverted_pairs"] < scores[False]["inverted_pairs"]:
            worse.append((pdf_path.parent.name, scores[False], scores[True]))
    assert not worse, ("sorting improved reading order here; re-measure before "
                       "changing pdf.py", worse)


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
