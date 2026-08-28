#!/usr/bin/env python3
"""Stage 5 (review aid): target the papers prompt.md says to check first.

    python -m pe.audit [--work work/] [--out output/review_screen.txt]

prompt.md's validation loop (added in v0.0.3) asks for three things. This produces all
three, mechanically:

  Screen A — assay-pairing disagreements. Papers where
    `perturbation_present_any_assay = yes` but `perturbation_present` is
    no/unclear: the assay-pairing requirement alone flipped the call. v0.0.3
    calls this "the highest-value QA pass since it's exactly the failure mode
    this version targets." Prints each perturbation's pairing, assay, and
    assay-pairing quote so the call can be checked without opening the paper.

  Screen B — possible missed assay. Papers with `has_single_cell_assay` no or
    unclear, grepped for qualifying single-cell/nucleus assay names. A hit means
    the assay taxonomy may have been applied too strictly.

  Screen C — possible missed perturbation. Papers with
    `perturbation_present_any_assay = no`, grepped for perturbation language.
    This is the pre-v0.0.3 false-negative screen, still useful.

  Screen F — suppressed candidates (v0.0.10). Every candidate the NOT list
    swallowed, with the `would_have_paired = "yes"` rows first: those are papers
    where one toggle flips the determination, so they are the review queue for
    the boundary rules themselves rather than for the model's reading of a paper.
    Unlike B and C this is not a keyword grep — the model told us it made these
    calls, which is exactly what v0.0.9 could not do.

All three are SCREENS, not verdicts. A keyword hit is not proof of an error:
"treated with" also describes routine processing, and "single-cell suspension"
is a dissociation step, not an assay — the two traps this task turns on.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pe.paper_text import prompt_version  # noqa: E402
from pe.validate import RULES_UNDER_REVIEW, paper_text_from_prompt  # noqa: E402

from pe.runroot import work_default, output_default  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

PERTURBATION_SIGNALS: dict[str, list[str]] = {
    "genetic": [
        r"\bknock(?:ed)?[ -]?outs?\b", r"\bknock(?:ed)?[ -]?downs?\b", r"\bknock[ -]?ins?\b",
        r"\bsh[Rr]NA\b", r"\bsi[Rr]NA\b", r"\bRNAi\b", r"\bCRISPRi?a?\b", r"\bsgRNA\b",
        r"\bCas9\b", r"\boverexpress", r"\btransgenic\b", r"\bfloxed\b", r"\bCre[ -]?(?:driver|recombinase|ER)",
        r"\bconditional (?:deletion|knockout)\b", r"\bnull (?:allele|mice)\b",
        r"\bbase edit", r"\bprime edit", r"\blentivir(?:us|al) (?:transduc|infect)",
    ],
    "chemical": [
        r"\btreated with\b", r"\btreatment with\b", r"\bvehicle[ -]?(?:treated|control)\b",
        r"\binhibitor\b", r"\bagonist\b", r"\bantagonist\b", r"\bdoxycycline\b", r"\btamoxifen\b",
        r"\b4[- ]?OHT\b", r"\bchemotherap", r"\bdosed? (?:with|at)\b",
    ],
    "biologic_stimulation": [
        r"\bstimulated with\b", r"\bstimulation with\b", r"\bLPS\b", r"\bPMA\b", r"\bionomycin\b",
        r"\banti[ -]?CD3\b", r"\banti[ -]?CD28\b", r"\bTCR (?:activation|stimulation)\b",
        r"\brecombinant (?:human|mouse|murine)\b", r"\bneutralizing antibod",
        r"\bblocking antibod", r"\bdepleting antibod", r"\bimmuniz", r"\bvaccinat",
    ],
    "physical_environmental": [
        r"\bhypoxi", r"\banoxi", r"\bheat[ -]?shock", r"\bcold[ -]?shock", r"\birradiat",
        r"\boxidative stress\b", r"\bstarvation\b", r"\bserum[ -]?starv",
    ],
    "dietary": [r"\bhigh[ -]?fat diet\b", r"\bfasting\b", r"\bfasted\b", r"\bcaloric restriction\b"],
    "model_system": [
        r"\borganoid", r"\breprogramm", r"\biPSC[ -]?derived\b", r"\bxenograft", r"\bPDX\b",
    ],
}

# Drawn from v0.0.3's Step 1 qualifying-assay taxonomy.
ASSAY_SIGNALS: dict[str, list[str]] = {
    "scRNA": [
        r"\bscRNA[- ]?seq\b", r"\bsingle[- ]cell RNA[- ]?(?:seq|sequencing)\b",
        r"\b10x Genomics\b", r"\bChromium\b", r"\bSmart[- ]seq ?[23]?\b", r"\bDrop[- ]seq\b",
        r"\binDrop\b", r"\bCEL[- ]seq2?\b", r"\bMARS[- ]seq\b", r"\bsci[- ]RNA[- ]seq\b",
        r"\bSeq[- ]Well\b",
    ],
    "snRNA": [r"\bsnRNA[- ]?seq\b", r"\bsingle[- ]nucleus RNA[- ]?(?:seq|sequencing)\b"],
    "scATAC": [
        r"\bscATAC[- ]?seq\b", r"\bsnATAC[- ]?seq\b", r"\bsci[- ]ATAC[- ]seq\b",
        r"\bsingle[- ](?:cell|nucleus) ATAC\b",
    ],
    "multiome": [r"\bmultiome\b", r"\bSNARE[- ]seq\b", r"\bSHARE[- ]seq\b", r"\bISSAAC[- ]seq\b"],
    "protein_multimodal": [r"\bCITE[- ]seq\b", r"\bREAP[- ]seq\b", r"\bASAP[- ]seq\b", r"\bTEA[- ]seq\b"],
    "perturb_screen": [
        r"\bPerturb[- ]seq\b", r"\bCROP[- ]seq\b", r"\bCRISP[- ]seq\b", r"\bMosaic[- ]seq\b",
        r"\bsci[- ]Plex\b",
    ],
    "patch_seq": [r"\bPatch[- ]seq\b"],
    "spatial_single_cell": [r"\bMERFISH\b", r"\bseqFISH\b", r"\bXenium\b", r"\bCosMx\b", r"\bSTARmap\b"],
    "single_cell_dna": [r"\bsingle[- ]cell (?:DNA|whole[- ]genome|exome)\b"],
}

# The v0.0.3 wording trap: a dissociation step, not an assay.
SUSPENSION_TRAP = re.compile(r"single[- ]cell suspension", re.IGNORECASE)


def _compile(groups: dict[str, list[str]]) -> dict[str, list[re.Pattern]]:
    return {g: [re.compile(p, re.IGNORECASE) for p in pats] for g, pats in groups.items()}


COMPILED_PERT = _compile(PERTURBATION_SIGNALS)
COMPILED_ASSAY = _compile(ASSAY_SIGNALS)


def screen(text: str, compiled: dict[str, list[re.Pattern]]) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for group, patterns in compiled.items():
        hits: list[tuple[str, str]] = []
        count = 0
        for pattern in patterns:
            matches = list(pattern.finditer(text))
            if not matches:
                continue
            count += len(matches)
            match = matches[0]
            start, end = max(0, match.start() - 90), min(len(text), match.end() + 90)
            hits.append((pattern.pattern, re.sub(r"\s+", " ", text[start:end]).strip()))
        if count:
            found[group] = {"count": count, "examples": hits[:4]}
    return found


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", default=str(work_default()))
    parser.add_argument("--out", default=str(output_default("review_screen.txt")))
    parser.add_argument("--prompt", default=str(ROOT / "prompt.md"))
    args = parser.parse_args()

    work = Path(args.work)
    manifest = {e["doi"]: e for e in json.loads((work / "manifest.json").read_text())}

    loaded: list[tuple[str, dict, Path]] = []
    for doi, entry in manifest.items():
        validated = work / "validated" / f"{doi}.json"
        if "error" not in entry and validated.exists():
            loaded.append((doi, json.loads(validated.read_text()), Path(entry["prompt_file"])))

    lines: list[str] = []
    counts = {"A": 0, "B": 0, "C": 0, "D": 0, "E": 0, "F": 0}

    # ---- Screen A: assay-pairing disagreements ------------------------------
    lines.append("=" * 78)
    lines.append("SCREEN A — assay-pairing flipped the call (review these first)")
    lines.append("perturbation_present_any_assay = yes, but perturbation_present = no/unclear")
    lines.append("=" * 78)
    for doi, result, _ in loaded:
        if not (result.get("validation") or {}).get("assay_filtered"):
            continue
        counts["A"] += 1
        lines.append("")
        lines.append(f"{doi}")
        lines.append(f"  present={result.get('perturbation_present')} "
                     f"any_assay={result.get('perturbation_present_any_assay')} "
                     f"has_sc_assay={result.get('has_single_cell_assay')} "
                     f"conf={result.get('paper_confidence')}")
        types = result.get("single_cell_assay_types") or []
        if isinstance(types, str):
            types = [types]
        lines.append(f"  sc assays reported: {', '.join(str(t) for t in types) or '(none)'}")
        for i, pert in enumerate(result.get("perturbations") or []):
            lines.append(f"    [{i}] paired={pert.get('single_cell_paired')} "
                         f"conf={pert.get('confidence')} {str(pert.get('agent'))[:58]}")
            lines.append(f"        assay_applied: {str(pert.get('assay_applied') or '(unstated)')[:110]}")
            assay_ev = pert.get("assay_evidence")
            quote = (assay_ev.get("quote") if isinstance(assay_ev, dict) else "") or ""
            src = (assay_ev.get("source_id") if isinstance(assay_ev, dict) else "") or "?"
            check = pert.get("assay_quote_check") or {}
            status = check.get("status")
            mark = "" if not quote.strip() else (
                "  [QUOTE OK]" if status == "verified" else f"  [{(status or 'UNCHECKED').upper()}]")
            shown = f"[{src}] {quote.strip()[:150]}" if quote.strip() else "(none — pairing inferred)"
            lines.append(f"        pairing quote: {shown}{mark}")
            if pert.get("pairing_downgraded_from"):
                lines.append(f"        NOTE: pairing downgraded from "
                             f"{pert['pairing_downgraded_from']!r} — assay_evidence unverifiable")
    if not counts["A"]:
        lines.append("")
        lines.append("  none — the assay-pairing requirement changed no determinations")

    # ---- Screen B: possible missed single-cell assay ------------------------
    lines.append("")
    lines.append("=" * 78)
    lines.append("SCREEN B — has_single_cell_assay is no/unclear, but assay language appears")
    lines.append("=" * 78)
    for doi, result, prompt_file in loaded:
        if result.get("has_single_cell_assay") == "yes":
            continue
        text = paper_text_from_prompt(prompt_file)
        found = screen(text, COMPILED_ASSAY)
        traps = len(SUSPENSION_TRAP.findall(text))
        lines.append("")
        lines.append(f"{doi}   has_single_cell_assay={result.get('has_single_cell_assay')} "
                     f"present={result.get('perturbation_present')}")
        if not found:
            lines.append("  no qualifying-assay language found — 'no' looks well supported")
        else:
            counts["B"] += 1
            for group, data in sorted(found.items(), key=lambda kv: -kv[1]["count"]):
                lines.append(f"  [{group}] {data['count']} hit(s)")
                for pattern, snippet in data["examples"][:2]:
                    lines.append(f"      /{pattern}/  ...{snippet[:170]}...")
        if traps:
            lines.append(f"  note: {traps} x 'single-cell suspension' — dissociation wording, "
                         f"not an assay -- prompt.md's wording trap)")

    # ---- Screen C: possible missed perturbation -----------------------------
    lines.append("")
    lines.append("=" * 78)
    lines.append("SCREEN C — perturbation_present_any_assay = no, but perturbation language appears")
    lines.append("=" * 78)
    for doi, result, prompt_file in loaded:
        if result.get("perturbation_present_any_assay") != "no":
            continue
        text = paper_text_from_prompt(prompt_file)
        found = screen(text, COMPILED_PERT)
        total = sum(g["count"] for g in found.values())
        lines.append("")
        lines.append(f"{doi}   any_assay=no conf={result.get('paper_confidence')} hits={total}")
        if not found:
            lines.append("  no perturbation language found — 'no' looks well supported")
        else:
            counts["C"] += 1
            for group, data in sorted(found.items(), key=lambda kv: -kv[1]["count"])[:4]:
                lines.append(f"  [{group}] {data['count']} hit(s)")
                for pattern, snippet in data["examples"][:1]:
                    lines.append(f"      /{pattern}/  ...{snippet[:170]}...")
        note = (result.get("ambiguities") or "").strip()
        if note:
            lines.append(f"  model's ambiguities note: {note[:300]}")

    # ---- Screen D: Stage-B caps (route to re-fetch, not to reading) ---------
    lines.append("")
    lines.append("=" * 78)
    lines.append("SCREEN D — capped at 'unclear' by Stage B (degraded/incomplete text)")
    lines.append("prompt.md step 10 priority 3: these go to the re-fetch queue, not to a reader")
    lines.append("=" * 78)
    for doi, result, _ in loaded:
        validation = result.get("validation") or {}
        if not validation.get("stage_b_capped"):
            continue
        counts["D"] += 1
        lines.append("")
        lines.append(f"{doi}   Stage A said {validation.get('stage_a')!r} -> reported "
                     f"{result.get('perturbation_present')!r}")
        lines.append(f"  processing_status={result.get('processing_status')} "
                     f"text_completeness={result.get('text_completeness')} "
                     f"reason={result.get('unresolved_reason')}")
        note = (result.get("ambiguities") or "").strip()
        if note:
            lines.append(f"  model's note: {note[:300]}")
    if not counts["D"]:
        lines.append("")
        lines.append("  none — no paper's negative rested on degraded text")

    # ---- Screen E: the multi-source path (validation loop step 4) -----------
    lines.append("")
    lines.append("=" * 78)
    lines.append("SCREEN E — supplementary-only evidence and quote attribution")
    lines.append("prompt.md validation loop step 4: the one behaviour a main-text-only")
    lines.append("sample cannot check. A perturbation resting ONLY on a supplementary")
    lines.append("quote is one the v0.0.4 main-text-only run could not have found.")
    lines.append("=" * 78)
    for doi, result, _ in loaded:
        perts = result.get("perturbations") or []
        by_source: dict[str, int] = {}
        supp_only = []
        for i, pert in enumerate(perts):
            sources = {str(q.get("source_id")) for q in (pert.get("evidence_quotes") or [])
                       if isinstance(q, dict)}
            for src in sources:
                by_source[src] = by_source.get(src, 0) + 1
            if sources and all(s.startswith("supp") for s in sources):
                supp_only.append((i, pert, sorted(sources)))
        validation = result.get("validation") or {}
        misattributed = [f for f in (validation.get("evidence_flags") or [])
                         if f in ("EV-WRONG-SOURCE", "CC-7")]
        if not supp_only and not misattributed:
            continue
        counts["E"] += 1
        lines.append("")
        lines.append(f"{doi}   present={result.get('perturbation_present')}  "
                     f"quotes by source: "
                     + (", ".join(f"{k}={v}" for k, v in sorted(by_source.items())) or "none"))
        if misattributed:
            lines.append(f"  ATTRIBUTION FLAGS: {', '.join(misattributed)}")
            for i, pert in enumerate(perts):
                for check in pert.get("quote_checks") or []:
                    if check.get("status") in ("wrong_source", "unknown_source"):
                        lines.append(f"    [{i}] claimed {check.get('claimed_source')!r} "
                                     f"-> actually {check.get('source_id')!r}: "
                                     f"{str(check.get('quote'))[:110]}")
        for i, pert, sources in supp_only:
            lines.append(f"    [{i}] SUPP-ONLY ({'|'.join(sources)}) "
                         f"paired={pert.get('single_cell_paired')} "
                         f"{str(pert.get('agent'))[:52]}")
            for quote in (pert.get("evidence_quotes") or [])[:1]:
                lines.append(f"        {str(quote.get('quote'))[:160]}")
    if not counts["E"]:
        lines.append("")
        lines.append("  none — no supplementary-only evidence and no misattributed quotes")

    # ---- Screen F: suppressed candidates (v0.0.10) --------------------------
    lines.append("")
    lines.append("=" * 78)
    lines.append("SCREEN F — suppressed candidates: what the NOT list swallowed")
    lines.append("Rows marked >>> WOULD HAVE PAIRED YES are one toggle from flipping the")
    lines.append("paper to 'yes'. They are a review of the RULES, not of the reading:")
    lines.append("the model named the candidate and judged its pairing, then excluded it.")
    lines.append("The paper-level marker counts only rules still under review, matching")
    lines.append("triage P2; a settled toggle pairing 'yes' is annotated but not flagged,")
    lines.append("since observational disease state alone would mark most clinical papers.")
    lines.append("=" * 78)

    def _supp_sort_key(item):
        doi, result, _ = item
        supp = result.get("suppressed_candidates") or []
        flips = any(s.get("would_have_paired") == "yes"
                    and s.get("rule") in RULES_UNDER_REVIEW for s in supp) and \
            result.get("perturbation_present") != "yes"
        return (0 if flips else 1, doi)

    rule_tally: dict[str, int] = {}
    for doi, result, _ in sorted(loaded, key=_supp_sort_key):
        supp = result.get("suppressed_candidates") or []
        if not supp:
            continue
        counts["F"] += 1
        present = result.get("perturbation_present")
        # Keyed on the rules under review, matching triage P2. A settled toggle
        # pairing "yes" is a fact worth printing but not a call to review, and
        # marking it would contradict the tier it is supposed to explain.
        flips = [s for s in supp if s.get("would_have_paired") == "yes"
                 and s.get("rule") in RULES_UNDER_REVIEW]
        lines.append("")
        marker = ("   >>> WOULD HAVE PAIRED YES — one toggle flips this paper"
                  if flips and present != "yes" else "")
        lines.append(f"{doi}   present={present}  "
                     f"{len(supp)} suppressed{marker}")
        for i, cand in enumerate(supp):
            rule = str(cand.get("rule"))
            rule_tally[rule] = rule_tally.get(rule, 0) + 1
            would = cand.get("would_have_paired")
            if would != "yes":
                flag = ""
            elif rule in RULES_UNDER_REVIEW:
                flag = "  <<< would have paired YES — rule under review"
            else:
                flag = "  <<< would have paired YES (settled toggle)"
            lines.append(f"    [{i}] {rule}  would_have_paired={would}{flag}")
            lines.append(f"        candidate: {str(cand.get('candidate'))[:110]}")
            lines.append(f"        why: {str(cand.get('why') or '(not given)')[:220]}")
            quote = cand.get("evidence_quote")
            if isinstance(quote, dict) and str(quote.get("quote") or "").strip():
                check = cand.get("quote_check") or {}
                mark = ("  [QUOTE OK]" if check.get("status") == "verified"
                        else f"  [{str(check.get('status') or 'UNCHECKED').upper()}]")
                lines.append(f"        quote: [{quote.get('source_id')}] "
                             f"{str(quote.get('quote')).strip()[:150]}{mark}")
            elif cand.get("evidence_quote_dropped"):
                lines.append("        quote: DROPPED as unverifiable — entry kept, "
                             "because the suppression still happened")
            else:
                lines.append("        quote: (none — exclusion rests on the absence "
                             "of a statement)")
    if not counts["F"]:
        lines.append("")
        lines.append("  none — no paper recorded a suppressed candidate. On a corpus of any")
        lines.append("  size that is itself suspicious: the NOT list is long, and a run where")
        lines.append("  nothing was ever excluded more likely means the field is being")
        lines.append("  skipped than that no paper had a candidate.")
    else:
        lines.append("")
        lines.append("  suppression rules used: "
                     + ", ".join(f"{k}={v}" for k, v in sorted(rule_tally.items())))

    header = [
        f"Review screen — prompt v{prompt_version(Path(args.prompt))} — "
        f"{len(loaded)} paper(s) validated",
        f"  Screen A (assay-pairing flipped the call): {counts['A']}",
        f"  Screen B (possible missed single-cell assay): {counts['B']}",
        f"  Screen C (possible missed perturbation): {counts['C']}",
        f"  Screen D (capped at unclear by Stage B — re-fetch queue): {counts['D']}",
        f"  Screen E (supplementary-only evidence / attribution): {counts['E']}",
        f"  Screen F (suppressed candidates — the NOT list's audit trail): {counts['F']}",
        "",
        "Screens, not verdicts. 'treated with' also describes routine processing, and",
        "'single-cell suspension' is a dissociation step rather than an assay.",
        "",
    ]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(header + lines) + "\n")
    print("\n".join(header[:7]))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
