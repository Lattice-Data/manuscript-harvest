"""prompt.md v0.0.10 / schema 0.0.6: `suppressed_candidates`.

The field's whole justification is that it makes the NOT list auditable and
countable WITHOUT changing what counts as a perturbation. So the first and most
important property here is a negative one: recording a suppressed candidate must
never move `perturbation_present`. Everything else -- quote verification, the
closed `rule` set, the counters, the new triage tier -- is in service of that.

A sibling to tests/test_determination_v005.py rather than a rename of it: the
determination contract those tests pin did not move, and saying it did would be
the more misleading of the two.

Run: python -m pytest tests/test_suppressed_candidates.py -q
"""
from __future__ import annotations

import itertools
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pe.compare import _suppression_matches, classify  # noqa: E402
from pe.paper_text import split_assembled  # noqa: E402
from pe.summarize import triage_priority  # noqa: E402
from pe.validate import (  # noqa: E402
    SUPPRESSION_RULES, expected_determination, stage_a, validate_result,
)

ROOT = Path(__file__).resolve().parent.parent
TRI = ("yes", "no", "unclear")

ASSEMBLED = (
    "<<<SOURCE id=main type=main_text>>>\n"
    "Four organoid lines were profiled by 10x scRNA-seq to characterize the model. "
    "Elsewhere we generated SFTPC-GFP reporter lines and performed CRISPRi knockdowns "
    "read out by qPCR.\n"
    "<<<SOURCE id=supp1 type=supplementary>>>\n"
    "Reporter lines carried a lentiviral SFTPC-promoter-GFP construct."
)


def _candidate(**over):
    base = {
        "candidate": "lentiviral SFTPC-promoter-GFP reporter",
        "rule": "reporter_or_marker",
        "why": "promoter reporter read out as a proxy for SFTPC expression",
        "evidence_quote": {"source_id": "supp1",
                           "quote": "Reporter lines carried a lentiviral "
                                    "SFTPC-promoter-GFP construct."},
        "would_have_paired": "unclear",
    }
    base.update(over)
    return base


def _record(**over):
    base = {
        "schema_version": "0.0.6",
        "paper_id": "10.1038_s44318-024-00328-6",
        "sources_seen": ["main", "supp1"],
        "processing_status": "ok",
        "text_completeness": "full",
        "has_single_cell_assay": "yes",
        "single_cell_assay_types": ["10x scRNA-seq"],
        "perturbation_present": "no",
        "perturbation_present_any_assay": "no",
        "paper_confidence": 0.85,
        "unresolved_reason": "none",
        "consistency_flags": [],
        "perturbations": [],
        "samples": [],
        "suppressed_candidates": [],
        "ambiguities": "",
    }
    base.update(over)
    return base


def _validate(record):
    return validate_result(record, split_assembled(ASSEMBLED), 0.85, "0.0.10")


# --------------------------------------------------------------------------
# THE acceptance property: visibility, not judgment.
# --------------------------------------------------------------------------

def test_suppressed_candidate_does_not_move_the_determination():
    """The paper is 'no' with an empty perturbations array; a suppressed
    candidate that WOULD have paired 'yes' must leave it 'no'. Under v0.0.8 this
    same candidate, reported as a perturbation with an unresolved pairing, drove
    the paper to 'unclear' under A5 -- that is the behaviour v0.0.9 removed and
    v0.0.10 must not reintroduce by the back door."""
    bare = _validate(_record())
    withsupp = _validate(_record(suppressed_candidates=[
        _candidate(would_have_paired="yes")]))

    assert bare["perturbation_present"] == "no"
    assert withsupp["perturbation_present"] == "no"
    assert withsupp["perturbation_present_final"] == bare["perturbation_present_final"]
    assert withsupp["validation"]["stage_a"] == bare["validation"]["stage_a"]
    assert withsupp["validation"]["stage_b_capped"] is bare["validation"]["stage_b_capped"]
    # And it never leaked into the array Stage A reads.
    assert withsupp["perturbations"] == []


def test_suppressed_candidates_are_invisible_to_stage_a_for_every_input():
    """Property form: over every Stage A input combination, attaching suppressed
    candidates changes neither Stage A nor the final determination."""
    pairings = [()] + [tuple(c) for n in (1, 2) for c in itertools.product(TRI, repeat=n)]
    supp = [_candidate(would_have_paired=w, rule=r)
            for w, r in zip(TRI, SUPPRESSION_RULES)]
    checked = 0
    for status in ("ok", "partial", "failed"):
        for completeness in ("full", "truncated", "methods_missing", "unknown"):
            for has_sc, any_assay in itertools.product(TRI, TRI):
                for paired in pairings:
                    shared = {
                        "processing_status": status,
                        "text_completeness": completeness,
                        "has_single_cell_assay": has_sc,
                        "perturbation_present_any_assay": any_assay,
                        "perturbations": [{"single_cell_paired": p} for p in paired],
                    }
                    without = dict(shared, suppressed_candidates=[])
                    with_ = dict(shared, suppressed_candidates=supp)
                    assert stage_a(without) == stage_a(with_)
                    assert expected_determination(without) == expected_determination(with_)
                    checked += 1
    assert checked == 3 * 4 * 9 * (1 + 3 + 9)


def test_a5_is_not_reachable_through_suppression():
    """A5 turns one 'unclear' pairing into an 'unclear' paper. A suppressed
    candidate whose `would_have_paired` is 'unclear' must not trip it."""
    out = _validate(_record(
        perturbation_present_any_assay="yes",
        perturbations=[{
            "category": "genetic", "agent": "CRISPRi knockdown of ITCH",
            "target": "ITCH", "modality_detail": "", "samples_affected": [],
            "evidence_quotes": [{"source_id": "main",
                                 "quote": "performed CRISPRi knockdowns read out by qPCR"}],
            "assay_applied": "qPCR", "single_cell_paired": "no",
            "assay_evidence": None, "confidence": 0.85, "reasoning": "",
        }],
        suppressed_candidates=[_candidate(would_have_paired="unclear")]))
    # A6: every pairing resolved to "no".
    assert out["perturbation_present"] == "no"
    assert out["validation"]["paired_unclear"] == 0


# --------------------------------------------------------------------------
# Quote verification: the same rules as everything else, one exception.
# --------------------------------------------------------------------------

def test_verified_quote_is_kept():
    out = _validate(_record(suppressed_candidates=[_candidate()]))
    cand = out["suppressed_candidates"][0]
    assert cand["quote_check"]["status"] == "verified"
    assert cand["evidence_quote"]["source_id"] == "supp1"
    assert out["validation"]["suppressed_quotes_checked"] == 1
    assert out["validation"]["suppressed_quotes_failed"] == 0
    assert "EV-SUPPRESSED-UNVERIFIED" not in out["validation"]["evidence_flags"]


def test_unverifiable_quote_drops_the_quote_and_keeps_the_entry():
    """prompt.md v0.0.10 step 6. Dropping the entry would restore exactly the
    silence the field exists to remove."""
    out = _validate(_record(suppressed_candidates=[_candidate(
        evidence_quote={"source_id": "main",
                        "quote": "cells were exposed to 1 uM retinoic acid for 48 h"})]))
    assert len(out["suppressed_candidates"]) == 1, "the entry must survive"
    cand = out["suppressed_candidates"][0]
    assert cand["evidence_quote"] is None
    assert cand["evidence_quote_dropped"]["quote"].startswith("cells were exposed")
    assert "EV-SUPPRESSED-UNVERIFIED" in out["validation"]["evidence_flags"]
    assert out["validation"]["suppressed_quotes_failed"] == 1
    assert out["validation"]["n_suppressed"] == 1
    # ...and it still did not touch the determination.
    assert out["perturbation_present"] == "no"


def test_misattributed_quote_is_corrected_not_accepted():
    out = _validate(_record(suppressed_candidates=[_candidate(
        evidence_quote={"source_id": "main",
                        "quote": "Reporter lines carried a lentiviral "
                                 "SFTPC-promoter-GFP construct."})]))
    cand = out["suppressed_candidates"][0]
    assert cand["evidence_quote"]["source_id"] == "supp1"
    assert cand["evidence_quote"]["source_id_corrected_from"] == "main"
    assert "EV-WRONG-SOURCE" in out["validation"]["evidence_flags"]


def test_null_quote_is_legitimate_when_the_exclusion_rests_on_silence():
    """The s44318 reporter case: the Methods never place the construct in the
    sequenced material, so there is nothing to quote."""
    out = _validate(_record(suppressed_candidates=[_candidate(
        evidence_quote=None,
        why="the scRNA-seq Methods never state the four sequenced lines carried it")]))
    cand = out["suppressed_candidates"][0]
    assert cand["evidence_quote"] is None
    assert "evidence_quote_dropped" not in cand
    assert out["validation"]["suppressed_quotes_checked"] == 0
    assert "EV-SUPPRESSED-UNVERIFIED" not in out["validation"]["evidence_flags"]


# --------------------------------------------------------------------------
# Schema enforcement: the failure v0.0.9 had no defence against.
# --------------------------------------------------------------------------

def test_missing_field_is_an_issue_and_normalizes_to_empty_list():
    record = _record()
    del record["suppressed_candidates"]
    out = _validate(record)
    assert out["suppressed_candidates"] == []
    assert any("suppressed_candidates missing" in i for i in out["validation"]["issues"])


def test_null_is_an_issue_because_it_cannot_be_told_from_never_considered():
    out = _validate(_record(suppressed_candidates=None))
    assert out["suppressed_candidates"] == []
    assert any("suppressed_candidates missing" in i for i in out["validation"]["issues"])


def test_rule_outside_the_closed_set_is_an_issue():
    out = _validate(_record(suppressed_candidates=[_candidate(rule="reporter")]))
    assert any("outside the closed set" in i for i in out["validation"]["issues"])
    # An off-enum rule is not tallied -- it would corrupt the corpus counter.
    assert out["validation"]["suppressed_rules"] == []
    assert out["validation"]["n_suppressed"] == 1


def test_offenum_would_have_paired_is_an_issue():
    out = _validate(_record(suppressed_candidates=[_candidate(would_have_paired="maybe")]))
    assert any("would_have_paired='maybe'" in i for i in out["validation"]["issues"])


def test_empty_candidate_name_is_an_issue():
    out = _validate(_record(suppressed_candidates=[_candidate(candidate="  ")]))
    assert any("candidate is empty" in i for i in out["validation"]["issues"])


def test_stale_schema_version_is_rejected():
    out = _validate(_record(schema_version="0.0.5"))
    assert any("expected '0.0.6'" in i for i in out["validation"]["issues"])


def test_counters_tally_rules_and_flag_would_pair_yes():
    out = _validate(_record(suppressed_candidates=[
        _candidate(rule="reporter_or_marker", would_have_paired="no"),
        _candidate(rule="unintended_condition", candidate="cultures likely became hypoxic",
                   evidence_quote=None, would_have_paired="yes"),
        _candidate(rule="reporter_or_marker", candidate="EF1a-TagRFP",
                   evidence_quote=None, would_have_paired="unclear"),
    ]))
    v = out["validation"]
    assert v["n_suppressed"] == 3
    assert v["suppressed_rules"] == ["reporter_or_marker", "unintended_condition"]
    assert v["suppressed_would_pair_yes"] is True


def test_all_yes_would_have_paired_is_flagged_as_undiscriminating():
    """Observed on the first v0.0.10 run: giving the model eight named buckets
    made suppression the salient action, and it pulled genuine perturbations
    across the line on 10.1038/s41586-024-07571-1 and 10.7554/elife.104978.2 --
    both moved yes -> no, both via incidental_clinical_therapy, and in both every
    suppressed candidate was marked would_have_paired='yes'. The prompt carries
    the fix; this is the mechanical tripwire for the same pattern recurring."""
    out = _validate(_record(suppressed_candidates=[
        _candidate(would_have_paired="yes", evidence_quote=None),
        _candidate(would_have_paired="yes", candidate="HypoThermosol 4C hold",
                   rule="sample_handling_protocol", evidence_quote=None),
    ]))
    assert any("stopped discriminating" in i for i in out["validation"]["issues"])
    # An issue, not a determination change: judgment stays in the prompt.
    assert out["perturbation_present"] == "no"


def test_a_mixed_set_is_not_flagged():
    out = _validate(_record(suppressed_candidates=[
        _candidate(would_have_paired="yes", evidence_quote=None),
        _candidate(would_have_paired="unclear", evidence_quote=None),
    ]))
    assert not any("stopped discriminating" in i for i in out["validation"]["issues"])


def test_a_single_yes_is_not_flagged():
    """One entry carries no signal about whether the column discriminates."""
    out = _validate(_record(suppressed_candidates=[
        _candidate(would_have_paired="yes", evidence_quote=None)]))
    assert not any("stopped discriminating" in i for i in out["validation"]["issues"])


# --------------------------------------------------------------------------
# The closed set must not drift between prompt.md and the harness.
# --------------------------------------------------------------------------

def test_prompt_and_code_agree_on_the_closed_rule_set():
    """prompt.md is the source of truth; `pe.validate` mirrors it. v0.0.7's
    precedence bug was one rule stated in three places and changed in two."""
    prompt = (ROOT / "prompt.md").read_text()
    schema_line = next(line for line in prompt.splitlines()
                       if '"rule":' in line and "reporter_or_marker" in line)
    declared = {tok.strip().strip('",')
                for tok in schema_line.split('"rule":')[1].split("|")}
    declared = {d for d in declared if d.replace("_", "").isalpha()}
    assert declared == set(SUPPRESSION_RULES), (
        f"prompt.md declares {sorted(declared)}, pe.validate has "
        f"{sorted(SUPPRESSION_RULES)}")


# --------------------------------------------------------------------------
# Triage: the new tier, and that it stays out of the way when it should.
# --------------------------------------------------------------------------

def _scored(**over):
    return _validate(_record(**over))


def test_would_pair_yes_on_a_non_yes_paper_is_priority_2():
    out = _scored(suppressed_candidates=[_candidate(would_have_paired="yes")])
    assert out["perturbation_present"] == "no"
    assert triage_priority(out) == 2


def test_would_pair_yes_on_an_already_yes_paper_is_not_priority_2():
    """Nothing to flip: the paper is already where the toggle would put it."""
    out = _scored(
        perturbation_present="yes", perturbation_present_any_assay="yes",
        paper_confidence=0.9,
        perturbations=[{
            "category": "genetic", "agent": "CRISPRi knockdown of ITCH",
            "target": "ITCH", "modality_detail": "", "samples_affected": [],
            "evidence_quotes": [{"source_id": "main",
                                 "quote": "performed CRISPRi knockdowns read out by qPCR"}],
            "assay_applied": "10x scRNA-seq", "single_cell_paired": "yes",
            "assay_evidence": None, "confidence": 0.9, "reasoning": "",
        }],
        suppressed_candidates=[_candidate(would_have_paired="yes")])
    assert out["perturbation_present"] == "yes"
    assert triage_priority(out) != 2


def test_a_settled_toggle_alone_does_not_reach_priority_2():
    """`observational_disease_state` pairs "yes" on any disease-vs-healthy
    contrast, i.e. most clinical papers. Unrestricted, this tier held 5 of the 6
    regression papers, which is not a queue."""
    out = _scored(suppressed_candidates=[_candidate(
        candidate="ulcerative colitis vs healthy donor tissue",
        rule="observational_disease_state", evidence_quote=None,
        would_have_paired="yes")])
    v = out["validation"]
    assert v["suppressed_would_pair_yes"] is True, "the raw fact is still reported"
    assert v["suppressed_would_pair_yes_under_review"] is False
    assert triage_priority(out) != 2


def test_a_rule_under_review_does_reach_priority_2_alongside_a_settled_one():
    out = _scored(suppressed_candidates=[
        _candidate(candidate="ulcerative colitis vs healthy donor tissue",
                   rule="observational_disease_state", evidence_quote=None,
                   would_have_paired="yes"),
        _candidate(candidate="cultures likely became hypoxic",
                   rule="unintended_condition", evidence_quote=None,
                   would_have_paired="yes"),
    ])
    assert out["validation"]["suppressed_would_pair_yes_under_review"] is True
    assert triage_priority(out) == 2


def test_suppression_without_would_pair_yes_is_not_priority_2():
    out = _scored(suppressed_candidates=[_candidate(would_have_paired="unclear")])
    assert triage_priority(out) != 2


def test_renumbered_ladder_matches_prompt_step_10():
    """The old 2-5 shifted to 3-6. If these drift from prompt.md step 10 the
    curator's queue silently mis-sorts."""
    low_conf_yes = _scored(
        perturbation_present="yes", perturbation_present_any_assay="yes",
        paper_confidence=0.3,
        perturbations=[{
            "category": "genetic", "agent": "CRISPRi knockdown of ITCH",
            "target": "ITCH", "modality_detail": "", "samples_affected": [],
            "evidence_quotes": [{"source_id": "main",
                                 "quote": "performed CRISPRi knockdowns read out by qPCR"}],
            "assay_applied": "10x scRNA-seq", "single_cell_paired": "yes",
            "assay_evidence": None, "confidence": 0.3, "reasoning": "",
        }])
    assert triage_priority(low_conf_yes) == 3

    degraded = _scored(processing_status="partial", text_completeness="truncated",
                       unresolved_reason="degraded_text")
    assert degraded["perturbation_present"] == "unclear"
    assert triage_priority(degraded) == 4

    filtered = _scored(
        perturbation_present_any_assay="yes",
        perturbations=[{
            "category": "genetic", "agent": "CRISPRi knockdown of ITCH",
            "target": "ITCH", "modality_detail": "", "samples_affected": [],
            "evidence_quotes": [{"source_id": "main",
                                 "quote": "performed CRISPRi knockdowns read out by qPCR"}],
            "assay_applied": "qPCR", "single_cell_paired": "no",
            "assay_evidence": None, "confidence": 0.85, "reasoning": "",
        }])
    assert filtered["perturbation_present"] == "no"
    assert triage_priority(filtered) == 5

    # Priority 1 is unchanged and still outranks the new tier.
    top = _scored(perturbation_present="unclear", unresolved_reason="pairing_not_stated",
                  perturbation_present_any_assay="yes",
                  perturbations=[{
                      "category": "genetic", "agent": "CRISPRi knockdown of ITCH",
                      "target": "ITCH", "modality_detail": "", "samples_affected": [],
                      "evidence_quotes": [
                          {"source_id": "main",
                           "quote": "performed CRISPRi knockdowns read out by qPCR"}],
                      "assay_applied": "", "single_cell_paired": "unclear",
                      "assay_evidence": None, "confidence": 0.5, "reasoning": "",
                  }],
                  suppressed_candidates=[_candidate(would_have_paired="yes")])
    assert triage_priority(top) == 1


# --------------------------------------------------------------------------
# pe.compare: the SUPPRESSED class, and Stage B's missing direction.
# --------------------------------------------------------------------------

def _run(present, perts=(), supp=(), capped=False):
    return {
        "perturbation_present": present,
        "processing_status": "ok",
        "text_completeness": "full",
        "has_single_cell_assay": "yes",
        "perturbation_present_any_assay": "yes",
        "perturbations": [{"agent": a, "single_cell_paired": p} for a, p in perts],
        "suppressed_candidates": list(supp),
        "validation": {"stage_b_capped": capped, "consistency_flags": [],
                       "evidence_flags": []},
    }


def test_suppressed_class_fires_when_a_baseline_perturbation_became_a_candidate():
    old = _run("unclear", perts=[("SFTPC-GFP reporter line", "unclear")])
    new = _run("no", supp=[_candidate(
        candidate="lentiviral SFTPC-promoter-GFP reporter")])
    assert "SUPPRESSED" in classify(new, old)
    assert "UNEXPLAINED" not in classify(new, old)
    matches = _suppression_matches(new, old)
    assert matches and matches[0][0] == "reporter_or_marker"


def test_matcher_survives_the_two_real_pairs_it_had_to_learn():
    """Both observed on the first v0.0.10 run against the v0.0.9 baseline.

    s41586 matched immediately. elife did not: the tokenizer kept
    "chemotherapy-driven" whole, so it shared nothing with the baseline's
    "chemotherapy (specific agent not named)", and even after splitting hyphens
    the single shared word fell under a two-token threshold. Hence hyphen
    splitting plus the single-distinctive-token rule."""
    pairs = [
        ("gluten-free diet",
         "treated coeliac disease patients maintained on a gluten-free diet"),
        ("chemotherapy (specific agent not named)",
         "chemotherapy-driven lineage switch in the relapse sample"),
    ]
    for agent, candidate in pairs:
        old = _run("yes", perts=[(agent, "yes")])
        new = _run("no", supp=[_candidate(candidate=candidate,
                                         rule="incidental_clinical_therapy")])
        assert _suppression_matches(new, old), f"missed: {agent!r} -> {candidate!r}"
        assert "SUPPRESSED" in classify(new, old)


def test_a_perturbation_that_survived_under_a_reworded_agent_is_not_suppressed():
    """The still-reported guard and the candidate match use the same test, so a
    perturbation still in `perturbations` cannot also count as suppressed."""
    old = _run("yes", perts=[("recombinant human IL-22 (20 ng/ml) vs mock", "yes")])
    new = _run("yes", perts=[("recombinant human IL-22, 20 ng/ml", "yes")],
               supp=[_candidate(candidate="IL-22 detection antibody",
                                rule="readout_reagent")])
    assert _suppression_matches(new, old) == []


def test_generic_qualifiers_alone_do_not_match():
    """"specific" is eight characters and would otherwise satisfy the
    single-distinctive-token rule while carrying no identity at all."""
    old = _run("yes", perts=[("a specific unnamed agent", "yes")])
    new = _run("no", supp=[_candidate(candidate="a specific unnamed condition",
                                     rule="unintended_condition")])
    assert _suppression_matches(new, old) == []


def test_unmatched_suppression_does_not_excuse_an_unexplained_change():
    """SUPPRESSED is an account, not an alibi: a suppression unrelated to what
    the baseline reported explains nothing."""
    old = _run("yes", perts=[("gluten-free diet", "yes")])
    new = _run("no", supp=[_candidate(candidate="puromycin selection marker",
                                      rule="reporter_or_marker")])
    assert _suppression_matches(new, old) == []
    assert "SUPPRESSED" not in classify(new, old)


def test_stage_b_cap_release_is_classified_not_unexplained():
    """A cap release -- what a completed re-extraction looks like -- previously
    fell through to UNEXPLAINED, whose message says to investigate a logic bug.
    Observed on 10.1126/science.adf5357."""
    old = _run("unclear", capped=True)
    new = _run("no", capped=False)
    classes = classify(new, old)
    assert "STAGE-B-RELEASED" in classes
    assert "UNEXPLAINED" not in classes


def test_stage_b_cap_entry_is_still_classified():
    old = _run("no", capped=False)
    new = _run("unclear", capped=True)
    classes = classify(new, old)
    assert "STAGE-B" in classes
    assert "STAGE-B-RELEASED" not in classes


def test_a_genuinely_unexplained_change_is_still_unexplained():
    """The guard on the two fixes above: neither may swallow a real logic bug."""
    old = _run("no")
    new = _run("yes")
    assert classify(new, old) == ["UNEXPLAINED"]
