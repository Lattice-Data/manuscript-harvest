"""JATS XML -> blocks. The best main-text source available, and it is already here.

40 of the 63 articles in this corpus carry Europe PMC's `fulltext.nxml` right
next to the PDF. Preferring it is the single largest accuracy win in this stage:

- sections are declared, not guessed from a line that happens to sit alone;
- tables are real tables, so a JATS table becomes a proper card;
- figure and supplement captions are labelled, which is how an opaque
  `41467_2023_40505_MOESM3_ESM.pdf` gets to be called "Supplementary Data 1".

That last one is why `supplement_labels` exists. The fetch manifest records each
supplement's `original_name`; JATS records the publisher's label for the same
name. Joining them is free and it is the difference between a model seeing a
filename and seeing "Supplementary Table 3: per-cell metadata".

Citation markers are dropped. Left in, `<xref ref-type="bibr">` turns "as shown
previously" into "as shown previously12,13", which is noise in a quote and worse
in an evidence check. References themselves are dropped for the same reason:
they are other people's findings, and a model asked for perturbations will
happily take one from a reference title.
"""

import re
import xml.etree.ElementTree as ET
from html.entities import name2codepoint
from typing import Dict, List, Optional, Tuple

from . import sections as sections_mod
from . import tables
from .blocks import CAPTION, HEADING, METADATA, PARAGRAPH, TABLE, Block
from .limits import Limits

OK = "ok"
NO_TEXT = "no_text"
UNREADABLE = "unreadable"

XLINK_HREF = "{http://www.w3.org/1999/xlink}href"

_PREDEFINED = {"amp", "lt", "gt", "quot", "apos"}
_DOCTYPE_RX = re.compile(rb"<!DOCTYPE[^>\[]*(\[[^\]]*\])?[^>]*>", re.DOTALL | re.IGNORECASE)
_ENTITY_RX = re.compile(r"&([A-Za-z][A-Za-z0-9._-]*);")


def _tag(element) -> str:
    tag = element.tag
    if isinstance(tag, str) and tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag if isinstance(tag, str) else ""


def _prepare(data: bytes) -> str:
    """Make publisher XML parseable by the stdlib parser.

    ElementTree resolves no external DTD, so every named entity a JATS DOCTYPE
    would have defined (`&alpha;`, `&thinsp;`) is an "undefined entity" fatal
    error. Dropping the DOCTYPE and mapping named entities to characters keeps a
    real file from being reported as unreadable over a Greek letter.
    """
    body = _DOCTYPE_RX.sub(b"", data, count=1)
    text = body.decode("utf-8", errors="replace")

    def replace(match: "re.Match") -> str:
        name = match.group(1)
        if name in _PREDEFINED:
            return match.group(0)
        codepoint = name2codepoint.get(name)
        return chr(codepoint) if codepoint else ""

    return _ENTITY_RX.sub(replace, text)


def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def _inline_text(element) -> str:
    """All text under `element`, minus citation markers."""
    parts: List[str] = []

    def walk(node, is_root: bool) -> None:
        if _tag(node) == "xref" and (node.get("ref-type") or "") == "bibr":
            if node.tail:
                parts.append(node.tail)
            return
        if node.text:
            parts.append(node.text)
        for child in node:
            walk(child, False)
        if not is_root and node.tail:
            parts.append(node.tail)

    walk(element, True)
    return _normalize_ws("".join(parts))


_WRAPPERS = {"media", "graphic", "alternatives", "inline-supplementary-material"}


def _label_and_caption(element) -> Tuple[Optional[str], Optional[str]]:
    """The `<label>` and `<caption>` of a float, looking one wrapper deep.

    Springer nests the real content of a `<supplementary-material>` inside a
    `<media>` element -- caption included -- so reading only direct children
    found a label for none of the 40 XML files in this corpus.
    """
    label = None
    caption_parts: List[str] = []

    def scan(parent) -> None:
        nonlocal label
        for child in parent:
            name = _tag(child)
            if name == "label" and label is None:
                label = _inline_text(child)
            elif name == "caption":
                texts = [_inline_text(g) for g in child]
                texts = [t for t in texts if t] or [_inline_text(child)]
                caption_parts.extend(t for t in texts if t)

    scan(element)
    if label is None and not caption_parts:
        for child in element:
            if _tag(child) in _WRAPPERS:
                scan(child)
    caption = " ".join(caption_parts) or None
    return label, caption


def _href(element) -> str:
    """The file this float points at, from the element or its media wrapper."""
    direct = element.get(XLINK_HREF)
    if direct:
        return direct
    for child in element.iter():
        if _tag(child) in _WRAPPERS | {"self-uri"}:
            value = child.get(XLINK_HREF)
            if value:
                return value
    return ""


def _table_rows(table_element) -> List[List[str]]:
    """Flatten a JATS `<table>` into rows of strings.

    `colspan` is not expanded. A spanning header cell therefore lands in one
    column rather than being repeated, which shifts nothing because the card
    profiles columns by position within each row as read.
    """
    rows: List[List[str]] = []
    for row in table_element.iter():
        if _tag(row) != "tr":
            continue
        cells = [_inline_text(cell) for cell in row if _tag(cell) in {"th", "td"}]
        if cells:
            rows.append(cells)
    return rows


class _Walker:
    def __init__(self, source_file: str, limits: Limits):
        self.source_file = source_file
        self.limits = limits
        self.blocks: List[Block] = []
        self.supplement_labels: Dict[str, dict] = {}
        self.tables_seen = 0
        self.section_names: List[str] = []

    def add(self, kind: str, text: str, section: Optional[str], locator: str,
            label: Optional[str] = None, table: Optional[dict] = None) -> None:
        if not text or len(self.blocks) >= self.limits.max_blocks_per_file:
            return
        self.blocks.append(Block(kind=kind, text=text, source_file=self.source_file,
                                 origin="jats", locator=locator, section=section,
                                 label=label, table=table))

    # -- containers
    def walk_section(self, element, inherited: Optional[str], path: str) -> None:
        title_element = next((c for c in element if _tag(c) == "title"), None)
        title = _inline_text(title_element) if title_element is not None else None
        section = sections_mod.normalize(title, element.get("sec-type")) or inherited
        if section in sections_mod.LOW_VALUE:
            return
        if section and section not in self.section_names:
            self.section_names.append(section)
        if title:
            self.add(HEADING, title, section, path)
        self.walk_children(element, section, path, skip_title=True)

    def walk_children(self, element, section: Optional[str], path: str,
                      skip_title: bool = False) -> None:
        index = 0
        for child in element:
            name = _tag(child)
            index += 1
            child_path = f"{path}/{name}[{index}]" if path else f"{name}[{index}]"
            if name == "title" and skip_title:
                continue
            if name == "sec":
                self.walk_section(child, section, child_path)
            elif name == "p":
                text = _inline_text(child)
                if len(text) >= self.limits.min_paragraph_chars:
                    self.add(PARAGRAPH, text, section, child_path)
                # A <p> can wrap a table or figure; do not lose it.
                for nested in child:
                    if _tag(nested) in {"table-wrap", "fig", "supplementary-material"}:
                        self.walk_children_one(nested, section, child_path)
            elif name in {"table-wrap", "fig", "supplementary-material", "boxed-text",
                          "list", "disp-quote", "ack", "app", "glossary", "abstract",
                          "sec-meta", "notes", "fn-group", "def-list"}:
                self.walk_children_one(child, section, child_path)
            elif name in {"ref-list", "back-ref-list"}:
                continue

    def walk_children_one(self, child, section: Optional[str], path: str) -> None:
        name = _tag(child)
        if name == "table-wrap":
            self.add_table(child, section, path)
        elif name == "fig":
            label, caption = _label_and_caption(child)
            text = " ".join(part for part in (label, caption) if part)
            self.add(CAPTION, text, section or sections_mod.FIGURE_LEGENDS, path, label=label)
        elif name == "supplementary-material":
            self.add_supplement(child, section, path)
        elif name in {"boxed-text", "list", "disp-quote", "ack", "app", "glossary",
                      "abstract", "notes", "fn-group", "def-list", "sec-meta"}:
            inner_section = section
            if name == "abstract":
                inner_section = sections_mod.ABSTRACT
            elif name in {"ack", "fn-group"}:
                inner_section = sections_mod.BACK_MATTER
            self.walk_children(child, inner_section, path)
            # Containers whose children are not <p>/<sec> still hold text.
            if not any(_tag(c) in {"p", "sec", "title", "list-item"} for c in child):
                text = _inline_text(child)
                if len(text) >= self.limits.min_paragraph_chars:
                    self.add(PARAGRAPH, text, inner_section, path)
            for item in child:
                if _tag(item) == "list-item":
                    text = _inline_text(item)
                    if text:
                        self.add(PARAGRAPH, text, inner_section, path)

    def add_table(self, element, section: Optional[str], path: str) -> None:
        label, caption = _label_and_caption(element)
        table_element = next((c for c in element if _tag(c) == "table"), None)
        rows = _table_rows(table_element) if table_element is not None else []
        if not rows:
            # Some publishers ship the table as an image; the caption is all there is.
            text = " ".join(part for part in (label, caption) if part)
            self.add(CAPTION, text, section, path, label=label)
            return
        if self.tables_seen >= self.limits.max_tables_per_file:
            return
        self.tables_seen += 1
        card = tables.build_card(
            rows, source_file=self.source_file, locator=path, limits=self.limits,
            title=label or "Table", caption=caption,
            data_ref={"file": self.source_file, "xpath": path},
        )
        if card is None:
            return
        self.add(TABLE, tables.render(card, self.limits), section, path,
                 label=label, table=card.to_dict())

    def add_supplement(self, element, section: Optional[str], path: str) -> None:
        label, caption = _label_and_caption(element)
        name = _href(element).rsplit("/", 1)[-1]
        if name:
            self.supplement_labels[name] = {"label": label, "caption": caption}
        text = " ".join(part for part in (label, caption) if part)
        if not text and name:
            text = f"Supplementary file {name}"
        self.add(CAPTION, text, section or sections_mod.SUPPLEMENTARY, path, label=label)


def _front_metadata(article, walker: "_Walker") -> dict:
    meta: dict = {}
    front = next((c for c in article if _tag(c) == "front"), None)
    if front is None:
        return meta

    def find_text(parent, *names) -> Optional[str]:
        for element in parent.iter():
            if _tag(element) in names:
                text = _inline_text(element)
                if text:
                    return text
        return None

    journal_meta = next((c for c in front if _tag(c) == "journal-meta"), None)
    article_meta = next((c for c in front if _tag(c) == "article-meta"), None)
    if journal_meta is not None:
        meta["journal"] = find_text(journal_meta, "journal-title")
    if article_meta is None:
        return meta

    meta["title"] = find_text(article_meta, "article-title")
    for element in article_meta.iter():
        if _tag(element) == "article-id" and element.get("pub-id-type") == "doi":
            meta["doi"] = _inline_text(element)
        elif _tag(element) == "year" and "year" not in meta:
            meta["year"] = _inline_text(element)

    keywords = [_inline_text(k) for k in article_meta.iter() if _tag(k) == "kwd"]
    subjects = [_inline_text(s) for s in article_meta.iter() if _tag(s) == "subject"]
    meta["keywords"] = [k for k in keywords if k]
    meta["subjects"] = [s for s in subjects if s]

    lines = [f"{key}: {value}" for key, value in (
        ("Title", meta.get("title")), ("Journal", meta.get("journal")),
        ("Year", meta.get("year")), ("DOI", meta.get("doi")),
        ("Keywords", "; ".join(meta["keywords"]) if meta.get("keywords") else None),
        ("Subjects", "; ".join(meta["subjects"]) if meta.get("subjects") else None),
    ) if value]
    if lines:
        walker.add(METADATA, "\n".join(lines), None, "front/article-meta")

    for element in article_meta:
        if _tag(element) == "abstract":
            walker.walk_children_one(element, sections_mod.ABSTRACT, "front/abstract")
    return meta


def blocks_from_jats(
    data: bytes, source_file: str, limits: Limits
) -> Tuple[List[Block], str, dict]:
    """Parse one JATS/NXML file. Returns `(blocks, status, meta)`."""
    try:
        root = ET.fromstring(_prepare(data))
    except ET.ParseError as e:
        return [], UNREADABLE, {"reason": f"XML parse error: {e}"}

    article = root if _tag(root) == "article" else next(
        (e for e in root.iter() if _tag(e) == "article"), None)
    if article is None:
        return [], UNREADABLE, {"reason": "no <article> element"}

    walker = _Walker(source_file, limits)
    meta = _front_metadata(article, walker)

    for child in article:
        name = _tag(child)
        if name == "body":
            walker.walk_children(child, None, "body")
        elif name == "back":
            walker.walk_children(child, sections_mod.BACK_MATTER, "back")
        elif name == "floats-group":
            walker.walk_children(child, None, "floats-group")

    meta["sections"] = walker.section_names
    meta["supplement_labels"] = walker.supplement_labels
    meta["tables"] = walker.tables_seen
    if not walker.blocks:
        return [], NO_TEXT, meta
    return walker.blocks, OK, meta


def supplement_labels(data: bytes) -> Dict[str, dict]:
    """Just the `filename -> {label, caption}` map, for joining to a manifest."""
    try:
        root = ET.fromstring(_prepare(data))
    except ET.ParseError:
        return {}
    found: Dict[str, dict] = {}
    for element in root.iter():
        if _tag(element) != "supplementary-material":
            continue
        name = _href(element).rsplit("/", 1)[-1]
        if not name:
            continue
        label, caption = _label_and_caption(element)
        found[name] = {"label": label, "caption": caption}
    return found
