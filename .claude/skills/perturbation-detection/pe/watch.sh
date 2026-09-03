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
  # "done" means what pe.pending means by it -- parses, carries every required
  # field, sources match the manifest. `ls raw | wc -l` counted files, so a
  # malformed write showed as progress, which is the definition run_headless.sh's
  # own comment forbids. One python call rather than three, and it prints
  # "N/M done, K failed" itself so an unreadable manifest says so instead of
  # printing "0/ done".
  "$PY" - "$WORK" <<'PYEOF' 2>/dev/null || echo "cannot read $WORK/manifest.json"
import json, os, pathlib, re, sys
sys.path.insert(0, os.getcwd())
from pe.pending import status_of

work = pathlib.Path(sys.argv[1])
entries = [e for e in json.load(open(work / "manifest.json")) if "error" not in e]
done = sum(1 for e in entries if status_of(e, work)[0] == "done")
log = work / "run.log"
fails = len(re.findall(r"(?m)^FAIL", log.read_text())) if log.is_file() else 0
print(f"{done}/{len(entries)} done, {fails} failed")
PYEOF
}

running() { pgrep -f "run_headless.sh $WORK" >/dev/null 2>&1; }

# A run directory that cannot be read is not a finished run. Saying "FINISHED"
# for one is the same false reassurance as reporting 0 failures from a log that
# was never written -- `./pe/watch.sh work status` used to print
# "0/ done, 0 failed  FINISHED" for a work dir that did not exist.
if [ ! -f "$WORK/manifest.json" ]; then
  echo "no run at $WORK -- no manifest.json. Run pe.prepare first, or check the path." >&2
  exit 2
fi

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
