"""Corpus layout and manifest.

    corpus/<doi_slug>/
        manifest.json
        fulltext.pdf
        fulltext.nxml            only when an OA package supplied it
        supplementary/01_...     original filenames, sanitised and ordered
        landing.html             browser tier only, for adapter debugging

The manifest follows the same principle as `curation/audit.py`: one record that
says exactly where every byte came from, including the attempts that failed. A
reader holding a manifest can tell which tier produced the PDF, whether the
publisher claimed supplements existed, and what went wrong if some are missing.
"""

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

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
    """True only for a manifest whose files are all present on disk.

    A `complete` status is not trusted on its own: if the PDF was deleted or the
    directory was partially copied, the fetch should run again rather than
    report a cached success.
    """
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


def new_record(ids, corpus_dir) -> dict:
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


def finalize_status(record: dict) -> dict:
    """Derive the top-level status from the per-artifact statuses.

    complete -> the PDF is usable and the supplement situation is resolved
                (either the publisher says there are none, or we got them).
    partial  -> we have something useful but not everything.
    failed   -> no usable PDF.
    """
    pdf_ok = (record.get("fulltext") or {}).get("status") in {"ok", "scanned_pdf_suspected"}
    supplements_settled = record.get("supplementary_status") in {"none_listed", "fetched"}

    if pdf_ok and supplements_settled:
        record["status"] = "complete"
    elif pdf_ok or record.get("supplementary"):
        record["status"] = "partial"
    else:
        record["status"] = "failed"
    return record


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
