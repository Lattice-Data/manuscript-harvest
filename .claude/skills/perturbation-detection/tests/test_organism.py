"""prompt.md v0.0.12 / schema 0.0.7: `organism` and `paired_organism`.

The field exists so a curator can see WHOSE sample was perturbed. Its whole
justification is that it does this without deciding anything, so the first and
most important property here is negative: recording an organism must never move
`perturbation_present`. The curator's instruction was explicit -- add the column
"but not call on it -- leaving it to the human interpreter" -- because the corpus
is human-primarily but not human-only, and a paper may be mouse, zebrafish or
killifish.

A sibling to test_determination_v005.py and test_suppressed_candidates.py rather
than a rename of either: the determination contract has still not moved.

Run: python -m pytest tests/test_organism.py -q
"""
from __future__ import annotations

import itertools
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pe.paper_text import split_assembled  # noqa: E402
from pe.summarize import triage_priority  # noqa: E402
from pe.validate import (  # noqa: E402
    is_human, normalise_organism, stage_a, validate_result,
)

TRI = ("yes", "no", "unclear")

ASSEMBLED = (
    "<<<SOURCE id=main type=main_text>>>\n"
    "Mice were subjected to chronic left anterior descending artery ligation and "
    "profiled by scRNA-seq. Human donor hearts were profiled by snRNA-seq.\n"
)


def _pert(**over):
    base = {
        "category": "physical_environmental",
        "agent": "LAD coronary artery ligation",
        "single_cell_paired": "yes",
        "paired_organism": "mouse",
        "evidence_quotes": [{"source_id": "main",
                             "quote": "Mice were subjected to chronic left anterior "
                                      "descending artery ligation and profiled by "
                                      "scRNA-seq."}],
        "assay_evidence": None,
        "confidence": 0.85,
        "reasoning": "surgical model applied and profiled",
    }
    base.update(over)
    return base


def _record(**over):
    base = {
        "schema_version": "0.0.7",
        "paper_id": "10.1038_s41586-022-05060-x",
        "sources_seen": ["main"],
        "processing_status": "ok",
        "text_completeness": "full",
        "has_single_cell_assay": "yes",
        "single_cell_assay_types": ["10x scRNA-seq"],
        "perturbation_present": "yes",
        "perturbation_present_any_assay": "yes",
        "paper_confidence": 0.8,
        "unresolved_reason": "none",
        "consistency_flags": [],
        "perturbations": [_pert()],
        "samples": [],
        "suppressed_candidates": [],
        "ambiguities": "",
    }
    base.update(over)
    return base


def _validate(record):
    return validate_result(record, split_assembled(ASSEMBLED), 0.85, "0.0.12")


# --------------------------------------------------------------------------
# THE acceptance property: description, not judgment.
# --------------------------------------------------------------------------

def test_organism_does_not_move_the_determination():
    """A mouse perturbation paired to mouse scRNA-seq is still `yes`. This is the
    curator's explicit instruction and the reason the field is safe: hard-coding
    human would silently drop a legitimately non-human curation target, the worst
    direction for a recall-biased task."""
    mouse = _validate(_record())
    human = _validate(_record(perturbations=[_pert(paired_organism="human")]))
    absent = _validate(_record(perturbations=[_pert(paired_organism=None)]))

    for got in (mouse, human, absent):
        assert got["perturbation_present"] == "yes"
        assert got["validation"]["stage_a"] == mouse["validation"]["stage_a"]
        assert len(got["perturbations"]) == 1


def test_organism_is_invisible_to_stage_a_for_every_input():
    """Property form: over every Stage A input combination, changing only the
    organism changes neither Stage A nor the final determination."""
    pairings = [()] + [tuple(c) for n in (1, 2) for c in itertools.product(TRI, repeat=n)]
    organisms = (None, "human", "mouse", "killifish", "Zebrafish  ")
    checked = 0
    for status in ("ok", "partial", "failed"):
        for completeness in ("full", "truncated", "methods_missing", "unknown"):
            for has_sc, any_assay in itertools.product(TRI, TRI):
                for paired in pairings:
                    baseline = None
                    for org in organisms:
                        rec = _record(
                            processing_status=status,
                            text_completeness=completeness,
                            has_single_cell_assay=has_sc,
                            perturbation_present_any_assay=any_assay,
                            perturbations=[_pert(single_cell_paired=p,
                                                 paired_organism=org)
                                           for p in paired],
                        )
                        got = stage_a(rec)
                        if baseline is None:
                            baseline = got
                        assert got == baseline, (
                            f"organism {org!r} moved Stage A for "
                            f"{status}/{completeness}/{has_sc}/{any_assay}/{paired}")
                        checked += 1
    assert checked > 2000, checked


# --------------------------------------------------------------------------
# The value set is OPEN, and `null` is never guessed.
# --------------------------------------------------------------------------

def test_unusual_species_is_accepted_not_coerced():
    """A closed set would have to enumerate every model organism in advance, and
    killifish is exactly the case that breaks such a list."""
    got = _validate(_record(perturbations=[_pert(paired_organism="killifish")]))
    assert got["validation"]["paired_organisms"] == ["killifish"]
    assert not [i for i in got["validation"]["issues"] if "organism" in i]


def test_null_organism_is_legal_and_reads_as_unknown_not_as_non_human():
    """An organism nobody stated must not read as a confident 'not human' --
    that would turn silence into a scope exclusion."""
    got = _validate(_record(perturbations=[_pert(paired_organism=None)]))
    assert got["validation"]["paired_organisms"] == []
    assert got["validation"]["paired_organism_human"] is None
    assert got["validation"]["n_paired_yes_human"] == 0
    assert triage_priority(got) != 7


def test_non_string_organism_is_an_issue():
    got = _validate(_record(perturbations=[_pert(paired_organism=["mouse"])]))
    assert any("paired_organism" in i for i in got["validation"]["issues"])


def test_sample_organism_type_is_checked_but_value_is_not():
    ok = _validate(_record(samples=[{"label": "AKI", "organism": "killifish",
                                     "perturbed": False, "is_single_cell_assay": "yes",
                                     "perturbation_refs": []}]))
    assert not [i for i in ok["validation"]["issues"] if "organism" in i]
    bad = _validate(_record(samples=[{"label": "AKI", "organism": 9606,
                                      "perturbed": False, "is_single_cell_assay": "yes",
                                      "perturbation_refs": []}]))
    assert any("organism" in i for i in bad["validation"]["issues"])


# --------------------------------------------------------------------------
# Mixed-species papers: the specific failure the field exists to prevent.
# --------------------------------------------------------------------------

def test_mixed_species_paper_reports_both_and_stays_human():
    """s41588-025-02158-6's shape: human therapy AND a mouse transgene, both
    paired. Collapsing to whichever was noticed first is the failure mode."""
    got = _validate(_record(perturbations=[
        _pert(agent="induction chemotherapy", paired_organism="human"),
        _pert(agent="TH-MYCN transgene", paired_organism="mouse"),
    ]))
    assert got["validation"]["paired_organisms"] == ["human", "mouse"]
    assert got["validation"]["paired_organism_human"] is True
    assert got["validation"]["n_paired_yes_human"] == 1
    assert triage_priority(got) != 7, "a paper with a human pairing is not a scope call"


def test_unpaired_human_does_not_rescue_an_animal_only_yes():
    """science.aay3224's shape: the only `yes` pairing is the mouse one. A human
    perturbation that is NOT paired must not make this look in-scope."""
    got = _validate(_record(perturbations=[
        _pert(agent="Rag1 knockout", paired_organism="mouse", single_cell_paired="yes"),
        _pert(agent="human cohort treatment", paired_organism="human",
              single_cell_paired="no"),
    ]))
    assert got["validation"]["paired_organisms"] == ["mouse"]
    assert got["validation"]["paired_organism_human"] is False
    assert triage_priority(got) == 7


# --------------------------------------------------------------------------
# Triage tier 7 took the unused slot, so 1-6 did not renumber.
# --------------------------------------------------------------------------

def test_tier_7_does_not_displace_tiers_1_to_6():
    """The v0.0.10 renumber is a documented trap. A paper that qualifies for both
    an earlier tier and 7 must still sort to the earlier one."""
    # yes + low confidence is tier 3; it is also animal-only.
    got = _validate(_record(paper_confidence=0.45))
    assert got["validation"]["paired_organism_human"] is False
    assert triage_priority(got) == 3

    # a plain animal-only yes with nothing else wrong lands in 7, not 9.
    plain = _validate(_record())
    assert triage_priority(plain) == 7


def test_non_yes_paper_is_never_a_scope_call():
    """Tier 7 is about a `yes` that may be out of scope. A `no` cannot be."""
    got = _validate(_record(perturbation_present="no",
                            perturbations=[_pert(single_cell_paired="no")]))
    assert got["perturbation_present"] == "no"
    assert triage_priority(got) != 7


# --------------------------------------------------------------------------
# Normalisation helpers.
# --------------------------------------------------------------------------

def test_normalise_and_is_human():
    assert normalise_organism("  Homo   Sapiens ") == "homo sapiens"
    assert normalise_organism("") is None
    assert normalise_organism(None) is None
    assert normalise_organism(42) is None
    assert is_human("Human") and is_human("homo sapiens") and is_human("9606")
    assert not is_human("mouse")
    assert not is_human(None), "unknown must not read as human"
