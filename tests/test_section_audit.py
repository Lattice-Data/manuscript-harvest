"""Offline tests for the section audit.

The audit is a measuring instrument, so these tests are mostly about it not
flattering the thing it measures: alignment must not match on boilerplate, text
the reference has no answer for must not count as an error, and the failure it
exists to catch -- a heading claiming a whole paper -- must show up as bad
precision rather than as a shrug.
"""

import pytest

from manuscript_harvest.extract import sections, section_audit
from manuscript_harvest.extract.blocks import PARAGRAPH, TABLE, Block

LOREM = ("we isolated nuclei from frozen tissue and profiled them with the chromium "
         "single cell three prime reagent kit version three")
RESULT = ("after filtering we retained two hundred and ninety five thousand nuclei for "
          "analysis of their transcriptomes and chromatin accessibility profiles")


def _block(text, section, kind=PARAGRAPH, origin="jats"):
    return Block(kind=kind, text=text, source_file="f", origin=origin, section=section)


# -- alignment ---------------------------------------------------------------

def test_words_ignore_punctuation_and_hyphenation():
    """The PDF path de-hyphenates and the XML never had the break, so both have to
    shingle the same way or nothing aligns."""
    assert section_audit.words("perturba-tion, of Genes!") == ["perturba", "tion", "of", "genes"]
    assert section_audit.words("perturbation of genes") == ["perturbation", "of", "genes"]


def test_text_shorter_than_the_window_yields_no_shingles():
    """A panel letter or an axis label is not evidence about anything, and PDF
    layout produces hundreds of them."""
    assert section_audit.shingles("Chromatin Accessibility") == []
    assert len(section_audit.shingles("a b c d e f g h i")) == 2


def test_a_shingle_seen_under_two_sections_is_dropped():
    """Boilerplate appears in both Methods and Data availability. A coin flip in the
    reference answer is worse than a smaller reference answer."""
    index = section_audit.reference_index([
        _block(LOREM, sections.METHODS),
        _block(LOREM, sections.DATA_AVAILABILITY),
    ])
    assert index == {}


def test_the_reference_answer_is_a_majority_of_matching_shingles():
    index = section_audit.reference_index([
        _block(LOREM, sections.METHODS), _block(RESULT, sections.RESULTS)])
    assert section_audit.reference_for(LOREM, index) == sections.METHODS
    assert section_audit.reference_for(RESULT, index) == sections.RESULTS
    assert section_audit.reference_for("entirely unrelated words here now", index) is None


def test_only_paragraphs_are_compared():
    """A table card is a profile of a table, not prose, and has no counterpart in
    the other rendition."""
    index = section_audit.reference_index([_block(LOREM, sections.METHODS, kind=TABLE)])
    assert index == {}


# -- scoring -----------------------------------------------------------------

def test_agreement_is_reported_as_agreement():
    jats = [_block(LOREM, sections.METHODS), _block(RESULT, sections.RESULTS)]
    pdf = [_block(LOREM, sections.METHODS, origin="pdf"),
           _block(RESULT, sections.RESULTS, origin="pdf")]
    report = section_audit.audit(jats, pdf)
    assert report["aligned"] == 2 and report["correct"] == 2
    assert report["accuracy"] == 1.0
    assert report["sections"][sections.METHODS]["precision"] == 1.0
    assert report["confusions"] == []


def test_a_heading_claiming_the_whole_paper_shows_up_as_bad_precision():
    """The failure this instrument was built for. 10.1126/science.adt8307 put 996 of
    1,184 blocks under `conclusions` because Science's front-page summary carries a
    standalone CONCLUSION line. Scored against a rendition that declares its
    sections, that has to read as precision 0, not as a shrug."""
    jats = [_block(LOREM, sections.METHODS), _block(RESULT, sections.RESULTS)]
    swallowed = [_block(LOREM, sections.CONCLUSIONS, origin="pdf"),
                 _block(RESULT, sections.CONCLUSIONS, origin="pdf")]
    report = section_audit.audit(jats, swallowed)
    assert report["aligned"] == 2 and report["correct"] == 0
    assert report["accuracy"] == 0.0
    assert report["sections"][sections.CONCLUSIONS]["precision"] == 0.0
    assert report["sections"][sections.METHODS]["recall"] == 0.0
    assert {(c["jats_says"], c["pdf_says"]) for c in report["confusions"]} == {
        (sections.METHODS, sections.CONCLUSIONS), (sections.RESULTS, sections.CONCLUSIONS)}


def test_an_unlabelled_pdf_paragraph_costs_recall_but_not_precision():
    """Leaving `section` null is the safe failure and has to score as the safe
    failure: it loses recall on the section that was missed and blames no other."""
    jats = [_block(LOREM, sections.METHODS)]
    pdf = [_block(LOREM, None, origin="pdf")]
    report = section_audit.audit(jats, pdf)
    assert report["sections"][sections.METHODS]["recall"] == 0.0
    assert report["sections"][sections.METHODS]["labelled"] == 0
    assert report["confusions"][0]["pdf_says"] == "(none)"


def test_text_the_reference_has_no_answer_for_is_not_an_error():
    """`jats.walk_section` drops reference lists as low-value while the PDF path
    extracts them, so every reference paragraph is unalignable by construction.
    Counting those as mistakes would measure the difference between two extractors
    rather than the accuracy of one labeller."""
    jats = [_block(LOREM, sections.METHODS)]
    pdf = [_block(LOREM, sections.METHODS, origin="pdf"),
           _block("smith j and jones k nature genetics volume fifty seven pages one to ten",
                  sections.REFERENCES, origin="pdf")]
    report = section_audit.audit(jats, pdf)
    assert report["aligned"] == 1 and report["accuracy"] == 1.0
    assert report["unaligned"] == 1 and report["unaligned_chars"] > 0
    assert sections.REFERENCES not in report["sections"]


def test_paragraphs_too_short_to_align_are_counted_apart():
    jats = [_block(LOREM, sections.METHODS)]
    pdf = [_block("Chromatin Accessibility", None, origin="pdf")]
    report = section_audit.audit(jats, pdf)
    assert report["too_short_to_align"] == 1
    assert report["aligned"] == 0 and report["accuracy"] is None


# -- the article and the command line ---------------------------------------

def test_an_article_without_both_renditions_is_skipped_not_failed(tmp_path):
    (tmp_path / "fulltext.pdf").write_bytes(b"%PDF-1.4 not really")
    assert section_audit.audit_article(tmp_path) is None


def test_a_rendition_that_does_not_parse_is_named(tmp_path):
    (tmp_path / "fulltext.pdf").write_bytes(b"not a pdf at all")
    (tmp_path / "fulltext.nxml").write_bytes(b"<not-an-article/>")
    report = section_audit.audit_article(tmp_path)
    assert report["skipped"]
    assert "unreadable" in (report["jats_status"], report["pdf_status"])
    assert "skipped" in section_audit.format_report(report)


def test_the_cli_says_so_when_no_article_has_a_pair(tmp_path, capsys):
    (tmp_path / "10.1_x").mkdir()
    assert section_audit.main(["--corpus-dir", str(tmp_path)]) == 2
    assert "open-access" in capsys.readouterr().err


def test_the_cli_writes_json_when_asked(tmp_path, monkeypatch):
    import json
    monkeypatch.setattr(section_audit, "audit_article",
                        lambda d, limits=None: {"slug": "x", "aligned": 1, "correct": 1,
                                                "accuracy": 1.0, "unaligned": 0,
                                                "unaligned_chars": 0,
                                                "too_short_to_align": 0, "sections": {},
                                                "confusions": [], "jats_paragraphs": 1,
                                                "pdf_paragraphs": 1})
    (tmp_path / "10.1_x").mkdir()
    out = tmp_path / "report.json"
    assert section_audit.main(["--corpus-dir", str(tmp_path), "--json", str(out)]) == 0
    assert json.loads(out.read_text())[0]["slug"] == "x"


@pytest.mark.parametrize("size", [4, 8, 12])
def test_the_window_size_is_configurable_end_to_end(size):
    jats = [_block(LOREM, sections.METHODS)]
    pdf = [_block(LOREM, sections.METHODS, origin="pdf")]
    report = section_audit.audit(jats, pdf, size=size)
    assert report["correct"] == 1
