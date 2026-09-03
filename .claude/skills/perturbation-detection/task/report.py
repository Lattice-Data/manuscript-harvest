"""TABLE 3's predicates: the triage ladder, the CSV row, the corpus counters.

Moved out of `pe/summarize.py`, which was 71% this task by line -- and almost all
of it table DATA rather than logic. The lists are `report.yaml`; the functions
that read a record are here.

The ladder was written twice in that file, once as predicates and once as labels
40 lines apart, with the only test pinning them code-against-code. It is one list
in report.yaml now, and `tiers()` reads it, so a renumber cannot land in one
place and not the other. `COLUMNS` and `row_for`'s 44-key literal were the same
shape of duplication: two hand-maintained halves that `csv.DictWriter` would only
catch at run time.

What `pe/summarize.py` keeps: reading a run, writing a CSV, sorting by tier, and
refusing to report on an empty set. It no longer knows what any column means.
"""

from __future__ import annotations

from collections import Counter

from pe.pack import tables
from task.rules import is_human, normalise_organism  # noqa: F401

_REP = tables()["report"]

#: prompt.md step 10's column list. One statement, read by the writer and by the
#: row builder below.
COLUMNS = list(_REP["columns"])

#: The ladder, as (n, label) in sort order. `triage_priority` returns the number;
#: `tier_labels` renders the queue summary from the same list.
TIERS = [(int(t["n"]), str(t["label"])) for t in _REP["tiers"]]

#: The confidence below which a positive is worth a second read. A FOURTH
#: threshold, matching none of prompt.md's three rubric band edges -- it came
#: from step 10 and is deliberately its own number.
LOW_CONFIDENCE_YES = float(_REP["low_confidence_yes"])

_LIMITS = _REP.get("column_limits") or {}


def tier_labels() -> list[tuple[int, str]]:
    return TIERS


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


UNRESOLVED_REASONS_TRIAGED = tuple(_REP["triaged_unresolved_reasons"])


def triage_priority(result: dict) -> int:
    """prompt.md v0.0.10 step 10's sort order. Lower sorts first.

    1 is the bucket most likely to hide a real match; 5 is the one the prompt
    says to sample rather than read in full. 9 is everything else, and 0 is
    reserved by `main` for rows that failed or are still pending.

    **The ladder renumbered at v0.0.10**: priority 2 is new, and the old 2-5
    shifted to 3-6. prompt.md step 10 carries the same list, and the two must be
    changed together -- a mismatch would silently mis-sort the curator's queue.

    **v0.0.12 did NOT renumber.** Its tier took the unused slot 7, so a priority
    column from v0.0.10 or v0.0.11 stays comparable to a v0.0.12 one for tiers
    1-6.
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
    # Restricted to the rules under review (task.rules.RULES_UNDER_REVIEW, from
    # `record.yaml: secondary_arrays[0].reasons_under_review`). The
    # unrestricted version put 5 of the 6 regression papers in this tier, because
    # `observational_disease_state` pairs "yes" on any disease-vs-healthy
    # contrast; a tier that holds most papers is not a queue.
    if present != "yes" and validation.get("suppressed_would_pair_yes_under_review"):
        return 2
    if present == "yes" and isinstance(confidence, (int, float)) \
            and confidence < LOW_CONFIDENCE_YES:
        return 3
    if present == "unclear" and reason == "degraded_text":
        return 4
    if present == "no" and result.get("perturbation_present_any_assay") == "yes":
        return 5
    # P6 is "the record has a defect", and an `unclear` carrying no usable reason
    # is one: `pe.validate` says so in `issues` ("the unclear bucket is not
    # triageable without a reason"), yet the paper used to fall past every tier
    # into P9, the bottom of the queue. No renumbering -- this widens an existing
    # tier rather than inserting one. Measured on the 392-paper v0.0.12 run: it
    # moves 0 papers, because no record currently hits it. It exists so that if
    # one ever does, the paper surfaces instead of sinking.
    untriageable_unclear = (present == "unclear"
                            and reason not in UNRESOLVED_REASONS_TRIAGED)
    if (validation.get("consistency_flags") or validation.get("evidence_flags")
            or untriageable_unclear):
        return 6
    # v0.0.12. A `yes` carried entirely by a non-human model. Tiers 1-6 flag
    # uncertainty or defect; this one flags a determination that is probably
    # CORRECT under these rules and may still be out of curation scope -- a
    # different kind of thing, so it sits below them rather than near the top.
    # It takes the previously-unused slot 7 precisely so 1-6 do not renumber;
    # the v0.0.10 renumber is a documented trap and is not repeated.
    if present == "yes" and validation.get("paired_organism_human") is False:
        return 7
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
        "single_cell_assay_types": _join_truncated(
            assay_types, **_LIMITS.get("single_cell_assay_types", {})),
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
        "suppressed_would_pair_yes_under_review": validation.get(
            "suppressed_would_pair_yes_under_review", ""),
        "paired_organisms": "|".join(validation.get("paired_organisms") or []),
        # "" rather than False when unknown: an organism nobody stated must not
        # read as a confident "not human".
        "paired_organism_human": ("" if validation.get("paired_organism_human") is None
                                  else validation.get("paired_organism_human")),
        "n_paired_yes_human": validation.get("n_paired_yes_human", ""),
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




def counters(rows: list[dict], results: dict[str, dict]) -> list[str]:
    """prompt.md step 11's corpus counters, over the rows this run produced."""
    ok = [r for r in rows if r["status"] == "ok"]
    out: list[str] = []

    def tally(label: str, key) -> None:
        counts = Counter(str(key(r)) for r in ok)
        out.append(f"  {label:<52} "
                   + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))

    validations = [r.get("validation") or {} for r in results.values()]
    versions = sorted({str(v.get("task_version") or "?") for v in validations}) or ["?"]
    packs = sorted({str(v.get("pack_sha256"))[:12] for v in validations
                    if v.get("pack_sha256")})
    out.append("")
    out.append(f"corpus counters (prompt.md step 11 — acceptance criteria) "
               f"— task v{'/'.join(versions)}"
               + (f", pack {'/'.join(packs)}" if packs else ""))
    if len(versions) > 1:
        out.append("  MIXED VERSIONS in one run — determinations from different "
                   "rule sets are not comparable; check which papers came from which")
    if len(packs) > 1:
        out.append("  MIXED PACK HASHES at the same task_version — the rules changed "
                   "without the version being bumped")
    # The countable form of "this record predates task_version". A per-paper note
    # would put one entry on every one of the 392 already-scored records, which is
    # the unreadable-issues-column failure the version collapse was undertaken to
    # remove. One line, and it names nothing when there is nothing to name.
    legacy = [d for d, r in results.items()
              if (r.get("validation") or {}).get("task_version_source")
              == "legacy_schema_version"]
    if legacy:
        out.append(f"  {'records predating task_version (read as their run version)':<52} "
                   f"{len(legacy)}/{len(results)}")
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
    # The share that comes from a rule still under review, which is what triage
    # acts on. The gap against the line above is the settled-toggle load --
    # mostly `observational_disease_state` on disease-vs-healthy contrasts.
    review = [r for r in held if r["suppressed_would_pair_yes_under_review"] is True]
    out.append(f"  {'  ...and under a rule still in review (triage P2)':<52} "
               f"{len(review)}"
               + (f" -> {', '.join(r['doi'] for r in review)}" if review else ""))

    # v0.0.12: the counter the curator otherwise runs a regex for. "yes carried
    # entirely by a non-human model" is the largest measured false-positive class
    # (5 of 15 positives on the 50-paper v0.0.11 run), and it is a SCOPE question,
    # so it is reported rather than acted on.
    by_org = Counter(o for r in ok
                     for o in (r["paired_organisms"] or "").split("|") if o)
    out.append(f"  {'organisms of yes-paired perturbations':<52} "
               + (", ".join(f"{k}={v}" for k, v in sorted(by_org.items())) or "none"))
    nonhuman = [r for r in ok
                if r["perturbation_present"] == "yes" and r["paired_organism_human"] is False]
    unknown = [r for r in ok
               if r["perturbation_present"] == "yes" and r["paired_organism_human"] == ""]
    out.append(f"  {'yes with NO human paired organism (triage P7)':<52} "
               f"{len(nonhuman)}/{sum(1 for r in ok if r['perturbation_present'] == 'yes')}"
               + (f" -> {', '.join(r['doi'] for r in nonhuman)}" if nonhuman else ""))
    out.append(f"  {'  ...and yes with organism unstated (not a P7)':<52} {len(unknown)}"
               + (f" -> {', '.join(r['doi'] for r in unknown)}" if unknown else ""))

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
    for priority, label in tier_labels():
        bucket = [r for r in ok if r["triage_priority"] == priority]
        out.append(f"  P{priority}  {label:<62} {len(bucket)}")
    return out


