"""Archive supplements -> the text-bearing files inside them.

Three containers, one contract. `read_members` reads a zip, `read_tar_members` a
tar, and `decompress` unwraps a single-file `.gz`/`.bz2`/`.xz` so the ordinary
dispatcher can look at what was in it. The first two return the same
`(members, meta)` pair, so `extractor._extract_archive` does not need to know which
it was handed.

Measured over the 393-article corpus: 124 archive supplements. 118 are `.zip` and
one of those is 1.4 GB. The other six are what the tar and gzip paths were added
for -- five `.gz` (73 MB on disk, 571 MB decompressed) and one 34 MB `.tgz` -- and
every one of them was reported `unsupported_format` with the note "compressed
archive other than zip; decompress by hand if it holds tables" until they were.

The contents are read into memory and handed to the ordinary parsers; nothing is
unpacked to disk, so an archive cannot add anything to the corpus directory.

Member names are still made safe. Nothing here writes them, but a name reaches
`source_file` on a block, and downstream code that joins that to a path should
not be handed `../../etc/passwd`. This is where this module and
`fetch/sources/pmc_oa.py::_unpack_tgz` part company, having the same job
otherwise: that tier *writes* members, so it reduces every name to its basename;
nothing here writes, so a member keeps its inner path for provenance and
`safe_member_name` is what makes that safe.

Sizes are checked before anything is decompressed -- from the zip directory, the
tar member header, or gzip's ISIZE trailer -- so an entry that claims to expand to
gigabytes is skipped rather than decompressed and then rejected. Where the claim
cannot be trusted the read is bounded as well: ISIZE is modulo 2**32 and describes
only the last member of a multi-member file, so a header that lies costs one cap's
worth of memory rather than all of it.
"""

import bz2
import io
import lzma
import posixpath
import tarfile
import zipfile
import zlib
from collections import Counter
from typing import Callable, List, Optional, Sequence, Tuple

from .limits import Limits

#: The statuses `decompress` can answer with, re-declared here the way every other
#: parser module in this package re-declares the ones it returns. `extractor` holds
#: the closed set; nothing imports it from there because it imports this.
OK = "ok"
UNSUPPORTED = "unsupported_format"
TOO_LARGE = "too_large"
UNREADABLE = "unreadable"


def safe_member_name(name: str) -> str:
    """Reduce an archive member path to a relative path that cannot escape."""
    cleaned = name.replace("\\", "/")
    cleaned = posixpath.normpath(cleaned)
    parts = [p for p in cleaned.split("/") if p not in {"", ".", ".."}]
    # Drop a Windows drive letter if one survived normalisation.
    if parts and len(parts[0]) == 2 and parts[0][1] == ":":
        parts = parts[1:]
    return "/".join(parts)


def _is_junk(name: str) -> bool:
    """A member the container's own tooling added, not content.

    Load-bearing rather than defensive on the tar side: 176 of the 296 members of
    10.1038/s41586-020-03182-8's MOESM4 are AppleDouble forks and `.DS_Store`
    files, so the archive reads as 296 files of which 120 are real.
    """
    return (not name or name.startswith("__MACOSX/")
            or posixpath.basename(name).startswith("."))


def read_members(
    data: bytes, limits: Limits, wanted_extensions: Sequence[str]
) -> Tuple[List[Tuple[str, bytes]], dict]:
    """Return `[(member_name, member_bytes), ...]` for readable, wanted members."""
    meta: dict = {"members_total": 0, "members_read": 0, "skipped": []}
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, OSError) as e:
        meta["reason"] = f"{type(e).__name__}: {e}"
        return [], meta

    wanted = {e.lower() for e in wanted_extensions}
    max_member_bytes = limits.max_member_mb * 1024 * 1024
    out: List[Tuple[str, bytes]] = []
    try:
        entries = [i for i in archive.infolist() if not i.is_dir()]
        meta["members_total"] = len(entries)
        census = Counter()
        for info in entries:
            name = safe_member_name(info.filename)
            if _is_junk(name):
                continue
            extension = posixpath.splitext(name)[1].lower()
            census[extension or "none"] += 1
            if extension not in wanted:
                meta["skipped"].append({"name": name, "reason": f"extension {extension or 'none'}"})
                continue
            if info.file_size > max_member_bytes:
                meta["skipped"].append({
                    "name": name,
                    "reason": f"{info.file_size} bytes is over the "
                              f"{limits.max_member_mb} MB member cap"})
                continue
            if len(out) >= limits.max_archive_members:
                meta["skipped"].append({"name": name, "reason": "member cap reached"})
                continue
            try:
                out.append((name, archive.read(info)))
            except (zipfile.BadZipFile, OSError, RuntimeError) as e:
                meta["skipped"].append({"name": name, "reason": f"{type(e).__name__}: {e}"})
        meta["member_extensions"] = dict(sorted(census.items()))
    finally:
        archive.close()

    meta["members_read"] = len(out)
    return out, meta


def looks_like_tar(data: bytes) -> bool:
    """Whether these bytes are a tar, compressed or not. Content, not suffix.

    The name is wrong in both directions in this corpus, so it cannot be the test.
    10.1038/s41586-020-03182-8's `MOESM4_ESM.tgz` is a *plain* tar that nobody
    compressed -- 296 members, `.tgz` name -- and 10.1126/science.adf5357 ships
    three `.gz` files that are one CSV each rather than an archive of many.

    `mode="r:*"` is the difference: `fetch/sources/pmc_oa.py::_unpack_tgz` opens
    `"r:gz"`, which is right where PMC produced the package and reads nothing at
    all from the one such file a publisher sent.

    Cheap on both answers, which is why it can be asked before anything else:
    tarfile validates the first member header and stops. Measured on the two real
    files, 38 MB and 36 MB, at 1 ms and under.
    """
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*"):
            return True
    except (tarfile.TarError, OSError, EOFError, ValueError):
        return False


def read_tar_members(
    data: bytes, limits: Limits, wanted_extensions: Sequence[str]
) -> Tuple[List[Tuple[str, bytes]], dict]:
    """`read_members` for a tarball. Same contract, same caps, no directory.

    A tar has no central directory, and that costs two things a zip does not pay.
    `members_total` is knowable only by walking, and the walk *is* the expense:
    reading the Nth header of a compressed tar means decompressing everything
    before it, so an archive that declares more than `max_file_mb` in members stops
    being walked and `walk_stopped` says where. Without that bound a 1 KB tar.gz
    claiming 10 GB of members would be read to the end before any cap applied.

    `isfile()` is doing more work here than `is_dir()` does for a zip: it also
    refuses symlinks, hardlinks and device nodes, which is the guard `_unpack_tgz`
    documents at length. An oversize member is skipped and recorded where that
    function raises and loses the whole package -- it is paying for one HTTP
    transfer and a member it cannot read makes the download worthless, and nothing
    here is paying for anything.
    """
    meta: dict = {"members_total": 0, "members_read": 0, "skipped": []}
    try:
        archive = tarfile.open(fileobj=io.BytesIO(data), mode="r:*")
    except (tarfile.TarError, OSError, EOFError, ValueError) as e:
        meta["reason"] = f"{type(e).__name__}: {e}"
        return [], meta

    wanted = {e.lower() for e in wanted_extensions}
    max_member_bytes = limits.max_member_mb * 1024 * 1024
    walk_budget = limits.max_file_mb * 1024 * 1024
    out: List[Tuple[str, bytes]] = []
    census = Counter()
    declared = 0
    try:
        for info in archive:
            if not info.isfile():
                continue
            declared += info.size
            if declared > walk_budget:
                meta["walk_stopped"] = (
                    f"members up to {safe_member_name(info.name) or '?'} declare "
                    f"{declared} bytes, over the {limits.max_file_mb} MB cap; the "
                    f"rest of the archive was not walked")
                break
            meta["members_total"] += 1
            name = safe_member_name(info.name)
            if _is_junk(name):
                continue
            extension = posixpath.splitext(name)[1].lower()
            census[extension or "none"] += 1
            if extension not in wanted:
                meta["skipped"].append({"name": name,
                                        "reason": f"extension {extension or 'none'}"})
                continue
            if info.size > max_member_bytes:
                meta["skipped"].append({
                    "name": name,
                    "reason": f"{info.size} bytes is over the "
                              f"{limits.max_member_mb} MB member cap"})
                continue
            if len(out) >= limits.max_archive_members:
                meta["skipped"].append({"name": name, "reason": "member cap reached"})
                continue
            try:
                handle = archive.extractfile(info)
                if handle is None:
                    continue
                out.append((name, handle.read()))
            except (tarfile.TarError, OSError, EOFError, ValueError) as e:
                meta["skipped"].append({"name": name, "reason": f"{type(e).__name__}: {e}"})
        meta["member_extensions"] = dict(sorted(census.items()))
    finally:
        archive.close()

    meta["members_read"] = len(out)
    return out, meta


#: Magic bytes -> the name of the format and an incremental decompressor for it.
#: All three are standard library, which is the reason `.7z` and `.rar` are still
#: refused: they would each be a third-party dependency for zero files here.
_COMPRESSORS: Tuple[Tuple[bytes, str, Callable], ...] = (
    (b"\x1f\x8b", "gzip", lambda: zlib.decompressobj(16 + zlib.MAX_WBITS)),
    (b"BZh", "bzip2", bz2.BZ2Decompressor),
    (b"\xfd7zXZ\x00", "xz", lzma.LZMADecompressor),
)


def compression_of(data: bytes) -> str:
    """`"gzip"`, `"bzip2"`, `"xz"`, or `""` for none of them, by magic bytes.

    Read rather than inferred from the suffix because `extractor.sniff_extension`
    used to answer `.gz` for a bzip2 file, which was harmless only for as long as
    nothing decompressed either.
    """
    for magic, name, _ in _COMPRESSORS:
        if data.startswith(magic):
            return name
    return ""


def gzip_declared_size(data: bytes) -> Optional[int]:
    """What a gzip's ISIZE trailer claims it expands to, or None if it is not one.

    Not the verdict on anything, and that is a correction rather than caution. This
    began as `decompress`'s size check -- four bytes to refuse 329 MB of TSV without
    touching it -- and it is wrong in both directions often enough that it cannot
    be. ISIZE is modulo 2**32, so anything over 4 GB understates by whole multiples
    of it, and it describes only the *last* member of a multi-member file, of which
    bgzip writes thousands and this corpus is genomics supplements. Worse in the
    other direction: in a truncated file the last four bytes are not the trailer at
    all, they are deflate, so a failed download reads as an arbitrary size and had
    every chance of being called `too_large` instead of the broken file it is.

    What it is good for is the *reason*. The three over-cap `.gz` supplements here
    declare 128 MB, 68 MB and 329 MB honestly, and "expands to more than 50 MB" does
    not tell a curator what to raise `max_member_mb` to while "declares 329019662
    bytes" does.
    """
    if not data.startswith(b"\x1f\x8b") or len(data) < 20:
        # 10-byte header, at least 2 of deflate, 8-byte trailer. Below that the
        # bytes being read as a trailer are certainly part of the header.
        return None
    return int.from_bytes(data[-4:], "little")


def decompress(data: bytes, limits: Limits) -> Tuple[Optional[bytes], str, dict]:
    """A single-file `.gz`/`.bz2`/`.xz` -> the bytes inside it.

    `(payload, status, meta)`, as every parser in this package answers, and `None`
    with `meta["reason"]` set when there is nothing to hand on. The status is the
    part worth being careful about: a file refused for size is `too_large` and not
    `unsupported_format`, because the two say different things to whoever reads the
    review queue. There is a parser now, and `max_member_mb` in config.yaml is all
    that stands between it and 329 MB of TSV -- where `unsupported_format` would
    claim this stage cannot read the file at all, which is what it did say about all
    six of these files until now.

    `max_member_mb` is the cap, read the way its own docstring asks to be read: the
    wrapper is the container and what is inside it is the member, so "one 500 MB
    member should not be read because its container was small" is this case
    exactly. 10.1126/science.adf5357's Table_7 is 38 MB on disk and 329 MB of TSV;
    two of the five `.gz` supplements here fit under the cap and three do not.

    Bounded by asking for one byte more than the cap allows: if that byte arrives
    the file is over. That read is the only thing deciding the size, because the
    cheap alternative could not -- see `gzip_declared_size` for why a header is a
    reason and not a verdict. The cost of being right is decompressing one cap's
    worth, 50 MB, of a file that turns out to be 329 MB.

    A member that ends early with the stream unfinished is a truncated download and
    is reported as one rather than as short content.

    Trailing bytes after the first member are decompressed too, since a
    multi-member gzip is one file to everything that reads it. If they turn out not
    to be a member at all -- padding, a second file concatenated by a publisher's
    script -- what was already read is kept and `trailing_bytes` records the rest,
    because the alternative is discarding a table over junk after the end of it.
    """
    name = compression_of(data)
    if not name:
        return None, UNSUPPORTED, {"reason": "not a gzip, bzip2 or xz stream"}

    meta: dict = {"compression": name}
    cap = limits.max_member_mb * 1024 * 1024
    claimed = gzip_declared_size(data)

    factory = next(f for _, n, f in _COMPRESSORS if n == name)
    chunks: List[bytes] = []
    total = 0
    pending = data
    while pending:
        engine = factory()
        try:
            plain = engine.decompress(pending, cap + 1 - total)
        except (zlib.error, OSError, EOFError, ValueError) as e:
            if not chunks:
                return None, UNREADABLE, {**meta, "reason": f"{type(e).__name__}: {e}"}
            meta["trailing_bytes"] = len(pending)
            break
        chunks.append(plain)
        total += len(plain)
        if total > cap:
            reason = (f"expands to more than the {limits.max_member_mb} MB member cap "
                      f"(`max_member_mb`)")
            if claimed is not None and claimed > cap:
                reason += f"; the gzip trailer declares {claimed} bytes"
            return None, TOO_LARGE, {**meta, "reason": reason}
        if not engine.eof:
            # Fewer bytes than asked for and the stream never ended: the input ran
            # out mid-member, so the file on disk is incomplete.
            if not total:
                return None, UNREADABLE, {
                    **meta, "reason": "the compressed stream is truncated"}
            meta["truncated_stream"] = True
            break
        pending = engine.unused_data

    meta["decompressed_bytes"] = total
    return b"".join(chunks), OK, meta


def inner_name(outer: str, data: bytes) -> str:
    """What the file inside a single-file wrapper is called.

    gzip stores the original name in its header whenever the compressor was asked
    to keep it, and three of the five `.gz` supplements here did: `meta.csv`,
    `cre.csv`, `table-S4-cell-type-taxonomy.tsv`. That is the whole difference
    between dispatching to the CSV parser and having to sniff -- and the two that
    stored nothing are `02_media-4.gz` and `24_media-6.gz`, whose names with `.gz`
    removed have no extension at all, so sniffing is what happens for them.

    A stored name is trusted exactly as far as a name on disk is, which is the
    order `extract_bytes` already applies: an extension decides and magic bytes are
    the fallback where there is no extension to read. The cost of a stored name
    that lies is one parser reporting `no_text` for a file it was handed; the cost
    of ignoring it is two of these three files going to the sniffer for no reason.
    """
    stored = ""
    if data.startswith(b"\x1f\x8b") and len(data) > 10 and data[3] & 0x08:
        end = data.find(b"\0", 10)
        if end > 10:
            stored = data[10:end].decode("latin-1", errors="replace")
    if stored:
        cleaned = posixpath.basename(safe_member_name(stored))
        if cleaned:
            return cleaned
    base = posixpath.basename(safe_member_name(outer))
    stem = posixpath.splitext(base)[0]
    return stem or base
