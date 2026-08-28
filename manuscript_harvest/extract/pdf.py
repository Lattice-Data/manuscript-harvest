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

**OCR, for pages that carry no text layer at all.** 70 supplements in this corpus
extract as `no_text_scanned_pdf` -- 245 pages between them, median 3, longest 11 --
and those are scans of tables and figure panels, not files with nothing in them.
Where the `tesseract` binary is available they are rendered, read, and returned as
`ok_via_ocr`: a status of its own, never folded into `ok`, because OCR'd characters
are weaker evidence than a text layer the document actually carries. Where it is
not available the file stays `no_text_scanned_pdf` and the reason names what to
install. `_ocr_pass` is the whole account.

Table structure is not recovered from PDFs. A supplementary PDF that is really a
table still yields its cell text as paragraphs -- searchable, but not a card.
That is a real gap; JATS and spreadsheets are where the tables come from.
"""

import re
import shutil
import subprocess
from collections import Counter
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

import fitz  # PyMuPDF

from . import sections as sections_mod
from .blocks import HEADING, PARAGRAPH, Block, strip_invisible
from .limits import Limits

# MuPDF's own diagnostics go to **stdout**, not stderr, and not through Python: the
# C library writes them itself, so nothing in this package could see them and
# nothing could keep them out of the way.
#
# That is a correctness problem and not merely noise, because `extract/cli.py`'s
# `one` prints the extracted directory to stdout as its machine-readable result.
# Four of the 1,097 PDFs in this corpus emit a message -- a broken ICC profile in
# 10.21203/rs.3.rs-7535904_v2, a stitching function with too many sub-functions in
# 10.1101/2025.09.14.673351, an annotation with no appearance stream in
# 10.1101/2024.08.12.607536, and `object is not a stream` in a supplement of
# 10.1038/s41588-025-02083-8 -- and for those four
# `DIR=$(manuscript-extract one ...)` came back with 55 bytes of MuPDF in front of
# the path. `extract all` was unaffected only because it reports on stderr.
#
# Silenced rather than redirected, and then read back per file: `mupdf_warnings()`
# returns everything MuPDF would have printed, so the messages end up in the
# extraction record where every other finding about a file already lives. The
# captured form is the fuller one -- for the ICC profile it is three lines ending
# `ignoring broken ICC profile`, which is MuPDF saying what it did about it.
#
# None of the four costs any text: all four files parse, and the two whose PDF this
# stage actually reads come out `ok` with 96,493 and 103,580 characters.
fitz.TOOLS.mupdf_display_errors(False)

OK = "ok"
OK_VIA_OCR = "ok_via_ocr"
NO_TEXT = "no_text"
SCANNED = "no_text_scanned_pdf"
UNREADABLE = "unreadable"
GARBLED = "garbled_text_encoding"

#: The language tesseract is told to expect. Not a `Limits` field: every paper in
#: this corpus is in English, and a run that needs another one needs the matching
#: tessdata installed as well, which is not something a config key can arrange.
OCR_LANGUAGE = "eng"

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


# -- OCR ---------------------------------------------------------------------

@lru_cache(maxsize=1)
def ocr_support() -> Tuple[str, str]:
    """`(tessdata_path, "")` when OCR can run here, `("", why_not)` when it cannot.

    An optional *system* dependency, handled the way `spreadsheet.cards_from_xls`
    handles an optional Python one: the file keeps its old status and the reason
    names what to install, rather than a traceback or a silent empty result.

    `shutil.which` is asked first even though `fitz.get_tessdata` would answer the
    same question, because of how it answers it. That function shells out to
    `tesseract --list-langs`, then to `whereis tesseract-ocr` and `whereis
    tesseract`, and on a machine without tesseract raises a RuntimeError whose text
    is a list of glob patterns that did not match -- true, and no use at all to
    whoever has to fix it. One `which` call turns that into a sentence with the
    install command in it.

    Cached because it is a subprocess. Asked once per file otherwise, which over
    the 70 scanned supplements here is 70 processes to learn one fact that cannot
    change during a run. `ocr_support.cache_clear()` is how a test moves it.
    """
    if shutil.which("tesseract") is None:
        return "", ("scanned pages need the tesseract binary, which is not on PATH "
                    "(macOS: brew install tesseract; Debian/Ubuntu: apt install "
                    "tesseract-ocr)")
    try:
        return fitz.get_tessdata(), ""
    except Exception as e:
        # tesseract without its language data: PyMuPDF cannot start an OCR pass and
        # the fix is a different package (`tesseract-ocr-eng`) from the binary.
        return "", (f"tesseract is installed but its language data was not found "
                    f"({type(e).__name__}: {e})")


@lru_cache(maxsize=1)
def tesseract_version() -> str:
    """`"5.5.1"`, or `"absent"`. For the extraction cache key, not for a decision.

    `extractor._parser_versions` records this for the same reason it records
    `xlrd`: a parser that is not in the key is a parser whose arrival does not
    invalidate anything, so installing tesseract would leave all 70 scanned
    supplements serving the cached `no_text_scanned_pdf` from the install that
    could not read them, and only `--force` would notice.

    Being in the key means installing tesseract re-extracts every article, not only
    the 70 with a scanned file in them. That is the same bluntness every other
    entry in that dict has -- the key is per article and knows nothing about which
    files are inside -- and the alternative, a key that varies with the previous
    extraction's own outcome, is not one.
    """
    if shutil.which("tesseract") is None:
        return "absent"
    try:
        out = subprocess.run(["tesseract", "--version"], capture_output=True,
                             text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    first = (out.stdout or out.stderr or "").splitlines()
    # `tesseract 5.5.1` on the first line, then leptonica and its dependencies.
    return first[0].split()[-1] if first and first[0].split() else "unknown"


def _render_with_ocr(data: bytes, limits: Limits, tessdata: str) -> bytes:
    """Render every page, OCR the image, return a PDF that has a real text layer.

    The point of returning a *PDF* rather than strings is that the result goes back
    through `blocks_from_pdf`, so OCR'd text is de-hyphenated, stripped of
    invisibles, checked for running heads and section-labelled by exactly the code
    that does it for text a publisher typeset. Two parsers for the same job would
    diverge, and the comparison between an `ok` file and an `ok_via_ocr` one is the
    thing this whole status exists to support.

    Each OCR'd page is placed back onto a page of the *original* size.
    `pdfocr_tobytes` hands back a page the size of the pixmap -- at 300 dpi four
    times the original in each direction -- and inserting that directly would
    record every `locator_ref.bbox` in a coordinate space nothing else in the
    corpus uses. `show_pdf_page` scales it back, so a bbox means the same thing on
    an OCR'd file as on any other.

    Measured over the 70 scanned supplements: 245 pages, 19.7 s of rendering at 300
    dpi, 80 ms a page, largest pixmap 54 MB and transient. Whatever this pass costs
    in practice is tesseract's time and not this.
    """
    source = fitz.open(stream=data, filetype="pdf")
    out = fitz.open()
    try:
        for page in source:
            pixmap = page.get_pixmap(dpi=limits.ocr_dpi)
            layer = fitz.open(stream=pixmap.pdfocr_tobytes(
                compress=True, language=OCR_LANGUAGE, tessdata=tessdata),
                filetype="pdf")
            try:
                target = out.new_page(width=page.rect.width, height=page.rect.height)
                target.show_pdf_page(target.rect, layer, 0)
            finally:
                layer.close()
        return out.tobytes()
    finally:
        source.close()
        out.close()


def _ocr_pass(data: bytes, source_file: str, limits: Limits, blocks: List[Block],
              meta: dict) -> Tuple[List[Block], str, dict]:
    """A PDF with no text layer -> `ok_via_ocr`, or `no_text_scanned_pdf` and why.

    `ok_via_ocr` is deliberately not `ok`. Three reasons, in the order they matter:
    OCR'd characters are a guess where a text layer is a fact, so the downstream
    perturbation stage should be free to weight a claim resting on them
    differently; `no_text_scanned_pdf` has to go on meaning "a scan nobody has read"
    rather than quietly becoming "a scan, or a scan we read, you cannot tell"; and a
    corpus-wide count of how much of the evidence came from OCR is worth being able
    to take.

    Every way this can decline leaves the file exactly as it was -- the original
    status, the original blocks, which are the handful of characters a scanned page
    does yield -- and puts the cause in `reason`, where the review queue shows it.

    Not applied to `garbled_text_encoding`, which returns before this is reached and
    is arguably the better candidate: those two files render perfectly and only
    their text layer is broken, which is precisely what OCR is for. Left alone
    because the measurement behind this pass is the 70 scanned files, and a status
    that means "the fonts do not say what their glyphs are" should not start
    sometimes meaning "and we OCR'd it anyway" without its own measurement.

    That measurement has since been taken, on both files, and it says keep the
    refusal. 10.1038/s41586-022-05670-5's MOESM3 is 55 pages, over `max_ocr_pages`,
    so this pass would decline it anyway and nothing changes. 10.1038/
    s41588-024-01702-0's MOESM2 is 3 pages and does OCR, to 7,200 characters -- and
    the document is a Nature Reporting Summary, a checkbox form, which comes back as
    `[] xX`, `Oo x`, `[__]| BX]` and boilerplate about editorial policy. So routing
    `garbled_text_encoding` here would buy nothing on one file and 7,200 characters
    of OCR'd tickbox on the other. Revisit if a garbled file turns up that is prose,
    under the page cap, and load-bearing.
    """
    pages = meta.get("pages") or 0
    if not pages:
        # A PDF that opens and declares no pages reaches the scanned branch, having
        # produced no characters. Said plainly here because the alternative is
        # `_render_with_ocr` raising `cannot save with zero pages` and this
        # returning "OCR failed", which blames the wrong thing.
        meta["reason"] = "the document declares no pages; there is nothing to OCR"
        return blocks, SCANNED, meta
    tessdata, why_not = ocr_support()
    if not tessdata:
        meta["reason"] = why_not
        return blocks, SCANNED, meta
    if pages > limits.max_ocr_pages:
        meta["reason"] = (f"{pages} pages is over the {limits.max_ocr_pages}-page OCR "
                          f"cap (`max_ocr_pages`); a scan this long is a document to "
                          f"read by hand rather than a supplementary table")
        return blocks, SCANNED, meta

    try:
        rendered = _render_with_ocr(data, limits, tessdata)
    except Exception as e:
        # One file's OCR must not cost the run, the same rule the page loop above
        # follows. A tesseract that is present and broken is a real shape: a
        # language pack half-installed answers this way.
        meta["reason"] = f"OCR failed: {type(e).__name__}: {e}"
        return blocks, SCANNED, meta

    ocr_blocks, status, ocr_meta = blocks_from_pdf(rendered, source_file, limits,
                                                   ocr=False)
    if status != OK or not ocr_blocks:
        meta["reason"] = (f"OCR at {limits.ocr_dpi} dpi found "
                          f"{ocr_meta.get('chars', 0)} characters, under "
                          f"{limits.min_pdf_text_chars}: the pages carry no legible "
                          f"text, not merely no text layer")
        meta["ocr"] = {"dpi": limits.ocr_dpi, "language": OCR_LANGUAGE,
                       "pages": pages, "chars": ocr_meta.get("chars", 0)}
        return blocks, SCANNED, meta

    # The OCR parse's own meta describes the document the blocks came from, so it
    # wins; the original parse's findings about the file on disk are kept under it.
    merged = {**meta, **ocr_meta}
    merged.pop("reason", None)
    merged["ocr"] = {"dpi": limits.ocr_dpi, "language": OCR_LANGUAGE, "pages": pages,
                     "chars": ocr_meta.get("chars", 0)}
    return ocr_blocks, OK_VIA_OCR, merged


#: How many of MuPDF's lines to keep per file, with the total kept alongside.
#:
#: Bounded because the buffer is wider than what was being printed. Only four PDFs
#: in this corpus emit an *error*, but 15 of a random 40 emit a warning -- "bogus
#: font", "freetype could not find any cmaps" -- at a median of one line, and a file
#: that goes through `_repair_glyph_encoding` provokes a run of
#: `FT_Get_Advance(...): invalid glyph index` on top: 20 lines for
#: 10.21203/rs.3.rs-7535904_v2. Five keeps the defect legible without letting font
#: chatter into 38% of records at unbounded length, and `mupdf_warnings_total` means
#: the cap is never silent about what it dropped.
_MAX_MUPDF_WARNINGS = 5


def _mupdf_warnings() -> dict:
    """What MuPDF said about the file just read, capped.

    Reading the buffer clears it -- `mupdf_warnings(reset=1)` is the default -- so
    this must be called once per file and its answer kept.
    """
    reported = (fitz.TOOLS.mupdf_warnings() or "").strip()
    if not reported:
        return {}
    lines = [line.strip() for line in reported.splitlines() if line.strip()]
    record: dict = {"mupdf_warnings": lines[:_MAX_MUPDF_WARNINGS]}
    if len(lines) > _MAX_MUPDF_WARNINGS:
        record["mupdf_warnings_total"] = len(lines)
    return record


#: A line ending in what could be a line number, and the number itself.
_TRAILING_NUMBER = re.compile(r"\s+(\d{1,4})\s*$")

#: What fraction of a document's lines must end in an ascending integer before its
#: numbering is read as line numbering rather than coincidence.
#:
#: Measured over the 124 PDF-sourced articles in this corpus. The two line-numbered
#: manuscripts score 0.90 (10.21203/rs.3.rs-7535904_v2, 1097 of 1222 lines) and 0.80
#: (10.1101/2022.05.18.492547, 729 of 910). The highest score any other article
#: reaches is 0.34 -- 10.1126/science.abo7257, whose trailing numbers are inline
#: reference markers -- and the rest sit at 0.23 and below. 0.6 is the middle of
#: that gap rather than a round number chosen for looking like one.
_LINE_NUMBER_FRACTION = 0.6

#: And they have to ascend. Both manuscripts score 1.00; this costs nothing and
#: stops a document whose lines happen to end in a figure number from qualifying on
#: the fraction alone.
_LINE_NUMBER_ASCENDING = 0.9

#: Lines shorter than this are ignored by the detector: a two-word heading tells
#: you nothing about whether the document is numbered, and there are enough of them
#: to move a fraction.
_LINE_NUMBER_MIN_CHARS = 20


def _line_numbered(per_page: List[List[Tuple[str, bool, dict]]]) -> bool:
    """Does this document carry its own line numbering on every line?

    Worth answering once per document rather than per line, because the per-line
    question has no safe answer: `Extended Data Fig. 1` and `Discussion 361` are the
    same shape, and only the rest of the document says which one is furniture.
    """
    numbers: List[int] = []
    total = 0
    for texts in per_page:
        for text, _margin, _ref in texts:
            stripped = text.strip()
            if len(stripped) < _LINE_NUMBER_MIN_CHARS:
                continue
            total += 1
            match = _TRAILING_NUMBER.search(stripped)
            if match:
                numbers.append(int(match.group(1)))
    if total < 20 or not numbers:
        return False
    if len(numbers) / total < _LINE_NUMBER_FRACTION:
        return False
    if len(numbers) < 2:
        return False
    ascending = sum(1 for a, b in zip(numbers, numbers[1:]) if b > a)
    return ascending / (len(numbers) - 1) >= _LINE_NUMBER_ASCENDING


def blocks_from_pdf(
    data: bytes, source_file: str, limits: Limits, ocr: bool = True
) -> Tuple[List[Block], str, dict]:
    """Parse one PDF. Returns `(blocks, status, meta)`.

    `no_text_scanned_pdf` is a distinct status from `no_text` on purpose: it means
    the file is the article and its pages are images, which is a different problem
    from a file that genuinely has nothing in it. Where tesseract is installed such
    a file now goes through `_ocr_pass` and comes back `ok_via_ocr`; where it is not,
    the status stands and says what to install.

    `ocr=False` is the recursion guard, and the reason the OCR pass returns a PDF
    rather than text: it renders the pages, has tesseract read them, and feeds the
    result -- which has a text layer of its own -- back through this same function,
    so OCR'd text gets exactly the treatment real text gets. Passing True on that
    inner call would OCR the OCR.
    """
    # Anything MuPDF has to say about *this* file starts here. Cleared rather than
    # appended to because the buffer is process-wide, so without this the first
    # noisy PDF in a 393-article run would be reported against every file after it.
    fitz.TOOLS.reset_mupdf_warnings()
    try:
        document = fitz.open(stream=data, filetype="pdf")
    except Exception as e:
        return [], UNREADABLE, {"reason": f"{type(e).__name__}: {e}",
                                **_mupdf_warnings()}

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
        # Read here, in the `finally`, for two reasons: every path out of the parse
        # above passes through it, and the buffer has to be emptied before
        # `_ocr_pass` re-enters this function on the rendered copy -- otherwise the
        # original's messages would be reported against the OCR'd rendition, or
        # cleared by it.
        meta.update(_mupdf_warnings())

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
    # Decided once, from the whole document, and recorded: a reader who sees
    # `methods` appear on an article that had none needs to be able to see why.
    line_numbered = _line_numbered(per_page)
    if line_numbered:
        meta["line_numbered"] = True

    def probe(text: str) -> str:
        """The text to ask the matchers about, which is not always the text stored.

        In a line-numbered manuscript every line ends in its own line number, so
        `Methods 606` is the heading `Methods`. The number is dropped for the
        question and kept in the block, because it is on the page and this stage
        does not silently rewrite what the page says.
        """
        return sections_mod.strip_line_number(text) if line_numbered else text

    def named_section(text: str) -> Optional[str]:
        """The section a heading names, by either route the loop below recognises one.

        Factored out because the summary pre-pass has to ask exactly the question
        the loop asks. Asking a narrower one -- `normalize` alone -- would miss a
        structured abstract whose headings are glued to their paragraphs, and the
        two answers drifting apart is the kind of bug that shows up as a span
        covering three of a summary's four headings.
        """
        named = sections_mod.normalize(text)
        if named:
            return named
        glued = sections_mod.split_leading_heading(text)
        return glued[0] if glued else None

    # Which headings belong to a structured abstract rather than to the paper --
    # decided from the whole first page before any of it is labelled, because the
    # evidence for the answer is the shape of the page and a single pass through it
    # only ever sees one heading at a time. Keyed by position, since a summary and
    # the paper it summarises use the same words.
    recognised = []
    for page_index, texts in enumerate(per_page, start=1):
        for item_index, (text, _margin, _ref) in enumerate(texts):
            if text in furniture or _PAGE_NUMBER.match(text):
                continue
            named = named_section(probe(text))
            if named:
                recognised.append(((page_index, item_index), page_index, named))
    summary = sections_mod.structured_abstract(recognised)
    if summary.page is not None:
        meta["structured_abstract_headings"] = len(summary.headings)

    for page_index, texts in enumerate(per_page, start=1):
        for item_index, (text, _margin, ref) in enumerate(texts):
            if text in furniture or _PAGE_NUMBER.match(text):
                meta["running_lines_dropped"] += 1
                continue
            if len(blocks) >= limits.max_blocks_per_file:
                meta["blocks_capped"] = True
                break
            locator = f"p.{page_index}"
            in_summary = (page_index, item_index) in summary.headings
            named = sections_mod.normalize(probe(text))
            if named:
                named = sections_mod.ABSTRACT if in_summary else named
                blocks.append(Block(kind=HEADING, text=text, source_file=source_file,
                                    origin="pdf", locator=locator, locator_ref=ref,
                                    section=tracker.heading(named)))
                continue
            glued = sections_mod.split_leading_heading(probe(text))
            if glued:
                named, heading, text = glued
                named = sections_mod.ABSTRACT if in_summary else named
                blocks.append(Block(kind=HEADING, text=heading, source_file=source_file,
                                    origin="pdf", locator=locator, locator_ref=ref,
                                    section=tracker.heading(named)))
                meta["glued_headings_split"] = meta.get("glued_headings_split", 0) + 1
                # MDPI glues a heading to its first subheading, so what is left over
                # is a heading too. Emitted rather than dropped: it is under
                # `min_paragraph_chars`, so the branch below would discard the text
                # entirely and the subheading would vanish from the article.
                if sections_mod.looks_like_heading(text):
                    inner = sections_mod.normalize(text)
                    blocks.append(Block(
                        kind=HEADING, text=text, source_file=source_file,
                        origin="pdf", locator=locator, locator_ref=ref,
                        # `heading` when the remainder names a section of its own and
                        # `carry` when it is a subheading, which is the same choice
                        # the two branches above make and for the same reason.
                        section=(tracker.heading(inner) if inner
                                 else tracker.carry(text))))
                    continue
            elif sections_mod.looks_like_heading(probe(text)):
                blocks.append(Block(kind=HEADING, text=text, source_file=source_file,
                                    origin="pdf", locator=locator, locator_ref=ref,
                                    section=tracker.carry(text)))
                continue
            if len(text) < limits.min_paragraph_chars:
                continue
            blocks.append(Block(kind=PARAGRAPH, text=text, source_file=source_file,
                                origin="pdf", locator=locator, locator_ref=ref,
                                section=tracker.carry(text)))
        # A summary is bounded by the page it is printed on, and the page is the
        # one thing the tracker cannot see.
        if page_index == summary.page:
            tracker.close_summary()

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
        if not ocr:
            return blocks, SCANNED, meta
        return _ocr_pass(data, source_file, limits, blocks, meta)
    return blocks, OK, meta
