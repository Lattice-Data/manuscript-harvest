"""Files on disk that no manifest entry points at, and what to do about them.

A supplement is stored as `<subdir>/<NN>_<name>`, with `NN` from `enumerate()` over
the set the tiers came away with (`fetcher._write_group`). Nothing about that number
is stable: a re-fetch that returns a different-sized or differently-ordered set
renumbers every file after the first change, writes the new names, and -- until this
module existed -- left the old ones on disk with nothing referring to them.

**Measured on the live corpus before the sweep existed: 202 files, 1.37 GB, across 29
of 393 articles.** It grew with every `--force` run: one re-fetch batch of 38
articles took it from 152 files / 1.24 GB to 202 / 1.37 GB. Nothing could see it --
`drop-media` walks manifest *entries*, `usage` and `enforce_budget` measure whole
directories, and `manifest_is_complete` asks only whether the files an entry names
are present. No command asked the reverse question, so 1.37 GB was invisible to all
of them while `usage` counted it against `fetch.max_corpus_gb`.

`10.64898/2026.02.15.704933` is the clearest specimen: 27 unreferenced files against
15 referenced paths, and `media-7.xlsx` stored four times over -- `02_`, `05_`, `06_`
and `07_media-7.xlsx`, all 1057643 bytes, mtimes spread across four separate runs
(Aug 25 15:12, Aug 26 08:30, Aug 26 17:02, Aug 27 09:00). Only `07_` is referenced.

Two callers, one question, different remedies:

- `sweep_article` runs inside `fetch_publication`, immediately after the manifest is
  written, and deletes unconditionally. Anything the *final* record does not
  reference was orphaned by the run that just finished, so there is nothing to weigh.
  It does **not** run on a cached hit, which returns at `fetcher.py:445` before the
  write-out block: a `batch` over 393 cached articles would then delete bytes while
  doing no fetching at all, which is the surprise the report-by-default convention
  exists to prevent. A fetch cleans up after itself; residue from runs that predate it
  is `drop-orphans`' job, and a human asks for that.
- `classify` and `sweep_corpus` back the `drop-orphans` command, which faces residue
  from runs whose records are long overwritten. There, content matters: 136 of the
  202 files were byte-identical to a file the manifest still references and 51 were
  bytes found nowhere else, so a blanket delete would have destroyed 0.869 GB of
  supplements a manifest had merely lost track of.

**Why this is not part of `drop-media`.** That command walks entry lists and deletes
the file an entry names, keeping the entry as the record (`store.mark_entry_removed`).
This one walks the directory and deletes files that have *no* entry -- so there is no
record to keep, and its counterpart to "keep the record" is "do not delete bytes that
exist nowhere else". Same corpus, opposite direction, and one `--apply` flag could
not have meant both.
"""

import hashlib
import tarfile
import zipfile
from pathlib import Path
from typing import Dict, List, Optional

from . import store

#: The subdirectories a fetch numbers files into, and so the only ones that can be
#: renumbered. Deliberately not the whole article directory: `fulltext.pdf`,
#: `fulltext.nxml`, `landing.html` and `manifest.json` live at the root under fixed
#: names that no `enumerate()` touches, and `store.path_is_protected` refuses all
#: four by name as well. Structure first, then the guard -- the same belt-and-braces
#: `drop_media` uses, for the same reason: the cost of a mistake here is the article.
_GROUPS = (store.SUPPLEMENT_DIR, store.MEDIA_DIR)

#: How much decompressed data `_member_hashes` will read out of one archive before it
#: gives up and reports the archive unresolved. A containment check is worth a few
#: seconds of I/O and is not worth a zip bomb: `extract/limits.py` bounds the
#: extraction stage at `max_archive_members: 25`, which is far too low here (the
#: archive this check exists for holds 264 members), so this bounds bytes instead of
#: count. Generous on purpose -- the largest archive it must resolve decompresses to
#: about 0.5 GB -- and an archive that exceeds it is reported, never assumed empty.
_MAX_ARCHIVE_READ = 2 * 1024 ** 3

#: Classification of one unreferenced file, by content rather than by name.
#: `REDUNDANT` and `REDUNDANT_ARCHIVE` are provably lossless to delete; `UNIQUE` is
#: not, and `sweep_article`'s callers are the only ones entitled to ignore the
#: difference (see the module docstring).
REDUNDANT = "redundant"
REDUNDANT_ARCHIVE = "redundant_archive"
UNIQUE = "unique"

#: Deletable without losing a byte the corpus does not still hold elsewhere. Read
#: this rather than restating the pair -- `cli.cmd_drop_orphans` and
#: `sweep_article` both key on it.
LOSSLESS = {REDUNDANT, REDUNDANT_ARCHIVE}


def sha256_path(path, chunk: int = 1 << 20) -> str:
    """`store.sha256_bytes` for a file too large to want in memory.

    The corpus holds a 91.9 MB `.xlsx` among its unreferenced files and a 326.9 MB
    article made entirely of them, and this runs over every candidate plus every
    referenced file in the same article, so reading whole files into memory to hash
    them is the one shape that would not scale here.
    """
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def unreferenced_files(directory, record: Optional[dict]) -> List[dict]:
    """Every file under `supplementary/` or `media/` that `record` does not name.

    Returns `[{"path", "bytes"}]`, sorted, relative to the article directory.

    **The referenced set must come from the record, not from what a run just wrote.**
    `fetch_publication` keeps the *existing* entry list when a re-fetch came away
    empty-handed but the old files are still accounted for (`existing_supplementary_ok`
    at `fetcher.py:727`, used at `:737` and `:773`). In that case the referenced set
    *is* the previous numbering, and computing it from the newly written group -- which
    is `[]` -- would delete the entire supplement set of exactly the articles that
    preservation branch exists to protect. Measured on the corpus: `--force` against a
    dead proxy session is the common way to reach it.

    Policy-removed entries (`store.entry_removed_by_policy`) carry no `path` by
    design and their files are already gone, so they contribute nothing to the
    referenced set and nothing on disk answers to them. That is the correct outcome
    and not a special case: there is no file to protect and none to delete. What
    would be wrong is treating the *absence* of a path as a licence to delete
    something -- which is why this walks the directory and subtracts, rather than
    trying to pair files with entries.

    Returns `[]` for an evicted article. `store.evict_article` takes every byte and
    leaves the entries' paths in place, so the subtraction would be meaningless, and
    a directory that is already empty has nothing to sweep.
    """
    directory = Path(directory)
    if record is None or record.get("status") == "evicted":
        return []
    referenced = store.referenced_paths(record)
    out: List[dict] = []
    for group in _GROUPS:
        group_dir = directory / group
        if not group_dir.is_dir():
            continue
        for path in sorted(group_dir.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(directory).as_posix()
            if relative in referenced:
                continue
            if store.path_is_protected(relative):
                # Unreachable through `_GROUPS`, since all four protected names live
                # at the article root -- kept for the same reason
                # `store.path_is_protected` keeps its own unreachable branch: the day
                # a fixed name moves into one of these directories, it must not
                # become deletable because nothing referenced it.
                continue
            out.append({"path": relative, "bytes": path.stat().st_size})
    return out


def sweep_article(directory, record: Optional[dict]) -> dict:
    """Delete every file `record` does not reference. Returns what went.

    The half of this module that runs during a fetch, where classification would be
    noise: the record was written seconds ago from the set the tiers actually
    returned, so an unreferenced file is one this run's renumbering just abandoned
    and its content is either still on disk under the new number or was never wanted.

    Returns `{"files": [...], "bytes": int, "failed": [...], "emptied_dirs": [...]}`.
    Never raises: a fetch that has already written a good manifest must not fail
    because a stale file could not be unlinked. A file left behind is the state this
    module exists to clean up and the next sweep will offer it again, which is the
    strictly better failure.
    """
    directory = Path(directory)
    candidates = unreferenced_files(directory, record)
    result: dict = {"files": [], "bytes": 0, "failed": [], "emptied_dirs": []}
    for item in candidates:
        try:
            (directory / item["path"]).unlink()
        except OSError as error:
            result["failed"].append({"path": item["path"], "error": str(error)})
            continue
        result["files"].append(item["path"])
        result["bytes"] += item["bytes"]
    if result["files"]:
        result["emptied_dirs"] = _prune_empty_dirs(directory)
    return result


def _prune_empty_dirs(directory: Path) -> List[str]:
    """Remove `supplementary/` or `media/` if the sweep emptied it. Returns the names.

    `drop_media._prune_empty_dirs` verbatim in behaviour and here for the same
    reason it gives: an empty `supplementary/` reads as an article whose supplements
    were never fetched, which is the one thing this corpus's status taxonomy exists
    to distinguish from an article that has none. Not imported from there because
    that module is about a text-bearing policy and this one is not; the shared thing
    is three lines of `rmdir`, and coupling the two commands to save them would tie
    a policy sweep to an integrity sweep.
    """
    gone = []
    for name in _GROUPS:
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


def _referenced_hashes(directory: Path, record: dict) -> Dict[str, List[str]]:
    """sha256 -> the referenced paths holding those bytes, for files on disk now.

    **Hashed off disk, not read from the entry's recorded `sha256`.** The recorded
    value describes the bytes as fetched, and this function's answer decides whether
    another file can be deleted -- so trusting the record would turn a referenced
    file that had been truncated or replaced into a licence to delete the last intact
    copy of its content. Verifying that stored bytes still match their manifest is
    `revalidate`'s job, and the point here is not to depend on its having been run.

    Bounded by the articles that have candidates at all: 29 of 393 in this corpus, so
    the whole classification pass reads a few GB rather than 26.
    """
    by_hash: Dict[str, List[str]] = {}
    for relative in sorted(store.referenced_paths(record)):
        path = directory / relative
        if not path.is_file():
            # A referenced file that is missing is the signal that makes
            # `manifest_is_complete` false and the next batch re-fetch the article.
            # It is not this command's business, and it must not become evidence that
            # anything else is redundant.
            continue
        by_hash.setdefault(sha256_path(path), []).append(relative)
    return by_hash


def _member_hashes(path: Path) -> Optional[List[str]]:
    """sha256 of every member of a zip or tar, or None if it is neither.

    Returns `None` for anything not readable as an archive, and for one that blows
    `_MAX_ARCHIVE_READ` -- "I cannot answer" and "there is nothing inside" must not
    collapse into the same value when a delete hangs off the difference.
    """
    read = 0
    try:
        if zipfile.is_zipfile(path):
            out = []
            with zipfile.ZipFile(path) as archive:
                for info in archive.infolist():
                    if info.is_dir():
                        continue
                    read += info.file_size
                    if read > _MAX_ARCHIVE_READ:
                        return None
                    digest = hashlib.sha256()
                    with archive.open(info) as handle:
                        for block in iter(lambda: handle.read(1 << 20), b""):
                            digest.update(block)
                    out.append(digest.hexdigest())
            return out
        if tarfile.is_tarfile(path):
            out = []
            # `r:*` rather than `r:gz`: the corpus holds a `.tgz` that nobody
            # compressed, which `r:gz` refuses. Same lesson `extract/archive.py`
            # records against `pmc_oa._unpack_tgz`.
            with tarfile.open(path, mode="r:*") as archive:
                for member in archive:
                    if not member.isfile():
                        continue
                    read += member.size
                    if read > _MAX_ARCHIVE_READ:
                        return None
                    handle = archive.extractfile(member)
                    if handle is None:
                        continue
                    digest = hashlib.sha256()
                    for block in iter(lambda: handle.read(1 << 20), b""):
                        digest.update(block)
                    out.append(digest.hexdigest())
            return out
    except (OSError, zipfile.BadZipFile, tarfile.TarError, EOFError, ValueError):
        # A truncated or encrypted archive answers nothing, which classifies its
        # file `unique` and leaves it for a human. Refusing to guess is the whole
        # value of this function.
        return None
    return None


_ARCHIVE_SUFFIXES = (".zip", ".tar", ".tgz", ".gz", ".bz2", ".xz")


def classify(directory, record: Optional[dict],
             candidates: Optional[List[dict]] = None) -> List[dict]:
    """Decide, per unreferenced file, whether deleting it would lose anything.

    Returns the `unreferenced_files` rows with `kind` added, plus `same_as` for a
    `REDUNDANT` file (the referenced paths holding the same bytes), `copies` for a
    `UNIQUE` file whose bytes appear at more than one unreferenced path, and
    `members`/`members_stored` for an archive that was opened.

    **Measured over the 202 files this was written for: 136 `redundant` (0.485 GB),
    51 `unique` (0.869 GB), 15 of those 51 duplicates of each other (0.019 GB).** So
    two thirds of the count is provably lossless and two thirds of the bytes are not,
    which is why the report separates them instead of offering one number.

    `REDUNDANT_ARCHIVE` is the case that would otherwise be filed as `unique` and
    left for a human forever: `10.1126/science.abo1984`'s orphaned
    `science.abo1984_tables_s1_to_s71.zip` is 31.7 MB of bytes that appear nowhere
    else in the article, and all 264 of its members are byte-identical to files
    already stored -- PMC flattens and renames the deposit, so not one member name
    matches, and only content shows it. Four more archives resolve the same way
    (`science.adf1226` 8 of 8, `science.abf3041` 2 of 2), and four do not:
    `sciimmunol.adf9988`'s `movies_s1_to_s6.zip` holds 107 MB and 0 of its 6 members
    are stored, `science.adf5357`'s holds 63.6 MB with 9 of 19. A name-based rule
    would have got every one of these wrong in one direction or the other.
    """
    directory = Path(directory)
    if record is None:
        return []
    if candidates is None:
        candidates = unreferenced_files(directory, record)
    if not candidates:
        return []

    referenced = _referenced_hashes(directory, record)
    rows = []
    for item in candidates:
        row = dict(item)
        row["sha256"] = sha256_path(directory / item["path"])
        rows.append(row)

    among_candidates: Dict[str, List[str]] = {}
    for row in rows:
        among_candidates.setdefault(row["sha256"], []).append(row["path"])

    for row in rows:
        if row["sha256"] in referenced:
            row["kind"] = REDUNDANT
            row["same_as"] = referenced[row["sha256"]]
            continue
        row["kind"] = UNIQUE
        copies = among_candidates[row["sha256"]]
        if len(copies) > 1:
            # Bytes stored several times over and referenced not at all -- the
            # renumbering trap after the manifest stopped naming the file under any
            # number. `media-9.xlsx` in `10.64898/2026.02.15.704933` is here three
            # times and `media-16.xlsx` twice. Still `UNIQUE`: keeping one copy would
            # preserve the content, and choosing which one is a human's call, not a
            # sweep's.
            row["copies"] = copies
        if Path(row["path"]).suffix.lower() not in _ARCHIVE_SUFFIXES:
            continue
        members = _member_hashes(directory / row["path"])
        if members is None:
            continue
        row["members"] = len(members)
        row["members_stored"] = sum(1 for digest in members if digest in referenced)
        if members and row["members_stored"] == len(members):
            row["kind"] = REDUNDANT_ARCHIVE
    return rows


def landing_html_state(directory, record: Optional[dict]) -> Optional[str]:
    """Is there a `landing.html` on disk that the manifest fails to mention?

    Returns `"unreferenced"` for one, `None` when the record and the disk agree
    (including when neither has it). The third case -- an entry naming a file that
    is gone -- is not this function's to report: `manifest_is_complete` already
    treats it as a reason to re-fetch, and it does not occur in this corpus (26
    articles have the file with no entry, 0 have the entry with no file).

    **Fixed at the source before this was written, and the residue is what is left.**
    `proxy_browser` saves the page it landed on so an adapter can be debugged after
    the fact, and `fetch_publication` records it in the same two lines that write it
    -- but until commit 186b2e4 a later run that came away without a landing page
    simply did not set the key, dropping the entry while leaving the file. That
    commit added the `_still_on_disk` fallback at `fetcher.py:723`, which is why the
    asymmetry is 26 and 0 rather than growing, and why nothing in the fetch path
    needs changing for it now.

    Worth referencing rather than deleting, on what the 26 files turned out to be:
    none of them is an article. Thirteen are ~1 KB Stanford "EZProxy error page"
    bodies, one is a 27 KB Cloudflare `Just a moment...` challenge, and eight are
    813 KB Elsevier TDM-policy pages fetched through the proxy. That is exactly the
    evidence `proxy_browser`'s comment wants kept -- `attempts` records that a tier
    failed, not the page it failed on -- and it is 7.8 MB in total. Deleting it to
    make the corpus consistent would spend the artifact to tidy the index.
    """
    directory = Path(directory)
    if record is None or record.get("status") == "evicted":
        return None
    if not (directory / store.LANDING_HTML).exists():
        return None
    if (record.get("landing_html") or {}).get("path"):
        return None
    return "unreferenced"


def adopt_landing_html(directory, record: dict) -> dict:
    """Give an unreferenced `landing.html` the manifest entry it never got.

    Mutates `record` and returns the entry. The caller writes the manifest.

    `path`, `bytes` and `sha256` are measured from the file, which is everything
    `store.save_file` would have recorded. What is *not* invented is `url`: the run
    that fetched the page is gone from the record, and a landing page's URL is the
    one field a reader would use to decide whether the page is the article -- so it
    is left absent and `adopted` says why, rather than filled in from the article's
    DOI as though it had been observed.

    **Behind its own flag rather than folded into `--apply`, although it deletes
    nothing.** `extract/extractor.py:905` keys the extraction cache on
    `sha256(manifest.json)`, so writing this entry re-extracts the article once --
    26 of them here. That is cheap and harmless, and it is still a side effect a
    command asked to delete stale files has no business having.
    """
    path = Path(directory) / store.LANDING_HTML
    entry = {
        "path": store.LANDING_HTML,
        "bytes": path.stat().st_size,
        "sha256": sha256_path(path),
        "adopted": True,
        "adopted_reason": ("file predates the manifest's landing_html key; the run "
                           "that saved it is no longer in this record, so its URL "
                           "is unknown"),
    }
    record["landing_html"] = entry
    return entry


def sweep_corpus(corpus_dir, apply: bool = False, slugs=None,
                 include_unique: bool = False, adopt_landing: bool = False) -> List[dict]:
    """Walk the corpus, classify what nothing references, and with `apply` act on it.

    `revalidate_corpus` and `drop_media_corpus`'s shape, including a report for an
    article with nothing to do, so the closing line can say "393 checked, 29 holding
    unreferenced files" and a user can tell the pass ran over the corpus they meant.

    `include_unique` is off by default and that is the safety property, not a
    convenience: `corpus/` is gitignored and unrecoverable, and 0.869 GB of the 1.37
    GB is bytes stored nowhere else. `10.1126/science.aat1699` is why it is a
    separate flag -- the article references no supplements at all and sits on
    `suppl=expected_but_missing`, while 326.9 MB of its supplementary PDFs and tables
    are right there on disk from an older successful fetch, including three
    differently-sized revisions of the same supplementary PDF. A sweep that deleted
    it would be destroying the only copy of a paper's supplements to fix an index.
    """
    root = Path(corpus_dir).expanduser()
    if not root.exists():
        return []
    wanted = set(slugs or ())
    reports = []
    for directory in sorted(p for p in root.iterdir() if p.is_dir()):
        if wanted and directory.name not in wanted:
            continue
        reports.append(sweep_article_report(
            directory, apply=apply, include_unique=include_unique,
            adopt_landing=adopt_landing))
    return reports


def sweep_article_report(directory, apply: bool = False, include_unique: bool = False,
                         adopt_landing: bool = False) -> dict:
    """Report -- and with `apply`, perform -- one article's sweep.

    Returns `{"slug", "doi", "files", "bytes", "kept", "kept_bytes", "landing",
    "removed", "failed", "note"}`. `files` is the rows this pass acted on (or would),
    `kept` the ones it declined to.

    **No manifest write unless something changed, and none at all for a deletion.**
    Unlike `drop_media`, this command does not touch the record when it deletes: the
    files it removes have no entries, so there is nothing to mark, and the manifest
    already describes the corpus correctly the moment they are gone. That inverts
    `drop_media`'s ordering problem -- an interrupt mid-loop leaves fewer orphans and
    a manifest that was true before and is still true -- so the write-per-file it
    needs has no counterpart here. The only write this command can make is
    `adopt_landing`'s, and it happens after the unlinks for the same reason
    `fetch_publication` sweeps after writing: a crash between them leaves the
    harmless state, not the one that makes `manifest_is_complete` false.
    """
    directory = Path(directory)
    record = store.read_manifest(directory)
    report: dict = {"slug": directory.name, "doi": None, "files": [], "bytes": 0,
                    "kept": [], "kept_bytes": 0, "landing": None, "removed": False,
                    "failed": [], "note": None}
    if record is None:
        report["note"] = "no manifest"
        return report
    report["doi"] = record.get("doi")
    if record.get("status") == "evicted":
        report["note"] = "evicted: the bytes are already gone and the record stands"
        return report

    rows = classify(directory, record)
    wanted_kinds = LOSSLESS | ({UNIQUE} if include_unique else set())
    acting = [row for row in rows if row["kind"] in wanted_kinds]
    declined = [row for row in rows if row["kind"] not in wanted_kinds]
    report["files"] = acting
    report["bytes"] = sum(row["bytes"] for row in acting)
    report["kept"] = declined
    report["kept_bytes"] = sum(row["bytes"] for row in declined)
    report["landing"] = landing_html_state(directory, record)

    if not apply:
        return report

    done, failed, freed = [], [], 0
    for row in acting:
        try:
            (directory / row["path"]).unlink()
        except OSError as error:
            failed.append({"path": row["path"], "error": str(error)})
            continue
        freed += row["bytes"]
        done.append(row)
    report.update({"files": done, "bytes": freed, "removed": bool(done),
                   "failed": failed})
    if done:
        report["emptied_dirs"] = _prune_empty_dirs(directory)
    if failed:
        report["note"] = f"{len(failed)} file(s) could not be deleted"
    if adopt_landing and report["landing"] == "unreferenced":
        adopt_landing_html(directory, record)
        store.write_manifest(directory, record)
        report["landing"] = "adopted"
    return report


def summarize(reports: List[dict]) -> dict:
    """Corpus totals for the closing line, split the way the decision splits."""
    affected = [r for r in reports if r["files"] or r["kept"]]
    by_kind: Dict[str, List[int]] = {}
    for report in affected:
        for row in list(report["files"]) + list(report["kept"]):
            slot = by_kind.setdefault(row["kind"], [0, 0])
            slot[0] += 1
            slot[1] += row["bytes"]
    return {
        "articles": len(reports),
        "affected": len(affected),
        "files": sum(len(r["files"]) for r in reports),
        "bytes": sum(r["bytes"] for r in reports),
        "kept": sum(len(r["kept"]) for r in reports),
        "kept_bytes": sum(r["kept_bytes"] for r in reports),
        "landing_unreferenced": sum(1 for r in reports if r["landing"] == "unreferenced"),
        "landing_adopted": sum(1 for r in reports if r["landing"] == "adopted"),
        "by_kind": by_kind,
    }


def human_kinds(by_kind: Dict[str, List[int]]) -> Optional[str]:
    """`"136 redundant 0.5GB, 51 unique 0.9GB"`, largest count first, or None."""
    if not by_kind:
        return None
    return ", ".join(
        f"{count} {kind} {store.human_bytes(size)}"
        for kind, (count, size) in sorted(by_kind.items(), key=lambda kv: -kv[1][0])
    )
