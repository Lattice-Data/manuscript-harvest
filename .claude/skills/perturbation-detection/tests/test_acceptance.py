"""The acceptance runner, one test per way a gate can certify nothing.

The criterion vocabulary was taken from the two scorers this replaces rather
than invented, so the tests are about the guards instead. Each of the three
below shipped in a real scorer at least once:

  * a criterion whose applicable set is empty printing PASS;
  * every failure list coming out empty because the papers were never scored;
  * a gate reporting FAIL and exiting 0.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.acceptance import (  # noqa: E402
    SpecError,
    dotted,
    gather,
    load_spec,
    main,
    report,
    score,
)


def write_run(root: Path, name: str, records: dict) -> None:
    out = root / name / "validated"
    out.mkdir(parents=True, exist_ok=True)
    for paper, body in records.items():
        (out / f"{paper.replace('/', '_')}.json").write_text(json.dumps(body))


def spec_dict(papers: dict, criteria: list, baseline: bool = False) -> dict:
    runs = {"r1": "r1", "r2": "r2"}
    if baseline:
        runs["baseline"] = "base"
    return {"version": "0.0.x", "runs": runs, "field": "v",
            "papers": papers, "criteria": criteria}


def run(tmp_path: Path, spec: dict) -> dict:
    return score(spec, gather(spec, tmp_path))


def verdicts(outcome: dict) -> dict:
    return {c["id"]: (c["applicable"], c["failures"]) for c in outcome["criteria"]}


# -- the spec is refused rather than half-understood -------------------------

def _written(tmp_path, spec) -> Path:
    path = tmp_path / "spec.yaml"
    path.write_text(yaml.safe_dump(spec))
    return path


@pytest.mark.parametrize("missing", ["version", "runs", "field", "papers", "criteria"])
def test_a_spec_missing_a_required_key_is_refused(tmp_path, missing):
    spec = spec_dict({"10.1/a": {"role": "free"}},
                     [{"id": "c", "kind": "anchors"}])
    del spec[missing]
    with pytest.raises(SpecError, match=missing):
        load_spec(_written(tmp_path, spec))


def test_a_single_run_is_refused(tmp_path):
    """Two runs of byte-identical input, because the prompt disagrees with
    itself on a few percent of papers -- so one run cannot tell an effect from
    that noise. This was learned the expensive way and is not optional."""
    spec = spec_dict({"10.1/a": {"role": "free"}}, [{"id": "c", "kind": "anchors"}])
    del spec["runs"]["r2"]
    with pytest.raises(SpecError, match="runs.r2"):
        load_spec(_written(tmp_path, spec))


def test_a_paper_with_no_role_is_refused(tmp_path):
    """The omission `no-unpredicted-movement` exists to catch, in the file that
    declares what is being checked."""
    spec = spec_dict({"10.1/a": {"expect": "no"}}, [{"id": "c", "kind": "anchors"}])
    with pytest.raises(SpecError, match="role"):
        load_spec(_written(tmp_path, spec))


def test_a_mover_without_both_ends_is_refused(tmp_path):
    spec = spec_dict({"10.1/a": {"role": "mover", "expect": "yes"}},
                     [{"id": "c", "kind": "movers"}])
    with pytest.raises(SpecError, match="`was`"):
        load_spec(_written(tmp_path, spec))


def test_an_unknown_criterion_kind_is_refused(tmp_path):
    """The whole failure mode: a kind nobody implemented scores nothing and the
    run reports a pass."""
    spec = spec_dict({"10.1/a": {"role": "free"}},
                     [{"id": "c", "kind": "vibes"}])
    with pytest.raises(SpecError, match="not one of"):
        load_spec(_written(tmp_path, spec))


def test_duplicate_criterion_ids_are_refused(tmp_path):
    spec = spec_dict({"10.1/a": {"role": "free"}},
                     [{"id": "c", "kind": "anchors"}, {"id": "c", "kind": "movers"}])
    with pytest.raises(SpecError, match="share the id"):
        load_spec(_written(tmp_path, spec))


def test_stable_without_a_field_and_exercised_without_a_predicate(tmp_path):
    for criterion, pattern in (({"id": "s", "kind": "stable"}, "`field`"),
                               ({"id": "e", "kind": "exercised"}, "`predicate`")):
        spec = spec_dict({"10.1/a": {"role": "free"}}, [criterion])
        with pytest.raises(SpecError, match=pattern):
            load_spec(_written(tmp_path, spec))


# -- the criterion kinds ----------------------------------------------------

def test_anchors_hold_and_fail(tmp_path):
    write_run(tmp_path, "r1", {"10.1/a": {"v": "no"}, "10.1/b": {"v": "yes"}})
    write_run(tmp_path, "r2", {"10.1/a": {"v": "no"}, "10.1/b": {"v": "yes"}})
    spec = spec_dict({"10.1/a": {"role": "anchor", "expect": "no"},
                      "10.1/b": {"role": "anchor", "expect": "no"}},
                     [{"id": "anchors", "kind": "anchors"}])
    applicable, failures = verdicts(run(tmp_path, spec))["anchors"]
    assert applicable == 2 and len(failures) == 1 and "10.1/b" in failures[0]


def test_a_mover_that_did_not_move_fails(tmp_path):
    write_run(tmp_path, "r1", {"10.1/a": {"v": "no"}})
    write_run(tmp_path, "r2", {"10.1/a": {"v": "no"}})
    spec = spec_dict({"10.1/a": {"role": "mover", "was": "no", "expect": "yes"}},
                     [{"id": "movers", "kind": "movers"}])
    applicable, failures = verdicts(run(tmp_path, spec))["movers"]
    assert applicable == 1 and len(failures) == 1


def test_unpredicted_movement_is_caught_against_the_baseline(tmp_path):
    write_run(tmp_path, "base", {"10.1/a": {"v": "no"}})
    write_run(tmp_path, "r1", {"10.1/a": {"v": "yes"}})
    write_run(tmp_path, "r2", {"10.1/a": {"v": "yes"}})
    spec = spec_dict({"10.1/a": {"role": "free"}},
                     [{"id": "quiet", "kind": "no-unpredicted-movement"}],
                     baseline=True)
    applicable, failures = verdicts(run(tmp_path, spec))["quiet"]
    assert applicable == 1 and len(failures) == 1


def test_unpredicted_movement_examines_nothing_without_a_baseline(tmp_path):
    """And so reports 0 examined rather than PASS -- the criterion cannot be
    evaluated at all, which is a different thing from being satisfied."""
    write_run(tmp_path, "r1", {"10.1/a": {"v": "yes"}})
    write_run(tmp_path, "r2", {"10.1/a": {"v": "yes"}})
    spec = spec_dict({"10.1/a": {"role": "free"}},
                     [{"id": "quiet", "kind": "no-unpredicted-movement"}])
    applicable, failures = verdicts(run(tmp_path, spec))["quiet"]
    assert applicable == 0 and not failures


def test_run_to_run_instability_is_caught(tmp_path):
    write_run(tmp_path, "r1", {"10.1/a": {"v": "no", "validation": {"s": "no"}}})
    write_run(tmp_path, "r2", {"10.1/a": {"v": "no", "validation": {"s": "yes"}}})
    spec = spec_dict({"10.1/a": {"role": "free"}},
                     [{"id": "stable", "kind": "stable", "field": "validation.s"}])
    applicable, failures = verdicts(run(tmp_path, spec))["stable"]
    assert applicable == 1 and len(failures) == 1


def test_exercised_counts_papers_where_the_mechanism_fired(tmp_path):
    write_run(tmp_path, "r1", {"10.1/a": {"v": "no", "validation": {"capped": True}},
                               "10.1/b": {"v": "no", "validation": {"capped": False}}})
    write_run(tmp_path, "r2", {"10.1/a": {"v": "no", "validation": {"capped": False}},
                               "10.1/b": {"v": "no", "validation": {"capped": False}}})
    spec = spec_dict({"10.1/a": {"role": "free"}, "10.1/b": {"role": "free"}},
                     [{"id": "fired", "kind": "exercised",
                       "predicate": "validation.capped", "minimum": 1}])
    applicable, failures = verdicts(run(tmp_path, spec))["fired"]
    assert applicable == 1 and not failures


def test_a_mechanism_that_never_fired_is_not_a_pass(tmp_path):
    """The v0.0.25 case exactly: the defect gate read a key the verifier never
    returned, so every claim was dropped, the cap fell back to a deterministic
    route, and the criterion would have agreed 24/24 over a dead mechanism."""
    write_run(tmp_path, "r1", {"10.1/a": {"v": "no", "validation": {}}})
    write_run(tmp_path, "r2", {"10.1/a": {"v": "no", "validation": {}}})
    spec = spec_dict({"10.1/a": {"role": "free"}},
                     [{"id": "fired", "kind": "exercised",
                       "predicate": "validation.capped", "minimum": 1}])
    outcome = run(tmp_path, spec)
    assert outcome["criteria"][0]["applicable"] == 0
    assert report(spec, outcome) == 1


# -- the guards -------------------------------------------------------------

def test_a_pending_paper_stops_everything_being_evaluated(tmp_path, capsys):
    """With a paper unscored every failure list is empty, so the run would
    print a wall of passes. Refuse before evaluating anything."""
    write_run(tmp_path, "r1", {"10.1/a": {"v": "no"}})
    write_run(tmp_path, "r2", {})
    spec = spec_dict({"10.1/a": {"role": "anchor", "expect": "WRONG"}},
                     [{"id": "anchors", "kind": "anchors"}])
    outcome = run(tmp_path, spec)
    assert outcome["pending"] == ["10.1/a"]
    assert report(spec, outcome) == 1
    assert "INCOMPLETE" in capsys.readouterr().err


def test_a_blocking_criterion_that_examined_nothing_fails(tmp_path):
    write_run(tmp_path, "r1", {"10.1/a": {"v": "no"}})
    write_run(tmp_path, "r2", {"10.1/a": {"v": "no"}})
    spec = spec_dict({"10.1/a": {"role": "free"}},
                     [{"id": "anchors", "kind": "anchors", "blocking": True}])
    outcome = run(tmp_path, spec)
    assert outcome["criteria"][0]["applicable"] == 0
    assert report(spec, outcome) == 1


def test_a_non_blocking_criterion_that_examined_nothing_does_not_fail(tmp_path):
    write_run(tmp_path, "r1", {"10.1/a": {"v": "no"}})
    write_run(tmp_path, "r2", {"10.1/a": {"v": "no"}})
    spec = spec_dict({"10.1/a": {"role": "free"}},
                     [{"id": "anchors", "kind": "anchors", "blocking": False}])
    assert report(spec, run(tmp_path, spec)) == 0


def test_a_failing_gate_exits_non_zero(tmp_path):
    """A gate that reports FAIL and exits 0 is the vacuous pass one layer up: a
    caller wiring it into CI sees success. One scorer shipped this way."""
    write_run(tmp_path, "r1", {"10.1/a": {"v": "yes"}})
    write_run(tmp_path, "r2", {"10.1/a": {"v": "yes"}})
    spec = spec_dict({"10.1/a": {"role": "anchor", "expect": "no"}},
                     [{"id": "anchors", "kind": "anchors"}])
    assert report(spec, run(tmp_path, spec)) == 1


def test_a_passing_gate_exits_zero(tmp_path):
    write_run(tmp_path, "r1", {"10.1/a": {"v": "no"}})
    write_run(tmp_path, "r2", {"10.1/a": {"v": "no"}})
    spec = spec_dict({"10.1/a": {"role": "anchor", "expect": "no"}},
                     [{"id": "anchors", "kind": "anchors"}])
    assert report(spec, run(tmp_path, spec)) == 0


def test_unreadable_json_is_raised_rather_than_counted_as_pending(tmp_path):
    """Pending means "not run yet". A corrupt record is a different thing and
    must not be filed under it, or a broken write looks like unfinished work."""
    write_run(tmp_path, "r1", {"10.1/a": {"v": "no"}})
    (tmp_path / "r2" / "validated").mkdir(parents=True)
    (tmp_path / "r2" / "validated" / "10.1_a.json").write_text("{not json")
    spec = spec_dict({"10.1/a": {"role": "free"}},
                     [{"id": "anchors", "kind": "anchors"}])
    with pytest.raises(SpecError):
        gather(spec, tmp_path)


# -- odds and ends ----------------------------------------------------------

def test_dotted_reads_nested_and_missing_alike():
    assert dotted({"a": {"b": 1}}, "a.b") == 1
    assert dotted({"a": {"b": 1}}, "a.c") is None
    assert dotted({"a": 1}, "a.b") is None
    assert dotted({}, "a") is None


def test_the_command_reports_a_missing_run_root(tmp_path):
    assert main([str(tmp_path / "s.yaml"), "--run-root", str(tmp_path / "nope")]) == 2


def test_the_command_reports_a_bad_spec(tmp_path):
    (tmp_path / "s.yaml").write_text("just a string\n")
    assert main([str(tmp_path / "s.yaml"), "--run-root", str(tmp_path)]) == 2


def test_the_template_in_the_repo_is_a_valid_spec():
    """A template nobody can load is worse than none: the first person to copy
    it finds out at the end of a two-run acceptance test."""
    template = Path(__file__).resolve().parent.parent / "history" / "acceptance" / "TEMPLATE.yaml"
    assert template.exists(), template
    spec = load_spec(template)
    assert spec["papers"] and spec["criteria"]


def test_an_unquoted_no_is_caught_as_a_yaml_boolean(tmp_path):
    """`expect: no` is the most natural thing to write and YAML 1.1 reads it as
    False. Unguarded it never equals the string the model emitted, so every
    anchor holding a negative fails for a reason the message would not explain.
    `harness/pack.py` guards the list case; this is the scalar one.
    """
    path = tmp_path / "s.yaml"
    path.write_text(
        "version: 0.0.x\nruns: {r1: r1, r2: r2}\nfield: v\n"
        "papers:\n  10.1/a:\n    role: anchor\n    expect: no\n"
        "criteria:\n  - {id: c, kind: anchors}\n")
    with pytest.raises(SpecError, match="YAML read as a boolean"):
        load_spec(path)


def test_a_quoted_no_is_fine(tmp_path):
    path = tmp_path / "s.yaml"
    path.write_text(
        "version: 0.0.x\nruns: {r1: r1, r2: r2}\nfield: v\n"
        'papers:\n  10.1/a:\n    role: anchor\n    expect: "no"\n'
        "criteria:\n  - {id: c, kind: anchors}\n")
    assert load_spec(path)["papers"]["10.1/a"]["expect"] == "no"


def test_blocking_stays_a_real_boolean(tmp_path):
    """The guard is scoped to the verdict cells. `blocking: true` is a flag."""
    path = tmp_path / "s.yaml"
    path.write_text(
        "version: 0.0.x\nruns: {r1: r1, r2: r2}\nfield: v\n"
        'papers:\n  10.1/a:\n    role: free\n'
        "criteria:\n  - {id: c, kind: anchors, blocking: false}\n")
    assert load_spec(path)["criteria"][0]["blocking"] is False
