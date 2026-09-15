#!/usr/bin/env python3
"""What a run cost, reconstructed from the transcripts it already left behind.

    python -m pe.usage --list
    python -m pe.usage --work work-corpus-v0021-r1
    python -m pe.usage --work work-corpus-v0021-r1 --per-paper

Stage 2 invokes `claude -p` with `--output-format text`, so the only thing the
per-paper log captures is the agent's final reply -- every log in the 392-paper
v0.0.21 run is five bytes reading `DONE`. No tokens, no cost, no request count.

They were never lost, only unrecorded. `run_headless.sh` does `cd "$ROOT"`
before it starts, so each `claude -p` call persisted a session transcript under
`~/.claude/projects/<encoded-cwd>/`, and every assistant line in one carries a
`message.usage` block. This module reads those transcripts and rebuilds the
run: per model, per paper, per attempt. It changes nothing about how a run is
made, touches no pack file, and cannot affect a determination -- which is the
whole reason it is worth having before the invocation is touched.

Three things it must get right, each of which produced a wrong number first:

**Dedupe by `message.id`.** Streaming re-emits the same assistant message on
several JSONL lines. One sampled transcript had 53 assistant lines carrying 38
unique ids; summing per line overstated its output tokens by 39%. Verified
across 300 transcripts and 2,101 id groups that duplicate copies are byte-equal
in their usage, so keeping any one copy is safe.

**A session is only part of a run if it says which paper it is.** The project
directory also holds ordinary interactive sessions started from the skill
folder -- 2,208 of 3,667 in the main directory, carrying 11,359 assistant
messages that have nothing to do with any paper. The filter is the prompt-file
path in the first user message, which names both the work directory and the
DOI. Filtering by model would not work: marked sessions legitimately contain
more than one model.

**`<synthetic>` is not an API request.** When a session hits a usage limit the
CLI injects an assistant message with `model: "<synthetic>"` and all-zero
usage. The v0.0.21 corpus run contains 145 of them -- the 145 spawns the
runner's own abort-sentinel comment records as wasted. They are excluded from
the request count and reported on their own line, because that count is the
most actionable number here.

One honest limitation. A transcript records per-message `timestamp` and nothing
else about time, so the span this module reports is the AGENT's wall-clock --
model inference plus tool execution plus queueing. It is not "model time". The
envelope's `duration_api_ms` is, and it becomes available once the invocation
switches to `--output-format json`; until then this report says agent
wall-clock and means it.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pe.pricing import RATES, canonical, cost  # noqa: E402
from pe.runroot import run_root  # noqa: E402

#: Where the CLI persists sessions: one directory per working directory, named
#: by flattening that path's separators. `run_headless.sh` cd's to the skill
#: root before every call, so the skill root's own encoded name is the
#: directory to read.
PROJECTS = Path.home() / ".claude" / "projects"

_SKILL_ROOT = Path(__file__).resolve().parent.parent


def _encoded(path: Path | str) -> str:
    """The CLI's project-directory name for a working directory."""
    return str(path).replace("/", "-").replace(".", "-")


def _project_glob() -> str:
    """Match this skill's transcripts, including worktree checkouts of it.

    Derived from the filesystem rather than written down, because `pe/` may not
    name the task in code (tests/test_seam.py) -- and because a hardcoded name
    would be wrong in any worktree anyway. A worktree's repo path differs but
    the tail inside the repo does not, so the glob anchors on the tail.
    """
    try:
        inside_repo = _SKILL_ROOT.relative_to(_SKILL_ROOT.parents[2])
    except (ValueError, IndexError):  # pragma: no cover - unusual layout
        return f"*{_encoded(_SKILL_ROOT.name)}"
    return f"*-{_encoded(inside_repo)}"


PROJECT_GLOB = _project_glob()

#: The marker that makes a session attributable: the prompt file the agent is
#: told to read, which carries the work directory and the DOI slug. Written by
#: `pe.prepare` and quoted verbatim into the task string by `run_headless.sh`.
PROMPT_PATH = re.compile(
    r"/(work-[^/\s\"']+)/prompts/([^/\s\"']+)\.txt"
)

#: The CLI's stand-in for a message it generated itself rather than fetching.
SYNTHETIC = "<synthetic>"


class UsageError(RuntimeError):
    """A caller asked for a run that cannot be reported on."""


@dataclass
class Tokens:
    """One model's token counts. Additive, so a run is a sum of sessions."""

    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write_1h: int = 0
    cache_write_5m: int = 0
    requests: int = 0

    @property
    def total(self) -> int:
        return (
            self.input
            + self.output
            + self.cache_read
            + self.cache_write_1h
            + self.cache_write_5m
        )

    def add(self, other: "Tokens") -> None:
        self.input += other.input
        self.output += other.output
        self.cache_read += other.cache_read
        self.cache_write_1h += other.cache_write_1h
        self.cache_write_5m += other.cache_write_5m
        self.requests += other.requests

    def as_usage(self) -> dict:
        """The shape `pricing.cost` reads, so one pricing path serves both."""
        return {
            "input_tokens": self.input,
            "output_tokens": self.output,
            "cache_read_input_tokens": self.cache_read,
            "cache_creation": {
                "ephemeral_1h_input_tokens": self.cache_write_1h,
                "ephemeral_5m_input_tokens": self.cache_write_5m,
            },
        }


@dataclass
class Session:
    """One `claude -p` call: one attempt at one paper."""

    work: str
    doi: str
    session_id: str
    path: Path
    by_model: dict[str, Tokens] = field(default_factory=lambda: defaultdict(Tokens))
    limit_refusals: int = 0
    #: The CLI's own list-price figure, when this session came from an envelope.
    #: Not used for the total -- one arithmetic path serves both sources -- but
    #: compared against it, so a change to Anthropic's published prices shows up
    #: as a disagreement instead of silently mispricing every later run.
    reported_cost: float | None = None
    started: datetime | None = None
    ended: datetime | None = None
    #: From an envelope only. `duration_ms` is the agent's span and
    #: `duration_api_ms` is time actually spent in inference -- the distinction a
    #: transcript cannot make, since it carries only per-message timestamps.
    duration_ms: int = 0
    api_ms: int = 0

    @property
    def seconds(self) -> float:
        if self.duration_ms:
            return self.duration_ms / 1000.0
        if self.started is None or self.ended is None:
            return 0.0
        return max(0.0, (self.ended - self.started).total_seconds())


def _stamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def read_session(path: Path) -> Session | None:
    """Parse one transcript, or None if it is not part of any run.

    Tolerant by construction: a transcript is an append-only log that may have
    been truncated mid-write, and one unparseable line must not lose the other
    thousand.
    """
    work = doi = None
    seen: set[str] = set()
    by_model: dict[str, Tokens] = defaultdict(Tokens)
    refusals = 0
    first = last = None

    try:
        handle = path.open(encoding="utf-8", errors="replace")
    except OSError:
        return None

    with handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except (ValueError, TypeError):
                continue
            if not isinstance(record, dict):
                continue

            when = _stamp(record.get("timestamp"))
            if when is not None:
                first = when if first is None else min(first, when)
                last = when if last is None else max(last, when)

            kind = record.get("type")

            if kind == "user" and work is None:
                content = (record.get("message") or {}).get("content")
                text = content if isinstance(content, str) else json.dumps(content)
                found = PROMPT_PATH.search(text or "")
                if found:
                    work, doi = found.group(1), found.group(2)
                continue

            if kind != "assistant":
                continue

            message = record.get("message") or {}
            model = message.get("model")
            if model == SYNTHETIC:
                refusals += 1
                continue

            # Dedupe on the message id. Falls back to the request id, then to
            # the record uuid, so a record missing one identifier is still
            # counted once rather than dropped.
            key = message.get("id") or record.get("requestId") or record.get("uuid")
            if key is not None:
                if key in seen:
                    continue
                seen.add(key)

            usage = message.get("usage")
            if not isinstance(usage, dict):
                continue

            def count(name: str, src: dict = usage) -> int:
                value = src.get(name)
                return int(value) if isinstance(value, (int, float)) else 0

            creation = usage.get("cache_creation")
            if isinstance(creation, dict):
                write_1h = count("ephemeral_1h_input_tokens", creation)
                write_5m = count("ephemeral_5m_input_tokens", creation)
            else:
                write_1h, write_5m = 0, count("cache_creation_input_tokens")

            bucket = by_model[canonical(model) or "unknown"]
            bucket.input += count("input_tokens")
            bucket.output += count("output_tokens")
            bucket.cache_read += count("cache_read_input_tokens")
            bucket.cache_write_1h += write_1h
            bucket.cache_write_5m += write_5m
            bucket.requests += 1

    if work is None or doi is None:
        return None

    return Session(
        work=work,
        doi=doi,
        session_id=path.stem,
        path=path,
        by_model=by_model,
        limit_refusals=refusals,
        started=first,
        ended=last,
    )


#: Written by `run_headless.sh`, one JSON envelope per ATTEMPT. Present only for
#: runs made after the invocation switched to `--output-format json`.
ENVELOPE_GLOB = "meta/*.usage.jsonl"


def from_envelopes(work_dir: Path) -> list[Session]:
    """Read a run's usage from the envelopes it recorded, if it recorded any.

    Preferred over the transcript scan wherever it is available, because it is
    the CLI's own accounting rather than a reconstruction: it carries
    `duration_api_ms`, which is real model time, where a transcript can only
    give the agent's wall-clock span. Runs made before the invocation changed
    have no envelopes and fall back automatically.
    """
    work_dir = Path(work_dir)
    # Envelopes only exist for papers run after the invocation changed, and a
    # run can straddle that line -- resumed after a usage limit, or topped up.
    # Returning the instrumented subset would let the report describe part of a
    # run as if it were all of it, silently and with no way to tell. The
    # transcript path covers every paper either way, so a partial set defers to
    # it rather than competing with it.
    sessions: list[Session] = []
    for path in sorted(work_dir.glob(ENVELOPE_GLOB)):

        doi = path.name[: -len(".usage.jsonl")]
        for attempt, line in enumerate(path.read_text(encoding="utf-8",
                                                      errors="replace").splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                envelope = json.loads(line)
            except (ValueError, TypeError):
                continue
            if not isinstance(envelope, dict):
                continue
            session = Session(work=work_dir.name, doi=doi,
                              session_id=str(envelope.get("session_id") or
                                             f"{doi}#{attempt}"),
                              path=path)
            # `modelUsage` is per-model and already aggregated across turns,
            # which is the shape the report wants. The flat `usage` block is the
            # same totals without the model split; its only unique contribution
            # is the cache-write TTL split, which decides whether those tokens
            # cost 2.0x or 1.25x the input rate -- a ~$140 difference over the
            # v0.0.21 corpus run. Its `iterations` array must never be summed:
            # it lists one entry per turn but not reliably all of them.
            flat = envelope.get("usage") or {}
            creation = flat.get("cache_creation") or {}
            hour = int(creation.get("ephemeral_1h_input_tokens") or 0)
            five = int(creation.get("ephemeral_5m_input_tokens") or 0)
            # Absent a split, price at the CHEAPER rate rather than guess high.
            # `hour >= five` would be true when both are zero, which is the
            # degenerate case this comment claims to handle and quietly got
            # backwards -- a run with no TTL reported would have been billed at
            # 2.0x. Guessing low is self-correcting: the drift line below
            # compares against the CLI's own total and says so.
            long_lived = hour > 0 and hour >= five

            per_model = envelope.get("modelUsage")
            # An API-error envelope carries `modelUsage: {}` and no top-level
            # `model`, so the fallback below would book it under "unknown" with
            # zero tokens -- inventing a row the report then flags as spend it
            # could not price. Nothing was billed; count it as a refusal and
            # move on. That also restores the wasted-spawn tally on this path,
            # which the transcript reader has and this one silently lacked.
            if not (isinstance(per_model, dict) and per_model) and not (
                    envelope.get("usage") or {}).get("output_tokens"):
                if envelope.get("is_error"):
                    refusals_only = Session(work=work_dir.name, doi=doi,
                                            session_id=session.session_id, path=path)
                    refusals_only.limit_refusals = 1
                    sessions.append(refusals_only)
                continue
            if isinstance(per_model, dict) and per_model:
                for name, counts in per_model.items():
                    if not isinstance(counts, dict):
                        continue
                    tokens = session.by_model[canonical(name) or "unknown"]
                    tokens.input += int(counts.get("inputTokens") or 0)
                    tokens.output += int(counts.get("outputTokens") or 0)
                    tokens.cache_read += int(counts.get("cacheReadInputTokens") or 0)
                    written = int(counts.get("cacheCreationInputTokens") or 0)
                    # `modelUsage` gives a total with no TTL; the flat block
                    # gives the TTL with no model. Collapsing the flat split to
                    # one boolean sent every model's writes to whichever side
                    # won, so a genuinely mixed envelope priced part of itself
                    # at the wrong rate. Apply the flat block's own ratio.
                    if hour and five:
                        share = hour / (hour + five)
                        tokens.cache_write_1h += round(written * share)
                        tokens.cache_write_5m += written - round(written * share)
                    elif long_lived:
                        tokens.cache_write_1h += written
                    else:
                        tokens.cache_write_5m += written
            else:
                tokens = session.by_model[canonical(envelope.get("model")) or "unknown"]
                tokens.input += int(flat.get("input_tokens") or 0)
                tokens.output += int(flat.get("output_tokens") or 0)
                tokens.cache_read += int(flat.get("cache_read_input_tokens") or 0)
                tokens.cache_write_1h += hour
                tokens.cache_write_5m += five or (
                    0 if hour else int(flat.get("cache_creation_input_tokens") or 0))

            # `num_turns` counts the SESSION, not a model, so it cannot be split
            # when more than one model ran. Attributed to the one that produced
            # the most output, which is the model doing the work; every run this
            # harness makes is single-model anyway.
            turns = int(envelope.get("num_turns") or 0)
            if turns and session.by_model:
                busiest = max(session.by_model.values(), key=lambda t: t.output)
                busiest.requests += turns

            session.duration_ms = int(envelope.get("duration_ms") or 0)
            session.api_ms = int(envelope.get("duration_api_ms") or 0)
            reported = envelope.get("total_cost_usd")
            if isinstance(reported, (int, float)):
                session.reported_cost = float(reported)
            sessions.append(session)

    # Coverage is checked against the DOIs that actually parsed, not against the
    # files on disk. An envelope truncated mid-write still creates a file, so a
    # filename-level check passes while the loop above silently drops that
    # paper -- and the report then describes part of a run as all of it, which
    # is the exact thing this guard exists to stop. Same reasoning as the
    # partial-instrumentation case: the transcript path covers every paper, so
    # anything short defers to it.
    manifest = work_dir / "manifest.json"
    if manifest.is_file():
        try:
            entries = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            entries = []
        expected = {str(e.get("doi")) for e in entries
                    if isinstance(e, dict) and e.get("doi") and "error" not in e}
        if expected and not expected <= {s.doi for s in sessions}:
            return []

    return sessions


def project_dirs(explicit: list[str] | None = None) -> list[Path]:
    if explicit:
        return [Path(p).expanduser() for p in explicit]
    if not PROJECTS.is_dir():
        return []
    return sorted(p for p in PROJECTS.glob(PROJECT_GLOB) if p.is_dir())


def collect(work: str | None = None, projects: list[str] | None = None) -> list[Session]:
    """Every attributable session, optionally narrowed to one work directory."""
    found: list[Session] = []
    for directory in project_dirs(projects):
        for path in sorted(directory.glob("*.jsonl")):
            session = read_session(path)
            if session is None:
                continue
            if work and session.work != work:
                continue
            found.append(session)
    return found


# ---------------------------------------------------------------- reporting


def _thousands(value: int) -> str:
    return f"{value:,}"


def _duration(seconds: float) -> str:
    """Readable at both ends: a smoke run is seconds, a corpus run is hours."""
    if seconds < 90:
        return f"{seconds:.0f}s"
    if seconds < 5400:
        return f"{seconds / 60:.0f}m"
    return f"{seconds / 3600:.1f}h"


def _table(by_model: dict[str, Tokens]) -> list[str]:
    """The per-model block, in the shape Claude Code's own run summary uses."""
    head = ("Model", "Input", "Output", "Cache read", "Cache write", "Total")
    rows = []
    for name in sorted(by_model, key=lambda m: -by_model[m].total):
        tokens = by_model[name]
        rows.append(
            (
                name,
                _thousands(tokens.input),
                _thousands(tokens.output),
                _thousands(tokens.cache_read),
                _thousands(tokens.cache_write_1h + tokens.cache_write_5m),
                _thousands(tokens.total),
            )
        )
    grand = Tokens()
    for tokens in by_model.values():
        grand.add(tokens)
    if len(by_model) > 1:
        rows.append(("All models", "", "", "", "", _thousands(grand.total)))

    if not rows:
        # Every attempt errored, so nothing was billed. `max(x, *())` raises,
        # which turned a run of pure refusals into a crash in the reporter
        # rather than a report saying no tokens were spent.
        return ["(no billable usage: every attempt returned an error)"]

    widths = [max(len(head[i]), *(len(r[i]) for r in rows)) for i in range(6)]
    lines = ["  ".join(h.ljust(widths[i]) if i == 0 else h.rjust(widths[i])
                       for i, h in enumerate(head))]
    for row in rows:
        lines.append("  ".join(cell.ljust(widths[i]) if i == 0 else cell.rjust(widths[i])
                               for i, cell in enumerate(row)))
    return lines


def _priced(by_model: dict[str, Tokens]) -> tuple[float, list[str]]:
    """Total list-price dollars, plus the names of any models we cannot price."""
    total, unknown = 0.0, []
    for name, tokens in by_model.items():
        amount = cost(name, tokens.as_usage())
        if amount is None:
            unknown.append(name)
        else:
            total += amount
    return total, unknown


def _split(by_model: dict[str, Tokens]) -> str:
    """Where the money went, which is the finding this report exists to show."""
    buckets = {"cache-write": 0.0, "cache-read": 0.0, "output": 0.0, "input": 0.0}
    for name, tokens in by_model.items():
        if canonical(name) not in RATES:
            continue
        rate_in, rate_out = RATES[canonical(name)]
        buckets["input"] += tokens.input * rate_in
        buckets["output"] += tokens.output * rate_out
        buckets["cache-read"] += tokens.cache_read * rate_in * 0.10
        buckets["cache-write"] += (
            tokens.cache_write_1h * rate_in * 2.00 + tokens.cache_write_5m * rate_in * 1.25
        )
    grand = sum(buckets.values())
    if grand <= 0:
        return ""
    parts = []
    for name, value in sorted(buckets.items(), key=lambda kv: -kv[1]):
        if value <= 0:
            continue
        share = 100 * value / grand
        # A bucket that rounds to 0% is not absent, and "input 0%" reads like a
        # bug. Input really is that small on these runs -- 0.007% of the corpus
        # run -- because the paper arrives as cache, not as fresh input.
        parts.append(f"{name} {share:.0f}%" if share >= 0.5 else f"{name} <1%")
    return "  " + " · ".join(parts)


def render(sessions: list[Session], work: str, per_paper: bool = False) -> str:
    by_model: dict[str, Tokens] = defaultdict(Tokens)
    papers: dict[str, list[Session]] = defaultdict(list)
    refusals = 0
    seconds = 0.0
    for session in sessions:
        for name, tokens in session.by_model.items():
            by_model[name].add(tokens)
        papers[session.doi].append(session)
        refusals += session.limit_refusals
        seconds += session.seconds

    requests = sum(t.requests for t in by_model.values())
    total, unknown = _priced(by_model)
    reported = [s.reported_cost for s in sessions if s.reported_cost is not None]

    # The work directory already names the run and the pack version, so the
    # heading does not restate what task this is -- which it also may not do.
    out = [f"Run usage — {work}", ""]
    out += _table(by_model)
    out.append("")
    out.append(f"List-price estimate for this run: ${total:,.2f}")
    split = _split(by_model)
    if split:
        out.append(split)
    if unknown:
        out.append(f"  price unknown for: {', '.join(sorted(unknown))} — excluded from the total")
    # Only envelopes carry the CLI's own figure. A gap means the price table
    # here and Anthropic's published rates have parted company; the table is
    # what to fix, and until then the CLI's number is the right one to believe.
    # Skipped when a model could not be priced: `total` excludes it while the
    # CLI's figure includes it, so the gap is guaranteed and says nothing about
    # the table. The missing price is already reported on its own line above.
    if reported and total > 0 and not unknown:
        drift = abs(sum(reported) - total) / total
        if drift > 0.01:
            out.append(f"  ! the CLI reported ${sum(reported):,.2f} for the same work, "
                       f"a {drift:.0%} gap — pe/pricing.py:RATES is probably stale")
    api_seconds = sum(s.api_ms for s in sessions) / 1000.0
    out.append(
        f"{_thousands(requests)} API requests across {_thousands(len(sessions))} sessions, "
        f"{len(papers)} papers, {_duration(seconds)} of agent wall-clock."
    )
    # `seconds` can be 0 while `api_seconds` is not: an envelope may carry
    # duration_api_ms and no duration_ms, and the transcript path sets neither.
    if api_seconds and seconds > 0:
        # Only an envelope reports this. Named separately from the span above
        # because the two differ by tool execution and queueing -- on the smoke
        # run, 146 s of inference inside a 151 s agent span.
        out.append(f"Of that, {_duration(api_seconds)} was model time "
                   f"({100 * api_seconds / seconds:.0f}% of the span).")

    retried = sum(1 for group in papers.values() if len(group) > 1)
    if retried:
        out.append(f"{retried} papers took more than one attempt; usage is summed across attempts.")
    if refusals:
        out.append(
            f"Overhead: {refusals} spawns returned a usage-limit message with zero usage "
            f"(not counted as requests)."
        )

    costs = []
    for doi, group in papers.items():
        merged: dict[str, Tokens] = defaultdict(Tokens)
        for session in group:
            for name, tokens in session.by_model.items():
                merged[name].add(tokens)
        amount, _ = _priced(merged)
        turns = sum(t.requests for t in merged.values())
        costs.append((amount, turns, doi, merged))

    if costs:
        ordered = sorted(c[0] for c in costs)
        mid = ordered[len(ordered) // 2]
        p10 = ordered[max(0, int(0.10 * (len(ordered) - 1)))]
        p90 = ordered[min(len(ordered) - 1, int(0.90 * (len(ordered) - 1)))]
        turns = sorted(c[1] for c in costs)
        out.append(
            f"Per paper: median ${mid:,.2f} (p10 ${p10:,.2f}, p90 ${p90:,.2f}), "
            f"median {turns[len(turns) // 2]} requests."
        )

    if per_paper:
        out.append("")
        out.append("Per paper, most expensive first:")
        for amount, turns, doi, merged in sorted(costs, key=lambda c: -c[0]):
            grand = Tokens()
            for tokens in merged.values():
                grand.add(tokens)
            attempts = len(papers[doi])
            suffix = f", {attempts} attempts" if attempts > 1 else ""
            out.append(
                f"  ${amount:>7,.2f}  {turns:>3} req{suffix:<14}  {doi}"
            )
            out.append(
                f"           in {_thousands(grand.input)} / out {_thousands(grand.output)}"
                f" / cache-read {_thousands(grand.cache_read)}"
                f" / cache-write {_thousands(grand.cache_write_1h + grand.cache_write_5m)}"
            )

    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--work", help="work directory name, e.g. work-corpus-v0021-r1")
    parser.add_argument("--per-paper", action="store_true", help="one block per paper")
    parser.add_argument("--list", action="store_true", dest="listing",
                        help="list the runs that have transcripts, and stop")
    parser.add_argument("--projects", nargs="*", help="override the transcript directories")
    args = parser.parse_args()

    # Envelopes first, and BEFORE insisting a transcript directory exists: a
    # fully instrumented run is completely readable without one, and the CLI
    # prunes transcripts. Demanding them anyway turned "your history is gone"
    # into "this run cannot be reported", which is false.
    sessions = []
    source = "CLI transcripts"
    if args.work and not args.projects:
        work_dir = run_root() / args.work
        sessions = from_envelopes(work_dir)
        if sessions:
            source = f"envelopes in {work_dir}"

    directories = project_dirs(args.projects)
    if not sessions:
        if not directories:
            raise UsageError(
                f"no envelopes under {run_root()}/{args.work or '<run>'} and no "
                f"transcript directories matching {PROJECTS}/{PROJECT_GLOB}. "
                f"Pass --projects to name them explicitly."
            )
        sessions = collect(args.work, args.projects)

    if args.listing:
        runs: dict[str, set[str]] = defaultdict(set)
        for session in sessions:
            runs[session.work].add(session.doi)
        if not runs:
            raise UsageError("no attributable sessions found in " +
                             ", ".join(str(d) for d in directories))
        for name in sorted(runs):
            print(f"{len(runs[name]):>4} papers  {name}")
        return 0

    if not args.work:
        raise UsageError("name a run with --work, or use --list to see which exist.")
    if not sessions:
        raise UsageError(
            f"no sessions found for {args.work!r}. Use --list to see which runs "
            f"have transcripts; the CLI may have pruned older ones."
        )

    print(render(sessions, args.work, per_paper=args.per_paper))
    # Named because the two sources are not equivalent: only the envelope
    # carries `duration_api_ms`, and only the transcript survives a work
    # directory being deleted.
    print(f"\nsource: {source}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except UsageError as exc:
        print(f"pe.usage: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
