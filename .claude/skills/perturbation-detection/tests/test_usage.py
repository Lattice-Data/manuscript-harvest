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
from pe.usage import collect, from_envelopes, read_session, render  # noqa: E402


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


# --------------------------------------------------------- envelopes

#: A real `claude -p --output-format json` envelope from the smoke run that
#: exercised the invocation change, trimmed to the fields the reader uses.
REAL_ENVELOPE = {
    "type": "result", "subtype": "success", "is_error": False, "result": "DONE",
    "num_turns": 5, "duration_ms": 148000, "duration_api_ms": 145686,
    "total_cost_usd": 1.0141,
    "usage": {"input_tokens": 10, "output_tokens": 13100,
              "cache_read_input_tokens": 193525,
              "cache_creation": {"ephemeral_1h_input_tokens": 58974,
                                 "ephemeral_5m_input_tokens": 0}},
    "modelUsage": {"claude-opus-5": {
        "inputTokens": 10, "outputTokens": 13100,
        "cacheReadInputTokens": 193525, "cacheCreationInputTokens": 58974,
        "costUSD": 1.0141, "costBasis": "list"}},
}


def _run(tmp_path, doi="10.1000_a", envelopes=(REAL_ENVELOPE,)):
    work = tmp_path / "work-x"
    (work / "meta").mkdir(parents=True)
    (work / "meta" / f"{doi}.usage.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in envelopes), encoding="utf-8")
    return work


def test_an_envelope_reproduces_the_clis_own_cost(tmp_path):
    """The live check: our table against the CLI's figure on a real Opus-5 call."""
    sessions = from_envelopes(_run(tmp_path))
    report = render(sessions, "work-x")
    assert "$1.01" in report
    # No drift warning means the two agree within 1%.
    assert "pricing.py:RATES is probably stale" not in report


def test_a_price_change_is_reported_rather_than_absorbed(tmp_path):
    """If Anthropic's rates move, the table is wrong and must say so."""
    envelope = dict(REAL_ENVELOPE, total_cost_usd=99.0)
    report = render(from_envelopes(_run(tmp_path, envelopes=[envelope])), "work-x")
    assert "pricing.py:RATES is probably stale" in report


def test_every_attempt_in_the_file_is_counted(tmp_path):
    """143 of 392 papers were retried; the file appends one line per attempt."""
    sessions = from_envelopes(_run(tmp_path, envelopes=[REAL_ENVELOPE, REAL_ENVELOPE]))
    assert len(sessions) == 2
    assert sum(t.output for s in sessions for t in s.by_model.values()) == 26200


def test_the_cache_write_ttl_comes_from_the_flat_block(tmp_path):
    """`modelUsage` gives no TTL, and the 1h rate is 1.6x the 5m one."""
    tokens = from_envelopes(_run(tmp_path))[0].by_model["claude-opus-5"]
    assert tokens.cache_write_1h == 58974
    assert tokens.cache_write_5m == 0


def test_an_envelope_without_a_ttl_split_is_priced_at_the_cheaper_rate(tmp_path):
    """Guessing high would overstate a bill; guessing low is visible in the drift
    line rather than silently inflating a number somebody quotes."""
    envelope = json.loads(json.dumps(REAL_ENVELOPE))
    envelope["usage"]["cache_creation"] = {}
    envelope["usage"]["cache_creation_input_tokens"] = 58974
    tokens = from_envelopes(_run(tmp_path, envelopes=[envelope]))[0].by_model["claude-opus-5"]
    assert tokens.cache_write_1h == 0
    assert tokens.cache_write_5m == 58974


def test_model_time_is_reported_apart_from_the_agent_span(tmp_path):
    """A transcript cannot make this distinction; an envelope can."""
    report = render(from_envelopes(_run(tmp_path)), "work-x")
    assert "of agent wall-clock" in report
    assert "was model time" in report


def test_a_truncated_envelope_line_does_not_lose_the_others(tmp_path):
    work = _run(tmp_path, envelopes=[REAL_ENVELOPE])
    path = work / "meta" / "10.1000_a.usage.jsonl"
    path.write_text(path.read_text() + '{"type": "res\n', encoding="utf-8")
    assert len(from_envelopes(work)) == 1


def test_a_run_with_no_envelopes_yields_nothing_rather_than_erroring(tmp_path):
    """Every run before the invocation changed. The caller falls back to
    transcripts, and an exception here would break that path."""
    empty = tmp_path / "work-old"
    empty.mkdir()
    assert from_envelopes(empty) == []


def test_a_partly_instrumented_run_defers_to_the_transcripts(tmp_path):
    """A run resumed across the invocation change has envelopes for some papers
    and not others. Reporting the instrumented subset would describe part of a
    run as if it were the whole one, with nothing to signal the gap."""
    work = _run(tmp_path, doi="10.1000_a")
    (work / "manifest.json").write_text(json.dumps([
        {"doi": "10.1000_a"}, {"doi": "10.1000_b"},
    ]), encoding="utf-8")
    assert from_envelopes(work) == []

    # Once every paper is instrumented it is used again.
    (work / "meta" / "10.1000_b.usage.jsonl").write_text(
        json.dumps(REAL_ENVELOPE) + "\n", encoding="utf-8")
    assert len(from_envelopes(work)) == 2


def test_a_manifest_error_entry_does_not_block_the_envelopes(tmp_path):
    """`run_headless.sh` skips entries carrying an `error`, so they never get an
    envelope and must not count against coverage."""
    work = _run(tmp_path, doi="10.1000_a")
    (work / "manifest.json").write_text(json.dumps([
        {"doi": "10.1000_a"}, {"doi": "10.1000_b", "error": "unfetched"},
    ]), encoding="utf-8")
    assert len(from_envelopes(work)) == 1


# ------------------------------------------- envelope reader, adversarial


def test_an_unpriceable_model_does_not_masquerade_as_price_drift(tmp_path):
    """`_priced` excludes an unknown model; total_cost_usd includes it. The gap
    is then guaranteed and blames the price table for someone else's problem."""
    envelope = json.loads(json.dumps(REAL_ENVELOPE))
    envelope["modelUsage"]["claude-unreleased-9"] = {
        "inputTokens": 1, "outputTokens": 5000, "cacheReadInputTokens": 0,
        "cacheCreationInputTokens": 0, "costUSD": 40.0}
    envelope["total_cost_usd"] = 41.0141
    report = render(from_envelopes(_run(tmp_path, envelopes=[envelope])), "work-x")
    assert "price unknown" in report
    assert "probably stale" not in report


def test_model_time_without_a_span_does_not_divide_by_zero(tmp_path):
    """An envelope may carry duration_api_ms and no duration_ms."""
    envelope = json.loads(json.dumps(REAL_ENVELOPE))
    envelope.pop("duration_ms")
    report = render(from_envelopes(_run(tmp_path, envelopes=[envelope])), "work-x")
    assert "API requests" in report


def test_an_api_error_envelope_is_a_refusal_not_a_phantom_model(tmp_path):
    """A 401 envelope has `modelUsage: {}` and no top-level `model`, so the
    fallback booked it under "unknown" with zero tokens -- inventing spend the
    report then said it could not price."""
    envelope = {"type": "result", "is_error": True, "api_error_status": "429",
                "result": "rate_limit_error", "num_turns": 1,
                "total_cost_usd": 0, "usage": {}, "modelUsage": {}}
    sessions = from_envelopes(_run(tmp_path, envelopes=[envelope]))
    assert sum(len(s.by_model) for s in sessions) == 0
    assert sum(s.limit_refusals for s in sessions) == 1
    report = render(sessions, "work-x")
    assert "unknown" not in report
    assert "usage-limit message" in report


def test_a_truncated_envelope_makes_the_run_defer_to_transcripts(tmp_path):
    """The coverage guard used to test filenames. A file whose only line does
    not parse still has a name, so the paper vanished from the report while the
    guard passed -- part of a run described as all of it."""
    work = _run(tmp_path, doi="10.1000_a")
    (work / "meta" / "10.1000_b.usage.jsonl").write_text('{"type": "res\n',
                                                         encoding="utf-8")
    (work / "manifest.json").write_text(json.dumps([
        {"doi": "10.1000_a"}, {"doi": "10.1000_b"}]), encoding="utf-8")
    assert from_envelopes(work) == []


def test_a_mixed_ttl_envelope_splits_writes_rather_than_picking_a_side(tmp_path):
    """`modelUsage` gives a write total with no TTL and the flat block gives the
    TTL with no model; collapsing to one boolean priced part of a genuinely
    mixed envelope at the wrong rate."""
    envelope = json.loads(json.dumps(REAL_ENVELOPE))
    envelope["usage"]["cache_creation"] = {"ephemeral_1h_input_tokens": 20000,
                                           "ephemeral_5m_input_tokens": 38974}
    tokens = from_envelopes(_run(tmp_path, envelopes=[envelope]))[0].by_model["claude-opus-5"]
    assert tokens.cache_write_1h + tokens.cache_write_5m == 58974
    assert tokens.cache_write_1h > 0 and tokens.cache_write_5m > 0
    # Proportional to the flat block, not all-or-nothing.
    assert abs(tokens.cache_write_1h / 58974 - 20000 / 58974) < 0.01


def test_a_run_where_every_attempt_errored_reports_rather_than_crashes(tmp_path):
    """`max(x, *())` raises, so an all-refusals run crashed the reporter."""
    envelope = {"type": "result", "is_error": True, "api_error_status": "429",
                "result": "rate_limit_error", "num_turns": 1,
                "total_cost_usd": 0, "usage": {}, "modelUsage": {}}
    report = render(from_envelopes(_run(tmp_path, envelopes=[envelope])), "work-x")
    assert "no billable usage" in report


# ------------------------------------------- tokens as evidence of work

def _two_papers(tmp_path, worked_output, thin_output):
    """One paper the model worked and one it barely touched, same work dir."""
    work = tmp_path / "work-evidence"
    (work / "meta").mkdir(parents=True)
    for doi, output, turns in (("10.1000_worked", worked_output, 14),
                               ("10.1000_thin", thin_output, 1)):
        envelope = json.loads(json.dumps(REAL_ENVELOPE))
        envelope["num_turns"] = turns
        envelope["usage"]["output_tokens"] = output
        envelope["modelUsage"]["claude-opus-5"]["outputTokens"] = output
        (work / "meta" / f"{doi}.usage.jsonl").write_text(
            json.dumps(envelope) + "\n", encoding="utf-8")
    return work


def test_the_per_paper_summary_is_stated_in_output_tokens(tmp_path):
    """Output is the per-paper evidence of reasoning; dollars are not."""
    report = render(from_envelopes(_two_papers(tmp_path, 13100, 200)), "work-evidence")
    assert "Per paper: median" in report
    assert "output tokens" in report
    # The old summary led with a median dollar figure. It must not come back:
    # the whole point of the line is to be comparable across papers as work.
    assert "Per paper: median $" not in report


def test_the_least_worked_paper_is_named(tmp_path):
    """A paper labelled without being analysed is invisible in a run total and
    in a median. Naming the floor is the only place it surfaces."""
    report = render(from_envelopes(_two_papers(tmp_path, 13100, 200)), "work-evidence")
    assert "Least-worked paper: 200 output tokens over 1 request — 10.1000_thin" in report


def test_the_per_paper_listing_is_ordered_by_output_not_by_price(tmp_path):
    """Cache-read dominates price and says nothing about engagement, so a
    price ordering can rank a barely-read paper above a heavily-worked one."""
    work = _two_papers(tmp_path, 13100, 200)
    report = render(from_envelopes(work), "work-evidence", per_paper=True)
    # Sliced to the listing: the floor line above it also names the thin paper.
    listing = report.split("Per paper, most output first:", 1)[1]
    assert listing.index("10.1000_worked") < listing.index("10.1000_thin")


def test_the_price_line_comes_last_and_is_not_called_a_bill(tmp_path):
    """A subscription run is not billed per token. The figure stays for the
    drift check against the CLI, but it is a footer, not the headline."""
    report = render(from_envelopes(_two_papers(tmp_path, 13100, 200)), "work-evidence")
    lines = [line for line in report.splitlines() if line.strip()]
    price = [i for i, line in enumerate(lines) if line.startswith("List-price equivalent")]
    assert price, "the list-price line should still be printed"
    assert "not a bill" in lines[price[0]]
    # Everything above it is tokens, requests and time; nothing below but the
    # price split and the drift warning.
    assert all("output tokens" not in line for line in lines[price[0]:])
