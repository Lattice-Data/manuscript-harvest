"""PDF -> blocks. The fallback for main text, and the only route for 13 articles.

PyMuPDF's layout blocks are used rather than raw page text, because a block is a
much better paragraph proxy than "text between blank lines" and it survives
two-column layouts, which most publisher PDFs are.

Two clean-ups happen here and they change the characters:

- **De-hyphenation.** "perturba-\\ntion" becomes "perturbation". Left alone, a
  hyphenated word is unsearchable and cannot be quoted. Any downstream check that
  a quote really appears in the source compares it against the text this module
  produced, so the text it produces is the text that counts.
- **Running heads.** A journal footer repeated on every page would otherwise
  appear as thirty near-identical paragraphs. A short line seen on at least
  `limits.running_header_min_pages` pages is dropped and the count recorded.

Table structure is not recovered from PDFs. A supplementary PDF that is really a
table still yields its cell text as paragraphs -- searchable, but not a card.
That is a real gap; JATS and spreadsheets are where the tables come from.
"""

import re
from collections import Counter
from typing import List, Tuple

import fitz  # PyMuPDF

from . import sections as sections_mod
from .blocks import HEADING, PARAGRAPH, Block
from .limits import Limits

OK = "ok"
NO_TEXT = "no_text"
SCANNED = "no_text_scanned_pdf"
UNREADABLE = "unreadable"

_HYPHEN_BREAK = re.compile(r"(\w)[-‐‑]\s*\n\s*(\w)")
_PAGE_NUMBER = re.compile(r"^\s*(?:page\s*)?\d{1,4}\s*(?:of\s*\d{1,4})?\s*$", re.IGNORECASE)


def _clean_block(text: str) -> str:
    text = _HYPHEN_BREAK.sub(r"\1\2", text)
    text = text.replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def _running_lines(page_texts: List[List[str]], limits: Limits) -> set:
    """Short strings that appear on enough pages to be furniture, not content."""
    appearances = Counter()
    for texts in page_texts:
        for text in set(texts):
            if len(text) <= 100:
                appearances[text] += 1
    return {text for text, count in appearances.items()
            if count >= limits.running_header_min_pages}


def blocks_from_pdf(
    data: bytes, source_file: str, limits: Limits, origin: str = "pdf"
) -> Tuple[List[Block], str, dict]:
    """Parse one PDF. Returns `(blocks, status, meta)`.

    `no_text_scanned_pdf` is a distinct status from `no_text` on purpose: it means
    the file is the article but needs an OCR step this pipeline does not have,
    which is a different problem from a file that genuinely has nothing in it.
    """
    try:
        document = fitz.open(stream=data, filetype="pdf")
    except Exception as e:
        return [], UNREADABLE, {"reason": f"{type(e).__name__}: {e}"}

    per_page: List[List[str]] = []
    meta: dict = {}
    try:
        meta["pages"] = document.page_count
        for page in document:
            texts = []
            try:
                raw_blocks = page.get_text("blocks")
            except Exception as e:  # a damaged page should not lose the whole file
                meta.setdefault("errors", []).append(f"page {page.number + 1}: {e}")
                per_page.append([])
                continue
            for raw in raw_blocks:
                # (x0, y0, x1, y1, text, block_no, block_type); type 1 is an image.
                if len(raw) >= 7 and raw[6] != 0:
                    continue
                cleaned = _clean_block(raw[4] if len(raw) > 4 else "")
                if cleaned:
                    texts.append(cleaned)
            per_page.append(texts)
    except Exception as e:
        # Page-level damage is already handled above; this is the document
        # itself giving way, which must cost one file rather than the run.
        meta["reason"] = f"{type(e).__name__}: {e}"
        return [], UNREADABLE, meta
    finally:
        document.close()

    furniture = _running_lines(per_page, limits)
    meta["running_lines_dropped"] = 0

    blocks: List[Block] = []
    tracker = sections_mod.SectionTracker()
    for page_index, texts in enumerate(per_page, start=1):
        for text in texts:
            if text in furniture or _PAGE_NUMBER.match(text):
                meta["running_lines_dropped"] += 1
                continue
            if len(blocks) >= limits.max_blocks_per_file:
                meta["blocks_capped"] = True
                break
            locator = f"p.{page_index}"
            named = sections_mod.normalize(text)
            if named:
                blocks.append(Block(kind=HEADING, text=text, source_file=source_file,
                                    origin=origin, locator=locator,
                                    section=tracker.heading(named)))
                continue
            glued = sections_mod.split_leading_heading(text)
            if glued:
                named, heading, text = glued
                blocks.append(Block(kind=HEADING, text=heading, source_file=source_file,
                                    origin=origin, locator=locator,
                                    section=tracker.heading(named)))
                meta["glued_headings_split"] = meta.get("glued_headings_split", 0) + 1
            elif sections_mod.looks_like_heading(text):
                blocks.append(Block(kind=HEADING, text=text, source_file=source_file,
                                    origin=origin, locator=locator,
                                    section=tracker.carry(text)))
                continue
            if len(text) < limits.min_paragraph_chars:
                continue
            blocks.append(Block(kind=PARAGRAPH, text=text, source_file=source_file,
                                origin=origin, locator=locator,
                                section=tracker.carry(text)))

    meta["sections"] = tracker.seen
    if tracker.abandoned:
        meta["sections_abandoned"] = tracker.abandoned
    if tracker.withheld:
        meta["low_value_blocks_withheld"] = tracker.withheld
    if tracker.reason():
        meta["reason"] = tracker.reason()
    body_chars = sum(len(b.text) for b in blocks)
    meta["chars"] = body_chars
    if body_chars < limits.min_pdf_text_chars:
        return blocks, SCANNED, meta
    return blocks, OK, meta
