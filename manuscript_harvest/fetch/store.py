"""Corpus layout and manifest.

    corpus/<doi_slug>/
        manifest.json
        fulltext.pdf
        fulltext.nxml            only when an OA package supplied it
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
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    target = path / MANIFEST_NAME
    target.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
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
    """
    if record and record.get("status") == "evicted":
        return True
    if not record or record.get("status") != "complete":
        return False
    directory = Path(record.get("_directory", "")) if record.get("_directory") else None
    if directory is None:
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
SUPPL_SETTLED = {"none_listed", "fetched", "fetched_unverified"}


def finalize_status(record: dict) -> dict:
    """Derive the top-level status from the per-artifact statuses.

    complete -> the PDF is usable and the supplement situation is resolved
                (either the publisher says there are none, or we got them).
    partial  -> we have something useful but not everything.
    failed   -> no usable PDF.
    """
    pdf_ok = (record.get("fulltext") or {}).get("status") in {"ok", "scanned_pdf_suspected"}
    supplements_settled = record.get("supplementary_status") in SUPPL_SETTLED

    if pdf_ok and supplements_settled:
        record["status"] = "complete"
    elif pdf_ok or record.get("supplementary"):
        record["status"] = "partial"
    else:
        record["status"] = "failed"
    return record


# -- size budget -------------------------------------------------------------
# Roughly 40 MB per paper in practice, and the bulk is content the pipeline
# actually wants: measured over 63 papers, 45% PDF and 25% spreadsheets/CSV,
# with only 8% video and images. So there is no useful "drop the media" saving --
# staying inside a budget means giving up whole articles, which is why eviction
# keeps the manifest and can be undone with --force.

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
