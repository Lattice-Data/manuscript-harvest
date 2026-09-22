"""The ledger check, one test per way it could pass over nothing.

Every assertion here is about a failure firing rather than a success happening,
because the failure modes are the point. A ground-truth check that cannot fail
is worse than no check: it converts "nobody looked" into a green tick.

Three of these encode mistakes that were actually made:

  * `not_applicable` read as `no`, because one is a prefix of the other. The
    first program to read this ledger did exactly that and reported a
    disagreement that did not exist.
  * a gate that graded zero rows and exited 0 -- the shape `harness/runstate.py`
    exists to prevent one layer down, and that the v0.0.25 defect gate shipped
    with.
  * resolving a changed ruling by preferring the later date, which is the
    program deciding which reading of a paper is right.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.ground_truth import (  # noqa: E402
    DECLINED,
    EXPECTED_TO_DIFFER,
    GRADED,
    LedgerError,
    check,
    conflicting,
    live,
    main,
    parse_ledger,
    unsealed_changes,
)
from harness.pack import load as load_pack, pack_files  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
VERDICTS = ["yes", "no", "unclear", "not_applicable"]
KINDS = [GRADED, EXPECTED_TO_DIFFER, "partial", DECLINED]

HEAD = "| # | paper | verdict | kind | date | supersedes | sealed |\n|---|---|---|---|---|---|---|\n"


def ledger(*rows: str) -> str:
    return "prose above\n\n" + HEAD + "".join(rows) + "\nprose below\n"


def row(num, paper="10.1/a", verdict="no", kind=GRADED, date="2026-01-01",
        supersedes="-", sealed="-") -> str:
    return f"| {num} | `{paper}` | {verdict} | {kind} | {date} | {supersedes} | {sealed} |\n"


# -- the parser -------------------------------------------------------------

def test_not_applicable_is_not_read_as_no():
    """The bug this file exists for. `no` is a prefix of `not_applicable`, so a
    parser matching an alternation in the wrong order silently downgrades one to
    the other -- and then reports a disagreement against a paper that agreed."""
    entries = parse_ledger(ledger(row(1, verdict="not_applicable")),
                           VERDICTS, KINDS)
    assert entries[0].verdict == "not_applicable"


@pytest.mark.parametrize("verdict", ["No", "no?", "**no**", "no - re-confirms 8",
                                     "not applicable", "maybe", ""])
def test_a_verdict_outside_the_closed_set_is_refused(verdict):
    """Commentary in the verdict cell is how the old table became unreadable:
    it held `no - **not adopted**, see 16 below` and `**not_applicable**` in the
    same column. Emphasis is stripped; prose is not tolerated."""
    if verdict == "**no**":                      # emphasis alone is fine
        assert parse_ledger(ledger(row(1, verdict=verdict)),
                            VERDICTS, KINDS)[0].verdict == "no"
        return
    with pytest.raises(LedgerError, match="verdict"):
        parse_ledger(ledger(row(1, verdict=verdict)), VERDICTS, KINDS)


def test_an_unknown_kind_is_refused():
    with pytest.raises(LedgerError, match="kind"):
        parse_ledger(ledger(row(1, kind="advisory")), VERDICTS, KINDS)


def test_a_duplicate_entry_number_is_refused():
    """`supersedes` points by number, so two rows sharing one make the pointer
    ambiguous and the resolution arbitrary."""
    with pytest.raises(LedgerError, match="already used"):
        parse_ledger(ledger(row(1), row(1, paper="10.1/b")), VERDICTS, KINDS)


def test_a_row_that_is_not_seven_cells_is_refused():
    with pytest.raises(LedgerError, match="seven pipe-delimited"):
        parse_ledger(ledger("| 1 | `10.1/a` | no |\n"), VERDICTS, KINDS)


def test_a_non_numeric_supersedes_is_refused():
    with pytest.raises(LedgerError, match="supersedes"):
        parse_ledger(ledger(row(1, supersedes="see above")), VERDICTS, KINDS)


def test_a_header_with_no_rows_is_an_error_not_an_empty_pass():
    with pytest.raises(LedgerError, match="no rows"):
        parse_ledger(ledger(), VERDICTS, KINDS)


def test_a_missing_table_is_an_error():
    with pytest.raises(LedgerError, match="no ledger table"):
        parse_ledger("nothing but prose\n", VERDICTS, KINDS)


def test_two_candidate_tables_are_refused_rather_than_merged():
    """The prose below the ledger carries other tables. If one ever grew this
    header, silently reading both would mix a worked example into the graded
    set."""
    with pytest.raises(LedgerError, match="Exactly one"):
        parse_ledger(ledger(row(1)) + "\n" + HEAD + row(2), VERDICTS, KINDS)


def test_the_prose_below_the_table_is_not_scanned():
    """A blank line ends the table. Everything after it is reasoning."""
    text = ledger(row(1)) + "\n| # | not the header | x |\n"
    assert len(parse_ledger(text, VERDICTS, KINDS)) == 1


# -- supersession and sealing ----------------------------------------------

def test_a_changed_verdict_is_reported_rather_than_resolved_by_date():
    """The curator's rule: only a human seals a reversal. Preferring the later
    date would be this program choosing which reading of a paper is right."""
    entries = parse_ledger(
        ledger(row(1, verdict="no"),
               row(2, verdict="yes", supersedes=1)), VERDICTS, KINDS)
    changes = unsealed_changes(entries)
    assert len(changes) == 1
    assert (changes[0][0].number, changes[0][1].number) == (1, 2)


def test_a_sealed_change_is_accepted():
    entries = parse_ledger(
        ledger(row(1, verdict="no"),
               row(2, verdict="yes", supersedes=1, sealed="2026-09-21 idan")),
        VERDICTS, KINDS)
    assert unsealed_changes(entries) == []


def test_re_confirming_the_same_verdict_needs_no_seal():
    """A second reader reaching the same answer is evidence, not a change of
    mind. Rulings 18 and 22 are exactly this."""
    entries = parse_ledger(
        ledger(row(1, verdict="no"), row(2, verdict="no", supersedes=1)),
        VERDICTS, KINDS)
    assert unsealed_changes(entries) == []


def test_superseding_an_entry_that_is_not_there_is_refused():
    with pytest.raises(LedgerError, match="not in the ledger"):
        unsealed_changes(parse_ledger(ledger(row(1, supersedes=99)),
                                      VERDICTS, KINDS))


def test_superseding_a_different_paper_is_refused():
    """A typo in `supersedes` would otherwise retire a ruling on an unrelated
    paper, and the retirement is invisible -- `live()` simply stops returning
    it."""
    entries = parse_ledger(
        ledger(row(1, paper="10.1/a"), row(2, paper="10.1/b", supersedes=1)),
        VERDICTS, KINDS)
    with pytest.raises(LedgerError, match="a different paper"):
        unsealed_changes(entries)


def test_only_the_latest_entry_on_a_paper_is_live():
    entries = parse_ledger(ledger(row(1), row(2, supersedes=1)), VERDICTS, KINDS)
    assert [e.number for e in live(entries)] == [2]


# -- grading ----------------------------------------------------------------

def _corpus(tmp_path, papers: dict, result_file="r.json") -> Path:
    for paper, verdict in papers.items():
        d = tmp_path / paper.replace("/", "_") / "extracted"
        d.mkdir(parents=True)
        body = "{}" if verdict is None else '{"v": "%s"}' % verdict
        (d / result_file).write_text(body)
    return tmp_path


def test_a_binding_disagreement_is_a_failure(tmp_path):
    entries = parse_ledger(ledger(row(1, verdict="no")), VERDICTS, KINDS)
    result = check(entries, _corpus(tmp_path, {"10.1/a": "yes"}), "v", "r.json")
    assert len(result["disagree"]) == 1


def test_an_out_of_scope_disagreement_is_not_a_failure(tmp_path):
    """Rulings 4 and 23. The classifier is *expected* to differ, because the
    ruling rests on the species of the deposit and on collection membership --
    neither of which is in the paper. Grading these would drive the criteria
    toward guessing at both."""
    entries = parse_ledger(
        ledger(row(1, verdict="no", kind=EXPECTED_TO_DIFFER)), VERDICTS, KINDS)
    result = check(entries, _corpus(tmp_path, {"10.1/a": "yes"}), "v", "r.json")
    assert result["disagree"] == []
    assert len(result["differ_expected"]) == 1


def test_a_not_adopted_ruling_is_not_graded(tmp_path):
    entries = parse_ledger(ledger(row(1, verdict="no", kind=DECLINED)),
                           VERDICTS, KINDS)
    result = check(entries, _corpus(tmp_path, {"10.1/a": "yes"}), "v", "r.json")
    assert result["disagree"] == []
    assert len(result["declined"]) == 1


def test_a_binding_paper_missing_from_the_corpus_is_not_a_pass(tmp_path):
    """The quiet way this check rots: papers leave the corpus, their rows stop
    being compared, and the summary still reads "0 disagree"."""
    entries = parse_ledger(ledger(row(1)), VERDICTS, KINDS)
    result = check(entries, _corpus(tmp_path, {}), "v", "r.json")
    assert len(result["absent"]) == 1
    assert result["disagree"] == []


def test_a_result_file_with_no_verdict_field_counts_as_absent(tmp_path):
    entries = parse_ledger(ledger(row(1)), VERDICTS, KINDS)
    result = check(entries, _corpus(tmp_path, {"10.1/a": None}), "v", "r.json")
    assert len(result["absent"]) == 1


def test_unparseable_json_is_raised_rather_than_swallowed(tmp_path):
    entries = parse_ledger(ledger(row(1)), VERDICTS, KINDS)
    d = tmp_path / "10.1_a" / "extracted"
    d.mkdir(parents=True)
    (d / "r.json").write_text("{not json")
    with pytest.raises(LedgerError):
        check(entries, tmp_path, "v", "r.json")


# -- the command ------------------------------------------------------------

def test_the_command_requires_a_corpus():
    """config.yaml records why: the old default resolved against the CWD, where
    a stale 382-paper copy sits, so it could only fire when someone forgot the
    flag and then graded a smaller corpus."""
    with pytest.raises(SystemExit):
        main([])


def test_the_command_fails_on_a_corpus_that_is_not_there(tmp_path):
    assert main(["--corpus", str(tmp_path / "nope")]) == 2


def test_the_real_ledger_grades_something_and_exits_cleanly_or_says_why(tmp_path):
    """Against the real ledger and the real corpus if one is present. The
    assertion is not "it passes" -- it is that it reaches a verdict for a
    non-zero number of binding rows, because zero graded rows exiting 0 is the
    failure this whole file guards."""
    corpus = ROOT.parent.parent.parent / "corpus"
    if not corpus.is_dir():
        pytest.skip("no local corpus")
    spec = load_pack().ground_truth
    entries = parse_ledger((ROOT / spec["path"]).read_text(),
                           list(spec["verdicts"]), list(spec["kinds"]))
    result = check(entries, corpus, spec["verdict_field"], spec["result_file"])
    assert len(result["graded"]) >= 15, len(result["graded"])
    assert not result["absent"], [r["entry"].number for r in result["absent"]]


def test_the_real_ledger_parses_and_every_kind_is_declared():
    spec = load_pack().ground_truth
    entries = parse_ledger((ROOT / spec["path"]).read_text(),
                           list(spec["verdicts"]), list(spec["kinds"]))
    assert len(entries) >= 26
    assert {e.kind for e in entries} <= set(spec["kinds"])
    assert any(e.kind == GRADED for e in entries)


def test_the_ledger_is_not_in_the_pack_hash():
    """Evidence about the rules, not a rule. Hashing it would mark every stored
    record as produced under different rules each time a paper is ruled on --
    the opposite of what `pack_sha256` is for."""
    spec = load_pack().ground_truth
    hashed = {p.relative_to(ROOT).as_posix() for p in pack_files(ROOT)}
    assert spec["path"] not in hashed


# -- the gate, against the real ledger --------------------------------------

def test_no_unsealed_change_is_outstanding():
    """**This is the gate, and it needs no corpus, so it runs in CI.**

    A reversal nobody has approved is the one thing this check refuses to decide
    for itself. Sealing is a sentence in the `sealed` column; until then a red
    test is the correct state, because the ledger is claiming two different
    answers for one paper and the prose has not said which governs.
    """
    spec = load_pack().ground_truth
    entries = parse_ledger((ROOT / spec["path"]).read_text(),
                           list(spec["verdicts"]), list(spec["kinds"]))
    outstanding = [
        f"{later.paper}: entry {earlier.number} ruled {earlier.verdict!r} "
        f"({earlier.date}), entry {later.number} ruled {later.verdict!r} "
        f"({later.date})"
        for earlier, later in unsealed_changes(entries)]
    assert not outstanding, (
        "unsealed change(s) in the ledger -- approve one reading and write it "
        f"into the later row's `sealed` cell: {outstanding}")


def test_every_binding_ruling_still_holds():
    """The regression check itself. Skips without a local corpus, which is why
    the test above carries the CI-visible half of the gate."""
    corpus = ROOT.parent.parent.parent / "corpus"
    if not corpus.is_dir():
        pytest.skip("no local corpus")
    spec = load_pack().ground_truth
    entries = parse_ledger((ROOT / spec["path"]).read_text(),
                           list(spec["verdicts"]), list(spec["kinds"]))
    result = check(entries, corpus, spec["verdict_field"], spec["result_file"])
    assert not result["disagree"], [
        (r["entry"].number, r["entry"].paper, r["entry"].verdict, r["found"])
        for r in result["disagree"]]


# -- the hole the adversarial pass found ------------------------------------

def test_two_live_paper_level_rulings_on_one_paper_are_flagged():
    """The way to defeat `unsealed_changes` is to forget the link.

    Add a second ruling on a paper already in the ledger, leave `supersedes` at
    `-`, and both entries stay live. If they disagree the grader reports one as
    a criteria bug -- pointing at the prompt for what is a ledger mistake. This
    was a real hole in the first version of this module, found by attacking it
    rather than by a test failing.
    """
    entries = parse_ledger(
        ledger(row(1, verdict="no"), row(2, verdict="yes")), VERDICTS, KINDS)
    assert unsealed_changes(entries) == []          # the link was never made
    clashes = conflicting(entries)
    assert len(clashes) == 1
    assert [e.number for e in clashes[0]] == [1, 2]


def test_agreeing_duplicates_are_flagged_too():
    """Two live entries saying the same thing double-count the paper and mean
    the next reader cannot tell which one a `supersedes` should point at."""
    entries = parse_ledger(
        ledger(row(1, verdict="no"), row(2, verdict="no")), VERDICTS, KINDS)
    assert len(conflicting(entries)) == 1


def test_linking_them_clears_the_clash():
    entries = parse_ledger(
        ledger(row(1, verdict="no"),
               row(2, verdict="yes", supersedes=1, sealed="2026-09-22 x")),
        VERDICTS, KINDS)
    assert conflicting(entries) == []
    assert unsealed_changes(entries) == []


def test_a_partial_may_sit_beside_a_paper_level_ruling():
    """Settling a sub-question is not answering the paper, so these coexist."""
    entries = parse_ledger(
        ledger(row(1, verdict="no", kind="partial"), row(2, verdict="yes")),
        VERDICTS, KINDS)
    assert conflicting(entries) == []


def test_binding_and_out_of_scope_on_one_paper_clash():
    """Both assert a verdict for the whole paper, so only one can be live."""
    entries = parse_ledger(
        ledger(row(1, verdict="no", kind=EXPECTED_TO_DIFFER),
               row(2, verdict="yes")), VERDICTS, KINDS)
    assert len(conflicting(entries)) == 1


def test_the_real_ledger_has_no_clashing_live_rulings():
    spec = load_pack().ground_truth
    entries = parse_ledger((ROOT / spec["path"]).read_text(),
                           list(spec["verdicts"]), list(spec["kinds"]))
    clashes = conflicting(entries)
    assert not clashes, [[e.number for e in g] for g in clashes]


def test_a_seal_must_look_like_an_approval():
    """Any non-empty string used to seal a reversal, so a stray character in
    the last column silently approved one -- defeating, by typo, the single
    thing this module refuses to decide for itself."""
    with pytest.raises(LedgerError, match="seal records"):
        parse_ledger(ledger(row(1, verdict="no"),
                            row(2, verdict="yes", supersedes=1, sealed="x")),
                     VERDICTS, KINDS)


def test_a_well_formed_seal_is_accepted():
    entries = parse_ledger(
        ledger(row(1, verdict="no"),
               row(2, verdict="yes", supersedes=1, sealed="2026-09-22 idan")),
        VERDICTS, KINDS)
    assert entries[1].sealed == "2026-09-22 idan"


def test_a_blank_line_inside_the_table_is_refused_not_ignored():
    """A blank line ends the table, so one in the middle drops every row below
    it and the summary still reads "0 disagree". Silent truncation is the worst
    failure this check has, because it looks exactly like success."""
    text = ledger(row(1)) .replace("\nprose below\n", "\n") + row(2, paper="10.1/b")
    with pytest.raises(LedgerError, match="fall after the blank line"):
        parse_ledger(text, VERDICTS, KINDS)
