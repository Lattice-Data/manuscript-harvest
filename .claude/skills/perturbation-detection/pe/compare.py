#!/usr/bin/env python3
"""Version-to-version comparison for prompt.md's validation loop step 1.

    python -m pe.compare --baseline <dir> [--out output/<a>_vs_<b>.txt]

The prompt is explicit that this diff is NOT expected to be empty, and that it
should be *classified* rather than merely counted:

    "Expect movement in exactly two classes: papers capped by Stage B, and
     papers with has_single_cell_assay = 'unclear' plus a 'yes' pairing (CC-5).
     Any change outside those two classes is a bug in this version, not a
     refinement, and should be investigated before the corpus run."

This run adds a third legitimate class the prompt's changelog does not mention,
because the v0.0.4 baseline was main-text-only while v0.0.5 supplies deduplicated
supplementary sources (prompt.md's default): a paper can move because the model
finally saw the Methods. That class is labelled SUPP-EVIDENCE and is evidenced by
a verified quote carrying a supplementary source_id. Anything left over is
labelled UNEXPLAINED and is what the prompt says to investigate.

Two later additions, both of which were previously landing in UNEXPLAINED and so
reading as logic bugs:

  STAGE-B-RELEASED — Stage B's cap is symmetric, but only its ENTRY was
    classified. A paper leaving the cap (`stage_b_capped` True -> False, which is
    what a completed re-extraction looks like) had no class. Observed on
    10.1126/science.adf5357.
  SUPPRESSED — v0.0.10 lets a paper move because a candidate was recorded in
    `suppressed_candidates` instead of `perturbations`. The class only fires when
    a baseline perturbation can actually be matched to a new suppressed
    candidate; an unmatched suppression explains nothing.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pe.runroot import work_default, output_default  # noqa: E402

ORDER = ["yes", "unclear", "no"]

CLASS_LABELS = {
    "STAGE-B": "Stage B's degraded-text cap was ENTERED in the new run — predicted class (a)",
    "STAGE-B-RELEASED": "Stage B's cap was RELEASED (the baseline was capped, this run is not) — e.g. re-extraction completed the text",
    "SUPPRESSED": "a candidate the baseline reported as a perturbation is now recorded in suppressed_candidates (v0.0.10)",
    "CC-5": "has_single_cell_assay='unclear' with a 'yes' pairing — predicted class (b)",
    "SUPP-EVIDENCE": "new supplementary evidence the v0.0.4 main-text-only run never saw",
    "HARNESS-PRUNE": "quote pruning changed the determination (EV-* flags)",
    "BASELINE-UNASSESSED": "baseline never assessed this paper's assay (pairing-only route skipped it)",
    "PERT-SET-CHANGED": "full re-extraction produced a different perturbation set than the baseline's reused v0.0.2 list",
    "ASSAY-CHANGED": "has_single_cell_assay differs between the two runs",
    "ANY-ASSAY-CHANGED": "perturbation_present_any_assay differs between the two runs",
    "UNEXPLAINED": "*** outside every expected class — investigate before the corpus run",
}


def determination_inputs(result: dict) -> dict:
    """The complete input set the determination is a function of.

    Stage A and Stage B read nothing else, so if two runs agree on all five of
    these and still disagree on `perturbation_present`, the logic itself is
    wrong. If they differ on any of them, the change is fully explained by the
    input that moved -- which is what makes this diff auditable rather than a
    matter of opinion.
    """
    paired = sorted(str(p.get("single_cell_paired"))
                    for p in (result.get("perturbations") or [])
                    if isinstance(p, dict))
    return {
        "processing_status": result.get("processing_status"),
        "text_completeness": result.get("text_completeness"),
        "has_single_cell_assay": result.get("has_single_cell_assay"),
        "any_assay": result.get("perturbation_present_any_assay"),
        "paired": paired,
    }


def classify(new: dict, old: dict | None = None) -> list[str]:
    """Which v0.0.5 mechanism(s) can account for this paper moving."""
    validation = new.get("validation") or {}
    classes = []

    # The v0.0.4 baseline was produced by the pairing-only route, which SKIPPED
    # papers whose v0.0.2 result found no perturbations: nothing could pair, so
    # `has_single_cell_assay` was set to "unclear" meaning "never assessed"
    # rather than measured. v0.0.5 re-extracts those from scratch, so movement
    # there is a baseline gap being filled, not a regression in this version.
    if old is not None and old.get("pairing_pass") == "skipped_no_perturbations":
        classes.append("BASELINE-UNASSESSED")

    # Stage B is symmetric: a paper moves both when it ENTERS the degraded-text
    # cap and when it LEAVES it. Only the first was checked before, so a cap
    # release -- `stage_b_capped` True -> False, which is what re-extraction
    # completing a paper's text looks like -- fell through to UNEXPLAINED and
    # read as a logic bug. Observed on 10.1126/science.adf5357.
    old_capped = bool((old or {}).get("validation", {}).get("stage_b_capped")) if old else False
    new_capped = bool(validation.get("stage_b_capped"))
    if new_capped:
        classes.append("STAGE-B")
    elif old_capped:
        classes.append("STAGE-B-RELEASED")
    if "CC-5" in (validation.get("consistency_flags") or []):
        classes.append("CC-5")

    # v0.0.10: a paper can now move because a candidate was SUPPRESSED rather
    # than reported. The precise account is a perturbation present in the
    # baseline that is absent from this run and reappears as a suppressed
    # candidate, so the match is required rather than assumed -- an unmatched
    # suppression explains nothing and must not silence UNEXPLAINED.
    if old is not None:
        matched = _suppression_matches(new, old)
        if matched:
            classes.append("SUPPRESSED")

    # A verified quote attributed to a supplementary source is direct evidence
    # that the model used text the main-text-only baseline never had.
    supp_quotes = 0
    for pert in new.get("perturbations") or []:
        for quote in pert.get("evidence_quotes") or []:
            if str(quote.get("source_id", "")).startswith("supp"):
                supp_quotes += 1
        assay_ev = pert.get("assay_evidence")
        if isinstance(assay_ev, dict) and str(assay_ev.get("source_id", "")).startswith("supp"):
            supp_quotes += 1
    if supp_quotes:
        classes.append("SUPP-EVIDENCE")

    if validation.get("determination_changed_by_harness"):
        classes.append("HARNESS-PRUNE")

    # The exact account: which determination input actually moved. The v0.0.4
    # baseline was built by the pairing-only route, which REUSED the v0.0.2
    # perturbation list and re-asked only the pairing. v0.0.5 is a full
    # re-extraction, so the perturbation set itself can legitimately differ --
    # most often because v0.0.5 requires a verbatim quote for every perturbation
    # and drops any that cannot be located.
    if old is not None:
        before, after = determination_inputs(old), determination_inputs(new)
        if before["paired"] != after["paired"]:
            classes.append("PERT-SET-CHANGED")
        if before["has_single_cell_assay"] != after["has_single_cell_assay"]:
            classes.append("ASSAY-CHANGED")
        if before["any_assay"] != after["any_assay"]:
            classes.append("ANY-ASSAY-CHANGED")

    return classes or ["UNEXPLAINED"]


def _tokens(value) -> set[str]:
    """Content words of an agent/candidate string, for matching across runs.

    Deliberately crude: the two runs describe the same construct in their own
    words ("SFTPC-GFP reporter line" vs "lentiviral SFTPC-promoter-GFP +
    EF1a-TagRFP reporter"), so an exact match would never fire. Short and
    generic words are dropped so "the medium" does not match "the inhibitor".
    """
    stop = {"the", "and", "with", "for", "from", "into", "was", "were", "that",
            "this", "only", "line", "lines", "cell", "cells", "human", "mouse",
            "sample", "samples", "using", "used", "via", "vs", "versus"}
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9\-']{2,}", str(value or "").lower())
    return {w for w in words if w not in stop}


def _suppression_matches(new: dict, old: dict) -> list[tuple[str, str, str]]:
    """Baseline perturbations that this run records as suppressed candidates.

    Returns [(rule, baseline agent, new candidate)]. Requiring the match is what
    keeps SUPPRESSED an explanation rather than an excuse: a run that suppressed
    something unrelated does not account for a paper moving.
    """
    supp = [s for s in (new.get("suppressed_candidates") or []) if isinstance(s, dict)]
    if not supp:
        return []
    new_agents = [_tokens(p.get("agent")) for p in (new.get("perturbations") or [])
                  if isinstance(p, dict)]
    matches = []
    for pert in old.get("perturbations") or []:
        if not isinstance(pert, dict):
            continue
        old_toks = _tokens(pert.get("agent"))
        if not old_toks:
            continue
        # Still reported in this run? Then it was not suppressed.
        if any(len(old_toks & cur) >= 2 or old_toks == cur for cur in new_agents):
            continue
        for cand in supp:
            cand_toks = _tokens(cand.get("candidate"))
            if len(old_toks & cand_toks) >= 2 or (
                    old_toks & cand_toks and len(old_toks) <= 2):
                matches.append((str(cand.get("rule")), str(pert.get("agent")),
                                str(cand.get("candidate"))))
                break
    return matches


def _quote_line(entry) -> str:
    if isinstance(entry, dict):
        src = entry.get("source_id", "?")
        return f"[{src}] {str(entry.get('quote', ''))[:150]}"
    return str(entry)[:150]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", default=str(work_default()))
    parser.add_argument("--baseline", required=True,
                        help="directory with validated/<doi>.json from the earlier run")
    parser.add_argument("--out", default=str(output_default("v004_vs_v005.txt")))
    args = parser.parse_args()

    work, baseline = Path(args.work), Path(args.baseline)
    manifest = json.loads((work / "manifest.json").read_text())

    rows = []
    for entry in manifest:
        doi = entry["doi"]
        new_file = work / "validated" / f"{doi}.json"
        # The baseline's validated/ is the scored v0.0.4 object.
        old_file = baseline / "validated" / f"{doi}.json"
        if not old_file.exists():
            old_file = baseline / "raw" / f"{doi}.json"
        if not (new_file.exists() and old_file.exists()):
            continue
        rows.append((doi, json.loads(old_file.read_text()),
                     json.loads(new_file.read_text()), entry))

    matrix = Counter((old.get("perturbation_present"), new.get("perturbation_present"))
                     for _, old, new, _ in rows)

    lines: list[str] = []
    versions = {str((new.get("validation") or {}).get("prompt_version")) for _, _, new, _ in rows}
    base_versions = {str((old.get("validation") or {}).get("prompt_version"))
                     for _, old, _, _ in rows} or {"?"}
    lines.append(f"baseline v{'/'.join(sorted(base_versions))} -> "
                 f"v{'/'.join(sorted(versions))} comparison over {len(rows)} paper(s)")
    lines.append("")
    lines.append("Both columns are the assay-paired `perturbation_present`, so this is a")
    lines.append("like-for-like diff of the primary curation field. Check whether the two")
    lines.append("runs saw the same input scope: a baseline built before supplementary")
    lines.append("sources were included is not comparable on evidence alone, and the")
    lines.append("SUPP-EVIDENCE class below flags papers where that mattered.")
    lines.append("")
    b_lbl = "/".join(sorted(base_versions))
    n_lbl = "/".join(sorted(versions))
    header = f"  {b_lbl} \\ {n_lbl}   " + "".join(f"{c:>10}" for c in ORDER) + "     total"
    lines.append(header)
    lines.append("  " + "-" * (len(header) - 2))
    for old_call in ORDER:
        cells = [matrix.get((old_call, new_call), 0) for new_call in ORDER]
        lines.append(f"  {old_call:>16}   " + "".join(f"{c:>10}" for c in cells)
                     + f"     {sum(cells):>5}")
    lines.append("  " + "-" * (len(header) - 2))
    totals = [sum(matrix.get((o, n), 0) for o in ORDER) for n in ORDER]
    lines.append("             total   " + "".join(f"{c:>10}" for c in totals)
                 + f"     {sum(totals):>5}")

    changed = [(doi, old, new, entry) for doi, old, new, entry in rows
               if old.get("perturbation_present") != new.get("perturbation_present")]
    lines.append("")
    lines.append(f"unchanged: {len(rows) - len(changed)}/{len(rows)}    "
                 f"changed: {len(changed)}")

    class_counts = Counter(c for _, old, new, _ in changed for c in classify(new, old))
    lines.append("")
    lines.append("change classes (a paper can fall in more than one)")
    for code, label in CLASS_LABELS.items():
        if class_counts.get(code):
            lines.append(f"  {code:<15} {class_counts[code]:>3}   {label}")
    unexplained = [doi for doi, old, new, _ in changed if classify(new, old) == ["UNEXPLAINED"]]
    if unexplained:
        lines.append("")
        lines.append(f"  !! {len(unexplained)} UNEXPLAINED change(s): {', '.join(unexplained)}")
        lines.append("     prompt.md validation loop step 1 says to investigate these "
                     "before the corpus run.")
    else:
        lines.append("")
        lines.append("  every change is accounted for by a known v0.0.5 mechanism.")

    lines.append("")
    lines.append("=" * 78)
    lines.append("CHANGED PAPERS")
    lines.append("=" * 78)
    for doi, old, new, entry in changed:
        validation = new.get("validation") or {}
        types = new.get("single_cell_assay_types") or []
        if isinstance(types, str):
            types = [types]
        lines.append("")
        lines.append(f"{doi}:  {old.get('perturbation_present')}  ->  "
                     f"{new.get('perturbation_present')}"
                     f"   [{', '.join(classify(new, old))}]")
        lines.append(f"  sources={'|'.join(entry.get('source_ids') or [])}  "
                     f"processing={new.get('processing_status')}  "
                     f"completeness={new.get('text_completeness')}  "
                     f"reason={new.get('unresolved_reason')}")
        lines.append(f"  has_single_cell_assay={new.get('has_single_cell_assay')}  "
                     f"assays={', '.join(str(t) for t in types) or '(none)'}  "
                     f"any_assay={new.get('perturbation_present_any_assay')}")
        if validation.get("consistency_flags") or validation.get("evidence_flags"):
            lines.append(f"  flags: {','.join(validation.get('consistency_flags') or [])} "
                         f"{','.join(validation.get('evidence_flags') or [])}")
        lines.append(f"  v0.0.4 had {len(old.get('perturbations') or [])} perturbation(s); "
                     f"v0.0.5 has {len(new.get('perturbations') or [])}")
        for rule, old_agent, cand in _suppression_matches(new, old):
            lines.append(f"    SUPPRESSED ({rule}): baseline reported "
                         f"{old_agent[:60]!r}")
            lines.append(f"      now recorded as a suppressed candidate: {cand[:80]}")
        old_cap = bool((old.get("validation") or {}).get("stage_b_capped"))
        new_cap = bool(validation.get("stage_b_capped"))
        if old_cap != new_cap:
            lines.append(f"  Stage B cap: {old_cap} -> {new_cap}"
                         + ("  (released — the text is no longer degraded)"
                            if old_cap else "  (entered)"))
        before, after = determination_inputs(old), determination_inputs(new)
        moved = [k for k in before if before[k] != after[k]]
        if moved:
            lines.append("  determination inputs that moved:")
            for key in moved:
                lines.append(f"    {key}: {before[key]!r} -> {after[key]!r}")
        else:
            lines.append("  determination inputs are IDENTICAL — this is a logic "
                         "difference, not an input difference. Investigate.")
        for pert in old.get("perturbations") or []:
            agents_new = {str(p.get("agent"))[:40] for p in (new.get("perturbations") or [])}
            if str(pert.get("agent"))[:40] not in agents_new:
                lines.append(f"    DROPPED vs baseline: paired={pert.get('single_cell_paired')} "
                             f"{str(pert.get('agent'))[:70]}")
        for i, pert in enumerate(new.get("perturbations") or []):
            lines.append(f"    [{i}] paired={pert.get('single_cell_paired')} "
                         f"{str(pert.get('agent'))[:52]}")
            lines.append(f"        assay: {str(pert.get('assay_applied') or '(unstated)')[:100]}")
            assay_ev = pert.get("assay_evidence")
            if isinstance(assay_ev, dict) and assay_ev.get("quote"):
                check = pert.get("assay_quote_check") or {}
                mark = "  [OK]" if check.get("status") == "verified" else f"  [{check.get('status', '?')}]"
                lines.append(f"        pairing quote: {_quote_line(assay_ev)}{mark}")
            for quote in (pert.get("evidence_quotes") or [])[:2]:
                lines.append(f"        evidence: {_quote_line(quote)}")

    lines.append("")
    lines.append("=" * 78)
    lines.append("UNCHANGED PAPERS")
    lines.append("=" * 78)
    for doi, old, new, entry in rows:
        call = new.get("perturbation_present")
        if old.get("perturbation_present") != call:
            continue
        perts = new.get("perturbations") or []
        paired = [p for p in perts if p.get("single_cell_paired") == "yes"]
        supp = "supp" if len(entry.get("source_ids") or []) > 1 else "main"
        lines.append(f"  {doi:34} {call:<8} {len(paired)}/{len(perts)} paired   "
                     f"[{supp}]")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n")
    cut = lines.index("=" * 78) if "=" * 78 in lines else len(lines)
    print("\n".join(lines[:cut]))
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
