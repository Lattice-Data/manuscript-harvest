"""The unit everything downstream reads: one block of text plus where it came from.

A block is deliberately small -- a paragraph, a heading, a figure caption, one
table's summary card. The alternative, one concatenated text file per article,
loses the two things that matter for curation:

1. **Provenance.** Verifying that a quote is a verbatim substring of the text a
   model was given is not enough on its own: with a flat blob it cannot say
   *which* of thirty supplementary files the quote came from. A block carries
   `source_file` and `locator`, so a human can be pointed at "sheet 'Table S6'
   of supplementary/03_mmc7.xlsx" and check the call.
2. **Selection.** The questions vary -- organism, age, sex, disease, treatment,
   library kit -- and each one wants a different slice. Blocks can be filtered by
   section and kind before anything is sent to a model; a blob can only be sent
   whole.

Blocks are written as JSON Lines with sorted keys and no timestamps, so
extracting the same bytes twice produces a byte-identical file. That is what
makes an extraction safe to hash and cheap to diff after a parser change.
"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, List, Optional

BLOCKS_NAME = "blocks.jsonl"

# -- kinds -------------------------------------------------------------------
HEADING = "heading"
PARAGRAPH = "paragraph"
CAPTION = "caption"
TABLE = "table"
METADATA = "metadata"

# -- roles -------------------------------------------------------------------
MAIN_TEXT = "main_text"
SUPPLEMENT = "supplement"
NON_EVIDENCE = "non_evidence"
"""A file a human marked as not article evidence: a peer-review file, a reporting
summary, a description-of-files stub. Its text is kept and readable, but
`cmd_show --role` and every downstream filter now see three values, not two."""

ROLES = frozenset({MAIN_TEXT, SUPPLEMENT, NON_EVIDENCE})


@dataclass
class Block:
    """One addressable piece of an article.

    `text` is what a model reads. `table` is the same table in structured form,
    for code that wants to query columns rather than read prose; it is set only
    when `kind == TABLE`.
    """

    kind: str
    text: str
    source_file: str
    origin: str
    """How the text was produced: jats, pdf, xlsx, xls, csv, docx, html, or
    `zip:<member>` for something read out of an archive."""
    role: str = MAIN_TEXT
    locator: str = ""
    """Where inside the file: `p.7`, `sheet 'Table S6'`, `para 42`,
    `body/sec[4]/p[2]`. A JATS locator is real XPath: `[n]` counts children of
    that tag, which it did not until 153 of one article's 168 body locators were
    found to point at a different element."""
    locator_ref: Optional[dict] = None
    """Machine-readable provenance where the locator alone is too coarse. For a
    PDF: `{"page": n, "bbox": [x0, y0, x1, y1], "block_no": n}`. PyMuPDF hands
    over the rectangle and this module kept two of the seven fields, so a PDF
    block was locatable only to a page -- which is not enough to point a human at
    a paragraph on it."""
    section: Optional[str] = None
    section_path: Optional[List[str]] = None
    """The heading path down to this block, verbatim, e.g.
    `["Methods", "Nuclei isolation"]`. Set only where the tree is real: JATS
    declares it and `walk_section` already knows it. A PDF's tree is a guess, and
    a guessed path is exactly what this package refuses to produce."""
    label: Optional[str] = None
    """The publisher's name for this item, e.g. "Supplementary Table 3".

    Not the fetch transport's name for it. `label: "Download"` was on 1,989 of
    the 2,076 blocks of 10.1126/science.aat5031 and
    `label: "Europe PMC supplementary archive"` on 347 of 536 in
    10.1038/s41467-023-40505-5, both straight from the manifest entry."""
    caption: Optional[str] = None
    """The publisher's description of the file this block came from, e.g.
    "Table S7. Cytokine analysis, related to Figure 6". `extract_bytes` accepted
    one and passed it only to the file-level record, so it reached
    `extraction.json` and none of that file's blocks."""
    table: Optional[dict] = None
    index: int = 0
    block_id: str = ""
    """An identity that survives a parser change. Assigned by `number_blocks`."""

    def to_dict(self) -> dict:
        record = {
            "index": self.index,
            "block_id": self.block_id,
            "kind": self.kind,
            "role": self.role,
            "origin": self.origin,
            "source_file": self.source_file,
            "locator": self.locator,
            "section": self.section,
            "label": self.label,
            "chars": len(self.text),
            "text_sha256": hashlib.sha256(self.text.encode("utf-8")).hexdigest(),
            "text": self.text,
        }
        if self.section_path:
            record["section_path"] = list(self.section_path)
        if self.caption:
            record["caption"] = self.caption
        if self.locator_ref:
            record["locator_ref"] = self.locator_ref
        if self.table is not None:
            record["table"] = self.table
        return record


def number_blocks(blocks: List[Block]) -> List[Block]:
    """Assign contiguous indices, and a `block_id` that survives a parser change.

    `index` is positional: insert one block and every downstream reference moves.
    That is the prerequisite for a review layer -- a human confirmation recorded
    against index 148 must not silently become a statement about a different
    paragraph.

    The identity is content plus provenance plus an occurrence ordinal. The
    ordinal is not optional: `(source_file, locator, text_sha256)` alone collides
    416 times in the 2,076 blocks of 10.1126/science.aat5031 (`p.79` /
    "Developing nephron" occurs 22 times), 128 of 1,050 in aba4163 and 11 of 536
    in the Nature paper, because a PDF locator is only a page.

    **`section` is deliberately excluded.** It is the most-revised heuristic in
    this package, and a confirmed fact about donor age has to survive a relabel.
    Measured across the real `6a54ff7^ -> HEAD` parser change: including it would
    have changed 21 of 1,717 ids in aat5031 and 2 of 882 in aba4163.

    Ids are assigned here because this is the one place indices are assigned, so
    no caller can forget one and not the other.
    """
    ordinals: dict = {}
    for offset, block in enumerate(blocks):
        block.index = offset
        key = "\x00".join([
            block.role, block.origin, block.source_file, block.locator, block.kind,
            hashlib.sha256(block.text.encode("utf-8")).hexdigest(),
        ])
        ordinal = ordinals.get(key, 0)
        ordinals[key] = ordinal + 1
        block.block_id = hashlib.sha256(
            f"{key}\x00{ordinal}".encode("utf-8")).hexdigest()[:16]
    return blocks


def write_blocks(path, blocks: List[Block]) -> dict:
    """Write the block list and describe what landed: `{path, sha256, lines}`.

    The sha and the line count go into `extraction.json` so the cache can check
    the file it is about to trust. Emptying a real 475 KB `blocks.jsonl` and
    re-running used to give `cached: True, status: complete, totals.blocks: 532`
    over zero lines on disk.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    lines = 0
    with target.open("wb") as handle:
        for block in blocks:
            # allow_nan=False: `Infinity` and `NaN` are Python's JSON dialect,
            # not JSON. A card built from a column holding Inf wrote
            # `"max": Infinity` into a line that serde_json, Go's encoding/json,
            # PostgreSQL jsonb and DuckDB all reject. Raising here means a future
            # path that produces one fails at write time instead of shipping an
            # artifact that is not what its extension says it is.
            line = (json.dumps(block.to_dict(), ensure_ascii=False, sort_keys=True,
                               allow_nan=False) + "\n").encode("utf-8")
            handle.write(line)
            digest.update(line)
            lines += 1
    return {"path": target, "sha256": digest.hexdigest(), "lines": lines}


def blocks_sha256(path) -> Optional[str]:
    """The sha of a `blocks.jsonl` on disk, or `None` when it is not there."""
    target = Path(path)
    if not target.exists():
        return None
    return hashlib.sha256(target.read_bytes()).hexdigest()


def read_blocks(path) -> Iterator[dict]:
    """Stream blocks back. Malformed lines are skipped rather than fatal, so a
    truncated file still yields the blocks that were written completely."""
    target = Path(path)
    if not target.exists():
        return
    with target.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue


def render_markdown(blocks: List[Block]) -> str:
    """A human-readable rendering of a block list, for reading and for pasting.

    Headings become markdown headings; table cards are fenced so their aligned
    lines survive. This is a convenience view -- `blocks.jsonl` is the artifact
    the pipeline consumes.
    """
    parts: List[str] = []
    current_file = None
    for block in blocks:
        if block.source_file != current_file:
            current_file = block.source_file
            parts.append(f"\n---\n\n## FILE: {current_file}\n")
        if block.kind == HEADING:
            parts.append(f"\n### {block.text}\n")
        elif block.kind == TABLE:
            parts.append(f"\n```\n{block.text}\n```\n")
        elif block.kind == CAPTION:
            parts.append(f"\n*{block.text}*\n")
        else:
            parts.append(block.text + "\n")
    return "\n".join(parts).strip() + "\n"
