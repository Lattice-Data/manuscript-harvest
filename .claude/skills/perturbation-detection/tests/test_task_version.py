"""One version, declared once, and structurally impossible to restate.

Supersedes `test_schema_version.py`, which guarded a weaker property. That file
asserted the four `schema_version` declarations inside prompt.md agreed with each
other — a real guard for a real bug: at v0.0.12 three of the four moved to 0.0.7
and one did not, the model split on the contradiction (**386 of 392 records
followed the schema example and emitted 0.0.7, 6 followed the instruction line
and emitted 0.0.6**), and `pe.validate` compared against a literal calibrated to
the minority, filing a spurious issue on 386 correct records.

0.0.13 removes the class of bug instead of testing for it. The version is
declared once, in `task/task.yaml`, and prompt.md carries `{{TASK_VERSION}}` at
every site that declares it — spliced in by `pe.prepare` exactly as
`{{PAPER_ID}}` is. Four declarations that must agree becomes one declaration and
three substitutions, so the tests below assert **the absence of a literal**
rather than the agreement of several. A test that four copies match is a test
that can pass while the design stays wrong.

Also here: the legacy path. All 392 already-scored records carry `schema_version`
and no `task_version`, and re-scoring them was explicitly not on the table, so
reading them correctly is part of the contract rather than a courtesy.

Run: python -m pytest tests/test_task_version.py -q
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pe.paper_text import prompt_version  # noqa: E402
from pe.prepare import build_template  # noqa: E402
from pe.validate import (  # noqa: E402
    LEGACY_VERSION_FIELD, expected_task_version, record_version, validate_result,
)
from pe.pack import PackError, TaskPack, load as load_pack, pack_files, pack_sha256  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SEMVER = re.compile(r"\b\d+\.\d+\.\d+\b")


@pytest.fixture()
def pack():
    return load_pack(ROOT)


# --------------------------------------------------------------------------
# One declaration
# --------------------------------------------------------------------------

def test_the_pack_declares_a_real_version(pack):
    assert SEMVER.fullmatch(pack.version), pack.version
    assert pack.name == "perturbation-detection"


def test_the_spec_contains_no_version_literal_at_any_declaration_site(pack):
    """The whole point. Not "the copies agree" — there are no copies.

    Historical version numbers in the changelog are left alone: those are a
    record of what happened, not a declaration of what is. The lines checked here
    are the four that used to declare the current version.
    """
    text = pack.spec_path.read_text()
    placeholder = pack.placeholders["task_version"]

    version_line = next(ln for ln in text.splitlines() if ln.startswith("Version:"))
    assert version_line == f"Version: {placeholder}", version_line

    for line in text.splitlines():
        # The instruction the model actually obeys, the schema example, the
        # constants table row, and the JSONL run record.
        if "Echo `task_version`" in line or '"task_version":' in line \
                or line.startswith("| `task_version`"):
            assert placeholder in line, f"declares a literal version: {line[:120]}"
            assert not SEMVER.search(line.replace(placeholder, "")), line[:120]


def test_the_spec_no_longer_asks_the_model_for_a_schema_version(pack):
    """Two version fields in the record was the drift surface. One is left."""
    text = pack.spec_path.read_text()
    changelog_end = text.index("## Scope and execution model")
    body = text[changelog_end:]
    assert 'Echo `schema_version`' not in body
    assert '"schema_version":' not in body


def test_nothing_in_pe_hardcodes_a_version(pack):
    """The harness holds no opinion about the version. `pe.validate` used to
    compare against a literal `"0.0.6"`, which is how a correct record got
    flagged 386 times."""
    offenders = {}
    for path in sorted((ROOT / "pe").glob("*.py")):
        for number, line in enumerate(path.read_text().splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or "0.0.1" in stripped and "prompt.md" in stripped:
                continue
            for found in SEMVER.findall(line):
                # A version inside a string literal is a hardcoded expectation.
                if f'"{found}"' in line or f"'{found}'" in line:
                    offenders[f"{path.name}:{number}"] = stripped[:100]
    assert not offenders, (
        f"a version literal in the harness is the bug 0.0.13 removed: {offenders}")


def test_prepare_substitutes_the_version_the_pack_declares(pack, tmp_path):
    """End to end: the model must see one consistent value in both the
    instruction it obeys and the schema example it copies. Those two disagreeing
    is precisely what produced the 386/6 split."""
    filled = (build_template(pack)
              .replace(pack.placeholders["task_version"], pack.version)
              .replace(pack.placeholders["paper_id"], "10.1_x")
              .replace(pack.placeholders["source_ids"], "main")
              .replace(pack.placeholders["paper_text"], "body"))
    assert pack.placeholders["task_version"] not in filled
    echoed = [ln for ln in filled.splitlines() if "Echo `task_version`" in ln]
    schema = [ln for ln in filled.splitlines() if '"task_version":' in ln]
    assert echoed and schema
    for line in echoed + schema:
        assert pack.version in line, line[:120]


def test_the_version_line_reader_still_works(pack):
    """`pe.paper_text.prompt_version` reads the `Version:` line, and pe.prepare
    uses it to assert the substitution happened. A regex that silently stopped
    matching would return "unknown" and disable that assertion."""
    assert prompt_version(pack.spec_path) == pack.placeholders["task_version"]


# --------------------------------------------------------------------------
# pack_sha256: whether the rules were identical, rather than whether the
# author thought they changed
# --------------------------------------------------------------------------

def test_the_pack_hash_covers_the_spec_and_the_pack_files(pack):
    names = {p.relative_to(ROOT).as_posix() for p in pack_files(ROOT)}
    assert "prompt.md" in names
    assert "task/task.yaml" in names
    assert not any("__pycache__" in n for n in names)


def test_the_hash_moves_when_a_rule_moves(tmp_path):
    spec = tmp_path / "prompt.md"
    spec.write_text("Version: {{TASK_VERSION}}\nrule one\n")
    (tmp_path / "task").mkdir()
    (tmp_path / "task" / "task.yaml").write_text("name: t\nversion: 0.0.1\n")
    before = pack_sha256(tmp_path)
    spec.write_text("Version: {{TASK_VERSION}}\nrule one, amended\n")
    assert pack_sha256(tmp_path) != before


def test_the_hash_does_not_depend_on_where_the_skill_is_checked_out(tmp_path):
    """Paths are hashed relative to the root, so two clones of the same rules
    agree. Otherwise the hash answers "which machine" rather than "which rules"."""
    import shutil
    a, b = tmp_path / "a", tmp_path / "b"
    for base in (a, b):
        (base / "task").mkdir(parents=True)
        (base / "prompt.md").write_text("Version: {{TASK_VERSION}}\nrule\n")
        (base / "task" / "task.yaml").write_text("name: t\nversion: 0.0.1\n")
    assert pack_sha256(a) == pack_sha256(b)
    shutil.rmtree(b)


def test_moving_a_rule_between_files_changes_the_hash(tmp_path):
    """Contents alone are not enough: a file split conserves the bytes and still
    changes where a reader has to look, so the path is hashed too."""
    (tmp_path / "task").mkdir()
    (tmp_path / "prompt.md").write_text("Version: {{TASK_VERSION}}\n")
    (tmp_path / "task" / "task.yaml").write_text("name: t\nversion: 0.0.1\nrule: x\n")
    before = pack_sha256(tmp_path)
    (tmp_path / "task" / "task.yaml").write_text("name: t\nversion: 0.0.1\n")
    (tmp_path / "task" / "rules.yaml").write_text("rule: x\n")
    assert pack_sha256(tmp_path) != before


# --------------------------------------------------------------------------
# The pack refuses rather than defaulting
# --------------------------------------------------------------------------

def test_a_missing_pack_is_an_error_not_a_default(tmp_path):
    """A run that cannot say which rules it is applying must not proceed as
    though it could."""
    with pytest.raises(PackError):
        load_pack(tmp_path)


@pytest.mark.parametrize("field", ["name", "version", "spec"])
def test_an_incomplete_pack_names_the_missing_field(field):
    config = {"name": "t", "version": "0.0.1",
              "spec": {"path": "prompt.md",
                       "anchors": {"instruction": "a", "schema_start": "b",
                                   "schema_end": "c"},
                       "placeholders": {"paper_id": "{{P}}", "paper_text": "{{T}}",
                                        "source_ids": "{{S}}",
                                        "task_version": "{{V}}"}}}
    del config[field]
    with pytest.raises(PackError) as exc:
        TaskPack(config, ROOT)
    assert field in str(exc.value)


def test_expected_task_version_is_unknown_only_when_the_pack_is_unreadable():
    assert expected_task_version() == load_pack(ROOT).version


# --------------------------------------------------------------------------
# The 392 records already on disk
# --------------------------------------------------------------------------

def _record(**over):
    record = {
        "task_version": load_pack(ROOT).version, "sources_seen": ["main"],
        "processing_status": "ok", "text_completeness": "full",
        "has_single_cell_assay": "yes", "perturbation_present": "no",
        "perturbation_present_any_assay": "no", "unresolved_reason": "none",
        "consistency_flags": [], "perturbations": [], "suppressed_candidates": [],
        "samples": [], "paper_confidence": 0.9,
    }
    record.update(over)
    return record


def _issues(record, **kw):
    return validate_result(record, {"main": "x"}, 0.85, **kw)["validation"]["issues"]


def test_a_current_record_draws_no_version_complaint():
    assert not [i for i in _issues(_record()) if "version" in i]


def test_a_wrong_version_is_still_rejected():
    issue = next(i for i in _issues(_record(task_version="0.0.1")) if "task_version" in i)
    assert "expected" in issue


def _legacy_record():
    record = _record()
    del record["task_version"]
    record[LEGACY_VERSION_FIELD] = "0.0.7"
    return record


def test_a_pre_0_0_13_record_is_read_as_its_run_version():
    """The 392 records on disk carry `schema_version` 0.0.7 and no
    `task_version`, and re-scoring them was explicitly off the table."""
    claimed, was_legacy = record_version(_legacy_record(), "0.0.12")
    assert (claimed, was_legacy) == ("0.0.12", True)

    validation = validate_result(_legacy_record(), {"main": "x"}, 0.85,
                                 version="0.0.12")["validation"]
    assert validation["task_version"] == "0.0.12"
    assert validation["task_version_source"] == "legacy_schema_version"


def test_the_legacy_flag_survives_a_record_that_has_quotes():
    """A shadowing bug, and the reason identical records disagreed.

    `_normalize_quote_entry` returns a third value that the quote loop unpacks
    into a variable also called `legacy`, so on any paper carrying an evidence
    quote the version flag was overwritten before it was recorded: 187 of the 392
    records reported `task_version_source="record"` while carrying only
    `schema_version`, and the 205 with no quotes at all reported it correctly.
    Records with byte-identical version fields disagreeing is what made it
    findable, so this asserts the two are independent rather than asserting a
    count.
    """
    with_quotes = _legacy_record()
    with_quotes["perturbations"] = [{
        "category": "chemical", "agent": "LPS", "target": "", "modality_detail": "",
        "samples_affected": [], "single_cell_paired": "no", "assay_applied": "",
        "assay_evidence": None, "confidence": 0.5, "reasoning": "",
        "evidence_quotes": [{"source_id": "main", "quote": "treated with LPS"}],
    }]
    validation = validate_result(with_quotes, {"main": "cells were treated with LPS"},
                                 0.85, version="0.0.12")["validation"]
    assert validation["quotes_checked"] == 1
    assert validation["task_version_source"] == "legacy_schema_version"


def test_a_legacy_record_files_no_issue_at_all():
    """The regression that matters, and one I wrote before catching it.

    The first version of this branch filed a "pre-0.0.13 record" note, which put
    one entry on every one of the 392 already-scored records and took the corpus
    issue count from 146 to 532. That is exactly the failure the version collapse
    was undertaken to remove: `validation.issues` is where real problems surface,
    and one entry on every paper makes the column unreadable. A correctly-labelled
    old record is not a problem, so it is recorded structurally and counted by
    pe.summarize instead — the same lesson `suppressed_candidates` taught, that a
    per-paper free-text note is neither enforceable nor countable.
    """
    issues = validate_result(_legacy_record(), {"main": "x"}, 0.85,
                             version="0.0.12")["validation"]["issues"]
    assert not [i for i in issues if "version" in i.lower()], issues


def test_the_legacy_path_changes_nothing_but_the_source_field():
    """Read at the same version, a legacy record and a current one are identical.

    Compared at the PACK's own version on both sides. Handing the current-format
    side an older version would correctly earn it a "superseded" issue, and
    comparing that against a legacy record's silence would be comparing two
    different questions.
    """
    live = load_pack(ROOT).version
    current = validate_result(_record(task_version=live), {"main": "x"}, 0.85,
                              version=live)["validation"]
    legacy = validate_result(_legacy_record(), {"main": "x"}, 0.85,
                             version=live)["validation"]
    assert legacy["task_version_source"] == "legacy_schema_version"
    assert current["task_version_source"] == "record"
    del current["task_version_source"], legacy["task_version_source"]
    assert current == legacy


def test_a_superseded_current_format_record_is_still_flagged():
    """The legacy silence must not extend to a record that DOES carry
    `task_version` and carries the wrong one -- that is a real mismatch."""
    issues = validate_result(_record(task_version="0.0.12"), {"main": "x"}, 0.85,
                             version="0.0.12")["validation"]["issues"]
    assert [i for i in issues if "task_version='0.0.12'" in i]


def test_a_record_with_no_version_at_all_is_an_issue():
    orphan = _record()
    del orphan["task_version"]
    assert record_version(orphan, "0.0.12") == (None, False)
    assert [i for i in _issues(orphan) if "neither" in i]


def test_the_two_version_names_are_written_from_one_variable():
    """`prompt_version` survives as an alias so preserved baselines stay
    comparable. It must never be a second value somebody maintains."""
    validation = validate_result(_record(), {"main": "x"}, 0.85,
                                 version="0.0.13")["validation"]
    assert validation["task_version"] == validation["prompt_version"] == "0.0.13"


def test_the_pack_hash_is_recorded_on_every_record():
    validation = validate_result(_record(), {"main": "x"}, 0.85,
                                 pack_sha256="abc123")["validation"]
    assert validation["pack_sha256"] == "abc123"
