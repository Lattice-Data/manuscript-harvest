"""prompt.md v0.0.5 Stage A / Stage B, checked for totality and against the
prompt's own worked examples.

**The filename still says v005 on purpose.** Stage A, Stage B and the truth table
have not changed since v0.0.5 -- not in v0.0.6-v0.0.9, and not in v0.0.10, which
adds a field that cannot reach them. Renaming this file to the current prompt
version would assert that the determination contract moved when it did not. The
v0.0.10 field has its own file, tests/test_suppressed_candidates.py, whose first
job is to prove this file's subject is unaffected.

Run: python -m pytest tests/test_determination_v005.py -q
"""
from __future__ import annotations

import itertools
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pe.paper_text import split_assembled, verify_quote_sourced  # noqa: E402
from pe.validate import (  # noqa: E402
    consistency_checks, expected_determination, stage_a, stage_b, validate_result,
)

TRI = ("yes", "no", "unclear")


def make(status="ok", completeness="full", has_sc="yes", any_assay="yes", paired=()):
    return {
        "processing_status": status,
        "text_completeness": completeness,
        "has_single_cell_assay": has_sc,
        "perturbation_present_any_assay": any_assay,
        "perturbations": [{"single_cell_paired": p} for p in paired],
    }


# --------------------------------------------------------------------------
# Totality: every combination must resolve. This is the property v0.0.4 added
# and v0.0.5 restated as "no input reaches the end unmatched".
# --------------------------------------------------------------------------

def test_stage_a_is_total():
    pairings = [()] + [tuple(c) for n in (1, 2, 3)
                       for c in itertools.product(TRI, repeat=n)]
    combos = 0
    for status in ("ok", "partial", "failed"):
        for completeness in ("full", "truncated", "methods_missing", "unknown"):
            for has_sc, any_assay in itertools.product(TRI, TRI):
                for paired in pairings:
                    result = make(status, completeness, has_sc, any_assay, paired)
                    assert stage_a(result) in TRI, result
                    assert expected_determination(result) in TRI, result
                    combos += 1
    assert combos == 3 * 4 * 9 * (1 + 3 + 9 + 27)
    print(f"  {combos} combinations, all resolved")


def test_stage_a_returns_none_on_offenum_inputs():
    assert stage_a(make(has_sc="bogus")) is None
    assert stage_a(make(any_assay=None)) is None
    # ...except when processing_status short-circuits at A0.
    assert stage_a(make(status="failed", has_sc="bogus")) == "unclear"


# --------------------------------------------------------------------------
# Stage A, rule by rule
# --------------------------------------------------------------------------

def test_a0_failed_is_unclear_never_no():
    assert stage_a(make(status="failed", any_assay="no", has_sc="no")) == "unclear"


def test_a1_empty_perturbations_has_explicit_terminal_rule():
    assert stage_a(make(any_assay="no", paired=())) == "no"
    assert stage_a(make(any_assay="unclear", paired=())) == "unclear"
    # any_assay="yes" with an empty array is CC-2; stated default is "unclear".
    assert stage_a(make(any_assay="yes", paired=())) == "unclear"
    assert "CC-2" in consistency_checks(make(any_assay="yes", paired=()))


def test_a2_no_assay_is_no():
    assert stage_a(make(has_sc="no", paired=("unclear", "no"))) == "no"


def test_a3_unclear_assay_caps_at_unclear():
    assert stage_a(make(has_sc="unclear", paired=("unclear",))) == "unclear"
    assert stage_a(make(has_sc="unclear", paired=("no", "no"))) == "no"
    # CC-5: a "yes" pairing under an unclear assay is a contradiction, and the
    # stated default is A3 -- which yields "unclear", not "yes".
    assert stage_a(make(has_sc="unclear", paired=("yes",))) == "unclear"
    assert "CC-5" in consistency_checks(make(has_sc="unclear", paired=("yes",)))


def test_a4_one_yes_is_sufficient():
    assert stage_a(make(paired=("no", "no", "yes"))) == "yes"


def test_a5_mixed_no_unclear_is_unclear():
    """The branch v0.0.3 left undefined; recall-biased resolution."""
    assert stage_a(make(paired=("no", "no", "unclear"))) == "unclear"
    assert stage_a(make(paired=("unclear",))) == "unclear"


def test_a6_all_no_is_no():
    assert stage_a(make(paired=("no", "no"))) == "no"


def test_a1_precedes_a2_v005_ordering():
    """v0.0.5 keys A1 on the empty array, not on any_assay='no'.

    Under v0.0.4 `any_assay='no'` returned "no" outright. v0.0.5 reaches A2-A6
    when the array is non-empty, so a CC-3 contradiction no longer silently
    forces a negative.
    """
    result = make(any_assay="no", has_sc="yes", paired=("yes",))
    assert stage_a(result) == "yes"
    assert "CC-3" in consistency_checks(result)


# --------------------------------------------------------------------------
# Stage B
# --------------------------------------------------------------------------

def test_stage_b_caps_only_negatives():
    assert stage_b("no", "partial", "full") == ("unclear", True)
    assert stage_b("no", "ok", "truncated") == ("unclear", True)
    assert stage_b("no", "ok", "methods_missing") == ("unclear", True)
    assert stage_b("no", "ok", "unknown") == ("unclear", True)
    assert stage_b("no", "ok", "full") == ("no", False)
    # Positives and unclears are never capped: missing text can hide evidence
    # but cannot manufacture it.
    assert stage_b("yes", "partial", "truncated") == ("yes", False)
    assert stage_b("unclear", "partial", "truncated") == ("unclear", False)


def test_stage_b_is_the_v004_to_v005_delta():
    """A paper that was "no" under v0.0.4 becomes "unclear" on degraded text."""
    degraded = make(status="ok", completeness="methods_missing", paired=("no",))
    assert stage_a(degraded) == "no"
    assert expected_determination(degraded) == "unclear"


# --------------------------------------------------------------------------
# The prompt's five worked examples (all assume ok/full)
# --------------------------------------------------------------------------

def test_worked_examples():
    # 1. IL-1beta then scRNA-seq -> yes
    assert expected_determination(make(paired=("yes",))) == "yes"
    # 2. doxorubicin bulk RNA-seq, separate untreated snRNA-seq cohort -> no
    ex2 = make(paired=("no",))
    assert expected_determination(ex2) == "no"
    assert ex2["perturbation_present_any_assay"] == "yes"
    # 3. PMA/ionomycin -> flow cytometry -> no
    assert expected_determination(make(paired=("no",))) == "no"
    # 4. Perturb-seq screen -> yes by construction
    assert expected_determination(make(paired=("yes",))) == "yes"
    # 5. neoadjuvant chemo then scRNA-seq of the resected tumour -> yes
    assert expected_determination(make(paired=("yes",))) == "yes"


def test_worked_example_2_on_degraded_text_is_capped():
    """The prompt states this explicitly under the worked examples."""
    assert expected_determination(
        make(status="partial", paired=("no",))) == "unclear"


# --------------------------------------------------------------------------
# Consistency checks
# --------------------------------------------------------------------------

def test_all_cc_codes_fire():
    assert "CC-1" in consistency_checks(make(has_sc="no", paired=("yes",)))
    assert "CC-2" in consistency_checks(make(any_assay="yes", paired=()))
    assert "CC-3" in consistency_checks(make(any_assay="no", paired=("yes",)))
    assert "CC-4" in consistency_checks(make(paired=("maybe",)))
    assert "CC-5" in consistency_checks(make(has_sc="unclear", paired=("yes",)))
    assert "CC-6" in consistency_checks(make(status="failed", paired=("yes",)))


def test_clean_result_has_no_cc_codes():
    assert consistency_checks(make(paired=("yes", "no"))) == []


# --------------------------------------------------------------------------
# Multi-source assembly + source-scoped verification
# --------------------------------------------------------------------------

ASSEMBLED = (
    "<<<SOURCE id=main type=main_text>>>\n"
    "We profiled untreated donor kidney by 10x scRNA-seq.\n"
    "<<<SOURCE id=supp1 type=supplementary>>>\n"
    "Cells were stimulated with 100 ng/ml LPS for 4 h before loading."
)


def test_split_assembled_roundtrip():
    parts = split_assembled(ASSEMBLED)
    assert set(parts) == {"main", "supp1"}
    assert parts["main"].startswith("We profiled")
    assert "LPS" in parts["supp1"]


def test_split_assembled_without_markers_is_single_main():
    assert split_assembled("plain text") == {"main": "plain text"}


def test_quote_verified_against_claimed_source():
    parts = split_assembled(ASSEMBLED)
    out = verify_quote_sourced("100 ng/ml LPS for 4 h", "supp1", parts)
    assert out["status"] == "verified"


def test_quote_attributed_to_wrong_source_is_not_a_pass():
    parts = split_assembled(ASSEMBLED)
    out = verify_quote_sourced("100 ng/ml LPS for 4 h", "main", parts)
    assert out["status"] == "wrong_source"
    assert out["source_id"] == "supp1"


def test_quote_citing_unknown_source_is_cc7():
    parts = split_assembled(ASSEMBLED)
    out = verify_quote_sourced("100 ng/ml LPS for 4 h", "supp9", parts)
    assert out["status"] == "unknown_source"


def test_hallucinated_quote_is_unverified():
    parts = split_assembled(ASSEMBLED)
    out = verify_quote_sourced("mice received doxorubicin intraperitoneally",
                               "main", parts)
    assert out["status"] == "unverified"


# --------------------------------------------------------------------------
# End-to-end: pruning must be able to change the determination
# --------------------------------------------------------------------------

def _v005_result(**over):
    """A current-schema record carrying v0.0.5 determination inputs.

    The determination fields are the ones this file exercises; the envelope
    tracks the live schema (0.0.6) so these end-to-end cases keep validating a
    record shape the pipeline actually accepts, rather than drifting into
    testing a version `pe.validate` now rejects.
    """
    base = {
        "schema_version": "0.0.6",
        "sources_seen": ["main", "supp1"],
        "processing_status": "ok",
        "text_completeness": "full",
        "has_single_cell_assay": "yes",
        "single_cell_assay_types": ["10x scRNA-seq"],
        "perturbation_present": "yes",
        "perturbation_present_any_assay": "yes",
        "paper_confidence": 0.9,
        "unresolved_reason": "none",
        "consistency_flags": [],
        "perturbations": [],
        "samples": [],
        "suppressed_candidates": [],
        "ambiguities": "",
    }
    base.update(over)
    return base


def test_fabricated_quote_drops_perturbation_and_flips_determination():
    """batch spec step 6: a determination resting on a hallucinated quote must
    not survive the removal of that quote."""
    result = _v005_result(perturbations=[{
        "category": "chemical",
        "agent": "doxorubicin",
        "target": "heart",
        "modality_detail": "",
        "samples_affected": [],
        "evidence_quotes": [{"source_id": "main",
                             "quote": "mice received doxorubicin intraperitoneally"}],
        "assay_applied": "snRNA-seq",
        "single_cell_paired": "yes",
        "assay_evidence": None,
        "confidence": 0.9,
        "reasoning": "",
    }])
    out = validate_result(result, split_assembled(ASSEMBLED), 0.85, "0.0.5")
    v = out["validation"]
    assert v["perturbations_dropped"] == 1
    assert out["perturbations"] == []
    assert "EV-PERT-DROPPED" in v["evidence_flags"]
    assert "EV-UNVERIFIED" in v["evidence_flags"]
    assert out["perturbation_present_model"] == "yes"
    # Empty array + any_assay="yes" -> A1's CC-2 default of "unclear".
    assert out["perturbation_present_final"] == "unclear"
    assert v["determination_changed_by_harness"] is True


def test_verified_supplementary_quote_survives_and_pairs():
    result = _v005_result(perturbations=[{
        "category": "activation_stimulation",
        "agent": "LPS 100 ng/ml",
        "target": "cells",
        "modality_detail": "4 h",
        "samples_affected": ["LPS"],
        "evidence_quotes": [{"source_id": "supp1",
                             "quote": "stimulated with 100 ng/ml LPS for 4 h"}],
        "assay_applied": "10x scRNA-seq",
        "single_cell_paired": "yes",
        "assay_evidence": {"source_id": "supp1",
                           "quote": "for 4 h before loading"},
        "confidence": 0.85,
        "reasoning": "",
    }])
    out = validate_result(result, split_assembled(ASSEMBLED), 0.85, "0.0.5")
    v = out["validation"]
    assert v["perturbations_dropped"] == 0
    assert v["quotes_failed"] == 0
    assert out["perturbation_present_final"] == "yes"
    assert v["evidence_flags"] == []


def test_unverifiable_assay_evidence_downgrades_the_pairing():
    result = _v005_result(perturbations=[{
        "category": "activation_stimulation",
        "agent": "LPS",
        "target": "cells",
        "modality_detail": "",
        "samples_affected": [],
        "evidence_quotes": [{"source_id": "supp1", "quote": "stimulated with 100 ng/ml LPS"}],
        "assay_applied": "10x scRNA-seq",
        "single_cell_paired": "yes",
        "assay_evidence": {"source_id": "main",
                           "quote": "the LPS-treated cells underwent CITE-seq"},
        "confidence": 0.85,
        "reasoning": "",
    }])
    out = validate_result(result, split_assembled(ASSEMBLED), 0.85, "0.0.5")
    v = out["validation"]
    assert "EV-PAIRING-DOWNGRADED" in v["evidence_flags"]
    assert out["perturbations"][0]["single_cell_paired"] == "unclear"
    assert out["perturbation_present_final"] == "unclear"


def test_dropped_perturbation_refs_are_remapped_not_dangling():
    result = _v005_result(
        perturbations=[
            {"category": "chemical", "agent": "ghost", "target": "", "modality_detail": "",
             "samples_affected": [], "evidence_quotes": [
                 {"source_id": "main", "quote": "no such sentence anywhere at all"}],
             "assay_applied": "", "single_cell_paired": "unclear", "assay_evidence": None,
             "confidence": 0.5, "reasoning": ""},
            {"category": "activation_stimulation", "agent": "LPS", "target": "", "modality_detail": "",
             "samples_affected": [], "evidence_quotes": [
                 {"source_id": "supp1", "quote": "stimulated with 100 ng/ml LPS"}],
             "assay_applied": "", "single_cell_paired": "yes", "assay_evidence": None,
             "confidence": 0.9, "reasoning": ""},
        ],
        samples=[{"label": "LPS", "perturbed": True, "perturbation_refs": [0, 1],
                  "assay": "10x scRNA-seq", "is_single_cell_assay": "yes"}])
    out = validate_result(result, split_assembled(ASSEMBLED), 0.85, "0.0.5")
    # index 1 survives and becomes index 0; index 0 was dropped.
    assert out["samples"][0]["perturbation_refs"] == [0]
    assert out["samples"][0]["perturbation_refs_original"] == [0, 1]


def test_samples_perturbed_unclear_is_no_longer_an_issue():
    """The v0.0.5 curator ruling: "unclear" is schema-legal."""
    result = _v005_result(
        perturbations=[{"category": "activation_stimulation", "agent": "LPS", "target": "",
                        "modality_detail": "", "samples_affected": [], "evidence_quotes": [
                            {"source_id": "supp1", "quote": "stimulated with 100 ng/ml LPS"}],
                        "assay_applied": "", "single_cell_paired": "yes",
                        "assay_evidence": None, "confidence": 0.9, "reasoning": ""}],
        samples=[{"label": "arm B", "perturbed": "unclear", "perturbation_refs": [],
                  "assay": "", "is_single_cell_assay": "unclear"}])
    out = validate_result(result, split_assembled(ASSEMBLED), 0.85, "0.0.5")
    assert not any("perturbed=" in i for i in out["validation"]["issues"])


def test_harness_truncation_overrides_a_model_claiming_full():
    result = _v005_result(perturbations=[], perturbation_present_any_assay="no",
                          perturbation_present="no")
    out = validate_result(result, split_assembled(ASSEMBLED), 0.85, "0.0.5",
                          truncated_by_harness=True)
    assert out["text_completeness"] == "truncated"
    # ...and Stage B then caps the negative.
    assert out["perturbation_present_final"] == "unclear"
    assert out["unresolved_reason"] == "degraded_text"
