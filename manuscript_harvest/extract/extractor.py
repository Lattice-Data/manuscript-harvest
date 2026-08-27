"""One article in, one `extracted/` directory out.

    corpus/<doi_slug>/extracted/
        blocks.jsonl      every block, in document order, with provenance
        article.md        the same content rendered for a human to read
        extraction.json   what each file yielded, and what it did not

The manifest is the point of this module as much as the blocks are. Every file in
the article is accounted for with a status, so a thin extraction is legible:
`image_no_text` on 321 figure files across the corpus is a fact about the
supplements, not a parser failure, and `no_text_scanned_pdf` is a file that needs
OCR rather than a file with nothing in it. The failure mode being designed
against is the same one `manuscript_harvest/fetch/validate.py` guards: a plausible empty
result that looks like a clean run.

Main text is chosen, not merged: JATS if it is there and substantial, else the
PDF, else the saved landing page. Extracting both the XML and the PDF would
double every paragraph and leave a model to guess which copy to quote.
"""

import hashlib
import io
import json
import sys
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..fetch import store
from ..fetch.validate import IDENTITY_FAILURES
from ..text_bearing import AUDIO_VIDEO_EXTENSIONS, IMAGE_EXTENSIONS
from . import __version__, archive, docxfile, htmlfile, jats, pdf, spreadsheet
from . import review, source_fingerprint
from .blocks import (
    MAIN_TEXT,
    NON_EVIDENCE,
    SUPPLEMENT,
    TABLE,
    Block,
    blocks_sha256,
    number_blocks,
    render_markdown,
    strip_invisible,
    write_blocks,
)
from .blocks import BLOCKS_NAME
from .limits import Limits
from .tables import render as render_card

EXTRACT_DIR = "extracted"
EXTRACTION_NAME = "extraction.json"
ARTICLE_MD = "article.md"

# -- per-file statuses -------------------------------------------------------
OK = "ok"
OK_VIA_OCR = "ok_via_ocr"
"""Text this stage produced by OCR rather than by reading a text layer.

Its own word rather than `ok` on purpose, and `pdf._ocr_pass` carries the argument.
The short version: OCR'd characters are a guess where a text layer is a fact, so
the perturbation stage should be able to weight a claim resting on them
differently, and `no_text_scanned_pdf` has to keep meaning "a scan nobody has
read" rather than becoming "a scan, or one we read, you cannot tell from here"."""
NO_TEXT = "no_text"
SCANNED = "no_text_scanned_pdf"
IMAGE_NO_TEXT = "image_no_text"
MEDIA_NO_TEXT = "media_no_text"
DATA_SKIPPED = "data_file_skipped"
UNSUPPORTED = "unsupported_format"
TOO_LARGE = "too_large"
MISSING = "missing"
UNREADABLE = "unreadable"
GARBLED = "garbled_text_encoding"
"""A file that parses, draws correctly, and whose text cannot be read: its fonts
never say what their glyphs mean, so what comes out is not the characters the
document contains. `pdf._repair_glyph_encoding` carries the whole account. In
neither `_PRODUCTIVE` nor `_BENIGN` for the same reason `parser_error` is in
neither -- the text was there and this stage did not get it."""
PARSER_ERROR = "parser_error"
"""A parser raised. Distinct from `unreadable`, which is a parser declining a
file it recognised as broken: this is the stage itself failing, and it is in
neither `_PRODUCTIVE` nor `_BENIGN` because the text was probably there."""

#: Statuses that mean "there was text here and we got it".
#:
#: `ok_via_ocr` belongs here, which is the one consequence of adding it that is
#: worth stating outright: without it those 70 files would stay in
#: `unextracted_text_files` and their articles would stay `partial` after the text
#: had in fact been read, which is the opposite of what this stage is for. The
#: distinction OCR earns is in the word, not in being treated as a failure.
_PRODUCTIVE = {OK, OK_VIA_OCR}
#: Statuses that are expected and carry no blame -- a figure has no text.
_BENIGN = {IMAGE_NO_TEXT, MEDIA_NO_TEXT, DATA_SKIPPED}

# -- caveats -----------------------------------------------------------------
# A closed vocabulary, like the statuses above, for things that are true about an
# extraction without being a per-file failure. Three of them come from the *fetch*
# stage's own verdict, which this module recorded and never read: an article whose
# manifest says `supplementary_status: expected_but_missing` and
# `problems: ["...listed supplementary material; no tier retrieved it"]` extracted
# as `status: complete, supplementary: [], suppl[-]`.
SUPPLEMENTS_MISSING = "supplements_expected_but_missing"
SUPPLEMENTS_UNVERIFIED = "supplement_set_unverified"
MAIN_TEXT_THIN = "main_text_thin"
LANDING_PAGE_ONLY = "landing_page_only"
MANIFEST_ENTRY_WITHOUT_PATH = "manifest_entry_without_a_path"
MAIN_TEXT_NOT_THE_ARTICLE = "main_text_is_not_the_requested_article"

CAVEATS = {
    SUPPLEMENTS_MISSING:
        "the fetch stage says supplementary material was listed and not retrieved",
    SUPPLEMENTS_UNVERIFIED:
        "supplements were fetched but no tier could confirm the set is complete",
    MAIN_TEXT_THIN:
        "the main text is shorter than min_main_text_chars: front matter, not an article",
    LANDING_PAGE_ONLY:
        "the main text is a saved publisher landing page, not the article",
    MANIFEST_ENTRY_WITHOUT_PATH:
        "a supplementary entry in the manifest has no file on disk to read",
    MAIN_TEXT_NOT_THE_ARTICLE:
        "the fetch stage says the stored full text is not the requested article",
}

#: Fetch verdicts that mean files were lost, not merely uncounted. `none_retrieved`
#: belongs here: the README defines it as "a tier tried and every file it went
#: after was lost".
#:
#: `none_text_bearing` deliberately belongs to neither this set nor the
#: `fetched_unverified` branch below. Nothing was lost -- the fetch stage read the
#: deposit, named every file and refused each one because no text can be extracted
#: from it -- and nothing is unbounded either, so an empty `supplementary: []` under
#: that status is the complete and correct extraction of this article. Adding it here
#: would raise `supplements_expected_but_missing`, which blocks `complete`, over
#: articles whose supplements are four figure JPEGs.
_FETCH_SUPPLEMENTS_LOST = {"expected_but_missing", "partial_failure", "none_retrieved"}

#: Caveats that stop an article being `complete`. `SUPPLEMENTS_UNVERIFIED` is
#: deliberately absent: it is common (2 of the 6 articles here) and is a caveat,
#: not a defect.
_BLOCKING_CAVEATS = {SUPPLEMENTS_MISSING, MAIN_TEXT_THIN, LANDING_PAGE_ONLY,
                     MAIN_TEXT_NOT_THE_ARTICLE}

SPREADSHEET_EXTENSIONS = {".xlsx", ".xlsm"}
LEGACY_SPREADSHEET_EXTENSIONS = {".xls"}
DELIMITED_EXTENSIONS = {".csv", ".tsv"}
PLAIN_TEXT_EXTENSIONS = {".txt", ".md"}
XML_EXTENSIONS = {".xml", ".nxml"}
HTML_EXTENSIONS = {".html", ".htm"}
# `IMAGE_EXTENSIONS` and `AUDIO_VIDEO_EXTENSIONS` are imported rather than listed
# here, and the second is renamed on the way: those two sets are now the fetch
# stage's refusal policy as well as this module's dispatch, so a second copy would
# mean a file the fetcher declined to download and this module called readable.
# `manuscript_harvest/text_bearing.py` carries the measurement and the argument. The
# rename is because "media" was already two things -- audio and video here, the
# article's own figures in `fetch/store.py`'s `media/` -- and that module is where
# the two meet.
DATA_EXTENSIONS = {".h5", ".h5ad", ".hdf5", ".loom", ".mtx", ".rds", ".rdata", ".npz",
                   ".npy", ".mat", ".sav", ".dta", ".bam", ".bai", ".cram", ".fastq",
                   ".fq", ".fa", ".fasta", ".bed", ".vcf", ".gtf", ".gff", ".bw",
                   ".bigwig", ".pkl", ".parquet", ".sqlite", ".db", ".zarr"}
#: Compressed containers, split by what the *name* claims is inside. Only ever a
#: first guess: `_extract_compressed` decides from the bytes, because both claims
#: are wrong somewhere in this corpus -- 10.1038/s41586-020-03182-8's `.tgz` is an
#: uncompressed tar and 10.1126/science.adf5357's three `.gz` files are one CSV
#: each rather than an archive of many.
TAR_EXTENSIONS = {".tar", ".tgz", ".tbz2", ".txz"}
STREAM_COMPRESSED_EXTENSIONS = {".gz", ".bz2", ".xz"}
#: No standard-library reader, so still refused, and the note names the format.
#: Zero files in this corpus, against six for the two sets above; a third-party
#: dependency for a format nobody has sent is the trade the `xls` extra lost.
OPAQUE_ARCHIVE_EXTENSIONS = {".7z", ".rar"}
COMPRESSED_EXTENSIONS = (TAR_EXTENSIONS | STREAM_COMPRESSED_EXTENSIONS
                         | OPAQUE_ARCHIVE_EXTENSIONS)
LEGACY_DOC_EXTENSIONS = {".doc", ".rtf", ".odt", ".ods", ".ppt", ".pptx", ".key", ".pages"}
"""Refused, and deliberately, which is the decision worth recording rather than the
capability.

Measured over the 393-article corpus: three `.rtf` totalling 1.7 MB and one 23.3 MB
`.doc`. Four files, and reading them means an external converter for each format --
`unrtf` for one, `antiword` or `catdoc` for the other -- so two system dependencies
that nothing else in this package needs and that CI would have to install.

That is the opposite trade to the two the neighbouring paths just made. `xlrd` went
from optional to required because the count moved to 56 files and 129 MB, and the tar
and gzip readers cost nothing but standard library for six files and 107 MB. Four
files behind two system dependencies is not that, and `unsupported_format` on a
`.rtf` is already a queueable failure: `review.py` puts it in front of a human, who
can open it in any word processor and say whether it holds evidence. Revisit if the
count moves the way the .xls count did."""

#: What is worth pulling out of a zip.
TEXT_BEARING_EXTENSIONS = (
    SPREADSHEET_EXTENSIONS | LEGACY_SPREADSHEET_EXTENSIONS | DELIMITED_EXTENSIONS
    | PLAIN_TEXT_EXTENSIONS | XML_EXTENSIONS | HTML_EXTENSIONS | {".pdf", ".docx"}
)

#: Every extension the dispatcher recognises, for deciding when to sniff instead.
KNOWN_EXTENSIONS = (
    TEXT_BEARING_EXTENSIONS | IMAGE_EXTENSIONS | AUDIO_VIDEO_EXTENSIONS
    | DATA_EXTENSIONS | COMPRESSED_EXTENSIONS | LEGACY_DOC_EXTENSIONS | {".zip"}
)

#: Content-Type is only consulted after the magic bytes have nothing to say.
_CONTENT_TYPES = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-excel": ".xls",
    "application/msword": ".doc",
    "application/zip": ".zip",
    "application/gzip": ".gz",
    "application/x-gzip": ".gz",
    "application/x-tar": ".tar",
    "application/x-bzip2": ".bz2",
    "text/csv": ".csv",
    "text/tab-separated-values": ".tsv",
    "text/plain": ".txt",
    "text/xml": ".xml",
    "application/xml": ".xml",
    "text/html": ".html",
    "application/rtf": ".rtf",
    "text/rtf": ".rtf",
}


def sniff_extension(data: bytes, content_type: str = "") -> str:
    """Guess what a file is when its name does not say. `""` when unknown.

    Thirteen supplements in this corpus were saved by the browser tier as
    `NN_url` with no extension at all, several of them real PDFs and
    spreadsheets. They are recoverable, but only by looking inside.

    Magic bytes decide, and Content-Type is the fallback -- the same order
    `manuscript_harvest/fetch/validate.py` uses, and for the same reason: a publisher that
    serves a paywall page as `application/pdf` will also mislabel a supplement.
    """
    head = data[:8]
    if head.startswith(b"%PDF"):
        return ".pdf"
    if head.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as package:
                names = set(package.namelist())
        except (zipfile.BadZipFile, OSError):
            return ".zip"
        if "xl/workbook.xml" in names:
            return ".xlsx"
        if "word/document.xml" in names:
            return ".docx"
        if any(name.startswith("ppt/") for name in names):
            return ".pptx"
        return ".zip"
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        # OLE2 container: legacy Excel and Word are indistinguishable this cheaply.
        return ".xls" if "excel" in content_type.lower() else ".doc"
    if head.startswith(b"{\\rt"):
        return ".rtf"
    if head.startswith(b"\x1f\x8b"):
        return ".gz"
    if head.startswith(b"BZh"):
        return ".bz2"
    if head.startswith(b"\xfd7zXZ\x00"):
        return ".xz"
    # A tar says so 257 bytes in, not at the front. Answered `""` before, which for
    # one of the 13 extensionless supplements would have meant no parser at all.
    if data[257:262] == b"ustar":
        return ".tar"

    mapped = _CONTENT_TYPES.get((content_type or "").split(";")[0].strip().lower())
    if mapped:
        return mapped

    prefix = data[:1024].lstrip()
    if prefix.startswith(b"<?xml") or prefix.startswith(b"<article"):
        return ".xml"
    if prefix[:15].lower().startswith((b"<!doctype html", b"<html")):
        return ".html"
    try:
        text = data[:4096].decode("utf-8")
    except UnicodeDecodeError:
        return ""
    printable = sum(1 for c in text if c.isprintable() or c in "\r\n\t")
    return ".txt" if text and printable / len(text) > 0.9 else ""


class FileResult:
    """What one file yielded."""

    def __init__(self, path: str, role: str, status: str, blocks: Optional[List[Block]] = None,
                 origin: str = "", meta: Optional[dict] = None, label: Optional[str] = None,
                 caption: Optional[str] = None, note: Optional[str] = None):
        self.path = path
        self.role = role
        self.status = status
        self.blocks = blocks or []
        self.origin = origin
        self.meta = meta or {}
        self.label = label
        self.caption = caption
        self.note = note

    @property
    def chars(self) -> int:
        return sum(len(b.text) for b in self.blocks)

    @property
    def n_tables(self) -> int:
        return sum(1 for b in self.blocks if b.kind == TABLE)

    def to_dict(self) -> dict:
        record = {
            "path": self.path,
            "role": self.role,
            "status": self.status,
            "origin": self.origin,
            "blocks": len(self.blocks),
            "chars": self.chars,
            "tables": self.n_tables,
        }
        if self.label:
            record["label"] = self.label
        if self.caption:
            record["caption"] = self.caption
        if self.note:
            record["note"] = self.note
        for key in ("reason", "sheets", "sheets_skipped", "strict_ooxml", "pages",
                    "members_total", "members_read", "member_extensions", "errors",
                    "compression", "decompressed_bytes", "walk_stopped", "ocr",
                    "truncated_stream", "trailing_bytes",
                    "blocks_capped", "delimiter", "read_as", "text_runs",
                    "glued_headings_split", "truncated_paragraphs",
                    "sections", "sections_abandoned",
                    "low_value_blocks_withheld",
                    "glyphs_mapped", "glyphs_unmapped",
                    "glyphs_unnamed", "glyphs_drawn",
                    "glyph_encoding_repaired", "garbled_sample",
                    "hyphens_kept", "hyphens_joined",
                    "running_lines_dropped", "running_lines",
                    "tables_skipped", "tables_capped", "reopens_refused",
                    "label_source", "reference_list_dropped", "sniffed_as",
                    "sections_from_review"):
            if key in self.meta:
                record[key] = self.meta[key]
        return record


def _plain_text_blocks(data: bytes, source_file: str, limits: Limits, role: str,
                       overrides=None) -> Tuple[List[Block], str, dict]:
    """A `.txt` supplement: tabular if it looks tabular, paragraphs otherwise."""
    text = data.decode("utf-8", errors="replace")
    if not text.strip():
        return [], NO_TEXT, {}

    # Tabular is judged by the most common delimiter count, not by every line
    # agreeing: these files routinely carry a caption line with no delimiters
    # above the header, and requiring uniformity sent a 23 MB TSV down the prose
    # path as a single paragraph.
    lines = [line for line in text.splitlines() if line.strip()][:50]
    for delimiter in ("\t", ",", ";"):
        counts = Counter(line.count(delimiter) for line in lines)
        common, frequency = counts.most_common(1)[0]
        if common >= 1 and len(lines) >= 2 and frequency >= 0.7 * len(lines):
            cards, status, meta = spreadsheet.cards_from_csv(
                data, source_file, limits, overrides)
            if status == OK:
                meta["read_as"] = f"delimited text ({delimiter!r})"
                blocks = [Block(kind=TABLE, text=render_card(card, limits),
                                source_file=source_file, origin="txt",
                                locator=card.locator, role=role, table=card.to_dict())
                          for card in cards]
                return blocks, OK, meta

    from .blocks import PARAGRAPH
    meta: dict = {}
    blocks: List[Block] = []
    for index, chunk in enumerate(text.split("\n\n"), start=1):
        # Stripped before the split, not after: U+200B is not whitespace, so
        # collapsing first would leave the two spaces around it behind.
        paragraph = " ".join(strip_invisible(chunk).split())
        if len(paragraph) < limits.min_paragraph_chars:
            continue
        if len(paragraph) > limits.max_paragraph_chars:
            paragraph = paragraph[: limits.max_paragraph_chars]
            meta["truncated_paragraphs"] = meta.get("truncated_paragraphs", 0) + 1
        blocks.append(Block(kind=PARAGRAPH, text=paragraph, source_file=source_file,
                            origin="txt", locator=f"para {index}", role=role))
        if len(blocks) >= limits.max_blocks_per_file:
            break
    if meta.get("truncated_paragraphs"):
        meta["reason"] = (f"{meta['truncated_paragraphs']} run(s) of text longer than "
                          f"{limits.max_paragraph_chars} chars were truncated; this file "
                          f"is probably data rather than prose")
    if not blocks:
        return [], NO_TEXT, meta
    return blocks, OK, meta


def _table_blocks(cards, source_file: str, origin: str, role: str, limits: Limits,
                  label: Optional[str] = None,
                  caption: Optional[str] = None) -> List[Block]:
    """Cards as blocks. A supplied label wins over the sheet name.

    `label=card.title` meant the sheet name, so a JATS-joined publisher label for
    the file lost to `cytokine_analysis` even when one had been found.
    """
    return [Block(kind=TABLE, text=render_card(card, limits, caption=caption),
                  source_file=source_file, origin=origin, locator=card.locator,
                  role=role, label=label or card.title, caption=caption,
                  table=card.to_dict())
            for card in cards]


def extract_bytes(
    data: bytes,
    relative_path: str,
    limits: Limits,
    role: str = SUPPLEMENT,
    label: Optional[str] = None,
    caption: Optional[str] = None,
    origin_prefix: str = "",
    content_type: str = "",
    depth: int = 0,
    overrides=None,
) -> FileResult:
    """Route one file's bytes to the right parser by extension.

    `origin_prefix` marks provenance for archive members, so a block read out of
    a zip says `zip:inner/table.xlsx` rather than pretending to be a top-level
    supplement. `depth` counts how many archives deep this file is.
    """
    extension = Path(relative_path).suffix.lower()
    sniffed = ""
    if extension not in KNOWN_EXTENSIONS:
        sniffed = sniff_extension(data, content_type)
        if sniffed:
            extension = sniffed

    def result(status: str, blocks=None, origin: str = "", meta=None, note=None) -> FileResult:
        for block in blocks or []:
            block.role = role
            if label and not block.label:
                block.label = label
            if caption and not block.caption:
                block.caption = caption
            # A block read out of an archive must say so, or its provenance reads
            # as a top-level supplement that does not exist on disk.
            if origin_prefix and not block.origin.startswith(origin_prefix):
                block.origin = origin_prefix + block.origin
        if sniffed:
            note = f"name carries no usable extension; read as {sniffed}" + \
                   (f"; {note}" if note else "")
            meta = dict(meta or {})
            meta["sniffed_as"] = sniffed
        return FileResult(relative_path, role, status, blocks, origin_prefix + origin,
                          meta, label, caption, note)

    def dispatch() -> FileResult:
        if extension in IMAGE_EXTENSIONS:
            return result(IMAGE_NO_TEXT, note="figure image; no extractable text "
                                              "(a vision pass would be needed)")
        if extension in AUDIO_VIDEO_EXTENSIONS:
            return result(MEDIA_NO_TEXT, note="audio or video")
        if extension in DATA_EXTENSIONS:
            return result(DATA_SKIPPED, note="binary or columnar data file, not prose")
        if extension in OPAQUE_ARCHIVE_EXTENSIONS:
            return result(UNSUPPORTED,
                          note=f"{extension} needs a third-party reader; decompress "
                               f"by hand if it holds tables")
        if extension in COMPRESSED_EXTENSIONS:
            return _extract_compressed(data, relative_path, limits, role, label,
                                       caption, depth, overrides)
        if extension in LEGACY_DOC_EXTENSIONS:
            return result(UNSUPPORTED, note=f"{extension} is not parsed by this stage")

        if extension in SPREADSHEET_EXTENSIONS:
            cards, status, meta = spreadsheet.cards_from_xlsx(data, relative_path, limits, overrides)
            return result(status, _table_blocks(cards, relative_path, "xlsx", role,
                                                limits, label, caption),
                          "xlsx", meta, note=meta.get("reason"))
        if extension in LEGACY_SPREADSHEET_EXTENSIONS:
            cards, status, meta = spreadsheet.cards_from_xls(data, relative_path, limits, overrides)
            return result(status, _table_blocks(cards, relative_path, "xls", role,
                                                limits, label, caption),
                          "xls", meta, note=meta.get("reason"))
        if extension in DELIMITED_EXTENSIONS:
            cards, status, meta = spreadsheet.cards_from_csv(data, relative_path, limits, overrides)
            return result(status, _table_blocks(cards, relative_path, "csv", role,
                                                limits, label, caption),
                          "csv", meta, note=meta.get("reason"))
        if extension in PLAIN_TEXT_EXTENSIONS:
            blocks, status, meta = _plain_text_blocks(data, relative_path, limits, role, overrides)
            return result(status, blocks, "txt", meta)
        if extension == ".pdf":
            blocks, status, meta = pdf.blocks_from_pdf(data, relative_path, limits)
            note = meta.get("reason")
            if status == SCANNED:
                # Both halves, and neither is the other's fallback: the first says
                # what the status means, the second says why OCR did not change it
                # -- which since `pdf._ocr_pass` exists is always answerable, and is
                # usually an install command.
                note = "parses as a PDF but has almost no extractable text: scanned images"
                if meta.get("reason"):
                    note = f"{note}; {meta['reason']}"
            if status == OK_VIA_OCR:
                ocr = meta.get("ocr") or {}
                note = (f"scanned pages, read by OCR at {ocr.get('dpi')} dpi: "
                        f"weaker evidence than a text layer")
            return result(status, blocks, "pdf", meta, note=note)
        if extension == ".docx":
            blocks, status, meta = docxfile.blocks_from_docx(data, relative_path, limits, overrides)
            return result(status, blocks, "docx", meta, note=meta.get("reason"))
        if extension in XML_EXTENSIONS:
            blocks, status, meta = jats.blocks_from_jats(data, relative_path, limits, overrides)
            return result(status, blocks, "jats", meta, note=meta.get("reason"))
        if extension in HTML_EXTENSIONS:
            blocks, status, meta = htmlfile.blocks_from_html(data, relative_path, limits)
            return result(status, blocks, "html", meta, note=meta.get("reason"))
        if extension == ".zip":
            return _extract_archive(data, relative_path, limits, role, label, caption,
                                    depth, overrides, kind="zip")

        return result(UNSUPPORTED,
                      note=f"no parser for {extension or 'files without an extension'}")

    try:
        return dispatch()
    except Exception as e:
        # The backstop. Each parser guards itself, but a run of sixty articles
        # must not end because one supplement found a shape nobody anticipated:
        # a 4,000-deep `<sec>` nest raised RecursionError out of extract_article
        # and left neither extraction.json nor blocks.jsonl on disk.
        return result(PARSER_ERROR, note=f"{type(e).__name__}: {e}")


#: Which container reader `_extract_archive` uses, and what a block read out of
#: one says its origin was.
_ARCHIVE_READERS = {
    "zip": archive.read_members,
    "tar": archive.read_tar_members,
}


def _nested_archive_extensions(limits: Limits, depth: int) -> set:
    """Container extensions worth pulling out of a container at this depth.

    Three of this corpus's zips contain only more zips, which is why the descent
    exists at all; a zip holding a `tables.tar.gz` is the same shape and now reads
    the same way.
    """
    if depth + 1 >= limits.max_archive_depth:
        return set()
    return {".zip"} | TAR_EXTENSIONS | STREAM_COMPRESSED_EXTENSIONS


def _extract_archive(data: bytes, relative_path: str, limits: Limits, role: str,
                     label: Optional[str], caption: Optional[str], depth: int,
                     overrides=None, kind: str = "zip") -> FileResult:
    """A zip or a tar: read the members worth reading, extract each in turn.

    `kind` picks the reader and nothing else. Both return the same
    `(members, meta)` pair and both apply the same caps, so the statuses below --
    which are the part a curator reads -- are decided once for either container.
    """
    wanted = set(TEXT_BEARING_EXTENSIONS) | _nested_archive_extensions(limits, depth)
    members, meta = _ARCHIVE_READERS[kind](data, limits, wanted)

    blocks: List[Block] = []
    statuses: List[str] = []
    for name, member_bytes in members:
        inner = extract_bytes(
            member_bytes, f"{relative_path}!{name}", limits, role=role,
            label=label, origin_prefix=f"{kind}:", depth=depth + 1, overrides=overrides,
        )
        statuses.append(inner.status)
        blocks.extend(inner.blocks)

    census = meta.get("member_extensions") or {}
    imagey = {e for e in census if e in IMAGE_EXTENSIONS | AUDIO_VIDEO_EXTENSIONS}
    note = meta.get("reason")

    if OK in statuses:
        status = OK
    elif census and imagey == set(census):
        status = IMAGE_NO_TEXT
        note = "archive holds only figure images or media"
    elif _nested_only(census) and not _nested_archive_extensions(limits, depth):
        status = NO_TEXT
        note = (f"archive holds only nested archives and the depth cap "
                f"({limits.max_archive_depth}) stopped the descent")
    else:
        status = NO_TEXT
        if meta.get("skipped") and not note:
            note = (f"nothing text-bearing was read from {meta.get('members_total', 0)} "
                    f"member(s); first reason: {meta['skipped'][0]['reason']}")

    if meta.get("skipped"):
        meta["errors"] = [f"{s['name']}: {s['reason']}" for s in meta["skipped"][:10]]
        meta.pop("skipped")
    if meta.get("walk_stopped") and not note:
        note = meta["walk_stopped"]
    return FileResult(relative_path, role, status, blocks, kind, meta, label, caption, note)


def _nested_only(census: dict) -> bool:
    """Whether every member of an archive is itself an archive."""
    containers = {".zip"} | TAR_EXTENSIONS | STREAM_COMPRESSED_EXTENSIONS
    return bool(census) and set(census) <= containers


def _extract_compressed(data: bytes, relative_path: str, limits: Limits, role: str,
                        label: Optional[str], caption: Optional[str], depth: int,
                        overrides=None) -> FileResult:
    """A `.gz`/`.tgz`/`.tar`/`.bz2`/`.xz` supplement: tarball or single file.

    Six files in this corpus, 107 MB, every one of them reported
    `unsupported_format` before this existed -- "compressed archive other than zip;
    decompress by hand if it holds tables" -- and five of the six are one
    compressed CSV or TSV each, which is to say a supplementary table with a
    wrapper on it rather than an archive at all.

    Which of the two shapes it is comes from the bytes, not the name.
    `archive.looks_like_tar` costs one member header, and the alternative would be
    wrong twice over: `MOESM4_ESM.tgz` is an uncompressed tar, so a suffix rule
    would hand it to gzip, and `...-supplement-Table_5.gz` is a single `meta.csv`,
    so the same rule reading `.gz` as "tarball" would find no members in it.

    The depth cap is checked here rather than only in the wanted set, which is what
    `_extract_archive` can get away with: nothing stops `extract_bytes` from
    dispatching a `.gz` inside a `.gz` inside a `.gz` by extension, and each level
    of that decompresses before anything looks at it.
    """
    def refuse(status: str, note: str, meta=None) -> FileResult:
        return FileResult(relative_path, role, status, [], "", meta or {}, label,
                          caption, note)

    if depth >= limits.max_archive_depth:
        return refuse(NO_TEXT, f"nested {limits.max_archive_depth} archives deep, "
                               f"which is the depth cap (`max_archive_depth`)")

    if archive.looks_like_tar(data):
        return _extract_archive(data, relative_path, limits, role, label, caption,
                                depth, overrides, kind="tar")

    plain, status, meta = archive.decompress(data, limits)
    if plain is None:
        return refuse(status, meta.get("reason", "not a readable compressed file"), meta)

    name = archive.inner_name(relative_path, data)
    inner = extract_bytes(
        plain, f"{relative_path}!{name}", limits, role=role, label=label,
        origin_prefix=f"{meta['compression']}:", depth=depth + 1, overrides=overrides,
    )
    note = f"{meta['compression']} wrapper around one file, {name}"
    if meta.get("truncated_stream"):
        note += "; the compressed stream is truncated, so this is a partial read"
    if inner.note:
        note = f"{note}; {inner.note}"
    return FileResult(relative_path, role, inner.status, inner.blocks,
                      meta["compression"], {**inner.meta, **meta}, label, caption, note)


def extract_path(path, relative_path: str, limits: Limits, role: str = SUPPLEMENT,
                 label: Optional[str] = None, caption: Optional[str] = None,
                 content_type: str = "", overrides=None) -> FileResult:
    """`extract_bytes` for a file on disk, with the existence and size checks."""
    target = Path(path)
    if not target.exists():
        return FileResult(relative_path, role, MISSING, note="recorded in the manifest "
                                                             "but not on disk")
    size = target.stat().st_size
    if size > limits.max_file_mb * 1024 * 1024:
        return FileResult(relative_path, role, TOO_LARGE, label=label, caption=caption,
                          note=f"{size} bytes is over the {limits.max_file_mb} MB cap")
    try:
        data = target.read_bytes()
    except OSError as e:
        return FileResult(relative_path, role, UNREADABLE, label=label, caption=caption,
                          note=f"{type(e).__name__}: {e}")
    return extract_bytes(data, relative_path, limits, role=role, label=label,
                         caption=caption, content_type=content_type,
                         overrides=overrides)


# -- how much of the main text carries a section label -----------------------

#: Sections that are part of the body of a paper, as opposed to its apparatus.
_BODY_SECTIONS = ("abstract", "introduction", "results", "methods", "discussion")
#: The two whose absence from a body makes every downstream filter unreliable.
_REQUIRED_SECTIONS = ("methods", "results")


def _section_labelling(main_result: FileResult, main_info: dict) -> dict:
    """What fraction of the main text is labelled, and how much to trust it.

    10.1126/science.aat5031 is `complete`, `main_text.source: pdf`, and 52 of its
    87 main-text blocks carry no section at all -- the entire Results and
    Discussion among them. `totals.sections` nonetheless lists `methods`, because
    every one of that article's methods blocks comes from a *supplementary* PDF,
    so a downstream filter for `section == methods` over the main text returns
    nothing and the record says nothing is wrong.

    Deliberately not a new article status: the taxonomy is closed, and this is a
    measurement rather than a verdict.
    """
    blocks = main_result.blocks
    total_chars = sum(len(b.text) for b in blocks)
    labelled = [b for b in blocks if b.section]
    labelled_chars = sum(len(b.text) for b in labelled)
    coverage = round(labelled_chars / total_chars, 2) if total_chars else 0.0
    present = {b.section for b in labelled}
    found = sorted(present & set(_BODY_SECTIONS))
    missing = [s for s in _REQUIRED_SECTIONS if s not in present]

    method = "declared" if main_result.origin.endswith("jats") else "heuristic"
    percent = f"{int(round(coverage * 100))}% of characters labelled"
    if method == "declared":
        confidence = "declared"
        why = "sections come from the JATS XML rather than from a heading heuristic"
    elif not present & {"introduction", "results", "methods", "discussion"}:
        confidence = "none"
        why = f"no body section label anywhere in the main text ({percent})"
    elif missing:
        confidence = "low"
        why = f"no {' or '.join(missing)} label anywhere in the main text ({percent})"
    elif coverage < 0.75:
        confidence = "low"
        why = f"only {percent} in the main text"
    else:
        confidence = "ok"
        why = f"{percent}"

    return {
        "method": method,
        "labelled_blocks": len(labelled), "total_blocks": len(blocks),
        "labelled_chars": labelled_chars, "total_chars": total_chars,
        "coverage": coverage,
        "body_sections_found": found,
        "body_sections_missing": missing,
        "confidence": confidence,
        "why": why,
    }


def _review_signals(all_blocks: List[Block], main_result: FileResult,
                    supplements: List[FileResult], jats_available: bool) -> dict:
    """The counts a review queue is computed from, at the top of the record.

    The strongest triage signal there is -- `header_confidence == "low"`, 16 of
    the 68 table cards on this machine -- lived only inside `blocks.jsonl`, so no
    queue could be built from `extraction.json` at all.
    """
    cards = [b.table for b in all_blocks if b.kind == TABLE and b.table]
    main_blocks = main_result.blocks
    return {
        "tables_total": len(cards),
        "tables_header_low": sum(1 for c in cards
                                 if c.get("header_confidence") == "low"),
        "tables_headerless": sum(1 for c in cards if c.get("header_row") is None),
        "tables_truncated": sum(1 for c in cards if c.get("truncated")),
        "tables_columns_dropped": sum(c.get("columns_dropped") or 0 for c in cards),
        "main_text_blocks": len(main_blocks),
        "main_text_unlabelled": sum(1 for b in main_blocks if not b.section),
        "supplements_sniffed": [r.path for r in supplements if r.meta.get("sniffed_as")],
        "jats_reference_available": jats_available,
    }


# -- the cache key -----------------------------------------------------------

def _parser_versions() -> Dict[str, str]:
    """The third-party parsers whose output is being cached.

    Python is major.minor only. A patch bump does not change how PyMuPDF reads a
    page, and invalidating every extraction in the corpus for one would make the
    key expensive enough that someone turns it off.

    Every parser this stage dispatches to has to be named here, and `xlrd` is the
    demonstration of what happens when one is not. It was missing for as long as it
    was an optional extra, so installing it moved nothing in `extraction_key`: the
    56 `.xls` supplements in the corpus went on being served their cached
    `unsupported_format` from the install that had no xlrd, and `--force` -- a
    re-extract of all 393 articles -- was the only thing that could pick the new
    parser up. `source_fingerprint` in `__init__.py` exists for this same bug one
    layer in: a version number nobody bumps is not a cache key, and neither is a
    dependency nobody records.

    `tesseract` is here for the same reason and is not a Python package at all: it
    is the optional *system* dependency the OCR pass needs, and 70 scanned
    supplements would otherwise keep serving the `no_text_scanned_pdf` cached from
    an install that had no way to read them. See `pdf.tesseract_version`, including
    what being in this key costs -- installing it re-extracts all 393 articles and
    not only the 70 that hold a scan.

    An absent parser is recorded as `"absent"` rather than left out, so the key
    moves in both directions -- installing xlrd invalidates, removing it
    invalidates -- and the `parser_versions` block in extraction.json says which of
    the two kinds of install produced the record.
    """
    import fitz
    import openpyxl
    try:
        import xlrd
        xlrd_version = xlrd.__version__
    except ImportError:
        # Still guarded: an install predating requirements.txt's promotion of xlrd
        # to a hard dependency reads `.xls` as `unsupported_format`, and that
        # verdict is exactly what this key has to stop outliving the install.
        xlrd_version = "absent"
    return {"pymupdf": fitz.__version__, "openpyxl": openpyxl.__version__,
            "xlrd": xlrd_version, "tesseract": pdf.tesseract_version(),
            "python": "%d.%d" % sys.version_info[:2]}


def extraction_key(manifest_sha: str, limits: Limits, review_sha: str = "") -> str:
    """Everything that decides what an extraction contains, in one hash.

    The parts stay in the record separately as `source_manifest_sha256`,
    `extractor_version` and `limits`, so a human can see *which* of them moved.
    `limits` is in here because editing `max_scan_rows` in config.yaml used to
    reuse a stale extraction made under the old cap.
    """
    payload = {
        "manifest": manifest_sha,
        "extractor_version": __version__,
        "source": source_fingerprint(),
        "limits": limits.to_dict(),
        "parsers": _parser_versions(),
        "review": review_sha,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


# -- article orchestration ---------------------------------------------------

def _supplement_key(entry: dict) -> List[str]:
    """Names a JATS `<supplementary-material>` href might use for this entry."""
    keys = []
    original = entry.get("original_name")
    if original:
        keys.append(original)
    path = entry.get("path") or ""
    base = Path(path).name
    if base:
        keys.append(base)
        # Stored names carry the retrieval-order prefix: `03_mmc7.xlsx`.
        if "_" in base:
            keys.append(base.split("_", 1)[1])
    return keys


#: Where a supplement's label came from. Closed, like the statuses.
LABEL_SOURCES = frozenset({"jats", "jats_caption", "manifest", "review", "none"})


def _label_from_caption(caption: str) -> Optional[str]:
    """`Table S7. Cytokine analysis, related to Figure 6` -> `Table S7`.

    12 of the 25 `ok` supplements here carry a JATS caption and no JATS label.
    The leading identifier before the first period is the publisher's name for
    the file; anything longer than 40 characters is a sentence, not a name, and
    gets no label rather than a wrong one.
    """
    head = caption.split(".", 1)[0].strip()
    return head if head and len(head) <= 40 else None


def _shared_manifest_labels(record: dict) -> set:
    """Manifest labels used by two or more entries: transport names, not file names.

    Measured rather than denylisted, because the strings vary by tier.
    `Europe PMC supplementary archive` covers 39/39 and 49/49 entries in two
    articles here and `Download` covers 11/11 and 2/2, while a genuine Cell Press
    per-file caption (`Table S1. Primer sequences...`) is unique.
    """
    counts = Counter(entry.get("label") for entry in (record.get("supplementary") or [])
                     if entry.get("label"))
    return {label for label, n in counts.items() if n >= 2}


def _shared_jats_labels(record: dict, labels: Dict[str, dict]) -> set:
    """The same rule for JATS labels, which a publisher can also make useless.

    A label two files share is not a per-file name, and nothing about that
    depends on the label having come from the manifest. 10.1073/pnas.1914143116
    gives every one of its ten `<supplementary-material>` elements
    `<label>Supplementary File</label>`, so the JATS branch -- trusted above the
    manifest precisely because it is the publisher's own name -- handed all ten
    files the same one. Rejecting it drops them to `none`, which is honest and is
    what puts them in front of a curator.
    """
    counts: Counter = Counter()
    for entry in record.get("supplementary") or []:
        if not entry.get("path"):
            continue
        matched = next((labels[name] for name in _supplement_key(entry) if name in labels),
                       None)
        if (matched or {}).get("label"):
            counts[matched["label"]] += 1
    return {label for label, n in counts.items() if n >= 2}


def _main_text(article_dir: Path, record: dict, limits: Limits,
               overrides=None) -> Tuple[FileResult, Dict[str, dict], dict]:
    """Pick and extract the main text. Returns `(result, supplement_labels, info)`.

    `info["thin"]` is set here for whichever rendition won, whatever its source.
    `main_usable` used to be `status == OK and chars > 0`, so a synthetic article
    with only a thin JATS body and no PDF came out `complete` with
    `main_text.chars: 185`. The four complete articles in this corpus carry
    89,151 / 88,262 / 43,746 / 94,014 characters, so the gate flips none of them.
    """
    info: dict = {}
    labels: Dict[str, dict] = {}
    result = _choose_main_text(article_dir, record, limits, info, labels, overrides)
    info["thin"] = result.chars < limits.min_main_text_chars
    _apply_reviewed_section(result, overrides)
    return result, labels, info


def _apply_reviewed_section(result: FileResult, overrides) -> None:
    """Label the blocks the parser left `None` with the section a human gave.

    `review._section_span_question` asks this only where `section_audit.py` cannot
    score the labeller for free -- a PDF main text with no JATS beside it -- and it
    asks once for the whole span, so the answer is one value keyed on the main
    text's own path. Applied here rather than inside `pdf.py` for that reason: it is
    a fact about the article, not about a page, and it keeps the parsers unaware of
    the review layer.

    Two rules the coarseness of that answer forces:

    * only blocks with no section are touched. A heading the parser *did* recognise
      is better evidence than one value covering everything it did not, so a
      reviewed span never overwrites one.
    * `section_for` is called once, not per block. It counts every call into
      `Overrides.applied()`, so asking per block would report one answer as
      hundreds and make `overrides_applied` useless as a check.

    Nothing here is silent: the section, the count and the blocks left alone all
    reach `extraction.json`, and each changed block carries `section_source`.
    """
    if overrides is None or not result.blocks:
        return
    section = overrides.section_for(result.path, "main_text", "", 0)
    if not section:
        return
    changed = 0
    for block in result.blocks:
        if block.section is None:
            block.section = section
            block.section_source = "review"
            changed += 1
    result.meta["sections_from_review"] = {
        "section": section,
        "blocks": changed,
        # The parser's own labels, kept. Reported so the reviewed span reads as a
        # floor under the labelling rather than a replacement for it.
        "blocks_already_labelled": len(result.blocks) - changed,
    }


def _rejected_by_fetch(entry: dict) -> bool:
    """Did the fetch stage say these bytes are not the article we asked for?

    The verdict was already made and written down; the trap is this stage not
    reading it. 10.1126/science.adf1226 stored a 10x Genomics Visium user guide as
    `fulltext.pdf`, and extraction turned it into 1,393 blocks of main text that
    read exactly like a paper with nothing to report. A human's `main_text_source`
    override still wins over this, because a human looked at the file.

    Reads `validate.IDENTITY_FAILURES` rather than restating the pair, so the two
    stages cannot disagree about which statuses mean "not the article".
    """
    return (entry or {}).get("status") in IDENTITY_FAILURES


def _choose_main_text(article_dir: Path, record: dict, limits: Limits,
                      info: dict, labels: Dict[str, dict],
                      overrides=None) -> FileResult:
    """JATS if it is there and substantial, else the PDF, else the landing page."""
    forced = overrides.main_text_source() if overrides is not None else None
    xml_entry = record.get("fulltext_xml") or {}
    xml_path = xml_entry.get("path")
    pdf_entry_early = record.get("fulltext") or {}
    rejected = [name for name, entry in (("fulltext.nxml", xml_entry),
                                         ("fulltext.pdf", pdf_entry_early))
                if _rejected_by_fetch(entry)]
    if rejected and forced is None:
        info["not_the_requested_article"] = {
            "files": rejected,
            "fetch_status": {name: (entry or {}).get("status") for name, entry
                             in (("fulltext.nxml", xml_entry),
                                 ("fulltext.pdf", pdf_entry_early))
                             if _rejected_by_fetch(entry)},
        }
        if _rejected_by_fetch(xml_entry):
            xml_path = None
    xml_result: Optional[FileResult] = None
    if xml_path and (article_dir / xml_path).exists():
        xml_result = extract_path(article_dir / xml_path, xml_path, limits,
                                  role=MAIN_TEXT, overrides=overrides)
        labels.update(xml_result.meta.get("supplement_labels") or {})
        info["jats_chars"] = xml_result.chars

    pdf_entry = record.get("fulltext") or {}
    pdf_path = pdf_entry.get("path")
    pdf_available = bool(pdf_path and (article_dir / pdf_path).exists())
    info["pdf_available"] = pdf_available
    if forced is None and _rejected_by_fetch(pdf_entry):
        pdf_available = False

    if forced == "jats" and xml_result is not None:
        info["source"] = "jats"
        info["source_forced_by_review"] = True
        return xml_result
    if forced == "pdf" and pdf_available:
        info["source"] = "pdf"
        info["source_forced_by_review"] = True
        return extract_path(article_dir / pdf_path, pdf_path, limits,
                            role=MAIN_TEXT, overrides=overrides)

    if xml_result is not None and xml_result.status == OK and \
            xml_result.chars >= limits.min_main_text_chars:
        info["source"] = "jats"
        if pdf_available:
            info["note"] = "JATS XML preferred over the PDF; the PDF was not parsed"
        return xml_result

    if pdf_available:
        if xml_result is not None and xml_result.status == OK:
            info["note"] = (f"JATS XML yielded only {xml_result.chars} characters "
                            f"(under {limits.min_main_text_chars}); fell back to the PDF")
        pdf_result = extract_path(article_dir / pdf_path, pdf_path, limits,
                                  role=MAIN_TEXT, overrides=overrides)
        if pdf_result.status == OK or xml_result is None:
            info["source"] = "pdf"
            return pdf_result
        # A scanned PDF beside thin XML: keep whichever has more text.
        if xml_result.chars >= pdf_result.chars:
            info["source"] = "jats"
            return xml_result
        info["source"] = "pdf"
        return pdf_result

    if xml_result is not None:
        info["source"] = "jats"
        if xml_result.status == OK and xml_result.chars < limits.min_main_text_chars:
            # The note used to claim a fallback to a PDF that is not there: it was
            # set before anything checked whether one existed.
            info["note"] = (f"JATS XML yielded only {xml_result.chars} characters "
                            f"(under {limits.min_main_text_chars}) and there is no PDF "
                            f"to fall back to; this is front matter, not the article")
        return xml_result

    landing = (record.get("landing_html") or {}).get("path") or store.LANDING_HTML
    if (article_dir / landing).exists():
        result = extract_path(article_dir / landing, landing, limits, role=MAIN_TEXT,
                              overrides=overrides)
        info["source"] = "landing_html"
        info["landing_page_only"] = True
        info["note"] = ("no PDF and no XML: main text is the saved publisher landing "
                        "page, which is metadata and abstract at best, not the article")
        return result

    info["source"] = None
    return FileResult("", MAIN_TEXT, MISSING,
                      note="no PDF, no XML, and no saved landing page")


def extract_article(article_dir, limits: Optional[Limits] = None, force: bool = False,
                    write_markdown: bool = True, config: Optional[dict] = None) -> dict:
    """Extract one corpus article. Returns the extraction record it wrote.

    A human review of this article, if there is one, is loaded from
    `reviews/<slug>.json` and threaded into every parser: a correction that does
    not change the next extraction is a note, not a correction.
    """
    limits = limits or Limits()
    article_dir = Path(article_dir)
    manifest_path = article_dir / store.MANIFEST_NAME
    record = store.read_manifest(article_dir)
    if record is None:
        return {"slug": article_dir.name, "status": "no_manifest",
                "problems": [f"no readable {store.MANIFEST_NAME} in {article_dir}"]}

    manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    slug = record.get("slug") or article_dir.name
    review_file = review.review_path(slug, config)
    # In the key, so the first correction is not silently discarded by the next
    # `manuscript-extract all`.
    review_sha = (hashlib.sha256(review_file.read_bytes()).hexdigest()
                  if review_file.exists() else "")
    overrides = review.Overrides.load(slug, record, config)
    key = extraction_key(manifest_sha, limits, review_sha)
    output_dir = article_dir / EXTRACT_DIR
    existing_path = output_dir / EXTRACTION_NAME
    problems: List[str] = []
    if not force and existing_path.exists():
        try:
            existing = json.loads(existing_path.read_text(encoding="utf-8"))
        except ValueError:
            existing = None
        if existing and existing.get("extraction_key") == key:
            # "blocks.jsonl exists" is not the same claim as "blocks.jsonl is the
            # file this record describes". Emptying a real 475 KB one and
            # re-running gave `cached: True, status: complete, blocks: 532` over
            # zero lines. A record written before this check has no
            # `blocks_sha256`, which counts as a mismatch and re-extracts once.
            on_disk = blocks_sha256(output_dir / BLOCKS_NAME)
            if on_disk is not None and existing.get("blocks_sha256") == on_disk:
                existing["cached"] = True
                return existing
            problems.append(f"{BLOCKS_NAME} did not match the hash in "
                            f"{EXTRACTION_NAME}; re-extracted")

    main_result, labels, main_info = _main_text(article_dir, record, limits, overrides)
    results: List[FileResult] = [main_result]

    entries_without_path = 0
    policy_removed: List[str] = []
    shared_labels = _shared_manifest_labels(record)
    shared_jats = _shared_jats_labels(record, labels)
    for entry in record.get("supplementary") or []:
        path = entry.get("path")
        if not path:
            # Two different facts arrive here as the same shape, and only the marker
            # tells them apart. `manuscript-fetch drop-media` deletes a stored figure
            # and rewrites its entry to `name`/`bytes`/`sha256` plus a removal
            # marker, deliberately without a `path`
            # (`store.mark_entry_removed` explains why -- a path over a deleted file
            # makes the article incomplete forever). Counting those into
            # `entries_without_path` would raise MANIFEST_ENTRY_WITHOUT_PATH, whose
            # text is "a supplementary entry in the manifest has no file on disk to
            # read", on the 138 articles in this corpus that are correct: a caveat
            # meaning "this manifest is malformed" would come to mean "this manifest
            # is either malformed or perfectly fine", and stop being worth reading.
            if store.entry_removed_by_policy(entry):
                policy_removed.append(entry.get("name") or entry.get("original_name")
                                      or "?")
                continue
            entries_without_path += 1
            continue
        matched = next((labels[name] for name in _supplement_key(entry) if name in labels),
                       None)
        caption = (matched or {}).get("caption")
        # JATS beats a synthesised name, which beats the manifest. A label two
        # entries share is rejected whichever it came from: from the manifest it
        # is the transport's name for the request, and from the JATS it is a
        # publisher labelling ten files `Supplementary File`. Neither names a file.
        reviewed = overrides.label_for(path) if overrides is not None else None
        if reviewed and (reviewed.get("override") or {}).get("label"):
            label, label_source = reviewed["override"]["label"], "review"
            caption = reviewed["override"].get("caption") or caption
        elif (matched or {}).get("label") and matched["label"] not in shared_jats:
            label, label_source = matched["label"], "jats"
        elif caption and _label_from_caption(caption):
            label, label_source = _label_from_caption(caption), "jats_caption"
        elif entry.get("label") and entry["label"] not in shared_labels:
            label, label_source = entry["label"], "manifest"
        else:
            label, label_source = None, "none"
        result = extract_path(article_dir / path, path, limits,
                              role=SUPPLEMENT, label=label, caption=caption,
                              content_type=entry.get("content_type") or "",
                              overrides=overrides)
        result.meta["label_source"] = label_source
        results.append(result)

    # A human's answer about whether a file carries content never changes the
    # file's status -- the taxonomy stays closed and a .pptx stays
    # `unsupported_format`. It changes what the article's status is computed
    # from, and nothing disappears from the record either way.
    unreachable_content: List[str] = []
    cleared_by_review: List[str] = []
    denied_evidence = overrides.evidence_denied() if overrides is not None else set()
    for result in results[1:]:
        expected = overrides.content_expected(result.path) if overrides is not None \
            else None
        if expected is True and result.status not in _PRODUCTIVE:
            unreachable_content.append(result.path)
        elif expected is False:
            cleared_by_review.append(result.path)
        if result.path in denied_evidence:
            for block in result.blocks:
                block.role = NON_EVIDENCE

    all_blocks: List[Block] = []
    for result in results:
        all_blocks.extend(result.blocks)
    number_blocks(all_blocks)

    output_dir.mkdir(parents=True, exist_ok=True)
    written = write_blocks(output_dir / BLOCKS_NAME, all_blocks)
    if write_markdown:
        (output_dir / ARTICLE_MD).write_text(render_markdown(all_blocks), encoding="utf-8")

    by_status: Dict[str, int] = {}
    for result in results[1:]:
        by_status[result.status] = by_status.get(result.status, 0) + 1

    text_bearing_failures = sorted({
        r.path for r in results[1:] if r.status not in _PRODUCTIVE | _BENIGN
    } | set(unreachable_content))
    # Nothing disappears: a file a human cleared stays listed, and one key away
    # is the human who cleared it.
    blocking = [p for p in text_bearing_failures if p not in cleared_by_review]
    # `in _PRODUCTIVE` rather than `== OK`, so an OCR'd main text counts as one.
    # No file in this corpus is that -- all 70 scanned files are supplements, zero
    # main texts -- but `== OK` would have made an OCR'd article `failed` while its
    # own record said `ok_via_ocr`, and a reader of the record can see which it was.
    main_usable = main_result.status in _PRODUCTIVE and main_result.chars > 0
    main_info["usable"] = main_usable
    supplement_text = any(r.blocks for r in results[1:])

    fetch_supplements = record.get("supplementary_status")
    caveats: List[str] = []
    if fetch_supplements in _FETCH_SUPPLEMENTS_LOST:
        caveats.append(SUPPLEMENTS_MISSING)
    elif fetch_supplements == "fetched_unverified":
        caveats.append(SUPPLEMENTS_UNVERIFIED)
    # `chars > 0` was the only length test, so a 185-character front-matter-only
    # JATS body with no PDF beside it came out `complete`. No new limit:
    # `min_main_text_chars` is already the number and already carries its why.
    if main_info.get("thin"):
        caveats.append(MAIN_TEXT_THIN)
    if main_info.get("landing_page_only"):
        caveats.append(LANDING_PAGE_ONLY)
    if main_info.get("not_the_requested_article"):
        caveats.append(MAIN_TEXT_NOT_THE_ARTICLE)
    if entries_without_path:
        caveats.append(MANIFEST_ENTRY_WITHOUT_PATH)
    # And no caveat of its own for `policy_removed`, which was the other option.
    # A caveat is for something true about an *extraction* that its statuses do not
    # already say, and a policy removal is true about nothing here: every file
    # `drop-media` takes is one this module would have dispatched to
    # `image_no_text` or `media_no_text` -- the same two extension sets, imported
    # from `text_bearing` precisely so that cannot drift -- both of which are in
    # `_BENIGN` and produce no block, no table and no character. Extract the article
    # before and after the sweep and `blocks.jsonl` is byte-identical. So the count
    # goes in the record below, where a reader can see the files are gone and why,
    # and the caveat vocabulary keeps meaning "read this extraction with care".

    if main_usable and not blocking \
            and not _BLOCKING_CAVEATS & set(caveats):
        status = "complete"
    elif main_usable or supplement_text:
        status = "partial"
    else:
        # No usable main text and no supplement text: there is nothing here to
        # ask a question of, and saying so is the whole point of this record.
        status = "failed"

    extraction = {
        "doi": record.get("doi"),
        "slug": record.get("slug") or article_dir.name,
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "extractor_version": __version__,
        "source_manifest_sha256": manifest_sha,
        "extraction_key": key,
        "parser_versions": _parser_versions(),
        "fetch_status": record.get("status"),
        "fetch_supplementary_status": fetch_supplements,
        "limits": limits.to_dict(),
        "main_text": {**main_result.to_dict(), **main_info,
                      "section_labelling": _section_labelling(main_result, main_info)},
        "supplementary": [r.to_dict() for r in results[1:]],
        "supplementary_by_status": by_status,
        "totals": {
            "files": len(results),
            "blocks": len(all_blocks),
            "chars": sum(len(b.text) for b in all_blocks),
            "tables": sum(1 for b in all_blocks if b.kind == TABLE),
            "sections": sorted({b.section for b in all_blocks if b.section}),
        },
        "unextracted_text_files": text_bearing_failures,
        # Files the fetch stage stored and `drop-media` later deleted under
        # `fetch.text_bearing_only`. Listed, not merely counted: it is the one place
        # an extraction says out loud that the article's supplement set on disk is
        # smaller than the set the manifest describes, and every name here is a file
        # this run would have reported as `image_no_text` or `media_no_text`.
        "removed_not_text_bearing": policy_removed,
        "unreachable_content": unreachable_content,
        "cleared_by_review": cleared_by_review,
        "supplement_label_rejected": sorted(shared_labels | shared_jats),
        "review_signals": _review_signals(
            all_blocks, main_result, results[1:],
            bool((record.get("fulltext_xml") or {}).get("path")
                 and (article_dir / (record["fulltext_xml"]["path"])).exists())),
        "caveats": caveats,
        "status": status,
        "problems": problems,
        "blocks_path": f"{EXTRACT_DIR}/{BLOCKS_NAME}",
        "blocks_sha256": written["sha256"],
        "blocks_lines": written["lines"],
    }
    # Always present, so a reviewed extraction is visibly different from an
    # unreviewed one without anyone having to open the review file.
    blocks_file = output_dir / BLOCKS_NAME
    queue = review.queue_for(extraction, blocks_file, limits, record)
    state, stale = review.state_of(review.read_review(review_file), extraction,
                                   record, queue)
    stored = review.read_review(review_file) or {}
    extraction["review"] = {
        "state": state,
        "queued": sum(1 for i in queue if i["kind"] != review.SIGN_OFF),
        "answered": len(stored.get("answers") or []),
        "stale": [i["kind"] for i in stale],
        "overrides_applied": overrides.applied(),
        "overrides_applied_kinds": overrides.applied_kinds(),
        "sign_off": stored.get("sign_off"),
        "queue_truncated": review.queue_truncated(extraction, blocks_file, limits),
    }
    existing_path.write_text(
        json.dumps(extraction, indent=2, ensure_ascii=False), encoding="utf-8")
    return extraction


def read_extraction(article_dir) -> Optional[dict]:
    path = Path(article_dir) / EXTRACT_DIR / EXTRACTION_NAME
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def summarize(extraction: dict) -> str:
    """One-line human summary, matching the shape `store.summarize` prints."""
    totals = extraction.get("totals") or {}
    main = extraction.get("main_text") or {}
    by_status = extraction.get("supplementary_by_status") or {}
    supplements = "  ".join(f"{k}={v}" for k, v in sorted(by_status.items())) or "-"
    caveats = extraction.get("caveats") or []
    reviewed = extraction.get("review") or {}
    state = reviewed.get("state", "unreviewed")
    detail = {"queued": reviewed.get("queued"), "stale": len(reviewed.get("stale") or [])}
    token = f"{state}({detail[state]})" if state in detail and detail[state] else state
    return (
        f"{extraction.get('status', '?'):8s} main={str(main.get('source')):13s} "
        f"blocks={totals.get('blocks', 0):<5d} tables={totals.get('tables', 0):<4d} "
        f"chars={totals.get('chars', 0):<8d} suppl[{supplements}]"
        + (f" caveats[{' '.join(caveats)}]" if caveats else "")
        + f" rev={token}"
    )
