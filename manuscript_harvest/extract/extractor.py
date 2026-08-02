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
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..fetch import store
from . import __version__, archive, docxfile, htmlfile, jats, pdf, spreadsheet
from .blocks import (
    MAIN_TEXT,
    SUPPLEMENT,
    TABLE,
    Block,
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
                    "low_value_blocks_withheld"):
            if key in self.meta:
                record[key] = self.meta[key]
        return record


def _plain_text_blocks(data: bytes, source_file: str, limits: Limits,
                       role: str) -> Tuple[List[Block], str, dict]:
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
            cards, status, meta = spreadsheet.cards_from_csv(data, source_file, limits)
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


def _table_blocks(cards, source_file: str, origin: str, role: str,
                  limits: Limits) -> List[Block]:
    return [Block(kind=TABLE, text=render_card(card, limits), source_file=source_file,
                  origin=origin, locator=card.locator, role=role, label=card.title,
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
            # A block read out of an archive must say so, or its provenance reads
            # as a top-level supplement that does not exist on disk.
            if origin_prefix and not block.origin.startswith(origin_prefix):
                block.origin = origin_prefix + block.origin
        if sniffed:
            note = f"name carries no usable extension; read as {sniffed}" + \
                   (f"; {note}" if note else "")
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
            cards, status, meta = spreadsheet.cards_from_xlsx(data, relative_path, limits)
            return result(status, _table_blocks(cards, relative_path, "xlsx", role, limits),
                          "xlsx", meta, note=meta.get("reason"))
        if extension in LEGACY_SPREADSHEET_EXTENSIONS:
            cards, status, meta = spreadsheet.cards_from_xls(data, relative_path, limits)
            return result(status, _table_blocks(cards, relative_path, "xls", role, limits),
                          "xls", meta, note=meta.get("reason"))
        if extension in DELIMITED_EXTENSIONS:
            cards, status, meta = spreadsheet.cards_from_csv(data, relative_path, limits)
            return result(status, _table_blocks(cards, relative_path, "csv", role, limits),
                          "csv", meta, note=meta.get("reason"))
        if extension in PLAIN_TEXT_EXTENSIONS:
            blocks, status, meta = _plain_text_blocks(data, relative_path, limits, role)
            return result(status, blocks, "txt", meta)
        if extension == ".pdf":
            blocks, status, meta = pdf.blocks_from_pdf(data, relative_path, limits)
            note = meta.get("reason")
            if status == SCANNED:
                note = "parses as a PDF but has almost no extractable text: scanned images"
            return result(status, blocks, "pdf", meta, note=note)
        if extension == ".docx":
            blocks, status, meta = docxfile.blocks_from_docx(data, relative_path, limits)
            return result(status, blocks, "docx", meta, note=meta.get("reason"))
        if extension in XML_EXTENSIONS:
            blocks, status, meta = jats.blocks_from_jats(data, relative_path, limits)
            return result(status, blocks, "jats", meta, note=meta.get("reason"))
        if extension in HTML_EXTENSIONS:
            blocks, status, meta = htmlfile.blocks_from_html(data, relative_path, limits)
            return result(status, blocks, "html", meta, note=meta.get("reason"))
        if extension == ".zip":
            return _extract_zip(data, relative_path, limits, role, label, caption, depth)

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
                 label: Optional[str], caption: Optional[str], depth: int) -> FileResult:
    wanted = set(TEXT_BEARING_EXTENSIONS)
    if depth + 1 < limits.max_archive_depth:
        wanted.add(".zip")
    members, meta = archive.read_members(data, limits, wanted)

    blocks: List[Block] = []
    statuses: List[str] = []
    for name, member_bytes in members:
        inner = extract_bytes(
            member_bytes, f"{relative_path}!{name}", limits, role=role,
            label=label, origin_prefix="zip:", depth=depth + 1,
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
                 content_type: str = "") -> FileResult:
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
                         caption=caption, content_type=content_type)


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


def _main_text(article_dir: Path, record: dict, limits: Limits) -> Tuple[FileResult, Dict[str, dict], dict]:
    """Pick and extract the main text. Returns `(result, supplement_labels, info)`."""
    info: dict = {}
    labels: Dict[str, dict] = {}

    xml_entry = record.get("fulltext_xml") or {}
    xml_path = xml_entry.get("path")
    xml_result: Optional[FileResult] = None
    if xml_path and (article_dir / xml_path).exists():
        xml_result = extract_path(article_dir / xml_path, xml_path, limits, role=MAIN_TEXT)
        labels = xml_result.meta.get("supplement_labels") or {}
        info["jats_chars"] = xml_result.chars

    pdf_entry = record.get("fulltext") or {}
    pdf_path = pdf_entry.get("path")
    pdf_available = bool(pdf_path and (article_dir / pdf_path).exists())
    info["pdf_available"] = pdf_available

    if xml_result is not None and xml_result.status == OK and \
            xml_result.chars >= limits.min_main_text_chars:
        info["source"] = "jats"
        if pdf_available:
            info["note"] = "JATS XML preferred over the PDF; the PDF was not parsed"
        return xml_result, labels, info

    if xml_result is not None and xml_result.status == OK:
        info["note"] = (f"JATS XML yielded only {xml_result.chars} characters "
                        f"(under {limits.min_main_text_chars}); fell back to the PDF")

    if pdf_available:
        pdf_result = extract_path(article_dir / pdf_path, pdf_path, limits, role=MAIN_TEXT)
        if pdf_result.status == OK or xml_result is None:
            info["source"] = "pdf"
            return pdf_result, labels, info
        # A scanned PDF beside thin XML: keep whichever has more text.
        if xml_result.chars >= pdf_result.chars:
            info["source"] = "jats"
            return xml_result, labels, info
        info["source"] = "pdf"
        return pdf_result, labels, info

    if xml_result is not None:
        info["source"] = "jats"
        return xml_result, labels, info

    landing = (record.get("landing_html") or {}).get("path") or store.LANDING_HTML
    if (article_dir / landing).exists():
        result = extract_path(article_dir / landing, landing, limits, role=MAIN_TEXT)
        info["source"] = "landing_html"
        info["landing_page_only"] = True
        info["note"] = ("no PDF and no XML: main text is the saved publisher landing "
                        "page, which is metadata and abstract at best, not the article")
        return result, labels, info

    info["source"] = None
    return FileResult("", MAIN_TEXT, MISSING,
                      note="no PDF, no XML, and no saved landing page"), labels, info


def extract_article(article_dir, limits: Optional[Limits] = None, force: bool = False,
                    write_markdown: bool = True) -> dict:
    """Extract one corpus article. Returns the extraction record it wrote."""
    limits = limits or Limits()
    article_dir = Path(article_dir)
    manifest_path = article_dir / store.MANIFEST_NAME
    record = store.read_manifest(article_dir)
    if record is None:
        return {"slug": article_dir.name, "status": "no_manifest",
                "problems": [f"no readable {store.MANIFEST_NAME} in {article_dir}"]}

    manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    output_dir = article_dir / EXTRACT_DIR
    existing_path = output_dir / EXTRACTION_NAME
    if not force and existing_path.exists():
        try:
            existing = json.loads(existing_path.read_text(encoding="utf-8"))
        except ValueError:
            existing = None
        if existing and existing.get("source_manifest_sha256") == manifest_sha \
                and existing.get("extractor_version") == __version__ \
                and (output_dir / BLOCKS_NAME).exists():
            existing["cached"] = True
            return existing

    main_result, labels, main_info = _main_text(article_dir, record, limits)
    results: List[FileResult] = [main_result]

    for entry in record.get("supplementary") or []:
        path = entry.get("path")
        if not path:
            continue
        matched = next((labels[key] for key in _supplement_key(entry) if key in labels), None)
        label = (matched or {}).get("label") or entry.get("label")
        caption = (matched or {}).get("caption")
        results.append(extract_path(article_dir / path, path, limits,
                                    role=SUPPLEMENT, label=label, caption=caption,
                                    content_type=entry.get("content_type") or ""))

    all_blocks: List[Block] = []
    for result in results:
        all_blocks.extend(result.blocks)
    number_blocks(all_blocks)

    output_dir.mkdir(parents=True, exist_ok=True)
    write_blocks(output_dir / BLOCKS_NAME, all_blocks)
    if write_markdown:
        (output_dir / ARTICLE_MD).write_text(render_markdown(all_blocks), encoding="utf-8")

    by_status: Dict[str, int] = {}
    for result in results[1:]:
        by_status[result.status] = by_status.get(result.status, 0) + 1

    text_bearing_failures = [
        r.path for r in results[1:]
        if r.status not in _PRODUCTIVE | _BENIGN
    ]
    main_usable = main_result.status == OK and main_result.chars > 0
    main_info["usable"] = main_usable
    supplement_text = any(r.blocks for r in results[1:])

    if main_usable and not text_bearing_failures and not main_info.get("landing_page_only"):
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
        "fetch_status": record.get("status"),
        "limits": limits.to_dict(),
        "main_text": {**main_result.to_dict(), **main_info},
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
        "status": status,
        "blocks_path": f"{EXTRACT_DIR}/{BLOCKS_NAME}",
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
    return (
        f"{extraction.get('status', '?'):8s} main={str(main.get('source')):13s} "
        f"blocks={totals.get('blocks', 0):<5d} tables={totals.get('tables', 0):<4d} "
        f"chars={totals.get('chars', 0):<8d} suppl[{supplements}]"
    )
