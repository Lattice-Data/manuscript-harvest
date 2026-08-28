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
import json
from collections import Counter
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pe.runroot import work_default, output_default  # noqa: E402

# prompt.md v0.0.5 step 10's column list, plus the pairing/quote counts the
# curator needs to act on a row without opening the JSON.
COLUMNS = [
    "triage_priority", "paper_id", "doi", "status",
    "perturbation_present",             # primary curation field (assay-paired)
    "perturbation_present_model",       # what the model returned, pre-pruning
    "perturbation_present_any_assay",   # pre-v0.0.3 semantics, kept for QA
    "unresolved_reason",
    "processing_status", "text_completeness",
    "has_single_cell_assay", "single_cell_assay_types",
    "paper_confidence",
    "n_perturbations", "n_paired_yes", "n_paired_no", "n_paired_unclear",
    "assay_filtered", "stage_b_capped", "determination_changed_by_harness",
    "consistency_flags", "evidence_flags",
    "categories", "n_samples", "n_samples_perturbed", "n_samples_unclear",
    "n_samples_sc_assay",
    "n_suppressed", "suppressed_rules", "suppressed_would_pair_yes",
    "quotes_checked", "quotes_failed", "quotes_wrong_source",
    "perturbations_dropped", "max_pert_confidence",
    "sources", "perturbation_agents", "n_issues", "chars", "needs_review",
]


def _join_truncated(items, per_item: int = 60, total: int = 400) -> str:
    """Join with '|', shortening long entries -- but mark every cut with '…'.

    A silent [:60] is indistinguishable from broken data: "forward genetic
    CRISPR " with the closing paren missing looks identical whether it was
    truncated on purpose or the pipeline lost the rest. Marking it makes clear
    this is intentional shortening for spreadsheet readability, and that the
    untruncated value is in work/validated/<doi>.json, not lost.
    """
    parts = []
    for item in items:
        text = str(item or "")
        if len(text) > per_item:
            text = text[: per_item - 1] + "…"
        parts.append(text)
    joined = "|".join(parts)
    if len(joined) > total:
        joined = joined[: total - 1] + "…"
    return joined


def triage_priority(result: dict) -> int:
    """prompt.md v0.0.10 step 10's sort order. Lower sorts first.

    1 is the bucket most likely to hide a real match; 5 is the one the prompt
    says to sample rather than read in full. 9 is everything else, and 0 is
    reserved by `main` for rows that failed or are still pending.

    **The ladder renumbered at v0.0.10**: priority 2 is new, and the old 2-5
    shifted to 3-6. prompt.md step 10 carries the same list, and the two must be
    changed together -- a mismatch would silently mis-sort the curator's queue.
    """
    validation = result.get("validation") or {}
    present = result.get("perturbation_present")
    reason = result.get("unresolved_reason")
    confidence = result.get("paper_confidence")

    if present == "unclear" and reason == "pairing_not_stated":
        return 1
    # v0.0.10. One toggle flips these: the candidate is named, its pairing is
    # already judged "yes", and the only thing between the paper and a "yes" is a
    # NOT-list rule. Ranked below priority 1 because that bucket is an open
    # question a reader must resolve, while this is a settled call to ratify --
    # narrower, but far more actionable.
    if present != "yes" and validation.get("suppressed_would_pair_yes"):
        return 2
    if present == "yes" and isinstance(confidence, (int, float)) and confidence < 0.6:
        return 3
    if present == "unclear" and reason == "degraded_text":
        return 4
    if present == "no" and result.get("perturbation_present_any_assay") == "yes":
        return 5
    if validation.get("consistency_flags") or validation.get("evidence_flags"):
        return 6
    return 9


def row_for(doi: str, result: dict, entry: dict) -> dict:
    perts = result.get("perturbations") or []
    samples = result.get("samples") or []
    validation = result.get("validation") or {}
    confidences = [p.get("confidence") for p in perts
                   if isinstance(p.get("confidence"), (int, float))]

    assay_types = result.get("single_cell_assay_types") or []
    if isinstance(assay_types, str):
        assay_types = [assay_types]

    return {
        "triage_priority": triage_priority(result),
        "paper_id": result.get("paper_id", doi),
        "doi": doi,
        "status": "ok",
        "perturbation_present": result.get("perturbation_present", ""),
        "perturbation_present_model": result.get("perturbation_present_model", ""),
        "perturbation_present_any_assay": result.get("perturbation_present_any_assay", ""),
        "unresolved_reason": result.get("unresolved_reason", ""),
        "processing_status": result.get("processing_status", ""),
        "text_completeness": result.get("text_completeness", ""),
        "has_single_cell_assay": result.get("has_single_cell_assay", ""),
        "single_cell_assay_types": _join_truncated(assay_types, per_item=60, total=200),
        "paper_confidence": result.get("paper_confidence", ""),
        "n_perturbations": len(perts),
        "n_paired_yes": validation.get("paired_yes", ""),
        "n_paired_no": validation.get("paired_no", ""),
        "n_paired_unclear": validation.get("paired_unclear", ""),
        "assay_filtered": validation.get("assay_filtered", ""),
        "stage_b_capped": validation.get("stage_b_capped", ""),
        "determination_changed_by_harness": validation.get(
            "determination_changed_by_harness", ""),
        "consistency_flags": "|".join(validation.get("consistency_flags") or []),
        "evidence_flags": "|".join(validation.get("evidence_flags") or []),
        "categories": "|".join(sorted({str(p.get("category", "")) for p in perts
                                       if p.get("category")})),
        "n_samples": len(samples),
        # The v0.0.5 curator ruling keeps "unclear" distinct from false; only
        # `is True` counts as perturbed, and the unclear count is reported
        # rather than folded into either bucket.
        "n_samples_perturbed": sum(1 for s in samples if s.get("perturbed") is True),
        "n_samples_unclear": sum(1 for s in samples if s.get("perturbed") == "unclear"),
        "n_samples_sc_assay": sum(1 for s in samples
                                  if s.get("is_single_cell_assay") == "yes"),
        "n_suppressed": validation.get("n_suppressed", ""),
        "suppressed_rules": "|".join(validation.get("suppressed_rules") or []),
        "suppressed_would_pair_yes": validation.get("suppressed_would_pair_yes", ""),
        "quotes_checked": validation.get("quotes_checked", ""),
        "quotes_failed": validation.get("quotes_failed", ""),
        "quotes_wrong_source": validation.get("quotes_wrong_source", ""),
        "perturbations_dropped": validation.get("perturbations_dropped", ""),
        "max_pert_confidence": max(confidences) if confidences else "",
        "sources": "|".join(entry.get("source_ids") or []),
        # Full sentences, not truncated: a curator reads this column directly
        # to judge each perturbation, and a cut mid-sentence ("forward genetic
        # CRISPR…") cannot be judged. The untruncated value already lived in
        # work/validated/<doi>.json; this just stops shortening it for display.
        "perturbation_agents": "|".join(str(p.get("agent", "")) for p in perts),
        "n_issues": len(validation.get("issues") or []),
        "chars": entry.get("chars", ""),
        "needs_review": result.get("needs_review", True),
    }


def _counters(rows: list[dict], results: dict[str, dict]) -> list[str]:
    """prompt.md v0.0.5 step 11."""
    ok = [r for r in rows if r["status"] == "ok"]
    out: list[str] = []

    def tally(label: str, key) -> None:
        counts = Counter(str(key(r)) for r in ok)
        out.append(f"  {label:<52} "
                   + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))

    out.append("")
    out.append("corpus counters (prompt.md v0.0.5 step 11 — acceptance criteria)")
    tally("papers by processing_status", lambda r: r["processing_status"])
    tally("papers by text_completeness", lambda r: r["text_completeness"])
    tally("papers by perturbation_present", lambda r: r["perturbation_present"])
    tally("papers by perturbation_present_any_assay",
          lambda r: r["perturbation_present_any_assay"])

    unclear = [r for r in ok if r["perturbation_present"] == "unclear"]
    reasons = Counter(str(r["unresolved_reason"]) for r in unclear)
    out.append(f"  {'unclear split by unresolved_reason':<52} "
               + (", ".join(f"{k}={v}" for k, v in sorted(reasons.items()))
                  or "(no unclear papers)"))

    capped = [r for r in ok if r["stage_b_capped"] is True]
    out.append(f"  {'moved no -> unclear by Stage B (v0.0.5 cost)':<52} {len(capped)}"
               + (f" -> {', '.join(r['doi'] for r in capped)}" if capped else ""))

    cc = Counter(c for r in ok for c in (r["consistency_flags"] or "").split("|") if c)
    out.append(f"  {'papers hitting each CC code':<52} "
               + (", ".join(f"{k}={v}" for k, v in sorted(cc.items())) or "none"))

    ev = Counter(c for r in ok for c in (r["evidence_flags"] or "").split("|") if c)
    out.append(f"  {'papers hitting each EV flag':<52} "
               + (", ".join(f"{k}={v}" for k, v in sorted(ev.items())) or "none"))

    changed = [r for r in ok if r["determination_changed_by_harness"] is True]
    out.append(f"  {'model != final after pruning (fabrication rate)':<52} "
               f"{len(changed)}/{len(ok)}"
               + (f" -> {', '.join(r['doi'] for r in changed)}" if changed else ""))

    filtered = [r for r in ok if r["assay_filtered"] is True]
    out.append(f"  {'any_assay=yes but perturbation_present=no/unclear':<52} "
               f"{len(filtered)}"
               + (f" -> {', '.join(r['doi'] for r in filtered)}" if filtered else ""))

    mixed = [d for d, res in results.items()
             if (res.get("validation") or {}).get("mixed_no_unclear")]
    out.append(f"  {'mixed no/unclear pairing papers (v0.0.4 gap)':<52} {len(mixed)}"
               + (f" -> {', '.join(mixed)}" if mixed else ""))

    # v0.0.10: what each NOT-list rule cost, corpus-wide. This is the counter
    # that makes a boundary change arguable from data rather than from
    # hand-reading -- the measurement v0.0.9 was justified by and did not have.
    by_rule = Counter(rule for r in ok
                      for rule in (r["suppressed_rules"] or "").split("|") if rule)
    n_supp_papers = sum(1 for r in ok if int(r["n_suppressed"] or 0) > 0)
    n_supp_total = sum(int(r["n_suppressed"] or 0) for r in ok)
    out.append(f"  {'papers by suppression rule':<52} "
               + (", ".join(f"{k}={v}" for k, v in sorted(by_rule.items())) or "none"))
    out.append(f"  {'papers with any suppressed candidate':<52} "
               f"{n_supp_papers}/{len(ok)} ({n_supp_total} candidate(s) total)")
    would = [r for r in ok if r["suppressed_would_pair_yes"] is True]
    held = [r for r in would if r["perturbation_present"] != "yes"]
    out.append(f"  {'suppressed candidate would have paired yes':<52} {len(would)}"
               + (f" -> {', '.join(r['doi'] for r in would)}" if would else ""))
    out.append(f"  {'  ...of which the paper is NOT yes (rule held it back)':<52} "
               f"{len(held)}"
               + (f" -> {', '.join(r['doi'] for r in held)}" if held else ""))

    q_checked = sum(int(r["quotes_checked"] or 0) for r in ok)
    q_failed = sum(int(r["quotes_failed"] or 0) for r in ok)
    q_wrong = sum(int(r["quotes_wrong_source"] or 0) for r in ok)
    out.append(f"  {'quotes verified':<52} "
               f"{q_checked - q_failed}/{q_checked} "
               f"(failed {q_failed}, misattributed {q_wrong})")
    dropped = sum(int(r["perturbations_dropped"] or 0) for r in ok)
    out.append(f"  {'perturbations dropped for unverifiable evidence':<52} {dropped}")

    out.append("")
    out.append("triage queue (step 10 priority)")
    for priority, label in (
        (1, "unclear + pairing_not_stated  (may hide a real match — read first)"),
        (2, "not yes + a suppressed candidate would have paired yes  (v0.0.10)"),
        (3, "yes with paper_confidence < 0.6"),
        (4, "unclear + degraded_text       (route to re-fetch, not to reading)"),
        (5, "no but any_assay=yes          (pairing filter fired — sample it)"),
        (6, "consistency or evidence flags"),
        (9, "everything else"),
    ):
        bucket = [r for r in ok if r["triage_priority"] == priority]
        out.append(f"  P{priority}  {label:<62} {len(bucket)}")
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", default=str(work_default()))
    parser.add_argument("--out", default=str(output_default("perturbations_summary.csv")))
    args = parser.parse_args()

    work = Path(args.work)
    manifest = json.loads((work / "manifest.json").read_text())
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    rows, results = [], {}
    for entry in manifest:
        doi = entry["doi"]
        blank = {c: "" for c in COLUMNS}
        if "error" in entry:
            rows.append({**blank, "triage_priority": 0, "doi": doi, "paper_id": doi,
                         "status": entry["error"], "needs_review": True})
            continue
        validated = work / "validated" / f"{doi}.json"
        if not validated.exists():
            rows.append({**blank, "triage_priority": 0, "doi": doi, "paper_id": doi,
                         "status": "pending", "chars": entry.get("chars", ""),
                         "needs_review": True})
            continue
        result = json.loads(validated.read_text())
        results[doi] = result
        rows.append(row_for(doi, result, entry))

    rows.sort(key=lambda r: (r["triage_priority"], str(r["doi"])))

    with out.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    ok = [r for r in rows if r["status"] == "ok"]
    print(f"wrote {out}  ({len(rows)} rows, {len(ok)} complete)")
    if ok:
        print("\n".join(_counters(rows, results)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
