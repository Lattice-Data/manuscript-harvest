"""`schema_version` is declared in four places in prompt.md and read in one.

At v0.0.12 three of those four moved to 0.0.7 and one did not: the "Constants for
a run" table still said 0.0.6, so did the `Echo schema_version as` instruction the
model is actually given, and `pe.validate` compared against a literal `"0.0.6"`.
The model followed the schema example and emitted 0.0.7, the validator complained,
and **386 of the 392 corpus records were filed with a schema_version issue** — on
papers where nothing was wrong. The cost is not the noise itself but what it hides:
`validation.issues` is where real problems surface, and one entry on every paper
makes the column unreadable.

Two guards, matching the two failures:

  1. The four declarations inside prompt.md must agree with each other. This is
     the same shape as `test_prompt_and_code_agree_on_the_closed_rule_set` in
     `test_suppressed_candidates.py`, and the same lesson SKILL.md records about
     v0.0.7's precedence bug — one rule stated in three places and changed in two.
  2. `pe.validate` must not hold an opinion of its own. It reads the table, so
     drift is structurally impossible rather than merely tested for; this asserts
     the reader still works, since a regex that silently stops matching would
     return "unknown" and disable the check.

Run: python -m pytest tests/test_schema_version.py -q
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pe.paper_text import schema_version  # noqa: E402
from pe.paper_text import split_assembled  # noqa: E402
from pe.validate import expected_schema_version, validate_result  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PROMPT = ROOT / "prompt.md"


def _prompt_text() -> str:
    return PROMPT.read_text()


# --------------------------------------------------------------------------
# The four declarations in prompt.md
# --------------------------------------------------------------------------

def test_constants_table_declares_a_real_version():
    declared = schema_version(PROMPT)
    assert re.fullmatch(r"\d+\.\d+\.\d+", declared), (
        f"the 'Constants for a run' table declares {declared!r}; if this is "
        "'unknown' the row was reworded and the reader no longer matches it")


def test_the_instruction_the_model_is_given_matches_the_table():
    """The one that mattered. The model does what this line says, not what the
    schema example shows, so a stale value here is a stale value in every
    record — or, as happened, a conflict the model resolves the other way."""
    declared = schema_version(PROMPT)
    echoed = re.search(r'Echo `schema_version` as "([^"]+)"', _prompt_text())
    assert echoed, "the 'Echo `schema_version` as' instruction is gone or reworded"
    assert echoed.group(1) == declared


def test_the_output_schema_example_matches_the_table():
    declared = schema_version(PROMPT)
    example = re.search(r'^\s*"schema_version": "([^"]+)",\s*$',
                        _prompt_text(), re.MULTILINE)
    assert example, "the output schema example no longer declares a schema_version"
    assert example.group(1) == declared


def test_every_schema_version_literal_in_the_prompt_agrees():
    """Covers the JSONL record in the batch spec too, and anything added later.

    Scoped to JSON-ish declarations (`"schema_version": "X"`) rather than every
    mention of a version number, because the changelog quite correctly talks
    about 0.0.5 and 0.0.6 in the past tense and must keep doing so.
    """
    declared = schema_version(PROMPT)
    found = set(re.findall(r'"schema_version":\s*"([^"]+)"', _prompt_text()))
    assert found, "no schema_version declarations found — did the format change?"
    assert found == {declared}, (
        f"prompt.md declares schema_version as {sorted(found)}; the constants "
        f"table says {declared!r}")


# --------------------------------------------------------------------------
# The harness reads it rather than repeating it
# --------------------------------------------------------------------------

def test_validate_takes_its_expectation_from_the_prompt():
    assert expected_schema_version() == schema_version(PROMPT)


def test_a_stale_record_is_still_rejected():
    """The fix must not have turned the check off."""
    out = _validate_bare({"schema_version": "0.0.5"})
    assert any("expected" in i and "schema_version" in i
               for i in out["validation"]["issues"])


def test_a_current_record_draws_no_schema_complaint():
    """The regression itself: 386 of 392 corpus records failed this."""
    out = _validate_bare({"schema_version": schema_version(PROMPT)})
    assert not any("schema_version=" in i for i in out["validation"]["issues"])


def test_an_unreadable_prompt_complains_instead_of_going_quiet():
    """A check that stops firing must say so. `pe.validate` cannot tell a record
    that matches from one it could not check, so silence would read as a pass."""
    out = _validate_bare({"schema_version": "0.0.7"}, expected_schema="unknown")
    assert any("schema_version not checked" in i for i in out["validation"]["issues"])


def test_expected_schema_version_is_unknown_for_a_missing_prompt(tmp_path):
    assert expected_schema_version(tmp_path / "nope.md") == "unknown"


def test_expected_schema_version_is_unknown_when_the_row_is_gone(tmp_path):
    stub = tmp_path / "prompt.md"
    stub.write_text("Version: 9.9.9\n\n| Constant | Value |\n|---|---|\n"
                    "| temperature | `0` |\n")
    assert expected_schema_version(stub) == "unknown"


# --------------------------------------------------------------------------

ASSEMBLED = "<<<SOURCE id=main type=main_text>>>\nNo perturbation was applied.\n"


def _validate_bare(over: dict, **kwargs) -> dict:
    record = {
        "paper_id": "10.0000_test",
        "sources_seen": ["main"],
        "processing_status": "ok",
        "text_completeness": "full",
        "has_single_cell_assay": "no",
        "single_cell_assay_types": [],
        "perturbation_present": "no",
        "perturbation_present_any_assay": "no",
        "paper_confidence": 0.9,
        "unresolved_reason": "none",
        "consistency_flags": [],
        "perturbations": [],
        "samples": [],
        "suppressed_candidates": [],
        "ambiguities": "",
    }
    record.update(over)
    return validate_result(json.loads(json.dumps(record)),
                           split_assembled(ASSEMBLED), 0.85, "test", **kwargs)
