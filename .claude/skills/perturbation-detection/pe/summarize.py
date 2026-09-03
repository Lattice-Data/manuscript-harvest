#!/usr/bin/env python3
"""Stage 4: the triage table and the corpus counters.

    python -m pe.summarize [--work work/] [--out output/perturbations_summary.csv]

prompt.md v0.0.10 batch spec steps 10 and 11 (priorities renumbered there and
here together; see `triage_priority`). Step 10 fixes both the column set
and the row ORDER -- the table is a work queue, not a dump, so rows are sorted by
the triage priority the prompt defines. Step 11's counters are described there as
"the acceptance criteria for the version, not decoration", so they are printed
every run rather than being available on request.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pe.runroot import output_default, output_name, work_default  # noqa: E402
from pe.runstate import RunError, load_validated  # noqa: E402

# The columns, the ladder, the row builder and the 21 counters are the pack's:
# `task/report.yaml` for the lists and `task/report.py` for the functions that
# read a record. This module reads a run, writes a CSV, sorts by tier and
# refuses to report on an empty set -- and no longer knows what a column means.
from task.report import COLUMNS, counters, row_for  # noqa: E402

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", default=str(work_default()))
    parser.add_argument("--out", default=str(output_default(output_name("summary_csv"))))
    args = parser.parse_args()

    run = load_validated(Path(args.work))
    # A CSV of 392 blank rows is not a summary of anything, and it used to be
    # written with exit 0. Every other tool now refuses the same way.
    run.require_papers("pe.summarize")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    unreadable = dict(run.unreadable)
    rows, results = [], {}
    for entry in run.manifest:
        doi = entry["doi"]
        blank = {c: "" for c in COLUMNS}
        if "error" in entry:
            rows.append({**blank, "triage_priority": 0, "doi": doi, "paper_id": doi,
                         "status": entry["error"], "needs_review": True})
            continue
        if doi in unreadable:
            # Previously an uncaught JSONDecodeError here produced no CSV at all,
            # so one corrupt record cost the whole table. The row says what
            # happened instead.
            rows.append({**blank, "triage_priority": 0, "doi": doi, "paper_id": doi,
                         "status": f"unreadable: {unreadable[doi]}",
                         "chars": entry.get("chars", ""), "needs_review": True})
            continue
        result = run.records.get(doi)
        if result is None:
            rows.append({**blank, "triage_priority": 0, "doi": doi, "paper_id": doi,
                         "status": "pending", "chars": entry.get("chars", ""),
                         "needs_review": True})
            continue
        results[doi] = result
        rows.append(row_for(doi, result, entry))

    rows.sort(key=lambda r: (r["triage_priority"], str(r["doi"])))

    with out.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    ok = [r for r in rows if r["status"] == "ok"]
    print(run.coverage())
    print(f"wrote {out}  ({len(rows)} rows, {len(ok)} complete)")
    print("\n".join(counters(rows, results)))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RunError as exc:
        print(f"pe.summarize: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
