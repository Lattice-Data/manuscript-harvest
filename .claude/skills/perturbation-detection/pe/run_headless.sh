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

  # An expired session fails every remaining paper identically, and each failure
  # still costs a process spawn and a round trip. Observed in practice: a 22-paper
  # run x2 burned ~60 minutes to produce 44 copies of the same 73-byte auth error.
  # Once one paper has proved the session is dead, the rest abort in milliseconds.
  # A sentinel file rather than killing xargs: the remaining invocations are
  # already queued, and a fast no-op is simpler than tearing down the pipeline.
  if [ -f "$work/.auth-failed" ]; then
    echo "ABORT $doi (session died earlier this run; re-authenticate and re-run)"
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

  if claude -p "$task" \
       --model "$MODEL" \
       --permission-mode acceptEdits \
       --allowedTools Read Write Bash Grep \
       --output-format text >"$log" 2>&1; then
    if [ -s "$raw_file" ]; then
      # Which model produced this result. The model is pinned (MODEL above) so
      # results are attributable across machines and across time, but nothing
      # recorded it, so the pin bought no attribution at all. Written beside the
      # result rather than into it: the JSON is the model's own output and the
      # harness does not edit it before pe.validate reads it.
      printf '%s\n' "$MODEL" > "$work/meta/$doi.model"
      echo "OK    $doi"
    else
      echo "FAIL  $doi (claude returned 0 but wrote no file; see $log)"
      return 1
    fi
  else
    if is_auth_failure "$log"; then
      : > "$work/.auth-failed"
      echo "FAIL  $doi (SESSION EXPIRED -- aborting the rest; see $log)"
    else
      echo "FAIL  $doi (claude exited non-zero; see $log)"
    fi
    return 1
  fi
}

# Shared by the preflight and the per-paper check, so the two cannot disagree
# about what "the session is dead" looks like.
is_auth_failure() {
  grep -qiE 'failed to authenticate|oauth session expired|not logged in|invalid api key|authentication_error' "$1" 2>/dev/null
}
export -f run_one is_auth_failure

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
rm -f "$WORK/.auth-failed"          # a previous run's verdict is not this run's
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
echo "done. next:"
echo "  $PY -m pe.pending  --work $WORK      # what still needs a rerun"
echo "  $PY -m pe.validate --work $WORK --write-corpus"
echo "  $PY -m pe.summarize --work $WORK"
