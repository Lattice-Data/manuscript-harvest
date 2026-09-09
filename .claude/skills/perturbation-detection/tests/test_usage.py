"""Guards for the usage backfill, one per way it produced a wrong number first.

`pe.usage` rebuilds what a run cost from the transcripts `claude -p` already
persisted. Every guard here is a defect that was real before it was a test:

  * summing per JSONL line instead of per `message.id` overstated one sampled
    transcript's output by 39%, because streaming re-emits a message;
  * counting the CLI's injected usage-limit messages as API requests
    overstated the v0.0.21 corpus run by exactly 145;
  * pricing 1-hour cache writes at the 5-minute rate understated that run by
    about $140;
  * attributing every session in the project directory to a paper would have
    swept in 2,208 unrelated interactive sessions.

The pricing table is checked against two real `claude -p --output-format json`
envelopes captured on this machine, to the cent. That is what makes the dollar
figures a measurement rather than an assertion.

`test_it_reproduces_the_v0021_corpus_run` is the end-to-end one and skips when
the transcripts are absent, the way `test_harness_guards.py` skips for want of
a run directory.

Run: python -m pytest tests/test_usage.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pe.pricing import CACHE_READ, CACHE_WRITE_1H, CACHE_WRITE_5M, RATES, canonical, cost  # noqa: E402
from pe.usage import collect, read_session, render  # noqa: E402


# --------------------------------------------------------------- pricing

#: Two real envelopes from `claude -p --output-format json` on this machine.
#: Kept verbatim, including the reported total, so the table is checked against
#: the CLI's own arithmetic rather than against a restatement of itself.
ENVELOPES = [
    {
        "model": "claude-haiku-4-5-20251001",
        "usage": {
            "input_tokens": 10,
            "output_tokens": 47,
            "cache_read_input_tokens": 13979,
            "cache_creation": {
                "ephemeral_1h_input_tokens": 7243,
                "ephemeral_5m_input_tokens": 0,
            },
        },
        "total_cost_usd": 0.0161289,
    },
    {
        "model": "claude-haiku-4-5-20251001",
        "usage": {
            "input_tokens": 18,
            "output_tokens": 394,
            "cache_read_input_tokens": 35228,
            "cache_creation": {
                "ephemeral_1h_input_tokens": 9066,
                "ephemeral_5m_input_tokens": 0,
            },
        },
        "total_cost_usd": 0.0236428,
    },
]


@pytest.mark.parametrize("envelope", ENVELOPES)
def test_the_price_table_reproduces_a_real_envelope_to_the_cent(envelope):
    got = cost(envelope["model"], envelope["usage"])
    assert got == pytest.approx(envelope["total_cost_usd"], abs=1e-9), (
        f"priced {got} against the CLI's own {envelope['total_cost_usd']}"
    )


def test_the_v0021_corpus_totals_price_to_the_published_figure():
    """The whole corpus run, from the token counts three readers agreed on."""
    got = cost("claude-opus-5", {
        "input_tokens": 9_158,
        "output_tokens": 3_660_624,
        "cache_read_input_tokens": 295_151_111,
        "cache_creation": {
            "ephemeral_1h_input_tokens": 37_446_096,
            "ephemeral_5m_input_tokens": 0,
        },
    })
    assert round(got, 2) == 613.60


def test_a_one_hour_cache_write_costs_more_than_a_five_minute_one():
    """The $140 defect: collapsing the TTL split to the cheaper rate.

    These runs use 1-hour caching exclusively, so a reader that ignores
    `cache_creation` and prices the flat total at the 5-minute rate understates
    every one of them.
    """
    tokens = 37_446_096
    hour = cost("claude-opus-5", {
        "cache_creation": {"ephemeral_1h_input_tokens": tokens,
                           "ephemeral_5m_input_tokens": 0}})
    five = cost("claude-opus-5", {
        "cache_creation": {"ephemeral_1h_input_tokens": 0,
                           "ephemeral_5m_input_tokens": tokens}})
    assert hour > five
    assert round(hour - five, 2) == 140.42


def test_cache_rates_are_derived_not_stored():
    """Two numbers per model, three ratios shared. Drift has nowhere to hide."""
    for name, (rate_in, _) in RATES.items():
        one = 1_000_000
        assert cost(name, {"cache_read_input_tokens": one}) == pytest.approx(rate_in * CACHE_READ)
        assert cost(name, {"cache_creation": {"ephemeral_5m_input_tokens": one}}) == \
            pytest.approx(rate_in * CACHE_WRITE_5M)
        assert cost(name, {"cache_creation": {"ephemeral_1h_input_tokens": one}}) == \
            pytest.approx(rate_in * CACHE_WRITE_1H)


def test_an_unknown_model_is_not_guessed():
    """A wrong price gets quoted; a missing one is visible in the report."""
    assert cost("claude-something-unreleased", {"output_tokens": 10_000}) is None
    assert canonical("claude-opus-5-20260101") == "claude-opus-5"
    assert canonical(None) is None


# --------------------------------------------------------------- transcripts


def _line(**over):
    record = {
        "type": "assistant",
        "timestamp": "2026-09-01T10:00:00.000Z",
        "requestId": "req_1",
        "uuid": "u1",
        "message": {
            "id": "msg_1",
            "model": "claude-opus-5",
            "usage": {
                "input_tokens": 2,
                "output_tokens": 100,
                "cache_read_input_tokens": 1_000,
                "cache_creation": {"ephemeral_1h_input_tokens": 500,
                                   "ephemeral_5m_input_tokens": 0},
            },
        },
    }
    record["message"].update(over.pop("message", {}))
    record.update(over)
    return record


def _transcript(tmp_path, *records, work="work-x", doi="10.1000_a", named=True):
    path = tmp_path / "session.jsonl"
    lines = []
    if named:
        lines.append({
            "type": "user",
            "timestamp": "2026-09-01T09:59:00.000Z",
            "message": {"content":
                        f"Read the file /home/u/.manuscript-harvest/perturbation/"
                        f"{work}/prompts/{doi}.txt in full"},
        })
    lines.extend(records)
    path.write_text("\n".join(json.dumps(r) for r in lines), encoding="utf-8")
    return path


def test_a_re_emitted_message_is_counted_once(tmp_path):
    """The 39% overcount: streaming repeats a message across JSONL lines."""
    path = _transcript(tmp_path, _line(), _line(), _line())
    session = read_session(path)
    tokens = session.by_model["claude-opus-5"]
    assert tokens.requests == 1
    assert tokens.output == 100


def test_distinct_messages_still_accumulate(tmp_path):
    """The negative control: dedupe must not collapse a real second request."""
    path = _transcript(
        tmp_path,
        _line(),
        _line(requestId="req_2", uuid="u2", message={"id": "msg_2"}),
    )
    tokens = read_session(path).by_model["claude-opus-5"]
    assert tokens.requests == 2
    assert tokens.output == 200


def test_a_usage_limit_message_is_not_an_api_request(tmp_path):
    """The 145 defect. Synthetic messages carry zero usage and no request."""
    path = _transcript(
        tmp_path,
        _line(),
        _line(requestId="req_2", uuid="u2",
              message={"id": "msg_2", "model": "<synthetic>",
                       "usage": {"input_tokens": 0, "output_tokens": 0}}),
    )
    session = read_session(path)
    assert session.limit_refusals == 1
    assert sum(t.requests for t in session.by_model.values()) == 1
    assert "<synthetic>" not in session.by_model


def test_a_session_that_names_no_paper_is_not_part_of_a_run(tmp_path):
    """2,208 of 3,667 transcripts are interactive sessions, not paper scoring."""
    assert read_session(_transcript(tmp_path, _line(), named=False)) is None


def test_the_work_directory_and_doi_come_from_the_prompt_path(tmp_path):
    session = read_session(
        _transcript(tmp_path, _line(), work="work-corpus-v0021-r1",
                    doi="10.1126_science.abl4290"))
    assert session.work == "work-corpus-v0021-r1"
    assert session.doi == "10.1126_science.abl4290"


def test_more_than_one_model_in_a_session_is_reported_separately(tmp_path):
    """Marked sessions do contain a second model; the table must show it."""
    path = _transcript(
        tmp_path,
        _line(),
        _line(requestId="req_2", uuid="u2",
              message={"id": "msg_2", "model": "claude-sonnet-5"}),
    )
    session = read_session(path)
    assert set(session.by_model) == {"claude-opus-5", "claude-sonnet-5"}


def test_a_truncated_line_does_not_lose_the_rest_of_the_transcript(tmp_path):
    """A transcript is an append-only log and can be cut mid-write."""
    path = _transcript(tmp_path, _line())
    path.write_text(path.read_text(encoding="utf-8") + '\n{"type": "assist',
                    encoding="utf-8")
    assert read_session(path).by_model["claude-opus-5"].requests == 1


def test_usage_is_summed_across_attempts(tmp_path):
    """143 of 392 papers were retried, each attempt a separate session."""
    for n in (1, 2):
        directory = tmp_path / f"try{n}"
        directory.mkdir()
        _transcript(directory, _line(), work="work-r", doi="10.1000_a")
    sessions = collect("work-r", [str(p) for p in sorted(tmp_path.iterdir())])
    assert len(sessions) == 2
    report = render(sessions, "work-r")
    assert "1 papers took more than one attempt" in report
    assert "2 sessions" in report


def test_an_unpriceable_model_is_named_rather_than_dropped_silently(tmp_path):
    path = _transcript(
        tmp_path, _line(message={"model": "claude-unreleased-9"}))
    report = render([read_session(path)], "work-x")
    assert "price unknown" in report
    assert "claude-unreleased-9" in report


# --------------------------------------------------------------- end to end


def test_it_reproduces_the_v0021_corpus_run():
    """The real thing: 392 papers, from transcripts, to the cent.

    Skips where the transcripts are absent -- another machine, or a CLI that
    has pruned them. The numbers below were independently derived three times
    (two subagents and this module) before being written down.
    """
    sessions = collect("work-corpus-v0021-r1")
    if not sessions:
        pytest.skip("no work-corpus-v0021-r1 transcripts on this machine")

    report = render(sessions, "work-corpus-v0021-r1")
    assert "$613.60" in report
    assert "4,579 API requests" in report
    assert "535 sessions" in report
    assert "392 papers" in report
    assert "143 papers took more than one attempt" in report
    assert "145 spawns returned a usage-limit message" in report

    tokens = {}
    for session in sessions:
        for name, counts in session.by_model.items():
            tokens.setdefault(name, []).append(counts)
    opus = tokens["claude-opus-5"]
    assert sum(t.input for t in opus) == 9_158
    assert sum(t.output for t in opus) == 3_660_624
    assert sum(t.cache_read for t in opus) == 295_151_111
    assert sum(t.cache_write_1h for t in opus) == 37_446_096
    # The whole run used 1-hour caching; a 5-minute write here would mean the
    # CLI changed its caching behaviour and the price table needs revisiting.
    assert sum(t.cache_write_5m for t in opus) == 0
