#!/usr/bin/env python3
"""Emit the stage-2 args for papers that still have no result — the resume path.

    python -m pe.pending                 # human-readable status
    python -m pe.pending --json          # args array, paste into Workflow(args=...)
    python -m pe.pending --json --out work/wf_args_retry.json

Why this exists: a stage-2 run can die part-way through for reasons unrelated to
the papers -- a session/rate limit is the one actually hit on the 40-paper v0.0.5
run, which killed 23 of 40 agents after 17 had finished. Because each subagent
writes its own `work/raw/<doi>.json`, the completed papers are durable and a
rerun only needs the missing ones. That makes the resume unit "papers with no
parseable raw file", which is what this computes.

This also implements prompt.md v0.0.5 batch spec step 7 (idempotency): a paper is
considered done only if its raw file parses AND its manifest source checksums
still match, so editing the corpus or the assembly invalidates the result rather
than silently keeping a stale one.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pe.paper_text import entry_paths  # noqa: E402
from pe.validate import parse_raw  # noqa: E402

from pe.runroot import work_default  # noqa: E402

# The fields pe/extract_workflow.js reads off each manifest entry.
ARG_FIELDS = ("doi", "prompt_file", "prompt_lines", "prompt_chars", "chars",
              "raw_file", "source_ids")

# A result must carry these to be a schema-0.0.6 record at all; anything less is
# a partial write and gets re-run rather than validated. `suppressed_candidates`
# is on the list deliberately: v0.0.9 told the model to note its exclusions in
# `ambiguities` and nothing checked that it had, so the exclusions went
# unrecorded. Requiring the field here is what makes it enforceable -- a result
# that omits it is re-run, not silently accepted.
REQUIRED = ("schema_version", "sources_seen", "processing_status",
            "text_completeness", "has_single_cell_assay", "perturbation_present",
            "perturbation_present_any_assay", "unresolved_reason",
            "consistency_flags", "perturbations", "suppressed_candidates")


def status_of(entry: dict, work: Path | None = None) -> tuple[str, str]:
    """Return (state, detail) for one manifest entry.

    `work` is optional only for backward compatibility with callers that predate
    the portable-path fix; pass it whenever available.
    """
    if work is not None:
        _, raw = entry_paths(entry, work)
    else:
        raw = Path(entry["raw_file"])
    if not raw.exists():
        return "missing", "no raw file"
    try:
        result = parse_raw(raw.read_text())
    except Exception as exc:  # noqa: BLE001 - any parse problem means re-run
        return "unparseable", str(exc)[:80]
    absent = [k for k in REQUIRED if k not in result]
    if absent:
        return "incomplete", "missing " + ",".join(absent)
    seen = {str(s) for s in (result.get("sources_seen") or [])}
    expected = {str(s) for s in (entry.get("source_ids") or [])}
    if seen != expected:
        return "source_mismatch", f"saw {sorted(seen)}, expected {sorted(expected)}"
    return "done", ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", default=str(work_default()))
    parser.add_argument("--json", action="store_true",
                        help="print the args array for Workflow instead of a report")
    parser.add_argument("--out", default=None, help="also write the args array here")
    parser.add_argument("--force", action="store_true",
                        help="treat every paper as pending (full re-run)")
    args = parser.parse_args()

    work = Path(args.work)
    manifest = json.loads((work / "manifest.json").read_text())

    pending, done, report = [], [], []
    for entry in manifest:
        doi = entry["doi"]
        if "error" in entry:
            report.append(("no-input", doi, entry["error"]))
            continue
        state, detail = ("missing", "forced") if args.force else status_of(entry, work)
        if state == "done":
            done.append(doi)
        else:
            pending.append({k: entry[k] for k in ARG_FIELDS if k in entry})
            report.append((state, doi, detail))

    payload = json.dumps(pending, separators=(",", ":"))
    if args.out:
        Path(args.out).write_text(payload)

    if args.json:
        print(payload)
        return 0

    print(f"done {len(done)}/{len(manifest)}   pending {len(pending)}")
    if report:
        print()
        for state, doi, detail in sorted(report):
            print(f"  {state:16} {doi:38} {detail}")
    if pending:
        chars = sum(p.get("chars", 0) for p in pending)
        print(f"\n{len(pending)} paper(s) to re-run, {chars:,} chars "
              f"(~{chars // 4:,} input tokens)")
        print("Resume with:")
        print("  python -m pe.pending --json --out work/wf_args_retry.json")
        print("  then Workflow(scriptPath='pe/extract_workflow.js', "
              "args=<contents of that file>)")
    else:
        print("\nnothing pending — run: python -m pe.validate --write-corpus")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
