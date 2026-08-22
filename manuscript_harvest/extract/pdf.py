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
- **Fonts that do not say what their glyphs mean.** Under `/Encoding /Identity-H`
  a character code is a glyph id, and a PDF whose `/ToUnicode` CMap does not cover
  every glyph it draws extracts as a cipher: 124,178 characters of
  `TheVe VWXdLeV ZeUe LQWeQded` out of 10.1126/science.adf5357's Methods. The gaps
  are filled from the embedded font's own character map before any page is read.
  Where that cannot be done the file is not `ok` -- see `_repair_glyph_encoding`,
  which is the long version of both halves.
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
from .blocks import HEADING, PARAGRAPH, Block, strip_invisible
from .limits import Limits

OK = "ok"
NO_TEXT = "no_text"
SCANNED = "no_text_scanned_pdf"
UNREADABLE = "unreadable"
GARBLED = "garbled_text_encoding"

_HYPHEN_BREAK = re.compile(r"(\w)[-‐‑]\s*\n\s*(\w)")
_PAGE_NUMBER = re.compile(r"^\s*(?:page\s*)?\d{1,4}\s*(?:of\s*\d{1,4})?\s*$", re.IGNORECASE)

#: A soft hyphen and whatever line break it caused. This runs first in
#: `_clean_block`: before `blocks.strip_invisible`, which would delete the
#: U+00AD and leave the line break sitting there, and so before `_HYPHEN_BREAK`,
#: which would then rejoin across it. It keeps any real hyphen beside it -- the
#: raw text of 10.1126/sciimmunol.aba4163 block 6 is `interleukin-­\n17A`, which
#: must become `interleukin-17A` and not `interleukin17A`. Nothing downstream
#: can stand in: U+00AD is category Cf, neither `\w` nor `\s`, so
#: `_HYPHEN_BREAK` could never fire across one on its own.
_SOFT_BREAK = re.compile("­[ \t]*\n?[ \t]*")

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


# -- fonts that do not say what their glyphs mean ----------------------------
#
# 10.1126/science.adf5357's Supplementary Materials -- the file holding that
# paper's Materials and Methods -- extracted 124,178 characters of
# `TheVe VWXdLeV ZeUe LQWeQded` where the page reads `These studies were
# intended`, and was reported `ok`. The page itself renders correctly, so the
# glyph outlines are right and only the *text* is wrong.
#
# The cause is structural, not a corruption. Its text is drawn with subsetted
# TrueType fonts under `/Encoding /Identity-H`, where a character code in the
# content stream is a glyph id rather than a character. A `/ToUnicode` CMap is
# what turns those back into characters, and this document's covers glyph ids
# 8-75 and stops: every glyph from `i` upward has no entry. MuPDF's fallback for
# a code it cannot map is to emit the code itself, and in this font's glyph order
# a codepoint happens to sit 29 above its glyph id, so `i` (glyph 76) surfaces as
# `L` and the file reads as a Caesar cipher.
#
# That offset is a property of one font's glyph order and nothing else, which is
# why the repair below reads the *font* instead of shifting the string. The font
# program the PDF embeds carries its own `cmap` -- the table a viewer uses to
# draw the page -- and it names every glyph the ToUnicode CMap left out. Reading
# it also settles the one case a string shift cannot: the extracted `T` is a real
# `T` (glyph 55, which the publisher's CMap does map) when it comes from a mapped
# code and a `q` (glyph 84) when it does not, and the two are different glyphs
# even though they are the same character on the way out.

#: What MuPDF reports through `get_texttrace` for a glyph it could not turn into
#: a character. It is not a character the document contains; it is the absence of
#: an answer, and it is the only place MuPDF admits the substitution happened --
#: `get_text` shows the fallback codepoint with nothing to mark it.
_NO_UNICODE = 0xFFFD

#: A `beginbfchar`/`beginbfrange` section of a ToUnicode CMap.
_CMAP_SECTION = re.compile(rb"begin(bfchar|bfrange)(.*?)end\1", re.S)
#: The only two token shapes a ToUnicode section holds: a hex string, and the
#: brackets around an array of them (`<lo> <hi> [<d0> <d1> ...]`).
_CMAP_TOKEN = re.compile(rb"<([0-9A-Fa-f]*)>|(\[)|(\])")

#: A single `bfrange` wider than this is not a font's character set, it is a
#: malformed CMap; refusing it keeps a bad `<0000><FFFFFFFF>` from being expanded.
_MAX_BFRANGE = 0x10000


def _cmap_tokens(body: bytes):
    """A ToUnicode section's hex strings, an array collapsed to a list of them.

    `None` when the brackets do not balance, which is the caller's signal to leave
    the font alone rather than act on a half-read CMap.
    """
    tokens: List = []
    array: Optional[List[bytes]] = None
    for match in _CMAP_TOKEN.finditer(body):
        hex_string, opening, closing = match.group(1), match.group(2), match.group(3)
        if opening:
            if array is not None:
                return None
            array = []
        elif closing:
            if array is None:
                return None
            tokens.append(array)
            array = None
        elif array is not None:
            array.append(hex_string)
        else:
            tokens.append(hex_string)
    return None if array is not None else tokens


def _utf16(hex_string: bytes) -> Optional[str]:
    """A CMap destination. `/Ordering (UCS)` means these are UTF-16BE."""
    if not hex_string or len(hex_string) % 4:
        return None
    try:
        return bytes.fromhex(hex_string.decode("ascii")).decode("utf-16-be")
    except (ValueError, UnicodeDecodeError):
        return None


def _cmap_entries(stream: bytes) -> Optional[Dict[int, str]]:
    """What a ToUnicode CMap maps, as `character code -> text`. `None` if unparsable.

    Read for two reasons, and the second is why the destinations are decoded
    rather than only the sources counted:

    * the repair adds entries for codes this does *not* cover and no others, so a
      merged CMap cannot change a character the document already resolved,
      whichever way an implementation breaks a tie on a duplicate definition;
    * where a code appears in both this and the font's own map, the two are
      compared. Agreement is the evidence that a character code really is a glyph
      id in this font, which is the assumption the whole repair rests on.

    `None` rather than an empty dict when the syntax is not understood: an empty
    dict would read as "this CMap maps nothing", and the repair would then write
    over entries it had failed to see.
    """
    entries: Dict[int, str] = {}
    for kind, body in _CMAP_SECTION.findall(stream):
        tokens = _cmap_tokens(body)
        if tokens is None:
            return None
        if kind == b"bfchar":
            if len(tokens) % 2:
                return None
            for source, destination in zip(tokens[0::2], tokens[1::2]):
                if isinstance(source, list) or isinstance(destination, list) or not source:
                    return None
                text = _utf16(destination)
                if text is None:
                    return None
                entries[int(source, 16)] = text
            continue
        if len(tokens) % 3:
            return None
        for low_hex, high_hex, destination in zip(tokens[0::3], tokens[1::3], tokens[2::3]):
            if isinstance(low_hex, list) or isinstance(high_hex, list) \
                    or not low_hex or not high_hex:
                return None
            low, high = int(low_hex, 16), int(high_hex, 16)
            if high < low or high - low >= _MAX_BFRANGE:
                return None
            if isinstance(destination, list):
                if len(destination) != high - low + 1:
                    return None
                for offset, item in enumerate(destination):
                    text = _utf16(item)
                    if text is None:
                        return None
                    entries[low + offset] = text
                continue
            base = _utf16(destination)
            if not base:
                return None
            # A range destination counts up from its last codepoint:
            # `<0044><004b><0061>` is glyphs 68-75 to `a` through `h`.
            if ord(base[-1]) + high - low > 0x10FFFF:
                return None
            for code in range(low, high + 1):
                entries[code] = base[:-1] + chr(ord(base[-1]) + code - low)
    return entries


def _embedded_glyph_unicodes(document, xref: int) -> Dict[int, int]:
    """`glyph id -> codepoint`, read out of the font program the PDF embeds.

    `fitz.Font` over the embedded bytes exposes the font's own `cmap`, so this is
    the document's own answer rather than an inference from the extracted string.
    An empty result means there is nothing to read -- the font is not embedded, or
    carries no character map -- and the caller must leave that font alone.

    Where two codepoints share one glyph the lower wins. Both collisions in
    adf5357's Times subset are a basic-Latin character against a lookalike --
    U+002D against U+00AD (soft hyphen), U+003B against U+037E (Greek question
    mark) -- and the basic-Latin one is what the paper means. It is also what
    `_clean_block` would have to strip if the other were chosen.
    """
    try:
        _name, _ext, _subtype, buffer = document.extract_font(xref)
        if not buffer:
            return {}
        font = fitz.Font(fontbuffer=buffer)
        codepoints = font.valid_codepoints()
    except Exception:
        return {}
    glyphs: Dict[int, int] = {}
    for codepoint in codepoints:
        codepoint = int(codepoint)
        # A lone surrogate has no UTF-16 encoding, so it cannot be written as a
        # CMap destination however the font reports it.
        if 0xD800 <= codepoint <= 0xDFFF or not 0 < codepoint <= 0x10FFFF:
            continue
        try:
            glyph = font.has_glyph(codepoint)
        except Exception:
            continue
        if glyph > 0 and codepoint < glyphs.get(glyph, 0x110000):
            glyphs[glyph] = codepoint
    return glyphs


_CMAP_HEAD = """/CIDInit /ProcSet findresource begin
12 dict begin
begincmap
/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def
/CMapName /Adobe-Identity-UCS def
/CMapType 2 def
1 begincodespacerange
<0000><FFFF>
endcodespacerange
"""
_CMAP_TAIL = "endcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n"

#: `bfchar` sections are capped at 100 entries by the PDF specification.
_BFCHAR_PER_SECTION = 100


def _bfchar_sections(glyphs: Dict[int, int]) -> str:
    """`glyph id -> codepoint` as ToUnicode `bfchar` sections.

    Destinations are UTF-16BE, which is what a CMap of `/Ordering (UCS)` means by
    a hex string, so an astral codepoint is written as its surrogate pair rather
    than truncated.
    """
    out = []
    entries = sorted(glyphs.items())
    for start in range(0, len(entries), _BFCHAR_PER_SECTION):
        section = entries[start:start + _BFCHAR_PER_SECTION]
        out.append("%d beginbfchar\n" % len(section))
        for glyph, codepoint in section:
            out.append("<%04X><%s>\n" % (glyph, chr(codepoint).encode("utf-16-be").hex().upper()))
        out.append("endbfchar\n")
    return "".join(out)


#: `/Subtype` and `/CIDToGIDMap` out of a descendant font, which may be written
#: as an object of its own or inline inside the `/DescendantFonts` array.
_DESCENDANT_SUBTYPE = re.compile(r"/Subtype\s*/(\w+)")
_CID_TO_GID = re.compile(r"/CIDToGIDMap\s*(/\w+|\d+\s+\d+\s+R)")
_INDIRECT = re.compile(r"(\d+)\s+\d+\s+R")

#: How much of the overlap between a font's own map and the CMap already in the
#: file may disagree before the font is left alone. See `_repair_font_encoding`.
_MAX_MAP_DISAGREEMENT = 0.5


def _descendant_font(document, xref: int) -> str:
    """The descendant `CIDFont` dictionary of a Type0 font, as text.

    Not resolved to an xref, because it does not always have one. 105 of the
    Identity-H fonts in this corpus -- every `CIDFont+F1` written by whatever tool
    Nature Portfolio's PDFs come out of -- write the descendant inline inside the
    array. Reaching for the first `N 0 R` in that array text finds
    `/Ordering 9675 0 R`, whose object is the string `(Identity)`, and the font
    then looks like it has no subtype at all and is skipped.
    """
    kind, value = document.xref_get_key(xref, "DescendantFonts")
    if kind == "xref":
        found = _INDIRECT.match((value or "").strip())
        value = document.xref_object(int(found.group(1))) if found else ""
    value = value or ""
    if "<<" in value:
        return value
    found = _INDIRECT.search(value)
    return document.xref_object(int(found.group(1))) or "" if found else ""


def _repair_font_encoding(document, xref: int, rewritten=None):
    """Fill the gaps in one font's ToUnicode CMap from the font's own `cmap`.

    Returns the number of glyphs given a character, `0` when the font needed
    nothing, and `None` when this is not a font the repair can speak for.

    The whole thing rests on one assumption -- that a character code in the
    content stream is a glyph id in the embedded font -- so each condition below
    is there to make that true rather than merely likely:

    * `/Encoding /Identity-H`, which is the encoding under which a code *is* a
      CID. Under any other encoding the code indexes an encoding table and the
      font's own map answers a different question.
    * a descendant `/CIDFontType2` with `/CIDToGIDMap` absent or `/Identity`,
      where CID and glyph id are equal by specification. A stream-valued map
      means they are not.
    * or a descendant `/CIDFontType0`, where the specification makes CID and glyph
      id equal exactly when the embedded CFF is not CID-keyed -- and a CID-keyed
      CFF is also one `_embedded_glyph_unicodes` cannot read, because its glyph
      names are `cid00042` and it has no character map. All 516 of the
      `CIDFontType0` fonts in this corpus fall out there, 10.1038/s41588-024-01702-0's
      reporting summary among them; the branch exists because the specification
      allows the other kind, not because a file here needs it.
    * where the CMap already in the file and the font's own map both name a glyph,
      they must mostly agree. This is the assumption being tested against the
      document's own data rather than argued from the specification, and it is the
      check that would catch a font the three rules above let through wrongly.

    Measured over the 898 fonts in this corpus where both maps exist: 846 agree
    outright and 52 disagree on 7% of their overlap at worst, every one of them a
    glyph named two equivalent ways -- U+FB01 against `fi`, thin space against
    narrow no-break space, hyphen against non-breaking hyphen. A font where the
    code is not the glyph id would disagree on nearly all of it, which is why the
    bar sits at half and not at zero.
    """
    def key(name):
        kind, value = document.xref_get_key(xref, name)
        return value if kind != "null" else None

    if key("Subtype") != "/Type0" or key("Encoding") != "/Identity-H":
        return None
    descendant = _descendant_font(document, xref)
    found = _DESCENDANT_SUBTYPE.search(descendant)
    subtype = found.group(1) if found else ""
    if subtype == "CIDFontType2":
        mapping = _CID_TO_GID.search(descendant)
        if mapping and mapping.group(1) != "/Identity":
            return None
    elif subtype != "CIDFontType0":
        return None

    glyphs = _embedded_glyph_unicodes(document, xref)
    if not glyphs:
        return None

    stream_xref = None
    to_unicode = document.xref_get_key(xref, "ToUnicode")
    existing = b""
    if to_unicode[0] == "xref":
        stream_xref = int(to_unicode[1].split()[0])
        # Two fonts may share one CMap object. Adding the second font's glyphs to
        # it would put entries for glyphs of one font under the codes of another,
        # so the first font wins and the second is left as it was.
        if rewritten is not None and stream_xref in rewritten:
            return None
        try:
            existing = document.xref_stream(stream_xref) or b""
        except Exception:
            return None
        entries = _cmap_entries(existing)
        if entries is None:
            return None
        overlap = [code for code in glyphs if code in entries]
        disagreed = sum(1 for code in overlap if entries[code] != chr(glyphs[code]))
        if overlap and disagreed > _MAX_MAP_DISAGREEMENT * len(overlap):
            return None
        glyphs = {g: c for g, c in glyphs.items() if g not in entries}
    if not glyphs:
        return 0

    if existing and b"endcmap" in existing:
        # Added to the publisher's own CMap rather than replacing it, so anything
        # this reader did not model -- a `usecmap`, a wider codespace range --
        # survives untouched.
        head, _, tail = existing.rpartition(b"endcmap")
        data = head + _bfchar_sections(glyphs).encode("latin-1") + b"endcmap" + tail
    else:
        data = (_CMAP_HEAD + _bfchar_sections(glyphs) + _CMAP_TAIL).encode("latin-1")

    if stream_xref is None:
        stream_xref = document.get_new_xref()
        document.update_object(stream_xref, "<<>>")
        document.update_stream(stream_xref, data, new=True)
        document.xref_set_key(xref, "ToUnicode", "%d 0 R" % stream_xref)
    else:
        document.update_stream(stream_xref, data, new=True)
    if rewritten is not None:
        rewritten.add(stream_xref)
    return len(glyphs)


def _repair_glyph_encoding(document) -> dict:
    """Give every embedded font a ToUnicode CMap covering the glyphs it draws.

    Walked over the xref table rather than page by page: the 60-page supplement
    of 10.1126/science.adf5357 reports 108 page-font pairs for 6 distinct fonts,
    and a font must be rewritten once. The whole walk is 1,452 objects and costs
    about 10 ms there, so it is unconditional -- deciding whether to bother would
    take the same page pass the repair is meant to avoid.

    The rewrite is in-memory and takes effect without saving: MuPDF reads a
    font's ToUnicode when the page that uses it is first laid out, and no page has
    been touched at this point.
    """
    repaired: Dict[str, int] = {}
    rewritten: set = set()
    for xref in range(1, document.xref_length()):
        try:
            if document.xref_get_key(xref, "Type")[1] != "/Font":
                continue
            added = _repair_font_encoding(document, xref, rewritten)
        except Exception:
            continue
        if added:
            name = (document.xref_get_key(xref, "BaseFont")[1] or "?").lstrip("/")
            repaired[name] = repaired.get(name, 0) + added
    return repaired


def _page_spans(page) -> List[dict]:
    """One `get_texttrace` pass, read by `_symbol_map` and `_unresolved_glyphs`.

    `get_text("dict")` was here, and it answers only the first of those two
    questions: it reports the character MuPDF settled on and not whether MuPDF
    could find one. `get_texttrace` reports both, at slightly less cost than
    `dict` -- measured over this corpus's two largest PDFs, 4.46s against 4.78s
    and 1.62s against 1.94s -- so the glyph count is not a third pass over every
    page but a second reader of the one that was already being made.

    `[]` on failure rather than raising, as the `dict` call it replaces did. A
    page whose trace fails costs its symbol map and its glyph count and keeps its
    text; the caller records that it happened.
    """
    try:
        return page.get_texttrace()
    except Exception:
        return []


def _symbol_map(spans) -> Dict[int, str]:
    """The private-use codepoints in these spans that came out of a symbol font.

    Built per page and per span so that a PUA codepoint from an unrelated
    subsetted font is never turned into a Greek letter.
    """
    found: Dict[int, str] = {}
    for span in spans:
        if not _SYMBOL_FONT.search(span.get("font") or ""):
            continue
        for char in span.get("chars") or ():
            replacement = _SYMBOL_PUA.get(char[0])
            if replacement is not None:
                found[char[0]] = replacement
    return found


def _unresolved_glyphs(spans):
    """`(glyphs drawn, glyphs MuPDF could not name)` in one page's spans.

    The measurement `blocks_from_pdf` refuses a file on, and deliberately not a
    check on the *words*: a supplementary figure PDF legitimately contains almost
    no English, so "this text has no function words in it" flags 26 files in this
    corpus of which one is actually broken, and it says nothing at all about a
    paper written in another language. This asks the parser instead, and the
    parser knows -- every character of 10.1126/science.adf5357's methods came back
    from `get_texttrace` marked U+FFFD while `get_text` was printing the fallback
    codepoint with nothing to mark it.
    """
    total = failed = 0
    for span in spans:
        for char in span.get("chars") or ():
            total += 1
            if char[0] == _NO_UNICODE:
                failed += 1
    return total, failed


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
    text = strip_invisible(text)
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
    drawn_glyphs = unnamed_glyphs = 0
    try:
        meta["pages"] = document.page_count
        # Before any page is laid out, or MuPDF has already read the CMap this
        # replaces.
        repaired = _repair_glyph_encoding(document)
        if repaired:
            meta["glyph_encoding_repaired"] = repaired
        for page in document:
            texts: List[Tuple[str, bool, dict]] = []
            try:
                raw_blocks = page.get_text("blocks")
                spans = _page_spans(page)
                if not spans and raw_blocks:
                    meta.setdefault("errors", []).append(
                        f"page {page.number + 1}: no glyph trace; symbol glyphs and "
                        f"the unnamed-glyph count are missing for this page")
                symbols = _symbol_map(spans)
                page_glyphs, page_unnamed = _unresolved_glyphs(spans)
                drawn_glyphs += page_glyphs
                unnamed_glyphs += page_unnamed
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
    if unnamed_glyphs:
        # Recorded on every file that has any, not only on the ones the status
        # rejects. Sub-threshold damage is real and is almost always a figure's
        # tick labels -- `SUHC*-DVWURF\WH-0` for `preCG-astrocyte-0` on page 11
        # of 10.1016/j.cell.2024.08.019's mmc8 -- and the module's standing rule
        # for a glyph it cannot name is to count it rather than drop it quietly.
        meta["glyphs_unnamed"] = unnamed_glyphs
        meta["glyphs_drawn"] = drawn_glyphs

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

    tracker.record(meta)
    body_chars = sum(len(b.text) for b in blocks)
    meta["chars"] = body_chars

    # The rule that stops this class being reported as a success, stated at the
    # point that enforces it. 10.1126/science.adf5357's Supplementary Materials
    # -- 124,178 characters of `TheVe VWXdLeV ZeUe LQWeQded`, and the only copy
    # of that paper's Materials and Methods -- was `ok` before it.
    #
    # Judged on glyphs the *document* declines to name, not on whether the text
    # looks like English. A supplementary figure PDF is mostly gene symbols and
    # axis labels, so "no function words in it" flags 26 files in this corpus of
    # which one is broken, and it says nothing at all about a paper in Chinese.
    # `get_texttrace` reports U+FFFD for a glyph MuPDF could not turn into a
    # character, which is the same question asked of the file instead of the
    # prose.
    #
    # The blocks go with the status. Their characters are MuPDF's fallback for a
    # code it could not map, not characters the document contains, and letting
    # them through is the whole bug: `blocks.jsonl` had 192 paragraphs of them
    # and nothing anywhere said so. What was there is still counted, in
    # `glyphs_unnamed` and in `garbled_sample`.
    if drawn_glyphs and unnamed_glyphs / drawn_glyphs > limits.max_unnamed_glyph_fraction:
        meta["reason"] = (
            f"{unnamed_glyphs} of {drawn_glyphs} glyphs ({unnamed_glyphs / drawn_glyphs:.0%}) "
            f"have no character behind them: the fonts carry no ToUnicode map and no "
            f"character map of their own, so what the page draws cannot be read as text")
        meta["garbled_sample"] = (blocks[0].text[:200] if blocks else "")
        meta["chars"] = 0
        return [], GARBLED, meta

    if body_chars < limits.min_pdf_text_chars:
        return blocks, SCANNED, meta
    return blocks, OK, meta
