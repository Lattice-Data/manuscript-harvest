"""Zip supplements -> the text-bearing files inside them.

18 supplements in this corpus are `.zip`, and some are large (one is 72 MB). The
contents are read into memory and handed to the ordinary parsers; nothing is
unpacked to disk, so an archive cannot add anything to the corpus directory.

Member names are still made safe. Nothing here writes them, but a name reaches
`source_file` on a block, and downstream code that joins that to a path should
not be handed `../../etc/passwd`.

Sizes are checked from the zip directory before reading, so an entry that claims
to expand to gigabytes is skipped rather than decompressed and then rejected.
"""

import io
import posixpath
import zipfile
from collections import Counter
from typing import List, Sequence, Tuple

from .limits import Limits


def safe_member_name(name: str) -> str:
    """Reduce an archive member path to a relative path that cannot escape."""
    cleaned = name.replace("\\", "/")
    cleaned = posixpath.normpath(cleaned)
    parts = [p for p in cleaned.split("/") if p not in {"", ".", ".."}]
    # Drop a Windows drive letter if one survived normalisation.
    if parts and len(parts[0]) == 2 and parts[0][1] == ":":
        parts = parts[1:]
    return "/".join(parts)


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
            if not name or name.startswith("__MACOSX/") or posixpath.basename(name).startswith("."):
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
