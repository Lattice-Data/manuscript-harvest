"""What the panel shows about the corpus, read from the records the stages write.

Every number on the page comes from `manifest.json` and `extraction.json` -- the
files the two stages already write -- and never from parsing a log line. Those
records carry a schema the rest of the package depends on, and they are correct
whether or not a job is running; a stderr line is prose, formatted to be read by a
person, and free to change shape. The log pane shows a job's own words verbatim and
unparsed, which is the other half of the same decision.

Nothing here writes anything, and nothing here imports the fetch or extract
orchestrators. What it does import is `fetch.store`, which is the module that
defines what a manifest *is* -- see `preflight` for the one question where reusing
that definition rather than restating it is the whole point.
"""

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from ..extract import extractor
from ..fetch import store
from ..fetch.identifiers import normalize_doi

# Suffixes worth sniffing for a DOI list. Names alone decide nothing -- see
# `doi_list_files`.
DOI_LIST_SUFFIXES = (".dois", ".doi", ".txt", ".list")

# A DOI list is a text file a person maintains by hand. Anything past these is not
# one, and reading it to find out costs more than saying so.
MAX_LIST_BYTES = 1_000_000
MAX_LIST_LINES = 5_000

# The pre-flight table is rendered a row per DOI, and its point is to be read
# before pressing a button. Past this many the table is not the answer anyway.
MAX_PREFLIGHT_DOIS = 2_000

# How many of the newest articles the panel shows under "recently added".
RECENT_ARTICLES = 12

# A full snapshot walks every article directory and reads two JSON files from each:
# measured at 0.2 s over the 392-article, 27 GB corpus this was built against, with
# the byte totals included. Short enough to compute on demand, long enough to be
# worth not repeating for every poll of a page that refreshes on a timer.
SNAPSHOT_TTL_SECONDS = 5.0

PREFLIGHT_NEW = "new"
PREFLIGHT_REFETCH = "refetch"
PREFLIGHT_CACHED = "cached"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # A record being written as we read it, or one a crash left truncated.
        # Reported as absent rather than raised: a panel that 500s because one
        # article of 392 is mid-write is worse than one that says 391.
        return None


def corpus_snapshot(corpus_dir) -> dict:
    """Counts, totals and the newest articles, from the manifests and extractions.

    Counted the way the two stages count: an article is one directory holding a
    `manifest.json`, which is exactly `extract.cli._article_dirs`' test, so the
    panel's "392 papers" is the same 392 that `manuscript-extract all` would read.
    """
    root = Path(corpus_dir).expanduser()
    result = {
        "corpus_dir": str(root),
        "exists": root.exists(),
        "papers": 0,
        "bytes": 0,
        "files": 0,
        "fetch": {},
        "extract": {},
        "totals": {"blocks": 0, "tables": 0, "chars": 0},
        "recent": [],
        "at": _now(),
    }
    if not root.exists():
        return result

    articles = []
    for directory in sorted(p for p in root.iterdir() if p.is_dir()):
        manifest_path = directory / store.MANIFEST_NAME
        if not manifest_path.exists():
            continue
        record = _read_json(manifest_path) or {}
        result["papers"] += 1
        status = record.get("status") or "unknown"
        result["fetch"][status] = result["fetch"].get(status, 0) + 1

        size = 0
        files = 0
        for child in directory.rglob("*"):
            try:
                if child.is_file():
                    size += child.stat().st_size
                    files += 1
            except OSError:
                continue
        result["bytes"] += size
        result["files"] += files

        extraction = extractor.read_extraction(directory)
        if extraction is None:
            result["extract"]["not extracted"] = result["extract"].get("not extracted", 0) + 1
            extract_status = None
        else:
            extract_status = extraction.get("status") or "unknown"
            result["extract"][extract_status] = result["extract"].get(extract_status, 0) + 1
            totals = extraction.get("totals") or {}
            for key in ("blocks", "tables", "chars"):
                result["totals"][key] += totals.get(key, 0) or 0

        articles.append({
            "slug": directory.name,
            "doi": record.get("doi"),
            "fetch_status": status,
            "extract_status": extract_status,
            "supplementary_status": record.get("supplementary_status"),
            "files": files,
            "bytes": size,
            # `fetched_at` is when the fetch ran; the manifest's mtime moves again
            # when a later command rewrites it (`drop-media` does). Sorted on the
            # record's own timestamp, so "recently added" means recently fetched
            # rather than recently touched.
            "fetched_at": record.get("fetched_at") or "",
        })

    articles.sort(key=lambda a: (a["fetched_at"], a["slug"]), reverse=True)
    result["recent"] = articles[:RECENT_ARTICLES]
    return result


class SnapshotCache:
    """`corpus_snapshot` behind a TTL, invalidated when a job finishes.

    The TTL covers the page's own polling; the invalidation covers the case that
    actually matters, a run that just added twenty papers and a header still
    showing the count from before it.
    """

    def __init__(self, corpus_dir, ttl: float = SNAPSHOT_TTL_SECONDS):
        self.corpus_dir = corpus_dir
        self.ttl = ttl
        self._lock = threading.Lock()
        self._value = None
        self._at = 0.0

    def get(self, force: bool = False) -> dict:
        with self._lock:
            fresh = self._value is not None and (time.monotonic() - self._at) < self.ttl
            if fresh and not force:
                return self._value
        # Computed outside the lock: it walks the whole corpus, and a slow walk
        # should not block the endpoint that reports a running job's progress.
        value = corpus_snapshot(self.corpus_dir)
        with self._lock:
            self._value = value
            self._at = time.monotonic()
            return value

    def invalidate(self) -> None:
        with self._lock:
            self._at = 0.0


def _sniff_doi_list(path: Path) -> dict:
    """How many of a file's lines are DOIs. `{"dois": n, "lines": n}` or None."""
    try:
        if path.stat().st_size > MAX_LIST_BYTES:
            return None
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    lines = 0
    dois = 0
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        lines += 1
        if lines > MAX_LIST_LINES:
            return None
        try:
            normalize_doi(line)
        except ValueError:
            continue
        dois += 1
    if not lines:
        return None
    return {"dois": dois, "lines": lines}


def doi_list_files(root) -> list:
    """Files in `root` that read like a list of DOIs, for the picker.

    Sniffed rather than matched on name. This repository's own root holds three
    `requirements*.txt`, which are `.txt` files and are not DOI lists, and the file
    the DOIs are actually kept in is called `finish-fetch.dois`. So a file
    qualifies when at least one line normalizes to a DOI and more than half of its
    non-comment lines do -- which admits a hand-kept list with a couple of typos in
    it, and refuses a prose note that happens to quote one DOI.

    Top level only. A recursive walk of a directory that also holds a 27 GB corpus
    is not something to do on a page load.
    """
    directory = Path(root).expanduser()
    if not directory.exists():
        return []
    found = []
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.suffix.lower() not in DOI_LIST_SUFFIXES:
            continue
        sniffed = _sniff_doi_list(path)
        if not sniffed or not sniffed["dois"]:
            continue
        if sniffed["dois"] * 2 <= sniffed["lines"]:
            continue
        found.append({
            "name": path.name,
            "path": str(path),
            "dois": sniffed["dois"],
            "lines": sniffed["lines"],
            "modified": datetime.fromtimestamp(
                path.stat().st_mtime, tz=timezone.utc).isoformat(),
        })
    found.sort(key=lambda f: f["modified"], reverse=True)
    return found


def preflight(corpus_dir, text: str) -> dict:
    """What a plain `manuscript-fetch batch` over these DOIs would actually do.

    The middle answer is the one worth the function. `fetch_publication` skips a
    paper only when `store.manifest_is_complete` says the article needs no further
    fetching, and that is a stricter and differently-shaped test than
    `status == "complete"`: an `evicted` article counts as complete, and a
    `complete` one whose recorded files have since left the disk does not. So a
    **partial** paper is re-fetched by a plain run with no `--force` at all --
    which is not what the flag's name suggests, and is the difference between a
    useful run and a twelve-paper no-op.

    Answered by calling `manifest_is_complete` itself, with the `_directory` key
    injected exactly as `fetch_publication` injects it, rather than by restating
    here what complete means. If that definition moves, this moves with it.
    """
    root = Path(corpus_dir).expanduser()
    counts = {PREFLIGHT_NEW: 0, PREFLIGHT_REFETCH: 0, PREFLIGHT_CACHED: 0}
    rows = []
    unparseable = []
    repeated = []
    seen = set()
    truncated = 0

    for raw in (text or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        try:
            doi = normalize_doi(line)
        except ValueError:
            unparseable.append(raw.strip()[:200])
            continue
        # Mirrors `cmd_batch`, which collapses repeats after normalization and
        # keeps the first: `10.1038/X` and `https://doi.org/10.1038/x` are one
        # paper, and the panel must count them the way the run will.
        if doi in seen:
            if doi not in repeated:
                repeated.append(doi)
            continue
        seen.add(doi)
        if len(rows) >= MAX_PREFLIGHT_DOIS:
            truncated += 1
            continue

        directory = store.article_dir(root, doi)
        record = store.read_manifest(directory)
        if record is None:
            state = PREFLIGHT_NEW
            record = {}
        else:
            record["_directory"] = str(directory)
            state = (PREFLIGHT_CACHED if store.manifest_is_complete(record)
                     else PREFLIGHT_REFETCH)
        counts[state] += 1
        extraction = extractor.read_extraction(directory) or {}
        rows.append({
            "doi": doi,
            "slug": directory.name,
            "state": state,
            "fetch_status": record.get("status"),
            "pdf": (record.get("fulltext") or {}).get("status"),
            "supplementary_status": record.get("supplementary_status"),
            "files": len(record.get("supplementary") or []),
            "extract_status": extraction.get("status"),
        })

    return {
        "rows": rows,
        "counts": dict(counts, total=len(seen), truncated=truncated),
        "unparseable": unparseable,
        "repeated": repeated,
    }


def health(config_path, config: dict, corpus_dir) -> dict:
    """The chips along the top: where settings came from, and which keys are set.

    Each of these is a thing that silently wastes a run. A `config.yaml` resolved
    against the wrong directory falls back to built-in defaults and writes to a
    different corpus while reporting success -- the note in `config.py` is about
    exactly that. A dead proxy session fails every paywalled paper identically. A
    missing Elsevier key makes Cell Press supplements unreachable rather than
    absent. None of them raise; all of them are visible from here.
    """
    fetch_cfg = config.get("fetch") or {}
    path = Path(config_path).expanduser()
    root = Path(corpus_dir).expanduser()

    key_env = bool(os.environ.get("MANUSCRIPT_HARVEST_ELSEVIER_API_KEY"))
    key_config = bool(fetch_cfg.get("elsevier_api_key"))
    result = {
        "config_path": str(path.resolve() if path.exists() else path),
        "config_found": path.exists(),
        "corpus_dir": str(root),
        "corpus_exists": root.exists(),
        "tiers": list(fetch_cfg.get("tiers") or []),
        # Booleans and a source, never the value. This dict is serialised to a web
        # page; a key is a secret even on loopback.
        "elsevier_key": key_env or key_config,
        "elsevier_key_source": "environment" if key_env else ("config" if key_config else None),
        "ncbi_key": bool(fetch_cfg.get("ncbi_api_key")),
        "contact_email": fetch_cfg.get("contact_email"),
        "proxy_tier": "proxy_browser" in (fetch_cfg.get("tiers") or []),
        "session_saved": None,
        "session_path": None,
        "session_age_seconds": None,
    }

    try:
        # Imported here rather than at module scope for the reason `fetch/cli.py`
        # gives: this path has to stay usable on an install without Playwright.
        from ..fetch.sources.proxy_browser import session_saved, state_path
    except ImportError:
        return result

    try:
        state_file = Path(state_path(fetch_cfg))
        result["session_path"] = str(state_file)
        result["session_saved"] = bool(session_saved(fetch_cfg))
        if state_file.exists():
            result["session_age_seconds"] = max(
                0.0, time.time() - state_file.stat().st_mtime)
    except (OSError, KeyError, TypeError):
        pass
    return result
