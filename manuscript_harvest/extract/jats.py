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

Citation markers are dropped from prose. Left in, `<xref ref-type="bibr">` turns
"as shown previously" into "as shown previously12,13", which is noise in a quote
and worse in an evidence check. References themselves are dropped for the same
reason: they are other people's findings, and a model asked for perturbations
will happily take one from a reference title.

They are *not* dropped from a table cell. In prose a citation is noise; in a cell
it is the value. 10 of the 29 SOURCE cells in 10.1016/j.cell.2021.01.053's key
resources table -- the one table this pipeline exists to read -- were destroyed
by the same rule: `(Korsunsky et al., 2019)` became `()`.
"""

import re
import xml.etree.ElementTree as ET
from collections import Counter
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


#: Children that end a run of text. Without a boundary between them a `<td>`
#: holding three `<p>`s reads as one value.
_BLOCK_LEVEL = frozenset({"p", "list-item", "disp-quote", "sec", "title", "def",
                          "term", "tr"})

#: The boundary marker, carried through the whitespace collapse and swapped for
#: the caller's separator at the end. A plain "; " inserted during the walk
#: leaves `'; 0.798; ; 0.15;'` once the empty runs collapse; a sentinel does not.
#: XML 1.0 forbids this codepoint in content, so no publisher file carries one.
_SEP = "\x1f"
_SEP_RUN = re.compile(r"\s*\x1f+\s*")

#: What is left of `(<xref/>; <xref/>; <xref/>)` once the citations are gone.
#: The lookbehind is load-bearing: it keeps `susie_rss()` and `HarmonyMatrix()`
#: intact while removing `report ()`.
_CITATION_HUSK = re.compile(r"(?<=\s)\(([,;&\s–—-]*)\)")
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([,;.)])")
_DOUBLED_SEPARATOR = re.compile(r"([,;])(\s*[,;])+")
_SEPARATOR_BEFORE_STOP = re.compile(r"[,;]+(?=[.)])")


def _drop_citation_husk(match: "re.Match") -> str:
    inside = match.group(1)
    # `(-)` and `(–)` are real markers -- a negative gate, an absent value. An
    # empty pair, or one holding only the separators between grouped citations,
    # is not: it is what a dropped `<xref ref-type="bibr">` left behind.
    if inside.strip() and not any(c in inside for c in ",;&"):
        return match.group(0)
    return ""


def _tidy_citation_punctuation(text: str) -> str:
    """Remove the punctuation a dropped citation group left behind.

    Measured over the JATS blocks of 10.1016/j.cell.2021.01.053: 35 literal
    `()`, 12 more `(` followed by a separator. One block read
    `...severe symptoms (; ; ; ; , ). While recent studies...`. In
    10.1038/s41467-023-40505-5: `into LD blocks using LDetect,.`
    """
    text = _CITATION_HUSK.sub(_drop_citation_husk, text)
    text = _SPACE_BEFORE_PUNCT.sub(r"\1", text)
    text = _DOUBLED_SEPARATOR.sub(r"\1", text)
    text = _SEPARATOR_BEFORE_STOP.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def _inline_text(element, block_sep: str = " ", keep_citations: bool = False) -> str:
    """All text under `element`, minus citation markers.

    `block_sep` is what separates block-level children. It is `"; "` for a table
    cell: 24 of the 144 `td`/`th` in 10.1038/s41467-023-40505-5 hold more than
    one block child, and Table 1's SNP PIP column read `0.7980.15` for two
    values, which also flipped the column's dtype from number to mixed and cost
    it its min/max/median.

    `keep_citations` is True for a table cell. In prose a citation marker is
    noise -- "as shown previously12,13" is worse in an evidence check than in a
    quote -- but in a cell it *is* the value: 10 of the 29 SOURCE cells in
    10.1016/j.cell.2021.01.053's key resources table were destroyed by dropping
    it, `(Korsunsky et al., 2019)` becoming `()`.
    """
    parts: List[str] = []

    def walk(node, is_root: bool) -> None:
        if not keep_citations and _tag(node) == "xref" \
                and (node.get("ref-type") or "") == "bibr":
            if node.tail:
                parts.append(node.tail)
            return
        if node.text:
            parts.append(node.text)
        for child in node:
            if _tag(child) in _BLOCK_LEVEL:
                parts.append(_SEP)
                walk(child, False)
                parts.append(_SEP)
            else:
                walk(child, False)
        if not is_root and node.tail:
            parts.append(node.tail)

    walk(element, True)
    text = _SEP_RUN.sub(_SEP, "".join(parts).replace("\xa0", " ")).strip(_SEP)
    text = re.sub(r"\s+", " ", text.replace(_SEP, block_sep)).strip()
    return text if keep_citations else _tidy_citation_punctuation(text)


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

    A cell's block-level children are joined with `"; "`. In prose the boundary
    between two paragraphs is a space; in a cell it is the boundary between two
    values, and 10.1038/s41467-023-40505-5 Table 1 read `0.7980.15` without it.
    """
    rows: List[List[str]] = []
    for row in table_element.iter():
        if _tag(row) != "tr":
            continue
        cells = [_inline_text(cell, block_sep="; ", keep_citations=True)
                 for cell in row if _tag(cell) in {"th", "td"}]
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
        self.title_stack: List[str] = []
        """The headings enclosing whatever is being walked, outermost first.

        `walk_section` has always known the full path and thrown it away, leaving
        `section` -- one canonical name out of eleven -- as the only structure a
        consumer could filter on. The tree is declared here, so recording it costs
        nothing and is not a guess.
        """

    def add(self, kind: str, text: str, section: Optional[str], locator: str,
            label: Optional[str] = None, table: Optional[dict] = None) -> None:
        if not text or len(self.blocks) >= self.limits.max_blocks_per_file:
            return
        self.blocks.append(Block(kind=kind, text=text, source_file=self.source_file,
                                 origin="jats", locator=locator, section=section,
                                 section_path=list(self.title_stack) or None,
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
            self.title_stack.append(title)
        try:
            if title:
                self.add(HEADING, title, section, path)
            self.walk_children(element, section, path, skip_title=True)
        finally:
            if title:
                self.title_stack.pop()

    def walk_children(self, element, section: Optional[str], path: str,
                      skip_title: bool = False) -> None:
        # `[n]` in XPath counts children *of that tag*, not all children. Counting
        # every child made 153 of the 168 body/back locators in
        # 10.1038/s41467-023-40505-5 point at a different element, and only 76 of
        # them resolve at all.
        seen: Counter = Counter()
        for child in element:
            name = _tag(child)
            seen[name] += 1
            child_path = (f"{path}/{name}[{seen[name]}]" if path
                          else f"{name}[{seen[name]}]")
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

    # Enumerated, not hard-coded: a Cell Press article carries a summary abstract
    # and a graphical one, and `front/abstract` named both.
    abstracts = 0
    for element in article_meta:
        if _tag(element) == "abstract":
            abstracts += 1
            walker.walk_children_one(element, sections_mod.ABSTRACT,
                                     f"front/article-meta/abstract[{abstracts}]")
    return meta


def blocks_from_jats(
    data: bytes, source_file: str, limits: Limits
) -> Tuple[List[Block], str, dict]:
    """Parse one JATS/NXML file. Returns `(blocks, status, meta)`.

    The walk is recursive and the depth of a publisher's `<sec>` tree is not
    something this module gets to choose, so `RecursionError` is caught here and
    reported as an unreadable file. `extractor.extract_bytes` has a generic guard
    behind this one; the point of catching here is that the reason names XML.
    """
    try:
        root = ET.fromstring(_prepare(data))
    except (ET.ParseError, ValueError) as e:
        return [], UNREADABLE, {"reason": f"XML parse error: {e}"}

    article = root if _tag(root) == "article" else next(
        (e for e in root.iter() if _tag(e) == "article"), None)
    if article is None:
        return [], UNREADABLE, {"reason": "no <article> element"}

    walker = _Walker(source_file, limits)
    try:
        meta = _front_metadata(article, walker)
        for child in article:
            name = _tag(child)
            if name == "body":
                walker.walk_children(child, None, "body")
            elif name == "back":
                walker.walk_children(child, sections_mod.BACK_MATTER, "back")
            elif name == "floats-group":
                walker.walk_children(child, None, "floats-group")
    except (ValueError, RecursionError) as e:
        return [], UNREADABLE, {"reason": f"{type(e).__name__}: {e}"}

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
