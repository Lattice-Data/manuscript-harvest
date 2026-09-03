"""What a run directory is, and the one rule every reader of one must obey.

A run directory holds `manifest.json` beside `prompts/`, `raw/` and `validated/`.
Six modules read one, and before this module each read `manifest.json` with a bare
`json.loads(...read_text())` -- so the most common mistake, a wrong `--work`, was a
twelve-line traceback at five duplicated sites.

**The rule that matters is the other one.** A reader that loads nothing must not
report. `pe.compare` pointed at a two-run baseline directory used to print

    baseline v? -> v comparison over 0 paper(s)
    ...
      every change is accounted for by a known v0.0.5 mechanism.

and exit 0 -- a PASS on an empty set, which is how a prompt version gets accepted
having compared nothing. `pe.audit` printed six screens of zero and `pe.summarize`
wrote a CSV of blank rows, both exit 0. The directory was the one SKILL.md names.

That is the repo's own forbidden failure -- emptiness with no account of itself --
so coverage is computed here, printed by every caller, and `require_papers()`
refuses outright when the count is zero. A PARTIAL run is different and stays
allowed: validating 50 of 392 and summarising them is the normal mid-run move. It
just has to say so.

The layout also has a second shape this module knows about. The acceptance protocol
preserves two runs of one prompt as `<baseline>/{manifest.json, r1/, r2/}`, and only
`pe.compare --baseline2` ever knew that. A bare `--work <that dir>` finds no
`validated/` at all, which is where the silent zero came from. `resolve_run_dir`
names the shape and says which subdirectory to pass rather than guessing: picking
one silently is the same class of mistake as handing `--baseline2` a different
prompt version, which `noise_floor` already refuses.
"""

from __future__ import annotations

import json
from pathlib import Path

#: Subdirectory a two-run baseline stores its first and second run under.
RUN_SUBDIRS = ("r1", "r2")


class RunError(Exception):
    """A run directory cannot be read, or would produce a vacuous report.

    Carries a message written for the person who typed the command, not a
    traceback. Every `main()` catches it, prints one line, and exits non-zero.
    """


def _looks_like_two_run_baseline(work: Path) -> list[str]:
    """Which of r1/r2 exist under `work` with a validated/ of their own."""
    return [name for name in RUN_SUBDIRS if (work / name / "validated").is_dir()]


def resolve_run_dir(work: Path, *, prefer: str | None = None) -> Path:
    """The directory that actually holds this run's `validated/`.

    `prefer` names the subdirectory to take when `work` is a two-run baseline:
    "r1" for the baseline itself, "r2" for its second run. With no preference a
    two-run directory is an error naming both options, because choosing one
    silently is how a version diff gets reported as run-to-run variance.
    """
    if (work / "validated").is_dir() or (work / "raw").is_dir():
        return work
    runs = _looks_like_two_run_baseline(work)
    if not runs:
        return work
    if prefer and prefer in runs:
        return work / prefer
    raise RunError(
        f"{work} is a two-run baseline: it holds {'/'.join(runs)} rather than a "
        f"validated/ of its own. Pass {work / runs[0]} to compare against the "
        f"first run, or {work / runs[-1]} for the second. Choosing one here would "
        f"make a version difference look like run-to-run variance.")


def manifest_dir(run_dir: Path) -> Path:
    """Where `run_dir`'s manifest lives -- itself, or its parent.

    A two-run baseline keeps ONE manifest beside `r1/` and `r2/`, because both
    runs were prepared from the same assembly; that is the whole basis for calling
    their disagreement a noise floor. So `<baseline>/r1` legitimately has a
    `validated/` and no manifest of its own.
    """
    if (run_dir / "manifest.json").is_file():
        return run_dir
    if run_dir.name in RUN_SUBDIRS and (run_dir.parent / "manifest.json").is_file():
        return run_dir.parent
    return run_dir


def load_manifest(work: Path) -> list[dict]:
    """Read `<work>/manifest.json`, or raise RunError with what to do about it."""
    path = work / "manifest.json"
    if not path.is_file():
        runs = _looks_like_two_run_baseline(work)
        if runs:
            hint = (f" It does hold {'/'.join(runs)}, so this may be a two-run "
                    f"baseline -- try {work / runs[0]}.")
        elif work.is_dir():
            hint = " The directory exists but has no manifest; run pe.prepare first."
        else:
            hint = " The directory does not exist -- check --work."
        raise RunError(f"no manifest at {path}.{hint}")
    try:
        manifest = json.loads(path.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RunError(f"{path} is not readable JSON: {exc}") from exc
    if not isinstance(manifest, list):
        raise RunError(f"{path} is not a list of manifest entries")
    return manifest


class Run:
    """A run directory plus what could actually be loaded out of it.

    `expected` counts manifest entries that were prepared successfully, so a paper
    that never had a blocks.jsonl is not held against the coverage. `loaded` counts
    the ones a reader got a record for. The two differing is legitimate mid-run and
    is reported; `loaded == 0` is not, and `require_papers` says so.
    """

    def __init__(self, work: Path, manifest: list[dict], stage: str) -> None:
        self.work = work
        self.manifest = manifest
        self.stage = stage
        self.entries = [e for e in manifest if "error" not in e]
        self.no_input = [e["doi"] for e in manifest if "error" in e]
        self.records: dict[str, dict] = {}
        self.unreadable: list[tuple[str, str]] = []
        self.absent: list[str] = []

    @property
    def expected(self) -> int:
        return len(self.entries)

    @property
    def loaded(self) -> int:
        return len(self.records)

    @property
    def complete(self) -> bool:
        return self.loaded == self.expected and not self.unreadable

    def coverage(self) -> str:
        """One line naming what was read and what was not. Always printed.

        Unconditional rather than only-on-a-problem: a reader who never sees the
        line cannot tell a full run from a run whose report happens to be short.
        """
        parts = [f"{self.loaded}/{self.expected} paper(s) with a {self.stage} record"]
        if self.absent:
            parts.append(f"{len(self.absent)} not yet {self.stage}")
        if self.unreadable:
            parts.append(f"{len(self.unreadable)} unreadable")
        if self.no_input:
            parts.append(f"{len(self.no_input)} never prepared")
        return "coverage: " + ", ".join(parts)

    def require_papers(self, tool: str) -> None:
        """Refuse to produce a report over zero papers.

        The whole point of this module. A verdict over an empty set reads exactly
        like a clean one, and that is the one thing this pipeline is not allowed
        to emit.
        """
        if self.loaded:
            return
        detail = f"{self.expected} manifest entr(ies)"
        if self.unreadable:
            detail += f", {len(self.unreadable)} unreadable"
        runs = _looks_like_two_run_baseline(self.work)
        hint = (f" {self.work} holds {'/'.join(runs)} -- pass one of those "
                f"subdirectories instead."
                if runs else
                f" Run pe.validate --work {self.work} first."
                if self.stage == "validated" else "")
        raise RunError(
            f"{tool}: no {self.stage} record could be read from {self.work} "
            f"({detail}). Refusing to report on an empty set -- a verdict over zero "
            f"papers is indistinguishable from a clean one.{hint}")


def load_validated(work: Path, *, prefer: str | None = None) -> Run:
    """Load `<work>/validated/<doi>.json` for every prepared manifest entry.

    A record that will not parse is recorded and skipped rather than aborting the
    run: `pe.summarize` and `pe.audit` both used to die on a single corrupt file
    and produce no output at all, while `pe.validate` had always degraded politely
    on the equivalent bad raw file.
    """
    resolved = resolve_run_dir(work, prefer=prefer)
    run = Run(resolved, load_manifest(manifest_dir(resolved)), "validated")
    for entry in run.entries:
        doi = entry["doi"]
        path = resolved / "validated" / f"{doi}.json"
        if not path.is_file():
            run.absent.append(doi)
            continue
        try:
            run.records[doi] = json.loads(path.read_text())
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            run.unreadable.append((doi, str(exc)[:80]))
    return run
