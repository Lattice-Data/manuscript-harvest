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

KINDS = (HEADING, PARAGRAPH, CAPTION, TABLE, METADATA)

# -- roles -------------------------------------------------------------------
MAIN_TEXT = "main_text"
SUPPLEMENT = "supplement"


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
    """Where inside the file: `p.7`, `sheet 'Table S6'`, `para 42`, `table-wrap 3`."""
    section: Optional[str] = None
    label: Optional[str] = None
    """The publisher's name for this item, e.g. "Supplementary Table 3"."""
    table: Optional[dict] = None
    index: int = 0

    def to_dict(self) -> dict:
        record = {
            "index": self.index,
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
        if self.table is not None:
            record["table"] = self.table
        return record


def number_blocks(blocks: List[Block], start: int = 0) -> List[Block]:
    """Assign stable, contiguous indices in document order."""
    for offset, block in enumerate(blocks):
        block.index = start + offset
    return blocks


def write_blocks(path, blocks: List[Block]) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for block in blocks:
            handle.write(json.dumps(block.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
    return target


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


def total_chars(blocks: List[Block]) -> int:
    return sum(len(b.text) for b in blocks)


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
