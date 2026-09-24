"""Guards for the usage report, one per way it produced a wrong number first.

`harness.usage` rebuilds which models a run used and how many tokens, from the
transcripts `claude -p` already persisted or the envelopes it now writes.
Every guard here is a defect that was real before it was a test:

  * summing per JSONL line instead of per `message.id` overstated one sampled
    transcript's output by 39%, because streaming re-emits a message;
  * counting the CLI's injected usage-limit messages as API requests
    overstated the v0.0.21 corpus run by exactly 145;
  * attributing every session in the project directory to a paper would have
    swept in 2,208 unrelated interactive sessions.

Nothing is priced. The counts are checked against the CLI's own instead: where
a session left both an envelope and a transcript the report compares them, and
on the runs saved on this machine they agreed exactly in 545 of 552 sessions.
That is what makes the counts a measurement rather than an assertion.

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

from harness.usage import (  # noqa: E402
    canonical, check_tokens, collect, from_envelopes, read_session, render, requested_models,
)


# --------------------------------------------------------------- model names


def test_a_dated_model_name_joins_its_undated_row():
    """Transcripts carry `claude-haiku-4-5-20251001`; envelopes need not."""
    assert canonical("claude-haiku-4-5-20251001") == "claude-haiku-4-5"
    assert canonical("claude-opus-5") == "claude-opus-5"
    assert canonical(None) is None


def test_a_model_no_list_has_heard_of_still_gets_a_row(tmp_path):
    """The price table this replaced named five models, and every other one
    came out as `price unknown`. Counting tokens needs no list of models."""
    path = _transcript(tmp_path, _line(message={"model": "claude-unreleased-9"}))
    report = render([read_session(path)], "work-x")
    assert any(line.startswith("claude-unreleased-9") for line in report.splitlines())
    assert "unknown" not in report


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
                "cache_creation_input_tokens": 500,
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


#: One request's usage, recording 40 of its 100 output tokens as thinking.
THINKING = {"input_tokens": 2, "output_tokens": 100, "cache_read_input_tokens": 1_000,
            "cache_creation_input_tokens": 500,
            "output_tokens_details": {"thinking_tokens": 40}}


def test_thinking_is_read_per_message_and_sits_inside_output(tmp_path):
    """Thinking tokens are output tokens. Adding them to the total would count
    them twice; showing them apart is the point."""
    path = _transcript(
        tmp_path,
        _line(message={"usage": THINKING}),
        _line(message={"usage": THINKING}),  # a streamed copy, counted once
        _line(requestId="req_2", uuid="u2", message={"id": "msg_2", "usage": THINKING}),
    )
    session = read_session(path)
    tokens = session.by_model["claude-opus-5"]
    assert (tokens.output, tokens.thinking, tokens.thinking_unrecorded) == (200, 80, 0)
    assert tokens.total == 2 * (2 + 100 + 1_000 + 500)
    assert "40% of it thinking" in render([session], "work-x")


def test_no_thinking_count_is_not_recorded_rather_than_zero(tmp_path):
    """The CLI recorded no thinking for the first runs. A 0 there would claim
    the model did not think, which nobody measured."""
    session = read_session(_transcript(tmp_path, _line()))
    assert session.by_model["claude-opus-5"].thinking_unrecorded == 100
    report = render([session], "work-x")
    row = next(line for line in report.splitlines() if line.startswith("claude-opus-5"))
    assert "not recorded" in row
    assert "thinking not recorded" in report


def test_a_partly_recorded_run_says_how_much_thinking_is_missing(tmp_path):
    path = _transcript(
        tmp_path,
        _line(),
        _line(requestId="req_2", uuid="u2", message={"id": "msg_2", "usage": THINKING}),
    )
    report = render([read_session(path)], "work-x")
    assert "40 (partial)" in report
    assert "Thinking was not recorded for 100 of 200 output tokens" in report


def test_a_record_with_only_the_lifetime_split_still_counts_its_writes(tmp_path):
    """Every transcript measured carries the flat write total, but the 5-minute
    and 1-hour split alone is the same count and must not read as no writes."""
    usage = {"input_tokens": 2, "output_tokens": 100, "cache_read_input_tokens": 1_000,
             "cache_creation": {"ephemeral_1h_input_tokens": 500,
                                "ephemeral_5m_input_tokens": 20}}
    session = read_session(_transcript(tmp_path, _line(message={"usage": usage})))
    assert session.by_model["claude-opus-5"].cache_write == 520


# --------------------------------------------------------------- end to end


def test_it_reproduces_the_v0021_corpus_run():
    """The real thing: 392 papers, from transcripts, to the token.

    Skips where the transcripts are absent -- another machine, or a CLI that
    has pruned them. The numbers below were independently derived three times
    (two subagents and this module) before being written down.
    """
    sessions = collect("work-corpus-v0021-r1")
    if not sessions:
        pytest.skip("no work-corpus-v0021-r1 transcripts on this machine")

    report = render(sessions, "work-corpus-v0021-r1")
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
    assert sum(t.cache_write for t in opus) == 37_446_096


# --------------------------------------------------------- envelopes

#: A real `claude -p --output-format json` envelope from the smoke run that
#: exercised the invocation change, trimmed to the fields the reader uses.
REAL_ENVELOPE = {
    "type": "result", "subtype": "success", "is_error": False, "result": "DONE",
    "num_turns": 5, "duration_ms": 148000, "duration_api_ms": 145686,
    "usage": {"input_tokens": 10, "output_tokens": 13100,
              "cache_read_input_tokens": 193525,
              "cache_creation": {"ephemeral_1h_input_tokens": 58974,
                                 "ephemeral_5m_input_tokens": 0}},
    "modelUsage": {"claude-opus-5": {
        "inputTokens": 10, "outputTokens": 13100,
        "cacheReadInputTokens": 193525, "cacheCreationInputTokens": 58974}},
}

#: Two more real envelopes, one per CLI generation, because the two put the
#: thinking count in different places. The older CLI (the envelope smoke run)
#: gives it only in the flat block; the newer one (a paper from
#: work-glyphfix-17) gives it per model as well. Trimmed like the one above.
OLDER_CLI_ENVELOPE = {
    "type": "result", "subtype": "success", "is_error": False, "result": "DONE",
    "num_turns": 6, "duration_ms": 228626, "duration_api_ms": 226005,
    "usage": {"input_tokens": 12, "output_tokens": 19984,
              "cache_read_input_tokens": 265057, "cache_creation_input_tokens": 66378,
              "output_tokens_details": {"thinking_tokens": 11255}},
    "modelUsage": {"claude-opus-5": {
        "inputTokens": 12, "outputTokens": 19984,
        "cacheReadInputTokens": 265057, "cacheCreationInputTokens": 66378}},
}
NEWER_CLI_ENVELOPE = {
    "type": "result", "subtype": "success", "is_error": False, "result": "DONE",
    "num_turns": 17, "duration_ms": 207186, "duration_api_ms": 202533,
    "usage": {"input_tokens": 34, "output_tokens": 14719,
              "cache_read_input_tokens": 1334580, "cache_creation_input_tokens": 125836,
              "output_tokens_details": {"thinking_tokens": 8458}},
    "modelUsage": {"claude-opus-5": {
        "inputTokens": 34, "outputTokens": 14719, "thinkingTokens": 8458,
        "cacheReadInputTokens": 1334580, "cacheCreationInputTokens": 125836}},
}


def _run(tmp_path, doi="10.1000_a", envelopes=(REAL_ENVELOPE,)):
    work = tmp_path / "work-x"
    (work / "meta").mkdir(parents=True)
    (work / "meta" / f"{doi}.usage.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in envelopes), encoding="utf-8")
    return work


def test_an_envelope_puts_the_clis_own_counts_in_the_table(tmp_path):
    report = render(from_envelopes(_run(tmp_path)), "work-x")
    row = next(line for line in report.splitlines() if line.startswith("claude-opus-5"))
    # This envelope predates thinking counts, so that cell says so.
    assert row.split() == ["claude-opus-5", "10", "13,100", "not", "recorded",
                           "193,525", "58,974", "265,609"]


def test_every_attempt_in_the_file_is_counted(tmp_path):
    """143 of 392 papers were retried; the file appends one line per attempt."""
    sessions = from_envelopes(_run(tmp_path, envelopes=[REAL_ENVELOPE, REAL_ENVELOPE]))
    assert len(sessions) == 2
    assert sum(t.output for s in sessions for t in s.by_model.values()) == 26200


def test_cache_writes_are_one_count_straight_from_modelusage(tmp_path):
    """The 5-minute/1-hour split only ever set a price. The count is the CLI's."""
    tokens = from_envelopes(_run(tmp_path))[0].by_model["claude-opus-5"]
    assert tokens.cache_write == 58974


def test_a_newer_envelope_gives_thinking_per_model(tmp_path):
    session = from_envelopes(_run(tmp_path, envelopes=[NEWER_CLI_ENVELOPE]))[0]
    tokens = session.by_model["claude-opus-5"]
    assert (tokens.thinking, tokens.thinking_unrecorded) == (8458, 0)


def test_an_older_envelope_gives_thinking_only_in_its_flat_block(tmp_path):
    """The flat total equals the per-model figure on all 418 envelopes measured
    that carry both, so for one model it is that model's own. With two it
    cannot be split, and a guess would be a number nobody measured."""
    session = from_envelopes(_run(tmp_path, envelopes=[OLDER_CLI_ENVELOPE]))[0]
    tokens = session.by_model["claude-opus-5"]
    assert (tokens.thinking, tokens.thinking_unrecorded) == (11255, 0)

    two = json.loads(json.dumps(OLDER_CLI_ENVELOPE))
    two["modelUsage"]["claude-haiku-4-5-20251001"] = {
        "inputTokens": 5, "outputTokens": 300,
        "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0}
    by_model = from_envelopes(_run(tmp_path / "two", envelopes=[two]))[0].by_model
    assert set(by_model) == {"claude-opus-5", "claude-haiku-4-5"}
    assert all(t.thinking == 0 and t.thinking_unrecorded == t.output
               for t in by_model.values())


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
    # The token check still sees the instrumented part.
    assert len(from_envelopes(work, complete_only=False)) == 1

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


def test_a_run_where_every_attempt_errored_reports_rather_than_crashes(tmp_path):
    """`max(x, *())` raises, so an all-refusals run crashed the reporter."""
    envelope = {"type": "result", "is_error": True, "api_error_status": "429",
                "result": "rate_limit_error", "num_turns": 1,
                "total_cost_usd": 0, "usage": {}, "modelUsage": {}}
    report = render(from_envelopes(_run(tmp_path, envelopes=[envelope])), "work-x")
    assert "no token usage" in report


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


def test_the_report_prints_no_dollar_figure(tmp_path):
    """Deleted outright. The report answers which models ran and how many
    tokens they used, and a dollar figure needed a price table kept in step
    with Anthropic's by hand. The envelope still carries the CLI's own cost,
    so this proves the report ignores it rather than that it was never there."""
    envelope = json.loads(json.dumps(REAL_ENVELOPE))
    envelope["total_cost_usd"] = 1.0141
    envelope["modelUsage"]["claude-opus-5"]["costUSD"] = 1.0141
    work = _run(tmp_path, envelopes=[envelope])
    report = render(from_envelopes(work), "work-x", per_paper=True,
                    check=check_tokens(from_envelopes(work), []))
    assert "$" not in report
    assert "price" not in report.lower()


# ------------------------------------------------ models requested and served


def test_the_requested_model_is_read_beside_each_result(tmp_path):
    """The runner writes `meta/<doi>.model` only beside a result, so a paper
    without one is reported as not recorded rather than assumed to have the pin."""
    work = _two_papers(tmp_path, 13100, 200)
    (work / "meta" / "10.1000_worked.model").write_text("claude-opus-5\n")
    requested = requested_models(work, {"10.1000_worked", "10.1000_thin"})
    assert requested == {"10.1000_worked": "claude-opus-5"}
    report = render(from_envelopes(work), "work-evidence", requested=requested)
    assert "Model requested: claude-opus-5 for 1 paper, not recorded for 1" in report
    assert "Model served:    claude-opus-5 for all 2 papers" in report
    assert "other than the one requested" not in report


def test_a_run_that_recorded_no_model_says_so(tmp_path):
    report = render(from_envelopes(_two_papers(tmp_path, 13100, 200)), "work-evidence")
    assert "Model requested: not recorded for any of the 2 papers" in report


def test_a_paper_another_model_served_is_named(tmp_path):
    """A second row in the table says a second model ran. Only this says for
    which paper, and that decides whose determination the record holds."""
    work = _two_papers(tmp_path, 13100, 200)
    path = work / "meta" / "10.1000_thin.usage.jsonl"
    envelope = json.loads(path.read_text())
    envelope["modelUsage"]["claude-sonnet-5"] = envelope["modelUsage"].pop("claude-opus-5")
    path.write_text(json.dumps(envelope) + "\n")
    requested = {"10.1000_worked": "claude-opus-5", "10.1000_thin": "claude-opus-5"}
    report = render(from_envelopes(work), "work-evidence", per_paper=True,
                    requested=requested)
    assert "Model served:    claude-opus-5 for 1 paper, claude-sonnet-5 for 1 paper" in report
    assert "1 paper served by a model other than the one requested:" in report
    assert "  10.1000_thin: requested claude-opus-5, served claude-sonnet-5" in report
    assert "! served by claude-sonnet-5, requested claude-opus-5" in report


# ------------------------------------------------------------ the token check

#: A transcript request with exactly REAL_ENVELOPE's counts, plus a thinking
#: count that envelope never had.
MATCHING = {"input_tokens": 10, "output_tokens": 13100, "cache_read_input_tokens": 193525,
            "cache_creation_input_tokens": 58974,
            "output_tokens_details": {"thinking_tokens": 6000}}


def _paired(tmp_path, transcript_usage):
    """One envelope and the transcript of the same session."""
    # `_transcript` writes `session.jsonl`, and the session id names the file.
    work = _run(tmp_path, envelopes=[dict(REAL_ENVELOPE, session_id="session")])
    directory = tmp_path / "projects"
    directory.mkdir()
    _transcript(directory, _line(message={"usage": transcript_usage}))
    return from_envelopes(work, complete_only=False), [directory]


def test_the_token_check_passes_when_both_records_agree(tmp_path):
    """The envelope predates thinking counts and the transcript has one: a gap
    in one record, which must not read as a disagreement about the count."""
    envelopes, directories = _paired(tmp_path, MATCHING)
    check = check_tokens(envelopes, directories)
    assert (check.envelopes, check.compared, check.matched) == (1, 1, 1)
    report = render(envelopes, "work-x", check=check)
    assert "Token check: transcripts match the CLI's own counts in the only session." in report


def test_the_token_check_names_a_session_whose_records_disagree(tmp_path):
    """Seven sessions of the v0.0.25 corpus run do this, and nothing reported
    it until the two records were held against each other."""
    envelopes, directories = _paired(tmp_path, dict(MATCHING, cache_read_input_tokens=150000))
    check = check_tokens(envelopes, directories)
    assert check.differing == ["10.1000_a"]
    report = render(envelopes, "work-x", check=check)
    assert "Token check: transcripts match the CLI's own counts in 0 of 1 session." in report
    assert "  Differ in 1:\n    10.1000_a" in report


def test_the_token_check_says_when_it_could_not_run(tmp_path):
    """No envelopes, or envelopes with no transcript left, and each says so.
    An attempt that errored used no tokens and is not a session to compare."""
    session = read_session(_transcript(tmp_path, _line()))
    report = render([session], "work-x", check=check_tokens([], [tmp_path]))
    assert "Token check: not possible, this run recorded no envelopes" in report

    errored = {"type": "result", "is_error": True, "api_error_status": "429",
               "result": "rate_limit_error", "num_turns": 1, "usage": {}, "modelUsage": {}}
    work = _run(tmp_path / "run", envelopes=[REAL_ENVELOPE, errored])
    check = check_tokens(from_envelopes(work, complete_only=False), [tmp_path / "gone"])
    assert (check.envelopes, check.compared) == (1, 0)
    report = render(from_envelopes(work), "work-x", check=check)
    assert "Token check: not possible, no transcript was found for the only session." in report
