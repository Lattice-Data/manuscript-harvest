"""The `ASSEMBLY:` block: what the pipeline removed, told to the model.

Step 0 asks the model whether the text is complete. Until v0.0.24 nothing in the
model-facing prompt said which cuts were the PIPELINE'S -- reference lists,
acknowledgments, funding, competing interests, data availability, back matter,
every table, every figure image, and Discussion/Introduction when the budget
ladder ran. The measured consequence: **154 of the 392 corpus papers end on a
bare heading with nothing under it** because the exclusion list took the content
and left the label, and 192 end without terminal punctuation.

`10.1182/bloodadvances.2023011445` is the paper that forced the block. Two
byte-identical v0.0.23 acceptance runs reported "full" and then "truncated",
Stage B downgraded one and not the other, and the determination moved -- on a paper
whose every harness fact says complete (JATS, 0.98 section coverage, all five
body sections found, supplement fetched and read, rung 0) and whose text ends on
"Associated Data / Supplementary Materials" with nothing under it.

So the guards here are about one property: **the note states facts the assembly
just produced, and states the same ones twice for the same input.** A note
assembled from a hand-written description of the pipeline would go stale silently,
and a note that varied between runs would reintroduce the defect it was written
to remove.

Run: python -m pytest tests/test_assembly_note.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.pack import PackError, load as load_pack, read_back_marker  # noqa: E402
from harness.prepare import (  # noqa: E402
    DEFAULT_BUDGET_CHARS, assembly_note, build_template, sources_within_budget,
)

ROOT = Path(__file__).resolve().parent.parent


def _spec_dir(base):
    """Where `PACK_GLOBS` looks for the spec. A fixture that writes it
    anywhere else builds a pack whose spec is not hashed."""
    d = base / "criteria"
    d.mkdir(exist_ok=True)
    return d

EXCLUDE = ("references", "supplementary", "acknowledgments", "back_matter")
INCLUDE = ("metadata", "heading", "paragraph", "caption")

_BLOCKS = [
    {"kind": "metadata", "section": None, "source_file": "fulltext.pdf",
     "text": "Title: A paper\nDOI: 10.1/x"},
    {"kind": "paragraph", "section": "methods", "source_file": "fulltext.pdf",
     "text": "Cells were treated and profiled. " * 40},
    {"kind": "paragraph", "section": "discussion", "source_file": "fulltext.pdf",
     "text": "We speculate at length. " * 40},
    {"kind": "paragraph", "section": "methods",
     "source_file": "supplementary/mmc1.pdf",
     "text": "Supplementary methods: 100 ng/mL for 4 h. " * 40},
]


def _note(budget=DEFAULT_BUDGET_CHARS, include_supplementary=True, blocks=None):
    sources, stats, truncation = sources_within_budget(
        blocks if blocks is not None else _BLOCKS,
        EXCLUDE, INCLUDE, include_supplementary, budget)
    return assembly_note(sources, stats, truncation, EXCLUDE, INCLUDE,
                         include_supplementary), sources, truncation


# --------------------------------------------------------------------------
# Every fact in the note is a fact about THIS assembly
# --------------------------------------------------------------------------

def test_every_supplied_source_is_named_with_its_size():
    """A source the model was given but the note omits is worse than no note:
    the model would read the omission as a source that was withheld."""
    note, sources, _ = _note()
    for source in sources:
        assert source["source_id"] in note
        assert f"{source['char_count']:,}" in note, (
            f"{source['source_id']} is named without its char count, so the "
            f"model cannot tell a 2,000-char stub from a whole article")


def test_every_removed_section_is_named_from_the_list_that_removed_it():
    """Derived, not described. The exclusion list lives in config.yaml and is
    passed to `build_sources`; a note restating it by hand would drift the first
    time somebody added a section."""
    note, _, _ = _note()
    for section in EXCLUDE:
        assert section in note, (
            f"{section!r} was removed from the text and the note does not say "
            f"so, which is exactly the silence v0.0.24 closed")


def test_every_supplied_content_kind_is_named():
    note, _, _ = _note()
    for kind in INCLUDE:
        assert kind in note
    assert "no tables" in note and "no figure images" in note, (
        "tables and figure images never reach the model at all, and a KEY "
        "RESOURCES TABLE absent from the text reads as an incomplete extraction")


# --------------------------------------------------------------------------
# The truncation ladder, which is the fact the model cannot see
# --------------------------------------------------------------------------

def test_an_untruncated_paper_says_so_rather_than_staying_silent():
    """Silence is the state this whole block replaced. "none" is a claim the
    model can act on; a missing line is one it has to guess about."""
    note, _, truncation = _note()
    assert truncation["rung"] == 0 and not truncation["truncated"]
    assert "budget truncation applied by the pipeline: none" in note


def test_a_truncated_paper_names_the_rung_and_the_sections_it_lost():
    note, _, truncation = _note(budget=2_000)
    assert truncation["truncated"], "the tiny budget did not trip the ladder"
    assert f"rung {truncation['rung']}" in note
    for section in truncation["dropped_sections"]:
        assert section in note, (
            f"{section!r} was dropped for budget and the note does not name it, "
            f"so a missing Discussion reads as a truncated article")


def test_the_ladder_running_out_is_stated_and_not_confused_with_an_ordinary_cut():
    """`needs_section_pass` means content went beyond the named sections. The
    two states must read differently: one is "Discussion is gone", the other is
    "more than that is gone"."""
    huge = [dict(_BLOCKS[1], text="Methods text. " * 40_000)]
    note, _, truncation = _note(budget=1_000, blocks=huge)
    assert truncation.get("needs_section_pass")
    assert "ran out" in note


# --------------------------------------------------------------------------
# The property the mover paper needs: the note cannot move between runs
# --------------------------------------------------------------------------

def test_the_same_assembly_renders_the_same_note():
    """`bloodadvances.2023011445` flipped its self-report on byte-identical
    input. A note that varied between two runs of one paper would put that
    instability back in, in the one input added to remove it."""
    first, _, _ = _note()
    second, _, _ = _note()
    assert first == second


def test_the_supplement_count_is_what_was_supplied_not_what_survived_dedup():
    """The last rung keeps only the largest supplement and rebuilds `chars`
    alone, so `stats['supp_files_kept']` still reports the pre-pruning count.
    Reading it there would have told the model that four files it never saw were
    supplied -- a false statement in the one block added to stop it guessing."""
    blocks = list(_BLOCKS)
    for n in range(2, 5):
        blocks.append({"kind": "paragraph", "section": "methods",
                       "source_file": f"supplementary/mmc{n}.pdf",
                       "text": f"Supplement {n} says something. " * 40})
    note, sources, truncation = _note(budget=3_000, blocks=blocks)
    supplied = [s for s in sources if s["source_type"] == "supplementary"]
    assert truncation["largest_supp_only"] and len(supplied) == 1
    assert "1 supplied of 4 found" in note, (
        "the note must count the files handed over, not the files the ladder "
        f"started with. Got: {note}")


def test_switching_supplementary_off_says_so_instead_of_reporting_none_found():
    """`--no-supplementary` returns before the counters are filled, so the
    honest 0-of-0 would read as "the publisher listed none" -- a different fact,
    and Step 0's answer turns on which one it is."""
    off, sources, _ = _note(include_supplementary=False)
    assert [s["source_id"] for s in sources] == ["main"]
    assert "configured to skip" in off
    on, _, _ = _note(include_supplementary=True)
    assert "1 supplied of 1 found" in on


# --------------------------------------------------------------------------
# It must not collide with the two markers the harness parses
# --------------------------------------------------------------------------

def test_the_note_cannot_be_mistaken_for_the_paper_text_or_a_source_block():
    """`harness.validate` recovers the text the model saw by searching BACKWARDS for
    the read-back marker, and splits it on `<<<SOURCE` markers. A note carrying
    either one would make the recovered "paper text" the wrong bytes, and then
    every quote would verify against the wrong thing."""
    note, _, _ = _note()
    assert read_back_marker().strip() not in note
    assert "<<<SOURCE" not in note


def test_the_note_tells_the_model_not_to_quote_from_it():
    """It is the pipeline talking, so a quote from it cites text that is in no
    source and fails verification for a reason the model cannot diagnose."""
    note, _, _ = _note()
    assert "never quote from it" in note


# --------------------------------------------------------------------------
# And it must actually reach the model
# --------------------------------------------------------------------------

def test_the_real_spec_carries_the_placeholder():
    pack = load_pack(ROOT)
    assert "assembly" in pack.placeholders
    assert pack.placeholders["assembly"] in build_template(pack)


def test_a_spec_that_drops_the_placeholder_is_refused_by_name(tmp_path):
    """The failure this guards is the silent one: a prompt without the
    placeholder would be prepared successfully, and the model would judge
    completeness with no statement of what was cut -- the v0.0.23 state, with a
    note in the manifest saying otherwise."""
    pack = load_pack(ROOT)
    spec = pack.spec_path.read_text().replace(pack.placeholders["assembly"], "")
    (_spec_dir(tmp_path) / "prompt.md").write_text(spec)

    class _Stub:
        anchors = pack.anchors
        placeholders = pack.placeholders
        spec_path = _spec_dir(tmp_path) / "prompt.md"

    with pytest.raises(PackError) as exc:
        build_template(_Stub())
    assert pack.placeholders["assembly"] in str(exc.value)
    assert "spec.placeholders.assembly" in str(exc.value)


def test_a_pack_declaring_no_assembly_placeholder_is_refused():
    """Required rather than optional, and this is the line that makes it so.
    Optional would mean absent-by-default, which is how the note would silently
    stop reaching the model."""
    from harness.pack import TaskPack

    config = {"name": "t", "version": "0.0.1",
              "spec": {"path": "criteria/prompt.md",
                       "anchors": {"instruction": "a", "schema_start": "b",
                                   "schema_end": "c"},
                       "placeholders": {"paper_id": "{{P}}", "paper_text": "{{T}}",
                                        "source_ids": "{{S}}",
                                        "task_version": "{{V}}"}}}
    with pytest.raises(PackError) as exc:
        TaskPack(config, ROOT)
    assert "assembly" in str(exc.value)


# --------------------------------------------------------------------------
# The model-facing text has to define "full" against the note
# --------------------------------------------------------------------------

def _model_facing() -> str:
    pack = load_pack(ROOT)
    spec = pack.spec_path.read_text()
    return spec[spec.index(pack.anchors["instruction"]):
                spec.index(pack.anchors["schema_start"])]


def test_step_0_sends_the_model_to_the_note_before_it_judges_the_text():
    text = _model_facing()
    step_0 = text[text.index("## Step 0:"):text.index("### Step 0b")]
    assert "ASSEMBLY:" in step_0, (
        "the block is spliced into the prompt but Step 0 never tells the model "
        "to read it, which is a note nobody reads")
    assert "nothing is missing BEYOND what `ASSEMBLY:` says was removed" in step_0, (
        "the definition of 'full' is the change; without it the note is trivia")


@pytest.mark.parametrize("cut", [
    "bare heading",          # the 154-paper artefact
    "reference list",        # stripped from every source
    "licence footer",        # the medRxiv running-header cut
])
def test_the_pipelines_own_cuts_are_named_as_non_defects(cut):
    """Guard 3 of the v0.0.10 attractor episode: a generic "do not read the
    pipeline's cuts as truncation" was ignored twice in one paper there; the
    concrete list held. Each item here is an artefact measured in this corpus."""
    step_0 = _model_facing()
    assert cut in step_0, (
        f"{cut!r} is a cut this pipeline makes on purpose and the prompt no "
        f"longer names it, so the model has to guess again")


def test_full_is_stated_to_be_the_common_answer():
    """Guard 2 of the same episode: without "zero is a normal result", a field
    the model is asked to justify reads as a quota. 372 of 392 is the measured
    number and it goes in the prompt rather than a comment."""
    assert '372 of the 392' in _model_facing()
