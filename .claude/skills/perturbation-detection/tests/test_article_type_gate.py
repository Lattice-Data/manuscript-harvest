"""v0.0.23's Step 0b gate, `not_applicable`, and Stage A rule A-1.

Its first job, like `test_suppressed_candidates.py` before it, is to prove that
the subject of `test_determination_v005.py` is UNAFFECTED: with
`reports_primary_research` = "yes" -- or absent, as every pre-0.0.23 record has
it -- Stage A behaves exactly as it did, rule for rule.

Its second job is the attractor. Adding `suppressed_candidates` in v0.0.10 moved
2 of 6 determinations with no criterion edited, because making a path structured
makes it more travelled. A gate is a worse version of that risk: it does not
merely reclassify a perturbation, it skips Steps 1-3 entirely. So the guards
below are about the gate firing when it should NOT.

Run: python -m pytest tests/test_article_type_gate.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.pack import tables  # noqa: E402
from task.rules import (  # noqa: E402
    DETERMINATION_LABELS, LABELS, PRIMARY_RESEARCH, consistency_checks,
    expected_determination, stage_a, stage_b,
)

_REC = tables()["record"]


def make(status="ok", completeness="full", has_sc="yes", any_assay="yes",
         paired=(), primary=None):
    r = {
        "processing_status": status,
        "text_completeness": completeness,
        "has_single_cell_assay": has_sc,
        "perturbation_present_any_assay": any_assay,
        "perturbations": [{"single_cell_paired": p} for p in paired],
    }
    if primary is not None:
        r["reports_primary_research"] = primary
    return r


# --------------------------------------------------------------------------
# The gate fires, and only where it should
# --------------------------------------------------------------------------

def test_a_non_primary_paper_is_not_applicable():
    """The whole point. `10.1016/j.coi.2022.102188` is the paper."""
    assert stage_a(make(primary="no", paired=())) == "not_applicable"


def test_a_primary_paper_is_untouched_by_the_gate():
    """Nearly every paper. A gate that changes a primary paper's answer is a bug."""
    for paired in ((), ("yes",), ("no",), ("unclear",), ("no", "unclear")):
        with_gate = stage_a(make(primary="yes", paired=paired))
        without = stage_a(make(primary=None, paired=paired))
        assert with_gate == without, (
            f"paired={paired}: the gate moved a primary paper from {without!r} "
            f"to {with_gate!r}")


def test_a_pre_0023_record_without_the_field_is_not_gated():
    """392 stored records have no such field. Absent must not mean "review"."""
    assert "reports_primary_research" not in make()
    assert stage_a(make(paired=("yes",))) == "yes"


def test_a_failed_extraction_outranks_the_gate():
    """A0 before A-1, deliberately.

    A paywall page, a captcha notice and an access-denied stub all look like a
    body with no Methods. "unclear" says nothing was assessed, which is true;
    "not_applicable" would assert something about an article nobody could read.
    """
    assert stage_a(make(status="failed", primary="no")) == "unclear"


def test_the_gate_is_not_folded_into_the_empty_array_rule():
    """A-1 is not A1, and this is why it gets its own rule.

    A review's `perturbations` array is empty because Step 0b RETURNED, not
    because the text was searched and nothing found. A1 reads emptiness as
    evidence and would answer "no" or "unclear" from it -- both of which claim
    something about a paper that was never assessed.
    """
    for any_assay in ("yes", "no", "unclear"):
        assert stage_a(make(primary="no", any_assay=any_assay, paired=())) \
            == "not_applicable", f"any_assay={any_assay} reached A1"


def test_a_gated_paper_is_not_downgraded_by_degraded_text():
    """A review very often has no Methods section, so it lands
    `text_completeness="methods_missing"` -- exactly the condition Stage B downgrades.
    The downgrade exists because missing text can hide a pairing sentence; a review has
    no pairing to hide, and downgrading every review to "unclear" would send them all
    to triage to be re-read as if the extraction had failed."""
    for completeness in ("truncated", "methods_missing", "unknown"):
        final, downgraded = stage_b("not_applicable", "ok", completeness)
        assert (final, downgraded) == ("not_applicable", False), (
            f"{completeness}: Stage B moved a gated paper to {final!r}")
    assert stage_b("no", "ok", "methods_missing") == ("unclear", True), \
        "the downgrade must still fire on a real negative, or this test proves nothing"


def test_the_gate_survives_stage_b_end_to_end():
    assert expected_determination(
        make(primary="no", completeness="methods_missing")) == "not_applicable"


# --------------------------------------------------------------------------
# CC-8: the contradiction, from either side
# --------------------------------------------------------------------------

def test_cc8_fires_when_a_gated_paper_still_carries_perturbations():
    """Step 0b returns before Step 2, so this cannot arise from following the
    steps in order. Either the gate over-fired on a paper with its own
    experiments, or another paper's experiments were filed under this one --
    which is literally what happened to `coi.2022.102188`, six of them."""
    assert "CC-8" in consistency_checks(make(primary="no", paired=("yes",)))


def test_cc8_is_silent_on_a_correctly_gated_paper():
    assert "CC-8" not in consistency_checks(make(primary="no", paired=()))


def test_cc8_is_silent_on_a_primary_paper_with_perturbations():
    """The common case. A check that fires here would fire on ~101 papers."""
    assert "CC-8" not in consistency_checks(make(primary="yes", paired=("yes",)))


def test_cc8_has_registered_wording():
    """A code with no message renders as a KeyError at report time."""
    assert "CC-8" in tables()["decide"]["checks"]


# --------------------------------------------------------------------------
# The over-permission this split exists to avoid
# --------------------------------------------------------------------------

def test_not_applicable_is_not_legal_on_the_tri_state_fields():
    """`labels` is the enum for `has_single_cell_assay` and
    `perturbation_present_any_assay` as well. Adding `not_applicable` to it --
    the obvious one-line way to ship this -- would have made
    `has_single_cell_assay: "not_applicable"` a valid record."""
    assert "not_applicable" not in LABELS
    assert "not_applicable" in DETERMINATION_LABELS
    assert set(LABELS) < set(DETERMINATION_LABELS), \
        "the determination set must still contain the tri-state, unchanged"


def test_the_gate_field_is_binary():
    """Not a tri-state. An article either reports its own study or it does not,
    and an "unclear" here would route to the same re-read as a degraded text
    while telling a curator nothing about which problem they have."""
    assert PRIMARY_RESEARCH == ("yes", "no")


def test_the_gate_field_is_required():
    """Optional-defaulting-to-primary is a gate that never fires, which is the
    shape six of the seven detection-review blockers had. `harness.pending` re-runs a
    record that lacks it rather than assuming."""
    assert "reports_primary_research" in _REC["required_fields"]


def test_the_gate_field_is_enum_checked():
    checked = {c["path"] for c in _REC["field_checks"]}
    assert "reports_primary_research" in checked


def test_the_determination_check_accepts_the_new_value():
    """`perturbation_present` must check against the wider set, or every gated
    paper is an off-enum validation issue."""
    by_path = {c["path"]: c["in"] for c in _REC["field_checks"]}
    assert by_path["perturbation_present"] == "determination_labels"
    assert by_path["has_single_cell_assay"] == "labels"
    assert by_path["perturbation_present_any_assay"] == "labels"


def test_the_confusion_matrix_can_name_the_new_value():
    """A determination the diff cannot name is one it drops silently."""
    assert "not_applicable" in tables()["change"]["order"]


# --------------------------------------------------------------------------
# The prompt's own guards, read out of the prompt
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def gate_text():
    spec = (Path(__file__).resolve().parent.parent / "criteria" / "prompt.md").read_text()
    start = spec.index("### Step 0b:")
    return spec[start:spec.index("## Step 1:")]


@pytest.mark.parametrize("phrase,why", [
    ("fires rarely", "guard 2: a required field with no such note reads as a quota"),
    ("reanalyses public or previously published data is primary research",
     "guard 3: the named negative that would otherwise gate the integration atlas"),
    ("10.1038/s41467-021-25125-1",
     "the atlas that must NOT trip the gate, named so the case is testable"),
    ("is not thereby a review",
     "methods_missing is about the TEXT; the gate is about the ARTICLE"),
    ("BEFORE you look at any experiment",
     "guard 1: precedence, or 'these look like someone else's' reaches the gate"),
    ("never suppresses a primary paper's perturbations", "guard 1's statement"),
])
def test_the_gate_states_its_guards(gate_text, phrase, why):
    assert phrase in gate_text, f"Step 0b has lost {why}"


def test_the_gate_returns_rather_than_carrying_perturbations_forward(gate_text):
    """If it did not return, a review's record would attribute six other
    groups' experiments to it, and CC-8 would have nothing to detect."""
    assert "return immediately" in gate_text
    assert "Do not run Steps 1-3." in gate_text


# --------------------------------------------------------------------------
# Downstream: the value has to survive the reporting layer
# --------------------------------------------------------------------------

def _gated_record():
    return {
        "task_version": "0.0.23",
        "paper_id": "10.1016_j.coi.2022.102188",
        "sources_seen": ["main"],
        "processing_status": "ok",
        "text_completeness": "methods_missing",
        "reports_primary_research": "no",
        "has_single_cell_assay": "yes",
        "perturbation_present": "not_applicable",
        "perturbation_present_any_assay": "no",
        "unresolved_reason": "none",
        "consistency_flags": [],
        "perturbations": [],
        "samples": [],
        "suppressed_candidates": [],
        "ambiguities": "Review article (Current Opinion in Immunology).",
        "validation": {},
    }


def test_a_gated_paper_lands_in_the_settled_tier():
    """Not tier 2 and not tier 5.

    Tier 2 is "not yes + a suppressed candidate under a rule in review would
    have paired yes", and `present != "yes"` is true of a gated paper -- so the
    only thing keeping it out is that Step 0b left `suppressed_candidates`
    empty. Worth pinning: if the gate ever stopped returning early, every review
    would appear at the top of a curator's queue.
    """
    from task.report import triage_priority
    assert triage_priority(_gated_record()) == 9


def test_a_gated_paper_is_visible_in_the_run_report():
    """The counters tally the VALUES present, not a fixed yes/no/unclear triple,
    so a gated paper must appear by name. If it did not, the first run under
    v0.0.23 could gate papers and show a curator nothing."""
    from task.report import counters, row_for
    rec = _gated_record()
    row = row_for(rec["paper_id"], rec, {"source_ids": ["main"]})
    assert row["perturbation_present"] == "not_applicable"
    text = "\n".join(counters([row], {rec["paper_id"]: rec}))
    assert "not_applicable" in text, (
        "the run report does not name the gated papers, so the gate's blast "
        "radius is invisible on the run that introduces it")


def test_the_pending_check_rejects_a_record_without_the_gate_field():
    """`reports_primary_research` is required, and this is the consumer that
    makes "required" mean something: a record lacking it is re-run rather than
    validated. All 392 stored v0.0.22 records lack it, which is correct -- they
    predate the criteria change and are stale by definition."""
    from harness.pending import REQUIRED
    assert "reports_primary_research" in REQUIRED
