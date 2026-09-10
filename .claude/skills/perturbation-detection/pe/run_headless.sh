#!/usr/bin/env bash
# Stage 2 from the terminal, one paper per `claude -p` call.
#
#   ./pe/run_headless.sh <work_dir> [N_PARALLEL]
#
# Why this works without an API key: `claude -p` (print/headless mode) uses the
# same logged-in Claude Code session as the interactive app. There is no
# ANTHROPIC_API_KEY anywhere in this path, and none is needed.
#
# Model is pinned to claude-opus-5 (see MODEL below), not left to the CLI's
# /model default, so results are attributable to one model across machines and
# across time. Override with PERTURBATION_MODEL=<id> for a one-off run.
#
# Reads work/manifest.json, skips papers that already have a result, and runs the
# rest. Safe to re-run: it is the same idempotency rule `pe.pending` uses.
set -uo pipefail

# Default outside the skill directory: `claude -p` cannot write under
# `.claude/` and exits 0 anyway, so a result written there is lost
# silently. See pe/runroot.py. An explicit path is honoured verbatim.
RUN_ROOT="${PERTURBATION_RUN_ROOT:-$HOME/.manuscript-harvest/perturbation}"
WORK="${1:-$RUN_ROOT/work}"
JOBS="${2:-3}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PY:-$(command -v python3 || command -v python)}"

# Pinned rather than left to the CLI's default: `claude -p` otherwise picks up
# whatever /model is set to on the machine running this script, which drifts
# between machines and between sessions on the same machine. Override with
# PERTURBATION_MODEL for a one-off run on a different model.
MODEL="${PERTURBATION_MODEL:-claude-opus-5}"
export MODEL

cd "$ROOT" || exit 1
mkdir -p "$WORK/logs" "$WORK/meta"

# One paper. Called by xargs below.
run_one() {
  local doi="$1" work="$2"
  local prompt_file="$work/prompts/$doi.txt"
  local raw_file="$work/raw/$doi.json"
  local log="$work/logs/$doi.log"
  # One JSON line per ATTEMPT, appended. The log is truncated on each retry and
  # 143 of 392 papers in the v0.0.21 corpus run took more than one, so a
  # per-attempt file that overwrites would silently drop the earlier cost.
  local usage="$work/meta/$doi.usage.jsonl"

  # An unusable session fails every remaining paper identically, and each failure
  # still costs a process spawn and a round trip. Observed in practice: a 22-paper
  # run x2 burned ~60 minutes to produce 44 copies of the same 73-byte auth error.
  # Once one paper has proved the session is unusable, the rest abort in
  # milliseconds. A sentinel file rather than killing xargs: the remaining
  # invocations are already queued, and a fast no-op is simpler than tearing down
  # the pipeline.
  #
  # The sentinel was `.auth-failed` and fired on AUTH failures only. A usage
  # limit is the other way a session goes unusable, and it did not fire: the
  # 392-paper v0.0.21 corpus run hit one at 249 papers and the remaining 145 each
  # paid a full spawn to receive the same "You've hit your session limit" line --
  # exactly the waste this sentinel was written to prevent, missed because the
  # guard keyed on one signature of a two-signature failure.
  if [ -f "$work/.session-dead" ]; then
    echo "ABORT $doi ($(cat "$work/.session-dead" 2>/dev/null || echo 'session unusable') earlier this run; see the summary below)"
    return 1
  fi
  # No `[ -s "$raw_file" ]` skip here. The queue below was computed with
  # pe.pending.status_of, whose whole point is that a non-empty raw file is NOT
  # enough -- it must parse, carry every required field, and match the manifest's
  # sources. A paper with a malformed result (seen in practice: one `claude -p`
  # call wrote JSON with a doubled closing quote) was therefore QUEUED here and
  # then unconditionally skipped, so the terminal path could never re-run it.
  # This function trusts the queue; only papers status_of called not-done reach it.
  if [ ! -f "$prompt_file" ]; then
    echo "MISS  $doi (no prompt file -- run pe.prepare first)"
    return 1
  fi

  # The instruction file is self-contained: prompt + schema + paper text. The
  # only thing the agent is told here is where to read and where to write.
  local task="Read the file ${prompt_file} in full -- every line, paging with Read \
or 'sed -n' if it is too large for one call. It contains a complete instruction \
prompt, a required output JSON schema, and the full text of one scientific paper \
divided by <<<SOURCE id=... type=...>>> marker lines.

Follow those instructions exactly. Then write the single resulting JSON object -- \
and nothing else, no prose, no markdown fences -- to ${raw_file} using the Write tool.

Every quote must be copied verbatim from the paper text, and each quote's \
source_id must name the <<<SOURCE>>> block you actually copied it from. \
Reply with only the word DONE when the file is written."

  # `--output-format json` rather than `text`: the envelope carries usage,
  # total_cost_usd, num_turns and duration_api_ms, none of which the text form
  # reports and all of which pe.usage otherwise has to reconstruct from CLI
  # transcripts. stdout and stderr are SPLIT -- merging them, as this did, would
  # interleave progress chatter into the JSON and leave neither parseable.
  # Kept on disk rather than a temp, and NOT deleted after it is folded into the
  # usage file: the failure predicates below read it. An earlier draft removed it
  # first and passed the (now absent) path to them, so they silently fell back to
  # the log alone -- the exact narrowing that let a usage limit through once
  # before. The tests passed anyway, because the auth message reaches stderr and
  # the limit message reached the log via the digest. Accidentally right is the
  # failure mode this guard exists to catch, so the file stays.
  local envelope="$work/meta/$doi.envelope.json"
  if claude -p "$task" \
       --model "$MODEL" \
       --permission-mode acceptEdits \
       --allowedTools Read Write Bash Grep \
       --output-format json >"$envelope" 2>"$log"; then
    keep_envelope "$envelope" "$usage" "$log"
    if [ -s "$raw_file" ]; then
      # Which model produced this result. The model is pinned (MODEL above) so
      # results are attributable across machines and across time, but nothing
      # recorded it, so the pin bought no attribution at all. Written beside the
      # result rather than into it: the JSON is the model's own output and the
      # harness does not edit it before pe.validate reads it.
      printf '%s\n' "$MODEL" > "$work/meta/$doi.model"
      echo "OK    $doi"
    else
      # Exit 0 and no result is not automatically a per-paper problem. The CLI
      # can report an in-band refusal and still exit 0 -- that is what
      # `is_error` and `api_error_status` are for -- and this branch used to
      # skip the sentinel entirely, so a session that died this way would let
      # every remaining paper spawn to discover the same thing. The exact waste
      # the sentinel exists to prevent, reachable by a path it did not cover.
      local verdict
      verdict="$(envelope_verdict "$envelope")"
      if [ "$verdict" = auth ] || is_auth_failure "$log" "$envelope"; then
        set_session_dead "$work" 'SESSION EXPIRED'
        echo "FAIL  $doi (SESSION EXPIRED, reported with exit 0 -- aborting the rest; see $log)"
      elif [ "$verdict" = limit ] || is_usage_limit "$log" "$envelope"; then
        limit_line="$(limit_message "$envelope" "$log")"
        set_session_dead "$work" "USAGE LIMIT (${limit_line:-no reset time reported})"
        echo "FAIL  $doi (USAGE LIMIT, reported with exit 0 -- aborting the rest: ${limit_line:-see $log})"
      else
        echo "FAIL  $doi (claude returned 0 but wrote no file; see $log)"
      fi
      return 1
    fi
  else
    keep_envelope "$envelope" "$usage" "$log"
    local verdict
    verdict="$(envelope_verdict "$envelope")"
    if [ "$verdict" = auth ] || is_auth_failure "$log" "$envelope"; then
      set_session_dead "$work" 'SESSION EXPIRED'
      echo "FAIL  $doi (SESSION EXPIRED -- aborting the rest; see $log)"
    elif [ "$verdict" = limit ] || is_usage_limit "$log" "$envelope"; then
      # The limit line carries the reset time. Keep it: the remedy is to wait
      # until then and re-run, and a caller told only "limit reached" has to go
      # digging through per-paper logs for the one fact that decides when.
      limit_line="$(limit_message "$envelope" "$log")"
      set_session_dead "$work" "USAGE LIMIT (${limit_line:-no reset time reported})"
      echo "FAIL  $doi (USAGE LIMIT -- aborting the rest: ${limit_line:-see $log})"
    else
      echo "FAIL  $doi (claude exited non-zero; see $log)"
    fi
    return 1
  fi
}

# Append one attempt's envelope to the per-paper usage file, and leave the
# human-readable log human-readable.
#
# The log used to hold stdout, which under `--output-format text` was the single
# word DONE. Splitting the streams left it holding only stderr -- usually empty,
# so a reader opening it after a successful paper saw nothing at all and could
# not tell success from a truncated write. A one-line digest is appended so the
# log still answers "what happened here" without anyone parsing JSON.
#
# Normalised to exactly one line before appending: `--output-format json` emits
# a single object today, but a pretty-printed one would corrupt every later
# reader of this file, and stripping newlines costs nothing.
keep_envelope() {
  local envelope="$1" usage="$2" log="$3"
  [ -s "$envelope" ] || { rm -f "$envelope"; return 0; }
  mkdir -p "$(dirname "$usage")"
  { tr -d '\r\n' < "$envelope"; printf '\n'; } >> "$usage"
  "$PY" - "$envelope" >> "$log" 2>/dev/null <<'PYEOF'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
usage = d.get("usage") or {}
creation = usage.get("cache_creation") or {}
print("result={} turns={} cost=${:.4f} api={}ms in={} out={} cache_read={} cache_write={}".format(
    str(d.get("result"))[:200], d.get("num_turns"), d.get("total_cost_usd") or 0.0,
    d.get("duration_api_ms"), usage.get("input_tokens"), usage.get("output_tokens"),
    usage.get("cache_read_input_tokens"),
    (creation.get("ephemeral_1h_input_tokens") or 0) + (creation.get("ephemeral_5m_input_tokens") or 0)))
PYEOF
}

# Shared by the preflight and the per-paper check, so the two cannot disagree
# about what "the session is unusable" looks like. Two predicates rather than
# one because the REMEDIES differ -- re-authenticate, versus wait for the reset
# -- and telling someone to re-authenticate when they need to wait until 11:50pm
# is worse than saying nothing. Both abort the queue.
#
# Both take ANY number of files and match if the signature appears in any of
# them. Under `--output-format json` the CLI reports a refusal in the envelope's
# `result` string, on stdout, while a crash still goes to stderr -- so a
# predicate reading one stream is a predicate that misses half the failures.
# This guard is what stopped a usage limit from spawning 145 doomed papers, and
# narrowing it by accident is exactly how it failed the first time.
# The vocabularies live in variables because three things read each of them --
# the predicate, the preflight and limit_message -- and the version that shipped
# had them written out twice with different words.
#
# WIDENED after measuring against the strings the CLI binary actually carries.
# The old patterns matched neither `Authentication failed` (what a 403 renders
# as), nor `permission_error`, nor `rate_limit_error` -- that last one because
# the pattern said `rate limit` with a space while the CLI emits an underscore.
# Six of eleven realistic failure strings matched nothing at all, which is the
# same too-narrow shape that let a usage limit burn 145 spawns.
AUTH_RE='failed to authenticate|authentication[ _-]?(failed|error)|oauth[^.]{0,30}expired|session expired|not logged in|invalid (api key|bearer token)|permission_error|please run /login'
LIMIT_RE='(session|usage|rate)[ _-]?limit|limit reached|too many requests|quota exceeded|credit balance|\\b429\\b'
export AUTH_RE LIMIT_RE

is_auth_failure() {
  grep -qiE "$AUTH_RE" "$@" 2>/dev/null
}

# Deliberately broad. The exact wording varies with the CLI version, and the
# cost of a false positive is one aborted run that resumes cleanly, while the
# cost of a miss is every remaining paper spawning to receive the same message.
# Verified against the real line: "You've hit your session limit · resets
# 11:50pm (America/Los_Angeles)" -- matched on `session limit`, without relying
# on the typographic apostrophe or the middot surviving a log.
is_usage_limit() {
  grep -qiE "$LIMIT_RE" "$@" 2>/dev/null
}

# The structured read, and the one that should decide. `--output-format json`
# reports a refusal in fields built for the purpose -- `is_error`,
# `api_error_status`, `subtype` -- and the harness was writing all three to disk
# and then classifying by regex over prose anyway. HTTP status is unambiguous
# where wording drifts between CLI versions: 401 and 403 mean re-authenticate,
# 429 means wait. 529/overloaded is deliberately NOT a session death -- it is
# transient, and aborting a 392-paper queue on one overloaded response would
# cost more than the retry it replaces.
#
# Prints `auth`, `limit`, or nothing. Silent when there is no envelope, which is
# every pre-json run and every case where the CLI died before emitting one.
envelope_verdict() {
  [ -s "$1" ] || return 0
  "$PY" - "$1" 2>/dev/null <<'PYEOF'
import json, re, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
status = str(d.get("api_error_status") or "")
text = " ".join(str(d.get(k) or "") for k in ("result", "subtype", "error"))
if status in ("401", "403"):
    print("auth"); sys.exit(0)
if status == "429":
    print("limit"); sys.exit(0)
if not d.get("is_error"):
    sys.exit(0)
if re.search(r"authentication_error|permission_error", text, re.I):
    print("auth")
elif re.search(r"rate_limit_error", text, re.I):
    print("limit")
PYEOF
}

# The one line worth surfacing: it carries the reset time, and the remedy is to
# wait until then. Read out of the envelope's own `result` field first, because
# grepping the log now also matches the digest keep_envelope wrote there -- which
# is a paraphrase of the same message and, being truncated, dropped "11:50pm"
# down to "1". Falls back to the log for the case where the CLI dies before
# emitting an envelope at all.
limit_message() {
  local envelope="$1" log="$2" line=""
  if [ -s "$envelope" ]; then
    line="$("$PY" - "$envelope" 2>/dev/null <<'PYEOF'
import json, re, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
# Same vocabulary as LIMIT_RE, underscores included: the CLI renders a 429 as
# the bare token `rate_limit_error`, which a pattern written with a space misses
# -- and the fall-through then matched our own digest line in the log instead.
text = str(d.get("result") or "")
m = re.search(r"[^.\n]*(?:(?:session|usage|rate)[ _-]?limit|limit reached|"
              r"too many requests|quota exceeded|credit balance)[^.\n]*", text, re.I)
status = str(d.get("api_error_status") or "")
if m:
    line = m.group(0).strip()
    # A bare error token says nothing about when to come back; the status at
    # least says what happened. A real limit message already carries the reset
    # time and is left alone.
    print(f"{line} (HTTP {status})" if status and " " not in line else line)
elif status:
    print(f"HTTP {status}: {text[:120]}".strip())
PYEOF
)"
  fi
  if [ -z "$line" ]; then
    # Wrapped in context, because the reset time is what makes this line worth
    # printing and `-o` without it returns the bare words "session limit".
    line="$(grep -ihoE "[^\"]*(${LIMIT_RE})[^\"]*" "$log" 2>/dev/null | head -1)"
  fi
  printf '%s' "$line"
}
# Two workers failing in the same instant both open `.session-dead` with
# O_TRUNC and both write at offset 0, so the shorter verdict overwrites only its
# own prefix and the summary prints the wrong remedy. Measured at 3 garbles in
# 400 trials at -P 8. A write-then-rename is atomic on the same filesystem, so
# the file is only ever one whole verdict or the other -- and which of two
# simultaneous failures wins does not matter, since both abort the queue.
set_session_dead() {
  local work="$1" text="$2" tmp
  tmp="$work/.session-dead.$$"
  printf '%s' "$text" > "$tmp" && mv -f "$tmp" "$work/.session-dead"
}

export -f run_one is_auth_failure is_usage_limit keep_envelope limit_message \
          envelope_verdict set_session_dead
export PY

# "Pending" means the same thing here as in pe.pending: a raw file existing
# and non-empty is NOT enough -- it must parse and carry every required field.
# A malformed write (seen in practice: one `claude -p` call produced JSON with
# a doubled closing quote) is a non-empty file that a naive existence check
# would treat as finished forever. Import pe.pending's own status_of rather
# than re-implementing a weaker version of it.
DOIS=$("$PY" - "$WORK" <<'PYEOF'
import json, os, pathlib, sys
sys.path.insert(0, os.getcwd())
from pe.pending import status_of

work = sys.argv[1]
for e in json.load(open(os.path.join(work, "manifest.json"))):
    if "error" in e:
        continue
    # status_of(entry, work), not status_of(entry). Without `work` it falls back
    # to the manifest's recorded path strings, so a run directory that was moved
    # or copied -- which the acceptance protocol does -- is judged against the
    # OLD directory while pe.pending uses the derived ones. The two disagreeing
    # about what is done is what `entry_paths` exists to prevent.
    state, _ = status_of(e, pathlib.Path(work))
    if state != "done":
        print(e["doi"])
PYEOF
) || {
  # A crash here used to leave $DOIS empty, and an empty queue reads as "nothing
  # pending" -- so a broken pack, or any import error, reported
  # "nothing to do ... every paper already has a result" and exited 0. Found by
  # running a second task pack whose record.yaml declares no secondary array:
  # pe/validate.py raised IndexError at import and this script called it success.
  # The same vacuous-pass shape pe/runstate.py exists to prevent, one layer up.
  echo "FAILED to compute the pending list -- see the traceback above." >&2
  echo "  Nothing was run. This is NOT an empty queue." >&2
  exit 4
}

if [ -z "$DOIS" ]; then
  echo "nothing to do -- every paper in $WORK/manifest.json already has a result"
  exit 0
fi

COUNT=$(printf '%s\n' "$DOIS" | wc -l | tr -d ' ')

# Preflight. One trivial call before committing to hours of work, because the
# alternative is finding out per-paper: `claude -p` reports the failure honestly,
# but only after every paper has spawned and timed out. Runs only when there is
# work to do, so a fully-cached re-run stays free. PERTURBATION_SKIP_PREFLIGHT=1
# bypasses it.
rm -f "$WORK/.session-dead"         # a previous run's verdict is not this run's
if [ -z "${PERTURBATION_SKIP_PREFLIGHT:-}" ]; then
  if ! command -v claude >/dev/null 2>&1; then
    echo "PREFLIGHT FAILED: no 'claude' on PATH -- stage 2 needs the Claude Code CLI." >&2
    exit 2
  fi
  probe="$WORK/.preflight.log"
  claude -p 'Reply with only: AUTHOK' --model "$MODEL" --output-format text >"$probe" 2>&1
  if ! grep -q 'AUTHOK' "$probe"; then
    echo "PREFLIGHT FAILED -- not running $COUNT paper(s)." >&2
    echo "  claude -p said: $(head -1 "$probe")" >&2
    if is_auth_failure "$probe"; then
      echo "  The logged-in session is dead. Re-authenticate in an interactive" >&2
      echo "  terminal ('claude', then /login) and re-run this script -- it is" >&2
      echo "  resumable and will pick up only what is still missing." >&2
    elif is_usage_limit "$probe"; then
      echo "  The session is over its usage limit, not broken. Wait for the reset" >&2
      echo "  named above and re-run this script -- it is resumable and will pick" >&2
      echo "  up only what is still missing. Do NOT re-authenticate; that is a" >&2
      echo "  different failure and /login will not move the limit." >&2
    fi
    exit 3
  fi
  rm -f "$probe"
fi

echo "running $COUNT paper(s), $JOBS at a time"
echo
# Teed into run.log because that is where ./pe/watch.sh looks for failures. It
# never existed, so watch.sh's "N failed" was hardcoded to 0 by accident -- a
# progress display that could not report a problem.
printf '%s\n' "$DOIS" | xargs -P "$JOBS" -I{} bash -c 'run_one "$@"' _ {} "$WORK" \
  | tee -a "$WORK/run.log"

echo
# Named once, at the end, where it cannot scroll past. A run that aborted has
# every remaining paper reporting "ABORT", and the one fact that decides what to
# do next -- re-authenticate, or wait until the reset -- was previously buried in
# whichever per-paper log happened to hit the failure first.
if [ -f "$WORK/.session-dead" ]; then
  echo "RUN ABORTED: $(cat "$WORK/.session-dead")"
  if grep -q 'USAGE LIMIT' "$WORK/.session-dead" 2>/dev/null; then
    echo "  Wait for the reset above, then re-run this exact command. Papers that"
    echo "  aborted wrote no result at all, so pe.pending sees them as missing and"
    echo "  the re-run picks up only the gap."
  else
    echo "  Re-authenticate ('claude', then /login), then re-run this exact"
    echo "  command -- it will pick up only what is still missing."
  fi
  echo
fi
echo "done. next:"
echo "  $PY -m pe.pending  --work $WORK      # what still needs a rerun"
echo "  $PY -m pe.validate --work $WORK --write-corpus"
echo "  $PY -m pe.summarize --work $WORK"
