#!/usr/bin/env bash
# Stage 2 from the terminal, one paper per `claude -p` call.
#
#   ./pe/run_headless.sh <work_dir> [N_PARALLEL]
#
# Why this works without an API key: `claude -p` (print/headless mode) uses the
# same logged-in Claude Code session as the interactive app. There is no
# ANTHROPIC_API_KEY anywhere in this path, and none is needed.
#
# Reads work/manifest.json, skips papers that already have a result, and runs the
# rest. Safe to re-run: it is the same idempotency rule `pe.pending` uses.
set -uo pipefail

WORK="${1:-work}"
JOBS="${2:-3}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PY:-$(command -v python3 || command -v python)}"

cd "$ROOT" || exit 1
mkdir -p "$WORK/logs"

# One paper. Called by xargs below.
run_one() {
  local doi="$1" work="$2"
  local prompt_file="$work/prompts/$doi.txt"
  local raw_file="$work/raw/$doi.json"
  local log="$work/logs/$doi.log"

  if [ -s "$raw_file" ]; then
    echo "SKIP  $doi (already has a result)"
    return 0
  fi
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
       --permission-mode acceptEdits \
       --allowedTools Read Write Bash Grep \
       --output-format text >"$log" 2>&1; then
    if [ -s "$raw_file" ]; then
      echo "OK    $doi"
    else
      echo "FAIL  $doi (claude returned 0 but wrote no file; see $log)"
      return 1
    fi
  else
    echo "FAIL  $doi (claude exited non-zero; see $log)"
    return 1
  fi
}
export -f run_one

DOIS=$("$PY" - "$WORK" <<'PYEOF'
import json, os, sys
work = sys.argv[1]
for e in json.load(open(os.path.join(work, "manifest.json"))):
    if "error" in e:
        continue
    if not os.path.exists(e["raw_file"]) or os.path.getsize(e["raw_file"]) == 0:
        print(e["doi"])
PYEOF
)

if [ -z "$DOIS" ]; then
  echo "nothing to do -- every paper in $WORK/manifest.json already has a result"
  exit 0
fi

COUNT=$(printf '%s\n' "$DOIS" | wc -l | tr -d ' ')
echo "running $COUNT paper(s), $JOBS at a time"
echo
printf '%s\n' "$DOIS" | xargs -P "$JOBS" -I{} bash -c 'run_one "$@"' _ {} "$WORK"

echo
echo "done. next:"
echo "  $PY -m pe.pending  --work $WORK      # what still needs a rerun"
echo "  $PY -m pe.validate --work $WORK --write-corpus"
echo "  $PY -m pe.summarize --work $WORK"
