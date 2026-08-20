#!/usr/bin/env bash
# "Tell me when the batch is done."
#
#   ./pe/watch.sh <work_dir>          # print progress every 30s, then notify
#   ./pe/watch.sh <work_dir> status   # print progress once and exit
#
# Waits for run_headless.sh to finish, then pops a macOS notification and prints
# a one-line summary. Safe to start at any time, including after the run began,
# and safe to Ctrl-C -- it only reads, it never touches the run.
set -uo pipefail

WORK="${1:?usage: ./pe/watch.sh <work_dir> [status]}"
MODE="${2:-watch}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PY:-$(command -v python3 || command -v python)}"
cd "$ROOT" || exit 1

progress() {
  local total done fails
  total=$("$PY" -c "import json,sys;print(sum(1 for e in json.load(open('$WORK/manifest.json')) if 'error' not in e))")
  done=$(ls "$WORK/raw" 2>/dev/null | wc -l | tr -d ' ')
  fails=$(grep -cE '^FAIL' "$WORK/run.log" 2>/dev/null | head -1)
  fails=${fails:-0}
  echo "$done/$total done, $fails failed"
}

running() { pgrep -f "run_headless.sh $WORK" >/dev/null 2>&1; }

if [ "$MODE" = "status" ]; then
  echo "$(progress)  $(running && echo RUNNING || echo FINISHED)"
  exit 0
fi

if ! running; then
  echo "run is not active -- $(progress)"
else
  echo "watching $WORK ... (Ctrl-C is safe, it won't stop the run)"
  while running; do
    echo "  $(date '+%H:%M:%S')  $(progress)"
    sleep 30
  done
fi

FINAL=$(progress)
echo
echo "BATCH FINISHED: $FINAL"
command -v osascript >/dev/null && osascript -e \
  "display notification \"$FINAL\" with title \"Perturbation batch finished\" sound name \"Glass\"" 2>/dev/null

echo
echo "next:"
echo "  $PY -m pe.pending   --work $WORK          # anything to re-run?"
echo "  $PY -m pe.validate  --work $WORK --write-corpus"
echo "  $PY -m pe.summarize --work $WORK"
