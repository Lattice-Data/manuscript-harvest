"""TABLE 4's predicates: which mechanism can account for a paper moving.

Moved out of `pe/compare.py`. The labels are `change.yaml`; the predicates that
read a pair of records are here, because each one names fields only this task
has -- `single_cell_paired`, `suppressed_candidates`, `stage_b_capped`.

The one invariant worth stating twice: `determination_inputs` must list exactly
what the decision reads. If the two ever disagree, the diff reports a change as
UNEXPLAINED while the input that moved is sitting in plain sight -- so it is
checked against decide.yaml at import rather than left to a comment.

What `pe/compare.py` keeps: loading two runs, refusing an empty overlap, the
confusion matrix, the noise floor and its same-version guard, and the
UNEXPLAINED warning. All of it task-blind.
"""

from __future__ import annotations

import re

from task import PackError, tables

_T = tables()
_CHG = _T["change"]
_DEC = _T["decide"]

#: Reading order for the confusion matrix. "yes" first: that is the bucket
#: curation acts on, so alphabetical would bury it.
ORDER = list(_CHG["order"])

#: Every mechanism allowed to account for a movement. The value of this table is
#: in what it does NOT contain -- each entry silences an UNEXPLAINED warning,
#: which is the only thing in the pipeline that says a human must look before the
#: corpus run.
CLASS_LABELS = dict(_CHG["classes"])
UNEXPLAINED = str(_CHG["unexplained_class"])
NOISE_CLASS = str(_CHG["noise_class"])

_MATCH = _CHG["match"]
_MIN_SHARED = int(_MATCH["min_shared_words"])
_STANDALONE = int(_MATCH["standalone_word_length"])
_STOPWORDS = frozenset(str(w) for w in _MATCH["stopwords"])

#: The determination's input set, from decide.yaml. Read rather than restated:
#: this list and the decision must agree about what the decision reads.
#: Printed under the confusion matrix. Which caveat is worth giving a reader
#: depends on the question, so it is the pack's. This text was three hardcoded
#: lines in `pe/compare.py` that named SUPP-EVIDENCE -- a class only this pack
#: declares -- so a second pack's report pointed at a class absent from its own
#: table.
DIFF_PREAMBLE = [
    "Check whether the two runs saw the same input scope: a baseline built before",
    "supplementary sources were included is not comparable on evidence alone, and",
    "the SUPP-EVIDENCE class below flags papers where that mattered.",
]

_INPUTS = dict(_DEC["inputs"])

def determination_inputs(result: dict) -> dict:
    """The complete input set the determination is a function of.

    Stage A and Stage B read nothing else, so if two runs agree on all five of
    these and still disagree on `perturbation_present`, the logic itself is
    wrong. If they differ on any of them, the change is fully explained by the
    input that moved -- which is what makes this diff auditable rather than a
    matter of opinion.
    """
    out = {}
    for name, path in _INPUTS.items():
        if "[]." in path:
            array, field = path.split("[].", 1)
            out[name] = sorted(str(p.get(field))
                               for p in (result.get(array) or [])
                               if isinstance(p, dict))
        else:
            out[name] = result.get(path)
    return out


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
    old_capped = bool(((old or {}).get("validation") or {}).get("stage_b_capped"))
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

    return classes or [UNEXPLAINED]


def _tokens(value) -> set[str]:
    """Content words of an agent/candidate string, for matching across runs.

    Deliberately crude: the two runs describe the same construct in their own
    words ("SFTPC-GFP reporter line" vs "lentiviral SFTPC-promoter-GFP +
    EF1a-TagRFP reporter"), so an exact match would never fire. Short and
    generic words are dropped so "the medium" does not match "the inhibitor".
    """
    # Generic qualifiers are dropped as hard as articles are. "specific" is
    # eight characters and carries no identity, so leaving it in would let the
    # single-distinctive-token rule below fire on it.
    stop = _STOPWORDS
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9\-']{2,}", str(value or "").lower())
    # Hyphenated compounds are emitted whole AND split. Without the split,
    # "chemotherapy-driven lineage switch" shares no token with the baseline's
    # "chemotherapy (specific agent not named)" and the pair goes unmatched --
    # observed on 10.7554/elife.104978.2, where the suppression was real and the
    # precise attribution was lost anyway.
    out = set()
    for w in words:
        out.add(w)
        if "-" in w:
            out.update(part for part in w.split("-") if len(part) >= 3)
    return {w for w in out if w not in stop}


def _looks_like_same_thing(a: set[str], b: set[str]) -> bool:
    """Whether two token sets plausibly name the same construct or condition.

    Two shared content words, or one shared word distinctive enough to stand
    alone. The single-token allowance exists because the two runs often describe
    the same thing at different lengths: "chemotherapy (specific agent not
    named)" against "chemotherapy-driven lineage switch in the relapse sample"
    shares exactly one word, and it is the only one that matters.

    Deliberately conservative in the false-positive direction. A wrong match
    would make `SUPPRESSED` silence an UNEXPLAINED warning, which is the one
    thing this module must not do; a missed match only costs precision, dropping
    the paper to the weaker PERT-SET-CHANGED account.
    """
    shared = a & b
    if len(shared) >= _MIN_SHARED:
        return True
    return any(len(word) >= _STANDALONE for word in shared)


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
        # Still reported in this run? Then it was not suppressed. Same test as
        # below, so the two sides cannot disagree about what "the same thing"
        # means -- a looser candidate match than still-reported match would let a
        # perturbation that survived under a reworded agent count as suppressed.
        if any(_looks_like_same_thing(old_toks, cur) for cur in new_agents):
            continue
        for cand in supp:
            cand_toks = _tokens(cand.get("candidate"))
            if _looks_like_same_thing(old_toks, cand_toks):
                matches.append((str(cand.get("rule")), str(pert.get("agent")),
                                str(cand.get("candidate"))))
                break
    return matches


def _quote_line(entry) -> str:
    if isinstance(entry, dict):
        src = entry.get("source_id", "?")
        return f"[{src}] {str(entry.get('quote', ''))[:150]}"
    return str(entry)[:150]

#: The field a curator reads. `pe.compare` diffs this and nothing else.
PRIMARY_FIELD = tables()["record"]["primary_field"]
PRIMARY_FIELD_GLOSS = tables()["record"]["primary_field_gloss"]


def render_paper(doi: str, old: dict, new: dict, entry: dict,
                 classes: list[str]) -> list[str]:
    """The per-paper detail block for a changed paper.

    Every line names a field only this task has -- `single_cell_assay_types`,
    `agent`, `assay_applied`, `assay_evidence`, `single_cell_paired` -- so it
    belongs to the pack. The harness decides which papers reach here; this
    decides what a reader is shown about one.
    """
    lines: list[str] = []
    validation = new.get("validation") or {}
    types = new.get("single_cell_assay_types") or []
    if isinstance(types, str):
        types = [types]
    lines.append("")
    lines.append(f"{doi}:  {old.get('perturbation_present')}  ->  "
                 f"{new.get('perturbation_present')}"
                 f"   [{', '.join(classes)}]")
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
    # Versions read off the records, not written in. These two were literal
    # "v0.0.4"/"v0.0.5" strings, so every run since v0.0.5 has mislabelled
    # both columns of its own diff.
    old_v = str((old.get("validation") or {}).get("prompt_version") or "?")
    new_v = str((new.get("validation") or {}).get("prompt_version") or "?")
    lines.append(f"  v{old_v} had {len(old.get('perturbations') or [])} perturbation(s); "
                 f"v{new_v} has {len(new.get('perturbations') or [])}")
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
    return lines


def render_unchanged(doi: str, new: dict, entry: dict) -> str:
    """The one-line summary for a paper that did NOT move.

    Prints how many of its items paired, which is a task-specific count, so it
    lives here with the rest of the rendering.
    """
    perts = new.get("perturbations") or []
    paired = [p for p in perts if p.get("single_cell_paired") == "yes"]
    supp = "supp" if len(entry.get("source_ids") or []) > 1 else "main"
    return (f"  {doi:34} {str(new.get(PRIMARY_FIELD)):<8} "
            f"{len(paired)}/{len(perts)} paired   [{supp}]")


# The invariant, asserted rather than commented. decide.yaml names the inputs and
# task/rules.py's `stage_a` reads them; if this module's view of that set ever
# drifts, a real movement gets reported as UNEXPLAINED while the input that moved
# is in plain sight.
_probe = determination_inputs({})
if set(_probe) != set(_INPUTS):
    raise PackError(
        f"change.py computes inputs {sorted(_probe)} but decide.yaml declares "
        f"{sorted(_INPUTS)}; the diff and the decision must read the same fields")
