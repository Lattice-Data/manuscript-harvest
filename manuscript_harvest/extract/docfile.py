"""Legacy Word (`.doc`, Word 97-2003) supplements -> their text.

One file in the 393-article corpus, and it is 23.3 MB of which almost all is
embedded images: `10.1002/pros.24020`'s supplementary information, about 96,000
characters of prose behind an OLE2 container. It was `unsupported_format` on the
reasoning in `LEGACY_DOC_EXTENSIONS` -- reading it means a system converter --
which is the same argument RTF was refused on and is more nearly true here.

Written with the stdlib rather than with `olefile` because this package has five
hard dependencies and the bar for a sixth is a measurement: `xlrd` earned its
place on 56 files and 129 MB. One file does not, and the format's two hard parts
are both bounded.

**Why this is not a `strings` pass.** Word keeps field codes in the text stream,
not beside it, so the naive extraction of this exact file also yields
`ADDIN EN.CITE <EndNote><Cite><Author>Landini</Author>...` between its sentences
-- 25 of 134 runs -- and loses paragraph order, since the stream is stored in
pieces that the piece table puts in reading order. Both are fixed by reading the
format rather than scanning it:

* the **piece table** (`CLX` in the `1Table` stream) says which ranges of the
  `WordDocument` stream are text and in what order, and whether each is cp1252 or
  UTF-16;
* **field characters** delimit codes in-band -- `0x13` begins a field, `0x14`
  separates its instruction from its result, `0x15` ends it -- so the instruction
  is skipped and the result kept, which is what a reader sees on the page.

What this does not do: styles, tables-as-tables, footnotes as anything but text.
The one real file is prose.
"""

import struct
from typing import Dict, List, Optional, Tuple

OK = "ok"
NO_TEXT = "no_text"
UNREADABLE = "unreadable"

_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

#: Sector chain terminators in the FAT.
_FREE = 0xFFFFFFFF
_END_OF_CHAIN = 0xFFFFFFFE

#: Streams smaller than this live in the mini stream, in 64-byte mini sectors,
#: rather than having sectors of their own. `1Table` is often under it.
_MINI_CUTOFF = 4096
_MINI_SECTOR = 64

#: In-band field delimiters. The instruction between BEGIN and SEPARATOR is what
#: carries `ADDIN EN.CITE`; the result between SEPARATOR and END is what a reader
#: sees. A field with no separator has no result and contributes nothing.
_FIELD_BEGIN = 0x13
_FIELD_SEPARATOR = 0x14
_FIELD_END = 0x15

#: Characters Word uses structurally that are not text. `0x07` ends a table cell
#: and row, so it becomes a tab; `0x0D` ends a paragraph.
_TRANSLATE = {0x07: "\t", 0x0D: "\n", 0x0B: "\n", 0x0C: "\n", 0x1E: "-", 0x1F: "",
              0x01: "", 0x02: "", 0x05: "", 0x08: "", 0x13: "", 0x14: "", 0x15: ""}


class _Ole:
    """The slice of a compound file this needs: named streams, read whole."""

    def __init__(self, data: bytes):
        if not data.startswith(_OLE_MAGIC):
            raise ValueError("not an OLE2 compound file")
        self.data = data
        self.sector_size = 1 << struct.unpack_from("<H", data, 0x1E)[0]
        self.mini_sector_size = 1 << struct.unpack_from("<H", data, 0x20)[0]
        self._fat = self._read_fat()
        self._dir = self._read_directory()
        self._mini_fat = self._read_chain_values(
            struct.unpack_from("<I", data, 0x3C)[0])
        root = self._dir.get("Root Entry")
        self._mini_stream = self._read_chain(root[1], root[2]) if root else b""

    # -- sectors ---------------------------------------------------------

    def _offset(self, sector: int) -> int:
        return (sector + 1) * self.sector_size

    def _sector(self, sector: int) -> bytes:
        start = self._offset(sector)
        return self.data[start:start + self.sector_size]

    def _read_fat(self) -> List[int]:
        """The FAT, assembled from the DIFAT. 109 entries are in the header.

        The DIFAT's own continuation chain is followed too. It is only reached by a
        file over about 6.8 MB of *sectors*, which the one real file here is --
        23.3 MB -- so this is load-bearing rather than defensive.
        """
        sectors = [s for s in struct.unpack_from("<109I", self.data, 0x4C)
                   if s not in (_FREE, _END_OF_CHAIN)]
        next_difat = struct.unpack_from("<I", self.data, 0x44)[0]
        per = self.sector_size // 4
        seen = set()
        while next_difat not in (_FREE, _END_OF_CHAIN) and next_difat not in seen:
            seen.add(next_difat)
            block = self._sector(next_difat)
            if len(block) < self.sector_size:
                break
            entries = struct.unpack_from(f"<{per}I", block, 0)
            sectors.extend(s for s in entries[:-1]
                           if s not in (_FREE, _END_OF_CHAIN))
            next_difat = entries[-1]

        fat: List[int] = []
        for sector in sectors:
            block = self._sector(sector)
            if len(block) < self.sector_size:
                break
            fat.extend(struct.unpack_from(f"<{per}I", block, 0))
        return fat

    def _walk(self, start: int) -> List[int]:
        """Sector numbers from `start` to the end of its chain.

        Bounded by `seen`: a malformed FAT with a cycle would otherwise spin
        forever on a file this is meant to answer `unreadable` about.
        """
        out: List[int] = []
        seen = set()
        current = start
        while current not in (_FREE, _END_OF_CHAIN) and current < len(self._fat):
            if current in seen:
                break
            seen.add(current)
            out.append(current)
            current = self._fat[current]
        return out

    def _read_chain(self, start: int, size: int) -> bytes:
        out = bytearray()
        for sector in self._walk(start):
            out.extend(self._sector(sector))
            if len(out) >= size:
                break
        return bytes(out[:size])

    def _read_chain_values(self, start: int) -> List[int]:
        raw = b"".join(self._sector(s) for s in self._walk(start))
        return list(struct.unpack_from(f"<{len(raw) // 4}I", raw, 0)) if raw else []

    def _read_mini(self, start: int, size: int) -> bytes:
        out = bytearray()
        current = start
        seen = set()
        while current not in (_FREE, _END_OF_CHAIN) and current < len(self._mini_fat):
            if current in seen:
                break
            seen.add(current)
            offset = current * _MINI_SECTOR
            out.extend(self._mini_stream[offset:offset + _MINI_SECTOR])
            if len(out) >= size:
                break
            current = self._mini_fat[current]
        return bytes(out[:size])

    # -- directory -------------------------------------------------------

    def _read_directory(self) -> Dict[str, Tuple[int, int, int]]:
        """`{name: (type, start_sector, size)}` for every entry."""
        first = struct.unpack_from("<I", self.data, 0x30)[0]
        raw = b"".join(self._sector(s) for s in self._walk(first))
        out: Dict[str, Tuple[int, int, int]] = {}
        for base in range(0, len(raw) - 127, 128):
            length = struct.unpack_from("<H", raw, base + 64)[0]
            if not 0 < length <= 64:
                continue
            name = raw[base:base + length - 2].decode("utf-16-le", "replace")
            entry_type = raw[base + 66]
            start = struct.unpack_from("<I", raw, base + 116)[0]
            size = struct.unpack_from("<Q", raw, base + 120)[0]
            if name:
                out[name] = (entry_type, start, size)
        return out

    def stream(self, name: str) -> bytes:
        entry = self._dir.get(name)
        if entry is None:
            raise KeyError(name)
        _type, start, size = entry
        if size < _MINI_CUTOFF and name != "Root Entry":
            return self._read_mini(start, size)
        return self._read_chain(start, size)


def _piece_table(table: bytes, fc_clx: int, lcb_clx: int) -> List[Tuple[int, bool, int]]:
    """`[(file_offset, is_utf16, char_count), ...]` in reading order.

    The CLX is a run of properties (`0x01`, each with a 2-byte length to skip) and
    then one `0x02` block holding the piece table: a character-position array of
    `n + 1` entries followed by `n` 8-byte descriptors. Bit 30 of a descriptor's
    offset word is *clear* for UTF-16; when it is set the offset must be halved and
    the text is cp1252. That inversion is the part worth stating, because getting
    it backwards yields plausible-looking mojibake rather than an error.
    """
    clx = table[fc_clx:fc_clx + lcb_clx]
    index = 0
    while index < len(clx) and clx[index] == 0x01:
        skip = struct.unpack_from("<H", clx, index + 1)[0]
        index += 3 + skip
    if index >= len(clx) or clx[index] != 0x02:
        return []
    size = struct.unpack_from("<I", clx, index + 1)[0]
    body = clx[index + 5:index + 5 + size]
    count = (len(body) - 4) // 12
    if count <= 0:
        return []

    positions = struct.unpack_from(f"<{count + 1}I", body, 0)
    pieces: List[Tuple[int, bool, int]] = []
    for n in range(count):
        base = 4 * (count + 1) + n * 8
        word = struct.unpack_from("<I", body, base + 2)[0]
        compressed = bool(word & 0x40000000)
        offset = (word & 0x3FFFFFFF) // 2 if compressed else (word & 0x3FFFFFFF)
        pieces.append((offset, not compressed, positions[n + 1] - positions[n]))
    return pieces


def _fc_clx(doc: bytes) -> Tuple[int, int]:
    """Where the piece table lives, from the FIB's variable-length arrays.

    `FibRgFcLcb97` is reached by walking past `rgW97` and `rgLw97`, each of whose
    lengths the FIB declares -- so the offsets are read rather than assumed, which
    is what keeps this working on the several `nFib` values in the wild. `fcClx` is
    the 34th pair in that array.
    """
    csw = struct.unpack_from("<H", doc, 0x20)[0]
    after_rgw = 0x20 + 2 + csw * 2
    cslw = struct.unpack_from("<H", doc, after_rgw)[0]
    after_rglw = after_rgw + 2 + cslw * 4
    rg_fc_lcb = after_rglw + 2
    base = rg_fc_lcb + 33 * 8
    return struct.unpack_from("<II", doc, base)


def _decode_pieces(doc: bytes, pieces) -> str:
    """Pieces -> text, with field instructions dropped and their results kept."""
    out: List[str] = []
    # Depth rather than a flag: Word nests fields, and a `0x15` inside a nested
    # instruction would otherwise switch the outer one back on.
    in_instruction = 0
    for offset, utf16, chars in pieces:
        if chars <= 0:
            continue
        if utf16:
            raw = doc[offset:offset + chars * 2]
            text = raw.decode("utf-16-le", "replace")
        else:
            text = doc[offset:offset + chars].decode("cp1252", "replace")
        for ch in text:
            code = ord(ch)
            if code == _FIELD_BEGIN:
                in_instruction += 1
                continue
            if code == _FIELD_SEPARATOR:
                in_instruction = max(0, in_instruction - 1)
                continue
            if code == _FIELD_END:
                in_instruction = max(0, in_instruction - 1)
                continue
            if in_instruction:
                continue
            out.append(_TRANSLATE.get(code, ch) if code < 0x20 else ch)
    return "".join(out)


def doc_to_text(data: bytes) -> str:
    """The document text of a Word 97-2003 file."""
    ole = _Ole(data)
    doc = ole.stream("WordDocument")
    if len(doc) < 0x60:
        return ""
    flags = struct.unpack_from("<H", doc, 0x0A)[0]
    if not flags & 0x0004:
        # `fComplex` clear means one contiguous run rather than a piece table.
        # Rare in practice and cheap to honour.
        fc_min, fc_mac = struct.unpack_from("<II", doc, 0x18)
        return _decode_pieces(doc, [(fc_min, False, max(0, fc_mac - fc_min))])

    fc_clx, lcb_clx = _fc_clx(doc)
    for name in ("1Table", "0Table"):
        try:
            table = ole.stream(name)
        except KeyError:
            continue
        pieces = _piece_table(table, fc_clx, lcb_clx)
        if pieces:
            return _decode_pieces(doc, pieces)
    return ""


def text_from_doc(data: bytes, limits=None) -> Tuple[str, str, dict]:
    """`(text, status, meta)` -- the shape the other parser modules answer with."""
    import re

    meta: dict = {}
    try:
        text = doc_to_text(data)
    except Exception as e:                                  # noqa: BLE001
        # An article is not worth losing over one supplement; every parser in this
        # package answers rather than raising.
        return "", UNREADABLE, {"reason": f"{type(e).__name__}: {e}"}

    collapsed = re.sub(r"[ \t]+\n", "\n", text)
    collapsed = re.sub(r"\n{3,}", "\n\n", collapsed).strip()
    meta["chars"] = len(collapsed)
    if not collapsed:
        return "", NO_TEXT, meta
    return collapsed, OK, meta


def paragraphs(data: bytes, min_chars: int = 2,
               max_chars: Optional[int] = None) -> Tuple[List[str], str, dict]:
    text, status, meta = text_from_doc(data)
    if status != OK:
        return [], status, meta
    out: List[str] = []
    for chunk in text.split("\n"):
        chunk = chunk.strip()
        if len(chunk) < min_chars:
            continue
        out.append(chunk[:max_chars] if max_chars else chunk)
    meta["paragraphs"] = len(out)
    return out, (OK if out else NO_TEXT), meta
