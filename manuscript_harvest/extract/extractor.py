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
NO_TEXT = "no_text"
SCANNED = "no_text_scanned_pdf"
IMAGE_NO_TEXT = "image_no_text"
MEDIA_NO_TEXT = "media_no_text"
DATA_SKIPPED = "data_file_skipped"
UNSUPPORTED = "unsupported_format"
TOO_LARGE = "too_large"
MISSING = "missing"
UNREADABLE = "unreadable"
PARSER_ERROR = "parser_error"
"""A parser raised. Distinct from `unreadable`, which is a parser declining a
file it recognised as broken: this is the stage itself failing, and it is in
neither `_PRODUCTIVE` nor `_BENIGN` because the text was probably there."""

#: Statuses that mean "there was text here and we got it".
_PRODUCTIVE = {OK}
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
}

#: Fetch verdicts that mean files were lost, not merely uncounted. `none_retrieved`
#: belongs here: the README defines it as "a tier tried and every file it went
#: after was lost".
_FETCH_SUPPLEMENTS_LOST = {"expected_but_missing", "partial_failure", "none_retrieved"}

#: Caveats that stop an article being `complete`. `SUPPLEMENTS_UNVERIFIED` is
#: deliberately absent: it is common (2 of the 6 articles here) and is a caveat,
#: not a defect.
_BLOCKING_CAVEATS = {SUPPLEMENTS_MISSING, MAIN_TEXT_THIN, LANDING_PAGE_ONLY}

SPREADSHEET_EXTENSIONS = {".xlsx", ".xlsm"}
LEGACY_SPREADSHEET_EXTENSIONS = {".xls"}
DELIMITED_EXTENSIONS = {".csv", ".tsv"}
PLAIN_TEXT_EXTENSIONS = {".txt", ".md"}
XML_EXTENSIONS = {".xml", ".nxml"}
HTML_EXTENSIONS = {".html", ".htm"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".tif", ".tiff", ".bmp", ".eps",
                    ".ps", ".svg", ".webp", ".ai"}
MEDIA_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".mpg", ".mpeg", ".m4v",
                    ".mp3", ".wav", ".flv"}
DATA_EXTENSIONS = {".h5", ".h5ad", ".hdf5", ".loom", ".mtx", ".rds", ".rdata", ".npz",
                   ".npy", ".mat", ".sav", ".dta", ".bam", ".bai", ".cram", ".fastq",
                   ".fq", ".fa", ".fasta", ".bed", ".vcf", ".gtf", ".gff", ".bw",
                   ".bigwig", ".pkl", ".parquet", ".sqlite", ".db", ".zarr"}
COMPRESSED_EXTENSIONS = {".gz", ".bz2", ".xz", ".tar", ".tgz", ".7z", ".rar"}
LEGACY_DOC_EXTENSIONS = {".doc", ".rtf", ".odt", ".ods", ".ppt", ".pptx", ".key", ".pages"}

#: What is worth pulling out of a zip.
TEXT_BEARING_EXTENSIONS = (
    SPREADSHEET_EXTENSIONS | LEGACY_SPREADSHEET_EXTENSIONS | DELIMITED_EXTENSIONS
    | PLAIN_TEXT_EXTENSIONS | XML_EXTENSIONS | HTML_EXTENSIONS | {".pdf", ".docx"}
)

#: Every extension the dispatcher recognises, for deciding when to sniff instead.
KNOWN_EXTENSIONS = (
    TEXT_BEARING_EXTENSIONS | IMAGE_EXTENSIONS | MEDIA_EXTENSIONS | DATA_EXTENSIONS
    | COMPRESSED_EXTENSIONS | LEGACY_DOC_EXTENSIONS | {".zip"}
)

#: Content-Type is only consulted after the magic bytes have nothing to say.
_CONTENT_TYPES = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-excel": ".xls",
    "application/msword": ".doc",
    "application/zip": ".zip",
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
    if head.startswith((b"\x1f\x8b", b"BZh")):
        return ".gz"

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
                    "blocks_capped", "delimiter", "read_as", "text_runs",
                    "glued_headings_split", "truncated_paragraphs",
                    "sections", "sections_abandoned",
                    "low_value_blocks_withheld",
                    "glyphs_mapped", "glyphs_unmapped",
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
        paragraph = " ".join(chunk.split())
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
        if extension in MEDIA_EXTENSIONS:
            return result(MEDIA_NO_TEXT, note="audio or video")
        if extension in DATA_EXTENSIONS:
            return result(DATA_SKIPPED, note="binary or columnar data file, not prose")
        if extension in COMPRESSED_EXTENSIONS:
            return result(UNSUPPORTED, note="compressed archive other than zip; "
                                            "decompress by hand if it holds tables")
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
                note = "parses as a PDF but has almost no extractable text: scanned images"
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
            return _extract_zip(data, relative_path, limits, role, label, caption,
                                depth, overrides)

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


def _extract_zip(data: bytes, relative_path: str, limits: Limits, role: str,
                 label: Optional[str], caption: Optional[str], depth: int,
                 overrides=None) -> FileResult:
    wanted = set(TEXT_BEARING_EXTENSIONS)
    if depth + 1 < limits.max_archive_depth:
        wanted.add(".zip")
    members, meta = archive.read_members(data, limits, wanted)

    blocks: List[Block] = []
    statuses: List[str] = []
    for name, member_bytes in members:
        inner = extract_bytes(
            member_bytes, f"{relative_path}!{name}", limits, role=role,
            label=label, origin_prefix="zip:", depth=depth + 1, overrides=overrides,
        )
        statuses.append(inner.status)
        blocks.extend(inner.blocks)

    census = meta.get("member_extensions") or {}
    imagey = {e for e in census if e in IMAGE_EXTENSIONS | MEDIA_EXTENSIONS}
    note = meta.get("reason")

    if OK in statuses:
        status = OK
    elif census and imagey == set(census):
        status = IMAGE_NO_TEXT
        note = "archive holds only figure images or media"
    elif ".zip" in census and ".zip" not in wanted:
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
    return FileResult(relative_path, role, status, blocks, "zip", meta, label, caption, note)


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
    """
    import fitz
    import openpyxl
    return {"pymupdf": fitz.__version__, "openpyxl": openpyxl.__version__,
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


def _choose_main_text(article_dir: Path, record: dict, limits: Limits,
                      info: dict, labels: Dict[str, dict],
                      overrides=None) -> FileResult:
    """JATS if it is there and substantial, else the PDF, else the landing page."""
    forced = overrides.main_text_source() if overrides is not None else None
    xml_entry = record.get("fulltext_xml") or {}
    xml_path = xml_entry.get("path")
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
    shared_labels = _shared_manifest_labels(record)
    shared_jats = _shared_jats_labels(record, labels)
    for entry in record.get("supplementary") or []:
        path = entry.get("path")
        if not path:
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
    main_usable = main_result.status == OK and main_result.chars > 0
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
    if entries_without_path:
        caveats.append(MANIFEST_ENTRY_WITHOUT_PATH)

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
