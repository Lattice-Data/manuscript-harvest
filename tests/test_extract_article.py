"""Article-level extraction: source choice, per-file statuses, and the manifest.

What these defend is the extraction record. A curation answer of "no perturbation
found" is only meaningful if the record can say the text was actually there, so
every assertion below is really an assertion that the stage does not report
emptiness it cannot account for: a figure image has no text, a scanned PDF needs
OCR, and a bot-check landing page is not an article.
"""

import json

import pytest

from manuscript_harvest.extract import extractor
from manuscript_harvest.extract.blocks import BLOCKS_NAME, TABLE, read_blocks
from manuscript_harvest.extract.cli import DEFAULT_EXTRACT_CONFIG, load_config, main
from manuscript_harvest.extract.extractor import EXTRACT_DIR, extract_article, sniff_extension
from manuscript_harvest.extract.limits import Limits
from manuscript_harvest.fetch import store
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


def test_a_thin_main_text_is_not_complete_and_says_there_was_no_fallback(tmp_path):
    """`main_usable` was `status == ok and chars > 0`, so an article with only a
    thin JATS body and no PDF came out `complete` with `main_text.chars: 185` --
    and carried the note "fell back to the PDF", about a PDF that does not
    exist, because that note was set before anything checked. The four complete
    articles in this corpus carry 89,151 / 88,262 / 43,746 / 94,014 characters,
    so the gate flips none of them."""
    directory = _article(tmp_path, xml=jats_article(
        "<sec><title>Results</title><p>Short.</p></sec>"))
    record = extract_article(directory, limits=L)
    assert record["main_text"]["source"] == "jats"
    assert record["main_text"]["thin"] is True
    assert record["main_text"]["chars"] < L.min_main_text_chars
    assert record["status"] == "partial"
    assert "no PDF to fall back to" in record["main_text"]["note"]
    assert "fell back to the PDF" not in record["main_text"]["note"]


def test_a_substantial_main_text_is_not_marked_thin(tmp_path):
    directory = _article(tmp_path, xml=jats_article(METHODS_BODY))
    record = extract_article(directory, limits=L)
    assert record["main_text"]["thin"] is False
    assert record["status"] == "complete"


def test_a_fetch_verdict_of_lost_supplements_blocks_complete(tmp_path):
    """`extract_article` copied `record["status"]` into `fetch_status` and never
    read `record["supplementary_status"]`. An article whose manifest says
    `expected_but_missing` -- with a problem line saying a tier listed
    supplementary material and no tier retrieved it -- extracted as
    `status: complete, supplementary: [], suppl[-]`."""
    directory = _article(tmp_path, xml=jats_article(METHODS_BODY))
    record = store.read_manifest(directory)
    record["supplementary_status"] = "expected_but_missing"
    store.write_manifest(directory, record)

    result = extract_article(directory, limits=L, force=True)
    assert result["fetch_supplementary_status"] == "expected_but_missing"
    assert extractor.SUPPLEMENTS_MISSING in result["caveats"]
    assert result["status"] == "partial"
    assert "caveats[supplements_expected_but_missing]" in extractor.summarize(result)


def test_an_unverified_supplement_set_is_a_caveat_not_a_defect(tmp_path):
    """`fetched_unverified` is 2 of the 6 articles in this corpus. It is worth
    saying and it is not a reason to withhold `complete`."""
    directory = _article(tmp_path, xml=jats_article(METHODS_BODY))
    record = store.read_manifest(directory)
    record["supplementary_status"] = "fetched_unverified"
    store.write_manifest(directory, record)

    result = extract_article(directory, limits=L, force=True)
    assert result["caveats"] == [extractor.SUPPLEMENTS_UNVERIFIED]
    assert result["status"] == "complete"


def test_every_caveat_is_in_the_closed_vocabulary(tmp_path):
    directory = _article(tmp_path, landing=LANDING_INTERSTITIAL)
    for name in extract_article(directory, limits=L)["caveats"]:
        assert name in extractor.CAVEATS


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


def test_a_shared_manifest_label_is_rejected_and_a_unique_one_survives(tmp_path):
    """`label: "Download"` was on 1,989 of the 2,076 blocks of
    10.1126/science.aat5031 and `label: "Europe PMC supplementary archive"` on
    347 of 536 in 10.1038/s41467-023-40505-5, straight from the manifest. Both
    are the fetch transport's name for the request, and a label used by two
    entries of the same article cannot be a per-file name."""
    directory = _article(
        tmp_path, xml=jats_article(METHODS_BODY),
        supplements=[("a.xlsx", make_xlsx({"S": [["a"], [1]]})),
                     ("b.xlsx", make_xlsx({"S": [["b"], [2]]})),
                     ("c.xlsx", make_xlsx({"S": [["c"], [3]]}))])
    manifest = store.read_manifest(directory)
    manifest["supplementary"][0]["label"] = "Download"
    manifest["supplementary"][1]["label"] = "Download"
    manifest["supplementary"][2]["label"] = "Table S1. Primer sequences"
    store.write_manifest(directory, manifest)

    record = extract_article(directory, limits=L, force=True)
    assert record["supplement_label_rejected"] == ["Download"]
    by_path = {e["path"]: e for e in record["supplementary"]}
    entries = [by_path[e["path"]] for e in manifest["supplementary"]]
    assert [e["label_source"] for e in entries] == ["none", "none", "manifest"]
    assert [e.get("label") for e in entries] == [None, None, "Table S1. Primer sequences"]
    labels = {b.get("label") for b in read_blocks(directory / EXTRACT_DIR / BLOCKS_NAME)}
    assert "Download" not in labels


def test_a_jats_caption_becomes_the_label_and_reaches_the_blocks(tmp_path):
    """`extract_bytes` accepted a caption and passed it only to `FileResult`, so
    "Table S7. Cytokine analysis, related to Figure 6" reached extraction.json
    and none of that file's blocks. 12 of the 25 ok supplements here carry one."""
    springer = SPRINGER_SUPPLEMENT.replace(
        "Supplementary Table 3",
        "Table S7. Cytokine analysis, related to Figure 6")
    directory = _article(
        tmp_path, xml=jats_article(METHODS_BODY + springer),
        supplements=[("41467_2023_40505_MOESM3_ESM.xlsx",
                      make_xlsx({"cytokine_analysis": [["cell", "IL17"], ["c1", 3]]}))])
    record = extract_article(directory, limits=L)
    entry = record["supplementary"][0]
    assert entry["label_source"] == "jats_caption"
    assert entry["label"] == "Table S7"
    block = next(b for b in read_blocks(directory / EXTRACT_DIR / BLOCKS_NAME)
                 if b["kind"] == TABLE)
    # The label beats the sheet name, and the caption is in the card a model reads.
    assert block["label"] == "Table S7"
    assert block["caption"] == "Table S7. Cytokine analysis, related to Figure 6"
    assert "Caption: Table S7. Cytokine analysis" in block["text"]


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
    manuscript_harvest/fetch/validate.py uses, because a publisher that mislabels a paywall
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


# -- a parser that dies must cost one file, not the run ----------------------

#: 4,000 nested `<sec>` elements. The walker is recursive, so this defeats it
#: well before CPython's limit is anywhere near the article's real depth.
DEEP_XML = (b"<article><body>" + b"<sec>" * 4000
            + b"<p>Nuclei were isolated from frozen tissue.</p>"
            + b"</sec>" * 4000 + b"</body></article>")


def test_a_deeply_nested_xml_is_unreadable_not_a_crash(tmp_path):
    """Measured: this supplement raised RecursionError straight out of
    `extract_article`, leaving neither extraction.json nor blocks.jsonl on disk."""
    directory = _article(tmp_path, xml=jats_article(METHODS_BODY),
                         supplements=[("deep.xml", DEEP_XML)])
    record = extract_article(directory, limits=L)
    entry = record["supplementary"][0]
    assert entry["status"] == "unreadable"
    assert "RecursionError" in entry["note"]
    assert (directory / EXTRACT_DIR / BLOCKS_NAME).exists()


def test_a_supplement_that_raises_becomes_parser_error_not_a_crash(tmp_path, monkeypatch):
    """The backstop behind the per-parser guards: whatever a parser does, the
    article still gets a record that names the file it happened in."""
    def explode(*args, **kwargs):
        raise KeyError("no such glyph")

    monkeypatch.setattr(extractor.docxfile, "blocks_from_docx", explode)
    directory = _article(tmp_path, xml=jats_article(METHODS_BODY),
                         supplements=[("legends.docx",
                                       make_docx([("paragraph", "Figure S1.")]))])
    record = extract_article(directory, limits=L)
    entry = record["supplementary"][0]
    assert entry["status"] == extractor.PARSER_ERROR
    assert entry["note"] == "KeyError: 'no such glyph'"
    # Not benign: a file that crashed the parser is a file whose text is missing.
    assert entry["path"] in record["unextracted_text_files"]
    assert record["status"] == "partial"


def test_cmd_all_survives_one_crashing_article(tmp_path, capsys):
    """`cmd_all` calls the extractor in a bare loop; one bad article took the
    whole corpus run with it."""
    make_article(tmp_path / "a", xml=jats_article(METHODS_BODY))
    make_article(tmp_path / "b", xml=jats_article(METHODS_BODY), doi="10.9999/second")
    config = tmp_path / "config.yaml"
    config.write_text(f"extract:\n  corpus_dir: {tmp_path}\n")

    real = extractor.extract_article
    calls = {"n": 0}

    def flaky(directory, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return real(directory, **kwargs)

    extractor.extract_article = flaky
    try:
        assert main(["--config", str(config), "all"]) == 1
    finally:
        extractor.extract_article = real
    err = capsys.readouterr().err
    assert "crashed: RuntimeError: boom" in err
    assert "crashed=1" in err
    assert calls["n"] == 2, "the second article was never reached"


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


def test_an_unlabelled_body_is_reported_even_when_the_article_is_complete(tmp_path):
    """10.1126/science.aat5031 is `complete` with 52 of its 87 main-text blocks
    carrying no section, the whole Results and Discussion among them, while
    `totals.sections` lists `methods` because every methods block comes from a
    supplementary PDF. A filter for `section == methods` over that main text
    returns nothing and the record used to say nothing was wrong."""
    page = ("Tissue-resident immune cells are important for organ homeostasis. "
            "We profiled the mature and developing human kidney. ") * 20
    directory = _article(tmp_path, fulltext=make_pdf_pages([[page]]))
    record = extract_article(directory, limits=L)
    report = record["main_text"]["section_labelling"]
    assert record["status"] == "complete"
    assert report["method"] == "heuristic"
    assert report["confidence"] == "none"
    assert report["body_sections_missing"] == ["methods", "results"]
    assert "no body section label" in report["why"]


def test_declared_sections_are_not_scored_as_a_heuristic(tmp_path):
    directory = _article(tmp_path, xml=jats_article(METHODS_BODY))
    report = extract_article(directory, limits=L)["main_text"]["section_labelling"]
    assert report["method"] == "declared" and report["confidence"] == "declared"
    assert "methods" in report["body_sections_found"]


def test_cli_prints_the_section_labelling_warning(tmp_path, capsys):
    page = ("Tissue-resident immune cells are important for organ homeostasis. "
            "We profiled the mature and developing human kidney. ") * 20
    _article(tmp_path, fulltext=make_pdf_pages([[page]]))
    config = tmp_path / "config.yaml"
    config.write_text(f"extract:\n  corpus_dir: {tmp_path}\n")
    main(["--config", str(config), "one", DOI])
    assert "section labelling is none" in capsys.readouterr().err
    main(["--config", str(config), "status"])
    err = capsys.readouterr().err
    assert "sect=none" in err
    assert "main-text section labelling: none=1" in err


def test_the_record_carries_the_counts_a_review_queue_needs(tmp_path):
    """The strongest triage signal -- `header_confidence == "low"` -- lived only
    inside blocks.jsonl, so no queue could be computed from the record at all."""
    directory = _article(
        tmp_path, xml=jats_article(METHODS_BODY),
        supplements=[("s1.xlsx", make_xlsx({"S": [["gene", "symbol"],
                                                  ["TP53", "p53"]]})),
                     ("url", make_xlsx({"T": [["a", "b"], [1, 2]]}))])
    signals = extract_article(directory, limits=L)["review_signals"]
    assert signals["tables_total"] == 2
    assert signals["tables_header_low"] == 1, "all-text under all-text headers"
    assert signals["main_text_blocks"] > 0
    assert signals["jats_reference_available"] is True
    assert signals["supplements_sniffed"] == \
        [next(e["path"] for e in store.read_manifest(directory)["supplementary"]
              if e["path"].endswith("_url"))]


def test_the_record_names_the_running_lines_it_deleted(tmp_path):
    """`meta["running_lines_dropped"]` was set by the parser but was missing from
    the allow-list, so it never reached extraction.json and nothing outside one
    test could see that a third of a file's blocks had been deleted."""
    body = ("Nuclei were isolated from frozen tissue and sequenced on a NovaSeq "
            "6000 instrument at the core facility. ")
    directory = _article(tmp_path, fulltext=make_pdf_pages(
        [["SCIENCE IMMUNOLOGY | RESEARCH ARTICLE", body * 3]] * 4))
    record = extract_article(directory, limits=L)
    main = record["main_text"]
    assert main["running_lines_dropped"] >= 4
    assert main["running_lines"][0]["text"] == "SCIENCE IMMUNOLOGY | RESEARCH ARTICLE"
    assert main["running_lines"][0]["pages"] == 4


def test_a_truncated_blocks_file_is_re_extracted_not_trusted(tmp_path):
    """The cache used to test only that blocks.jsonl existed. Emptying a real
    475 KB one and re-running gave `cached: True, status: complete,
    totals.blocks: 532` over zero lines on disk."""
    directory = _article(tmp_path, xml=jats_article(METHODS_BODY))
    first = extract_article(directory, limits=L)
    assert first["blocks_lines"] == first["totals"]["blocks"]
    blocks_file = directory / EXTRACT_DIR / BLOCKS_NAME
    blocks_file.write_text("")

    again = extract_article(directory, limits=L)
    assert again.get("cached") is None
    assert again["problems"] == [f"{BLOCKS_NAME} did not match the hash in "
                                 f"{extractor.EXTRACTION_NAME}; re-extracted"]
    assert len(list(read_blocks(blocks_file))) == again["totals"]["blocks"] > 0
    # Once repaired, the article goes back to being cached rather than looping.
    assert extract_article(directory, limits=L).get("cached") is True


def test_a_record_without_a_blocks_hash_is_re_extracted_once(tmp_path):
    """Every extraction written before this check has no `blocks_sha256`. That
    counts as a mismatch, so the corpus repairs itself on the next run."""
    directory = _article(tmp_path, xml=jats_article(METHODS_BODY))
    extract_article(directory, limits=L)
    path = directory / EXTRACT_DIR / extractor.EXTRACTION_NAME
    stale = json.loads(path.read_text())
    stale.pop("blocks_sha256")
    path.write_text(json.dumps(stale))
    assert extract_article(directory, limits=L).get("cached") is None
    assert extract_article(directory, limits=L).get("cached") is True


def test_a_changed_limit_invalidates_the_cache(tmp_path):
    """`limits` was recorded in the record but was not part of the key, so
    editing `max_scan_rows` in config.yaml reused an extraction made under the
    old cap."""
    directory = _article(tmp_path, xml=jats_article(METHODS_BODY))
    extract_article(directory, limits=Limits(max_scan_rows=5000))
    assert extract_article(directory, limits=Limits(max_scan_rows=5000)).get("cached") is True
    assert extract_article(directory, limits=Limits(max_scan_rows=10)).get("cached") is None


def test_a_changed_parser_source_invalidates_the_cache(tmp_path, monkeypatch):
    """`sections.py` changed materially twice under the same `"0.1.0"`, and 21
    blocks of 10.1126/science.aat5031 got a different section out of it. Until
    this key moved with the source, `--force` was the only way to see a fix."""
    directory = _article(tmp_path, xml=jats_article(METHODS_BODY))
    extract_article(directory, limits=L)
    assert extract_article(directory, limits=L).get("cached") is True
    monkeypatch.setattr(extractor, "source_fingerprint", lambda: "0123456789abcdef")
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


def test_the_table_command_reprints_the_rows_the_card_describes(tmp_path, capsys):
    """`data_ref` is a contract, not a comment: the card says which file, which
    sheet and which rows, and this re-opens the source at that offset. Nothing in
    the repo could do that before, and `data_ref` was not sufficient to -- no
    scan window and no file hash."""
    _article(tmp_path, xml=jats_article(METHODS_BODY),
             supplements=[("s1.xlsx", make_xlsx({"Donors": [
                 ["donor", "age", "sex"], ["D1", 44, "F"], ["D2", 61, "M"]]}))])
    config = tmp_path / "config.yaml"
    config.write_text(f"extract:\n  corpus_dir: {tmp_path}\n")
    assert main(["--config", str(config), "one", DOI]) == 0
    capsys.readouterr()

    assert main(["--config", str(config), "table", DOI, "--file", "s1.xlsx"]) == 0
    out = capsys.readouterr().out
    assert "header (row 1): donor | age | sex" in out
    assert "2: D1 | 44 | F" in out
    assert "3: D2 | 61 | M" in out


def test_the_table_command_says_when_the_source_changed_under_the_card(tmp_path, capsys):
    directory = _article(
        tmp_path, xml=jats_article(METHODS_BODY),
        supplements=[("s1.xlsx", make_xlsx({"D": [["a", "b"], [1, 2]]}))])
    config = tmp_path / "config.yaml"
    config.write_text(f"extract:\n  corpus_dir: {tmp_path}\n")
    main(["--config", str(config), "one", DOI])
    record = store.read_manifest(directory)
    (directory / record["supplementary"][0]["path"]).write_bytes(
        make_xlsx({"D": [["a", "b"], [9, 9]]}))
    capsys.readouterr()

    main(["--config", str(config), "table", DOI, "--file", "s1.xlsx"])
    assert "the source file has changed" in capsys.readouterr().err


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
