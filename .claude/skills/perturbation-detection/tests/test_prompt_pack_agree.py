"""Every value stated in both prompt.md and the pack must agree.

The 0.0.13 split moved nine closed sets out of `harness/` and into `task/`, which
made them findable but did **not** make them singular: prompt.md states each one
too, because the model has to be told. So the pair survives, and the pair is the
failure mode this repo has now hit twice.

  v0.0.7  one rule stated in three places, changed in two -- the temperature rule
          and the protocol rule contradicted each other, and two runs of one
          paper under one prompt version disagreed because the model was being
          asked to arbitrate.
  v0.0.12 `schema_version` stated in four places, changed in two. The model split
          386/6 on the contradiction and the validator was calibrated to the
          minority.

`task_version` was removed from prompt.md entirely, which is the better fix --
there is nothing left to disagree with. That is not available for the value sets:
the model must be told what `category` may contain, so `record.yaml` and the
spec both hold the list. What is available is a test, and before this file there
was exactly ONE, for `suppressed_candidates[].rule`, written after v0.0.7 taught
the lesson for a single rule. The other eight sets were unguarded.

**Every parser here fails loudly when it matches nothing.** A drift guard that
quietly stops firing is worse than no guard, because the record then looks clean
for the wrong reason -- and the existing single guard is brittle in exactly that
way: it finds its input with a `next()` over lines containing two substrings,
which raises `StopIteration` if the schema block is ever reformatted. Each
assertion below checks it found something before checking that it agrees.

Run: python -m pytest tests/test_prompt_pack_agree.py -q
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.pack import load as load_pack, tables  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def spec() -> str:
    return load_pack(ROOT).spec_path.read_text()


@pytest.fixture(scope="module")
def pack() -> dict:
    return tables(ROOT)


def _schema_block(spec: str) -> str:
    """The `## Output schema` section, which is where the enums are declared.

    Sliced with the pack's own anchors rather than literals, so a spec that
    renames a heading fails here with a message instead of silently narrowing
    every check below to an empty string.
    """
    anchors = load_pack(ROOT).anchors
    start, end = anchors["schema_start"], anchors["schema_end"]
    assert start in spec, f"the spec has no {start!r} heading"
    assert end in spec, f"the spec has no {end!r} heading"
    return spec[spec.index(start):spec.index(end)]


def _declared_enums(spec: str) -> dict[str, list[str]]:
    """Every `"field": "a | b | c"` line in the schema block.

    One generic parser rather than one regex per set: a set added to the schema
    in future is then covered by `test_no_declared_enum_is_unguarded` below
    without anybody remembering to write a test for it.
    """
    block = _schema_block(spec)
    found = {}
    for match in re.finditer(r'"(\w+)":\s*"([^"]*\|[^"]*)"', block):
        values = [v.strip() for v in match.group(2).split("|")]
        # A prose value with spaces in it is a description, not an enum.
        if all(re.fullmatch(r"[\w.]+", v) for v in values):
            found.setdefault(match.group(1), values)
    assert found, ("no `\"field\": \"a | b | c\"` enum lines found in the schema "
                   "block -- the parser has stopped matching, so every check in "
                   "this file would pass on nothing")
    return found


#: pack path -> the field name the spec declares it under.
#: `labels` maps to three fields, all of which must carry the same three values.
GUARDED = {
    "processing_status": "processing_status",
    "text_completeness": "text_completeness",
    "unresolved_reasons": "unresolved_reason",
    "category": "category",
    "suppression_rules": "rule",
    # v0.0.23. `perturbation_present` left LABEL_FIELDS below because it is no
    # longer a tri-state: it alone may be `not_applicable`. Guarded here instead
    # so it is still checked, against its own set rather than against `labels`.
    "determination_labels": "perturbation_present",
    "primary_research": "reports_primary_research",
    # v0.0.25. The defect kinds gate Stage B, so the spec and the pack stating
    # them separately is the shape that split 386 records from 6 at v0.0.12.
    "defect_kinds": "kind",
}
#: Fields that must carry `labels` exactly. `perturbation_present` is NOT one of
#: them any more -- see `determination_labels` above. Widening `labels` itself
#: would have made `has_single_cell_assay: "not_applicable"` legal, which is the
#: over-permission this split exists to avoid.
LABEL_FIELDS = ("has_single_cell_assay", "single_cell_paired", "would_have_paired")


def _pack_set(pack: dict, name: str) -> list[str]:
    rec = pack["record"]
    return {
        "processing_status": rec["run_states"]["processing_status"],
        "text_completeness": rec["run_states"]["text_completeness"],
        "unresolved_reasons": rec["unresolved_reasons"],
        "category": rec["item_array"]["enums"]["category"],
        "suppression_rules": rec["secondary_arrays"][0]["reasons"],
        "determination_labels": rec["determination_labels"],
        "defect_kinds": rec["defect_array"]["kinds"],
        "primary_research": rec["primary_research"],
    }[name]


@pytest.mark.parametrize("pack_name,spec_field", sorted(GUARDED.items()))
def test_a_closed_set_matches_the_spec(pack_name, spec_field, spec, pack):
    """Order matters as well as membership.

    The spec's order is the order a curator reads and the order the prompt
    presents to the model, and `suppression_rules` in particular is documented as
    "the first four arrived with v0.0.9; the last four are older rules" -- a
    reordering would silently invalidate that comment and the
    `reasons_under_review` slice that depends on it.
    """
    declared = _declared_enums(spec)
    assert spec_field in declared, (
        f"the spec no longer declares {spec_field!r} as an enum; either it was "
        f"renamed or the schema line was reformatted past the parser")
    assert list(_pack_set(pack, pack_name)) == declared[spec_field], (
        f"{pack_name} drift:\n  record.yaml: {list(_pack_set(pack, pack_name))}\n"
        f"  {spec_field} in the spec: {declared[spec_field]}")


@pytest.mark.parametrize("field", LABEL_FIELDS)
def test_every_tri_state_field_uses_the_same_labels(field, spec, pack):
    """Four fields, one label set. They drifted apart once already in a different
    guise: `compare.ORDER` and `validate.TRISTATE` held the same three values in
    two different orders, in two modules, with nothing checking either."""
    declared = _declared_enums(spec)
    assert field in declared, f"the spec no longer declares {field!r} as an enum"
    assert list(pack["record"]["labels"]) == declared[field], (
        f"{field} drift:\n  record.yaml labels: {list(pack['record']['labels'])}\n"
        f"  spec: {declared[field]}")


def test_no_declared_enum_is_unguarded(spec, pack):
    """A set added to the spec must be added to this file too.

    The point of the whole exercise: eight of nine sets were unguarded because
    nobody remembered to write the ninth test. This one fails when the spec grows
    an enum the pack does not mirror, so the omission surfaces at the moment it
    is made rather than at the version bump that breaks on it.
    """
    declared = set(_declared_enums(spec))
    accounted = set(GUARDED.values()) | set(LABEL_FIELDS) | {
        # Declared in the spec but deliberately not a pack table:
        "perturbation_present_any_assay",   # a label field, covered by labels
        "is_single_cell_assay",             # ditto, on samples[]
        "perturbed",                        # true|false|"unclear", a tri-state bool
        "source_type",                      # set by the harness, not the model
    }
    unguarded = declared - accounted
    assert not unguarded, (
        f"the spec declares {sorted(unguarded)} and nothing here checks them "
        f"against the pack. Add them to GUARDED (or to the accounted set, with a "
        f"comment saying why the pack does not mirror them).")


def test_required_fields_all_exist_in_the_spec_schema(spec, pack):
    """`harness.pending` re-runs any paper missing one of these, so a required field
    the spec never asks for would re-run every paper forever."""
    block = _schema_block(spec)
    top_level = set(re.findall(r'^  "(\w+)":', block, re.MULTILINE))
    assert top_level, "no top-level schema keys found -- the parser has stopped matching"
    required = set(pack["record"]["required_fields"])
    missing = required - top_level
    assert not missing, (
        f"record.yaml requires {sorted(missing)} but the spec's schema does not "
        f"declare them, so every paper would be re-run as incomplete forever")


def test_rules_under_review_matches_the_spec(spec, pack):
    """The triage-tier-2 subset, stated in the spec's step 10 as prose.

    Not in the schema block, so it needs its own parser -- and it is the set most
    likely to move, because it is defined as "the rules we are still arguing
    about" and shrinks as arguments settle.
    """
    line = next((ln for ln in spec.splitlines()
                 if "boundary is under review" in ln), None)
    assert line, ("the spec no longer contains a line saying which rules' "
                  "boundary is under review; step 10 was reworded past this parser")

    # Cut at the exclusion clause. The same sentence goes on to NAME the settled
    # rules it excludes, so reading the whole line and comparing a prefix -- which
    # is what this did first -- passes when the pack's set SHRINKS: a prefix of
    # four still prefix-matches three. Caught by mutating the pack and finding
    # this the one guard of eight that did not fire.
    # Count-free on purpose. This marker was "The other four rules" until
    # v0.0.15 added a fifth under-review rule and the SETTLED set went from four
    # to four-of-nine -- so the cut marker was itself a stale count, in a test
    # written to catch stale counts.
    marker = "The remaining rules"
    assert marker in line, (
        f"the spec's step-10 line no longer contains {marker!r}, which is where "
        f"the under-review list ends and the excluded list begins")
    reasons = pack["record"]["secondary_arrays"][0]["reasons"]
    declared = [r for r in re.findall(r"`(\w+)`", line[:line.index(marker)])
                if r in reasons]
    under_review = list(pack["record"]["secondary_arrays"][0]["reasons_under_review"])
    assert declared, "no suppression rule names found before the exclusion clause"
    assert declared == under_review, (
        f"reasons_under_review drift:\n  record.yaml: {under_review}\n"
        f"  spec step 10: {declared}")
    # The prose states the count of the OTHER side, so it is a second, independent
    # statement of the same fact and can disagree on its own.
    assert len(reasons) - len(under_review) == 4, (
        f"the spec says \"The other four rules\" are excluded, but the pack has "
        f"{len(reasons)} rules with {len(under_review)} under review, leaving "
        f"{len(reasons) - len(under_review)}")


def test_the_consistency_codes_match_the_spec(spec, pack):
    """CC-1..CC-7 and nothing else. An extra code in the pack would be raised and
    never explained to the model; a missing one would be explained and never
    raised."""
    declared = re.findall(r"^- \*\*(CC-\d+)\.\*\*", spec, re.MULTILINE)
    assert declared, ("no `- **CC-n.**` bullets found -- the Consistency checks "
                      "section was reformatted past this parser")
    assert sorted(pack["decide"]["checks"]) == sorted(declared), (
        f"consistency codes drift:\n  decide.yaml: {sorted(pack['decide']['checks'])}\n"
        f"  spec: {sorted(declared)}")


def test_the_triage_tier_numbers_match_the_spec(spec, pack):
    """The ladder's NUMBERS, against the spec's numbered list.

    The existing ladder test compares the code to itself. This is the half that
    was missing, and the renumber at v0.0.10 -- P2 inserted, the old P2-P5 shifted
    to P3-P6 -- is the documented trap it exists for. Tier 9 is the catch-all and
    is not enumerated in the spec's list.
    """
    step = spec[spec.index("### 10."):]
    step = step[:step.index("### 11.")]
    declared = [int(n) for n in re.findall(r"^(\d+)\.\s", step, re.MULTILINE)]
    assert declared, "no numbered tiers found in step 10 -- parser stopped matching"
    # A vacated slot stays in the spec's numbered list so a reader can see why
    # the ladder has a hole, but it is not a live tier. Slot 3 went this way at
    # 0.0.22; closing the gap instead would renumber, which is the trap the
    # ladder's own note exists to prevent.
    vacant = {int(n) for n in re.findall(r"^(\d+)\.\s+\*\(vacant", step, re.MULTILINE)}
    declared = [n for n in declared if n not in vacant]
    assert vacant, ("no vacant slot found in step 10 -- if slot 3 was refilled or "
                    "the wording changed, this guard needs revisiting")
    tiers = [int(t["n"]) for t in pack["report"]["tiers"] if int(t["n"]) != 9]
    assert tiers == declared, (
        f"triage ladder drift:\n  report.yaml: {tiers}\n  spec step 10: {declared}\n"
        f"A mismatch silently mis-sorts the curator's queue.")


def test_no_tier_reads_the_confidence_number(spec, pack):
    """The inverse of the guard this replaces, and the point of 0.0.22.

    There used to be a two-sided check that `report.yaml: low_confidence_yes`
    and prompt.md step 10 both said 0.6. The threshold is gone: it was the only
    consumer of `paper_confidence` in the pack, and across nine paired
    same-input re-runs 11 of the 16 papers it selected flipped in or out of the
    tier. The field itself stays on the record -- it is the best predictor of a
    determination flip there is -- but nothing routes on it and nothing prints
    it, and this is what keeps it that way.
    """
    assert "low_confidence_yes" not in pack["report"], (
        "the confidence threshold is back in report.yaml. It was removed at "
        "0.0.22 because its tier's membership was ~69% unstable on identical "
        "input; re-adding it needs that measurement redone.")
    step = spec[spec.index("### 10."):]
    step = step[:step.index("### 11.")]
    live = [line for line in step.splitlines()
            if "paper_confidence" in line and "vacant" not in line]
    assert not live, f"step 10 still routes on the confidence number: {live}"
    assert not [c for c in pack["report"]["columns"] if "confidence" in c], (
        "a confidence column is back in the summary CSV")


def _exclusion_table(spec: str) -> dict[str, str]:
    """The eight-row table under "Recording an exclusion", as {rule: promotion}.

    v0.0.14 made this table the single owner of the eight exclusion reasons'
    SCOPES, which the prompt previously stated twice with an overlap: the
    `routine_processing` paragraph and the `sample_handling_protocol` rule both
    claimed a media-brand comparison, a storage-duration series and a
    dissociation-enzyme benchmark, with no tiebreak -- so one population was
    splitting across two buckets run to run (63 papers against 27 on the corpus).

    Owning the scopes means restating the eight NAMES, which is a third
    declaration site beside `record.yaml: reasons` and the schema block's `rule`
    enum. This repo has been burned twice by a value stated in N places and
    changed in fewer, so the duplication is guarded rather than tolerated.
    """
    heading = "**Recording an exclusion from this list.**"
    assert heading in spec, (
        "the spec no longer has the 'Recording an exclusion' section that owns "
        "the eight exclusion reasons; this parser found nothing")
    body = spec[spec.index(heading):]
    rows: dict[str, str] = {}
    in_body = False             # rows only count after the |---|---| separator,
    for line in body.splitlines():  # or the header's own `rule` cell reads as one
        if re.fullmatch(r"\|[\s\-:|]+\|", line):
            in_body = True
            continue
        if not in_body:
            continue
        if not line.startswith("|"):
            break               # table ended
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        name = re.fullmatch(r"`([a-z_]+)`", cells[0])
        if name:
            rows[name.group(1)] = cells[-1]
    assert rows, "found the exclusion section but parsed no `rule` rows out of it"
    return rows


def test_the_exclusion_table_covers_exactly_the_closed_rule_set(spec, pack):
    """One row per reason, no extras, or a rule has no stated scope again."""
    declared = _exclusion_table(spec)
    reasons = list(pack["record"]["secondary_arrays"][0]["reasons"])
    assert sorted(declared) == sorted(reasons), (
        f"exclusion-table drift:\n  table rows: {sorted(declared)}\n"
        f"  record.yaml reasons: {sorted(reasons)}\n"
        f"A reason with no row has no stated scope, which is the overlap "
        f"v0.0.14 removed; a row with no reason cannot be tallied.")


def test_every_exclusion_reason_states_whether_promotion_reaches_it(spec):
    """The 1b fix, held.

    The NOT-list heading used to carry a blanket "unless the paper makes the item
    the manipulated variable", and four of the eight rules under it were flat
    exclusions with no exception -- only ONE of which said so. A paper whose
    studied variable was a reporter construct had the heading and the rule
    pointing opposite ways. Each row now answers the question, so a ninth rule
    added without an answer fails here rather than inheriting a blanket clause
    that v0.0.5 already shipped as a bug.
    """
    for rule, verdict in _exclusion_table(spec).items():
        assert re.match(r"\*\*(Yes|No)\.?\*\*", verdict), (
            f"{rule}: the promotion column says {verdict[:60]!r}, which does not "
            f"start with a bolded Yes or No. Every reason must state whether a "
            f"biological-variable role can promote it out of the NOT list.")


def _exclusion_scope(spec: str, rule: str) -> str:
    """The middle cell -- "what it covers" -- for one exclusion rule.

    `_exclusion_table` above returns the promotion column, because that is what
    every guard before v0.0.23 asked about. The scope column matters too now:
    v0.0.23 resolved a natural infection and a donor's pre-existing medication
    by naming them in the scopes, and the table is documented as "the only place
    their scopes are defined".
    """
    heading = "**Recording an exclusion from this list.**"
    body = spec[spec.index(heading):]
    in_body = False
    for line in body.splitlines():
        if re.fullmatch(r"\|[\s\-:|]+\|", line):
            in_body = True
            continue
        if not in_body:
            continue
        if not line.startswith("|"):
            break
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        name = re.fullmatch(r"`([a-z_]+)`", cells[0])
        if name and name.group(1) == rule:
            return cells[1]
    raise AssertionError(f"no exclusion row named {rule!r}")


def test_the_observational_rule_names_its_one_promotion_route(spec):
    """It used to say "**No** -- nothing was applied, so there is nothing to
    promote", which is true of a donor genotype and false of an infection:
    something WAS applied, just not by the investigators.

    The row is the authoritative statement of every rule's scope, so the route
    has to be stated here. Without it a natural infection has no way out of the
    NOT list, and 34 corpus papers sat on the contradiction -- 21 resolving it
    one way, 13 the other.
    """
    row = _exclusion_table(spec)["observational_disease_state"]
    assert "acquired EXPOSURE whose response is the paper's own question" in row, (
        "the observational rule no longer states the route that promotes it")
    assert "infection the subjects acquired naturally" in _exclusion_scope(
        spec, "observational_disease_state"), (
        "the rule no longer says it covers a naturally acquired infection, which "
        "is the case v0.0.23 resolved")


def test_the_clinical_therapy_rule_points_at_the_axis_sharpening(spec):
    """This is the rule that attracts the error, so the pointer lives on it.

    It must POINT rather than restate: three differently-keyed copies of the
    governing question are what rulings 9-13 were needed to reconcile, and
    `test_the_governing_question_is_stated_exactly_once` holds that line.
    """
    row = _exclusion_table(spec)["incidental_clinical_therapy"]
    assert "Read its first sharpening before promoting anything here" in row, (
        "the clinical-therapy rule no longer points at the axis-versus-subject "
        "sharpening, and grouping-by-therapy is the promotion it wrongly attracts")
    assert "Is the applied thing what the paper is trying to LEARN ABOUT" not in row, (
        "the rule now RESTATES the governing question instead of pointing at it")
    assert "medication the donor was already taking" in _exclusion_scope(
        spec, "incidental_clinical_therapy"), (
        "the rule no longer says it covers a donor's pre-existing medication, "
        "which is the contraception case")
