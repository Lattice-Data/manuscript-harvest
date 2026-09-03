"""Guards for five harness defects, plus the negative control for the 0-flag result.

The strongest claim this skill makes is that over 392 papers **2,471 of 2,471
evidence quotes verified, with 0 unverifiable, 0 misattributed, 0 perturbations
dropped and 0 EV/CC flags raised.** A result that clean has two readings — the
model is honest, or the checker cannot fail — and until now nothing separated
them at corpus scale. `test_the_verifier_can_actually_fail` is that separation:
it corrupts a quote in real records from a real run and asserts the flags fire.
It skips when no run directory is present, the same way
`tests/test_extract_corpus.py` skips for want of a local corpus.

The rest are regressions for defects found reviewing this layer against a
taggable release. Each one was reproduced before it was fixed.

Run: python -m pytest tests/test_harness_guards.py -q
"""
from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pe.paper_text import build_sources, split_assembled  # noqa: E402
from pe.runroot import work_default  # noqa: E402
from pe.validate import (  # noqa: E402
    TEXT_COMPLETENESS, model_of, stage_b, validate_result,
)

ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------
# Stage B must fail CLOSED
# --------------------------------------------------------------------------

@pytest.mark.parametrize("completeness", [None, "", "Full", "truncated ", "partial",
                                          "unknown_value", 0])
def test_stage_b_caps_a_no_on_any_value_that_is_not_full(completeness):
    """prompt.md: cap when `text_completeness` is "anything other than 'full'".

    The guard here used to be `text_completeness in TEXT_COMPLETENESS and
    != "full"`, so every value above skipped the cap and kept the "no" — while
    an honest "unknown" was capped. The safety mechanism failed OPEN on exactly
    the input it should distrust most, and a typo switched it off.
    """
    assert stage_b("no", "ok", completeness) == ("unclear", True)


def test_stage_b_still_does_not_cap_a_full_text_negative():
    assert stage_b("no", "ok", "full") == ("no", False)


@pytest.mark.parametrize("verdict", ["yes", "unclear"])
def test_stage_b_never_caps_a_positive(verdict):
    """The asymmetry is the point: missing text can hide the sentence that would
    have paired a perturbation, but it cannot invent one."""
    assert stage_b(verdict, "partial", "truncated") == (verdict, False)


def test_every_legal_completeness_value_is_still_covered():
    """A tightened guard must not have loosened the enum it replaced."""
    for value in TEXT_COMPLETENESS:
        expected = ("no", False) if value == "full" else ("unclear", True)
        assert stage_b("no", "ok", value) == expected


# --------------------------------------------------------------------------
# --no-supplementary: a documented toggle that had never worked
# --------------------------------------------------------------------------

_BLOCKS = [
    {"kind": "metadata", "section": None, "source_file": "fulltext.pdf",
     "text": "Title: A paper\nDOI: 10.1/x"},
    {"kind": "paragraph", "section": "methods", "source_file": "fulltext.pdf",
     "text": "Cells were treated with LPS and profiled by scRNA-seq. " * 20},
    {"kind": "paragraph", "section": "methods",
     "source_file": "supplementary/mmc1.pdf",
     "text": "Supplementary methods: LPS at 100 ng/mL for 4 h. " * 20},
]


def test_main_text_only_assembly_reports_its_char_count():
    """`pe.prepare --no-supplementary` died on `KeyError: 'chars'`.

    `build_sources` returned early on this path, before the block that set
    `chars` and `supp_chars`, so the toggle prompt.md documents ("Supplementary
    sources -> Main text only") crashed every time it was used.
    """
    sources, stats = build_sources(_BLOCKS, include_supplementary=False)
    assert [s["source_id"] for s in sources] == ["main"]
    assert stats["chars"] == stats["main_chars"] == sources[0]["char_count"]
    assert stats["supp_chars"] == 0


def test_both_assembly_paths_agree_about_the_main_text():
    with_supp, s_with = build_sources(_BLOCKS, include_supplementary=True)
    _, s_without = build_sources(_BLOCKS, include_supplementary=False)
    assert s_with["main_chars"] == s_without["main_chars"]
    assert s_with["chars"] > s_without["chars"]
    assert [s["source_id"] for s in with_supp] == ["main", "supp1"]


def test_stats_keys_do_not_depend_on_the_path_taken():
    """The defect was a key present on one path and absent on the other, which
    no caller can defend against. Asserted as a property, not per key."""
    _, a = build_sources(_BLOCKS, include_supplementary=True)
    _, b = build_sources(_BLOCKS, include_supplementary=False)
    assert set(a) == set(b)


# --------------------------------------------------------------------------
# config.yaml is read, not documentation. Every key must have a reader.
# --------------------------------------------------------------------------

def test_no_config_key_is_read_by_nothing():
    """Five keys were dead: `fuzzy_match.enabled`, `.normalize_unicode`,
    `.normalize_punctuation`, `confidence_thresholds` and `flag_all_for_review`.

    config.yaml's own header says it "is READ by pe.prepare and pe.validate — it
    is not documentation", and it already carried a comment explaining that the
    removed `output_dir:` key "was read by nothing, so setting it looked like it
    worked and did not". The same failure, five more times. This is the guard
    that comment was asking for.
    """
    yaml = pytest.importorskip("yaml")
    config = yaml.safe_load((ROOT / "config.yaml").read_text()) or {}
    source = "\n".join(p.read_text() for p in sorted((ROOT / "pe").glob("*.py")))

    def keys(node, prefix=""):
        for key, value in node.items():
            yield key
            if isinstance(value, dict):
                yield from keys(value, f"{prefix}{key}.")

    unread = [k for k in keys(config)
              if f'"{k}"' not in source and f"'{k}'" not in source]
    assert not unread, (
        f"config.yaml declares {unread} but no module in pe/ reads them. A key "
        f"nobody reads looks like it works and does not — either wire it up or "
        f"delete it.")


# --------------------------------------------------------------------------
# needs_section_pass routes to the right queue
# --------------------------------------------------------------------------

def _minimal(**over):
    record = {
        "task_version": "0.0.13", "sources_seen": ["main"],
        "processing_status": "ok", "text_completeness": "full",
        "has_single_cell_assay": "yes", "perturbation_present": "no",
        "perturbation_present_any_assay": "no", "unresolved_reason": "none",
        "consistency_flags": [], "perturbations": [], "suppressed_candidates": [],
        "samples": [], "paper_confidence": 0.9,
    }
    record.update(over)
    return record


def test_over_budget_paper_says_re_fetching_will_not_help():
    """Both papers that hit `needs_section_pass` on the 392-paper run were capped
    at "unclear"/degraded_text like any truncated paper and sorted to triage P4 —
    "route to re-fetch, not to reading". That is the wrong queue: the text arrived
    complete and simply does not fit the budget with Methods preserved. pe.prepare
    wrote the flag into the manifest and nothing read it.
    """
    out = validate_result(_minimal(), {"main": "x"}, 0.85,
                          needs_section_pass=True, truncated_by_harness=True)
    assert out["needs_section_pass"] is True
    issue = next(i for i in out["validation"]["issues"] if "needs_section_pass" in i)
    assert "re-fetching will not change it" in issue
    assert "section-level second pass" in issue


def test_an_ordinary_truncation_does_not_claim_to_need_a_section_pass():
    out = validate_result(_minimal(), {"main": "x"}, 0.85,
                          truncated_by_harness=True)
    assert "needs_section_pass" not in out
    assert not [i for i in out["validation"]["issues"] if "needs_section_pass" in i]


# --------------------------------------------------------------------------
# model_id: the pin now buys attribution
# --------------------------------------------------------------------------

def test_model_id_is_recorded_when_the_runner_wrote_it(tmp_path):
    (tmp_path / "meta").mkdir()
    (tmp_path / "meta" / "10.1_x.model").write_text("claude-opus-5\n")
    assert model_of(tmp_path, "10.1_x") == "claude-opus-5"
    out = validate_result(_minimal(), {"main": "x"}, 0.85, model_id="claude-opus-5")
    assert out["validation"]["model_id"] == "claude-opus-5"


def test_an_unrecorded_model_is_none_not_a_guess(tmp_path):
    """Every run before the sidecar existed has no model recorded, and None is
    the honest answer. Defaulting to the current pin would backdate a claim."""
    assert model_of(tmp_path, "10.1_x") is None
    assert validate_result(_minimal(), {"main": "x"}, 0.85)["validation"]["model_id"] is None


# --------------------------------------------------------------------------
# The negative control for 2,471/2,471
# --------------------------------------------------------------------------

def _real_run() -> Path | None:
    """A run directory with validated records and the prompts they were scored on.

    Honours PERTURBATION_RUN_ROOT through `work_default()`, so this follows the
    same location every other module uses.
    """
    for candidate in (Path(os.environ["PE_TEST_WORK"]) if os.environ.get("PE_TEST_WORK")
                      else None, work_default()):
        if candidate and (candidate / "validated").is_dir() and (candidate / "prompts").is_dir():
            return candidate
    return None


def test_the_verifier_can_actually_fail():
    """0 failed quotes over 392 papers: honest model, or a checker that cannot fail?

    Takes real records that verified cleanly, corrupts the middle of each
    evidence quote against the real assembled text, and re-runs the real
    validator. Every one must now fail to verify, drop its perturbation, raise
    EV-UNVERIFIED and EV-PERT-DROPPED, and recompute the determination away from
    "yes". A unit test with synthetic text proves the mechanism exists; this
    proves it engages on the actual corpus, at the actual 0.85 threshold, against
    the actual multi-source assembly — which is where a too-permissive fuzzy
    match would hide.
    """
    work = _real_run()
    if work is None:
        pytest.skip(f"no run directory at {work_default()} (set PE_TEST_WORK)")

    from pe.validate import paper_text_from_prompt

    checked = 0
    for path in sorted((work / "validated").glob("*.json"))[:200]:
        record = json.loads(path.read_text())
        prompt_file = work / "prompts" / f"{path.stem}.txt"
        if not prompt_file.is_file():
            continue
        perts = record.get("perturbations") or []
        if record.get("perturbation_present") != "yes" or not perts:
            continue
        quotes = [q for p in perts for q in (p.get("evidence_quotes") or [])
                  if isinstance(q, dict) and len(str(q.get("quote") or "")) > 60]
        if not quotes:
            continue

        sources = split_assembled(paper_text_from_prompt(prompt_file))
        clean = validate_result(copy.deepcopy(record), sources, 0.85)
        if clean["validation"]["quotes_failed"]:
            continue                      # not a clean record; nothing to falsify

        # Corrupt every quote the record rests on, in the middle, where a
        # prefix/suffix match cannot rescue it.
        poisoned = copy.deepcopy(record)
        for pert in poisoned["perturbations"]:
            for quote in pert.get("evidence_quotes") or []:
                text = str(quote.get("quote") or "")
                if len(text) > 60:
                    half = len(text) // 2
                    quote["quote"] = (text[:half] + " ZZQX fabricated interpolation "
                                      "that appears in no source ZZQX " + text[half:])
            if isinstance(pert.get("assay_evidence"), dict):
                pert["assay_evidence"]["quote"] = "ZZQX wholly invented pairing claim ZZQX"

        out = validate_result(poisoned, sources, 0.85)
        validation = out["validation"]
        assert validation["quotes_failed"] > 0, f"{path.stem}: fabricated quote verified"
        assert "EV-UNVERIFIED" in validation["evidence_flags"], path.stem
        assert "EV-PERT-DROPPED" in validation["evidence_flags"], path.stem
        assert validation["perturbations_kept"] == 0, path.stem
        assert out["perturbation_present"] != "yes", (
            f"{path.stem}: determination survived the removal of all its evidence")
        assert validation["determination_changed_by_harness"] is True, path.stem
        checked += 1
        if checked == 5:
            break

    if not checked:
        pytest.skip("no clean 'yes' record with a long quote in this run")
    assert checked >= 1
