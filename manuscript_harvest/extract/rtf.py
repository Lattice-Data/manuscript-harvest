"""RTF supplements -> their text.

Three files in the 393-article corpus, 1.7 MB between them, and all three were
`unsupported_format` on the reasoning recorded in `LEGACY_DOC_EXTENSIONS`: reading
them means an external converter, and putting two system dependencies behind four
files is not worth it.

That reasoning holds for `.doc`, and does not hold here. RTF is not a binary
container -- it is 7-bit ASCII with backslash control words and brace groups, and
the whole format is a text encoding. `unrtf` is a convenience, not a requirement,
and the corpus's three files parse with the stdlib in a few hundred lines less
than that argument assumed.

What this does NOT do, deliberately: layout, styles, tables-as-tables. A `.cell`
becomes a tab and a `.row` a newline, so a table arrives as delimited text rather
than as a `TableCard`. The three real files are prose supplements -- methods and
figure legends -- and inventing table structure from RTF's row markers would be a
second parser's worth of guessing for content that has none.

The hard part of RTF is not the syntax but knowing which groups to *drop*.
`{\\*\\fonttbl ...}` and friends carry no document text, and a naive strip that
keeps them produces a page of font names before the first real sentence.
"""

import re
from typing import List, Optional, Tuple

OK = "ok"
NO_TEXT = "no_text"
UNREADABLE = "unreadable"

#: Destinations whose contents are metadata, not document text. `\\*\\` marks a
#: destination a reader may ignore wholesale, but these are ignorable whether or
#: not the producer marked them -- Word omits the star on several.
#:
#: `pict` is the one that matters most by volume: an embedded image is hex-encoded
#: inside the group, so a strip that keeps it turns a 1.5 MB file into megabytes of
#: `a3f01e...` that every downstream character count then believes is text.
_DROP_DESTINATIONS = frozenset({
    "fonttbl", "colortbl", "stylesheet", "listtable", "listoverridetable",
    "info", "pict", "object", "themedata", "colorschememapping", "latentstyles",
    "datastore", "generator", "xmlnstbl", "rsidtbl", "mmathPr", "wgrffmtfilter",
    "filetbl", "revtbl", "upr", "bkmkstart", "bkmkend", "header", "footer",
    "headerl", "headerr", "footerl", "footerr", "footnote", "shppict", "nonshppict",
    # The rest, taken from what the corpus's three files actually contain rather
    # than from the specification's list. `objdata` is the one with teeth: file
    # `17_HEP4-6-821-s003.rtf` embeds a hex-encoded ZIP in it, so leaving it in
    # put 1.4 MB of `504b0304...` into the extracted text.
    "panose", "pnseclvl", "defchp", "defpap", "pgptbl", "saveprevpict", "blipuid",
    "objclass", "objdata", "datafield", "userprops", "fldinst", "ts", "cs",
    "aftnsep", "aftnsepc", "ftnsep", "ftnsepc", "ftnalt", "atnid", "atnauthor",
    "annotation", "atnref", "atndate", "falt", "fname", "svb", "xmlopen",
})

#: Control words that stand for whitespace rather than for formatting.
_BREAKS = {"par": "\n", "line": "\n", "sect": "\n", "page": "\n",
           "tab": "\t", "cell": "\t", "row": "\n", "nestrow": "\n",
           "lquote": "\u2018", "rquote": "\u2019",
           "ldblquote": "\u201c", "rdblquote": "\u201d",
           "emdash": "\u2014", "endash": "\u2013",
           "bullet": "\u2022", "~": "\u00a0", "_": "-", "-": ""}

_TOKEN = re.compile(
    r"\\(?P<word>[a-zA-Z]{1,32})(?P<arg>-?\d{1,10})?[ ]?"   # control word
    r"|\\'(?P<hex>[0-9a-fA-F]{2})"                          # \'hh byte
    r"|\\(?P<symbol>[^a-zA-Z])"                             # \\ \{ \} \~ \- \_
    r"|(?P<brace>[{}])"
    r"|(?P<text>[^\\{}]+)"
)

#: RTF's default when `\\ansicpg` says nothing. Every file in this corpus is one of
#: these two, and cp1252 is what Word writes.
_DEFAULT_CODEPAGE = "cp1252"

_CODEPAGE = re.compile(rb"\\ansicpg(\d+)")


def _codec(data: bytes) -> str:
    match = _CODEPAGE.search(data[:4096])
    if not match:
        return _DEFAULT_CODEPAGE
    try:
        codec = f"cp{int(match.group(1))}"
        "".encode(codec)
        return codec
    except (ValueError, LookupError):
        return _DEFAULT_CODEPAGE


def rtf_to_text(data: bytes) -> str:
    """The document text of an RTF file, with groups that carry none removed.

    Single pass over the token stream with a brace-depth stack. Each frame records
    whether it is inside a dropped destination and how many characters of a
    `\\uN` replacement are still to be swallowed -- both are properties of the
    group, so they are pushed and popped with it rather than tracked globally,
    which is what keeps a `{\\*\\fonttbl ...}` nested inside a kept group from
    switching the whole rest of the document off.
    """
    codec = _codec(data)
    body = data.decode("latin-1")

    out: List[str] = []
    # (dropping, unicode_skip). A frame inherits both from its parent: text inside
    # a group nested in `{\\*\\pict ...}` is just as unwanted as text directly in it.
    stack: List[Tuple[bool, int]] = [(False, 1)]
    skip_chars = 0
    # A destination control word only names the group when it is the first thing in
    # it, so `\\pict` mid-paragraph must not silence the paragraph.
    fresh_group = False
    pending_bytes = bytearray()

    def flush() -> None:
        if pending_bytes:
            out.append(pending_bytes.decode(codec, "replace"))
            pending_bytes.clear()

    for match in _TOKEN.finditer(body):
        dropping, uc = stack[-1]
        kind = match.lastgroup

        if kind == "brace":
            flush()
            if match.group() == "{":
                stack.append((dropping, uc))
                fresh_group = True
            elif len(stack) > 1:
                stack.pop()
                fresh_group = False
            continue

        if kind == "hex":
            if not dropping:
                if skip_chars > 0:
                    skip_chars -= 1
                else:
                    pending_bytes.append(int(match.group("hex"), 16))
            fresh_group = False
            continue

        flush()

        if kind == "text":
            if not dropping:
                text = match.group("text")
                if skip_chars > 0:
                    eaten = min(skip_chars, len(text))
                    skip_chars -= eaten
                    text = text[eaten:]
                if text:
                    out.append(text.replace("\r", "").replace("\n", ""))
            fresh_group = False
            continue

        if kind == "symbol":
            symbol = match.group("symbol")
            if symbol == "*":
                # `{\*\destination ...}` -- the star says "ignorable", and the name
                # it qualifies is the *next* token. Clearing `fresh_group` here is
                # the bug that let every starred destination through: the group was
                # no longer considered fresh by the time `\xmlnstbl` arrived, so it
                # was read as an ordinary formatting word and its contents kept.
                continue
            if not dropping and symbol in {"\\", "{", "}"}:
                out.append(symbol)
            elif not dropping and symbol in _BREAKS:
                out.append(_BREAKS[symbol])
            fresh_group = False
            continue

        word = match.group("word")
        arg = match.group("arg")

        if word == "u" and arg is not None:
            if not dropping:
                point = int(arg)
                if point < 0:
                    point += 0x10000
                if 0 <= point <= 0x10FFFF:
                    out.append(chr(point))
            # The ASCII fallback that follows a `\\uN` is there for readers that
            # cannot do Unicode. Having taken the real character, drop it.
            skip_chars = uc
            fresh_group = False
            continue

        if word == "uc" and arg is not None:
            stack[-1] = (dropping, max(0, int(arg)))
            fresh_group = False
            continue

        if fresh_group and word in _DROP_DESTINATIONS:
            stack[-1] = (True, uc)
            fresh_group = False
            continue

        if not dropping and word in _BREAKS:
            out.append(_BREAKS[word])

        fresh_group = False

    flush()
    return "".join(out)


def text_from_rtf(data: bytes, limits=None) -> Tuple[str, str, dict]:
    """`(text, status, meta)` -- the shape the other parser modules answer with."""
    meta: dict = {}
    try:
        text = rtf_to_text(data)
    except Exception as e:                                  # noqa: BLE001
        # A malformed RTF is a file this stage could not read, not a crash of the
        # run: every other parser here answers `unreadable` rather than raising,
        # and an article is not worth losing over one supplement.
        return "", UNREADABLE, {"reason": f"{type(e).__name__}: {e}"}

    if not data[:5].startswith(b"{\\rt"):
        meta["reason"] = "no RTF signature; read anyway"
    collapsed = re.sub(r"[ \t]+\n", "\n", text)
    collapsed = re.sub(r"\n{3,}", "\n\n", collapsed).strip()
    meta["chars"] = len(collapsed)
    if not collapsed:
        # Worth naming rather than leaving as a bare "no text". `17_HEP4-6-821-
        # s003.rtf` is 1.48 MB of which 99% is one `{\*\objdata ...}` group -- an
        # embedded OLE spreadsheet -- and the document around it has not one
        # sentence. A reader told only `no_text` about a 1.5 MB file would
        # reasonably suspect the parser; this says where the bytes went.
        if b"objdata" in data[:2_000_000] or b"\\object" in data[:2_000_000]:
            meta["reason"] = ("the document is a wrapper around an embedded object "
                              "(OLE) and carries no text of its own")
        return "", NO_TEXT, meta
    return collapsed, OK, meta


def paragraphs(data: bytes, min_chars: int = 2,
               max_chars: Optional[int] = None) -> Tuple[List[str], str, dict]:
    """The text split the way `_plain_text_blocks` wants it."""
    text, status, meta = text_from_rtf(data)
    if status != OK:
        return [], status, meta
    out: List[str] = []
    for chunk in text.split("\n"):
        chunk = chunk.strip()
        if len(chunk) < min_chars:
            continue
        if max_chars and len(chunk) > max_chars:
            chunk = chunk[:max_chars]
        out.append(chunk)
    meta["paragraphs"] = len(out)
    return out, (OK if out else NO_TEXT), meta
