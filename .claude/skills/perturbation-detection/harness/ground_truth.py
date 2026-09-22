#!/usr/bin/env python3
"""Score stored results against the hand-ruled ledger the pack points at.

    python -m harness.ground_truth [--corpus DIR] [--quiet]

No model calls. Reads the ledger the pack declares under `ground_truth:` and the
per-paper result files already on disk, and answers one question: does the
current pack still produce the verdicts a human ruled by reading the paper?

**Why this is a program and not a careful read.** The ledger held 26 rulings
over 21 papers and nothing read it, so a criteria change could contradict a
ruling from four versions earlier and nobody found out until someone re-read the
file. 21 papers is also cheap where a full re-score is 392, which makes this the
fast regression check the heavyweight version-comparison tools are not.

**A verdict is not automatically an expectation, and conflating the two is
dangerous rather than merely wrong.** Some rulings rest on facts outside the
paper -- which species the deposit holds, whether samples reached a collection --
and a reader of the paper alone cannot and must not guess at them. Scored
naively those look like two failures, and the obvious way to clear them is to
change the criteria until they pass, which would make the classifier worse at
exactly the point the project decided it must not guess. So the ledger marks what
each verdict has authority over, and only `binding` is graded.

**Nothing here resolves a changed ruling.** Where a later entry supersedes an
earlier one with a different verdict, that is a human's call: the pair is
reported and this exits non-zero until the row carries a seal. Preferring the
later date would be this program deciding which reading of a paper is right.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.pack import PackError, load as load_pack  # noqa: E402

#: One ledger row. `|` delimited, seven cells, and no cell may be guessed at:
#: the first program to read this table matched the verdict with an alternation
#: that put `no` before `not_applicable`, so a `not_applicable` row was read as
#: `no` and reported a disagreement that did not exist. Hence exact membership
#: of a closed set below, and a hard failure on anything else.
_ROW = re.compile(r"^\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|\s*$")

#: What a row means it is authority over. Only the first is graded.
GRADED = "binding"
EXPECTED_TO_DIFFER = "out-of-scope"
SUB_QUESTION = "partial"
DECLINED = "not-adopted"

_NONE = {"-", "--", "---", "—", "–", ""}


class Entry(NamedTuple):
    number: int
    paper: str
    verdict: str
    kind: str
    date: str
    supersedes: Optional[int]
    sealed: Optional[str]


class LedgerError(Exception):
    """The ledger could not be read. Never a soft failure: a ledger this program
    cannot parse is indistinguishable from one with no rows in it, and a check
    that passes over no rows is worse than no check."""


def _cell(text: str) -> str:
    return text.strip().strip("*").strip("`").strip()


def _optional_int(text: str, row: int, column: str) -> Optional[int]:
    value = _cell(text)
    if value in _NONE:
        return None
    if not value.isdigit():
        raise LedgerError(
            f"row {row}: {column} is {value!r}, which is neither a row number "
            f"nor empty. Use the number of the entry it replaces, or '-'.")
    return int(value)


#: A seal is a human approving one reading of a paper over another, so it has to
#: look like one. Any non-empty string used to do, which means a stray character
#: in the last column silently approves a reversal -- the one thing this module
#: refuses to decide for itself, defeated by a typo.
_SEAL = re.compile(r"^\d{4}-\d{2}-\d{2}\s+\S")


def _seal(text: str, row: int) -> Optional[str]:
    value = _cell(text)
    if value in _NONE:
        return None
    if not _SEAL.match(value):
        raise LedgerError(
            f"row {row}: sealed is {value!r}. A seal records who approved a "
            f"changed ruling and when, as 'YYYY-MM-DD name'.")
    return value


def parse_ledger(text: str, verdicts: List[str], kinds: List[str]) -> List[Entry]:
    """Every row of the one pipe table whose header starts with `| # | paper`.

    Stops at the first blank line after the header, so the prose below -- which
    contains other tables -- is not scanned. A second table matching the header
    would be ambiguous rather than additive, and is refused.
    """
    lines = text.split("\n")
    heads = [i for i, line in enumerate(lines)
             if line.startswith("| # | paper")]
    if not heads:
        raise LedgerError(
            "no ledger table found. The machine-readable table's header line "
            "must start with '| # | paper'.")
    if len(heads) > 1:
        raise LedgerError(
            f"{len(heads)} tables start with '| # | paper' (lines "
            f"{[i + 1 for i in heads]}). Exactly one is the ledger.")

    entries: List[Entry] = []
    seen: Dict[int, int] = {}
    for offset, line in enumerate(lines[heads[0] + 2:], start=heads[0] + 3):
        if not line.strip():
            break
        if set(line.strip()) <= set("|- "):        # a separator row
            continue
        match = _ROW.match(line)
        if not match:
            raise LedgerError(
                f"row {offset}: {line.strip()!r} is not seven pipe-delimited "
                f"cells. Every row of the ledger is graded or explicitly not, "
                f"so a row that cannot be read is a failure rather than a skip.")
        num_s, paper, verdict, kind, date, supersedes, sealed = match.groups()

        number = _optional_int(num_s, offset, "the row number")
        if number is None:
            raise LedgerError(f"row {offset}: no row number")
        if number in seen:
            raise LedgerError(
                f"row {offset}: number {number} already used on row "
                f"{seen[number]}. Numbers are how `supersedes` points.")
        seen[number] = offset

        verdict = _cell(verdict)
        if verdict not in verdicts:
            raise LedgerError(
                f"row {offset} (entry {number}): verdict {verdict!r} is not one "
                f"of {verdicts}. Commentary belongs in the prose, not this cell.")
        kind = _cell(kind)
        if kind not in kinds:
            raise LedgerError(
                f"row {offset} (entry {number}): kind {kind!r} is not one of "
                f"{kinds}.")

        entries.append(Entry(
            number=number,
            paper=_cell(paper),
            verdict=verdict,
            kind=kind,
            date=_cell(date),
            supersedes=_optional_int(supersedes, offset, "supersedes"),
            sealed=_seal(sealed, offset),
        ))

    if not entries:
        raise LedgerError("the ledger table has a header and no rows.")

    # A blank line ends the table, which makes a stray one in the middle of it
    # a SILENT truncation: every row below simply stops being graded and the
    # summary still reads "0 disagree". So anything below that still looks like
    # a numbered row is an error rather than prose.
    tail = lines[offset:] if entries else []
    stray = [i for i, line in enumerate(tail, start=offset + 1)
             if _ROW.match(line) and _cell(_ROW.match(line).group(1)).isdigit()]
    if stray:
        raise LedgerError(
            f"line(s) {stray} look like ledger rows but fall after the blank "
            f"line that ends the table, so nothing would grade them. A blank "
            f"line inside the table truncates it silently -- remove it.")
    return entries


def unsealed_changes(entries: List[Entry]) -> List[tuple]:
    """Pairs where a later entry replaced an earlier one with a DIFFERENT
    verdict and nobody has sealed it.

    Same verdict needs no seal: re-confirming a ruling from a reader who had not
    seen it is evidence, not a change of mind.
    """
    by_number = {entry.number: entry for entry in entries}
    out = []
    for entry in entries:
        if entry.supersedes is None:
            continue
        earlier = by_number.get(entry.supersedes)
        if earlier is None:
            raise LedgerError(
                f"entry {entry.number} supersedes {entry.supersedes}, which is "
                f"not in the ledger.")
        if earlier.paper != entry.paper:
            raise LedgerError(
                f"entry {entry.number} ({entry.paper}) supersedes "
                f"{earlier.number} ({earlier.paper}), a different paper.")
        if earlier.verdict != entry.verdict and not entry.sealed:
            out.append((earlier, entry))
    return out


#: Kinds that assert a verdict for the paper as a whole. Two live entries of
#: these kinds on one paper are two answers to one question.
_PAPER_LEVEL = (GRADED, EXPECTED_TO_DIFFER)


def live(entries: List[Entry]) -> List[Entry]:
    """Entries nothing later replaces."""
    replaced = {e.supersedes for e in entries if e.supersedes is not None}
    return [e for e in entries if e.number not in replaced]


def conflicting(entries: List[Entry]) -> List[List[Entry]]:
    """Papers with more than one LIVE paper-level entry.

    `unsealed_changes` only sees pairs someone linked with `supersedes`, which
    makes forgetting the link the way to defeat it: add a second ruling on a
    paper already in the ledger, leave the column at `-`, and both entries stay
    live. If they disagree the grader reports one as a criteria bug -- pointing
    at the prompt for what is really a ledger mistake -- and if they agree it
    silently double-counts.

    A `partial` may sit beside a paper-level entry, because settling a
    sub-question is not answering the paper. Two paper-level entries cannot.
    """
    by_paper: Dict[str, List[Entry]] = {}
    for entry in live(entries):
        if entry.kind in _PAPER_LEVEL:
            by_paper.setdefault(entry.paper, []).append(entry)
    return [sorted(group, key=lambda e: e.number)
            for group in by_paper.values() if len(group) > 1]


def _slug(paper: str) -> str:
    return paper.replace("/", "_")


def check(entries: List[Entry], corpus: Path, field: str,
          result_name: str) -> dict:
    """Read the stored verdict for every live entry and compare where graded."""
    rows = []
    for entry in sorted(live(entries), key=lambda e: e.number):
        path = corpus / _slug(entry.paper) / "extracted" / result_name
        if not path.exists():
            found = None
        else:
            try:
                found = json.loads(path.read_text()).get(field)
            except (ValueError, OSError) as exc:
                raise LedgerError(f"{path}: {exc}") from exc
        rows.append({"entry": entry, "found": found,
                     "agrees": found == entry.verdict})
    graded = [r for r in rows if r["entry"].kind == GRADED]
    absent = [r for r in graded if r["found"] is None]
    return {
        "rows": rows,
        "graded": graded,
        "absent": absent,
        "disagree": [r for r in graded
                     if r["found"] is not None and not r["agrees"]],
        "differ_expected": [r for r in rows
                            if r["entry"].kind == EXPECTED_TO_DIFFER],
        "sub_question": [r for r in rows if r["entry"].kind == SUB_QUESTION],
        "declined": [r for r in rows if r["entry"].kind == DECLINED],
    }


def _report(result: dict, unsealed: list, clashes: list, quiet: bool) -> None:
    def line(text: str = "") -> None:
        print(text, file=sys.stderr)

    graded, disagree = result["graded"], result["disagree"]
    absent = result["absent"]
    if not quiet:
        for row in result["rows"]:
            entry = row["entry"]
            if entry.kind != GRADED:
                continue
            mark = "ok " if row["agrees"] else "BAD"
            line(f"  {mark} {entry.number:>3}  {entry.paper:36s} "
                 f"ruled {entry.verdict:15s} found {row['found']}")
    line()
    line(f"graded (binding): {len(graded) - len(absent)} compared, "
         f"{len(graded) - len(absent) - len(disagree)} agree, "
         f"{len(disagree)} disagree, {len(absent)} not in the corpus")

    for label, key, note in (
        ("expected to differ", "differ_expected",
         "the ruling rests on something outside the paper"),
        ("sub-question only", "sub_question",
         "settles a sub-question, not the paper"),
        ("not adopted", "declined", "considered and declined"),
    ):
        rows = result[key]
        if not rows:
            continue
        line(f"{label}: {len(rows)} not graded -- {note}")
        for row in rows:
            entry = row["entry"]
            line(f"       {entry.number:>3}  {entry.paper:36s} "
                 f"ruled {entry.verdict:15s} found {row['found']}")

    if disagree:
        line()
        line("DISAGREEMENTS on binding rulings. A ruling wins over the spec, so "
             "each of these is a bug in the criteria -- unless the ruling rests "
             "on something the classifier cannot see, in which case its kind is "
             "wrong and the prose has to say why.")
        for row in disagree:
            entry = row["entry"]
            line(f"  entry {entry.number}: {entry.paper} ruled "
                 f"{entry.verdict!r}, found {row['found']!r}")
    if absent:
        line()
        line("NOT IN THE CORPUS -- a binding ruling nothing scored is not a "
             "pass, it is an ungraded row.")
        for row in absent:
            line(f"  entry {row['entry'].number}: {row['entry'].paper}")
    if clashes:
        line()
        line("TWO LIVE RULINGS ON ONE PAPER. Nothing says which governs, and "
             "neither is superseded -- so a grader would report one of them as "
             "a criteria bug when the mistake is here. Point the later entry "
             "at the earlier one with `supersedes`, and seal it if the verdict "
             "changed.")
        for group in clashes:
            line(f"  {group[0].paper}: entries "
                 + ", ".join(f"{e.number} ({e.verdict!r}, {e.date})"
                             for e in group))
    if unsealed:
        line()
        line("UNSEALED CHANGES. A later entry replaced an earlier one with a "
             "different verdict. Which reading is decisive is a human's call, "
             "so this is reported rather than resolved -- add a seal to the "
             "later row once it has been approved.")
        for earlier, later in unsealed:
            line(f"  {later.paper}: entry {earlier.number} ({earlier.date}) "
                 f"ruled {earlier.verdict!r}, entry {later.number} "
                 f"({later.date}) ruled {later.verdict!r}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    # Required, like `harness.prepare` and `harness.validate`. config.yaml
    # records why there is no default: the old one resolved against the CWD,
    # every documented invocation runs from this directory, and a stale
    # 382-paper copy sits beside it -- so a default could only fire when someone
    # forgot the flag, and when it fired it silently graded a smaller corpus.
    parser.add_argument("--corpus", required=True,
                        help="directory of extracted papers, e.g. ../../../corpus")
    parser.add_argument("--quiet", action="store_true",
                        help="summary and failures only, no per-entry lines")
    args = parser.parse_args(argv)

    try:
        pack = load_pack()
    except PackError as exc:
        print(f"pack: {exc}", file=sys.stderr)
        return 2

    spec = pack.ground_truth
    if not spec:
        print("the pack declares no `ground_truth:` block, so there is nothing "
              "to check against.", file=sys.stderr)
        return 2

    corpus = Path(args.corpus)
    if not corpus.is_dir():
        print(f"{corpus}: not a directory", file=sys.stderr)
        return 2

    ledger_path = pack.root / spec["path"]
    try:
        entries = parse_ledger(ledger_path.read_text(),
                               list(spec["verdicts"]), list(spec["kinds"]))
        unsealed = unsealed_changes(entries)
        clashes = conflicting(entries)
        result = check(entries, corpus, spec["verdict_field"],
                       spec["result_file"])
    except LedgerError as exc:
        print(f"{ledger_path}: {exc}", file=sys.stderr)
        return 2

    if not result["graded"]:
        print("no binding rows in the ledger, so this check graded nothing. "
              "That is a failure rather than a pass.", file=sys.stderr)
        return 2

    _report(result, unsealed, clashes, args.quiet)
    return 1 if (result["disagree"] or result["absent"] or unsealed
                 or clashes) else 0


if __name__ == "__main__":
    sys.exit(main())
