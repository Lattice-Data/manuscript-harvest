"""A reader that loaded nothing must not report. The rest is how a run dir is read.

Every one of these was reproducible against a real directory on this machine
before `pe/runstate.py` existed. `~/.manuscript-harvest/perturbation/baseline-v0012-50b`
is the two-run baseline SKILL.md tells you to pass, and it holds `manifest.json`
beside `r1/` and `r2/` rather than a `validated/` of its own. So:

    pe.compare --baseline <that dir>
      -> "baseline v? -> v comparison over 0 paper(s)"
         "every change is accounted for by a known v0.0.5 mechanism."   exit 0

A PASS on the acceptance gate for a prompt version, having compared nothing.
`pe.audit --work <that dir>` printed six screens of zero and `pe.summarize` wrote
50 blank rows, both exit 0; `pe.audit --work <that dir>/r1` raised an uncaught
FileNotFoundError. The repo's stated design principle is that emptiness must
account for itself, and this was the one place that mattered most.

Run: python -m pytest tests/test_runstate.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pe.runstate import (  # noqa: E402
    RunError, load_manifest, load_validated, manifest_dir, resolve_run_dir,
)

RECORD = {
    "schema_version": "0.0.7", "sources_seen": ["main"], "processing_status": "ok",
    "text_completeness": "full", "has_single_cell_assay": "yes",
    "perturbation_present": "no", "perturbation_present_any_assay": "no",
    "unresolved_reason": "none", "consistency_flags": [], "perturbations": [],
    "suppressed_candidates": [],
}


def _run(tmp_path: Path, dois=("a", "b"), *, validated=None, subdir="") -> Path:
    """A minimal run directory. `validated` defaults to every paper."""
    work = tmp_path / "work"
    (work / "prompts").mkdir(parents=True)
    (work / "raw").mkdir(parents=True)
    target = work / subdir if subdir else work
    (target / "validated").mkdir(parents=True, exist_ok=True)
    (work / "manifest.json").write_text(json.dumps(
        [{"doi": d, "paper_id": d, "fetch_status": "ok", "source_ids": ["main"],
          "prompt_file": str(work / "prompts" / f"{d}.txt"),
          "raw_file": str(work / "raw" / f"{d}.json")} for d in dois]))
    for doi in (dois if validated is None else validated):
        (target / "validated" / f"{doi}.json").write_text(
            json.dumps({**RECORD, "paper_id": doi}))
    return work


# --------------------------------------------------------------------------
# The rule: no report over an empty set
# --------------------------------------------------------------------------

def test_zero_loaded_papers_is_refused(tmp_path):
    run = load_validated(_run(tmp_path, validated=()))
    assert run.loaded == 0
    with pytest.raises(RunError) as exc:
        run.require_papers("pe.audit")
    # The message has to say why refusing is the right answer, or the next
    # person just reaches for a --force flag.
    assert "empty set" in str(exc.value)
    assert "indistinguishable from a clean one" in str(exc.value)


def test_a_partial_run_is_allowed_and_says_so(tmp_path):
    """Validating 50 of 392 and summarising them is the normal mid-run move.

    The failure being fixed is silence, not incompleteness, so a partial run must
    still report -- and must state its coverage, because a short report and a
    clean one are otherwise identical.
    """
    run = load_validated(_run(tmp_path, dois=("a", "b", "c"), validated=("a",)))
    run.require_papers("pe.summarize")          # must NOT raise
    assert run.loaded == 1 and run.expected == 3
    assert not run.complete
    assert "1/3" in run.coverage()


def test_coverage_is_printed_even_when_nothing_is_wrong(tmp_path):
    run = load_validated(_run(tmp_path))
    assert run.complete
    assert run.coverage().startswith("coverage: 2/2")


def test_a_paper_that_was_never_prepared_is_not_held_against_coverage(tmp_path):
    """`expected` counts prepared entries only.

    A paper with no blocks.jsonl never had a prompt, so it cannot have a record;
    counting it as missing coverage would make every corpus with one unfetchable
    paper look permanently incomplete.
    """
    work = _run(tmp_path)
    manifest = json.loads((work / "manifest.json").read_text())
    manifest.append({"doi": "never", "error": "no blocks.jsonl",
                     "fetch_status": "not_found"})
    (work / "manifest.json").write_text(json.dumps(manifest))
    run = load_validated(work)
    assert run.expected == 2 and run.loaded == 2 and run.complete
    assert run.no_input == ["never"]
    assert "1 never prepared" in run.coverage()


# --------------------------------------------------------------------------
# The two-run baseline layout
# --------------------------------------------------------------------------

def test_two_run_baseline_names_both_options_rather_than_guessing(tmp_path):
    """Picking r1 or r2 silently is the mistake `noise_floor` already refuses.

    A noise floor is the disagreement between two runs of the SAME prompt. Choose
    the wrong member here and a version difference is reported as variance, which
    launders a real effect into "nothing moved" -- so the resolver declines to
    choose without being told.
    """
    work = tmp_path / "baseline"
    for sub in ("r1", "r2"):
        (work / sub / "validated").mkdir(parents=True)
    (work / "manifest.json").write_text("[]")
    with pytest.raises(RunError) as exc:
        resolve_run_dir(work)
    assert "two-run baseline" in str(exc.value)
    assert str(work / "r1") in str(exc.value) and str(work / "r2") in str(exc.value)


def test_prefer_selects_the_named_run(tmp_path):
    work = tmp_path / "baseline"
    for sub in ("r1", "r2"):
        (work / sub / "validated").mkdir(parents=True)
    (work / "manifest.json").write_text("[]")
    assert resolve_run_dir(work, prefer="r1") == work / "r1"
    assert resolve_run_dir(work, prefer="r2") == work / "r2"


def test_a_run_subdir_finds_the_manifest_beside_it(tmp_path):
    """`<baseline>/r1` legitimately has no manifest of its own.

    Both runs were prepared from one assembly -- that is the entire basis for
    calling their disagreement a noise floor -- so there is one manifest, in the
    parent. `pe.audit --work <baseline>/r1` used to die on this.
    """
    work = _run(tmp_path, subdir="r1")
    assert manifest_dir(work / "r1") == work
    run = load_validated(work / "r1")
    assert run.loaded == 2 and run.complete


def test_manifest_dir_does_not_climb_out_of_an_ordinary_run(tmp_path):
    """Only r1/r2 look upward. Any other directory name must not.

    Otherwise a typo'd --work inside a run root would silently adopt a sibling
    run's manifest and report against the wrong papers.
    """
    work = tmp_path / "work"
    (work / "somewhere" / "validated").mkdir(parents=True)
    (work / "manifest.json").write_text("[]")
    assert manifest_dir(work / "somewhere") == work / "somewhere"


# --------------------------------------------------------------------------
# Manifest errors say what to do, and one bad record does not cost the report
# --------------------------------------------------------------------------

def test_missing_manifest_names_the_fix_not_a_traceback(tmp_path):
    with pytest.raises(RunError) as exc:
        load_manifest(tmp_path / "nope")
    assert "does not exist" in str(exc.value)

    (tmp_path / "empty").mkdir()
    with pytest.raises(RunError) as exc:
        load_manifest(tmp_path / "empty")
    assert "pe.prepare" in str(exc.value)


def test_unparseable_manifest_is_reported_as_such(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    (work / "manifest.json").write_text("{not json")
    with pytest.raises(RunError) as exc:
        load_manifest(work)
    assert "not readable JSON" in str(exc.value)


def test_one_corrupt_validated_record_does_not_abort_the_rest(tmp_path):
    """pe.summarize and pe.audit both used to die on a raw JSONDecodeError here,
    producing no CSV and no review screen at all -- while pe.validate had always
    degraded politely on the equivalent bad raw file."""
    work = _run(tmp_path, dois=("a", "b"))
    (work / "validated" / "a.json").write_text("{ truncated")
    run = load_validated(work)
    assert run.loaded == 1
    assert [doi for doi, _ in run.unreadable] == ["a"]
    assert "1 unreadable" in run.coverage()
    run.require_papers("pe.summarize")          # b is still reportable
