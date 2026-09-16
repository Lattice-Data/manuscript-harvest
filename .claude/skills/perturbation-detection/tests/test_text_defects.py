"""`text_defects`: the evidence Stage B's cap now needs, and the one-way check.

v0.0.25. The cap used to fire on `processing_status = "partial" OR
text_completeness != "full"` -- two paper-level adjectives -- and that trigger
flipped on byte-identical input in two consecutive acceptance passes: **3 of 30
papers at v0.0.23, and 4 of 24 at v0.0.24** after the `ASSEMBLY:` block had
already removed the largest single cause.

Reading those eight flips is what this file encodes. Three quarters of them were
adjudicable by a quote the model had already read, and the rest pointed at
nothing:

  `s41586-023-06981-x`   one run quoted the break verbatim, the other said `full`
  `2021.09.16.460628`    one cited dangling "see Methods" cross-references
  `science.aat1699`      one described the garbled SUPPLEMENT, one the whole MAIN
                         text -- both true, and nothing could hold both
  `atvbaha.122.317953`   claimed `truncated` and named no locus at all

Run: python -m pytest tests/test_text_defects.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pe.paper_text import section_chars  # noqa: E402
from task.rules import (  # noqa: E402
    DEFECT_KINDS, capping_defects, methods_claim_refuted, validate_defects,
)

ROOT = Path(__file__).resolve().parent.parent

#: Verifies any quote containing REAL, and attributes it where it was claimed.
def _verify(quote, source):
    return {"verified": "REAL" in (quote or ""), "source_id": source}


def _record(**over):
    return dict({"text_completeness": "full", "text_defects": []}, **over)


# --------------------------------------------------------------------------
# A claim has to point at something
# --------------------------------------------------------------------------

def test_a_verified_quote_is_kept():
    record = _record(text_defects=[
        {"source_id": "main", "kind": "garbled_run", "quote": "REAL mojibake"}])
    kept, checked, rejected = validate_defects(record, _verify, [], set())
    assert len(kept) == 1 and checked == 1 and rejected == 0


def test_an_unverifiable_quote_is_dropped_rather_than_kept():
    """The opposite of `validate_secondary`, deliberately. A suppression is a
    record of a decision already made, so a bad quote there drops the quote and
    keeps the entry. A defect entry is a LICENCE TO CAP, so keeping one the
    harness could not confirm would put the unevidenced adjective straight back.
    """
    record = _record(text_completeness="truncated", text_defects=[
        {"source_id": "main", "kind": "ends_mid_sentence", "quote": "invented"}])
    issues: list = []
    kept, _, rejected = validate_defects(record, _verify, issues, set())
    assert kept == [] and rejected == 1
    assert record["text_defects"] == []
    assert any("does not verify" in i for i in issues)


def test_a_degraded_self_report_with_no_evidence_is_reported():
    """`atvbaha.122.317953`'s v0.0.24 run: `truncated`, and nothing behind it.
    The determination no longer turns on it, so this is an issue rather than a
    consistency code -- but a curator has to be able to tell "the text is fine"
    from "nobody could check"."""
    record = _record(text_completeness="truncated")
    issues: list = []
    validate_defects(record, _verify, issues, set())
    assert any("carries no evidence" in i for i in issues)


def test_a_wrong_source_is_corrected_not_discarded():
    """Same treatment the item array gets: the text is real, the attribution is
    not, so fix the attribution rather than throw the evidence away."""
    record = _record(text_defects=[
        {"source_id": "supp9", "kind": "garbled_run", "quote": "REAL noise"}])
    flags: set = set()
    kept, _, _ = validate_defects(
        record, lambda q, src: {"verified": True, "source_id": "main"},
        [], flags)
    assert kept[0]["source_id"] == "main"
    assert "EV-WRONG-SOURCE" in flags


# --------------------------------------------------------------------------
# The absence that cannot be quoted, and the threshold that decides it
# --------------------------------------------------------------------------

def test_no_methods_content_may_omit_its_quote():
    record = _record(text_completeness="methods_missing", text_defects=[
        {"source_id": "main", "kind": "no_methods_content", "quote": None}])
    kept, checked, rejected = validate_defects(record, _verify, [], set())
    assert len(kept) == 1 and checked == 0 and rejected == 0


@pytest.mark.parametrize("kind", [k for k in DEFECT_KINDS
                                  if k != "no_methods_content"])
def test_no_other_kind_may_omit_its_quote(kind):
    record = _record(text_defects=[{"source_id": "main", "kind": kind}])
    kept, _, rejected = validate_defects(record, _verify, [], set())
    assert kept == [] and rejected == 1


def test_the_methods_check_refutes_on_substance_not_on_a_label():
    """The measurement this threshold exists for.

    `science.aat1699` carries five `methods`-labelled blocks totalling **228
    characters**: two copies of the heading "Materials and Methods" and a list
    of supplementary figure captions. Its `methods_missing` report is CORRECT.
    A check keyed on the label existing would have refuted a true claim and
    released the cap on the one text in this corpus that is genuinely broken.
    """
    assert methods_claim_refuted({"abstract": 6209, "methods": 228}) is False
    assert methods_claim_refuted({"methods": 11776}) is True


def test_the_methods_check_is_one_way():
    """`False` means "cannot refute", never "the claim is true". **15 of the 392
    corpus papers supply zero methods-labelled characters and most of them are
    correctly `full`**, because JATS and Science put methods under labels the
    extractor never matched. The label measures labelling, not content -- so a
    missing label may not be read as a missing section."""
    assert methods_claim_refuted({}) is False
    assert methods_claim_refuted(None) is False
    assert methods_claim_refuted({"abstract": 5000}) is False


def test_a_refuted_methods_claim_is_dropped_and_said_so():
    record = _record(text_defects=[
        {"source_id": "main", "kind": "no_methods_content", "quote": None}])
    issues: list = []
    flags: set = set()
    kept, _, _ = validate_defects(record, _verify, issues, flags,
                                  {"methods": 40_000})
    assert kept == [] and "EV-DEFECT-REFUTED" in flags
    assert any("refuted" in i for i in issues)


# --------------------------------------------------------------------------
# Per source, which is half of why the old trigger flipped
# --------------------------------------------------------------------------

def test_two_sources_can_disagree_without_the_record_lying():
    """`science.aat1699`'s flip was not a disagreement about facts. One run
    described the garbled supplement, the other the intact main article, and the
    schema had nowhere to hold both at once. Now it does -- and
    `processing_status = "partial"` with `text_completeness = "full"` is the
    legal, meaningful way to say "one source is garbage, the article is whole".
    """
    record = _record(processing_status="partial", text_completeness="full",
                     text_defects=[{"source_id": "supp2", "kind": "garbled_run",
                                    "quote": "REAL mojibake"}])
    kept, _, _ = validate_defects(record, _verify, [], set())
    assert len(kept) == 1
    assert capping_defects(record) == [], (
        "a garbled reporting summary could not have hidden a pairing sentence, "
        "so it is recorded and does not cap")


def test_section_chars_counts_what_reached_the_model():
    """Derived from the same filters `build_sources` applies -- two statements of
    what was kept would eventually disagree."""
    blocks = [
        {"kind": "heading", "section": "methods", "source_file": "f.pdf",
         "text": "Materials and Methods"},
        {"kind": "paragraph", "section": "methods", "source_file": "f.pdf",
         "text": "x" * 500},
        {"kind": "paragraph", "section": "references", "source_file": "f.pdf",
         "text": "y" * 9000},
        {"kind": "table", "section": "methods", "source_file": "f.pdf",
         "text": "z" * 9000},
    ]
    counts = section_chars(blocks)
    assert counts == {"methods": 521}, (
        "excluded sections and excluded block kinds must not be counted: "
        f"got {counts}")
