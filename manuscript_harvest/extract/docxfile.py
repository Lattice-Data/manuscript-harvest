"""Word documents -> blocks, using the standard library only.

A `.docx` is a zip holding `word/document.xml`, and the two things worth pulling
out of it -- paragraph text and table cells -- need `w:p` and `w:tbl`, nothing
more. That is a small enough job that `python-docx` would be a dependency added
for 21 files, so it is not one.

These 21 files matter more than their count suggests: in this corpus they are
`suppmatmeth.docx` (supplementary methods) and `supplement-fS1-S7.docx` (figure
legends) -- exactly the places where a library kit or an animal age is written
down and the main text says only "see Supplementary Methods".

Field codes (`w:instrText`) and tracked deletions (`w:delText`) are skipped: the
first is a formula such as a cross-reference, the second is text the authors
removed, and quoting either as evidence would be wrong.
"""

import io
import re
import xml.etree.ElementTree as ET
import zipfile
from typing import List, Tuple

from . import sections as sections_mod
from . import tables
from .blocks import HEADING, PARAGRAPH, TABLE, Block
from .limits import Limits

OK = "ok"
NO_TEXT = "no_text"
UNREADABLE = "unreadable"

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
DOCUMENT_PART = "word/document.xml"

_SKIP_TEXT_TAGS = {W + "instrText", W + "delText"}


def _paragraph_text(element) -> str:
    parts: List[str] = []
    for node in element.iter():
        tag = node.tag
        if tag in _SKIP_TEXT_TAGS:
            continue
        if tag == W + "t" and node.text:
            parts.append(node.text)
        elif tag == W + "tab":
            parts.append(" ")
        elif tag in (W + "br", W + "cr"):
            parts.append(" ")
    return re.sub(r"\s+", " ", "".join(parts).replace("\xa0", " ")).strip()


def _style(element) -> str:
    properties = element.find(W + "pPr")
    if properties is None:
        return ""
    style = properties.find(W + "pStyle")
    return (style.get(W + "val") or "") if style is not None else ""


def _is_heading_style(style: str) -> bool:
    lowered = style.lower()
    return lowered.startswith("heading") or lowered in {"title", "subtitle"} or \
        bool(re.match(r"^h[1-6]$", lowered))


def _table_rows(element) -> List[List[str]]:
    rows: List[List[str]] = []
    for row in element.findall(W + "tr"):
        cells: List[str] = []
        for cell in row.findall(W + "tc"):
            texts = [_paragraph_text(p) for p in cell.findall(W + "p")]
            cells.append(" ".join(t for t in texts if t))
        if cells:
            rows.append(cells)
    return rows


def blocks_from_docx(
    data: bytes, source_file: str, limits: Limits, overrides=None
) -> Tuple[List[Block], str, dict]:
    """Parse one `.docx`. Returns `(blocks, status, meta)`."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = set(archive.namelist())
            if DOCUMENT_PART not in names:
                return [], UNREADABLE, {"reason": f"no {DOCUMENT_PART} in the archive"}
            document_xml = archive.read(DOCUMENT_PART)
    except (zipfile.BadZipFile, OSError, KeyError) as e:
        return [], UNREADABLE, {"reason": f"{type(e).__name__}: {e}"}

    try:
        root = ET.fromstring(document_xml)
    except ET.ParseError as e:
        return [], UNREADABLE, {"reason": f"XML parse error: {e}"}

    body = root.find(W + "body")
    if body is None:
        return [], UNREADABLE, {"reason": "no w:body"}

    blocks: List[Block] = []
    meta: dict = {"tables": 0, "paragraphs": 0}
    tracker = sections_mod.SectionTracker(limits=limits)
    paragraph_index = 0
    table_index = 0

    for element in body:
        if len(blocks) >= limits.max_blocks_per_file:
            meta["blocks_capped"] = True
            break

        if element.tag == W + "p":
            paragraph_index += 1
            text = _paragraph_text(element)
            if not text:
                continue
            meta["paragraphs"] += 1
            locator = f"para {paragraph_index}"
            named = sections_mod.normalize(text)
            heading = _is_heading_style(_style(element)) or bool(named)
            if not heading and len(text) < limits.min_paragraph_chars:
                continue
            # `carry` accounts for the text it labels, so it is called once per
            # block that is actually kept.
            section = tracker.heading(named) if named else tracker.carry(text)
            blocks.append(Block(kind=HEADING if heading else PARAGRAPH, text=text,
                                source_file=source_file, origin="docx",
                                locator=locator, section=section))

        elif element.tag == W + "tbl":
            table_index += 1
            if meta["tables"] >= limits.max_tables_per_file:
                continue
            rows = _table_rows(element)
            if not rows:
                continue
            # The nearest preceding heading or paragraph is usually the caption.
            caption = next((b.text for b in reversed(blocks)
                            if b.kind in {HEADING, PARAGRAPH} and len(b.text) < 400), None)
            locator = f"table {table_index}"
            forced = {}
            if overrides is not None:
                answer = overrides.header_for(source_file, locator)
                if answer is not None:
                    row = (answer.get("override") or {}).get("header_row")
                    forced = {"forced_header_row": row,
                              "forced_headerless": row is None,
                              "review_note": overrides.note_for(answer)}
            card = tables.build_card(
                rows, source_file=source_file, locator=locator,
                limits=limits, title=f"Table {table_index}", caption=caption,
                data_ref={"file": source_file, "table_index": table_index}, **forced,
            )
            if card is None:
                continue
            meta["tables"] += 1
            # `current` rather than `carry`: a rendered card is thousands of
            # characters of profile, not prose the section budget should be spent
            # on. It is already bounded by whatever the prose around it claimed.
            blocks.append(Block(kind=TABLE, text=tables.render(card, limits),
                                source_file=source_file, origin="docx",
                                locator=f"table {table_index}", section=tracker.current,
                                table=card.to_dict()))

    meta["sections"] = tracker.seen
    if tracker.abandoned:
        meta["sections_abandoned"] = tracker.abandoned
    if tracker.withheld:
        meta["low_value_blocks_withheld"] = tracker.withheld
    if tracker.reopens_refused:
        meta["reopens_refused"] = tracker.reopens_refused
    if tracker.reason():
        meta["reason"] = tracker.reason()
    if not blocks:
        return [], NO_TEXT, meta
    return blocks, OK, meta
