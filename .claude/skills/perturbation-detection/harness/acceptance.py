#!/usr/bin/env python3
"""Score an acceptance run against the expectations a version declared.

    python -m harness.acceptance history/acceptance/v0.0.26.yaml

No model calls. Reads a spec, reads the validated records of the runs it names,
and decides whether the version did what its author said it would.

**Why one runner and not a script per version.** Twelve acceptance documents
shipped with two scorers between them, each hardcoding its own papers and its
own expectations, so versions 0.0.11 to 0.0.22 cannot be re-scored at all and
every new script was a fresh chance at the same mistake. It was not a
hypothetical one: the v0.0.25 gate read a key the verifier never returned,
dropped all fourteen defect claims, and would have certified a pass over a
mechanism that never ran.

**The vocabulary is taken from the two scorers rather than invented.** Between
them they express five things -- papers that must not move, papers that must,
nothing else moving, agreement across two runs of the same input, and a
mechanism having actually fired. Nothing here is a criterion kind that no real
acceptance test has needed, because a vocabulary guessed in advance is the thing
`task/decide.yaml` argues against.

**Every guard below was learned rather than designed.** A criterion that
evaluates nothing prints PASS; a paper with no result makes every failure list
empty; a gate can report FAIL and still exit 0. All three shipped at least once.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.pack import PackError  # noqa: E402
from harness.runroot import run_root  # noqa: E402

#: The criterion kinds any spec may declare. Closed, because an unknown kind
#: silently scoring nothing is the failure this module exists to prevent.
KINDS = ("anchors", "movers", "no-unpredicted-movement", "stable", "exercised")

#: Roles a paper may carry. `free` is explicit rather than a default: a paper in
#: the set with no role stated is an omission, and omissions are what
#: `no-unpredicted-movement` is supposed to catch.
ROLES = ("anchor", "mover", "free")


class SpecError(Exception):
    """The spec could not be read, or declares something unscoreable. Always
    fatal: a spec this runner half-understands grades a subset nobody chose."""


def dotted(record: dict, path: str) -> Any:
    """`validation.stage_a` out of a nested record. Missing is None."""
    node: Any = record
    for part in path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def load_spec(path: Path) -> dict:
    try:
        spec = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise SpecError(f"{path}: {exc}") from exc
    if not isinstance(spec, dict):
        raise SpecError(f"{path}: not a mapping")

    for key in ("version", "runs", "field", "papers", "criteria"):
        if not spec.get(key):
            raise SpecError(f"{path}: no {key!r}")
    runs = spec["runs"]
    for key in ("r1", "r2"):
        if not runs.get(key):
            raise SpecError(
                f"{path}: runs.{key} is required. Two runs of byte-identical "
                f"input, because this prompt disagrees with itself on a few "
                f"percent of papers and a single run cannot tell an effect "
                f"from that noise.")

    for paper, entry in spec["papers"].items():
        if not isinstance(entry, dict):
            raise SpecError(f"{path}: {paper} is not a mapping")
        role = entry.get("role")
        if role not in ROLES:
            raise SpecError(
                f"{path}: {paper} has role {role!r}, not one of {list(ROLES)}. "
                f"A paper with no role is the omission "
                f"'no-unpredicted-movement' exists to catch.")
        # YAML 1.1 reads bare `no` as False, and `expect: no` is the single
        # most natural thing to write here. Unguarded it loads as a boolean,
        # never equals the string the model emitted, and every anchor holding
        # a negative fails for a reason the message would not explain.
        # `harness/pack.py` carries the same guard for list elements; this is
        # the scalar case, and `blocking: true` is left alone because that one
        # really is a boolean.
        for key in ("expect", "was"):
            if isinstance(entry.get(key), bool):
                raise SpecError(
                    f"{path}: {paper} has {key}: {str(entry[key]).lower()}, "
                    f"which YAML read as a boolean. Quote it -- "
                    f'{key}: "no" -- because the record holds strings.')
        if role in ("anchor", "mover") and not entry.get("expect"):
            raise SpecError(f"{path}: {paper} is a {role} with no `expect`")
        if role == "mover" and not entry.get("was"):
            raise SpecError(
                f"{path}: {paper} is a mover with no `was`. A movement needs "
                f"both ends or 'it moved' cannot be checked.")

    seen = set()
    for criterion in spec["criteria"]:
        cid, kind = criterion.get("id"), criterion.get("kind")
        if not cid:
            raise SpecError(f"{path}: a criterion has no id")
        if cid in seen:
            raise SpecError(f"{path}: two criteria share the id {cid!r}")
        seen.add(cid)
        if kind not in KINDS:
            raise SpecError(
                f"{path}: criterion {cid!r} has kind {kind!r}, not one of "
                f"{list(KINDS)}. An unrecognised kind would score nothing and "
                f"report a pass.")
        if kind == "stable" and not criterion.get("field"):
            raise SpecError(f"{path}: criterion {cid!r} (stable) needs a `field`")
        if kind == "exercised" and not criterion.get("predicate"):
            raise SpecError(
                f"{path}: criterion {cid!r} (exercised) needs a `predicate`")
    return spec


def _read(run: Path, paper: str) -> Optional[dict]:
    path = run / "validated" / f"{paper.replace('/', '_')}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except ValueError as exc:
        raise SpecError(f"{path}: {exc}") from exc


def gather(spec: dict, root: Path) -> dict:
    """Both runs' records for every paper, plus the baseline if one is named."""
    runs = spec["runs"]
    out: Dict[str, dict] = {}
    for paper in spec["papers"]:
        out[paper] = {
            "r1": _read(root / runs["r1"], paper),
            "r2": _read(root / runs["r2"], paper),
            "baseline": (_read(root / runs["baseline"], paper)
                         if runs.get("baseline") else None),
        }
    return out


def score(spec: dict, records: dict) -> dict:
    """Evaluate every criterion, and count what each one actually looked at.

    The count is not decoration. A criterion whose applicable set is empty
    prints PASS while proving nothing, which is how a dead gate certifies
    itself -- so `applicable` travels beside every verdict and a blocking
    criterion that examined nothing FAILS.
    """
    field = spec["field"]
    papers = spec["papers"]

    pending = sorted(p for p, r in records.items()
                     if r["r1"] is None or r["r2"] is None)

    def value(paper: str, which: str = "r1") -> Any:
        record = records[paper][which]
        return None if record is None else dotted(record, field)

    results = []
    for criterion in spec["criteria"]:
        kind, cid = criterion["kind"], criterion["id"]
        failures: List[str] = []
        applicable = 0
        shortfall = 0

        if kind == "anchors":
            for paper, entry in papers.items():
                if entry["role"] != "anchor" or paper in pending:
                    continue
                applicable += 1
                got = value(paper)
                if got != entry["expect"]:
                    failures.append(f"{paper}: want {entry['expect']!r}, got {got!r}")

        elif kind == "movers":
            for paper, entry in papers.items():
                if entry["role"] != "mover" or paper in pending:
                    continue
                applicable += 1
                got = value(paper)
                if got != entry["expect"]:
                    failures.append(
                        f"{paper}: want {entry['was']!r} -> {entry['expect']!r}, "
                        f"got {got!r}")

        elif kind == "no-unpredicted-movement":
            for paper, entry in papers.items():
                if entry["role"] != "free" or paper in pending:
                    continue
                base = records[paper]["baseline"]
                if base is None:
                    continue          # counted below, not silently passed
                applicable += 1
                before, got = dotted(base, field), value(paper)
                if before != got:
                    failures.append(f"{paper}: {before!r} -> {got!r}, unpredicted")

        elif kind == "stable":
            path = criterion["field"]
            for paper in papers:
                if paper in pending:
                    continue
                applicable += 1
                a = dotted(records[paper]["r1"], path)
                b = dotted(records[paper]["r2"], path)
                if a != b:
                    failures.append(f"{paper}: r1 {a!r} != r2 {b!r}")

        elif kind == "exercised":
            path = criterion["predicate"]
            minimum = int(criterion.get("minimum", 1))
            for paper in papers:
                if paper in pending:
                    continue
                if any(dotted(records[paper][r] or {}, path)
                       for r in ("r1", "r2")):
                    applicable += 1
            # Deliberately NOT a failure string: an exercised criterion below
            # its minimum is reported as NOT EXERCISED, which is the word the
            # scorer this replaces used and the one that says what is wrong.
            # A FAIL reads as "the mechanism misbehaved"; the truth is that it
            # never ran, and agreement over a mechanism that never ran is not
            # evidence about the mechanism.
            shortfall = minimum if applicable < minimum else 0

        results.append({
            "id": cid, "kind": kind, "applicable": applicable,
            "failures": failures,
            "minimum": (shortfall if kind == "exercised" else 0),
            "blocking": bool(criterion.get("blocking", True)),
            "note": criterion.get("note"),
        })
    return {"pending": pending, "criteria": results}


def report(spec: dict, outcome: dict) -> int:
    def line(text: str = "") -> None:
        print(text, file=sys.stderr)

    line(f"acceptance: {spec['version']}  "
         f"({spec['runs']['r1']} vs {spec['runs']['r2']})")
    if spec.get("what_changed"):
        line(f"  {str(spec['what_changed']).strip()}")
    line()

    if outcome["pending"]:
        line(f"INCOMPLETE: {len(outcome['pending'])} of {len(spec['papers'])} "
             f"papers have no result in both runs. Nothing below is evaluated "
             f"-- an empty failure list over unscored papers is not a pass.")
        for paper in outcome["pending"]:
            line(f"  pending: {paper}")
        return 1

    failed = False
    for entry in outcome["criteria"]:
        blocking = entry["blocking"]
        if entry["failures"]:
            verdict = "FAIL"
            failed = failed or blocking
        elif entry.get("minimum"):
            verdict = f"NOT EXERCISED (<{entry['minimum']})"
            failed = failed or blocking
        elif entry["applicable"] == 0:
            # A criterion that examined nothing has not passed. Blocking ones
            # fail; the rest say so out loud rather than printing PASS.
            verdict = "NOT EXERCISED"
            failed = failed or blocking
        else:
            verdict = "PASS"
        flag = "" if blocking else " (non-blocking)"
        line(f"  {verdict:14} {entry['id']:34} "
             f"{entry['applicable']:>3} examined{flag}")
        if entry["note"]:
            line(f"                 {entry['note']}")
        for failure in entry["failures"]:
            line(f"                 - {failure}")

    line()
    line("PASS" if not failed else "FAIL")
    return 1 if failed else 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("spec", help="the version's expectations file")
    parser.add_argument("--run-root", default=None,
                        help="where the run directories live; default from the pack")
    args = parser.parse_args(argv)

    try:
        root = Path(args.run_root) if args.run_root else run_root()
    except PackError as exc:
        print(f"pack: {exc}", file=sys.stderr)
        return 2
    if not root.is_dir():
        print(f"{root}: not a directory", file=sys.stderr)
        return 2

    try:
        spec = load_spec(Path(args.spec))
        outcome = score(spec, gather(spec, root))
    except SpecError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    return report(spec, outcome)


if __name__ == "__main__":
    sys.exit(main())
