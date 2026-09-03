"""The seam: `pe/` is the harness, `task/` is the judgment, and this holds the line.

Three layers, and only the top one is about perturbations:

    JUDGMENT   task/   the spec + four lookup tables. SWAP this.
    PLUMBING   pe/     assemble sources, splice the prompt, one call per paper,
                       verify every quote, prune, recompute, tabulate, diff. KEEP.
    TEXT       manuscript_harvest   DOI -> labelled text with provenance. KEEP.

Before the 0.0.13 split, `pe/` was 1,038 task lines to 1,185 generic ones, with
the two interleaved inside four files -- `audit.py` 80% task, `summarize.py` 71%,
`validate.py` 60%. The tests below are what stops that growing back, and the
first is the one that matters: **no module in `pe/` may name this task in code.**

Comments and docstrings are exempt, deliberately. Half of this repo's value is
the record of which DOI taught which rule, and a guard that forced that history
out of the harness would be trading the thing worth keeping for a tidier grep.
The line drawn here is that `pe/` may EXPLAIN what it once knew and may not USE
it.

Run: python -m pytest tests/test_seam.py -q
"""
from __future__ import annotations

import io
import sys
import tokenize
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from task import TABLE_FILES, load as load_pack, tables  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HARNESS = sorted((ROOT / "pe").glob("*.py"))

#: Words that name THIS task rather than any per-paper classification task.
TASK_WORDS = ("perturb", "single_cell", "scrna", "snrna", "assay",
              "suppressed_candidate", "organism", "curator")


def _code_tokens(path: Path):
    """Every token that is not a comment and not a docstring."""
    source = path.read_text()
    previous = None
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type in (tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE,
                          tokenize.INDENT, tokenize.DEDENT):
            continue
        is_docstring = (
            token.type == tokenize.STRING
            and token.string.lstrip("rbuRBUf").startswith(('"""', "'''"))
            and (previous is None or previous.type == tokenize.INDENT
                 or previous.string in (":", ""))
        )
        if not is_docstring:
            yield token
        previous = token


def test_the_harness_does_not_name_the_task_in_code():
    """The seam itself.

    A hit here means a task word has got back into an identifier, a string
    literal or a dict key in `pe/`, which is how the layers grew together the
    first time. The fix is never to add a word to the allow-list -- it is to move
    the value into `task/` and read it from there.
    """
    offenders: dict[str, list[str]] = {}
    for path in HARNESS:
        for token in _code_tokens(path):
            lowered = token.string.lower()
            hit = next((w for w in TASK_WORDS if w in lowered), None)
            if hit:
                offenders.setdefault(path.name, []).append(
                    f"line {token.start[0]}: {token.string[:60]}")
    assert not offenders, (
        "the harness names the task in code:\n"
        + "\n".join(f"  {f}: {', '.join(v)}" for f, v in offenders.items())
        + "\nMove the value into task/ and read it from the pack. Adding a word "
          "to TASK_WORDS' allow-list would be conceding the seam.")


def test_runroot_takes_its_names_from_the_pack_with_no_fallback():
    """The seam's easiest place to concede, and the one it was conceded in first.

    `output_name(key, fallback)` had a fallback argument, and every caller passed
    this task's own filename -- so `perturbations_summary.csv` was back in the
    harness as a string literal, in the one spot nobody greps. There is no
    fallback now: an unreadable pack is an error, not a run that quietly writes
    to the previous task's paths.
    """
    from pe.runroot import ENV_VAR, output_name
    outputs = load_pack(ROOT)._config["outputs"]
    assert ENV_VAR == outputs["env_var"]
    assert output_name("summary_csv") == outputs["summary_csv"]
    with pytest.raises(KeyError):
        output_name("a_key_no_pack_declares")


def test_the_pack_supplies_everything_the_harness_asks_of_it():
    """The interface, asserted as a list rather than discovered by a crash.

    This is the checklist a second pack has to satisfy, so it is stated in one
    place and tested. A pack missing any of these fails at import today; the
    point of naming them is that a pack AUTHOR can read the requirement without
    running anything.
    """
    import task.change as change
    import task.report as report
    import task.rules as rules
    import task.screens as screens

    for module, names in (
        (rules, ("decide", "checks", "metrics", "validate_items",
                 "validate_secondary", "extra_field_issues", "progress_line")),
        (report, ("COLUMNS", "TIERS", "triage_priority", "row_for", "counters")),
        (screens, ("SCREENS", "render")),
        (change, ("ORDER", "CLASS_LABELS", "UNEXPLAINED", "PRIMARY_FIELD",
                  "classify", "determination_inputs", "render_paper",
                  "render_unchanged")),
    ):
        missing = [n for n in names if not hasattr(module, n)]
        assert not missing, f"{module.__name__} is missing {missing}"


def test_all_four_tables_exist_and_are_named_for_what_they_hold():
    """The four the plan named: what counts, how to decide, what to read first,
    what counts as a change."""
    loaded = tables(ROOT)
    assert set(loaded) == set(TABLE_FILES)
    assert {"labels", "required_fields", "item_array"} <= set(loaded["record"])
    assert {"inputs", "cap", "checks"} <= set(loaded["decide"])
    assert {"tiers", "columns", "screens", "signals"} <= set(loaded["report"])
    assert {"classes", "order", "match"} <= set(loaded["change"])


def test_the_diff_and_the_decision_read_the_same_inputs():
    """Stated in decide.yaml, used by task/change.py, and asserted at import
    there too -- restated here so the failure names the reason rather than
    arriving as a PackError from an import.

    If they ever disagree, `pe.compare` reports a movement as UNEXPLAINED while
    the input that moved is sitting in plain sight, and UNEXPLAINED is the only
    signal in the pipeline that says a human must look before the corpus run.
    """
    from task.change import determination_inputs
    declared = set(tables(ROOT)["decide"]["inputs"])
    assert set(determination_inputs({})) == declared


def test_every_rule_bearing_file_is_in_the_pack_hash():
    """A rule the hash does not cover is a rule two runs can differ on silently."""
    from task import pack_files
    covered = {p.relative_to(ROOT).as_posix() for p in pack_files(ROOT)}
    expected = {"prompt.md"} | {f"task/{f}" for f in TABLE_FILES.values()} | {
        "task/__init__.py", "task/rules.py", "task/report.py", "task/screens.py",
        "task/change.py", "task/task.yaml"}
    assert expected <= covered, f"not hashed: {sorted(expected - covered)}"


def test_the_pack_names_the_outputs_so_a_second_pack_gets_its_own(tmp_path):
    """Two packs in one repo must not share a run root, or each would read the
    other's papers as pending and `pe.pending` would report nonsense."""
    outputs = load_pack(ROOT)._config.get("outputs") or {}
    for key in ("run_root_subdir", "env_var", "per_paper_file", "summary_csv",
                "review_txt", "diff_txt"):
        assert outputs.get(key), f"task.yaml outputs is missing {key!r}"


@pytest.mark.parametrize("table", sorted(TABLE_FILES))
def test_no_table_is_empty(table):
    """An empty table loads without complaint and silently disables whatever it
    was supposed to control. Every one of these has required keys asserted
    above; this catches the file being truncated to nothing."""
    assert tables(ROOT)[table], f"{TABLE_FILES[table]} is empty"
