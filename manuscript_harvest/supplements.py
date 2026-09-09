"""Did we actually get the supplements the paper says exist?

`supplementary_status: fetched` is the strongest word the fetch stage has, and
it does not mean what it sounds like. It means one tier's own index bounded the
set that tier enumerated. `10.1016/j.cell.2020.11.028` reads `fetched` --
"supplements complete" in `article_state` -- with 3 of the 14 items its own JATS
declares actually on disk. Nothing in the pipeline compares the two, so a paper
scored from a third of its supplementary material is indistinguishable from a
complete one.

This module makes that comparison. It answers two questions, and keeps them
apart on purpose, because conflating them misreports the cause:

    DECLARED -> FETCHED   did the file arrive?     a fetcher gap
    FETCHED  -> READ      did text come out of it?  an extraction gap

The distinction is not academic. Across the 23 corpus papers whose bundle
caption declares a figure range, captions inside the fetched PDF bind 183 of 192
declared figures; the 9 failures cluster on two papers where the file WAS
fetched and its legend was not extracted. A single "missing" verdict would send
someone to re-run the fetcher for a problem the fetcher does not have.

**What this cannot see, stated plainly.** The declared set comes from the
publisher's own JATS `<supplementary-material>` elements. When a paper's prose
references an item the publisher never declared as a file, nothing here notices:
`10.1016/j.cell.2020.08.013` mentions Figures S1-S7 fifty-four times, its JATS
declares five supplements and none of them is a figure, and this module reports
that paper as fetch-complete. Catching that needs a prose-mention layer with a
labelled ground truth, which is deliberately not in this first pass -- a
mention matcher leaves 193 of 392 papers at "cannot tell" and nobody has
adjudicated a single paper yet. What is here is the part that is objective: a
name the publisher wrote down, and whether we have the bytes.

124 of 392 corpus papers have no JATS at all. They are reported as
`declaration unavailable`, never as complete -- an unmeasurable paper that reads
as a clean one is the failure this whole module exists to prevent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from . import text_bearing
from .extract import jats
from .fetch import store

# -- the three stages a declared item passes through -------------------------
# Named for the stage and its condition, never as a bare verdict: a reader has
# to be able to tell WHICH stage is outstanding, because that is what decides
# which command fixes it.

#: The publisher named it and the bytes are on disk.
FETCHED = "fetched"
#: The publisher named it, it was retrieved, and `drop_media` then deleted it
#: because it is not text-bearing. A decision, not a failure -- but the evidence
#: is just as absent, so it is counted and never silently folded into `fetched`.
REMOVED_BY_POLICY = "removed_by_policy"
#: The publisher named it, no manifest entry mentions it, and every file we did
#: fetch is already spoken for. Nothing else can account for it: a fetcher gap.
NOT_FETCHED = "not_fetched"
#: The publisher named it, no manifest entry mentions it -- but files we fetched
#: are sitting unclaimed, so the NAMES failed to correspond and there is no
#: telling which item those files are.
#:
#: This is not a hedge, it is the majority case. PMC author-manuscript deposits
#: name items `NIHMS1841171-supplement-Table_S1.xlsx` while the tier that
#: actually ran stored the publisher's `mmc1.xlsx`. Of the 45 corpus articles
#: with unmatched declared items, 28 look like this and they carry 176 of the
#: 217 unmatched items -- so calling them all missing would have been wrong four
#: times in five. The give-away is that the counts usually match exactly:
#: 18 declared unmatched against 19 files unclaimed, 13 against 13, 10 against 10.
#:
#: Positional matching would close most of it and is refused on purpose. Across
#: 239 Elsevier entries with a parseable caption the offset between `mmcN` and
#: `Type SK` is +1 in 94 cases, 0 in 58, and also -4, -2, -1, +2, +3, +4 and +7.
#: A rule that is right two thirds of the time here produces confident wrong
#: attributions, which is worse than a named unknown.
NOT_RECONCILED = "not_reconciled"

ITEM_STATES = (FETCHED, REMOVED_BY_POLICY, NOT_FETCHED, NOT_RECONCILED)

#: Extraction statuses that mean no text came out of a file that is on disk.
#: `ok_via_ocr` is deliberately absent: it is a success with a different route,
#: and a consumer testing `status == "ok"` would miscount 68 corpus files.
TEXT_NOT_READ = frozenset({
    "no_text", "image_no_text", "no_text_scanned_pdf", "unsupported_format",
    "too_large", "garbled_text_encoding", "data_file_skipped",
})

#: Where the declared set came from, or why there isn't one.
FROM_JATS = "jats"
NO_DECLARATION = "unavailable"


def _names_for(entry: dict) -> list[str]:
    """Every filename a JATS href might use for this manifest entry.

    Wider than `extractor._supplement_key` in two ways that matter here. It
    reads `name` as well as `path`, because a `drop_media` removal keeps `name`
    and drops `path` -- and those 2,295 corpus entries are exactly the ones this
    module must be able to recognise rather than call missing. And it yields
    bare stems, because publishers rewrite the extension: PNAS declares
    `pnas.1914143116.sd02.xlsx` and ships `sd02.csv`.
    """
    names: list[str] = []
    for value in (entry.get("original_name"), entry.get("path"), entry.get("name")):
        if not value:
            continue
        base = Path(str(value)).name
        if not base:
            continue
        names.append(base)
        # Stored names carry the retrieval-order prefix (`03_mmc7.xlsx`). That
        # prefix is retrieval order and changes on re-fetch; it is never an item
        # number, whatever it looks like.
        if "_" in base:
            names.append(base.split("_", 1)[1])
    return names


def _stem(name: str) -> str:
    return Path(name).stem.lower()


@dataclass
class Item:
    """One `<supplementary-material>` the publisher declared."""

    href: str
    label: Optional[str] = None
    caption: Optional[str] = None
    state: str = NOT_FETCHED
    #: How the declared name was matched to a manifest entry, if it was.
    matched_by: Optional[str] = None
    matched_path: Optional[str] = None
    removed_reason: Optional[str] = None
    #: The extraction stage's verdict on the matched file, when it was fetched.
    read_status: Optional[str] = None
    chars: int = 0

    @property
    def text_read(self) -> bool:
        return self.state == FETCHED and self.chars > 0

    @property
    def refused_by_policy(self) -> Optional[str]:
        """Why `text_bearing` would decline this name, if it would.

        A movie the fetcher missed and a spreadsheet the fetcher missed are the
        same failure and not the same loss: `fetch.text_bearing_only` would have
        deleted the movie on arrival. Of the 41 declared-and-never-retrieved
        items in this corpus, 19 are `.mp4`, `.jpg` or `.gif` and 22 are
        spreadsheets and PDFs. Reporting one number for both would make the
        actionable list twice as long as the work in it.
        """
        return text_bearing.skip_reason(self.href)

    @property
    def evidence_lost(self) -> bool:
        """Declared, absent, and it would have carried readable text."""
        return self.state == NOT_FETCHED and not self.refused_by_policy

    def as_dict(self) -> dict:
        return {
            "href": self.href,
            "label": self.label,
            "caption": self.caption,
            "state": self.state,
            "matched_by": self.matched_by,
            "matched_path": self.matched_path,
            "removed_reason": self.removed_reason,
            "read_status": self.read_status,
            "chars": self.chars,
        }


@dataclass
class Ledger:
    """One article's declared set, reconciled against disk and against text."""

    slug: str
    doi: Optional[str] = None
    declared_from: str = NO_DECLARATION
    items: list[Item] = field(default_factory=list)
    #: Manifest entries no declared item claimed. Not a defect on its own --
    #: a tier may legitimately ship more than the JATS lists -- but it is the
    #: other half of the join and hiding it would make the match rate unreadable.
    unclaimed_files: int = 0
    fetch_status: Optional[str] = None
    #: Counted from disk regardless of whether anything declared them, so an
    #: article with no JATS still gets a real read clause instead of silence.
    files_on_disk: int = 0
    files_with_text: int = 0
    #: Stamped so a stored ledger cannot quietly describe an older extraction.
    #: Without this a sidecar is the same never-fires shape as the supplementary
    #: filter that tested a field blocks.jsonl does not emit.
    manifest_sha256: Optional[str] = None
    extraction_key: Optional[str] = None

    @property
    def declared(self) -> int:
        return len(self.items)

    def count(self, state: str) -> int:
        return sum(1 for item in self.items if item.state == state)

    @property
    def read(self) -> int:
        return sum(1 for item in self.items if item.text_read)

    @property
    def evidence_lost(self) -> int:
        """Declared items that are absent AND would have carried text."""
        return sum(1 for item in self.items if item.evidence_lost)

    @property
    def measurable(self) -> bool:
        return self.declared_from == FROM_JATS and bool(self.items)

    def as_dict(self) -> dict:
        return {
            "slug": self.slug,
            "doi": self.doi,
            "declared_from": self.declared_from,
            "declared": self.declared,
            "fetched": self.count(FETCHED),
            "removed_by_policy": self.count(REMOVED_BY_POLICY),
            "not_fetched": self.count(NOT_FETCHED),
            "not_reconciled": self.count(NOT_RECONCILED),
            "text_read": self.read,
            "unclaimed_files": self.unclaimed_files,
            "fetch_status": self.fetch_status,
            "manifest_sha256": self.manifest_sha256,
            "extraction_key": self.extraction_key,
            "items": [item.as_dict() for item in self.items],
        }


def _read_json(path: Path) -> dict:
    try:
        with path.open(encoding="utf-8") as handle:
            loaded = json.load(handle)
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def reconcile(article_dir: Path) -> Ledger:
    """Reconcile one article's declared supplements against disk and text."""
    article_dir = Path(article_dir)
    manifest = _read_json(article_dir / store.MANIFEST_NAME)
    extraction = _read_json(article_dir / "extracted" / "extraction.json")

    ledger = Ledger(
        slug=article_dir.name,
        doi=manifest.get("doi"),
        fetch_status=manifest.get("supplementary_status"),
        manifest_sha256=extraction.get("source_manifest_sha256"),
        extraction_key=extraction.get("extraction_key"),
    )

    # Per-file extraction outcome, keyed by the stored path. Read before the
    # declaration is even looked for, because an article with no JATS still has
    # files on disk and still deserves a read clause.
    read_by_path = {
        str(record.get("path")): record
        for record in (extraction.get("supplementary") or [])
        if record.get("path")
    }
    for record in read_by_path.values():
        ledger.files_on_disk += 1
        chars = record.get("chars")
        if isinstance(chars, (int, float)) and chars > 0:
            ledger.files_with_text += 1

    xml = article_dir / store.FULLTEXT_XML
    if not xml.is_file():
        return ledger
    try:
        declared = jats.supplement_labels(xml.read_bytes())
    except OSError:
        return ledger
    if not declared:
        # A JATS that declares nothing is a real answer -- the article has no
        # supplements -- and is distinct from having no JATS to ask.
        ledger.declared_from = FROM_JATS
        return ledger

    ledger.declared_from = FROM_JATS

    entries = list(manifest.get("supplementary") or [])
    by_name: dict[str, dict] = {}
    by_stem: dict[str, dict] = {}
    for entry in entries:
        for name in _names_for(entry):
            by_name.setdefault(name.lower(), entry)
            by_stem.setdefault(_stem(name), entry)

    claimed: set[int] = set()
    for href, meta in sorted(declared.items()):
        item = Item(href=href,
                    label=(meta or {}).get("label"),
                    caption=(meta or {}).get("caption"))

        entry = by_name.get(href.lower())
        if entry is not None:
            item.matched_by = "name"
        else:
            entry = by_stem.get(_stem(href))
            if entry is not None:
                # The publisher rewrote the extension between the JATS and the
                # file it served. Matching on the stem recovers it; refusing to
                # would report a fetched file as missing.
                item.matched_by = "stem"

        if entry is None:
            ledger.items.append(item)
            continue

        claimed.add(id(entry))
        if store.entry_removed_by_policy(entry):
            item.state = REMOVED_BY_POLICY
            item.removed_reason = entry.get("removed_reason")
        elif entry.get("path"):
            item.state = FETCHED
            item.matched_path = entry.get("path")
            record = read_by_path.get(str(entry.get("path")))
            if record:
                item.read_status = record.get("status")
                chars = record.get("chars")
                item.chars = int(chars) if isinstance(chars, (int, float)) else 0
        else:
            # An entry with neither a path nor a policy removal is a manifest
            # defect, not a fetched file. Counting it as present would be the
            # optimistic direction, which is the one this module must not take.
            item.state = NOT_FETCHED

        ledger.items.append(item)

    # Only files still on disk can account for a declared item, so a
    # policy-removed entry left unclaimed is not evidence the join failed.
    ledger.unclaimed_files = sum(
        1 for entry in entries
        if id(entry) not in claimed
        and entry.get("path")
        and not store.entry_removed_by_policy(entry)
    )

    # The join failed rather than the fetch: something arrived that no declared
    # name claims, so an unmatched item may well be one of those files under a
    # different name. Which one is not knowable from the manifest, and guessing
    # by position is measurably unsafe -- so the item is named unreconciled
    # rather than reported missing.
    if ledger.unclaimed_files:
        for item in ledger.items:
            if item.state == NOT_FETCHED:
                item.state = NOT_RECONCILED

    return ledger


def describe(ledger: Ledger) -> list[tuple[str, str]]:
    """One clause per stage, every stage, always present.

    Returns `(stage, condition)` pairs rather than a sentence so a caller can
    lay them out however it likes without any of them going missing. Three
    rules, all learned the hard way and all enforced by tests:

    * Every stage appears even when it has nothing to report. A reader must
      never have to infer a stage's condition from its absence.
    * No clause relies on its position to be understood -- each names its own
      stage, because a bare `partial` was meaningless until you knew which of
      three columns it sat in.
    * A settled-sounding value does not get to read as good when its claim is
      weaker than it sounds. `fetched_unverified` means "every file we
      identified arrived", not "the deposit was enumerated", so it is phrased
      as the latter and the raw token stays reachable.
    """
    def plural(n: int, noun: str = "item") -> str:
        return f"{n} {noun}" if n == 1 else f"{n} {noun}s"

    if ledger.declared_from != FROM_JATS:
        declaration = "no JATS for this article, so nothing lists what should exist"
    elif not ledger.items:
        declaration = "JATS lists no supplementary items for this article"
    else:
        declaration = f"JATS lists {plural(ledger.declared)}"

    fetched = ledger.count(FETCHED)
    missing = ledger.count(NOT_FETCHED)
    unmatched = ledger.count(NOT_RECONCILED)
    pruned = ledger.count(REMOVED_BY_POLICY)

    if ledger.declared_from != FROM_JATS or not ledger.items:
        # Say what the fetcher itself claims, phrased so the weaker claim reads
        # as the weaker claim -- and never as a count against a list we do not
        # have.
        raw = ledger.fetch_status or "unknown"
        fetch = {
            "fetched": "files arrived; no list to check them against",
            "fetched_unverified": "files arrived; the deposit was never enumerated",
            "none_listed": "no supplementary files were listed",
            "none_retrieved": "nothing was retrieved",
            "partial_failure": "some retrievals failed",
            "expected_but_missing": "files were expected and none arrived",
        }.get(raw, raw)
        fetch = f"{fetch} [{raw}]"
    else:
        parts = [f"{fetched} of {ledger.declared} retrieved"]
        if missing:
            lost = ledger.evidence_lost
            if lost and lost != missing:
                parts.append(f"{missing} never retrieved ({lost} text-bearing, "
                             f"{missing - lost} non-text the policy would refuse)")
            elif lost:
                parts.append(f"{missing} never retrieved, text-bearing")
            else:
                parts.append(f"{missing} never retrieved, all non-text the policy "
                             f"would refuse")
        if unmatched:
            parts.append(f"{unmatched} unmatched, with "
                         f"{ledger.unclaimed_files} fetched files unaccounted for")
        if pruned:
            parts.append(f"{pruned} deleted as non-text by policy")
        fetch = ", ".join(parts)

    if ledger.declared_from != FROM_JATS or not ledger.items:
        # Nothing declared these files, so they cannot be counted against a
        # list -- but they exist, and saying "nothing to read" about an article
        # holding 11 supplements would be the false-clean reading this module
        # is built to prevent.
        if not ledger.files_on_disk:
            read = "no supplementary file on disk"
        else:
            read = (f"{ledger.files_with_text} of "
                    f"{plural(ledger.files_on_disk, 'file')} on disk yielded text; "
                    f"which items they are is unknown")
    elif not fetched:
        # No declared item matched a file, which is not the same as no file.
        # Saying "nothing to read" while three supplements sit on disk would
        # describe the join's failure as the article's emptiness.
        read = ("no supplementary file on disk" if not ledger.files_on_disk else
                f"no declared item matched a file, though "
                f"{ledger.files_with_text} of "
                f"{plural(ledger.files_on_disk, 'file')} on disk yielded text")
    elif ledger.read == fetched:
        read = f"{ledger.read} of {plural(fetched, 'retrieved item')} yielded text"
    else:
        silent = [item for item in ledger.items
                  if item.state == FETCHED and not item.text_read]
        reasons = sorted({item.read_status or "no record" for item in silent})
        read = (f"{ledger.read} of {plural(fetched, 'retrieved item')} yielded text; "
                f"{len(silent)} did not ({', '.join(reasons)})")

    return [("declaration", declaration), ("fetch", fetch), ("read", read)]


#: Written beside `extraction.json`, in the extract stage's own output
#: directory, because that is what it describes -- and deleted with it when an
#: article is re-extracted or evicted. The CODE stays out of
#: `manuscript_harvest/extract/`: `source_fingerprint()` globs `extract/*.py`
#: into `extraction_key`, so a module added there would invalidate all 392
#: extractions to add a report that reads them.
SIDECAR_NAME = "supplement_items.json"

#: Bumped when the reconciliation itself changes meaning, so an old sidecar is
#: recognisably old rather than quietly wrong.
SIDECAR_VERSION = 1


def sidecar_path(article_dir: Path) -> Path:
    return Path(article_dir) / "extracted" / SIDECAR_NAME


def write_sidecar(article_dir: Path, ledger: Optional[Ledger] = None) -> Path:
    """Persist one article's ledger, stamped against the extraction it describes."""
    article_dir = Path(article_dir)
    if ledger is None:
        ledger = reconcile(article_dir)
    payload = ledger.as_dict()
    payload["sidecar_version"] = SIDECAR_VERSION
    path = sidecar_path(article_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1, sort_keys=False) + "\n",
                    encoding="utf-8")
    return path


def load_sidecar(article_dir: Path) -> dict:
    return _read_json(sidecar_path(article_dir))


def stale_reason(article_dir: Path) -> Optional[str]:
    """Why a stored sidecar no longer describes this article, or None.

    A sidecar has no equivalent of `extraction_key` to invalidate it, so
    without this it would go on describing an extraction that has since been
    redone -- silently, which is the same failure shape as the supplementary
    filter that tested a field `blocks.jsonl` never emitted and therefore never
    fired. Callers must check; nothing else will.
    """
    article_dir = Path(article_dir)
    stored = load_sidecar(article_dir)
    if not stored:
        return "no sidecar has been written for this article"
    if stored.get("sidecar_version") != SIDECAR_VERSION:
        return (f"written by sidecar version {stored.get('sidecar_version')!r}, "
                f"this is version {SIDECAR_VERSION}")
    extraction = _read_json(article_dir / "extracted" / "extraction.json")
    # The sidecar's own field names, paired with the extraction record's. They
    # differ, and reading one name from both is how this check first came to
    # report every article stale -- the same never-fires shape as the filter
    # this module's staleness stamp exists to avoid, caught only because a
    # freshly written sidecar claimed to be out of date.
    for mine, theirs, label in (
        ("extraction_key", "extraction_key", "the extraction"),
        ("manifest_sha256", "source_manifest_sha256", "the manifest"),
    ):
        current = extraction.get(theirs)
        if stored.get(mine) != current:
            return (f"{label} has changed since this was written "
                    f"({theirs}: {stored.get(mine)!r} -> {current!r})")
    return None


def survey(corpus_dir: Path, slugs: Optional[Iterable[str]] = None) -> list[Ledger]:
    """Reconcile every article in a corpus, or the named ones."""
    corpus_dir = Path(corpus_dir)
    if slugs is None:
        directories = sorted(p for p in corpus_dir.iterdir()
                             if p.is_dir() and (p / store.MANIFEST_NAME).is_file())
    else:
        directories = [corpus_dir / slug for slug in slugs]
    return [reconcile(directory) for directory in directories]
