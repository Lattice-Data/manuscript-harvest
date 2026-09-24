#!/usr/bin/env python3
"""Which models a run used and how many tokens, per model and per paper.

Reports models and tokens, and prices nothing. Output tokens and request count
are the per-paper evidence that a determination was reasoned rather than
assigned: both collapse for a paper that was not actually read, while
cache-read does not. Thinking is counted inside output and shown beside it,
wherever the CLI recorded it.

There is no dollar figure. A subscription run is not billed per token, and a
list-price equivalent needed every model's rates copied into a table and kept
in step with Anthropic's price list by hand. When it was deleted that table
lacked ten current models and had one rate wrong. Token counts need no table:
every model the CLI names gets a row.

    python -m harness.usage --list
    python -m harness.usage --work work-corpus-v0021-r1
    python -m harness.usage --work work-corpus-v0021-r1 --per-paper

Stage 2 invokes `claude -p` with `--output-format text`, so the only thing the
per-paper log captures is the agent's final reply -- every log in the 392-paper
v0.0.21 run is five bytes reading `DONE`. No tokens and no request count.

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

**Two checks, and neither needs a price.** Where a session left both an
envelope and a transcript, the report compares their token counts class by
class. The envelope is the CLI's own accounting and the transcript is this
module's reconstruction; the two are parsed independently, so agreement vouches
for both readers and a disagreement names the paper. And where the runner
recorded which model it asked for, the report names every paper that another
model served -- after a refusal fallback, say -- which the per-model table
would otherwise show only as an extra row with no paper attached.

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
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.runroot import model_of, run_root  # noqa: E402

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

    Derived from the filesystem rather than written down, because `harness/` may not
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
#: `harness.prepare` and quoted verbatim into the task string by `run_headless.sh`.
PROMPT_PATH = re.compile(
    r"/(work-[^/\s\"']+)/prompts/([^/\s\"']+)\.txt"
)

#: The CLI's stand-in for a message it generated itself rather than fetching.
SYNTHETIC = "<synthetic>"

#: How many papers or sessions a report line names before it summarises the rest.
LISTED = 10


def canonical(model: str | None) -> str | None:
    """`claude-haiku-4-5-20251001` -> `claude-haiku-4-5`.

    One model, one row. The envelope's `modelUsage` block already supplies a
    `canonicalModel` field, but the transcript records this module backfills
    from carry only the dated `message.model`, so the two paths need one shared
    way to agree on a name. Nothing here is a list of known models: a model this
    module has never heard of is counted under its own name.
    """
    if not model:
        return None
    # Strip a trailing -YYYYMMDD, which is the only suffix form in use.
    head, _, tail = model.rpartition("-")
    if head and len(tail) == 8 and tail.isdigit():
        return head
    return model


class UsageError(RuntimeError):
    """A caller asked for a run that cannot be reported on."""


@dataclass
class Tokens:
    """One model's token counts. Additive, so a run is a sum of sessions."""

    input: int = 0
    output: int = 0
    #: A share of `output`, not an addition to it. Thinking tokens are output
    #: tokens, so `total` must not count them twice.
    thinking: int = 0
    #: Output from requests that carried no thinking count at all. Kept apart
    #: so that "none recorded" cannot read as "none used": the CLI reported
    #: thinking only from partway through this project's runs.
    thinking_unrecorded: int = 0
    cache_read: int = 0
    #: One number. The 5-minute/1-hour split decided what a write cost and
    #: nothing else, and nothing here is priced.
    cache_write: int = 0
    requests: int = 0

    @property
    def total(self) -> int:
        return self.input + self.output + self.cache_read + self.cache_write

    @property
    def thinking_recorded(self) -> bool:
        """True when every output token here came with a thinking count."""
        return self.thinking_unrecorded == 0

    def add(self, other: "Tokens") -> None:
        self.input += other.input
        self.output += other.output
        self.thinking += other.thinking
        self.thinking_unrecorded += other.thinking_unrecorded
        self.cache_read += other.cache_read
        self.cache_write += other.cache_write
        self.requests += other.requests


def _thinking(block: object, key: str) -> int | None:
    """A thinking count, or None where the record carries none -- which is not 0."""
    if not isinstance(block, dict):
        return None
    value = block.get(key)
    return int(value) if isinstance(value, (int, float)) else None


def _written(usage: dict) -> int:
    """Cache-write tokens in one usage block.

    The flat total where there is one, which is every record measured; failing
    that, the 5-minute/1-hour split it is the sum of. A record carrying only the
    split must not read as no writes at all.
    """
    value = usage.get("cache_creation_input_tokens")
    if isinstance(value, (int, float)):
        return int(value)
    creation = usage.get("cache_creation")
    if not isinstance(creation, dict):
        return 0
    parts = (creation.get(key) for key in ("ephemeral_1h_input_tokens",
                                           "ephemeral_5m_input_tokens"))
    return sum(int(part) for part in parts if isinstance(part, (int, float)))


def _add_output(tokens: Tokens, output: int, thinking: int | None) -> None:
    """Book output, and the thinking inside it, or the fact that it went unrecorded."""
    tokens.output += output
    if thinking is None:
        tokens.thinking_unrecorded += output
    else:
        tokens.thinking += thinking


@dataclass
class Session:
    """One `claude -p` call: one attempt at one paper."""

    work: str
    doi: str
    session_id: str
    path: Path
    by_model: dict[str, Tokens] = field(default_factory=lambda: defaultdict(Tokens))
    limit_refusals: int = 0
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

            bucket = by_model[canonical(model) or "unknown"]
            bucket.input += count("input_tokens")
            _add_output(bucket, count("output_tokens"),
                        _thinking(usage.get("output_tokens_details"), "thinking_tokens"))
            bucket.cache_read += count("cache_read_input_tokens")
            bucket.cache_write += _written(usage)
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


def from_envelopes(work_dir: Path, complete_only: bool = True) -> list[Session]:
    """Read a run's usage from the envelopes it recorded, if it recorded any.

    Preferred over the transcript scan wherever it is available, because it is
    the CLI's own accounting rather than a reconstruction: it carries
    `duration_api_ms`, which is real model time, where a transcript can only
    give the agent's wall-clock span. Runs made before the invocation changed
    have no envelopes and fall back automatically.

    `complete_only=False` returns whatever parsed, however little. That is for
    the token check, which compares session by session and so is as good on a
    partly instrumented run as on a whole one; the report itself never uses it.
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
            # same totals without the model split, read only where `modelUsage`
            # is absent -- and for thinking, which older CLIs reported there
            # and nowhere else. Its `iterations` array must never be summed: it
            # lists one entry per turn but not reliably all of them.
            flat = envelope.get("usage") or {}
            flat_thinking = _thinking(flat.get("output_tokens_details"), "thinking_tokens")

            per_model = envelope.get("modelUsage")
            # An API-error envelope carries `modelUsage: {}` and no top-level
            # `model`, so the fallback below would book it under "unknown" with
            # zero tokens -- inventing a model row with nothing in it. No tokens
            # were used; count it as a refusal and move on. That also restores
            # the wasted-spawn tally on this path, which the transcript reader
            # has and this one silently lacked.
            if not (isinstance(per_model, dict) and per_model) and not (
                    envelope.get("usage") or {}).get("output_tokens"):
                if envelope.get("is_error"):
                    refusals_only = Session(work=work_dir.name, doi=doi,
                                            session_id=session.session_id, path=path)
                    refusals_only.limit_refusals = 1
                    sessions.append(refusals_only)
                continue
            if isinstance(per_model, dict) and per_model:
                rows = {name: counts for name, counts in per_model.items()
                        if isinstance(counts, dict)}
                for name, counts in rows.items():
                    tokens = session.by_model[canonical(name) or "unknown"]
                    tokens.input += int(counts.get("inputTokens") or 0)
                    tokens.cache_read += int(counts.get("cacheReadInputTokens") or 0)
                    tokens.cache_write += int(counts.get("cacheCreationInputTokens") or 0)
                    # Per model where the CLI gives it. Older CLIs gave only the
                    # flat session total, which is this model's own when it is
                    # the only one -- equal to the per-model figure on all 418
                    # envelopes measured that carry both. With two models the
                    # total cannot be split, so it goes unrecorded, not guessed.
                    thinking = _thinking(counts, "thinkingTokens")
                    if thinking is None and len(rows) == 1:
                        thinking = flat_thinking
                    _add_output(tokens, int(counts.get("outputTokens") or 0), thinking)
            else:
                tokens = session.by_model[canonical(envelope.get("model")) or "unknown"]
                tokens.input += int(flat.get("input_tokens") or 0)
                tokens.cache_read += int(flat.get("cache_read_input_tokens") or 0)
                tokens.cache_write += _written(flat)
                _add_output(tokens, int(flat.get("output_tokens") or 0), flat_thinking)

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
        if complete_only and expected and not expected <= {s.doi for s in sessions}:
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


def requested_models(work_dir: Path, dois) -> dict[str, str]:
    """The model the runner asked for, per paper, where it wrote one down.

    The runner writes `meta/<doi>.model` beside a paper's result, so a paper
    with no result has none, and neither does any run from before the sidecar.
    Both are reported as not recorded rather than filled in with the pin.
    """
    found = {}
    for doi in dois:
        name = model_of(work_dir, doi)
        if name:
            found[doi] = name
    return found


# ---------------------------------------------------------------- token check

#: Compared exactly. `requests` is not: an envelope reports turns and a
#: transcript reports messages, which are counts of different things.
_CHECKED = ("input", "output", "cache_read", "cache_write")


@dataclass
class TokenCheck:
    """Envelope against transcript, for every session that left both."""

    #: Sessions whose envelope shows any tokens. An attempt that errored has
    #: nothing to compare, and is left out rather than counted as a match.
    envelopes: int = 0
    compared: int = 0
    matched: int = 0
    #: One DOI per session whose two records disagree.
    differing: list[str] = field(default_factory=list)

    @property
    def without_transcript(self) -> int:
        return self.envelopes - self.compared


def _same(a: dict[str, Tokens], b: dict[str, Tokens]) -> bool:
    for name in set(a) | set(b):
        x, y = a.get(name, Tokens()), b.get(name, Tokens())
        if any(getattr(x, key) != getattr(y, key) for key in _CHECKED):
            return False
        # Thinking only where both sides recorded it. An older envelope without
        # it is a gap in one record, not a disagreement about the count.
        if x.thinking_recorded and y.thinking_recorded and x.thinking != y.thinking:
            return False
    return True


def check_tokens(envelopes: list[Session], directories: list[Path]) -> TokenCheck:
    """Compare each envelope with the transcript of the same session.

    The envelope is the CLI's own accounting and the transcript is rebuilt here
    message by message. They are parsed independently, so agreement vouches for
    both readers -- the transcript reader above all, since it is the only
    source for every run made before envelopes existed. The session id names
    the transcript file, so this reads one transcript per session rather than
    scanning every directory the way `collect` does.
    """
    check = TokenCheck()
    for session in envelopes:
        if not session.by_model:
            continue
        check.envelopes += 1
        rebuilt = None
        for directory in directories:
            candidate = directory / f"{session.session_id}.jsonl"
            if candidate.is_file():
                rebuilt = read_session(candidate)
                break
        if rebuilt is None:
            continue
        check.compared += 1
        if _same(session.by_model, rebuilt.by_model):
            check.matched += 1
        else:
            check.differing.append(session.doi)
    return check


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


def _papers(n: int) -> str:
    return f"{n} paper" if n == 1 else f"{n} papers"


def _sessions(n: int) -> str:
    return f"{n} session" if n == 1 else f"{n} sessions"


def _thinking_cell(tokens: Tokens) -> str:
    """Thinking tokens, or why there is no number. Never a bare 0 for "unknown"."""
    if tokens.output and tokens.thinking_unrecorded == tokens.output:
        return "not recorded"
    if tokens.thinking_unrecorded:
        return f"{_thousands(tokens.thinking)} (partial)"
    return _thousands(tokens.thinking)


def _table(by_model: dict[str, Tokens]) -> list[str]:
    """The per-model block, in the shape Claude Code's own run summary uses."""
    head = ("Model", "Input", "Output", "Thinking", "Cache read", "Cache write", "Total")
    rows = []
    for name in sorted(by_model, key=lambda m: -by_model[m].total):
        tokens = by_model[name]
        rows.append(
            (
                name,
                _thousands(tokens.input),
                _thousands(tokens.output),
                _thinking_cell(tokens),
                _thousands(tokens.cache_read),
                _thousands(tokens.cache_write),
                _thousands(tokens.total),
            )
        )
    grand = Tokens()
    for tokens in by_model.values():
        grand.add(tokens)
    if len(by_model) > 1:
        rows.append(("All models", "", "", "", "", "", _thousands(grand.total)))

    if not rows:
        # Every attempt errored, so no tokens were used. `max(x, *())` raises,
        # which turned a run of pure refusals into a crash in the reporter
        # rather than a report saying no tokens were spent.
        return ["(no token usage: every attempt returned an error)"]

    widths = [max(len(head[i]), *(len(r[i]) for r in rows)) for i in range(len(head))]
    lines = ["  ".join(h.ljust(widths[i]) if i == 0 else h.rjust(widths[i])
                       for i, h in enumerate(head))]
    for row in rows:
        lines.append("  ".join(cell.ljust(widths[i]) if i == 0 else cell.rjust(widths[i])
                               for i, cell in enumerate(row)))
    if 0 < grand.thinking_unrecorded < grand.output:
        lines.append(f"Thinking was not recorded for {_thousands(grand.thinking_unrecorded)} of "
                     f"{_thousands(grand.output)} output tokens; the column counts the rest.")
    return lines


def _spread(counts: Counter, papers: int) -> list[str]:
    """`claude-opus-5 for all 392 papers`, or one clause per model."""
    if len(counts) == 1 and sum(counts.values()) == papers:
        (name,) = counts
        return [f"{name} for the only paper" if papers == 1
                else f"{name} for all {papers} papers"]
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [f"{name} for {_papers(n)}" for name, n in ranked]


def _model_lines(served: dict[str, set[str]], requested: dict[str, str],
                 other: dict[str, set[str]]) -> list[str]:
    """What the runner asked for, what answered, and every paper where they differ.

    The table already shows a second model as a second row, but not which
    papers it answered. A refusal fallback or a mistyped pin changes who made a
    determination, and a run total is where that would hide.
    """
    papers = len(served)
    asked = Counter(canonical(requested[doi]) for doi in served if doi in requested)
    unrecorded = papers - sum(asked.values())
    if not asked:
        asked_text = ("not recorded" if papers == 1
                      else f"not recorded for any of the {papers} papers")
    else:
        clauses = _spread(asked, papers)
        if unrecorded:
            clauses.append(f"not recorded for {unrecorded}")
        asked_text = ", ".join(clauses)

    answered = Counter(name for names in served.values() for name in names)
    idle = sum(1 for names in served.values() if not names)
    if not answered:
        served_text = "no tokens used"
    else:
        clauses = _spread(answered, papers)
        if idle:
            clauses.append(f"no tokens for {idle}")
        served_text = ", ".join(clauses)

    lines = [f"Model requested: {asked_text}", f"Model served:    {served_text}"]
    if other:
        lines.append(f"{_papers(len(other))} served by a model other than the one requested:")
        for doi in sorted(other)[:LISTED]:
            lines.append(f"  {doi}: requested {requested[doi]}, "
                         f"served {' + '.join(sorted(other[doi]))}")
        if len(other) > LISTED:
            lines.append(f"  ...and {len(other) - LISTED} more")
    return lines


def _check_lines(check: TokenCheck) -> list[str]:
    """The token check, including when and why it could not run."""
    if not check.envelopes:
        return ["Token check: not possible, this run recorded no envelopes "
                "to check the transcripts against."]
    if not check.compared:
        which = ("the only session" if check.envelopes == 1
                 else f"any of the {check.envelopes} sessions")
        return [f"Token check: not possible, no transcript was found for {which}."]
    if check.matched == check.compared:
        scope = ("in the only session" if check.compared == 1
                 else f"in all {check.compared} sessions")
    else:
        scope = f"in {check.matched} of {_sessions(check.compared)}"
    lines = [f"Token check: transcripts match the CLI's own counts {scope}."]
    if check.differing:
        lines.append(f"  Differ in {len(check.differing)}:")
        lines += [f"    {doi}" for doi in check.differing[:LISTED]]
        if len(check.differing) > LISTED:
            lines.append(f"    ...and {len(check.differing) - LISTED} more")
    if check.without_transcript:
        lines.append(f"  No transcript for {check.without_transcript} of the "
                     f"{_sessions(check.envelopes)}.")
    return lines


def render(sessions: list[Session], work: str, per_paper: bool = False,
           requested: dict[str, str] | None = None,
           check: TokenCheck | None = None) -> str:
    requested = requested or {}
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

    # A model "served" a paper when it used any tokens on it, in any attempt.
    served = {doi: {name for session in group
                    for name, tokens in session.by_model.items() if tokens.total}
              for doi, group in papers.items()}
    other = {doi: names for doi, names in served.items()
             if doi in requested and names and names != {canonical(requested[doi])}}

    # The work directory already names the run and the pack version, so the
    # heading does not restate what task this is -- which it also may not do.
    out = [f"Run usage — {work}", ""]
    out += _model_lines(served, requested, other)
    out.append("")
    out += _table(by_model)
    out.append("")
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

    # Output tokens and request count are the two numbers that evidence
    # per-paper work: output is reasoning the model generated about THIS paper,
    # and requests are the turns it took to get there. Neither can be high for a
    # paper that was labelled without being read. Cache-read cannot carry that
    # weight -- it is dominated by the shared prompt, so it stays large whether
    # or not the paper was engaged with. So the per-paper view sorts and
    # summarises on output, and thinking is reported as a share of it.
    per = []
    for doi, group in papers.items():
        merged: dict[str, Tokens] = defaultdict(Tokens)
        for session in group:
            for name, tokens in session.by_model.items():
                merged[name].add(tokens)
        grand = Tokens()
        for tokens in merged.values():
            grand.add(tokens)
        turns = sum(t.requests for t in merged.values())
        per.append((grand.output, turns, doi, grand))

    if per:
        ordered = sorted(c[0] for c in per)
        mid = ordered[len(ordered) // 2]
        p10 = ordered[max(0, int(0.10 * (len(ordered) - 1)))]
        p90 = ordered[min(len(ordered) - 1, int(0.90 * (len(ordered) - 1)))]
        turns = sorted(c[1] for c in per)
        # A share only for a paper whose every output token came with a count:
        # a paper that recorded half its thinking would read as half as much.
        worked = [g for *_, g in per if g.output]
        shares = sorted(g.thinking / g.output for g in worked if g.thinking_recorded)
        if not shares:
            nothing = all(g.thinking_unrecorded == g.output for g in worked)
            thinking = ", thinking not recorded" if nothing else ", thinking only partly recorded"
        else:
            thinking = f", {100 * shares[len(shares) // 2]:.0f}% of it thinking"
            if len(shares) < len(worked):
                thinking += f" in the {_papers(len(shares))} that recorded it"
        out.append(
            f"Per paper: median {_thousands(mid)} output tokens "
            f"(p10 {_thousands(p10)}, p90 {_thousands(p90)}){thinking}, "
            f"median {turns[len(turns) // 2]} requests."
        )
        # The floor is the number worth reading. A paper that was assigned a
        # determination without being analysed shows up here and nowhere else
        # in this report: the run total and the median both absorb it.
        low_out, low_turns, low_doi, _ = min(per, key=lambda c: (c[0], c[1]))
        out.append(
            f"Least-worked paper: {_thousands(low_out)} output tokens over "
            f"{low_turns} request{'s' if low_turns != 1 else ''} — {low_doi}"
        )

    if per_paper:
        out.append("")
        out.append("Per paper, most output first:")
        for output, turns, doi, grand in sorted(per, key=lambda c: (-c[0], -c[1])):
            attempts = len(papers[doi])
            suffix = f", {attempts} attempts" if attempts > 1 else ""
            out.append(
                f"  {_thousands(output):>9} out  {turns:>3} req{suffix:<14}  {doi}"
            )
            out.append(
                f"           in {_thousands(grand.input)}"
                f" / thinking {_thinking_cell(grand)}"
                f" / cache-read {_thousands(grand.cache_read)}"
                f" / cache-write {_thousands(grand.cache_write)}"
            )
            if doi in other:
                out.append(f"           ! served by {' + '.join(sorted(other[doi]))}, "
                           f"requested {requested[doi]}")

    # Last, because it vouches for everything above it: the token counts are
    # only as good as the reader that produced them, and this is the one place
    # two independent readers are held against each other.
    if check is not None:
        out.append("")
        out += _check_lines(check)

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
    work_dir = run_root() / args.work if args.work else None
    if work_dir is not None and not args.projects:
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

    # Both read the run directory, so both exist only for a named run. The
    # check takes every envelope that parsed, not only a complete set, because
    # it compares session by session.
    requested = requested_models(work_dir, {s.doi for s in sessions})
    check = check_tokens(from_envelopes(work_dir, complete_only=False), directories)
    print(render(sessions, args.work, per_paper=args.per_paper,
                 requested=requested, check=check))
    # Named because the two sources are not equivalent: only the envelope
    # carries `duration_api_ms`, and only the transcript survives a work
    # directory being deleted.
    print(f"\nsource: {source}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except UsageError as exc:
        print(f"harness.usage: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
