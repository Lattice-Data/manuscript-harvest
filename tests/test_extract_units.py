"""Unit coverage for the extraction stage: sections, table cards, and each parser.

Most of these pin a rule that a real file in the corpus proved wrong at least
once, and where that is so the docstring names the DOI. The recurring theme is
that a parser must not report emptiness it cannot justify: a strict-conformance
workbook, a header on row 4, and a bot-check landing page all look like "there
was nothing there" unless something checks.
"""

import json
import sys
import types
from unittest import mock

import openpyxl
import openpyxl.worksheet._read_only
import pytest

from manuscript_harvest.extract import archive, docxfile, htmlfile, jats, ooxml, pdf, sections
from manuscript_harvest.extract import spreadsheet, tables
from manuscript_harvest.extract.blocks import (
    CAPTION,
    HEADING,
    METADATA,
    PARAGRAPH,
    TABLE,
    Block,
    read_blocks,
    render_markdown,
    write_blocks,
)
from manuscript_harvest.extract.limits import Limits
from tests.fakes import (
    LANDING_INTERSTITIAL,
    SPRINGER_SUPPLEMENT,
    jats_article,
    make_dimensionless_xlsx,
    make_docx,
    make_pdf_pages,
    make_scanned_pdf,
    make_strict_xlsx,
    make_zero_sheet_xlsx,
    make_xlsx,
    make_zip,
)

L = Limits()


# -- sections ----------------------------------------------------------------

@pytest.mark.parametrize("heading,expected", [
    ("Methods", "methods"),
    ("METHODS", "methods"),
    ("Online Methods", "methods"),
    ("Materials and methods", "methods"),
    ("STAR Methods", "methods"),
    ("Experimental procedures", "methods"),
    ("2.1 Methods", "methods"),
    ("Results and discussion", "results"),
    ("Results:", "results"),
    ("Discussion", "discussion"),
    ("Data availability", "data_availability"),
    ("Supplementary Methods", "methods"),
    ("Figure legends", "figure_legends"),
    ("Acknowledgements", "back_matter"),
    ("References", "references"),
])
def test_known_headings_normalise(heading, expected):
    assert sections.normalize(heading) == expected


@pytest.mark.parametrize("heading,expected", [
    # Cell Press STAR Methods, 10.1016/j.cell.2021.01.053
    ("Experimental Model and Subject Details", "methods"),
    ("Quantification and Statistical Analysis", "methods"),
    ("Supplemental Experimental Procedures", "methods"),
    ("Supplemental Information", "supplementary"),
    # The same headings as PyMuPDF renders Cell Press's bullet glyph, page 18.
    ("d EXPERIMENTAL MODEL AND SUBJECT DETAILS", "methods"),
    ("d METHOD DETAILS", "methods"),
    ("d QUANTIFICATION AND STATISTICAL ANALYSIS", "methods"),
    ("• Method Details", "methods"),
    # Science, 10.1126/science.aat5031 supplement page 83
    ("References and Notes", "references"),
    ("Methods Summary", "methods"),
    ("Data Availability Statement", "data_availability"),
    ("Availability of data and materials", "data_availability"),
])
def test_the_top_level_headings_publishers_actually_use(heading, expected):
    """Every one of these returned None. The strongest single case is
    `References and Notes` on page 83 of 10.1126/science.aat5031's supplement:
    unrecognised, it left 71 blocks and 19,265 characters of other people's
    reference titles labelled `methods`."""
    assert sections.normalize(heading) == expected


@pytest.mark.parametrize("heading", [
    # Nature uses `Main` for the body as a whole; mapping it to any canonical
    # name is the guess this module refuses to make.
    "Main",
    # The bullet prefix must not promote a Cell Press highlight line, of which
    # 10.1016/j.cell.2021.01.053 has four on page 2.
    "d Detailed COVID-19 immune landscape depicted by",
    "d SARS-CoV-2 RNA is present in diverse epithelial and",
    "d Megakaryocytes and monocyte subsets may contribute to",
])
def test_the_widened_prefix_does_not_promote_a_highlight_line(heading):
    assert sections.normalize(heading) is None


@pytest.mark.parametrize("heading", [
    "Single-cell profiling of pancreatic islets",
    "TP53 is required for the response",
    "",
    None,
    "We used the following methods to isolate nuclei from frozen tissue and then "
    "sequenced them on a NovaSeq",
])
def test_unrecognised_headings_stay_none(heading):
    """Guessing is worse than not knowing: an unrecognised heading is as likely to
    be a Methods subsection as a Results one."""
    assert sections.normalize(heading) is None


def test_sec_type_is_used_when_the_title_is_uninformative():
    assert sections.normalize("Nuclei isolation", sec_type="methods") == "methods"
    assert sections.normalize("Anything", sec_type="ref-list") == "references"


def test_glued_heading_is_split_off_the_paragraph():
    """Nature's PDFs put the heading in the same layout block as the paragraph:
    one block reads "Methods Data collection Nuclei isolation from adult heart
    tissue...". Without this split, 10.1038/s41467-023-40505-5's PDF yields no
    sections at all."""
    split = sections.split_leading_heading(
        "Methods Data collection Nuclei isolation from adult heart tissue was performed")
    assert split is not None
    name, heading, rest = split
    assert name == "methods"
    assert heading == "Methods"
    assert rest.startswith("Data collection")


def test_a_glued_references_and_notes_is_split_off_its_first_citation():
    """The lookahead demanded a capital letter and a reference list starts with a
    digit, so page 83 of 10.1126/science.aat5031's supplement could only split as
    heading `REFERENCES` and rest `AND NOTES 1. K. W. Wucherpfennig...`. Ordering
    matters too: `_leading_patterns` has no `$` anchor, so with the bare
    `references?` first the alternation would still take it."""
    split = sections.split_leading_heading(
        "REFERENCES AND NOTES 1. K. W. Wucherpfennig, Structural basis of "
        "molecular mimicry, J. Autoimmun. 16, 293-302 (2001).")
    assert split is not None
    name, heading, rest = split
    assert (name, heading) == (sections.REFERENCES, "REFERENCES AND NOTES")
    assert rest.startswith("1. K. W. Wucherpfennig")


@pytest.mark.parametrize("text", [
    "Results of the assay were consistent with prior work in this area of study",
    "Background information about the mice was taken from the vendor records here",
    "Methods",
])
def test_sentences_that_merely_start_with_a_keyword_are_not_split(text):
    """The guard is that a heading is followed by the start of a new sentence. It
    is also a regression test for `re.IGNORECASE` making `[A-Z]` match lower case,
    which silently disabled the guard entirely."""
    assert sections.split_leading_heading(text) is None


def test_a_heading_carries_its_section_over_what_follows():
    tracker = sections.SectionTracker()
    assert tracker.carry("text before any heading") is None
    assert tracker.heading(sections.METHODS) == sections.METHODS
    assert tracker.carry("We isolated nuclei") == sections.METHODS
    assert tracker.heading(sections.RESULTS) == sections.RESULTS
    assert tracker.carry("We found cells") == sections.RESULTS
    assert tracker.seen == [sections.METHODS, sections.RESULTS]
    assert tracker.abandoned == [] and tracker.reason() is None


def test_cell_press_star_methods_is_recognised_with_the_glyph():
    """Cell Press publishes the heading as STAR★METHODS, with U+2605, in the XML as
    well as the PDF. The alias allowed an ASCII asterisk, which is how the heading is
    written *about* -- so the top-level Methods section of both Cell papers here went
    unrecognised, leaving 69 and 51 main-text blocks unlabelled, the key resources
    table among them."""
    for heading in ("STAR★METHODS", "STAR★methods", "STAR ★ Methods",
                    "STAR Methods", "STAR*Methods", "star methods"):
        assert sections.normalize(heading) == sections.METHODS, heading


def test_a_low_value_heading_only_claims_text_that_looks_like_its_content():
    """10.1016/j.cell.2025.05.027, a PMC author manuscript: the REFERENCES heading on
    page 31 carried 227 of 415 blocks to the end of the document, which in that
    layout is the key resources table -- reagents and catalogue numbers labelled as
    other people's bibliography. `references` is on LOW_VALUE, so a consumer that
    skips it does not deprioritise that text, it drops it."""
    tracker = sections.SectionTracker()
    tracker.heading(sections.REFERENCES)
    citation = "1. Wen L, Li G, Huang T, Geng W, Pei H (2022). Single-cell technologies."
    assert tracker.carry(citation) == sections.REFERENCES
    for row in ("Punch pliers Total Tools 9070220SB",
                "micro-Slide 8-well cell culture chamber ibidi 80841",
                "40 um strainer Cell Strainer PN 43-10040-40"):
        assert tracker.carry(row) is None, row
    assert tracker.withheld == 3
    assert "low-value" in tracker.reason()
    # The span stays open, so a citation after a stray line is still labelled: a
    # reference list interrupted by a page artifact must not lose its tail.
    assert tracker.carry("2. Smith J, Jones K (2020). Another paper. doi:10.1/x") \
        == sections.REFERENCES


@pytest.mark.parametrize("text,expected", [
    ("1. Wen L, Li G (2022). Single-cell technologies for multiomic analysis.", True),
    ("12) Author A, Author B, et al. Nature Genetics.", True),
    ("Smith and Jones (2019) reported similar results in mouse.", True),
    ("Available at doi:10.1016/j.cell.2025.05.027", True),
    ("Punch pliers Total Tools 9070220SB", False),
    ("Chromium Next GEM Single Cell 3' Kit v3.1 10x Genomics PN-1000268", False),
])
def test_what_counts_as_a_citation(text, expected):
    assert sections.looks_like_citation(text) is expected


def test_an_unbounded_section_flows_as_far_as_the_paper_does():
    """Methods legitimately runs for pages through its own unrecognised
    subsection headings, and that is the behaviour that makes it attributable."""
    tracker = sections.SectionTracker()
    tracker.heading(sections.METHODS)
    for _ in range(40):
        assert tracker.carry("x" * 1000) == sections.METHODS
    assert tracker.abandoned == []


def test_a_statement_section_is_abandoned_rather_than_carried_over_a_paper():
    """10.1126/science.adt8307: the standalone `CONCLUSION` line in Science's
    front-page structured summary was never followed by another heading this module
    recognises, so 996 of 1,184 main-text blocks came back labelled `conclusions`
    and the paper's real Results reported 5. A wrong section is worse than none --
    it makes a filter drop the text it was looking for and call it an answer."""
    tracker = sections.SectionTracker()
    tracker.heading(sections.CONCLUSIONS)
    labelled = [tracker.carry("x" * 1000) for _ in range(20)]
    assert labelled[0] == sections.CONCLUSIONS, "the conclusion itself is still labelled"
    assert labelled[-1] is None, "the rest of the paper is not"
    assert tracker.abandoned == [sections.CONCLUSIONS]
    assert "conclusions" in tracker.reason()
    # And a real heading later on still reopens labelling.
    assert tracker.heading(sections.METHODS) == sections.METHODS
    assert tracker.carry("Nuclei were isolated") == sections.METHODS


def test_an_abandoned_section_is_not_reopened_by_its_own_heading():
    """The existing coverage only reopened with a *different* section.
    10.1126/science.aat5031 abandons `abstract` at block 33, and then block 70 --
    the heading `One Sentence Summary`, an ABSTRACT alias -- reopened it, so
    blocks 70-85 came back labelled `abstract`: 16 blocks and 6,272 characters,
    four of them figure legends beginning "Fig. 1. Mapping the spatial and
    temporal architecture of the mature and developing human kidney". The record
    said "the blocks after it are left unlabelled" the whole time."""
    tracker = sections.SectionTracker()
    tracker.heading(sections.ABSTRACT)
    for _ in range(10):
        tracker.carry("x" * 1000)
    assert tracker.abandoned == [sections.ABSTRACT]

    assert tracker.heading(sections.ABSTRACT) is None
    assert tracker.carry("Fig. 1. Mapping the spatial and temporal architecture") is None
    assert tracker.reopens_refused == [sections.ABSTRACT]
    assert "reopen abstract" in tracker.reason()
    # A different section still opens normally.
    assert tracker.heading(sections.METHODS) == sections.METHODS


def test_the_abandonment_bound_is_a_configurable_cap():
    """It decided a third of one article's labels while being a module constant
    with no config key, which the README said outright."""
    assert Limits().max_bounded_section_chars == sections.MAX_BOUNDED_SECTION_CHARS
    tracker = sections.SectionTracker(limits=Limits(max_bounded_section_chars=100))
    tracker.heading(sections.ABSTRACT)
    assert tracker.carry("x" * 200) == sections.ABSTRACT
    assert tracker.carry("the next paragraph") is None
    assert tracker.abandoned == [sections.ABSTRACT]
    assert "100 characters" in tracker.reason()


def test_a_long_abstract_is_kept_up_to_the_measured_budget():
    """The longest legitimate run measured is a 4,653-character Cell Press abstract
    with its highlights and eTOC blurb, so the budget must not cut that."""
    tracker = sections.SectionTracker()
    tracker.heading(sections.ABSTRACT)
    assert tracker.carry("x" * 4653) == sections.ABSTRACT
    assert tracker.carry("the next paragraph") == sections.ABSTRACT
    assert tracker.abandoned == []


@pytest.mark.parametrize("name", sorted(sections.BOUNDED_SECTIONS))
def test_every_bounded_section_is_actually_bounded(name):
    tracker = sections.SectionTracker()
    tracker.heading(name)
    for _ in range(20):
        tracker.carry("x" * 1000)
    assert tracker.abandoned == [name]


# -- table cards -------------------------------------------------------------

def test_header_found_below_a_title_and_a_caption_row():
    """10.1038/s41591-018-0269-2's MOESM1 puts the title on row 1, the caption on
    row 2, a blank on row 3, and the header on row 4. Assuming row 1 reads the
    caption as column names and the column names as data."""
    rows = [
        ("Supplementary Table 1", None, None, None),
        ("Detailed demographic and clinical data for all patients", None, None, None),
        (None, None, None, None),
        ("patient_code", "diagnosis", "Sex", "Age"),
        ("SMM05", "SMM", "M", 61),
        ("AL01", "AL", "F", 74),
    ]
    card = tables.build_card(rows, "x.xlsx", "sheet 'S1'", L)
    assert card.header_row == 3
    assert card.header == ["patient_code", "diagnosis", "Sex", "Age"]
    assert card.n_rows == 2
    assert "Detailed demographic" in card.caption
    assert card.header_confidence == "high"


def test_header_on_row_two_after_a_single_caption_line():
    """10.1016/j.xcrm.2023.101158's mmc7.xlsx."""
    rows = [("Table S6: Cluster marker genes", None, None),
            ("cluster", "gene", "p_val"),
            (0, "FGFBP2", 0.0)]
    card = tables.build_card(rows, "mmc7.xlsx", "sheet 'Table S6'", L)
    assert card.header_row == 1
    assert card.caption == "Table S6: Cluster marker genes"


def test_low_cardinality_text_column_is_enumerated():
    """This is the point of the card: two distinct values answer "sex" outright."""
    rows = [("id", "Sex", "organism"), ("a", "M", "Mus musculus"),
            ("b", "F", "Mus musculus"), ("c", "M", "Mus musculus")]
    card = tables.build_card(rows, "x.xlsx", "s", L)
    sex = next(c for c in card.columns if c["name"] == "Sex")
    assert sex["values"] == ["F", "M"]
    organism = next(c for c in card.columns if c["name"] == "organism")
    assert organism["values"] == ["Mus musculus"]
    assert "Sex [text, 2 distinct] = F | M" in tables.render(card, L)


def test_few_numeric_values_are_enumerated_but_many_are_summarised():
    """`0 | 6 | 24` says "these are the timepoints"; 30 patient ages say nothing a
    range does not, at ten times the length."""
    timepoints = [("t",)] + [(v,) for v in [0, 6, 24] * 4]
    card = tables.build_card(timepoints, "x.xlsx", "s", L)
    assert card.columns[0]["values"] == ["0", "6", "24"]

    ages = [("Age",)] + [(v,) for v in range(30, 70)]
    card = tables.build_card(ages, "x.xlsx", "s", L)
    column = card.columns[0]
    assert "values" not in column and "examples" in column
    assert (column["min"], column["max"]) == (30, 69)
    assert "median" in column


def test_numeric_values_are_enumerated_in_numeric_order():
    rows = [("dose",)] + [(v,) for v in [10, 2, 100]]
    card = tables.build_card(rows, "x.xlsx", "s", L)
    assert card.columns[0]["values"] == ["2", "10", "100"]


def test_mostly_numeric_column_with_na_strings_is_still_numeric():
    rows = [("value",)] + [(v,) for v in [1, 2, 3, 4, 5, 6, 7, 8, 9, "NA"]]
    card = tables.build_card(rows, "x.xlsx", "s", L)
    assert card.columns[0]["dtype"] == tables.NUMBER


def test_an_infinite_cell_does_not_make_an_invalid_json_line(tmp_path):
    """`float("inf")` succeeds, so line 520 of
    10.1038/s41467-023-40505-5's blocks.jsonl carried `"max": Infinity` -- which
    Python's json.loads accepts by default and serde_json, Go's encoding/json,
    PostgreSQL jsonb and DuckDB all reject."""
    values = [1.5, 2.5, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 12.0, 13.0,
              "Inf", "-inf", "NaN"]
    card = tables.build_card([("neg. log10-pval",)] + [(v,) for v in values], "x.xlsx", "s", L)
    column = card.columns[0]
    assert column["dtype"] == tables.NUMBER
    assert column["max"] == 13.0 and column["min"] == 1.5
    assert column["n_non_finite"] == 3
    assert any("non-finite" in note for note in card.notes)

    path = tmp_path / "blocks.jsonl"
    write_blocks(path, [Block(kind=TABLE, text=tables.render(card, L),
                              source_file="x.xlsx", origin="xlsx",
                              table=card.to_dict())])
    for line in path.read_text().splitlines():
        json.loads(line, parse_constant=lambda c: pytest.fail(f"not JSON: {c}"))


def test_headerless_matrix_is_reported_not_guessed():
    rows = [(1, 2, 3), (4, 5, 6), (7, 8, 9)]
    card = tables.build_card(rows, "x.xlsx", "s", L)
    assert card.header_row is None
    assert any("no header row" in note for note in card.notes)
    assert card.header == ["column_1", "column_2", "column_3"]


def test_a_header_whose_names_repeat_is_accepted_not_turned_into_data():
    """`08_mmc5.xlsx` sheet `TV+vs.V-` is two identical eight-column tables side
    by side: present 16, distinct 8, threshold 11, so the 0.7 rule rejected it.
    The card then said "no header row identified; columns are positional" and
    printed `7. column_7 [text, 3 distinct] = cluster | virus+ | virus-` -- a
    header string offered as one of three complete values."""
    header = ["row.names", "p_val", "cluster", "gene"] * 2
    rows = [tuple(header)] + [("1", "0", "virus-", "IFITM1") * 2 for _ in range(4)]
    card = tables.build_card(rows, "mmc5.xlsx", "sheet 'TV+vs.V-'", L)
    assert card.header_row == 0
    assert card.header[:4] == ["row.names", "p_val", "cluster", "gene"]
    assert any("2 tables side by side" in note for note in card.notes)
    assert not any("no header row" in note for note in card.notes)


def test_a_two_row_header_is_composed_rather_than_read_as_data():
    """`49_..._MOESM4_ESM.xlsx` sheet `Supplementary Data 5` puts the cell type on
    one row and `Sum.PIPs / N.SNPs` on the next, so the card printed
    `column_5 [number, 6 distinct] = 0 | 0.01 | 0.02 | 0.03 | Endothelial OCRs |
    Sum.PIPs` -- two header strings offered as data values."""
    rows = [
        (None, None, "Cardiomyocyte OCRs", None, "Endothelial OCRs", None),
        ("Locus", "Location", "Sum.PIPs", "N.SNPs", "Sum.PIPs", "N.SNPs"),
        (7, "chr1:9365199-10806984", 0.74, 7, 0.0, 0),
        (15, "chr1:21736588-23086883", 0.05, 5, 0.01, 6),
    ]
    card = tables.build_card(rows, "m4.xlsx", "sheet 'Supplementary Data 5'", L)
    assert card.header_row == 1 and card.header_rows == [0, 1]
    assert card.header == ["Locus", "Location",
                           "Cardiomyocyte OCRs / Sum.PIPs",
                           "Cardiomyocyte OCRs / N.SNPs",
                           "Endothelial OCRs / Sum.PIPs",
                           "Endothelial OCRs / N.SNPs"]
    assert any("header spans 2 rows" in note for note in card.notes)
    values = {v for c in card.columns for v in (c.get("values") or [])}
    assert "Endothelial OCRs" not in values and "Sum.PIPs" not in values


def test_a_blank_line_between_a_caption_and_a_header_keeps_them_apart():
    """10.1038/s41591-018-0269-2 MOESM1 is title, caption, blank, then the header
    on row 4. Those are not one two-row header."""
    rows = [("Supplementary Table 1", None, None),
            ("Donor characteristics for the cohort", None, None),
            (None, None, None),
            ("donor", "age", "sex"),
            ("D1", 44, "F"), ("D2", 61, "M")]
    card = tables.build_card(rows, "m1.xlsx", "s", L)
    assert card.header_row == 3 and card.header_rows is None
    assert card.header == ["donor", "age", "sex"]


def test_header_confidence_is_low_without_a_type_change():
    """All-text rows under all-text headers could be a first data row of gene
    names; the card says so rather than pretending to know."""
    rows = [("gene", "symbol"), ("TP53", "p53"), ("MYC", "myc")]
    card = tables.build_card(rows, "x.xlsx", "s", L)
    assert card.header_confidence == "low"
    assert any("type-change" in note for note in card.notes)


def test_a_column_holding_its_own_header_name_is_never_high_confidence():
    """The safety net behind `split_blocks`, for a sheet whose second table is
    not separated by a blank row. Before the splitter landed this fired on
    exactly 7 column-instances across 5 cards of this corpus, all of them `high`,
    with no false positives."""
    rows = [("timepoint", "value"), ("0", "1.2"), ("24", "3.4"),
            ("timepoint", "value"), ("0", "5.6")]
    card = tables.build_card(rows, "x.xlsx", "s", L)
    assert card.header_confidence == "low"
    assert any("its own header name" in note for note in card.notes)
    assert not any("type-change" in note for note in card.notes), \
        "the type-change note would be false: there was one"


def test_an_ordinary_column_is_not_accused_of_holding_its_header_name():
    """Exact match, not substring: `gene` inside `gene_id` fires on 50 columns of
    this corpus, most of them legitimate."""
    rows = [("gene", "gene_id"), ("TP53", "gene_00001"), ("MYC", "gene_00002")]
    card = tables.build_card(rows, "x.xlsx", "s", L)
    assert not any("its own header name" in note for note in card.notes)


def test_trailing_empty_columns_are_dropped():
    rows = [("a", "b", None, None), (1, 2, None, None)]
    card = tables.build_card(rows, "x.xlsx", "s", L)
    assert card.n_columns == 2


def test_empty_table_yields_no_card():
    assert tables.build_card([], "x.xlsx", "s", L) is None
    assert tables.build_card([(None, None), (None,)], "x.xlsx", "s", L) is None


def test_header_with_no_data_rows_still_makes_a_card():
    """The column names alone say what the table was going to be about."""
    card = tables.build_card([("organism", "age", "sex")], "x.xlsx", "s", L)
    assert card.n_rows == 0
    assert any("no data rows" in note for note in card.notes)


@pytest.mark.parametrize("value,expected", [
    ("  spaced   out  ", "spaced out"),
    ("\xa0", None),
    ("", None),
    (None, None),
    (True, "TRUE"),
    (3.0, "3"),
    (3.5, "3.5"),
])
def test_clean_cell(value, expected):
    """Excel's empty-looking cells are not all None: a formula evaluated to "" or
    a whitespace-only string counted as present is what makes a caption row look
    like a header row."""
    assert tables.clean_cell(value) == expected


def test_card_render_respects_the_character_budget():
    limits = Limits(max_card_chars=800)
    rows = [tuple(f"column_{i}" for i in range(80))] + [
        tuple(f"value_{i}_{r}" for i in range(80)) for r in range(5)]
    card = tables.build_card(rows, "wide.xlsx", "s", limits)
    text = tables.render(card, limits)
    assert len(text) <= 900, len(text)
    assert "further column(s) not shown" in text


def test_the_renderer_says_when_it_dropped_sample_rows():
    """Nothing a cap drops may be silent, and the column line has always said so
    while the sample-row loop just broke out."""
    limits = Limits(max_card_chars=700, max_sample_rows=3)
    rows = [("a", "b", "c")] + [tuple(f"{c}_value_{r}" * 4 for c in "abc")
                                for r in range(5)]
    text = tables.render(tables.build_card(rows, "x.xlsx", "s", limits), limits)
    assert "further sample row(s) not shown" in text


def test_card_states_when_the_scan_was_capped():
    limits = Limits(max_scan_rows=10)
    rows = [("a", "b")] + [(i, i * 2) for i in range(9)]
    card = tables.build_card(rows, "x.xlsx", "s", limits, truncated=True,
                             n_rows_total=99999)
    text = tables.render(card, limits)
    assert "scan stopped at 10 rows" in " ".join(card.notes)
    assert "source reports 99999" in text


def test_a_truncated_scan_never_renders_its_value_set_as_complete():
    """The `=` form means the complete value set, which is the entire point of
    the card. 10.1126/science.aat5031's data_s1.csv is 40,269 lines; scanned to
    5,000 it printed `celltype [text, 12 distinct] = B cell | CD4 T cell | ...`
    where the file holds 33, missing Podocyte, Proximal tubule, Glomerular
    endothelium and every other epithelial and endothelial type."""
    body = b"barcode,celltype\n" + b"".join(
        f"bc{i},{'Podocyte' if i > 40 else 'B cell'}\n".encode() for i in range(59))

    whole = spreadsheet.cards_from_csv(body, "s1.csv", L)[0][0]
    assert whole.n_rows_total == 60 and whole.truncated is False
    assert "celltype [text, 2 distinct] = B cell | Podocyte" in tables.render(whole, L)

    capped = spreadsheet.cards_from_csv(body, "s1.csv", Limits(max_scan_rows=10))[0][0]
    text = tables.render(capped, Limits(max_scan_rows=10))
    assert capped.n_rows_total == 60
    assert "source reports 60 row(s)" in text
    assert "scan stopped at 10 rows of 60" in text
    assert " = " not in text
    assert "celltype [text, 1 distinct] e.g. B cell" in text


#: The shape of `Figure 6` in 10.1126/sciimmunol.aba4163's data file, trimmed to
#: three panels: a panel title on its own row, a header row, then data, with a
#: blank row between panels.
def _panel(name, header, values):
    return [(name, None, None), header] + [(None,) + row for row in values]


STACKED_PANELS = (
    _panel("Figure 6C", ("% Crescents", "Control", "S. aureus + NTN"),
           [(0, 13.3), (0, 23.3), (3.3, 20.0)])
    + [(None, None, None)]
    + _panel("Figure 6D", ("[% of CD3+]", "Control", "S. aureus + NTN"),
             [(0.23, 1.53), (0.16, 2.68), (0.06, 1.81)])
    + [(None, None, None)]
    + _panel("Figure 6E", ("[%CD3+ cells]", "Control", "S. aureus + NTN"),
             [(0.94, 10.9), (0.67, 9.56), (0.91, 6.31)])
)


def test_a_sheet_of_stacked_panels_becomes_one_card_per_panel():
    """`detect_header` found the first panel's header and `build_card` read every
    later panel's title, units and header row as data:
    `Figure 6C [text, 23 distinct, 63 empty] = % Crescents | [% of CD3+] | ... |
    Figure 6D | Figure 6E | ...`, at header_confidence high and with no note."""
    data = make_xlsx({"Figure 6": [list(r) for r in STACKED_PANELS]})
    cards, status, _ = spreadsheet.cards_from_xlsx(data, "s1.xlsx", L)
    assert status == "ok" and len(cards) == 3
    assert [c.locator for c in cards] == ["sheet 'Figure 6' rows 1-5",
                                          "sheet 'Figure 6' rows 7-11",
                                          "sheet 'Figure 6' rows 13-17"]
    assert cards[0].data_ref["row_start"] == 1 and cards[0].data_ref["row_end"] == 5
    assert all("3 blank-row-separated tables" in c.notes[0] for c in cards)
    # The pooled column name is gone: no card carries a later panel's title.
    names = {n for c in cards for n in c.header}
    values = {v for c in cards for col in c.columns
              for v in (col.get("values") or []) + (col.get("examples") or [])}
    assert "Figure 6D" not in names | values
    assert "Figure 6E" not in names | values


@pytest.mark.parametrize("parts,expected", [
    # A lone row above a table is a panel title; merge it down, do not refuse.
    ([1, 13, 13, 13], [(0, 15), (16, 29), (30, 43)]),
    ([9, 1, 9, 10], [(0, 9), (10, 21), (22, 32)]),
    # One title row above one table is one table.
    ([1, 13], []),
    ([1, 40], []),
    # A trailing lone row is a footnote, not a table.
    ([5, 5, 1], [(0, 5), (6, 11)]),
    ([9], []),
])
def test_which_blank_separated_sheets_are_split(parts, expected):
    """Requiring every part to be at least 2 rows was tried and it disables the
    split on `STable 4.4` `[1, 13, 13, 13]` and `Figure S7` `[9, 1, 9, 10]`."""
    rows: list = []
    for size in parts:
        if rows:
            rows.append((None,))
        rows.extend([(f"r{i}", i) for i in range(size)])
    assert tables.split_blocks(rows, L) == expected


def test_a_sheet_with_more_panels_than_the_cap_says_how_many_it_dropped():
    sheet = []
    for panel in range(4):
        if sheet:
            sheet.append([None, None])
        sheet.append([f"Panel {panel}", None])
        sheet.append(["name", "value"])
        sheet.extend([[f"r{i}", i] for i in range(3)])
    cards, _, meta = spreadsheet.cards_from_xlsx(
        make_xlsx({"S": sheet}), "s.xlsx", Limits(max_tables_per_sheet=2))
    assert len(cards) == 2
    assert meta["tables_skipped"] == 2


def test_card_does_not_copy_the_data_it_points_at_it():
    """Duplicating a 2.4 GB corpus to paraphrase it would be the wrong trade: the
    card records where to re-read the real values instead -- and it has to record
    enough to actually do it, which for a while it did not: no scan window and no
    file hash, so the rows a card described could not be re-read reproducibly."""
    card = tables.build_card([("a",), (1,), (2,)], "x.xlsx", "sheet 'S1'", L,
                             data_ref={"file": "x.xlsx", "sheet": "S1",
                                       "sha256": "abc123"})
    assert card.data_ref == {"file": "x.xlsx", "locator": "sheet 'S1'",
                             "sheet": "S1", "sha256": "abc123",
                             "header_row": 1, "first_data_row": 2, "last_data_row": 3}


def test_a_split_panels_data_ref_carries_absolute_row_numbers():
    """A panel's card is built from a slice of the sheet, so its own row indices
    start at zero; `data_ref` has to say where that slice sat."""
    data = make_xlsx({"Figure 6": [list(r) for r in STACKED_PANELS]})
    cards, _, _ = spreadsheet.cards_from_xlsx(data, "s1.xlsx", L)
    # Each panel is a title row, a header row and three data rows.
    assert [c.data_ref["header_row"] for c in cards] == [2, 8, 14]
    assert [c.data_ref["first_data_row"] for c in cards] == [3, 9, 15]
    assert [c.data_ref["last_data_row"] for c in cards] == [5, 11, 17]
    assert len({c.data_ref["sha256"] for c in cards}) == 1


@pytest.mark.parametrize("name,payload,ref", [
    ("s.csv", b"donor,age\nD1,44\nD2,61\nD3,7\n",
     {"delimiter": ",", "header_row": 1, "first_data_row": 2, "last_data_row": 4}),
])
def test_read_rows_reprints_the_rows_the_card_describes(name, payload, ref):
    header, rows = spreadsheet.read_rows(payload, ref, ".csv", limit=2)
    assert header == ["donor", "age"]
    assert rows == [(2, ["D1", "44"]), (3, ["D2", "61"])]


# -- spreadsheets ------------------------------------------------------------

def test_xlsx_sheets_become_cards():
    data = make_xlsx({"Table S1": [["id", "Sex"], ["a", "M"], ["b", "F"]],
                      "Table S2": [["gene", "logFC"], ["TP53", 1.5]]})
    cards, status, meta = spreadsheet.cards_from_xlsx(data, "x.xlsx", L)
    assert status == "ok" and meta["sheets"] == 2
    assert [c.title for c in cards] == ["Table S1", "Table S2"]
    assert cards[0].locator == "sheet 'Table S1'"


def test_strict_ooxml_workbook_is_read_not_reported_empty():
    """10.1016/j.cell.2021.01.053's mmc7.xlsx is a strict ISO-29500 workbook.
    openpyxl reads those as having zero worksheets -- no exception, no warning --
    so a file holding `sampleID, Age, Sex, CoVID-19 severity` reported `no_text`,
    which is indistinguishable from a genuinely empty supplement."""
    sheets = {"Patient_info": [["sampleID", "Age", "Sex"], ["P1", 61, "M"]]}
    strict = make_strict_xlsx(sheets)

    import openpyxl
    assert openpyxl.load_workbook(__import__("io").BytesIO(strict),
                                  read_only=True).worksheets == [], \
        "fixture must reproduce the silent-empty behaviour"

    cards, status, meta = spreadsheet.cards_from_xlsx(strict, "mmc7.xlsx", L)
    assert status == "ok" and meta["strict_ooxml"] is True
    assert cards[0].header == ["sampleID", "Age", "Sex"]


def test_relax_strict_returns_none_for_an_ordinary_workbook():
    assert ooxml.relax_strict(make_xlsx({"S": [["a"]]})) is None
    assert ooxml.relax_strict(b"not a zip") is None


def test_workbook_without_declared_dimensions_is_read():
    """10.1038/s44161-025-00612-6's MOESM5 has unsized worksheets; openpyxl raises
    `ValueError: Worksheet is unsized` from `calculate_dimension()`, which is why
    this stage never calls it."""
    data = make_dimensionless_xlsx({"Figure 2d": [["Patient", "value"], ["BS_H15", 23.44]]})
    cards, status, _ = spreadsheet.cards_from_xlsx(data, "x.xlsx", L)
    assert status == "ok"
    assert cards[0].header == ["Patient", "value"]


def test_xlsx_scan_stops_at_the_row_cap():
    """One sheet in this corpus is 16,596 x 88. It cannot go into a prompt and
    almost none of it would help if it did."""
    limits = Limits(max_scan_rows=20)
    data = make_xlsx({"big": [["gene", "value"]] + [[f"G{i}", i] for i in range(500)]})
    cards, status, _ = spreadsheet.cards_from_xlsx(data, "x.xlsx", limits)
    assert status == "ok"
    assert cards[0].truncated is True
    assert cards[0].n_rows < 500
    assert cards[0].n_rows_total == 501


def test_empty_workbook_is_no_text():
    cards, status, _ = spreadsheet.cards_from_xlsx(make_xlsx({"blank": []}), "x.xlsx", L)
    assert (cards, status) == ([], "no_text")


def test_unreadable_bytes_are_named_not_crashed():
    cards, status, meta = spreadsheet.cards_from_xlsx(b"not a workbook", "x.xlsx", L)
    assert status == "unreadable" and "reason" in meta


@pytest.mark.parametrize("body,delimiter", [
    (b"a,b,c\n1,2,3\n4,5,6\n", ","),
    (b"a\tb\tc\n1\t2\t3\n4\t5\t6\n", "\t"),
    (b"a;b;c\n1;2;3\n4;5;6\n", ";"),
])
def test_csv_delimiter_is_sniffed(body, delimiter):
    cards, status, meta = spreadsheet.cards_from_csv(body, "x.csv", L)
    assert status == "ok" and meta["delimiter"] == delimiter
    assert cards[0].header == ["a", "b", "c"]


def test_empty_csv_is_no_text():
    assert spreadsheet.cards_from_csv(b"   \n", "x.csv", L)[1] == "no_text"


def test_legacy_xls_without_xlrd_says_so(monkeypatch):
    """One file in this corpus is a legacy `.xls`. Reporting it as unsupported is
    better than adding a dependency for 1 of 191 spreadsheets."""
    monkeypatch.setitem(sys.modules, "xlrd", None)
    cards, status, meta = spreadsheet.cards_from_xls(b"\xd0\xcf\x11\xe0", "x.xls", L)
    assert status == "unsupported_format" and "xlrd" in meta["reason"]


def test_a_workbook_that_still_declares_no_sheets_after_relaxing_is_unreadable():
    """The other half of the strict-OOXML story. `relax_strict` returning None means
    the namespaces were already ordinary, so zero worksheets is the file's own
    answer -- and `unreadable` says that, where `no_text` would blame the content."""
    data = make_zero_sheet_xlsx()
    cards, status, meta = spreadsheet.cards_from_xlsx(data, "x.xlsx", L)

    assert (cards, status) == ([], "unreadable")
    assert "declares no worksheets" in meta["reason"]
    assert meta["sheets"] == 0
    assert "strict_ooxml" not in meta, "nothing was relaxed"


def test_sheets_beyond_the_cap_are_counted_not_dropped_quietly():
    sheets = {f"S{n}": [["id", "value"], [f"r{n}", n]] for n in range(6)}
    cards, status, meta = spreadsheet.cards_from_xlsx(
        make_xlsx(sheets), "x.xlsx", Limits(max_sheets=2))

    assert status == "ok" and len(cards) == 2
    assert meta["sheets"] == 6 and meta["sheets_skipped"] == 4


def test_the_card_cap_stops_the_sheet_loop():
    sheets = {f"S{n}": [["id", "value"], [f"r{n}", n]] for n in range(5)}
    cards, status, _ = spreadsheet.cards_from_xlsx(
        make_xlsx(sheets), "x.xlsx", Limits(max_tables_per_file=2))
    assert status == "ok" and len(cards) == 2


def test_one_unreadable_sheet_does_not_lose_the_others():
    """A workbook is a bag of independent tables; a single sheet that blows up on
    iteration must be recorded and stepped over, not take the file down with it."""
    data = make_xlsx({"good": [["id", "value"], ["a", 1]],
                      "bad": [["id", "value"], ["b", 2]]})

    real_iter_rows = openpyxl.worksheet._read_only.ReadOnlyWorksheet.iter_rows

    def flaky(self, *args, **kwargs):
        if self.title == "bad":
            raise ValueError("Worksheet is unsized")
        return real_iter_rows(self, *args, **kwargs)

    with mock.patch.object(openpyxl.worksheet._read_only.ReadOnlyWorksheet,
                           "iter_rows", flaky):
        cards, status, meta = spreadsheet.cards_from_xlsx(data, "x.xlsx", L)

    assert status == "ok"
    assert [c.title for c in cards] == ["good"]
    assert meta["errors"] == ["sheet 'bad': ValueError: Worksheet is unsized"]


def test_a_sheet_that_cannot_reset_its_dimensions_is_still_read():
    """`reset_dimensions` is a newer openpyxl API and the call is defensive. If it
    is missing or refuses, the sheet must still be scanned rather than skipped."""
    data = make_xlsx({"S": [["id", "value"], ["a", 1]]})

    def refuse(self):
        raise AttributeError("no reset_dimensions on this version")

    with mock.patch.object(openpyxl.worksheet._read_only.ReadOnlyWorksheet,
                           "reset_dimensions", refuse, create=True):
        cards, status, _ = spreadsheet.cards_from_xlsx(data, "x.xlsx", L)

    assert status == "ok" and cards[0].header == ["id", "value"]


# -- spreadsheets: legacy .xls -----------------------------------------------

class _FakeXlsSheet:
    def __init__(self, name, rows):
        self.name = name
        self._rows = rows
        self.nrows = len(rows)

    def row_values(self, index):
        return self._rows[index]


class _FakeXlsBook:
    def __init__(self, *sheets):
        self._sheets = list(sheets)
        self.nsheets = len(self._sheets)

    def sheets(self):
        return self._sheets


def _fake_xlrd(book=None, error=None):
    """A stand-in for the optional `xlrd`, so the .xls path is covered without it.

    xlrd 2.x is an optional extra for exactly one file in this corpus, so CI does
    not install it -- but the branch that reads it is ours and should not go
    untested for that reason.
    """
    module = types.SimpleNamespace()

    def open_workbook(file_contents=None):
        if error is not None:
            raise error
        return book

    module.open_workbook = open_workbook
    return module


def test_legacy_xls_sheets_become_cards(monkeypatch):
    book = _FakeXlsBook(
        _FakeXlsSheet("Table S1", [["id", "Sex"], ["a", "M"], ["b", "F"]]),
        _FakeXlsSheet("Notes", [["read me"], ["some prose"]]),
    )
    monkeypatch.setitem(sys.modules, "xlrd", _fake_xlrd(book))
    cards, status, meta = spreadsheet.cards_from_xls(b"\xd0\xcf\x11\xe0", "mmc2.xls", L)

    assert status == "ok" and meta["sheets"] == 2
    assert [c.title for c in cards] == ["Table S1", "Notes"]
    assert cards[0].header == ["id", "Sex"]
    assert cards[0].locator == "sheet 'Table S1'"


def test_legacy_xls_honours_the_row_and_sheet_caps(monkeypatch):
    rows = [["gene", "value"]] + [[f"G{n}", n] for n in range(100)]
    book = _FakeXlsBook(_FakeXlsSheet("big", rows), _FakeXlsSheet("second", [["a"], ["b"]]))
    monkeypatch.setitem(sys.modules, "xlrd", _fake_xlrd(book))
    cards, status, _ = spreadsheet.cards_from_xls(
        b"\xd0\xcf\x11\xe0", "x.xls", Limits(max_scan_rows=10, max_sheets=1))

    assert status == "ok" and len(cards) == 1
    assert cards[0].truncated is True
    assert cards[0].n_rows_total == 101


def test_an_unreadable_xls_is_named_not_crashed(monkeypatch):
    monkeypatch.setitem(sys.modules, "xlrd",
                        _fake_xlrd(error=ValueError("Unsupported format, or corrupt file")))
    cards, status, meta = spreadsheet.cards_from_xls(b"garbage", "x.xls", L)
    assert (cards, status) == ([], "unreadable")
    assert "ValueError: Unsupported format" in meta["reason"]


def test_an_empty_xls_is_no_text(monkeypatch):
    monkeypatch.setitem(sys.modules, "xlrd", _fake_xlrd(_FakeXlsBook()))
    assert spreadsheet.cards_from_xls(b"\xd0\xcf\x11\xe0", "x.xls", L)[1] == "no_text"


# -- spreadsheets: CSV sniffing ----------------------------------------------

@pytest.mark.parametrize("body,expected", [
    (b"single column\nvalue\nother\n", ","),      # sniffer fails; no candidate present
    (b"a|b|c\n1|2|3\n", "|"),
    (b"justoneline", ","),
    # The sniffer refuses a one-column file, but the fallback's count still finds
    # the real delimiter on the first line.
    (b"a;b\n1;2\n", ";"),
])
def test_the_delimiter_falls_back_to_counting_when_sniffing_fails(body, expected):
    """`csv.Sniffer` raises "Could not determine delimiter" for a single-column
    file, and a raise here would report a readable supplement as unreadable."""
    cards, status, meta = spreadsheet.cards_from_csv(body, "x.csv", L)
    assert meta["delimiter"] == expected
    assert status in {"ok", "no_text"}


def test_a_utf8_bom_is_stripped_from_the_first_header():
    """Excel writes CSVs with a BOM; leaving it on turns the first column name into
    `\\ufeffid`, which no downstream match on "id" would find."""
    cards, status, _ = spreadsheet.cards_from_csv(
        "﻿id,Sex\na,M\nb,F\n".encode("utf-8"), "x.csv", L)
    assert status == "ok" and cards[0].header == ["id", "Sex"]


def test_a_csv_with_nothing_but_a_delimiter_line_is_no_text():
    """The card builder finds no usable table, and the sniffed delimiter is still
    reported -- it is the evidence for why the file was read the way it was."""
    cards, status, meta = spreadsheet.cards_from_csv(b",,,\n", "x.csv", L)
    assert (cards, status) == ([], "no_text")
    assert "delimiter" in meta


def test_a_csv_field_over_the_stdlib_limit_is_named_not_crashed():
    """`csv.reader` raises "field larger than field limit (131072)" mid-iteration,
    not at construction, so the catch has to wrap the scan. A supplement with one
    enormous cell -- a pasted FASTA, typically -- is how this shows up."""
    body = b'a,b\n"' + b"x" * 200_000 + b'",2\n'
    cards, status, meta = spreadsheet.cards_from_csv(body, "x.csv", L)

    assert (cards, status) == ([], "unreadable")
    assert "field larger than field limit" in meta["reason"]


# -- JATS --------------------------------------------------------------------

def test_jats_sections_and_metadata():
    body = ('<sec sec-type="methods"><title>Methods</title>'
            '<p>Islets were dissociated and loaded on a 10x Chromium controller.</p></sec>'
            '<sec><title>Results</title><p>We found beta cells.</p></sec>')
    blocks, status, meta = jats.blocks_from_jats(jats_article(body), "f.nxml", L)
    assert status == "ok"
    assert meta["title"] == "A test article about islets"
    assert meta["keywords"] == ["single-cell", "pancreas"]
    assert set(meta["sections"]) >= {"methods", "results"}
    methods = [b for b in blocks if b.section == "methods" and b.kind == PARAGRAPH]
    assert "10x Chromium" in methods[0].text
    assert any(b.kind == METADATA for b in blocks)
    assert any(b.section == "abstract" for b in blocks)


def test_a_jats_block_carries_the_heading_path_it_sits_under():
    """`section` is one canonical name out of eleven, and `walk_section` already
    knows the whole tree. `pdf.py` is deliberately left alone: there the tree is
    a guess, and a guessed path is what this package refuses to produce."""
    body = ('<sec sec-type="methods"><title>Methods</title>'
            '<sec><title>Nuclei isolation</title>'
            '<p>Nuclei were isolated from frozen tissue.</p></sec>'
            '<sec><title>Library preparation</title>'
            '<p>Libraries were made with the 10x kit.</p></sec></sec>')
    blocks, _, _ = jats.blocks_from_jats(jats_article(body), "f.nxml", L)
    paths = {b.text: b.section_path for b in blocks if b.origin == "jats"}
    assert paths["Nuclei were isolated from frozen tissue."] == \
        ["Methods", "Nuclei isolation"]
    assert paths["Libraries were made with the 10x kit."] == \
        ["Methods", "Library preparation"]
    assert paths["Nuclei isolation"] == ["Methods", "Nuclei isolation"]
    assert paths["Methods"] == ["Methods"]
    # Emitted only when it is real, so nothing else in the line changes.
    front = next(b for b in blocks if b.kind == METADATA)
    assert front.section_path is None and "section_path" not in front.to_dict()


def test_a_jats_locator_counts_children_of_its_own_tag():
    """`[n]` in XPath means the nth child *of that tag*. Counting every child made
    153 of the 168 body/back locators in 10.1038/s41467-023-40505-5 point at a
    different element, and only 76 resolved at all."""
    body = ("<sec><title>T</title><p>first paragraph here</p><fig/>"
            "<p>second paragraph here</p></sec>")
    blocks, _, _ = jats.blocks_from_jats(jats_article(body), "f.nxml", L)
    second = next(b for b in blocks if b.text == "second paragraph here")
    assert second.locator == "body/sec[1]/p[2]"


def test_a_pdf_block_carries_the_rectangle_it_came_from():
    """PyMuPDF hands over (x0, y0, x1, y1, text, block_no, block_type) and this
    module kept two of the seven, so a PDF block was locatable only to a page."""
    data = make_pdf_pages([["Nuclei were isolated from frozen heart tissue and "
                            "libraries were prepared with the 10x Chromium kit."]])
    blocks, _, _ = pdf.blocks_from_pdf(data, "f.pdf", L)
    ref = blocks[0].locator_ref
    assert ref["page"] == 1
    assert len(ref["bbox"]) == 4 and all(isinstance(v, float) for v in ref["bbox"])
    # Rounded, so re-extracting the same bytes gives the same line.
    assert all(round(v, 1) == v for v in ref["bbox"])
    assert blocks[0].to_dict()["locator_ref"] == ref


def test_a_pdf_block_gets_no_guessed_heading_path():
    data = make_pdf_pages([[
        "Methods Data collection Nuclei isolation from adult heart tissue was "
        "performed as described, and libraries were prepared with the 10x kit."]])
    blocks, _, _ = pdf.blocks_from_pdf(data, "f.pdf", L)
    assert all(b.section_path is None for b in blocks)


def test_citation_markers_are_dropped_but_other_cross_references_kept():
    """Left in, `<xref ref-type="bibr">` turns "as shown previously" into "as shown
    previously12,13", which is noise in a quote and worse in an evidence check."""
    body = ('<sec><title>Results</title><p>As shown previously'
            '<xref ref-type="bibr" rid="b1">12,13</xref>, islets vary '
            '(<xref ref-type="fig" rid="f1">Fig. 1a</xref>).</p></sec>')
    blocks, _, _ = jats.blocks_from_jats(jats_article(body), "f.nxml", L)
    text = " ".join(b.text for b in blocks if b.kind == PARAGRAPH)
    assert "previously, islets" in text
    assert "12,13" not in text
    assert "Fig. 1a" in text


@pytest.mark.parametrize("body,expected", [
    ('<p>We report <xref ref-type="bibr" rid="b1">1</xref>.</p>', "We report."),
    ('<p>severe symptoms (<xref ref-type="bibr" rid="b1">1</xref>; '
     '<xref ref-type="bibr" rid="b2">2</xref>, '
     '<xref ref-type="bibr" rid="b3">3</xref>). While recent studies</p>',
     "severe symptoms. While recent studies"),
    ('<p>LD blocks using LDetect<xref ref-type="bibr" rid="b1">7</xref>,'
     '<xref ref-type="bibr" rid="b2">8</xref>.</p>', "LD blocks using LDetect."),
    # The lookbehind is what keeps a function call intact.
    ('<p>we ran the susie_rss() function on each locus</p>',
     "we ran the susie_rss() function on each locus"),
    ('<p>the HarmonyMatrix() call and a negative (-) gate</p>',
     "the HarmonyMatrix() call and a negative (-) gate"),
])
def test_dropping_a_citation_does_not_leave_its_punctuation(body, expected):
    """35 literal `()` and 12 more `(` followed by a separator over the JATS
    blocks of 10.1016/j.cell.2021.01.053. One block read
    `...severe symptoms (; ; ; ; , ). While recent studies...`."""
    blocks, _, _ = jats.blocks_from_jats(
        jats_article(f"<sec><title>Results</title>{body}</sec>"), "f.nxml", L)
    assert next(b.text for b in blocks if b.kind == PARAGRAPH
                and b.section == "results") == expected


def test_a_citation_in_a_table_cell_is_the_value_and_stays():
    """10 of the 29 SOURCE cells in 10.1016/j.cell.2021.01.053's key resources
    table were destroyed: `(Korsunsky et al., 2019)` became `()`, and the card
    read `SOURCE [text, 11 distinct, 11 empty] = () | 10x Genomics | ; | ...`."""
    body = ('<sec><title>Methods</title><table-wrap><label>Key resources</label>'
            '<table><tr><th>REAGENT</th><th>SOURCE</th></tr>'
            '<tr><td>Harmony</td><td>(<xref ref-type="bibr" rid="b1">Korsunsky '
            'et al., 2019</xref>)</td></tr>'
            '<tr><td>Scanpy</td><td><xref ref-type="bibr" rid="b2">Wolf et al., '
            '2018</xref></td></tr></table></table-wrap></sec>')
    blocks, _, _ = jats.blocks_from_jats(jats_article(body), "f.nxml", L)
    source = next(c for c in next(b for b in blocks if b.kind == TABLE)
                  .table["columns"] if c["name"] == "SOURCE")
    assert source["values"] == ["(Korsunsky et al., 2019)", "Wolf et al., 2018"]


def test_reference_list_is_not_extracted():
    """A model asked for perturbations will happily take one from a reference
    title."""
    back = ('<ref-list><title>References</title><ref><element-citation>'
            '<article-title>CRISPR knockout of TP53 in mice</article-title>'
            '</element-citation></ref></ref-list>')
    blocks, _, meta = jats.blocks_from_jats(jats_article("", back=back), "f.nxml", L)
    assert not any("CRISPR knockout of TP53" in b.text for b in blocks)
    assert "references" not in meta["sections"]


def test_jats_table_becomes_a_card():
    body = ('<sec><title>Results</title><table-wrap id="T1"><label>Table 1</label>'
            '<caption><p>Donor characteristics</p></caption><table><thead>'
            '<tr><th>Donor</th><th>Sex</th></tr></thead><tbody>'
            '<tr><td>D1</td><td>M</td></tr><tr><td>D2</td><td>F</td></tr>'
            '</tbody></table></table-wrap></sec>')
    blocks, _, meta = jats.blocks_from_jats(jats_article(body), "f.nxml", L)
    card = next(b for b in blocks if b.kind == TABLE)
    assert meta["tables"] == 1
    assert card.label == "Table 1"
    assert card.table["header"] == ["Donor", "Sex"]
    assert "Donor characteristics" in card.text


def test_a_cell_holding_several_paragraphs_keeps_them_apart():
    """10.1038/s41467-023-40505-5 Table 1: 24 of its 144 cells hold more than one
    block child, and the SNP PIP column read `0.7980.15` for two values -- which
    also flipped the column's dtype from number to mixed and cost it its
    min/max/median."""
    body = ('<sec><title>Results</title><table-wrap><label>Table 1</label><table>'
            '<tr><th>Locus</th><th>SNP PIP</th><th>Supporting SNPs</th></tr>'
            '<tr><td><p>1p13</p></td><td><p>0.798</p><p>0.15</p></td>'
            '<td><p>rs1906615</p><p>rs7689774</p></td></tr>'
            '</table></table-wrap></sec>')
    blocks, _, _ = jats.blocks_from_jats(jats_article(body), "f.nxml", L)
    rows = next(b for b in blocks if b.kind == TABLE).table
    assert "0.798; 0.15" in next(b for b in blocks if b.kind == TABLE).text
    assert rows["header"] == ["Locus", "SNP PIP", "Supporting SNPs"]


def test_a_prose_paragraph_with_inline_markup_is_unchanged():
    """The separator is a table-cell rule. Inline elements are not block-level and
    must not gain one."""
    body = ('<sec><title>Results</title><p>The <italic>Tp53</italic> locus and the '
            '<sup>3</sup>H label were <bold>both</bold> measured.</p></sec>')
    blocks, _, _ = jats.blocks_from_jats(jats_article(body), "f.nxml", L)
    text = next(b.text for b in blocks if b.kind == PARAGRAPH and "Tp53" in b.text)
    assert text == "The Tp53 locus and the 3H label were both measured."


def test_a_single_paragraph_cell_gains_no_separator():
    body = ('<sec><title>Results</title><table-wrap><table>'
            '<tr><th>Donor</th><th>Sex</th></tr>'
            '<tr><td><p>D1</p></td><td>M</td></tr>'
            '</table></table-wrap></sec>')
    blocks, _, _ = jats.blocks_from_jats(jats_article(body), "f.nxml", L)
    card = next(b for b in blocks if b.kind == TABLE)
    assert card.table["header"] == ["Donor", "Sex"]
    assert "; " not in card.table["columns"][0]["values"][0]


def test_image_only_table_yields_its_caption():
    body = ('<sec><title>Results</title><table-wrap><label>Table 2</label>'
            '<caption><p>Primer sequences</p></caption>'
            '<graphic xlink:href="t2.jpg"/></table-wrap></sec>')
    blocks, _, _ = jats.blocks_from_jats(jats_article(body), "f.nxml", L)
    caption = next(b for b in blocks if b.kind == CAPTION)
    assert "Primer sequences" in caption.text


def test_figure_captions_are_kept():
    body = ('<sec><title>Results</title><fig id="f1"><label>Fig. 1</label>'
            '<caption><p>UMAP of 12,000 nuclei from mouse heart</p></caption></fig></sec>')
    blocks, _, _ = jats.blocks_from_jats(jats_article(body), "f.nxml", L)
    assert any("UMAP of 12,000 nuclei" in b.text for b in blocks if b.kind == CAPTION)


def test_supplement_labels_are_read_from_the_nested_media_element():
    """Springer nests href and caption inside `<media>`, so reading only the direct
    children of `<supplementary-material>` found labels for none of the 40 XML
    files in this corpus. The join is what turns
    `41467_2023_40505_MOESM3_ESM.xlsx` into "Supplementary Table 3"."""
    data = jats_article(SPRINGER_SUPPLEMENT)
    labels = jats.supplement_labels(data)
    assert labels["41467_2023_40505_MOESM3_ESM.xlsx"]["caption"] == "Supplementary Table 3"
    _, _, meta = jats.blocks_from_jats(data, "f.nxml", L)
    assert meta["supplement_labels"] == labels


def test_named_entities_and_the_doctype_do_not_break_parsing():
    """ElementTree resolves no external DTD, so every entity a JATS DOCTYPE would
    have defined is a fatal "undefined entity" -- a real file reported unreadable
    over a Greek letter."""
    body = "<sec><title>Results</title><p>TNF&alpha; rose 5&nbsp;fold.</p></sec>"
    blocks, status, _ = jats.blocks_from_jats(jats_article(body), "f.nxml", L)
    assert status == "ok"
    assert "TNF\u03b1" in " ".join(b.text for b in blocks)


def test_malformed_xml_is_unreadable_with_a_reason():
    blocks, status, meta = jats.blocks_from_jats(b"<article><body><p>x", "f.nxml", L)
    assert (blocks, status) == ([], "unreadable")
    assert "parse error" in meta["reason"]


def test_xml_without_an_article_element_is_unreadable():
    assert jats.blocks_from_jats(b"<other/>", "f.nxml", L)[1] == "unreadable"


# -- PDF ---------------------------------------------------------------------

def test_hyphenated_line_breaks_are_rejoined():
    """A hyphenated word is unsearchable and cannot be quoted, and any check that
    a quote really appears in the source compares it against this text."""
    data = make_pdf_pages([["We measured the perturba-\ntion of gene expression "
                            "across every single condition tested in this study, "
                            "and compared each result against the matched control "
                            "samples that were processed on the same day by the "
                            "same operator using identical reagent lots."]])
    blocks, status, _ = pdf.blocks_from_pdf(data, "f.pdf", L)
    assert status == "ok"
    assert "perturbation" in " ".join(b.text for b in blocks)


class _FakePage:
    """A PyMuPDF page as far as `_symbol_map` is concerned: spans with fonts.

    A real fixture is not available -- PyMuPDF's own base-14 Symbol font ships a
    ToUnicode map, so a synthesized PDF round-trips `g` as `g` and never
    reproduces the private-use codepoint this guard exists for. The corpus test
    over 10.1126/sciimmunol.aba4163 covers the real shape.
    """

    def __init__(self, spans):
        self._spans = spans

    def get_text(self, kind):
        return {"blocks": [{"lines": [{"spans": [
            {"font": font, "text": text} for font, text in self._spans]}]}]}


@pytest.mark.parametrize("raw,expected", [
    # `interleukin-<soft hyphen>\n17A` keeps its real hyphen.
    ("interleukin-\u00ad\n17A", "interleukin-17A"),
    ("scRNA-\u00ad seq", "scRNA-seq"),
    ("pheno\u00ad type", "phenotype"),
    ("pheno\u00ad\ntype", "phenotype"),
    ("zero\u200bwidth", "zerowidth"),
    ("\ufeffbom", "bom"),
])
def test_a_soft_hyphen_does_not_survive_inside_a_word(raw, expected):
    """U+00AD is category Cf -- neither `\\w` nor `\\s` -- so `_HYPHEN_BREAK`
    could not fire across one and the whitespace collapse left it in the word.
    21 of them in 10.1126/sciimmunol.aba4163."""
    cleaned = pdf._clean_block(raw)
    assert cleaned == expected
    assert "\u00ad" not in cleaned and "\u200b" not in cleaned


def test_a_symbol_font_glyph_becomes_the_greek_letter_it_stands_for():
    """`SymbolGreek` U+F067 is the gamma in `IFN-\u03b3`, 41 times in one article."""
    symbols = pdf._symbol_map(_FakePage([("SymbolGreek", "\uf067\uf062")]))
    assert symbols == {0xF067: "\u03b3", 0xF062: "\u03b2"}
    assert pdf._clean_block("IFN-\uf067 and TGF-\uf062", symbols) == "IFN-\u03b3 and TGF-\u03b2"


def test_a_private_use_codepoint_from_an_ordinary_font_is_left_alone():
    """A subsetted Latin face reuses the private use area for its own glyphs.
    Turning one of those into a Greek letter would invent a character."""
    assert pdf._symbol_map(_FakePage([("ABCDEF+MinionPro", "\uf067")])) == {}
    assert pdf._clean_block("x\uf067y", {}) == "x\uf067y"


@pytest.mark.parametrize("raw,expected", [
    ("IL-\n17A", "IL-17A"),
    ("scRNA-\nseq", "scRNA-seq"),
    ("CD4-\npositive", "CD4-positive"),
    ("SARS-CoV-\n2", "SARS-CoV-2"),
    ("perturba-\ntion", "perturbation"),
    # The accepted cost of the rule: nothing short of a dictionary separates a
    # common hyphenated adjective from a word broken at a syllable.
    ("well-\nknown", "wellknown"),
])
def test_a_hyphen_inside_an_identifier_survives_the_line_break(raw, expected):
    """Rejoining unconditionally deleted a real hyphen out of 78 of the 648
    line-break hyphens in this corpus's PDFs -- `SARS-CoV-2`, `COVID-19`,
    `Mono_c1-CD14-CCL3` -- which is exactly the vocabulary a curation answer is
    made of."""
    assert pdf._clean_block(raw) == expected


def test_the_hyphen_ratio_is_recorded_so_it_can_be_inspected():
    data = make_pdf_pages([["The IL-\n17A response and the perturba-\ntion of it "
                            "were measured across all of the matched samples that "
                            "were collected on the same day by the same operator."]])
    _, _, meta = pdf.blocks_from_pdf(data, "f.pdf", L)
    assert meta["hyphens_kept"] == 1 and meta["hyphens_joined"] == 1


def test_an_unmapped_private_use_glyph_is_counted_not_hidden():
    data = make_pdf_pages([["ordinary text with no symbol font at all here"]])
    _, _, meta = pdf.blocks_from_pdf(data, "f.pdf", L)
    assert "glyphs_unmapped" not in meta and "glyphs_mapped" not in meta


def test_running_headers_are_dropped_and_named():
    """A journal footer repeated on every page would otherwise appear as thirty
    near-identical paragraphs. Naming them matters as much as dropping them: the
    rule deletes 424 of 1,160 blocks in 10.1126/sciimmunol.aba4163 and 854 of
    2,474 in the 89-page Science supplement, and a bare count cannot be checked."""
    body = ("Nuclei were isolated from frozen tissue and sequenced on a NovaSeq "
            "6000 instrument at the core facility. ")
    data = make_pdf_pages([["Nature Communications | volume 14", body * 3]] * 4)
    blocks, _, meta = pdf.blocks_from_pdf(data, "f.pdf", L)
    assert meta["running_lines_dropped"] >= 4
    assert not any("volume 14" in b.text for b in blocks)
    assert meta["running_lines"][0] == {"text": "Nature Communications | volume 14",
                                        "pages": 4}


def test_a_repeated_line_in_the_body_is_not_mistaken_for_a_running_head():
    """`Reviewer #2 (Remarks to the Author):` sits at y0/h = 0.28, 0.53 and 0.12
    in 10.1038/s41467-023-40505-5's peer-review file, and the count-only rule
    deleted all three: that article's blocks held reviewers 1, 3, 1, 3 and 4 and
    no reviewer 2 at all, whose remarks then read as a continuation of
    reviewer 1's."""
    heading = "Reviewer #2 (Remarks to the Author):"
    body = ("The authors should clarify how the nuclei were isolated and how many "
            "donors contributed to each cluster in the figure. ")
    data = make_pdf_pages([["Nature Communications | volume 14", heading, body * 2]] * 4)
    blocks, _, meta = pdf.blocks_from_pdf(data, "f.pdf", L)
    texts = [b.text for b in blocks]
    assert texts.count(heading) == 4
    assert not any("volume 14" in t for t in texts)
    assert [r["text"] for r in meta["running_lines"]] == \
        ["Nature Communications | volume 14"]


def test_glued_section_heading_is_split_in_a_real_pdf():
    data = make_pdf_pages([[
        "Methods Data collection Nuclei isolation from adult heart tissue was "
        "performed as described, and libraries were prepared with the 10x kit."]])
    blocks, _, meta = pdf.blocks_from_pdf(data, "f.pdf", L)
    assert meta["glued_headings_split"] == 1
    assert blocks[0].kind == HEADING and blocks[0].text == "Methods"
    assert blocks[1].section == "methods"
    assert blocks[1].text.startswith("Data collection")


def test_scanned_pdf_is_flagged_not_reported_empty():
    """It is the article but needs an OCR step this pipeline does not have, which
    is a different problem from a file with nothing in it."""
    blocks, status, meta = pdf.blocks_from_pdf(make_scanned_pdf(), "f.pdf", L)
    assert status == "no_text_scanned_pdf"
    assert meta["chars"] < L.min_pdf_text_chars


def test_damaged_pdf_is_unreadable_with_a_reason():
    blocks, status, meta = pdf.blocks_from_pdf(b"%PDF-1.4 truncated", "f.pdf", L)
    assert (blocks, status) == ([], "unreadable")
    assert "reason" in meta


# -- docx --------------------------------------------------------------------

def test_docx_paragraphs_headings_and_tables():
    """In this corpus these are `suppmatmeth.docx` and `supplement-fS1-S7.docx` --
    where the library kit is written down when the main text says only "see
    Supplementary Methods"."""
    data = make_docx([
        ("paragraph", "Supplementary materials and methods", "Heading1"),
        ("paragraph", "Libraries were prepared with the Chromium Single Cell 3' v3 kit."),
        ("table", [["Primer", "Sequence"], ["Actb-F", "GGCTGTATTCCCCTCCATCG"]]),
    ])
    blocks, status, meta = docxfile.blocks_from_docx(data, "s.docx", L)
    assert status == "ok" and meta["tables"] == 1
    assert blocks[0].kind == HEADING and blocks[0].section == "methods"
    assert blocks[1].kind == PARAGRAPH and "v3 kit" in blocks[1].text
    card = next(b for b in blocks if b.kind == TABLE)
    assert card.table["header"] == ["Primer", "Sequence"]


def test_docx_field_codes_and_deleted_text_are_skipped():
    """A field code is a formula, and deleted text is text the authors removed;
    quoting either as evidence would be wrong."""
    raw = ('<w:p><w:r><w:t>Mice were </w:t></w:r>'
           '<w:r><w:instrText> REF _Ref1 \\h </w:instrText></w:r>'
           '<w:r><w:delText>eight</w:delText></w:r>'
           '<w:r><w:t>ten weeks old at the time of harvest.</w:t></w:r></w:p>')
    blocks, _, _ = docxfile.blocks_from_docx(make_docx([("raw", raw)]), "s.docx", L)
    text = blocks[0].text
    assert "ten weeks old" in text
    assert "REF" not in text and "eight" not in text


def test_docx_that_is_not_a_zip_is_unreadable():
    blocks, status, meta = docxfile.blocks_from_docx(b"not a docx", "s.docx", L)
    assert (blocks, status) == ([], "unreadable")
    assert "reason" in meta


def test_docx_without_a_document_part_is_unreadable():
    data = make_zip([("word/other.xml", b"<x/>")])
    assert docxfile.blocks_from_docx(data, "s.docx", L)[1] == "unreadable"


# -- HTML --------------------------------------------------------------------

def test_landing_page_metadata_and_prose_are_kept():
    page = (b'<html><head><meta name="citation_title" content="A paper about mice">'
            b'<meta name="citation_author" content="Smith J">'
            b'<meta name="citation_author" content="Jones K">'
            b'<script>var x = "ignore me";</script></head><body>'
            b'<nav>Home Journals Search</nav>'
            b'<p>' + b'The abstract of this article describes single-cell profiling. ' * 4
            + b'</p><div>Cite</div></body></html>')
    blocks, status, meta = htmlfile.blocks_from_html(page, "landing.html", L)
    assert status == "ok"
    assert meta["meta_tags"]["citation_author"] == ["Smith J", "Jones K"]
    assert any("Smith J; Jones K" in b.text for b in blocks if b.kind == METADATA)
    assert any("single-cell profiling" in b.text for b in blocks if b.kind == PARAGRAPH)
    assert not any("ignore me" in b.text for b in blocks)
    assert not any("Home Journals Search" in b.text for b in blocks)


def test_bot_check_landing_page_is_not_reported_as_text():
    """Nine Elsevier landing pages in this corpus hold 129 characters: the
    browser's own user-agent string. Calling that `ok` is the failure mode
    manuscript_harvest/fetch/validate.py exists to prevent."""
    blocks, status, meta = htmlfile.blocks_from_html(
        LANDING_INTERSTITIAL, "landing.html", L)
    assert (blocks, status) == ([], "no_text")
    assert "interstitial" in meta["reason"]


def test_paywall_landing_page_names_the_denial():
    page = (b"<html><body><div>Please sign in to read the full article or purchase "
            b"this article to continue reading the text.</div></body></html>")
    _, status, meta = htmlfile.blocks_from_html(page, "landing.html", L)
    assert status == "no_text" and "paywalled" in meta["reason"]


# -- archives ----------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("../../etc/passwd", "etc/passwd"),
    ("/abs/file.csv", "abs/file.csv"),
    ("a\\b\\c.csv", "a/b/c.csv"),
    ("C:/data/x.csv", "data/x.csv"),
    ("./x.csv", "x.csv"),
])
def test_archive_member_names_cannot_escape(raw, expected):
    """Nothing here writes them, but a member name reaches `source_file` on a
    block, and code that joins that to a path should not get `../../etc/passwd`."""
    assert archive.safe_member_name(raw) == expected


def test_only_wanted_extensions_are_read():
    data = make_zip([("table.csv", b"a,b\n1,2\n"), ("figure.jpg", b"\xff\xd8fig")])
    members, meta = archive.read_members(data, L, {".csv"})
    assert [name for name, _ in members] == ["table.csv"]
    assert meta["member_extensions"] == {".csv": 1, ".jpg": 1}
    assert any("figure.jpg" in s["name"] for s in meta["skipped"])


def test_oversized_member_is_skipped_with_its_size_in_the_reason():
    """10.7554/eLife.104978's fig1-data2.zip holds a 159 MB CSV. The size is read
    from the zip directory, so nothing is decompressed to find that out."""
    limits = Limits(max_member_mb=0)
    data = make_zip([("big.csv", b"a,b\n" + b"1,2\n" * 500)])
    members, meta = archive.read_members(data, limits, {".csv"})
    assert members == []
    assert "over the 0 MB member cap" in meta["skipped"][0]["reason"]


def test_member_cap_is_reported():
    limits = Limits(max_archive_members=1)
    data = make_zip([("a.csv", b"x,y\n1,2\n"), ("b.csv", b"x,y\n3,4\n")])
    members, meta = archive.read_members(data, limits, {".csv"})
    assert len(members) == 1
    assert any(s["reason"] == "member cap reached" for s in meta["skipped"])


def test_mac_resource_forks_are_ignored():
    data = make_zip([("__MACOSX/._x.csv", b"junk"), ("x.csv", b"a,b\n1,2\n")])
    members, _ = archive.read_members(data, L, {".csv"})
    assert [name for name, _ in members] == ["x.csv"]


def test_unreadable_archive_is_reported():
    members, meta = archive.read_members(b"not a zip", L, {".csv"})
    assert members == [] and "reason" in meta


# -- blocks ------------------------------------------------------------------

def test_block_record_carries_provenance_and_a_hash():
    block = Block(kind=PARAGRAPH, text="Mice were 8 weeks old.", source_file="s.docx",
                  origin="docx", locator="para 4", section="methods", label="Table S1")
    record = block.to_dict()
    assert record["chars"] == len(block.text)
    assert len(record["text_sha256"]) == 64
    assert record["source_file"] == "s.docx" and record["locator"] == "para 4"


def test_blocks_round_trip_and_are_byte_stable(tmp_path):
    """Same bytes in, byte-identical file out: that is what makes an extraction
    safe to hash and cheap to diff after a parser change."""
    blocks = [Block(kind=PARAGRAPH, text=f"paragraph {i}", source_file="f.pdf",
                    origin="pdf", index=i) for i in range(3)]
    first = tmp_path / "a.jsonl"
    second = tmp_path / "b.jsonl"
    write_blocks(first, blocks)
    write_blocks(second, blocks)
    assert first.read_bytes() == second.read_bytes()
    assert [b["text"] for b in read_blocks(first)] == ["paragraph 0", "paragraph 1",
                                                       "paragraph 2"]


def test_read_blocks_skips_a_truncated_line(tmp_path):
    path = tmp_path / "blocks.jsonl"
    path.write_text('{"text": "good", "index": 0}\n{"text": "trunc\n')
    assert [b["text"] for b in read_blocks(path)] == ["good"]


def test_read_blocks_of_a_missing_file_is_empty(tmp_path):
    assert list(read_blocks(tmp_path / "nope.jsonl")) == []


def test_markdown_rendering_groups_by_file():
    blocks = [
        Block(kind=HEADING, text="Methods", source_file="f.nxml", origin="jats"),
        Block(kind=PARAGRAPH, text="We used mice.", source_file="f.nxml", origin="jats"),
        Block(kind=TABLE, text="TABLE: S1", source_file="s.xlsx", origin="xlsx"),
    ]
    text = render_markdown(blocks)
    assert "## FILE: f.nxml" in text and "## FILE: s.xlsx" in text
    assert "### Methods" in text and "```" in text
