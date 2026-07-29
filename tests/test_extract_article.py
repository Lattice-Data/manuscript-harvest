"""Article-level extraction: source choice, per-file statuses, and the manifest.

What these defend is the extraction record. A curation answer of "no perturbation
found" is only meaningful if the record can say the text was actually there, so
every assertion below is really an assertion that the stage does not report
emptiness it cannot account for: a figure image has no text, a scanned PDF needs
OCR, and a bot-check landing page is not an article.
"""

import pytest

from curation.extract import extractor
from curation.extract.blocks import BLOCKS_NAME, TABLE, read_blocks
from curation.extract.cli import DEFAULT_EXTRACT_CONFIG, load_config, main
from curation.extract.extractor import EXTRACT_DIR, extract_article, sniff_extension
from curation.extract.limits import Limits
from curation.fetch import store
from tests.fakes import (
    DOI,
    LANDING_INTERSTITIAL,
    SPRINGER_SUPPLEMENT,
    jats_article,
    make_article,
    make_docx,
    make_pdf,
    make_pdf_pages,
    make_scanned_pdf,
    make_xlsx,
    make_zip,
)

L = Limits()

#: Long enough to clear `Limits.min_main_text_chars`, so the JATS-vs-PDF choice
#: under test is the preference rule and not the thin-XML fallback.
METHODS_BODY = (
    '<sec sec-type="methods"><title>Methods</title><p>'
    + "Islets from eight-week-old male C57BL/6 mice were dissociated and loaded "
      "on a 10x Chromium controller with the Single Cell 3' v3 kit. " * 25
    + '</p></sec>'
)


def _article(tmp_path, **kwargs):
    return make_article(tmp_path / store.doi_slug(DOI), **kwargs)


# -- choosing the main text --------------------------------------------------

def test_jats_preferred_and_the_pdf_is_left_unparsed(tmp_path):
    """63% of this corpus carries JATS next to the PDF: sections are declared
    rather than guessed, and tables are real tables. Extracting both would double
    every paragraph and leave a model to guess which copy to quote."""
    directory = _article(tmp_path, xml=jats_article(METHODS_BODY), fulltext=make_pdf())
    record = extract_article(directory, limits=L)
    assert record["main_text"]["source"] == "jats"
    assert record["main_text"]["pdf_available"] is True
    assert "not parsed" in record["main_text"]["note"]
    assert record["status"] == "complete"
    assert "methods" in record["totals"]["sections"]


def test_thin_jats_falls_back_to_the_pdf(tmp_path):
    """Some deposited XML carries only front matter."""
    body = "<sec><title>Results</title><p>Short.</p></sec>"
    long_page = ("Nuclei were isolated from frozen heart tissue and libraries were "
                 "prepared with the Chromium kit. ") * 30
    directory = _article(tmp_path, xml=jats_article(body),
                         fulltext=make_pdf_pages([[long_page]]))
    record = extract_article(directory, limits=L)
    assert record["main_text"]["source"] == "pdf"
    assert "fell back to the PDF" in record["main_text"]["note"]


def test_pdf_is_used_when_there_is_no_xml(tmp_path):
    directory = _article(tmp_path, fulltext=make_pdf())
    record = extract_article(directory, limits=L)
    assert record["main_text"]["source"] == "pdf"
    assert record["main_text"]["usable"] is True


def test_landing_page_only_is_never_complete(tmp_path):
    """The flag exists so that "no perturbations found" in such an article reads as
    "we never had the article" rather than as a finding."""
    page = (b'<html><head><meta name="citation_title" content="A paper">'
            b'</head><body><p>' + b"An abstract describing single-cell work. " * 40
            + b'</p></body></html>')
    directory = _article(tmp_path, landing=page)
    record = extract_article(directory, limits=L)
    assert record["main_text"]["source"] == "landing_html"
    assert record["main_text"]["landing_page_only"] is True
    assert record["status"] == "partial"
    assert "not the article" in record["main_text"]["note"]


def test_bot_check_landing_page_gives_a_failed_article(tmp_path):
    """Nine Elsevier articles in this corpus are in exactly this state."""
    directory = _article(tmp_path, landing=LANDING_INTERSTITIAL)
    record = extract_article(directory, limits=L)
    assert record["status"] == "failed"
    assert record["totals"]["blocks"] == 0


def test_no_main_text_at_all_is_failed_and_says_why(tmp_path):
    directory = _article(tmp_path)
    record = extract_article(directory, limits=L)
    assert record["status"] == "failed"
    assert "no PDF, no XML" in record["main_text"]["note"]


def test_scanned_main_pdf_keeps_the_richer_xml(tmp_path):
    directory = _article(tmp_path, xml=jats_article(METHODS_BODY),
                         fulltext=make_scanned_pdf())
    record = extract_article(directory, limits=L)
    assert record["main_text"]["source"] == "jats"


def test_missing_manifest_is_reported_not_raised(tmp_path):
    record = extract_article(tmp_path, limits=L)
    assert record["status"] == "no_manifest"


# -- supplements -------------------------------------------------------------

def test_supplement_kinds_get_the_statuses_they_deserve(tmp_path):
    """321 of this corpus's supplement files are figure images. That is a fact
    about the supplements, not a parser failure, and it must not read as one."""
    directory = _article(
        tmp_path, xml=jats_article(METHODS_BODY),
        supplements=[
            ("table.xlsx", make_xlsx({"S1": [["id", "Sex"], ["a", "M"], ["b", "F"]]})),
            ("figure1.jpg", b"\xff\xd8fake image bytes"),
            ("movie.mp4", b"\x00\x00\x00 ftypmp42"),
            ("counts.h5ad", b"\x89HDF\r\n\x1a\n"),
            ("legends.docx", make_docx([("paragraph", "Figure S1. UMAP of nuclei.")])),
            ("notes.rtf", b"{\\rtf1 text}"),
            ("scan.pdf", make_scanned_pdf()),
        ])
    record = extract_article(directory, limits=L)
    by_path = {s["path"].split("_", 1)[1]: s for s in record["supplementary"]}
    assert by_path["table.xlsx"]["status"] == "ok"
    assert by_path["table.xlsx"]["tables"] == 1
    assert by_path["figure1.jpg"]["status"] == "image_no_text"
    assert "vision pass" in by_path["figure1.jpg"]["note"]
    assert by_path["movie.mp4"]["status"] == "media_no_text"
    assert by_path["counts.h5ad"]["status"] == "data_file_skipped"
    assert by_path["legends.docx"]["status"] == "ok"
    assert by_path["notes.rtf"]["status"] == "unsupported_format"
    assert by_path["scan.pdf"]["status"] == "no_text_scanned_pdf"
    # Only files that should have yielded text count against the article.
    assert record["unextracted_text_files"] == [by_path["notes.rtf"]["path"],
                                                by_path["scan.pdf"]["path"]]
    assert record["status"] == "partial"


def test_a_figure_only_article_is_still_complete(tmp_path):
    """Images are expected to have no text, so they carry no blame."""
    directory = _article(tmp_path, xml=jats_article(METHODS_BODY),
                         supplements=[("f1.jpg", b"\xff\xd8x"), ("f2.gif", b"GIF89a")])
    record = extract_article(directory, limits=L)
    assert record["status"] == "complete"
    assert record["supplementary_by_status"] == {"image_no_text": 2}


def test_supplement_labels_are_joined_from_the_jats(tmp_path):
    """The manifest records `original_name`; JATS records the publisher's label for
    the same name. Joining them is the difference between a model seeing
    `MOESM3_ESM.xlsx` and seeing "Supplementary Table 3"."""
    directory = _article(
        tmp_path, xml=jats_article(METHODS_BODY + SPRINGER_SUPPLEMENT),
        supplements=[("41467_2023_40505_MOESM3_ESM.xlsx",
                      make_xlsx({"S3": [["cell", "organism"], ["c1", "Homo sapiens"]]}))])
    record = extract_article(directory, limits=L)
    entry = record["supplementary"][0]
    assert entry["caption"] == "Supplementary Table 3"
    labelled = [b for b in read_blocks(directory / EXTRACT_DIR / BLOCKS_NAME)
                if b["source_file"].endswith("MOESM3_ESM.xlsx")]
    assert labelled and all(b["label"] for b in labelled)


def test_a_missing_recorded_file_is_reported(tmp_path):
    directory = _article(tmp_path, xml=jats_article(METHODS_BODY),
                         supplements=[("gone.xlsx", make_xlsx({"S": [["a"], [1]]}))])
    record = store.read_manifest(directory)
    (directory / record["supplementary"][0]["path"]).unlink()
    result = extract_article(directory, limits=L, force=True)
    assert result["supplementary"][0]["status"] == "missing"


def test_an_oversized_supplement_is_recorded_not_read(tmp_path):
    limits = Limits(max_file_mb=0)
    directory = _article(tmp_path, xml=jats_article(METHODS_BODY),
                         supplements=[("big.xlsx", make_xlsx({"S": [["a"], [1]]}))])
    record = extract_article(directory, limits=limits)
    assert record["supplementary"][0]["status"] == "too_large"


# -- zips --------------------------------------------------------------------

def test_zip_members_are_read_and_marked_as_coming_from_the_archive(tmp_path):
    inner = make_xlsx({"S1": [["id", "treatment"], ["a", "DMSO"], ["b", "TGFb"]]})
    directory = _article(tmp_path, xml=jats_article(METHODS_BODY),
                         supplements=[("data.zip", make_zip([("tables/s1.xlsx", inner)]))])
    record = extract_article(directory, limits=L)
    entry = record["supplementary"][0]
    assert entry["status"] == "ok" and entry["tables"] == 1
    block = next(b for b in read_blocks(directory / EXTRACT_DIR / BLOCKS_NAME)
                 if b["kind"] == TABLE and "s1.xlsx" in b["source_file"])
    assert block["origin"] == "zip:xlsx"
    assert block["source_file"].endswith("data.zip!tables/s1.xlsx")


def test_nested_zip_is_followed_one_level(tmp_path):
    """Three of this corpus's zips contain only more zips, so a single level of
    nesting has to be followed or those supplements read as empty."""
    inner = make_zip([("s1.csv", b"id,sex\na,M\nb,F\n")])
    outer = make_zip([("inner.zip", inner)])
    directory = _article(tmp_path, xml=jats_article(METHODS_BODY),
                         supplements=[("data.zip", outer)])
    record = extract_article(directory, limits=L)
    assert record["supplementary"][0]["status"] == "ok"


def test_the_archive_depth_cap_is_stated_when_it_stops_the_descent(tmp_path):
    limits = Limits(max_archive_depth=1)
    outer = make_zip([("inner.zip", make_zip([("s1.csv", b"id,sex\na,M\n")]))])
    directory = _article(tmp_path, xml=jats_article(METHODS_BODY),
                         supplements=[("data.zip", outer)], )
    record = extract_article(directory, limits=limits)
    entry = record["supplementary"][0]
    assert entry["status"] == "no_text"
    assert "depth cap" in entry["note"]


def test_an_image_only_zip_is_not_blamed_for_having_no_text(tmp_path):
    data = make_zip([("f1.jpg", b"\xff\xd8x"), ("f2.tif", b"II*\x00")])
    directory = _article(tmp_path, xml=jats_article(METHODS_BODY),
                         supplements=[("figs.zip", data)])
    record = extract_article(directory, limits=L)
    entry = record["supplementary"][0]
    assert entry["status"] == "image_no_text"
    assert record["status"] == "complete"


# -- files whose names do not say what they are ------------------------------

@pytest.mark.parametrize("data,expected", [
    (b"%PDF-1.7 body", ".pdf"),
    (b"{\\rtf1 hello}", ".rtf"),
    (b"\x1f\x8b\x08\x00", ".gz"),
    (b"<?xml version='1.0'?><article/>", ".xml"),
    (b"<!DOCTYPE html><html></html>", ".html"),
    (b"id,sex\na,M\n", ".txt"),
    (b"\x00\x01\x02\x03\xff\xfe", ""),
])
def test_sniff_extension_from_magic_bytes(data, expected):
    assert sniff_extension(data) == expected


def test_sniff_looks_inside_an_ooxml_package():
    assert sniff_extension(make_xlsx({"S": [["a"]]})) == ".xlsx"
    assert sniff_extension(make_docx([("paragraph", "x")])) == ".docx"
    assert sniff_extension(make_zip([("a.csv", b"x")])) == ".zip"


def test_content_type_is_only_the_fallback():
    """Magic bytes decide, Content-Type is the fallback -- the same order
    curation/fetch/validate.py uses, because a publisher that mislabels a paywall
    page as application/pdf will mislabel a supplement too."""
    assert sniff_extension(b"%PDF-1.7 real pdf", "text/csv") == ".pdf"
    assert sniff_extension(b"\x00\xff\x00\xfe binary", "application/pdf") == ".pdf"
    assert sniff_extension(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",
                           "application/vnd.ms-excel") == ".xls"


def test_extensionless_supplement_is_recovered(tmp_path):
    """Thirteen supplements in this corpus were saved by the browser tier as
    `NN_url` with no extension at all, several of them real spreadsheets."""
    directory = _article(
        tmp_path, xml=jats_article(METHODS_BODY),
        supplements=[("url", make_xlsx({"S1": [["id", "Sex"], ["a", "F"]]}))])
    record = extract_article(directory, limits=L)
    entry = record["supplementary"][0]
    assert entry["status"] == "ok" and entry["origin"] == "xlsx"
    assert "read as .xlsx" in entry["note"]


def test_giant_delimited_text_is_read_as_a_table_not_one_paragraph(tmp_path):
    """10.1126/science.aax6234's TableS8.txt is a 23 MB TSV whose first line is a
    caption. Requiring every line to agree on the delimiter count sent it down the
    prose path as a single 23 MB "paragraph"."""
    body = b"Supplementary Table 8. DEGs over the trajectory.\ngene\tlogFC\tp\n"
    body += b"".join(f"G{i}\t{i / 10}\t0.01\n".encode() for i in range(200))
    directory = _article(tmp_path, xml=jats_article(METHODS_BODY),
                         supplements=[("TableS8.txt", body)])
    record = extract_article(directory, limits=L)
    entry = record["supplementary"][0]
    assert entry["status"] == "ok" and entry["tables"] == 1
    assert entry["chars"] < 10000


def test_prose_text_file_is_read_as_paragraphs(tmp_path):
    body = b"Supplementary note\n\nMice were housed at 22 degrees.\n\nAll procedures "
    body += b"were approved by the committee.\n"
    directory = _article(tmp_path, xml=jats_article(METHODS_BODY),
                         supplements=[("note.txt", body)])
    record = extract_article(directory, limits=L)
    assert record["supplementary"][0]["status"] == "ok"
    assert record["supplementary"][0]["tables"] == 0


def test_a_runaway_paragraph_is_truncated_and_the_note_says_so(tmp_path):
    limits = Limits(max_paragraph_chars=500)
    directory = _article(tmp_path, xml=jats_article(METHODS_BODY),
                         supplements=[("dump.txt", b"word " * 5000)])
    record = extract_article(directory, limits=limits)
    entry = record["supplementary"][0]
    assert entry["truncated_paragraphs"] == 1
    assert "probably data rather than prose" in entry["reason"]


# -- the record and the artifacts -------------------------------------------

def test_extraction_writes_blocks_markdown_and_a_record(tmp_path):
    directory = _article(tmp_path, xml=jats_article(METHODS_BODY),
                         supplements=[("s1.xlsx", make_xlsx({"S": [["a", "b"], [1, 2]]}))])
    record = extract_article(directory, limits=L)
    output = directory / EXTRACT_DIR
    assert (output / BLOCKS_NAME).exists()
    assert (output / "article.md").exists()
    assert (output / extractor.EXTRACTION_NAME).exists()
    assert record["blocks_path"] == f"{EXTRACT_DIR}/{BLOCKS_NAME}"
    assert record["totals"]["blocks"] == len(list(read_blocks(output / BLOCKS_NAME)))


def test_block_indices_are_contiguous_across_every_file(tmp_path):
    directory = _article(tmp_path, xml=jats_article(METHODS_BODY),
                         supplements=[("s1.xlsx", make_xlsx({"S": [["a"], [1]]})),
                                      ("s2.csv", b"x,y\n1,2\n")])
    extract_article(directory, limits=L)
    indices = [b["index"] for b in read_blocks(directory / EXTRACT_DIR / BLOCKS_NAME)]
    assert indices == list(range(len(indices)))


def test_re_extraction_is_byte_identical(tmp_path):
    """Same bytes in, same file out: that is what makes an extraction safe to hash
    and lets a parser change be reviewed as a diff."""
    directory = _article(tmp_path, xml=jats_article(METHODS_BODY),
                         supplements=[("s1.xlsx", make_xlsx({"S": [["a", "b"], [1, 2]]}))])
    extract_article(directory, limits=L)
    first = (directory / EXTRACT_DIR / BLOCKS_NAME).read_bytes()
    extract_article(directory, limits=L, force=True)
    assert (directory / EXTRACT_DIR / BLOCKS_NAME).read_bytes() == first


def test_unchanged_articles_are_not_re_extracted(tmp_path):
    directory = _article(tmp_path, xml=jats_article(METHODS_BODY))
    extract_article(directory, limits=L)
    assert extract_article(directory, limits=L).get("cached") is True
    assert extract_article(directory, limits=L, force=True).get("cached") is None


def test_a_changed_manifest_invalidates_the_cache(tmp_path):
    """A re-fetch rewrites the manifest, and stale extracted text would then
    describe files that are no longer there."""
    directory = _article(tmp_path, xml=jats_article(METHODS_BODY))
    extract_article(directory, limits=L)
    record = store.read_manifest(directory)
    record["fetched_at"] = "2026-08-01T00:00:00+00:00"
    store.write_manifest(directory, record)
    assert extract_article(directory, limits=L).get("cached") is None


def test_the_record_names_the_caps_it_ran_under(tmp_path):
    """A thin result has to be attributable: was the table empty, or was the scan
    capped at 5,000 rows?"""
    directory = _article(tmp_path, xml=jats_article(METHODS_BODY))
    record = extract_article(directory, limits=Limits(max_scan_rows=7))
    assert record["limits"]["max_scan_rows"] == 7


def test_summarize_is_one_line(tmp_path):
    directory = _article(tmp_path, xml=jats_article(METHODS_BODY))
    line = extractor.summarize(extract_article(directory, limits=L))
    assert "\n" not in line and "complete" in line


# -- CLI ---------------------------------------------------------------------

def test_config_defaults_are_filled(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("extract:\n  limits:\n    max_scan_rows: 11\n")
    config = load_config(path)
    assert config["extract"]["limits"]["max_scan_rows"] == 11
    assert config["extract"]["limits"]["max_unique_values"] == \
        DEFAULT_EXTRACT_CONFIG["limits"]["max_unique_values"]


def test_extract_follows_the_fetch_corpus_directory(tmp_path):
    """One corpus, two stages: moving it should need one edit, not two that drift."""
    path = tmp_path / "config.yaml"
    path.write_text("fetch:\n  corpus_dir: /data/mine\n")
    assert load_config(path)["extract"]["corpus_dir"] == "/data/mine"

    path.write_text("fetch:\n  corpus_dir: /data/mine\nextract:\n  corpus_dir: /other\n")
    assert load_config(path)["extract"]["corpus_dir"] == "/other"


def test_config_survives_a_missing_file(tmp_path):
    assert load_config(tmp_path / "nope.yaml")["extract"]["corpus_dir"] == "corpus"


def test_limits_from_dict_ignores_unknown_keys():
    limits = Limits.from_dict({"max_scan_rows": 3, "not_a_cap": 9})
    assert limits.max_scan_rows == 3


def test_cli_one_and_show_run_offline(tmp_path, capsys):
    _article(tmp_path, xml=jats_article(METHODS_BODY),
             supplements=[("s1.xlsx", make_xlsx({"S": [["id", "Sex"], ["a", "M"]]}))])
    config = tmp_path / "config.yaml"
    config.write_text(f"extract:\n  corpus_dir: {tmp_path}\n")

    assert main(["--config", str(config), "one", DOI]) == 0
    capsys.readouterr()

    assert main(["--config", str(config), "show", DOI, "--kind", "table"]) == 0
    out = capsys.readouterr().out
    assert "TABLE" in out and "Sex" in out

    assert main(["--config", str(config), "status", "--quiet"]) == 0
    assert "1/1 articles extracted" in capsys.readouterr().err


def test_cli_reports_an_unknown_article(tmp_path, capsys):
    config = tmp_path / "config.yaml"
    config.write_text(f"extract:\n  corpus_dir: {tmp_path}\n")
    assert main(["--config", str(config), "one", "10.9999/nope"]) == 2
    assert "no article directory" in capsys.readouterr().err


def test_cli_accepts_a_path_as_well_as_a_doi(tmp_path):
    directory = _article(tmp_path, xml=jats_article(METHODS_BODY))
    config = tmp_path / "config.yaml"
    config.write_text(f"extract:\n  corpus_dir: {tmp_path}\n")
    assert main(["--config", str(config), "one", str(directory)]) == 0


def test_cli_all_reports_failures_in_its_exit_code(tmp_path, capsys):
    _article(tmp_path, landing=LANDING_INTERSTITIAL)
    config = tmp_path / "config.yaml"
    config.write_text(f"extract:\n  corpus_dir: {tmp_path}\n")
    assert main(["--config", str(config), "all"]) == 1
    assert "failed=1" in capsys.readouterr().err
