"""Corpus layout and manifest.

    corpus/<doi_slug>/
        manifest.json
        fulltext.pdf
        fulltext.nxml            when Europe PMC, bioRxiv or an OA package had JATS
        supplementary/01_...     original filenames, sanitised and ordered
        landing.html             browser tier only, for adapter debugging

One record per article that says exactly where every byte came from, including
the attempts that failed. A reader holding a manifest can tell which tier
produced the PDF, whether the publisher claimed supplements existed, and what
went wrong if some are missing. Downstream stages are entitled to trust it, so
it records refusals as carefully as successes.
"""

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from . import __version__
from .identifiers import doi_slug

MANIFEST_NAME = "manifest.json"
SUPPLEMENT_DIR = "supplementary"
MEDIA_DIR = "media"
FULLTEXT_PDF = "fulltext.pdf"
FULLTEXT_XML = "fulltext.nxml"
LANDING_HTML = "landing.html"

_MAX_FILENAME = 120
_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]")


def article_dir(corpus_dir, doi: str) -> Path:
    return Path(corpus_dir).expanduser() / doi_slug(doi)


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sanitize_filename(name: str, fallback: str = "file") -> str:
    """Reduce an arbitrary supplement filename to something safe to write.

    Publisher filenames arrive with spaces, unicode, URL query strings, and
    occasionally path separators. Only the basename survives, so a crafted
    `../../etc/passwd` cannot escape the corpus directory.
    """
    # Drop any query string or fragment, then keep only the final path segment.
    candidate = re.split(r"[?#]", name or "")[0]
    candidate = candidate.replace("\\", "/").rstrip("/")
    candidate = candidate.rsplit("/", 1)[-1]
    candidate = _SAFE_CHARS.sub("-", candidate).strip("-.")

    if not candidate:
        return fallback

    if len(candidate) > _MAX_FILENAME:
        stem, dot, extension = candidate.rpartition(".")
        if dot and len(extension) <= 8:
            keep = _MAX_FILENAME - len(extension) - 1
            candidate = f"{stem[:keep]}.{extension}"
        else:
            candidate = candidate[:_MAX_FILENAME]

    return candidate


def supplement_filename(index: int, name: str) -> str:
    """Prefix with the retrieval order so listings stay stable and unambiguous."""
    return f"{index:02d}_{sanitize_filename(name, fallback=f'supplement{index:02d}')}"


def read_manifest(directory) -> Optional[dict]:
    path = Path(directory) / MANIFEST_NAME
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def write_manifest(directory, record: dict) -> Path:
    """Write the manifest, atomically: either the old record or the new one.

    A plain `write_text` truncates the file before it writes, so a process that dies
    mid-write leaves a half-written manifest -- and a manifest is not a cache. It is
    the only record of where every byte of the article came from, and `read_manifest`
    answers `None` for one that will not parse, at which point the article reads as
    never fetched. Writing to a sibling temp file and `os.replace`-ing it makes the
    swap a single rename, which POSIX guarantees is atomic within a filesystem, so a
    reader sees one whole record or the other and never a truncated one.

    Cheap insurance that was never load-bearing while a fetch wrote the manifest once
    per article, and became so when `drop_media` started writing it once per file
    deleted -- about eighteen times per swept article -- to keep the file and its
    entry from ever disagreeing. That multiplied the exposure by the same factor.
    """
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    target = path / MANIFEST_NAME
    body = json.dumps(record, indent=2, ensure_ascii=False)
    # Same directory, so the rename cannot cross a filesystem boundary. The `.tmp`
    # name is per-manifest rather than per-process: two concurrent writers to one
    # article would be a bug elsewhere, and a leftover from a kill is overwritten
    # by the next write rather than accumulating.
    staging = path / (MANIFEST_NAME + ".tmp")
    staging.write_text(body, encoding="utf-8")
    os.replace(staging, target)
    return target


def manifest_is_complete(record: Optional[dict]) -> bool:
    """True when this article needs no further fetching.

    A `complete` status is not trusted on its own: if the PDF was deleted or the
    directory was partially copied, the fetch should run again rather than
    report a cached success.

    `evicted` is the exception. Those bytes were removed deliberately to stay
    inside the size budget, and the manifest still records what was there.
    Treating eviction as "incomplete" would make the next batch re-download
    everything the budget just freed, thrash against the cap, and never settle.
    Use `--force` to deliberately re-fetch an evicted article.

    **The loop at the bottom is why a policy removal must not keep its `path`.** It
    is the reason `mark_entry_removed` moves the path to `name`: a supplementary
    entry that names a file which does not exist makes the article incomplete
    forever, so `drop-media` over the 138 articles that hold a figure would undo
    itself on the next batch -- re-downloading every removed file, then removing it
    again. There is no status to short-circuit on the way `evicted` does, because
    only part of the article is gone and the rest of it really is complete. The entry
    keeps `name`, `bytes` and `sha256`, which is the record, and drops the one key
    this function reads.
    """
    if record and record.get("status") == "evicted":
        return True
    if not record or record.get("status") != "complete":
        return False
    directory = Path(record.get("_directory", "")) if record.get("_directory") else None
    if directory is None:
        # Defensive, and deliberately kept although no caller reaches it today:
        # `_directory` is injected at run time by the fetcher, not stored in the
        # manifest and not added by `read_manifest`, so any future caller reading a
        # record off disk and passing it straight here arrives without one. Note what
        # this answers in that case -- complete, on the record alone, with no file
        # checked. Reading `record["_directory"]` instead would turn it into a
        # KeyError on exactly that input, which is why the guard stays.
        return True
    for entry in [record.get("fulltext") or {}] + list(record.get("supplementary") or []):
        relative = entry.get("path")
        if relative and not (directory / relative).exists():
            return False
    return True


def save_file(directory, relative_path: str, content: bytes) -> dict:
    """Write bytes into the article directory and describe what was written."""
    target = Path(directory) / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return {
        "path": relative_path,
        "bytes": len(content),
        "sha256": sha256_bytes(content),
    }


def new_record(ids) -> dict:
    """Start a manifest for one article."""
    return {
        "doi": ids.doi,
        "doi_raw": ids.doi_raw,
        "slug": doi_slug(ids.doi),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "fetcher_version": __version__,
        "identifiers": ids.to_dict(),
        "fulltext": {"status": "not_found", "path": None},
        "fulltext_xml": None,
        "supplementary": [],
        "supplementary_status": "unknown_none_found",
        "tiers_tried": [],
        "attempts": [],
        "problems": list(ids.problems),
        "status": "failed",
    }


#: Supplement statuses that need no further fetching. `fetched_unverified` is in
#: here deliberately: the set is unbounded, not incomplete, and re-running would
#: scrape the same page and reach the same answer. Leaving it out would make
#: every batch re-download every browser-tier article forever and thrash against
#: the size budget -- the same trap `evicted` exists to avoid in
#: `manifest_is_complete`. This is the only definition; read it from here rather
#: than restating the set, so the two cannot drift apart.
#:
#: `none_text_bearing` is in here for the same reason and it is the newest member:
#: `fetch.text_bearing_only` refused every supplement this article has because no
#: text can be extracted from any of them (see the legend in `fetcher`). Nothing is
#: missing, nothing failed, and a re-run applies the identical rule to the identical
#: names -- so an unsettled verdict would re-list and re-refuse the 138 articles in
#: this corpus that hold such a file, on every batch, forever.
SUPPL_SETTLED = {"none_listed", "fetched", "fetched_unverified", "none_text_bearing"}

#: `fulltext.status` values that mean the PDF is on disk and usable.
#: `scanned_pdf_suspected` is in here because the file *is* the article -- it needs
#: OCR, which is a separate problem from not having it. Same rule as above: this is
#: the only definition, so read it rather than restating the pair. It had been
#: restated as a bare literal eleven lines below that instruction.
PDF_USABLE = {"ok", "scanned_pdf_suspected"}


def finalize_status(record: dict) -> dict:
    """Derive the top-level status from the per-artifact statuses.

    complete -> the PDF is usable and the supplement situation is resolved
                (either the publisher says there are none, or we got them).
    partial  -> we have something useful but not everything.
    failed   -> no usable PDF.
    """
    pdf_ok = (record.get("fulltext") or {}).get("status") in PDF_USABLE
    supplements_settled = record.get("supplementary_status") in SUPPL_SETTLED

    if pdf_ok and supplements_settled:
        record["status"] = "complete"
    elif pdf_ok or record.get("supplementary"):
        record["status"] = "partial"
    else:
        record["status"] = "failed"
    return record


# -- policy removals ---------------------------------------------------------

#: Why a stored file was deleted while its record was kept. One value so far, and
#: the key is the marker rather than the value being a boolean: a later policy that
#: removes files for a different reason must be distinguishable in a manifest
#: written today.
NOT_TEXT_BEARING = "not_text_bearing"

#: Never removed by any policy, whatever a manifest entry claims. The pruner reaches
#: these only through a malformed record -- a supplementary entry whose `path` points
#: at the article -- and the cost of that mistake is the article, so it is refused
#: here rather than trusted not to happen.
_NEVER_REMOVED = {FULLTEXT_PDF, FULLTEXT_XML, LANDING_HTML, MANIFEST_NAME}


def path_is_protected(relative_path: Optional[str]) -> bool:
    """Is this manifest path one no policy may remove?

    Asked twice on purpose, and the order is what makes it a guard rather than a
    comment: `drop_media._removable` asks before the file is unlinked, and
    `mark_entry_removed` asks again before the entry is rewritten. Only the first can
    actually save the article -- a refusal after the `unlink` would leave the bytes
    gone and the `path` key in place, which is precisely the state that makes every
    later batch re-fetch the article.

    **Unreachable today, and kept deliberately.** All four of these names carry
    text-bearing extensions, so `text_bearing.skip_reason` never nominates one and no
    input reaches this check with a protected path. It is here so that the day a name
    is added to `_NEVER_REMOVED`, or an extension moves into the skip sets, a
    malformed entry pointing at the article cannot quietly become removable -- the
    same reason `manifest_is_complete` keeps a branch for a record with no
    `_directory`.

    Matched on the basename as well as the whole path, because a stored supplement is
    always prefixed (`supplementary/01_...`) and so cannot collide with these four,
    while a malformed entry naming `fulltext.pdf` in either form must not get through.
    An empty path is protected too: there is nothing there to remove.
    """
    if not relative_path:
        return True
    return (relative_path in _NEVER_REMOVED
            or Path(relative_path).name in _NEVER_REMOVED)


def entry_removed_by_policy(entry) -> bool:
    """Has this manifest entry already had its file removed by a policy sweep?

    The idempotence test for `drop-media` and the "this entry is not malformed"
    test for `extract/extractor.py`, which is the same question asked twice: an
    entry with no `path` is either a removal that was recorded or a manifest that
    is broken, and only the marker tells them apart.
    """
    if not isinstance(entry, dict):
        return False
    return bool(entry.get("removed")) and not entry.get("path")


def mark_entry_removed(entry: dict, reason: str,
                       policy: str = NOT_TEXT_BEARING) -> Optional[str]:
    """Record that this entry's file was deleted by policy. Returns the path removed.

    Returns None -- and changes nothing -- for an entry with no path, so calling this
    twice is safe and the second call is not a second removal.

    **The `path` key is dropped, and that is the whole design.**
    `manifest_is_complete` walks every supplementary entry and calls the article
    incomplete when one names a file that is not there, so an entry that kept its
    path would make the next batch re-fetch the article, re-download the file, and
    the sweep would undo itself. What is kept is the record: `name` (the path the
    file had), `bytes`, `sha256`, everything the entry already carried about where it
    came from, plus who removed it and why. `evict_article` marks its entries the
    same way and can afford to keep their paths, because it takes the *whole*
    article and `manifest_is_complete` short-circuits on `status: evicted` before the
    loop; here only part of the article is gone and the rest is genuinely complete,
    so there is no status to short-circuit on.
    """
    path = entry.get("path")
    if not path or path_is_protected(path):
        return None
    entry.pop("path")
    # Under `name`, not `path`: same string, and a key `manifest_is_complete` does
    # not read. `original_name` stays whatever the publisher called the file.
    entry["name"] = path
    entry["removed"] = policy
    entry["removed_reason"] = reason
    entry["removed_at"] = datetime.now(timezone.utc).isoformat()
    return path


# -- size budget -------------------------------------------------------------
# Roughly 40 MB per paper in practice, and the bulk is content the pipeline
# actually wants. Re-measured over the whole corpus -- 393 articles, 26.90 GB of
# payload -- and the shape of the original 63-paper measurement holds: 70.0% of the
# bytes are text-bearing files and 19.0% are archives (5.05 GB of it `.zip`, mostly
# supplementary tables), against 8.0% audio/video and 2.8% images. So there is still
# no useful "drop the media" saving *against a budget*: 10.8% now rather than 8%,
# and staying inside a budget still means giving up whole articles, which is why
# eviction keeps the manifest and can be undone with --force.
#
# `fetch.text_bearing_only` and `drop-media` drop exactly that media, and the
# sentence above is the reason they are not sold as a disk measure: the 2.90 GB they
# reclaim is a tenth of the corpus, while the 2428 entries they remove are 47% of
# every supplementary entry it holds. The saving is in requests, entries and
# extraction records that yield no text; disk is a side effect.

def article_size(directory) -> int:
    """Bytes on disk for one article, manifest included."""
    total = 0
    for path in Path(directory).rglob("*"):
        if path.is_file():
            total += path.stat().st_size
    return total


def corpus_usage(corpus_dir) -> List[dict]:
    """Per-article usage, oldest first, for reporting and eviction."""
    root = Path(corpus_dir).expanduser()
    if not root.exists():
        return []
    entries = []
    for directory in sorted(p for p in root.iterdir() if p.is_dir()):
        record = read_manifest(directory) or {}
        entries.append({
            "slug": directory.name,
            "path": str(directory),
            "doi": record.get("doi"),
            "status": record.get("status", "unknown"),
            "fetched_at": record.get("fetched_at") or "",
            "bytes": article_size(directory),
            "files": sum(1 for p in directory.rglob("*") if p.is_file()),
        })
    entries.sort(key=lambda e: (e["fetched_at"], e["slug"]))
    return entries


def evict_article(directory) -> int:
    """Delete an article's payload but keep its manifest. Returns bytes freed.

    The manifest is rewritten with `status: evicted` and each file entry marked,
    so the record of what existed -- names, sizes, sha256 -- survives even though
    the bytes do not. A corpus that forgets what it deleted is worse than one that
    never had it.
    """
    directory = Path(directory)
    record = read_manifest(directory)
    freed = 0

    for path in sorted(directory.rglob("*"), key=lambda p: -len(p.parts)):
        if path.name == MANIFEST_NAME:
            continue
        try:
            if path.is_file():
                freed += path.stat().st_size
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        except OSError:
            continue

    if record is not None:
        record["status"] = "evicted"
        record["evicted_at"] = datetime.now(timezone.utc).isoformat()
        record["evicted_bytes"] = freed
        for group in ("supplementary", "media"):
            for entry in record.get(group) or []:
                if entry_removed_by_policy(entry):
                    # Already gone, and its own marker says who took it. Marking it
                    # evicted as well would put two removals' names on one file, and
                    # imply it under `evicted_bytes`, which counts what this call
                    # actually freed.
                    continue
                entry["evicted"] = True
        for key in ("fulltext", "fulltext_xml", "landing_html"):
            entry = record.get(key)
            if isinstance(entry, dict) and entry.get("path"):
                entry["evicted"] = True
        write_manifest(directory, record)
    return freed


def enforce_budget(corpus_dir, max_bytes: Optional[int], dry_run: bool = False) -> dict:
    """Evict oldest-first until the corpus fits inside `max_bytes`.

    Articles already evicted are skipped, and an article is never evicted to make
    room for itself -- the newest is kept because it is the one just fetched.
    """
    entries = corpus_usage(corpus_dir)
    total = sum(e["bytes"] for e in entries)
    result = {"total_bytes": total, "max_bytes": max_bytes, "evicted": [], "freed_bytes": 0}
    if not max_bytes or total <= max_bytes:
        return result

    # Oldest first, but never the most recent article.
    candidates = [e for e in entries[:-1] if e["status"] != "evicted"]
    for entry in candidates:
        if total <= max_bytes:
            break
        freed = entry["bytes"] if dry_run else evict_article(entry["path"])
        total -= freed
        result["evicted"].append({"slug": entry["slug"], "doi": entry["doi"],
                                  "freed_bytes": freed})
        result["freed_bytes"] += freed

    result["total_bytes"] = total
    if total > max_bytes:
        result["note"] = (
            "still over budget: the remaining articles cannot be evicted "
            "(all evicted already, or only the newest is left)"
        )
    return result


def human_bytes(count: int) -> str:
    value = float(count)
    # `or unit == "TB"` makes the last iteration return unconditionally, so the
    # loop is the only exit and no fallthrough is needed however large `count` is.
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f}{unit}" if unit != "B" else f"{int(value)}B"
        value /= 1024


def summarize(record: dict) -> str:
    """One-line human summary, used by the CLI and batch report."""
    fulltext = (record.get("fulltext") or {}).get("status", "?")
    supplements = record.get("supplementary_status", "?")
    count = len(record.get("supplementary") or [])
    tiers = ",".join(record.get("tiers_tried") or []) or "-"
    return (
        f"{record.get('status', '?'):8s} pdf={fulltext:22s} "
        f"suppl={supplements:22s} files={count:<3d} tiers={tiers}"
    )
