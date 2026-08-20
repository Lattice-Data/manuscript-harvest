#!/usr/bin/env python3
"""Stage 4: the triage table and the corpus counters.

    python -m pe.summarize [--work work/] [--out output/perturbations_summary.csv]

prompt.md v0.0.5 batch spec steps 10 and 11. Step 10 fixes both the column set
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
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

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
    "quotes_checked", "quotes_failed", "quotes_wrong_source",
    "perturbations_dropped", "max_pert_confidence",
    "sources", "agents", "n_issues", "chars", "needs_review",
]


def triage_priority(result: dict) -> int:
    """prompt.md v0.0.5 step 10's sort order. Lower sorts first.

    1 is the bucket most likely to hide a real match; 4 is the one the prompt
    says to sample rather than read in full. 9 is everything else.
    """
    validation = result.get("validation") or {}
    present = result.get("perturbation_present")
    reason = result.get("unresolved_reason")
    confidence = result.get("paper_confidence")

    if present == "unclear" and reason == "pairing_not_stated":
        return 1
    if present == "yes" and isinstance(confidence, (int, float)) and confidence < 0.6:
        return 2
    if present == "unclear" and reason == "degraded_text":
        return 3
    if present == "no" and result.get("perturbation_present_any_assay") == "yes":
        return 4
    if validation.get("consistency_flags") or validation.get("evidence_flags"):
        return 5
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
        "single_cell_assay_types": "|".join(str(a) for a in assay_types)[:200],
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
        "quotes_checked": validation.get("quotes_checked", ""),
        "quotes_failed": validation.get("quotes_failed", ""),
        "quotes_wrong_source": validation.get("quotes_wrong_source", ""),
        "perturbations_dropped": validation.get("perturbations_dropped", ""),
        "max_pert_confidence": max(confidences) if confidences else "",
        "sources": "|".join(entry.get("source_ids") or []),
        # Truncated so the CSV stays readable in a spreadsheet; full detail
        # lives in work/validated/<doi>.json.
        "agents": "|".join(str(p.get("agent", ""))[:60] for p in perts)[:400],
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
        (2, "yes with paper_confidence < 0.6"),
        (3, "unclear + degraded_text       (route to re-fetch, not to reading)"),
        (4, "no but any_assay=yes          (pairing filter fired — sample it)"),
        (5, "consistency or evidence flags"),
        (9, "everything else"),
    ):
        bucket = [r for r in ok if r["triage_priority"] == priority]
        out.append(f"  P{priority}  {label:<62} {len(bucket)}")
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", default=str(ROOT / "work"))
    parser.add_argument("--out", default=str(ROOT / "output" / "perturbations_summary.csv"))
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
