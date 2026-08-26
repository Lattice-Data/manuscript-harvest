"""Remove the stored files no text can be extracted from, keeping their record.

`fetch.text_bearing_only` stops these files arriving from now on, which does
nothing for the ones already on disk. Measured over this corpus: of 5116 stored
supplementary entries, 2428 (47%) are image, audio or video -- 138 articles hold at
least one, and inside those articles 71% of the supplement slots are files nothing
downstream can read. They cost 2.90 GB and, more to the point, 2428 manifest
entries and 2428 extraction records whose only content is the word `image_no_text`.

So this is `revalidate`'s shape rather than `prune`'s: it reads the corpus, reports
what it would do, and writes only with `apply`. It is the *opposite* command to
`prune`, which enforces `fetch.max_corpus_gb` by evicting whole articles oldest-first
-- that one gives up papers to stay under a cap, this one gives up files no stage
of this pipeline ever reads, from every paper. `store.py`'s size-budget comment has
said since it was written that there is no useful drop-the-media saving to be had
against a budget, and that measurement still holds: this reclaims a tenth of the
bytes and half of the entries.

Four things it will not do:

- **It never drops a `path` key without deleting the file, and never deletes a file
  without dropping the `path` key.** `store.manifest_is_complete` walks every
  supplementary entry and calls the article incomplete when one names a file that is
  not there, so half of either would make the next batch re-fetch all 138 articles
  and undo the sweep. `store.mark_entry_removed` owns that shape.
- **It never touches `fulltext.pdf`, `fulltext.nxml`, `landing.html` or
  `manifest.json`.** Structurally, first: it only ever walks the `supplementary` and
  `media` entry lists. Then `store.path_is_protected` refuses those four names anyway,
  before the `unlink` and again in `mark_entry_removed` -- a guard nothing can reach
  today, because all four carry text-bearing extensions, and which exists so that a
  later change to either list cannot make the article removable by accident.
- **It never changes `supplementary_status`.** After this change that status is a
  claim about the supplementary files text can be extracted from, and this command
  removes none of those -- the set it describes is unchanged, and what went is
  recorded entry by entry. Rewriting it to `none_text_bearing` for an article whose
  remaining supplements are three spreadsheets would be false; rewriting the
  all-media articles alone would make the status depend on when the sweep ran.
- **It does not re-derive anything.** Same predicate as the fetch stage
  (`text_bearing.skip_reason`), read from the same module, so a file this deletes is
  one a fresh fetch would not have downloaded.

Idempotent: a second pass sees entries that are already marked and have no path, and
reports nothing to do.

Nothing has to be re-extracted by hand afterwards, and nothing goes stale either:
`extract/extractor.py` keys its cache on the manifest's sha256, which this changes,
so the next `manuscript-extract all` re-extracts a swept article once and records the
removals. The blocks it writes are identical -- see the caveat argument there.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from .. import text_bearing
from . import store

#: The manifest groups whose files this walks. `media/` is in scope and cannot be
#: left out: with `fetch.text_bearing_only` on, every extension
#: `pmc_oa.supplement_or_media` routes there is an image extension, so the directory
#: holds nothing but files this policy refuses -- sweeping `supplementary/` and
#: leaving `media/` full would be the same corpus with a tidier index.
_GROUPS = ("supplementary", "media")


def _removable(directory: Path, record: dict) -> List[dict]:
    """Every stored file in this article that no text can be extracted from.

    Judged on the *stored* path, which is the name `extract/extractor.py` dispatches
    on, so a file named here is one it would have recorded as `image_no_text` or
    `media_no_text`. Deliberately not judged on `original_name`, and not by sniffing
    the bytes as the extractor does when a name carries no extension: this deletes
    files, so it acts only on what the name on disk proves. The 13 extensionless
    `NN_url` supplements the browser tier saved therefore stay, which is the same
    answer `text_bearing.skip_reason` gives them at fetch time.
    """
    out: List[dict] = []
    for group in _GROUPS:
        for entry in record.get(group) or []:
            if not isinstance(entry, dict):
                continue
            relative = entry.get("path")
            if not relative:
                # Either an already-recorded removal or a malformed entry. Both are
                # nothing to reclaim, and telling them apart is
                # `entry_removed_by_policy`'s job, not this walk's.
                continue
            if store.path_is_protected(relative):
                # A supplementary entry pointing at `fulltext.pdf` is malformed, and
                # the cost of humouring it is the article. Asked here as well as in
                # `mark_entry_removed`, because that one runs *after* the `unlink`.
                # Unreachable while all four protected names are text-bearing -- see
                # `store.path_is_protected` on why it is kept anyway.
                continue
            path = directory / relative
            if not path.exists():
                # A missing file with a live `path` is the signal that makes the next
                # batch re-fetch this article. Marking it removed here would silence
                # that, over bytes this command never had and never deleted.
                continue
            reason = text_bearing.skip_reason(relative)
            if reason is None:
                continue
            out.append({"group": group, "entry": entry, "path": relative,
                        "reason": reason, "bytes": path.stat().st_size})
    return out


def _prune_empty_dirs(directory: Path) -> List[str]:
    """Remove `supplementary/` or `media/` if the sweep emptied it. Returns the names.

    An empty `supplementary/` is worse than untidy: it reads as an article whose
    supplements were never fetched, which is the one thing this corpus's whole status
    taxonomy exists to distinguish from an article that has none.
    """
    gone = []
    for name in (store.SUPPLEMENT_DIR, store.MEDIA_DIR):
        target = directory / name
        if not target.is_dir():
            continue
        if any(target.iterdir()):
            continue
        try:
            target.rmdir()
        except OSError:
            continue
        gone.append(name)
    return gone


def drop_media_article(directory, apply: bool = False) -> dict:
    """Report -- and with `apply`, perform -- one article's removals.

    Returns `{"slug", "doi", "files", "bytes", "removed", "note"}`. `files` is one
    entry per file, each with its path, its reason and its size, so the caller can
    print a per-article line without re-reading anything.
    """
    directory = Path(directory)
    record = store.read_manifest(directory)
    report = {"slug": directory.name, "doi": None, "files": [], "bytes": 0,
              "removed": False, "note": None}
    if record is None:
        report["note"] = "no manifest"
        return report
    report["doi"] = record.get("doi")

    if record.get("status") == "evicted":
        # The budget sweep already took every byte and its manifest records what was
        # there, paths included. There is nothing on disk to reclaim, and rewriting
        # those entries would overwrite one removal's record with another's.
        report["note"] = "evicted: the bytes are already gone and the record stands"
        return report

    candidates = _removable(directory, record)
    report["files"] = [{"path": item["path"], "reason": item["reason"],
                        "bytes": item["bytes"]} for item in candidates]
    report["bytes"] = sum(item["bytes"] for item in candidates)
    if not candidates or not apply:
        return report

    freed = 0
    done: List[dict] = []
    failed: List[dict] = []
    # Read once, before the loop: the cumulative history this sweep adds to. Same
    # shape as `revalidate`'s `revalidated` key and `evict_article`'s `evicted_at`,
    # and cumulative because a second sweep after a `--force` re-fetch is a second
    # removal -- overwriting would report the corpus as having lost less than it did.
    history = record.get("media_dropped") or {}
    was_files = int(history.get("files") or 0)
    was_bytes = int(history.get("bytes") or 0)

    for item in candidates:
        try:
            (directory / item["path"]).unlink()
        except OSError as e:
            # The entry is left alone for a file that is still there. A record saying
            # "removed" over bytes on disk is the one outcome worse than leaving the
            # file: nothing downstream would ever look at it again.
            failed.append({"path": item["path"], "error": str(e)})
            continue
        freed += item["bytes"]
        store.mark_entry_removed(item["entry"], item["reason"])
        done.append({"path": item["path"], "reason": item["reason"],
                     "bytes": item["bytes"]})
        # **Persisted here, once per file, and that is the whole point.**
        #
        # Writing the manifest after the loop instead is what a first cut did, and it
        # is wrong in a way no test caught: `unlink` had already run for every earlier
        # file, so an interrupt anywhere in this loop left those files deleted with
        # live `path` keys still naming them. `manifest_is_complete` then calls the
        # article incomplete, every later batch re-fetches and re-downloads the media,
        # and the sweep undoes itself on a loop -- the exact trap dropping the `path`
        # key exists to prevent. Measured on a three-supplement article with a
        # KeyboardInterrupt on the third `unlink`: two files gone, two entries still
        # naming them, `manifest_is_complete` False, and a second `--apply` could not
        # repair it because `_removable` skips an entry whose file is already missing.
        #
        # Writing inside the loop bounds that to the single file currently in flight,
        # and only to a kill that lands between the `unlink` above and this line --
        # `try`/`finally` would cover Ctrl-C and SIGTERM but not SIGKILL, so the
        # narrower window is worth the extra writes. `store.write_manifest` is atomic
        # for the same reason: eighteen writes per swept article is eighteen chances
        # to truncate the record, so it stages and renames.
        record["media_dropped"] = {
            "at": datetime.now(timezone.utc).isoformat(),
            "policy": store.NOT_TEXT_BEARING,
            "files": was_files + len(done),
            "bytes": was_bytes + freed,
        }
        store.write_manifest(directory, record)

    report.update({"files": done, "bytes": freed, "removed": bool(done)})
    if failed:
        report["failed"] = failed
        report["note"] = f"{len(failed)} file(s) could not be deleted"
    if done:
        # After the writes, not before: `_prune_empty_dirs` only removes a directory
        # it finds empty, so the manifest on disk is already consistent with it.
        report["emptied_dirs"] = _prune_empty_dirs(directory)
    return report


def drop_media_corpus(corpus_dir, apply: bool = False, slugs=None) -> List[dict]:
    """Walk `corpus_dir` (or just `slugs`), oldest-first by directory name.

    `revalidate_corpus`'s shape exactly, including reporting an article with nothing
    to do: "393 checked, 138 to sweep" is the line that tells a user the pass ran
    over the corpus they meant.
    """
    root = Path(corpus_dir).expanduser()
    if not root.exists():
        return []
    wanted = set(slugs or ())
    reports = []
    for directory in sorted(p for p in root.iterdir() if p.is_dir()):
        if wanted and directory.name not in wanted:
            continue
        reports.append(drop_media_article(directory, apply=apply))
    return reports


def summarize(reports: List[dict]) -> dict:
    """Corpus totals for the closing line: articles, files, bytes, and by reason."""
    affected = [r for r in reports if r["files"]]
    by_reason: dict = {}
    for report in affected:
        for item in report["files"]:
            by_reason[item["reason"]] = by_reason.get(item["reason"], 0) + 1
    return {
        "articles": len(reports),
        "affected": len(affected),
        "files": sum(len(r["files"]) for r in affected),
        "bytes": sum(r["bytes"] for r in affected),
        "by_reason": by_reason,
    }


def human_reasons(by_reason: dict) -> Optional[str]:
    """`"2373 image, 59 audio_video"`, largest first, or None when there are none."""
    if not by_reason:
        return None
    return ", ".join(f"{count} {reason}" for reason, count
                     in sorted(by_reason.items(), key=lambda kv: -kv[1]))
