"""The spec must not say two different things about one case.

`test_prompt_pack_agree.py` guards the values prompt.md shares with the tables.
This file guards prompt.md against ITSELF, which is where v0.0.14's findings
were: fourteen places the written spec answered one question twice.

**Why this is worth a test file rather than a careful read.** The repo has
measured the cost of an internal contradiction twice, and both times the model
arbitrated it rather than failing:

  v0.0.7   one rule stated in three places and changed in two -- two runs of the
           same paper under the same prompt returned different determinations.
  v0.0.12  one version stated in four places and changed in two -- 386 of 392
           records followed the schema example, 6 followed the instruction line,
           and the validator was calibrated to the minority.

A contradiction is therefore not a documentation defect here. It is a
reproducibility defect with a measured blast radius, and the model will not
report it: asked to obey two rules, it picks one silently.

**Every parser below fails loudly when it matches nothing.** A guard that
quietly stops firing leaves the spec looking clean for the wrong reason, which
is the failure mode of the guard it replaced.

Run: python -m pytest tests/test_spec_self_consistency.py -q
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pe.pack import load as load_pack, tables  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def spec() -> str:
    return load_pack(ROOT).spec_path.read_text()


@pytest.fixture(scope="module")
def model_facing(spec) -> str:
    """Only the text the model is actually handed.

    Two blocks: the instruction prompt and the output schema, sliced with the
    pack's own anchors rather than literals. Everything else in the file -- the
    changelog, the batch wrapper spec, the validation loop -- is for a human, and
    a contradiction there costs a reader's time rather than a determination.
    """
    pack = load_pack(ROOT)
    a = pack.anchors
    instruction = spec[spec.index(a["instruction"]):spec.index(a["schema_start"])]
    schema = spec[spec.index(a["schema_start"]):spec.index(a["schema_end"])]
    assert len(instruction) > 5000 and len(schema) > 2000, (
        "the anchors sliced almost nothing; this fixture is not looking at the "
        "text it claims to")
    return instruction + schema


def _section(text: str, start: str, end: str) -> str:
    assert start in text, f"the spec no longer contains {start!r}; parser found nothing"
    body = text[text.index(start):]
    return body[:body.index(end)] if end in body else body


# --------------------------------------------------------------------------
# The NOT list: one blanket clause that four rules under it contradicted.
# --------------------------------------------------------------------------

def test_the_not_list_heading_carries_no_blanket_promotion_clause(spec):
    """v0.0.5 shipped this and v0.0.6 named it, for one rule only.

    The heading qualified the WHOLE list with "unless the paper makes the item
    the manipulated variable" -- and in a pipeline-benchmarking paper the
    protocol *is* the manipulated variable, so the clause promoted exactly the
    cases the list exists to exclude. Four rules under it were flat exclusions
    and only ONE said so, so a paper whose studied variable was a reporter
    construct had the heading and the rule pointing opposite ways.

    The heading now states the biological/technical distinction and defers to the
    per-rule table. It must not go back to qualifying the list as a whole.
    """
    heading = [ln for ln in spec.splitlines()
               if ln.startswith("#### NOT perturbations by themselves")]
    assert heading, "the NOT-list heading is gone or was renamed; parser found nothing"
    assert len(heading) == 1, f"the NOT-list heading appears {len(heading)} times"
    assert "unless" not in heading[0].lower(), (
        f"the NOT-list heading carries a blanket qualifier again: {heading[0]!r}. "
        f"Promotion is stated per rule, in the table under 'Recording an "
        f"exclusion', because a blanket clause promoted the benchmarking cases "
        f"the list exists to exclude.")


def test_the_two_reagent_rules_do_not_claim_the_same_benchmark_cases(spec):
    """The 1a finding: one population splitting across two buckets.

    `routine_processing`'s paragraph said its reagents "become entries only in a
    paper that makes them its studied variable -- a media-brand comparison, a
    storage-duration series, a dissociation-enzyme benchmark", and
    `sample_handling_protocol` claimed those same three cases. Both fired on the
    corpus (63 papers against 27), so the tally the eight-value scheme exists to
    produce was splitting one population in two, run to run.
    """
    section = _section(spec, "**Recording an exclusion from this list.**",
                       "### Rules for tricky cases")
    row = [ln for ln in section.splitlines() if ln.startswith("| `routine_processing`")]
    assert row, "no `routine_processing` row in the exclusion table; parser found nothing"
    claimed = [phrase for phrase in ("media-brand comparison", "storage-duration series",
                                     "dissociation-enzyme benchmark")
               if phrase in row[0]]
    assert not claimed, (
        f"`routine_processing` claims {claimed} again — those are "
        f"`sample_handling_protocol`'s, which owns the benchmarking shape. Two "
        f"rules claiming one case is what split 63 papers from 27.")


# --------------------------------------------------------------------------
# Step 1 / Step 2 / Step 0: instructions that contradicted required fields.
# --------------------------------------------------------------------------

def test_step_1_does_not_stop_the_model_before_step_2(spec):
    """The 1d finding, dormant on this corpus and not on the next one.

    Step 1 said "do not proceed to perturbation matching" on a no-assay paper,
    while `perturbation_present_any_assay` is defined over Step 2 REGARDLESS of
    assay and `suppressed_candidates` is required. Such a paper had to either
    disobey Step 1 or return a field the spec calls required. Zero of 392 papers
    reach it -- the corpus is pre-filtered to single-cell papers -- so this is a
    latent gap, and it matters for any pack whose equivalent step fires often.
    """
    step = _section(spec, "## Step 1:", "## Step 2:")
    assert "do not proceed to perturbation matching" not in step, (
        "Step 1 tells the model to stop before Step 2 again. Two required fields "
        "are defined over Step 2 regardless of assay, so a paper stopped here "
        "cannot answer its own schema.")
    assert "Do still complete Step 2" in step, (
        "Step 1 no longer tells the model to complete Step 2 on a no-assay paper; "
        "the instruction that replaced 'do not proceed' has been lost")


def test_step_2_does_not_claim_its_criteria_are_unchanged(spec):
    """Its criteria changed at v0.0.6, v0.0.7, v0.0.9, v0.0.10 and v0.0.11.

    The heading carried "(same criteria as before)" from v0.0.3 while the
    changelog recorded five subsequent changes to exactly those criteria -- and
    it sits inside the text handed to the model, telling it not to expect the
    boundary rules 13 lines below.
    """
    headings = [ln for ln in spec.splitlines() if ln.startswith("## Step 2:")]
    assert headings, "the Step 2 heading is gone; parser found nothing"
    assert "same criteria" not in headings[0], (
        f"Step 2 claims unchanged criteria again: {headings[0]!r}")


def test_a_failed_extraction_is_exempt_from_the_confidence_rubric(spec):
    """The 1e finding: two meanings of one word.

    Step 0 mandates `paper_confidence` = 0.0 on a failed extraction. The rubric
    defines confidence as "how likely a careful curator would assign the value
    you assigned" -- and any curator reading an access-denied page would agree
    completely, which is ~1.0. The two statements were using "confidence" for
    different things, so one of them has to say which.
    """
    step0 = _section(spec, "## Step 0:", "## Step 1:")
    rubric = _section(spec, "## Confidence rubric", "## Determination logic")
    assert "sentinel" in step0, (
        "Step 0 mandates 0.0 without saying it is a sentinel rather than a rubric "
        "score, which the rubric would put near 1.0")
    assert "Exempt" in rubric and "sentinel" in rubric, (
        "the rubric no longer exempts a failed extraction, so it again implies a "
        "near-1.0 confidence for the value Step 0 mandates as 0.0")


def test_the_partial_text_ceiling_applies_only_to_negatives(spec):
    """The 1f finding, measured: three positives pinned at exactly 0.38.

    Stage B caps NEGATIVES on degraded text and deliberately leaves positives
    alone -- missing text can hide the sentence that would pair a perturbation
    but cannot invent one. The rubric's low band contradicted that by capping
    "any determination" on partial text at 0.39, which routed every positive on
    partial text into triage tier 3 by rubric rather than by any judgment about
    the paper. All three such papers sat at the ceiling, not at the evidence.
    """
    rubric = _section(spec, "## Confidence rubric", "## Determination logic")
    assert "partial" in rubric, "the rubric no longer mentions partial text"
    assert not re.search(r"Any determination made on `processing_status` = \"partial\"",
                         rubric), (
        "the rubric caps ANY determination on partial text again, contradicting "
        "Stage B's deliberate asymmetry")
    assert "does not apply to a \"yes\"" in rubric, (
        "the rubric no longer states that the partial-text ceiling spares "
        "positives; Stage B's asymmetry is unstated again")


# --------------------------------------------------------------------------
# The clinical-therapy rule: three sites, and only one had condition (iii).
# --------------------------------------------------------------------------

def test_the_report_rule_precedence_note_names_the_third_condition(spec):
    """The report rule was stated in its pre-v0.0.11 form where it wins.

    The `suppressed_candidates` precedence note said report rules beat NOT rules
    and gave "a named therapy tied to specific sequenced samples" as one -- which
    is conditions (i) and (ii) standing alone, exactly the test v0.0.11 replaced
    because it returned the wrong answer on `10.1038/s41467-025-65049-8`. So a
    setting-type therapy tied to sequenced samples had an explicit precedence
    claim on one side and an explicit suppression order on the other.
    """
    note = [ln for ln in spec.splitlines()
            if ln.startswith("- **`suppressed_candidates` never takes anything away")]
    assert note, "the precedence note is gone or was reworded past this parser"
    assert "Report rules win" in note[0], "the precedence note no longer states precedence"
    assert "(iii)" in note[0] or "treats as its variable" in note[0].lower(), (
        "the precedence note states the clinical-therapy report rule without its "
        "third condition, so it again overrides the governing question that "
        "curator ruling 2 turns on")


def test_only_the_incidental_rule_states_its_own_signal_threshold(spec):
    """One test, two thresholds: "any one is enough" against "all three are met".

    They part company on every cohort that trips some but not all three signals
    -- the majority case, and the one worked example 5 is built on. Nothing said
    which governed. The rule keeps its threshold; no other line may restate it.
    """
    rule = _section(spec, "- **Incidental treatment heterogeneity", "- **THE GOVERNING")
    assert "any one of which is enough" in rule, (
        "the incidental rule no longer states its own threshold")
    others = spec.replace(rule, "")
    assert "the three signals in that rule are met" not in others, (
        "a second line states the incidental threshold as conjunctive again, "
        "against the rule's own disjunctive test")


def test_worked_example_5_is_decided_under_the_third_condition(spec):
    """Every clinical-therapy precedent in the file predated (iii).

    Example 5 resolved on (i) and (ii) alone -- and its own stated axis, tumour
    versus adjacent normal, is one of the signals the spec lists as evidence that
    the therapy is the SETTING. It is the largest suppression boundary in the
    corpus (86 papers) and the subject of curator ruling 2.
    """
    examples = _section(spec, "## Worked examples of the pairing rule", "## Resolution")
    five = [ln for ln in examples.splitlines() if ln.startswith("5. ")]
    assert five, "worked example 5 is gone or was renumbered; parser found nothing"
    assert "(iii)" in five[0], (
        "worked example 5 does not apply condition (iii), so the file's "
        "clinical-therapy precedent again predates the rule that governs it")


def test_no_worked_example_count_is_claimed(spec):
    """It said "All five examples" under seven of them.

    A stated count is a second declaration of something already countable, and
    this one had been wrong since the sixth example was added. The line now
    describes the examples without numbering them.
    """
    examples = _section(spec, "## Worked examples of the pairing rule", "## Resolution")
    stale = re.search(r"All (five|six|seven|eight|nine|ten|\d+) examples", examples)
    assert not stale, (
        f"the worked-example section claims a count again ({stale.group(0)!r}); "
        f"the last one went stale as soon as an example was added")


# --------------------------------------------------------------------------
# Version numbers and record shape.
# --------------------------------------------------------------------------

def test_the_model_facing_text_names_no_second_version_number(model_facing):
    """0.0.13 collapsed `prompt_version` + `schema_version` into one number.

    It removed the four DECLARATION sites, and left prose in the model's own text
    still using the abolished pair -- a section header reading "(schema 0.0.7,
    prompt v0.0.12)" and two "New in schema 0.0.6/0.0.7" notes inside the schema
    block. `tests/test_task_version.py` guards the declarations; this guards the
    prose, because the model reads both and the 386/6 split came from exactly
    this kind of disagreement about which number is in force.
    """
    found = re.findall(r"(?:schema|prompt)[- ]?v?ersion?\s*v?\d+\.\d+\.\d+|"
                       r"schema\s+v?\d+\.\d+\.\d+", model_facing, re.IGNORECASE)
    assert not found, (
        f"the model-facing text names a schema/prompt version again: {found}. "
        f"There is one version, `task_version`, spliced from the pack.")


def test_the_batch_spec_records_no_field_the_harness_never_writes(spec):
    """It specified an envelope no record has ever had.

    Step 9 described `{"run": {...}, "result": {...}}` with `run_id`,
    `assembled_text_sha256`, `input_tokens` and `error_code`. Every record is
    flat with a `validation` block, and those four names appear nowhere in `pe/`,
    `task/` or `tests/`. A spec of the record shape is what the next pack author
    builds against, so an aspirational one is worse than none.
    """
    spec_body = _section(spec, "## Batch wrapper spec", "## Validation loop")
    blocks = re.findall(r"```json(.*?)```", spec_body, re.DOTALL)
    assert blocks, "no JSON examples in the batch spec; parser found nothing"
    source = "\n".join(p.read_text() for p in
                       sorted((ROOT / "pe").glob("*.py")) + sorted((ROOT / "task").glob("*.py")))
    declared = {k for block in blocks for k in re.findall(r'"([a-z_][a-z0-9_]*)":', block)}
    ghosts = sorted(k for k in declared
                    if f'"{k}"' not in source and f"'{k}'" not in source)
    # `sources`/`path` etc. come from the input manifest, which the fetch layer
    # writes -- only the OUTPUT record's fields are this file's business.
    manifest_owned = {"pmcid", "fetch_status", "source_type", "extractor", "char_count",
                      "references_stripped", "sha256", "doi", "path", "source_id",
                      "sources"}
    # Built from a table value rather than written as a literal, so the
    # literal-in-source test cannot see it: `f"{PRIMARY_FIELD}_final"`.
    rec = tables(ROOT)["record"]
    derived = {f'{rec["primary_field"]}_final'}
    ghosts = [g for g in ghosts if g not in manifest_owned and g not in derived]
    assert not ghosts, (
        f"the batch spec presents {ghosts} as record fields and nothing in pe/ or "
        f"task/ writes them. Describe what the harness does, or build it.")


def test_the_disease_model_rule_does_not_decide_on_the_contrast_shape(spec):
    """v0.0.15's tell was refuted by the first two papers the curator read.

    The rule offered "the sequenced contrast is diseased tissue against healthy
    -- a STATE contrast" as the sign of a model. Curator rulings 9 and 10 are
    `yes` on a western-diet NASH paper and a spinal-contusion injury atlas, both
    of which have exactly that structure. The rule is keyed on attribution now,
    and the disclaimer is part of the rule rather than a footnote, because the
    refuted tell is the intuitive reading and would come back.
    """
    rule = _section(spec, "- **A manipulation is the MODEL rather than a perturbation",
                    "- Transfection/transduction:")
    assert "NOT the tell" in rule, (
        "the disease-model rule no longer disclaims the contrast shape as a tell; "
        "'diseased against healthy' describes both the perturbation cases "
        "(rulings 9, 10) and the model cases (rulings 7, 8, 12, 13)")
    assert "was anything applied during the study at all" in rule, (
        "the rule no longer leads with the cheap mechanical test from rulings 12 "
        "and 13. An intent test applied first is this pipeline's documented "
        "instability -- v0.0.15 left three papers flipping across identical runs")
    # Test 1 must come before Test 2: cheap and mechanical, then judgment.
    assert rule.index("was anything applied") < rule.index("what does the paper attribute"), (
        "the judgment test is stated before the mechanical one, which inverts the "
        "order rulings 12 and 13 were used to establish")
