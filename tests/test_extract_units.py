"""Unit coverage for the extraction stage: sections, table cards, and each parser.

Most of these pin a rule that a real file in the corpus proved wrong at least
once, and where that is so the docstring names the DOI. The recurring theme is
that a parser must not report emptiness it cannot justify: a strict-conformance
workbook, a header on row 4, and a bot-check landing page all look like "there
was nothing there" unless something checks.
"""

import sys

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


def test_headerless_matrix_is_reported_not_guessed():
    rows = [(1, 2, 3), (4, 5, 6), (7, 8, 9)]
    card = tables.build_card(rows, "x.xlsx", "s", L)
    assert card.header_row is None
    assert any("no header row" in note for note in card.notes)
    assert card.header == ["column_1", "column_2", "column_3"]


def test_header_confidence_is_low_without_a_type_change():
    """All-text rows under all-text headers could be a first data row of gene
    names; the card says so rather than pretending to know."""
    rows = [("gene", "symbol"), ("TP53", "p53"), ("MYC", "myc")]
    card = tables.build_card(rows, "x.xlsx", "s", L)
    assert card.header_confidence == "low"
    assert any("type-change" in note for note in card.notes)


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


def test_card_states_when_the_scan_was_capped():
    limits = Limits(max_scan_rows=10)
    rows = [("a", "b")] + [(i, i * 2) for i in range(9)]
    card = tables.build_card(rows, "x.xlsx", "s", limits, truncated=True,
                             n_rows_total=99999)
    text = tables.render(card, limits)
    assert "scan stopped at 10 rows" in " ".join(card.notes)
    assert "source reports 99999" in text


def test_card_does_not_copy_the_data_it_points_at_it():
    """Duplicating a 2.4 GB corpus to paraphrase it would be the wrong trade: the
    card records where to re-read the real values instead."""
    card = tables.build_card([("a",), (1,)], "x.xlsx", "sheet 'S1'", L,
                             data_ref={"file": "x.xlsx", "sheet": "S1"})
    assert card.data_ref == {"file": "x.xlsx", "sheet": "S1"}


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


def test_running_headers_are_dropped():
    """A journal footer repeated on every page would otherwise appear as thirty
    near-identical paragraphs."""
    body = ("Nuclei were isolated from frozen tissue and sequenced on a NovaSeq "
            "6000 instrument at the core facility. ")
    data = make_pdf_pages([["Nature Communications | volume 14", body * 3]] * 4)
    blocks, _, meta = pdf.blocks_from_pdf(data, "f.pdf", L)
    assert meta["running_lines_dropped"] >= 4
    assert not any("volume 14" in b.text for b in blocks)


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
