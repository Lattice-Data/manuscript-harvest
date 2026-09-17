"""`parse_raw`: the control characters a `garbled_run` quote is made of.

v0.0.25 asks the model for a verbatim `quote` on a `garbled_run` defect, and
garbled text is made of precisely the bytes JSON forbids unescaped inside a
string. Two of the 392 papers in the v0.0.25 corpus run died on it --
`10.1038_s41586-020-2496-1` and `10.1126_science.aat1699` -- and both were
counted "unparseable" and skipped, so the corpus finished split across two task
versions: 390 at 0.0.25 and 2 left at 0.0.21. That is the exact state
`task.yaml`'s single version number exists to make impossible, and it was caused
by the model doing exactly what the prompt told it to do.

Run: python -m pytest tests/test_parse_raw.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pe.validate import parse_raw  # noqa: E402

#: Verbatim from work-corpus-v0025-r1/raw/10.1038_s41586-020-2496-1.json, which
#: decodes under a uniform **+31** byte shift to "No sample size calculation was
#: performed." -- `\x01 + 31 == 0x20`, i.e. these are undecoded GLYPH INDICES
#: from a subset font whose `/ToUnicode` CMap was not applied, not corruption.
#: A corpus scan on 2026-09-17 found the same fault in 84 of 392 papers,
#: 13,482 characters in total. Fixing it belongs in
#: `manuscript_harvest/extract/pdf.py`; this file only keeps the record
#: parseable when the model quotes such a run verbatim, as v0.0.25 asks it to.
REAL_GARBLED = "/P\x01TBNQMF\x01TJ[F\x01DBMDVMBUJPO\x01XBT\x01QFSGPSNFE\x0f"


def _record(quote: str) -> str:
    return json.dumps(
        {"task_version": "0.0.25", "paper_id": "p",
         "text_defects": [{"source_id": "main", "kind": "garbled_run",
                           "quote": "QUOTE"}]},
        indent=2).replace("QUOTE", quote)


def test_a_garbled_run_quote_with_control_bytes_is_recovered():
    """The bug. Without the fallback this raises JSONDecodeError and the paper
    is dropped as unparseable."""
    raw = _record(REAL_GARBLED)
    with pytest.raises(json.JSONDecodeError):
        json.loads(raw)                      # strict JSON cannot take it
    got = parse_raw(raw)                     # parse_raw can
    assert got["text_defects"][0]["kind"] == "garbled_run"
    assert "\x01" not in got["text_defects"][0]["quote"]


def test_the_stripped_set_is_the_one_the_matcher_strips():
    """The property that makes the recovery safe rather than merely convenient:
    a quote recovered here normalizes to the same string the verifier looks for,
    so it still has to be found in the source. If these two ever diverge, a
    recovered quote could be unverifiable through no fault of the model."""
    from pe.paper_text import normalize_text
    recovered = parse_raw(_record(REAL_GARBLED))["text_defects"][0]["quote"]
    assert normalize_text(recovered) == normalize_text(REAL_GARBLED)


def test_a_clean_record_is_returned_byte_for_byte_unchanged():
    """Strict first, always. 390 of the 392 parsed on the first attempt and must
    keep doing so -- the fallback must not become a silent rewriter."""
    raw = _record("a perfectly ordinary quote")
    assert parse_raw(raw) == json.loads(raw)


def test_tab_newline_and_return_are_preserved_as_json_structure():
    """\\t \\n \\r are the JSON's own formatting and are NOT stripped. A literal
    newline inside a string is a different defect, and one this deliberately
    does not paper over."""
    obj = parse_raw('{\n\t"a": 1,\r\n\t"b": "x"\n}')
    assert obj == {"a": 1, "b": "x"}


def test_a_genuinely_broken_record_still_raises():
    """The fallback must not turn into 'accept anything'. Stripping control
    characters cannot rescue malformed structure, and it must not try."""
    with pytest.raises((ValueError, json.JSONDecodeError)):
        parse_raw('{"a": 1, "b":')
