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
from manuscript_harvest.extract import (extractor, jats, pdf, review,
                                        section_audit, spreadsheet, tables)
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
    files = {extractor.OK, extractor.OK_VIA_OCR, extractor.NO_TEXT, extractor.SCANNED,
             extractor.IMAGE_NO_TEXT, extractor.MEDIA_NO_TEXT, extractor.DATA_SKIPPED,
             extractor.UNSUPPORTED, extractor.TOO_LARGE, extractor.MISSING,
             extractor.UNREADABLE, extractor.PARSER_ERROR, extractor.GARBLED}
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
    `interferon-γ (IFN-γ)`.

    The private-use half of this asks only for what `pdf.py` promises. A glyph
    it *can* name -- anything in `_SYMBOL_PUA` -- must never survive. A glyph it
    cannot is a different case: `CIDFont+F5` in 10.1038/s41467-025-67643-2 emits
    U+F021 twice on page 3 of its supplement, and that font is not a symbol face,
    so reading the codepoint as the Adobe Symbol position for `!` would invent a
    character the document never had. Those are allowed through and must instead
    be *counted*, which is the rule `pdf.py` states: "named rather than silently
    passed on". This asserts the naming actually happened.
    """
    records = {r["slug"]: r for r in _extractions()}
    damaged = collections.Counter()
    uncounted = collections.Counter()
    for path in sorted(CORPUS.glob("*/extracted/blocks.jsonl")):
        slug = path.parent.parent.name
        record = records.get(slug) or {}
        counted = set()
        for entry in [record.get("main_text")] + (record.get("supplementary") or []):
            counted.update(((entry or {}).get("glyphs_unmapped") or {}).keys())
        for block in read_blocks(path):
            for char in block["text"]:
                point = ord(char)
                name = f"U+{point:04X}"
                if point in (0x00AD, 0x200B, 0x200C, 0x200D, 0xFEFF) \
                        or point in pdf._SYMBOL_PUA:
                    damaged[(slug, name)] += 1
                elif pdf._is_pua(point) and name not in counted:
                    uncounted[(slug, name)] += 1
    assert not damaged, dict(damaged)
    assert not uncounted, ("private-use glyphs reached blocks.jsonl without being "
                           "recorded in glyphs_unmapped", dict(uncounted))


def test_no_table_card_fuses_two_numbers_into_one_value():
    """`_inline_text` had no boundary between a cell's block-level children, so
    10.1038/s41467-023-40505-5 Table 1's SNP PIP column read `0.7980.15` and its
    dtype flipped from number to mixed.

    A dotted European date has the same shape and is not a fusion:
    10.1101/2024.11.01.621259's `GeoMx run date` column holds `11.05.2023`,
    `18.07.2023` and three more. A four-digit trailing group is what separates
    them -- the fusion this guards against ends in the tail of a decimal, so
    `0.7980.15` still matches.
    """
    _extractions()
    fused = re.compile(r"^\d+\.\d+\d\.\d")
    dotted_date = re.compile(r"^\d{1,2}\.\d{1,2}\.\d{4}$")
    offenders = []
    for path in sorted(CORPUS.glob("*/extracted/blocks.jsonl")):
        for block in read_blocks(path):
            for column in ((block.get("table") or {}).get("columns") or []):
                for value in (column.get("values") or []) + (column.get("examples") or []):
                    text = str(value)
                    if fused.match(text) and not dotted_date.match(text):
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


def test_the_science_supplement_reads_as_english_and_not_as_a_cipher():
    """10.1126/science.adf5357's Supplementary Materials holds that paper's only
    copy of its Materials and Methods, and extracted as
    `TheVe VWXdLeV ZeUe LQWeQded WR be Whe fLUVW e[SORUaWLRQV` -- 124,178
    characters of it, reported `ok`, with nothing anywhere saying otherwise.

    Its fonts are Identity-H subsets whose ToUnicode CMap stops at glyph 75, so
    every glyph from `i` up came out as its own glyph id. `pdf._repair_glyph_encoding`
    fills the gap from the embedded fonts' own character maps.

    The count is the measurement that found the bug: of the 192 blocks in this
    file longer than 200 characters, 78 held a common English word before the
    repair and 178 do after. The other 14 are runs of numbers and gene symbols.
    """
    _needs("10.1126_science.adf5357/supplementary/01_science.adf5357_sm.pdf")
    path = _needs("10.1126_science.adf5357/extracted/blocks.jsonl")
    common = re.compile(r"\b(the|and|of|to|in|for|with|was|were|that|from)\b")
    blocks = [b for b in read_blocks(path)
              if b["source_file"].endswith("01_science.adf5357_sm.pdf")]
    long_blocks = [b for b in blocks if len(b["text"]) > 200]
    readable = [b for b in long_blocks if common.search(b["text"])]
    assert len(long_blocks) >= 190, len(long_blocks)
    assert len(readable) >= 175, (len(readable), len(long_blocks))
    assert any(b["text"].strip() == "Materials and Methods" for b in blocks)


def test_a_pdf_whose_glyphs_have_no_characters_behind_them_is_not_ok():
    """10.1038/s41588-024-01702-0's reporting summary is the case the repair
    cannot answer: every one of its 6,869 glyphs is unnamed, and its fonts are
    CID-keyed CFF subsets with identity ordering, no ToUnicode, no character map
    and glyph names of the form `cid00042`. Nothing in the file says what its
    glyphs mean, so reading it would be a guess and the honest outcome is a
    status that stops it counting as text."""
    _needs("10.1038_s41588-024-01702-0/supplementary/"
           "14_41588_2024_1702_MOESM2_ESM.pdf")
    record = json.loads(_needs("10.1038_s41588-024-01702-0/extracted/"
                               "extraction.json").read_text())
    entry = next(e for e in record["supplementary"]
                 if e["path"].endswith("14_41588_2024_1702_MOESM2_ESM.pdf"))
    assert entry["status"] == extractor.GARBLED
    assert entry["blocks"] == 0 and entry["chars"] == 0
    assert entry["glyphs_unnamed"] == entry["glyphs_drawn"]


def test_no_file_is_ok_while_most_of_its_glyphs_have_no_character():
    """The invariant the two tests above are instances of. `ok` on a file whose
    text is the parser's fallback for codes it could not map is the exact shape
    of the failure this stage exists to prevent, and it is worth asserting over
    whatever has been extracted rather than only over the two files that taught
    it."""
    for record in _extractions():
        for entry in [record["main_text"]] + record["supplementary"]:
            drawn = (entry or {}).get("glyphs_drawn")
            if not drawn or entry["status"] != extractor.OK:
                continue
            fraction = entry["glyphs_unnamed"] / drawn
            assert fraction <= L.max_unnamed_glyph_fraction, \
                (record["slug"], entry["path"], round(fraction, 4))


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
#: Recorded on PyMuPDF 1.28.0, and it does not reproduce on any earlier release --
#: measured on 1.24.0, 1.24.14, 1.25.5, 1.26.7, 1.27.1, 1.27.2, 1.27.2.2 and
#: 1.27.2.3, all of which fail this gate. That is why `requirements.txt` floors
#: pymupdf at 1.28.
#:
#: Read the failure carefully before believing an older release labels badly. What
#: moves is mostly the *alignable sample*, not the accuracy: on 1.27.2.3 the two
#: pinned papers align 70 and 79 paragraphs against 98 and 125, because PyMuPDF
#: segments the text differently, and the hit rate only slips from 88.8% to 82.9%
#: and from 92.0% to 91.1%. Since the assertion below compares absolute `correct`
#: counts, it is sensitive to how many paragraphs aligned at all -- so a change to
#: PyMuPDF can fail it without the labeller having got worse.
#:
#: A real improvement rewrites this file in the same commit. Never re-record it
#: downward to make a version pass.


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

    Re-measured over 18 XML/PDF pairs (was 2). `inverted_pairs`: sorting worse on
    13, better on 1, tied on 4. `backward_steps`: worse on 13, better on 0, tied
    on 5. The conclusion is stronger than when it was pinned, but the one
    exception showed that asserting it per article was too strict --
    10.1016/j.isci.2023.106877 moves 0.096 -> 0.092 on `inverted_pairs` while
    `backward_steps` stays put at 10, which is noise on one statistic rather than
    a reading order that got better. So the assertion is the claim itself: over
    the corpus, sorting must not win more often than it loses, and no single
    article may improve on *both* statistics at once, which is what a genuine
    improvement would look like.
    """
    import fitz

    pairs = [(p.parent / store.FULLTEXT_XML, p)
             for p in sorted(CORPUS.glob("*/" + store.FULLTEXT_PDF))]
    pairs = [(x, p) for x, p in pairs if x.is_file()]
    if not pairs:
        pytest.skip("no article here has both renditions")

    better_on_both = []
    sorting_wins = sorting_loses = 0
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
        # `inverted_pairs` is None when fewer than two paragraphs aligned, so
        # there is no ordering to be right or wrong about. 10.1038/s41586-024-08560-0
        # is an author correction -- four paragraphs, one of which aligns -- and
        # comparing None to None raised TypeError rather than skipping.
        if any(scores[s]["inverted_pairs"] is None for s in (False, True)):
            continue
        if scores[True]["inverted_pairs"] < scores[False]["inverted_pairs"]:
            sorting_wins += 1
            if scores[True]["backward_steps"] < scores[False]["backward_steps"]:
                better_on_both.append((pdf_path.parent.name, scores[False], scores[True]))
        elif scores[True]["inverted_pairs"] > scores[False]["inverted_pairs"]:
            sorting_loses += 1

    assert not better_on_both, (
        "sorting improved both reading-order statistics here; re-measure before "
        "changing pdf.py", better_on_both)
    assert sorting_wins <= sorting_loses, (
        "sorting now wins on inverted_pairs more often than it loses; re-measure "
        "before changing pdf.py", sorting_wins, sorting_loses)


# -- the specific files that taught this stage its rules ---------------------

def test_the_strict_ooxml_supplement_still_reads():
    """10.1016/j.cell.2021.01.053, mmc7.xlsx: three worksheets openpyxl reports as
    zero. It holds sampleID, Age, Sex and CoVID-19 severity."""
    path = _needs("10.1016_j.cell.2021.01.053/supplementary/04_mmc7.xlsx")
    cards, status, meta = spreadsheet.cards_from_xlsx(path.read_bytes(), str(path), L)
    assert status == "ok" and meta.get("strict_ooxml") is True
    headers = {name.lower() for card in cards for name in card.header}
    assert {"age", "sex"} <= headers


def test_the_legacy_xls_supplements_still_read():
    """The shape no fixture in `tests/fakes.py` builds: BIFF8 inside an OLE2
    container, which is what publishers actually ship.

    `make_xls` writes a bare BIFF5 record stream, so xlrd's own parser is covered
    offline but the container is not, and these files are the only thing that
    exercises it. They are also the measurement behind xlrd being a hard
    requirement rather than an extra -- 56 files, 129 MB -- so a regression here is
    the whole reason for the dependency going quiet.

    Asserted over every `.xls` in the corpus rather than one named file: the point
    is the population, and picking a favourite would let 55 regress unnoticed.
    """
    paths = sorted(CORPUS.glob("*/supplementary/*.xls"))
    if not paths:
        pytest.skip("no legacy .xls supplement in this corpus")
    failed = []
    for path in paths:
        cards, status, meta = spreadsheet.cards_from_xls(path.read_bytes(), str(path), L)
        if status != extractor.OK or not cards:
            failed.append((str(path), status, meta.get("reason")))
    assert not failed, failed


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


def test_the_non_zip_archives_still_read():
    """The six files the tar and gzip paths were added for, checked against the real
    bytes rather than a fixture.

    Two shapes no synthetic archive in `tests/fakes.py` reproduces on its own, and
    both of them decided the design: 10.1038/s41586-020-03182-8's MOESM4 is a plain
    tar under a `.tgz` name (so `mode="r:gz"` reads nothing from it) with 176 of its
    296 members AppleDouble junk, and 10.1126/science.adf5357's three `.gz` files
    are one compressed table each, three of the five here carrying the inner file's
    real name in the gzip header.

    `too_large` is a pass, not a failure: 68, 128 and 329 MB of decompressed TSV
    against a 50 MB `max_member_mb`. What must not happen is `unsupported_format`,
    which is what all six said before and which claims there is no parser.
    """
    paths = [p for p in sorted(CORPUS.rglob("*"))
             if p.is_file() and p.suffix.lower() in extractor.COMPRESSED_EXTENSIONS]
    if not paths:
        pytest.skip("no non-zip archive in this corpus")
    verdicts = {}
    for path in paths:
        result = extractor.extract_path(path, path.name, L)
        verdicts[path.name] = (result.status, result.n_tables, result.note)
        assert result.status != extractor.UNSUPPORTED, (path.name, result.note)
        assert result.status in {extractor.OK, extractor.TOO_LARGE}, (path.name, result.note)
        if result.status == extractor.TOO_LARGE:
            assert "max_member_mb" in (result.note or "")
    assert any(status == extractor.OK and tables
               for status, tables, _ in verdicts.values()), verdicts


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
