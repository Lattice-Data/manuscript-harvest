"""PDF -> blocks. The fallback for main text, and the only route for 13 articles.

PyMuPDF's layout blocks are used rather than raw page text, because a block is a
much better paragraph proxy than "text between blank lines" and it survives
two-column layouts, which most publisher PDFs are.

Several clean-ups happen here and they change the characters:

- **De-hyphenation.** "perturba-\\ntion" becomes "perturbation". Left alone, a
  hyphenated word is unsearchable and cannot be quoted. Any downstream check that
  a quote really appears in the source compares it against the text this module
  produced, so the text it produces is the text that counts.
- **Invisible characters.** Soft hyphens, zero-width spaces and BOMs are removed.
  U+00AD is category Cf -- neither `\\w` nor `\\s` -- so it survived both the
  de-hyphenation pattern and the whitespace collapse and sat inside the word:
  79 damaged codepoints in 10.1126/sciimmunol.aba4163, whose block 6 read
  `interleukin-<U+00AD> 17A` where the paper says `interleukin-17A`.
- **Symbol-font glyphs.** A Symbol glyph with no ToUnicode map arrives as an
  Adobe Symbol position in the private use area: 41 of the same file's blocks
  carried U+F067 where the paper says gamma. Those are translated, but only for
  spans whose font is a symbol face, and whatever is left over is counted in
  `glyphs_unmapped` rather than passed on quietly.
- **Running heads.** A journal footer repeated on every page would otherwise
  appear as thirty near-identical paragraphs. A short line seen *in a page
  margin* on at least `limits.running_header_min_pages` pages is dropped, and
  both the count and the strings are recorded. Position is what makes the rule
  safe: without it the same test deleted `Reviewer #2 (Remarks to the Author):`
  from a peer-review file and a UMAP legend from a figure.

Table structure is not recovered from PDFs. A supplementary PDF that is really a
table still yields its cell text as paragraphs -- searchable, but not a card.
That is a real gap; JATS and spreadsheets are where the tables come from.
"""

import re
from collections import Counter
from typing import Dict, List, Optional, Tuple

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

#: A soft hyphen and whatever line break it caused. This runs *before*
#: `_HYPHEN_BREAK`, and it keeps any real hyphen beside it: the raw text of
#: 10.1126/sciimmunol.aba4163 block 6 is `interleukin-­\n17A`, which must
#: become `interleukin-17A` and not `interleukin17A`.
_SOFT_BREAK = re.compile("­[ \t]*\n?[ \t]*")

#: Zero-width and invisible characters, removed outright. U+00AD is category Cf
#: -- neither `\w` nor `\s` -- so `_HYPHEN_BREAK` could never fire across one and
#: the whitespace collapse left it sitting inside the word.
_INVISIBLE = {0x00ad: None, 0x200b: None, 0x200c: None, 0x200d: None, 0xfeff: None}

#: Fonts whose private-use codepoints are Adobe Symbol positions rather than a
#: subsetted Latin face. Checked per span, because a PUA codepoint out of an
#: ordinary subsetted font means something else entirely and must be left alone.
_SYMBOL_FONT = re.compile(r"symbol|cmsy|cmmi|mathematicalpi|advp", re.I)

#: The Adobe Symbol encoding, offset into the private use area at 0xF000, which
#: is how PyMuPDF reports a Symbol glyph with no ToUnicode map. Measured on
#: 10.1126/sciimmunol.aba4163 (font `SymbolGreek`): 41x U+F067, which is the
#: gamma in `IFN-γ`, `RORγt` and `CD8` across blocks 6, 13, 18, 66, 137,
#: 138, 235-245 and 301.
_SYMBOL_PUA: Dict[int, str] = {
    # upper-case Greek
    0xF041: "Α", 0xF042: "Β", 0xF043: "Χ", 0xF044: "Δ", 0xF045: "Ε", 0xF046: "Φ",
    0xF047: "Γ", 0xF048: "Η", 0xF049: "Ι", 0xF04A: "ϑ", 0xF04B: "Κ", 0xF04C: "Λ",
    0xF04D: "Μ", 0xF04E: "Ν", 0xF04F: "Ο", 0xF050: "Π", 0xF051: "Θ", 0xF052: "Ρ",
    0xF053: "Σ", 0xF054: "Τ", 0xF055: "Υ", 0xF056: "ς", 0xF057: "Ω", 0xF058: "Ξ",
    0xF059: "Ψ", 0xF05A: "Ζ",
    # lower-case Greek
    0xF061: "α", 0xF062: "β", 0xF063: "χ", 0xF064: "δ", 0xF065: "ε", 0xF066: "φ",
    0xF067: "γ", 0xF068: "η", 0xF069: "ι", 0xF06A: "ϕ", 0xF06B: "κ", 0xF06C: "λ",
    0xF06D: "μ", 0xF06E: "ν", 0xF06F: "ο", 0xF070: "π", 0xF071: "θ", 0xF072: "ρ",
    0xF073: "σ", 0xF074: "τ", 0xF075: "υ", 0xF076: "ϖ", 0xF077: "ω", 0xF078: "ξ",
    0xF079: "ψ", 0xF07A: "ζ",
    # the mathematical operators that turn up in a methods section
    0xF02D: "−", 0xF0A3: "≤", 0xF0A5: "∞", 0xF0AC: "←", 0xF0AD: "↑", 0xF0AE: "→",
    0xF0AF: "↓", 0xF0B0: "°", 0xF0B1: "±", 0xF0B2: "″", 0xF0B3: "≥", 0xF0B4: "×",
    0xF0B5: "∝", 0xF0B6: "∂", 0xF0B7: "•", 0xF0B8: "÷", 0xF0B9: "≠", 0xF0BA: "≡",
    0xF0BB: "≈", 0xF0BC: "…", 0xF0D6: "√", 0xF0D7: "⋅",
}

_PUA_START, _PUA_END = 0xE000, 0xF8FF


def _is_pua(codepoint: int) -> bool:
    return _PUA_START <= codepoint <= _PUA_END


def _symbol_map(page) -> Dict[int, str]:
    """The private-use codepoints on this page that came out of a symbol font.

    Built per page from `page.get_text("dict")` spans so that a PUA codepoint
    from an unrelated subsetted font is never turned into a Greek letter.
    """
    found: Dict[int, str] = {}
    try:
        rendered = page.get_text("dict")
    except Exception:
        return found
    for block in rendered.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                if not _SYMBOL_FONT.search(span.get("font") or ""):
                    continue
                for char in span.get("text") or "":
                    replacement = _SYMBOL_PUA.get(ord(char))
                    if replacement is not None:
                        found[ord(char)] = replacement
    return found


def _rejoin_hyphen(match: "re.Match", tally: Optional[Counter] = None) -> str:
    """Close a line break at a hyphen, keeping the hyphen inside an identifier.

    Rejoining unconditionally deletes a real hyphen whenever a hyphenated token
    happens to break at it. Measured over the PDFs in this corpus: 648 hyphens at
    a line break, 78 of them in tokens this guard keeps -- `SARS-CoV-2`,
    `COVID-19`, `snRNA-seq`, `Mono_c1-CD14-CCL3`, `T_CD8_c09-SLC4A10`. Those are
    gene symbols, cell-type names and accessions, which is what a curation answer
    is made of; the other 570 are ordinary words like `perturba-tion`.

    A digit or a capital on either side of the break is the whole test. It costs
    `well-\\nknown`, which becomes `wellknown` -- nothing short of a dictionary
    separates that from `perturba-tion`, and losing a hyphen out of a common
    adjective is cheaper than losing one out of a cell-type name.
    """
    before, after = match.group(1), match.group(2)
    keep = before.isdigit() or before.isupper() or after.isdigit() or after.isupper()
    if tally is not None:
        tally["kept" if keep else "joined"] += 1
    return f"{before}-{after}" if keep else f"{before}{after}"


def _clean_block(text: str, symbols: Optional[Dict[int, str]] = None,
                 hyphens: Optional[Counter] = None) -> str:
    text = _SOFT_BREAK.sub("", text)
    text = text.translate(_INVISIBLE)
    if symbols:
        text = text.translate(symbols)
    text = _HYPHEN_BREAK.sub(lambda m: _rejoin_hyphen(m, hyphens), text)
    text = text.replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


#: How far in from each edge still counts as a margin, as a fraction of the page.
_MARGIN_TOP, _MARGIN_BOTTOM = 0.12, 0.88
_MARGIN_LEFT, _MARGIN_RIGHT = 0.08, 0.92


def _in_margin(rect, width: float, height: float) -> bool:
    """Whether a layout block sits in a page margin rather than in the body."""
    x0, y0, x1, y1 = rect
    return (y1 < _MARGIN_TOP * height or y0 > _MARGIN_BOTTOM * height
            or x1 < _MARGIN_LEFT * width or x0 > _MARGIN_RIGHT * width)


def _running_lines(page_texts: List[List[Tuple[str, bool, dict]]],
                   limits: Limits) -> Dict[str, int]:
    """Short strings repeated in a page *margin*: furniture, not content.

    Returns `text -> margin pages`, not a set, so the record can name what was
    deleted. Position is the whole guard. Counting every appearance dropped 424
    of 1,160 blocks in 10.1126/sciimmunol.aba4163 and 854 of 2,474 in the
    89-page Science supplement, and among them
    `Reviewer #2 (Remarks to the Author):` -- so that article's blocks.jsonl held
    reviewers 1, 3, 1, 3 and 4 and no reviewer 2 at all, their remarks reading as
    a continuation of reviewer 1's. It also deleted the UMAP legend
    (`CD4 T cell`, `MNP-a`) and aba4163's `S. aureus`, `Day 0`, `Day 60`,
    `Control`, `Crescents (%)`: treatment and timepoint labels.

    "In a margin on *every* page" was tried and fails on real furniture. Counting
    only margin appearances keeps `Krebs et al., Sci. Immunol...` (y0/h = 0.03
    throughout), `SCIENCE IMMUNOLOGY | RESEARCH ARTICLE`, the rotated
    `Downloaded from https://www.science.org...` (x0/w = 0.95), `ll`,
    `ll Resource` and `(legend on next page)` dropped, while
    `Reviewer #2 (Remarks to the Author):` (y0/h = 0.28, 0.53, 0.12) survives.
    """
    appearances: Counter = Counter()
    for texts in page_texts:
        for text in {t for t, margin, _ref in texts if margin}:
            if len(text) <= 100:
                appearances[text] += 1
    return {text: count for text, count in appearances.items()
            if count >= limits.running_header_min_pages}


def blocks_from_pdf(
    data: bytes, source_file: str, limits: Limits
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

    per_page: List[List[Tuple[str, bool, dict]]] = []
    meta: dict = {}
    mapped: Counter = Counter()
    unmapped: Counter = Counter()
    hyphens: Counter = Counter()
    try:
        meta["pages"] = document.page_count
        for page in document:
            texts: List[Tuple[str, bool, dict]] = []
            try:
                raw_blocks = page.get_text("blocks")
                symbols = _symbol_map(page)
                width = max(page.rect.width, 1.0)
                height = max(page.rect.height, 1.0)
            except Exception as e:  # a damaged page should not lose the whole file
                meta.setdefault("errors", []).append(f"page {page.number + 1}: {e}")
                per_page.append([])
                continue
            for raw in raw_blocks:
                # (x0, y0, x1, y1, text, block_no, block_type); type 1 is an image.
                if len(raw) >= 7 and raw[6] != 0:
                    continue
                source = raw[4] if len(raw) > 4 else ""
                mapped.update(ord(c) for c in source if ord(c) in symbols)
                cleaned = _clean_block(source, symbols, hyphens)
                unmapped.update(ord(c) for c in cleaned if _is_pua(ord(c)))
                if cleaned:
                    # Rounded to one decimal so the JSON stays byte-stable across
                    # re-extraction of the same bytes.
                    ref = {"page": page.number + 1,
                           "bbox": [round(float(v), 1) for v in raw[0:4]],
                           "block_no": int(raw[5]) if len(raw) > 5 else None}
                    texts.append((cleaned, _in_margin(raw[0:4], width, height), ref))
            per_page.append(texts)
    except Exception as e:
        # Page-level damage is already handled above; this is the document
        # itself giving way, which must cost one file rather than the run.
        meta["reason"] = f"{type(e).__name__}: {e}"
        return [], UNREADABLE, meta
    finally:
        document.close()

    if hyphens:
        meta["hyphens_kept"] = hyphens["kept"]
        meta["hyphens_joined"] = hyphens["joined"]
    if mapped:
        meta["glyphs_mapped"] = {f"U+{cp:04X}": _SYMBOL_PUA[cp] for cp in sorted(mapped)}
    if unmapped:
        # Named rather than silently passed on: `1 <U+F8FF>i <U+F8FF>n` from
        # CMSY10 in 10.1038/s41467-023-40505-5 is really `1 <= i <= n`, and
        # U+F8FF is PyMuPDF's "no unicode mapping" fallback rather than an Adobe
        # Symbol position, so guessing a character for it would be a guess.
        meta["glyphs_unmapped"] = {f"U+{cp:04X}": n for cp, n in sorted(unmapped.items())}

    furniture = _running_lines(per_page, limits)
    meta["running_lines_dropped"] = 0
    # Nothing a rule drops may be silent. `running_lines_dropped` was set here
    # already but was not in `extractor.py`'s allow-list, so it never reached
    # `extraction.json` and the only reader anywhere was one test.
    meta["running_lines"] = [{"text": text, "pages": pages} for text, pages
                             in sorted(furniture.items(), key=lambda kv: (-kv[1], kv[0]))][:20]

    blocks: List[Block] = []
    tracker = sections_mod.SectionTracker(limits=limits)
    for page_index, texts in enumerate(per_page, start=1):
        for text, _margin, ref in texts:
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
                                    origin="pdf", locator=locator, locator_ref=ref,
                                    section=tracker.heading(named)))
                continue
            glued = sections_mod.split_leading_heading(text)
            if glued:
                named, heading, text = glued
                blocks.append(Block(kind=HEADING, text=heading, source_file=source_file,
                                    origin="pdf", locator=locator, locator_ref=ref,
                                    section=tracker.heading(named)))
                meta["glued_headings_split"] = meta.get("glued_headings_split", 0) + 1
            elif sections_mod.looks_like_heading(text):
                blocks.append(Block(kind=HEADING, text=text, source_file=source_file,
                                    origin="pdf", locator=locator, locator_ref=ref,
                                    section=tracker.carry(text)))
                continue
            if len(text) < limits.min_paragraph_chars:
                continue
            blocks.append(Block(kind=PARAGRAPH, text=text, source_file=source_file,
                                origin="pdf", locator=locator, locator_ref=ref,
                                section=tracker.carry(text)))

    meta["sections"] = tracker.seen
    if tracker.abandoned:
        meta["sections_abandoned"] = tracker.abandoned
    if tracker.withheld:
        meta["low_value_blocks_withheld"] = tracker.withheld
    if tracker.reopens_refused:
        meta["reopens_refused"] = tracker.reopens_refused
    if tracker.reason():
        meta["reason"] = tracker.reason()
    body_chars = sum(len(b.text) for b in blocks)
    meta["chars"] = body_chars
    if body_chars < limits.min_pdf_text_chars:
        return blocks, SCANNED, meta
    return blocks, OK, meta
