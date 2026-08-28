#!/usr/bin/env python3
"""Stage 3: verify evidence quotes source-by-source, prune, then recompute.

    python -m pe.validate [--work work/] [--write-corpus]

prompt.md v0.0.5 batch spec step 6 changes this stage's job. Under v0.0.4 the
validator only *flagged*: it checked quotes, downgraded confidence, and left the
model's `perturbation_present` alone. v0.0.5 requires the harness to prune
unverified evidence and then RECOMPUTE the determination over what survives,
because "a determination resting on a hallucinated quote must not survive the
removal of that quote". Both values are kept -- `perturbation_present_model` and
`perturbation_present_final` -- and their disagreement rate is the corpus-level
signal for evidence fabrication.

v0.0.10 adds `suppressed_candidates` (schema 0.0.6). Its quotes are verified
exactly like the rest -- the field would be worthless if it were the one place a
quote went unchecked -- but an unverifiable quote drops the QUOTE and keeps the
ENTRY, because the suppression still happened and deleting the entry restores the
silence the field exists to remove. Nothing about suppressed candidates reaches
Stage A or Stage B: see `_validate_suppressed`.

No LLM calls.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pe.paper_text import (  # noqa: E402
    entry_paths, prompt_version, split_assembled,
    verify_quote_sourced,
)

try:
    import yaml
except ImportError:
    yaml = None

from pe.runroot import work_default  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DOWNGRADE_CONFIDENCE = 0.2
CATEGORIES = {
    "chemical", "biologic", "activation_stimulation", "genetic",
    "physical_environmental", "dietary", "other",
}
# prompt.md v0.0.10's closed `rule` set. Closed on purpose: the field exists to
# be tallied ("how many papers did the reporter rule hold back from yes?"), and
# an open string cannot be. The first four arrived with v0.0.9; the last four are
# older rules that had always been silent.
SUPPRESSION_RULES = (
    "reporter_or_marker", "incidental_clinical_therapy", "unintended_condition",
    "derivation_formulation", "observational_disease_state",
    "sample_handling_protocol", "readout_reagent", "routine_processing",
)
# The subset whose boundary is under active review -- the four v0.0.9 additions.
# The other four are long-settled toggles, and `observational_disease_state` in
# particular fires on any tumour-vs-normal or disease-vs-healthy contrast, which
# is most clinical papers. Triage uses this subset so priority 2 means "a rule
# we are still arguing about held this paper back" rather than "this paper has a
# disease contrast", which would drown the tier.
RULES_UNDER_REVIEW = (
    "reporter_or_marker", "incidental_clinical_therapy",
    "unintended_condition", "derivation_formulation",
)
TRISTATE = ("yes", "no", "unclear")
PROCESSING_STATUS = ("ok", "partial", "failed")
TEXT_COMPLETENESS = ("full", "truncated", "methods_missing", "unknown")
UNRESOLVED_REASONS = (
    "degraded_text", "pairing_not_stated", "assay_type_unconfirmed",
    "perturbation_role_unclear", "contradiction_unresolved", "none",
)


def _paired(result: dict) -> list:
    return [p.get("single_cell_paired") for p in (result.get("perturbations") or [])
            if isinstance(p, dict)]


def stage_a(result: dict) -> str | None:
    """prompt.md v0.0.5 Stage A: evidence-based determination, ordered A0-A6.

    Returns the implied `perturbation_present`, or None if a required input is
    missing or off-enum. The numbered comments map 1:1 onto the prompt's rules.
    """
    status = result.get("processing_status")
    has_sc = result.get("has_single_cell_assay")
    any_assay = result.get("perturbation_present_any_assay")
    perts = [p for p in (result.get("perturbations") or []) if isinstance(p, dict)]
    paired = [p.get("single_cell_paired") for p in perts]

    # A0. Nothing was assessed.
    if status == "failed":
        return "unclear"
    if any_assay not in TRISTATE or has_sc not in TRISTATE:
        return None

    # A1. Empty perturbations array -- an explicit terminal rule in v0.0.5,
    # rather than v0.0.4's resolution by vacuous truth through the paired list.
    if not perts:
        if any_assay == "no":
            return "no"
        if any_assay == "unclear":
            return "unclear"
        return "unclear"  # any_assay == "yes" is CC-2; stated default.

    # A2. No qualifying assay -> nothing to pair to.
    if has_sc == "no":
        return "no"
    # A3. An unconfirmed assay caps the paper-level call at "unclear".
    if has_sc == "unclear":
        return "unclear" if any(x in ("yes", "unclear") for x in paired) else "no"
    # A4. One confirmed pairing is sufficient.
    if "yes" in paired:
        return "yes"
    # A5. A single unresolved pairing is enough, however many "no"s accompany it.
    if "unclear" in paired:
        return "unclear"
    # A6. Every pairing resolved to "no".
    return "no"


def stage_b(stage_a_result: str | None, processing_status: str,
            text_completeness: str) -> tuple[str | None, bool]:
    """prompt.md v0.0.5 Stage B: cap a negative drawn from degraded text.

    Returns (determination, capped). The asymmetry is deliberate: missing text
    can hide the sentence that would have paired a perturbation to a single-cell
    assay, but it cannot invent one, so only "no" is capped.
    """
    degraded = processing_status == "partial" or (
        text_completeness in TEXT_COMPLETENESS and text_completeness != "full")
    if degraded and stage_a_result == "no":
        return "unclear", True
    return stage_a_result, False


def expected_determination(result: dict) -> str | None:
    """Stage A then Stage B, as the harness applies them."""
    a = stage_a(result)
    if a is None:
        return None
    final, _ = stage_b(a, result.get("processing_status"),
                       result.get("text_completeness"))
    return final


def consistency_checks(result: dict) -> list[str]:
    """CC-1 .. CC-6 from prompt.md v0.0.5. CC-7 is raised during quote checking."""
    codes: list[str] = []
    has_sc = result.get("has_single_cell_assay")
    any_assay = result.get("perturbation_present_any_assay")
    perts = [p for p in (result.get("perturbations") or []) if isinstance(p, dict)]
    paired = [p.get("single_cell_paired") for p in perts]

    if has_sc == "no" and "yes" in paired:
        codes.append("CC-1")
    if any_assay == "yes" and not perts:
        codes.append("CC-2")
    if any_assay == "no" and perts:
        codes.append("CC-3")
    if any(p not in TRISTATE for p in paired):
        codes.append("CC-4")
    if has_sc == "unclear" and "yes" in paired:
        codes.append("CC-5")
    if result.get("processing_status") == "failed" and perts:
        codes.append("CC-6")
    return codes


CC_TEXT = {
    "CC-1": "has_single_cell_assay='no' but a perturbation is single_cell_paired='yes'",
    "CC-2": "perturbation_present_any_assay='yes' with an empty perturbations array",
    "CC-3": "perturbation_present_any_assay='no' but perturbation(s) reported",
    "CC-4": "a single_cell_paired value outside yes/no/unclear",
    "CC-5": "has_single_cell_assay='unclear' with a single_cell_paired='yes'",
    "CC-6": "processing_status='failed' with a non-empty perturbations array",
    "CC-7": "a quote citing a source_id that was never supplied",
}


def parse_raw(text: str) -> dict:
    """Accept the model's JSON whether or not it arrived wrapped in prose/fences."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json)?\s*\n(.*?)\n\s*```", text, re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            pass

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return json.loads(text[start : end + 1])
    raise ValueError("no JSON object found")


def paper_text_from_prompt(prompt_file: Path) -> str:
    """Recover exactly the PAPER_TEXT the model saw, so validation is honest."""
    body = prompt_file.read_text()
    marker = "\nPAPER_TEXT:"
    idx = body.rindex(marker)
    return body[idx + len(marker):].lstrip("\n")


def _normalize_quote_entry(entry, default_source: str = "main") -> tuple[str, str, bool]:
    """Return (source_id, quote, was_legacy_string).

    v0.0.4 emitted bare strings; v0.0.5 requires {"source_id", "quote"}. A bare
    string is accepted so an off-schema response is flagged rather than crashing
    the stage, but it is reported.
    """
    if isinstance(entry, dict):
        return str(entry.get("source_id") or default_source), str(entry.get("quote") or ""), False
    return default_source, str(entry or ""), True


def _validate_suppressed(result: dict, sources_text: dict[str, str], threshold: float,
                         issues: list[str], evidence_flags: set[str]) -> tuple[list, int, int, int]:
    """Verify and normalize `suppressed_candidates` (prompt.md v0.0.10, schema 0.0.6).

    Returns (entries, checked, failed, wrong_source).

    **This function cannot move the determination, and that is structural rather
    than a convention to be careful about.** `stage_a` reads exactly four things
    -- `processing_status`, `has_single_cell_assay`,
    `perturbation_present_any_assay`, and the `single_cell_paired` values inside
    `perturbations` -- and nothing here writes any of them. A suppressed
    candidate is by definition not a perturbation, so it is never appended to
    that array and never promoted out of this one. If a suppressed candidate ever
    changes a determination, the bug is a write that escaped this function.

    An unverifiable quote drops the quote and keeps the entry (step 6). The
    alternative -- dropping the entry -- would restore exactly the silence the
    field was added to remove, and would make a bad quote look like a decision
    that was never made.
    """
    raw = result.get("suppressed_candidates")
    if raw is None:
        issues.append(
            "suppressed_candidates missing; schema 0.0.6 requires it. Use [] when "
            "nothing was suppressed: a null cannot be told apart from 'the model "
            "never considered the question', which is the ambiguity this field exists "
            "to remove")
        raw = []
    elif not isinstance(raw, list):
        issues.append(f"suppressed_candidates={raw!r} is not a list")
        raw = []

    checked = failed = wrong_source = 0
    entries: list[dict] = []

    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            issues.append(f"suppressed_candidates[{i}] is not an object")
            continue

        rule = item.get("rule")
        if rule not in SUPPRESSION_RULES:
            issues.append(
                f"suppressed_candidates[{i}].rule={rule!r} is outside the closed set "
                f"{list(SUPPRESSION_RULES)} — an open value cannot be tallied, which "
                f"is the whole point of the field")
        if item.get("would_have_paired") not in TRISTATE:
            issues.append(f"suppressed_candidates[{i}].would_have_paired="
                          f"{item.get('would_have_paired')!r} not in yes/no/unclear")
        if not str(item.get("candidate") or "").strip():
            issues.append(f"suppressed_candidates[{i}].candidate is empty — the entry "
                          f"names nothing and cannot be reviewed")

        entry = item.get("evidence_quote")
        if isinstance(entry, str):
            issues.append(f"suppressed_candidates[{i}].evidence_quote is a bare string, "
                          f"expected {{source_id, quote}}")
            entry = {"source_id": "main", "quote": entry}

        if isinstance(entry, dict) and str(entry.get("quote") or "").strip():
            quote = str(entry.get("quote"))
            claimed = str(entry.get("source_id") or "main")
            outcome = verify_quote_sourced(quote, claimed, sources_text, threshold)
            checked += 1
            item["quote_check"] = outcome

            if outcome["status"] == "verified":
                item["evidence_quote"] = {"source_id": claimed, "quote": quote}
            elif outcome["status"] in ("wrong_source", "unknown_source"):
                wrong_source += 1
                evidence_flags.add("EV-WRONG-SOURCE")
                item["evidence_quote"] = {"source_id": outcome["source_id"],
                                          "quote": quote,
                                          "source_id_corrected_from": claimed}
                if outcome["status"] == "unknown_source":
                    evidence_flags.add("CC-7")
                    issues.append(f"suppressed_candidates[{i}] quote cited unknown source "
                                  f"{claimed!r}; found in {outcome['source_id']!r} (CC-7)")
                else:
                    issues.append(f"suppressed_candidates[{i}] quote attributed to "
                                  f"{claimed!r} but found in {outcome['source_id']!r} "
                                  f"(EV-WRONG-SOURCE)")
            else:
                failed += 1
                evidence_flags.add("EV-SUPPRESSED-UNVERIFIED")
                item["evidence_quote_dropped"] = {"source_id": claimed, "quote": quote}
                item["evidence_quote"] = None
                issues.append(
                    f"suppressed_candidates[{i}] ({str(item.get('candidate'))[:50]!r}) "
                    f"quote unverifiable in any source (best ratio {outcome['ratio']}) — "
                    f"quote dropped, ENTRY KEPT (EV-SUPPRESSED-UNVERIFIED)")
        else:
            # Legitimate per the prompt: an exclusion resting on the ABSENCE of a
            # statement has nothing to quote. `why` is expected to say so.
            item["evidence_quote"] = None

        entries.append(item)

    # prompt.md v0.0.10: `would_have_paired` held to Step 3's evidence standard,
    # not used as an emphasis marker. If every entry says "yes" the column has
    # stopped discriminating -- and in practice that pattern travelled with the
    # field over-firing, pulling real perturbations across the line (observed on
    # 10.1038/s41586-024-07571-1 and 10.7554/elife.104978.2, both moved yes ->
    # no by a wrongly-suppressed clinical therapy). Mechanically checkable, so
    # it is checked. This raises an issue only: judgment stays in the prompt.
    if len(entries) >= 2 and all(e.get("would_have_paired") == "yes" for e in entries):
        issues.append(
            f"all {len(entries)} suppressed candidates have would_have_paired='yes'; "
            f"the column has stopped discriminating. Check that none of them is "
            f"actually a perturbation under a Step 2 report rule — filling "
            f"suppressed_candidates must not shorten the perturbations array")

    return entries, checked, failed, wrong_source


def validate_result(result: dict, sources_text: dict[str, str], threshold: float,
                    version: str = "unknown", truncated_by_harness: bool = False) -> dict:
    """Verify quotes per source, prune, recompute the determination."""
    issues: list[str] = []
    evidence_flags: set[str] = set()
    source_ids = set(sources_text)

    model_present = result.get("perturbation_present")

    # ---- flat enum checks -------------------------------------------------
    if model_present not in TRISTATE:
        issues.append(f"perturbation_present={model_present!r} not in yes/no/unclear")
    for field in ("has_single_cell_assay", "perturbation_present_any_assay"):
        if result.get(field) not in TRISTATE:
            issues.append(f"{field}={result.get(field)!r} not in yes/no/unclear")
    if result.get("processing_status") not in PROCESSING_STATUS:
        issues.append(f"processing_status={result.get('processing_status')!r} off-schema")
    if result.get("text_completeness") not in TEXT_COMPLETENESS:
        issues.append(f"text_completeness={result.get('text_completeness')!r} off-schema")
    if result.get("unresolved_reason") not in UNRESOLVED_REASONS:
        issues.append(f"unresolved_reason={result.get('unresolved_reason')!r} off-schema")
    if str(result.get("schema_version")) != "0.0.6":
        issues.append(f"schema_version={result.get('schema_version')!r}, expected '0.0.6'")
    if not isinstance(result.get("consistency_flags"), list):
        issues.append("consistency_flags missing or not a list")

    seen = result.get("sources_seen")
    if not isinstance(seen, list) or set(map(str, seen)) != source_ids:
        issues.append(f"sources_seen={seen!r} does not match the supplied "
                      f"{sorted(source_ids)} — the assembly step may have dropped a file")

    if result.get("has_single_cell_assay") == "yes" and not result.get("single_cell_assay_types"):
        issues.append("has_single_cell_assay='yes' but single_cell_assay_types is empty")

    # prompt.md batch spec step 3: truncation is a harness fact the model cannot
    # see, so it is enforced here rather than trusted.
    if truncated_by_harness and result.get("text_completeness") == "full":
        issues.append("harness truncated the text but the model reported "
                      "text_completeness='full'; treating it as 'truncated'")
        result["text_completeness"] = "truncated"
        result["text_completeness_source"] = "harness"

    perturbations = result.get("perturbations") or []
    if not isinstance(perturbations, list):
        issues.append("perturbations is not a list")
        perturbations = []

    # ---- source-scoped quote verification + pruning (batch spec step 6) ----
    checked = failed = wrong_source = 0
    kept: list[dict] = []
    dropped: list[dict] = []
    index_map: dict[int, int] = {}

    for i, pert in enumerate(perturbations):
        if not isinstance(pert, dict):
            issues.append(f"perturbations[{i}] is not an object")
            continue

        if pert.get("category") not in CATEGORIES:
            issues.append(f"perturbations[{i}].category={pert.get('category')!r} off-schema")

        paired = pert.get("single_cell_paired")
        if paired not in TRISTATE:
            issues.append(
                f"perturbations[{i}].single_cell_paired={paired!r} not in yes/no/unclear")

        raw_quotes = pert.get("evidence_quotes") or []
        if isinstance(raw_quotes, (str, dict)):
            raw_quotes = [raw_quotes]

        verified_quotes, quote_checks = [], []
        for entry in raw_quotes:
            source_id, quote, legacy = _normalize_quote_entry(entry)
            if legacy:
                issues.append(f"perturbations[{i}] evidence_quote is a bare string "
                              f"(v0.0.4 shape), expected {{source_id, quote}}")
            outcome = verify_quote_sourced(quote, source_id, sources_text, threshold)
            checked += 1
            check = {"claimed_source": source_id, "quote": quote, **outcome}
            quote_checks.append(check)

            if outcome["status"] == "verified":
                verified_quotes.append({"source_id": source_id, "quote": quote})
            elif outcome["status"] in ("wrong_source", "unknown_source"):
                # Keep the text, correct the attribution, flag it.
                wrong_source += 1
                verified_quotes.append({"source_id": outcome["source_id"], "quote": quote,
                                        "source_id_corrected_from": source_id})
                evidence_flags.add("EV-WRONG-SOURCE")
                if outcome["status"] == "unknown_source":
                    evidence_flags.add("CC-7")
                    issues.append(f"perturbations[{i}] quote cited unknown source "
                                  f"{source_id!r}; found in {outcome['source_id']!r} (CC-7)")
                else:
                    issues.append(f"perturbations[{i}] quote attributed to {source_id!r} "
                                  f"but found in {outcome['source_id']!r} (EV-WRONG-SOURCE)")
            else:
                failed += 1
                evidence_flags.add("EV-UNVERIFIED")
                issues.append(f"perturbations[{i}] quote unverifiable in any source "
                              f"(best ratio {outcome['ratio']}) — dropped")

        pert["quote_checks"] = quote_checks
        pert["evidence_quotes"] = verified_quotes
        pert["quotes_validated"] = bool(verified_quotes)

        # assay_evidence: object or null in v0.0.5.
        assay_ev = pert.get("assay_evidence")
        if isinstance(assay_ev, str):
            issues.append(f"perturbations[{i}].assay_evidence is a bare string "
                          f"(v0.0.4 assay_evidence_quote shape)")
            assay_ev = {"source_id": "main", "quote": assay_ev}
        if isinstance(assay_ev, dict) and str(assay_ev.get("quote") or "").strip():
            outcome = verify_quote_sourced(str(assay_ev.get("quote")),
                                           str(assay_ev.get("source_id") or "main"),
                                           sources_text, threshold)
            checked += 1
            pert["assay_quote_check"] = outcome
            if outcome["status"] == "unverified":
                failed += 1
                if paired in ("yes", "no"):
                    pert["single_cell_paired"] = "unclear"
                    pert["pairing_downgraded_from"] = paired
                    paired = "unclear"
                    evidence_flags.add("EV-PAIRING-DOWNGRADED")
                    issues.append(
                        f"perturbations[{i}].assay_evidence unverifiable — pairing "
                        f"downgraded to 'unclear' (EV-PAIRING-DOWNGRADED)")
                else:
                    evidence_flags.add("EV-UNVERIFIED")
            elif outcome["status"] in ("wrong_source", "unknown_source"):
                wrong_source += 1
                evidence_flags.add("EV-WRONG-SOURCE")
                assay_ev = dict(assay_ev, source_id=outcome["source_id"],
                                source_id_corrected_from=assay_ev.get("source_id"))
                pert["assay_evidence"] = assay_ev
        elif paired in ("yes", "no"):
            # Legitimate per the prompt (an inferred pairing), but recorded so a
            # curator can see the pairing is not quoted.
            issues.append(
                f"perturbations[{i}].single_cell_paired={paired!r} asserted with no "
                f"assay_evidence (pairing is inferred, not quoted)")

        if not verified_quotes:
            # batch spec step 6: zero verified quotes -> drop the perturbation.
            evidence_flags.add("EV-PERT-DROPPED")
            issues.append(
                f"perturbations[{i}] ({pert.get('agent')!r}) DROPPED: no evidence quote "
                f"could be verified against any source")
            pert["dropped_reason"] = "no verifiable evidence quote"
            pert["confidence_original"] = pert.get("confidence")
            pert["confidence"] = DOWNGRADE_CONFIDENCE
            dropped.append(pert)
        else:
            index_map[i] = len(kept)
            kept.append(pert)

    result["perturbations"] = kept
    if dropped:
        result["perturbations_dropped"] = dropped

    # ---- samples ----------------------------------------------------------
    for j, sample in enumerate(result.get("samples") or []):
        if not isinstance(sample, dict):
            issues.append(f"samples[{j}] is not an object")
            continue
        # v0.0.5 curator ruling: true | false | "unclear" are all schema-legal,
        # so "unclear" is no longer an issue. Only pe.summarize's `is true` test
        # decides what counts as perturbed.
        if sample.get("perturbed") not in (True, False, "unclear"):
            issues.append(
                f"samples[{j}].perturbed={sample.get('perturbed')!r} not in "
                f"true/false/'unclear'")
        if sample.get("is_single_cell_assay") not in TRISTATE:
            issues.append(f"samples[{j}].is_single_cell_assay="
                          f"{sample.get('is_single_cell_assay')!r} not in yes/no/unclear")
        # Reindex refs onto the pruned array so they never dangle.
        refs = sample.get("perturbation_refs") or []
        remapped = []
        for ref in refs:
            if not isinstance(ref, int) or ref not in index_map:
                if isinstance(ref, int) and 0 <= ref < len(perturbations):
                    issues.append(f"samples[{j}].perturbation_refs -> {ref} pointed at a "
                                  f"dropped perturbation; reference removed")
                else:
                    issues.append(f"samples[{j}].perturbation_refs contains invalid "
                                  f"index {ref!r}")
                continue
            remapped.append(index_map[ref])
        if remapped != refs:
            sample["perturbation_refs_original"] = refs
            sample["perturbation_refs"] = remapped

    # ---- suppressed candidates (v0.0.10) ----------------------------------
    # Placed after the perturbation loop so its quotes join the same counters,
    # and before the recomputation only for readability: it writes nothing the
    # recomputation reads. See `_validate_suppressed`.
    suppressed, s_checked, s_failed, s_wrong = _validate_suppressed(
        result, sources_text, threshold, issues, evidence_flags)
    result["suppressed_candidates"] = suppressed
    checked += s_checked
    failed += s_failed
    wrong_source += s_wrong

    # ---- consistency + recomputation --------------------------------------
    cc_codes = consistency_checks(result)
    if "CC-7" in evidence_flags:
        cc_codes.append("CC-7")
    for code in cc_codes:
        issues.append(f"{code}: {CC_TEXT[code]}")

    model_flags = [str(f) for f in (result.get("consistency_flags") or [])]
    undeclared = sorted(set(cc_codes) - set(model_flags))
    if undeclared:
        issues.append(f"consistency_flags did not declare {undeclared} "
                      f"(model reported {model_flags or 'none'})")

    a_result = stage_a(result)
    final, capped = stage_b(a_result, result.get("processing_status"),
                            result.get("text_completeness"))

    result["perturbation_present_model"] = model_present
    result["perturbation_present"] = final if final is not None else model_present
    result["perturbation_present_final"] = result["perturbation_present"]

    # unresolved_reason: "none" unless the final call is unclear; Stage B owns
    # the degraded_text case outright.
    if capped:
        result["unresolved_reason"] = "degraded_text"
    elif result["perturbation_present"] != "unclear" and result.get("unresolved_reason") != "none":
        issues.append(f"unresolved_reason={result.get('unresolved_reason')!r} but "
                      f"perturbation_present={result['perturbation_present']!r}; "
                      f"forcing 'none'")
        result["unresolved_reason"] = "none"
    elif result["perturbation_present"] == "unclear" and result.get("unresolved_reason") in (None, "none"):
        issues.append("perturbation_present='unclear' with unresolved_reason='none'; "
                      "the unclear bucket is not triageable without a reason")

    if final is not None and model_present != final:
        issues.append(
            f"determination recomputed: model said {model_present!r}, harness Stage "
            f"A/B over the pruned evidence gives {final!r}")

    paired_values = _paired(result)
    result["validation"] = {
        "quotes_checked": checked,
        "quotes_failed": failed,
        "quotes_wrong_source": wrong_source,
        "perturbations_kept": len(kept),
        "perturbations_dropped": len(dropped),
        # v0.0.10: what the NOT list swallowed on this paper. `would_pair_yes` is
        # the actionable one -- those papers are one toggle from "yes".
        "n_suppressed": len(suppressed),
        "suppressed_rules": sorted({str(s.get("rule")) for s in suppressed
                                    if s.get("rule") in SUPPRESSION_RULES}),
        # Both are reported: the raw fact, and the subset triage acts on. A
        # curator comparing them sees how much of the suppression load comes
        # from settled toggles rather than from the rules in review.
        "suppressed_would_pair_yes": any(
            s.get("would_have_paired") == "yes" for s in suppressed),
        "suppressed_would_pair_yes_under_review": any(
            s.get("would_have_paired") == "yes"
            and s.get("rule") in RULES_UNDER_REVIEW for s in suppressed),
        "suppressed_quotes_checked": s_checked,
        "suppressed_quotes_failed": s_failed,
        "paired_yes": paired_values.count("yes"),
        "paired_no": paired_values.count("no"),
        "paired_unclear": paired_values.count("unclear"),
        "mixed_no_unclear": ("no" in paired_values and "unclear" in paired_values
                             and "yes" not in paired_values),
        "assay_filtered": (result.get("perturbation_present_any_assay") == "yes"
                           and result["perturbation_present"] in ("no", "unclear")),
        "stage_a": a_result,
        "stage_b_capped": capped,
        "determination_changed_by_harness": final is not None and model_present != final,
        "consistency_flags": cc_codes,
        "evidence_flags": sorted(evidence_flags),
        "issues": issues,
        "threshold": threshold,
        "prompt_version": version,
        "sources_verified_against": sorted(source_ids),
    }
    # User decision: every paper gets human review regardless of confidence.
    result["needs_review"] = True
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", default=str(work_default()))
    parser.add_argument("--threshold", type=float, default=None,
                        help="overrides config.yaml fuzzy_match.threshold")
    parser.add_argument("--config", default=str(ROOT / "config.yaml"))
    parser.add_argument("--prompt", default=str(ROOT / "prompt.md"))
    parser.add_argument("--corpus", default="./corpus",
                        help="directory of <paper_id>/extracted/blocks.jsonl")
    parser.add_argument("--write-corpus", action="store_true",
                        help="also write corpus/<doi>/extracted/perturbations.json")
    args = parser.parse_args()

    threshold = args.threshold
    if threshold is None:
        config = {}
        if yaml and Path(args.config).exists():
            config = yaml.safe_load(Path(args.config).read_text()) or {}
        threshold = (config.get("fuzzy_match") or {}).get("threshold", 0.85)

    version = prompt_version(Path(args.prompt)) if Path(args.prompt).exists() else "unknown"

    work = Path(args.work)
    manifest = json.loads((work / "manifest.json").read_text())
    (work / "validated").mkdir(parents=True, exist_ok=True)

    done = missing = broken = 0
    for entry in manifest:
        doi = entry["doi"]
        if "error" in entry:
            continue
        prompt_file, raw_file = entry_paths(entry, work)
        if not raw_file.exists():
            print(f"  PENDING {doi}: no raw output yet")
            missing += 1
            continue

        try:
            result = parse_raw(raw_file.read_text())
        except (ValueError, json.JSONDecodeError) as exc:
            print(f"  BAD JSON {doi}: {exc}")
            broken += 1
            continue

        result.setdefault("paper_id", doi)
        paper_text = paper_text_from_prompt(prompt_file)
        sources_text = split_assembled(paper_text)
        # The manifest records the version the prompts were built from; prefer it
        # over whatever prompt.md says right now.
        entry_version = entry.get("prompt_version") or version
        result = validate_result(
            result, sources_text, threshold, entry_version,
            truncated_by_harness=bool(entry.get("truncation", {}).get("truncated")))

        payload = json.dumps(result, indent=2)
        (work / "validated" / f"{doi}.json").write_text(payload)
        if args.write_corpus:
            corpus_out = Path(args.corpus) / doi / "extracted" / "perturbations.json"
            corpus_out.parent.mkdir(parents=True, exist_ok=True)
            corpus_out.write_text(payload)
        done += 1

        v = result["validation"]
        quotes_ok = v["quotes_checked"] - v["quotes_failed"]
        flags = ""
        if v["perturbations_dropped"]:
            flags += f"  DROPPED={v['perturbations_dropped']}"
        if v["stage_b_capped"]:
            flags += "  STAGE-B-CAP"
        if v["determination_changed_by_harness"]:
            flags += f"  MODEL={result['perturbation_present_model']}"
        if v["assay_filtered"]:
            flags += "  ASSAY-FILTERED"
        if v["consistency_flags"]:
            flags += "  " + ",".join(v["consistency_flags"])
        if v["evidence_flags"]:
            flags += "  " + ",".join(v["evidence_flags"])
        print(f"  {doi:38} {str(result.get('processing_status', '?')):8}"
              f"{str(result.get('text_completeness', '?')):16}"
              f"sc={str(result.get('has_single_cell_assay', '?')):8}"
              f"{str(result.get('perturbation_present', '?')):8}"
              f"(any={str(result.get('perturbation_present_any_assay', '?')):8}) "
              f"conf={str(result.get('paper_confidence', '?')):<5} "
              f"perts={v['perturbations_kept']:<3} "
              f"y/n/u={v['paired_yes']}/{v['paired_no']}/{v['paired_unclear']:<2} "
              f"q={quotes_ok}/{v['quotes_checked']}{flags}")

    print(f"\nvalidated {done} | pending {missing} | unparseable {broken}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
