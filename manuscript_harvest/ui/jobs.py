"""One job at a time, run as the real command line, watched from outside.

The panel never imports `fetcher` or `extractor`. A button builds the same argv a
person would type, spawns it, and reads its output. That costs a process launch per
run and buys three things worth more than the launch:

- **The tested surface stays the only code path.** There is no second way to run a
  fetch that could drift from what the CLI does.
- **A crash is contained.** `pymupdf` on a malformed PDF can take an interpreter
  down with it; a subprocess taking itself down leaves the panel running to say so.
- **Stopping is the CLI's own SIGINT handling** -- `progress.StopRequest`, which
  finishes the paper in flight and writes the summary -- rather than a cancellation
  protocol invented here.

**One job at a time is a correctness rule, not a simplification.** The per-host
request interval that keeps this client polite lives in a single `Http` object
inside one process (`cmd_batch` builds one and shares it across the batch for
exactly that reason), so two concurrent fetch runs would hit a publisher at twice
the configured rate while each obeyed the limit as it understood it. Two concurrent
extract runs would race on the same `extracted/` directories.
"""

import collections
import itertools
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

# A batch run over this corpus emits a few hundred lines; `extract all` about one
# per article plus warnings. Twenty thousand is far above either and bounded, so a
# run that somehow produces output without end cannot grow the panel's memory
# without limit. What falls off the front is reported, never silently dropped.
MAX_LOG_LINES = 20_000

# Finished jobs kept for the session, so the page can show what was run and how it
# came out. Not persisted: this is a panel, not a record. `--report` and the
# manifests are the record.
MAX_HISTORY = 20

# What each button runs. Two things here are load-bearing.
#
# `--config` is a *top-level* flag on both CLIs, so it goes before the subcommand.
# The README makes the point; argparse enforces it, which at least means getting it
# wrong is an error rather than a silent fallback to defaults.
#
# The dry-run polarity is not uniform, and assuming it is deletes bytes. `prune`
# acts unless given `--dry-run`; `revalidate`, `drop-media` and `drop-orphans` only
# report unless given `--apply`. So each destructive command names the flag for its
# own direction rather than inheriting a convention that does not exist.
COMMANDS = {
    "fetch": {"tool": "fetch", "sub": "batch", "progress": True},
    "extract-all": {"tool": "extract", "sub": "all", "progress": True},
    "extract-one": {"tool": "extract", "sub": "one"},
    "login": {"tool": "fetch", "sub": "login"},
    "check": {"tool": "fetch", "sub": "check"},
    "prune": {"tool": "fetch", "sub": "prune", "preview_flag": "--dry-run"},
    "revalidate": {"tool": "fetch", "sub": "revalidate", "apply_flag": "--apply"},
    "drop-media": {"tool": "fetch", "sub": "drop-media", "apply_flag": "--apply"},
    "drop-orphans": {"tool": "fetch", "sub": "drop-orphans", "apply_flag": "--apply"},
}

# Kinds that can delete or rewrite what is already in the corpus. The server
# requires a typed confirmation before running one of these with `apply` set.
DESTRUCTIVE = frozenset({"prune", "revalidate", "drop-media", "drop-orphans"})

# Boolean options the panel may pass through, and the flag each one becomes. An
# allow-list rather than a translation of whatever the client sent, so a request
# cannot name a flag that is not here.
PASSTHROUGH_FLAGS = {
    "force": "--force",
    "oa_only": "--oa-only",
    "no_supplements": "--no-supplements",
    "no_proxy": "--no-proxy",
}

# Which of those each command actually accepts, in the order they are appended.
# argparse would reject an unknown flag anyway; this table is what turns a nonsense
# request into a refusal from the server rather than a job that dies on its own
# argv a second after the page said it started.
ACCEPTED_FLAGS = {
    "fetch": ("force", "oa_only", "no_supplements", "no_proxy"),
    "extract-all": ("force",),
    "extract-one": ("force",),
    "check": ("no_proxy",),
}


class Busy(RuntimeError):
    """A job is already running. See the module docstring for why that is a rule."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def tool_argv(tool: str) -> list:
    """How to invoke one of the CLIs: the console script, or this interpreter.

    The console script is preferred only so that the command shown on the page is
    the command a person would type. It is accepted just when it sits in the same
    directory as the running interpreter -- a `manuscript-fetch` from some other
    environment is a different version of this package, reading a different config
    and writing a different corpus, and running it would make the panel's own
    numbers wrong. `sys.executable -m` is always the right code and is the
    documented no-install route anyway.
    """
    script = shutil.which(f"manuscript-{tool}")
    if script and Path(script).parent == Path(sys.executable).parent:
        return [script]
    return [sys.executable, "-m", f"manuscript_harvest.{tool}.cli"]


def build_argv(kind: str, *, config_path, corpus_dir=None, options=None,
               progress_path=None, target=None) -> list:
    """The argv for one button press. Raises ValueError on anything not in the table."""
    spec = COMMANDS.get(kind)
    if spec is None:
        raise ValueError(f"unknown command {kind!r}")
    options = options or {}

    argv = tool_argv(spec["tool"]) + ["--config", str(config_path), spec["sub"]]

    if kind in {"fetch", "extract-one"}:
        if not target:
            raise ValueError(f"{kind} needs a target")
        argv.append(str(target))

    # Only passed when the panel was started with an explicit override. Left off
    # otherwise so the config file stays the single answer to where the corpus is,
    # rather than the panel restating it on every command line.
    if corpus_dir and spec["sub"] not in {"login", "check"}:
        argv += ["--corpus-dir", str(corpus_dir)]

    for key in ACCEPTED_FLAGS.get(kind, ()):
        if options.get(key):
            argv.append(PASSTHROUGH_FLAGS[key])

    apply_flag = spec.get("apply_flag")
    preview_flag = spec.get("preview_flag")
    applying = bool(options.get("apply"))
    if apply_flag and applying:
        argv.append(apply_flag)
    if preview_flag and not applying:
        argv.append(preview_flag)

    if spec.get("progress") and progress_path:
        argv += ["--progress-jsonl", str(progress_path)]
    return argv


class Job:
    """One spawned command: its log, its heartbeat, and how it ended."""

    def __init__(self, job_id: str, kind: str, argv: list, cwd, *, label: str,
                 progress_path=None, destructive: bool = False):
        self.id = job_id
        self.kind = kind
        self.argv = list(argv)
        self.cwd = str(cwd)
        self.label = label
        self.destructive = destructive
        self.progress_path = Path(progress_path) if progress_path else None
        self.started_at = _now()
        self.started_monotonic = time.monotonic()
        self.finished_at = None
        self.returncode = None
        self.stopping = False
        self.force_stopped = False
        self.error = None
        self.duration = None

        self.proc = None
        self._lines = collections.deque(maxlen=MAX_LOG_LINES)
        self.log_total = 0
        self._progress_handle = None
        self._progress_pending = ""
        # `files`/`bytes` are what a fetch adds; `blocks`/`tables` are what an
        # extract produces. Both stages report through the same heartbeat, and each
        # simply leaves the other's fields out, so a running total of zero here
        # means "this stage does not count that" rather than "nothing happened".
        # The page reads `kind` to decide which pair to show.
        self.progress = {
            "total": None,
            "done": 0,
            "by_status": {},
            "files": 0,
            "bytes": 0,
            "blocks": 0,
            "tables": 0,
            "stopped": False,
            "ended": False,
            "recent": collections.deque(maxlen=50),
        }

    # -- log ----------------------------------------------------------------

    def append(self, line: str) -> None:
        self._lines.append(line)
        self.log_total += 1

    def log_since(self, cursor: int) -> dict:
        """Lines after `cursor`, and how many the buffer had already dropped."""
        window_start = self.log_total - len(self._lines)
        cursor = max(0, min(cursor, self.log_total))
        dropped = max(0, window_start - cursor)
        offset = max(0, cursor - window_start)
        return {
            "cursor": self.log_total,
            "dropped": dropped,
            "lines": list(itertools.islice(self._lines, offset, None)),
        }

    # -- heartbeat ----------------------------------------------------------

    def drain_progress(self) -> None:
        """Read whatever `--progress-jsonl` has added since the last read.

        Whole lines only. `progress.ProgressLog` flushes after each one, but a line
        longer than the io buffer reaches disk in pieces -- the `start` event for
        `extract all` names every slug in the corpus, which is about 15 KB over this
        one -- so a tail that trusted "flushed means complete" would parse half a
        JSON object. Anything after the final newline is held for next time.
        """
        if self.progress_path is None:
            return
        if self._progress_handle is None:
            if not self.progress_path.exists():
                return
            try:
                self._progress_handle = self.progress_path.open("r", encoding="utf-8")
            except OSError:
                return
        try:
            chunk = self._progress_handle.read()
        except OSError:
            return
        if not chunk:
            return
        self._progress_pending += chunk
        parts = self._progress_pending.split("\n")
        self._progress_pending = parts.pop()
        for line in parts:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            self._absorb(event)

    def _absorb(self, event: dict) -> None:
        kind = event.get("event")
        if kind == "start":
            self.progress["total"] = event.get("total")
            return
        if kind == "end":
            self.progress["ended"] = True
            self.progress["stopped"] = bool(event.get("stopped"))
            if event.get("by_status"):
                self.progress["by_status"] = event["by_status"]
            return
        if kind != "item":
            return
        self.progress["done"] = event.get("seq") or (self.progress["done"] + 1)
        if event.get("total") is not None:
            self.progress["total"] = event["total"]
        status = event.get("status") or "?"
        by_status = self.progress["by_status"]
        by_status[status] = by_status.get(status, 0) + 1
        for key in ("files", "bytes", "blocks", "tables"):
            self.progress[key] += event.get(key) or 0
        self.progress["recent"].append({
            "doi": event.get("doi"),
            "slug": event.get("slug"),
            "status": status,
            "files": event.get("files"),
            "bytes": event.get("bytes"),
            "blocks": event.get("blocks"),
            "tables": event.get("tables"),
            "cached": event.get("cached"),
            "problems": (event.get("problems") or [])[:3],
        })

    # -- lifecycle ----------------------------------------------------------

    @property
    def live(self) -> bool:
        return self.returncode is None and self.error is None

    def send(self, sig) -> None:
        """Signal the job's whole process group.

        The group, not the process: the fetch path launches a browser, and the
        login command launches one and waits for a human. Signalling only the
        Python process would leave Chrome running with nobody reading it.
        """
        proc = self.proc
        if proc is None or proc.poll() is not None:
            return
        try:
            if hasattr(os, "killpg"):
                os.killpg(os.getpgid(proc.pid), sig)
            else:
                proc.send_signal(sig)
        except (ProcessLookupError, PermissionError, OSError):
            # Already gone, or not ours to signal. Either way there is nothing to
            # stop, and the waiter thread is about to record how it ended.
            pass

    def elapsed(self) -> float:
        """Seconds since it started, or how long it took, once it has stopped."""
        if self.duration is not None:
            return self.duration
        return time.monotonic() - self.started_monotonic

    def eta_seconds(self):
        """Rough: mean seconds per item so far, times the items left.

        Rough on purpose, and labelled that way on the page. Papers are not
        interchangeable -- one with 40 supplements costs 40 rate-limited requests
        and one from cache costs none -- so this is a running mean, not a forecast.
        """
        total = self.progress.get("total")
        done = self.progress.get("done") or 0
        if not total or done <= 0 or done >= total:
            return None
        return (self.elapsed() / done) * (total - done)

    def summary(self, cursor: int = 0) -> dict:
        progress = dict(self.progress)
        progress["recent"] = list(self.progress["recent"])
        eta = self.eta_seconds() if self.live else None
        return {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "destructive": self.destructive,
            "command": shlex.join(self.argv),
            "cwd": self.cwd,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "elapsed": round(self.elapsed(), 1),
            "returncode": self.returncode,
            "stopping": self.stopping,
            "force_stopped": self.force_stopped,
            "error": self.error,
            "live": self.live,
            "progress": progress,
            "eta": round(eta) if eta is not None else None,
            "log": self.log_since(cursor),
        }


class JobRunner:
    """Holds at most one live job, plus a short history of finished ones."""

    def __init__(self, on_finish=None):
        self._lock = threading.RLock()
        self._current = None
        self._history = collections.deque(maxlen=MAX_HISTORY)
        self._counter = 0
        self._on_finish = on_finish
        self._tmpdir = None

    # -- accessors ----------------------------------------------------------

    @property
    def current(self):
        with self._lock:
            return self._current

    def busy(self) -> bool:
        with self._lock:
            return self._current is not None and self._current.live

    def history(self) -> list:
        with self._lock:
            return [
                {
                    "id": job.id,
                    "kind": job.kind,
                    "label": job.label,
                    "command": shlex.join(job.argv),
                    "started_at": job.started_at,
                    "finished_at": job.finished_at,
                    "returncode": job.returncode,
                    "error": job.error,
                    "stopped": job.progress.get("stopped") or job.stopping,
                    "by_status": dict(job.progress.get("by_status") or {}),
                }
                for job in reversed(self._history)
            ]

    def snapshot(self, cursor: int = 0) -> dict:
        with self._lock:
            job = self._current
            if job is None:
                return {"job": None, "history": self.history()}
            job.drain_progress()
            return {"job": job.summary(cursor), "history": self.history()}

    # -- running ------------------------------------------------------------

    def progress_dir(self) -> Path:
        """The panel's own temporary directory, made on first use.

        Everything a request causes to be written goes here: the heartbeat files,
        and any DOI list pasted into the page. Nothing a client sends names a
        destination.
        """
        if self._tmpdir is None:
            self._tmpdir = tempfile.mkdtemp(prefix="manuscript-harvest-ui-")
        return Path(self._tmpdir)

    def scratch_file(self, name: str, text: str) -> Path:
        """Write client-supplied text somewhere the panel owns, and return the path.

        Used for DOIs pasted into the page rather than kept in a file. The path is
        the panel's own temporary directory, never anywhere the request names: a
        request says what the DOIs *are*, never where to put them.
        """
        path = self.progress_dir() / name
        path.write_text(text, encoding="utf-8")
        return path

    def start(self, kind: str, argv: list, cwd, *, label: str,
              progress_path=None, destructive: bool = False) -> Job:
        with self._lock:
            if self._current is not None and self._current.live:
                raise Busy(f"{self._current.label} is still running")
            self._counter += 1
            job = Job(f"job{self._counter}", kind, argv, cwd, label=label,
                      progress_path=progress_path, destructive=destructive)
            self._current = job

        env = dict(os.environ)
        # Belt and braces. CPython line-buffers `sys.stderr` even when it is a
        # pipe, which is where both CLIs print, but `stdout` on a pipe is block
        # buffered -- and a panel whose whole point is watching a run should not
        # depend on knowing which stream each line went to.
        env["PYTHONUNBUFFERED"] = "1"
        try:
            job.proc = subprocess.Popen(
                argv, cwd=str(cwd), stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, bufsize=1,
                env=env,
                # Its own process group, for two reasons: `send` can then reach the
                # browser the fetch path launches, and a Ctrl-C in the terminal
                # running the panel does not silently kill the job -- stopping a
                # run is a decision made on the page.
                start_new_session=True,
            )
        except OSError as e:
            with self._lock:
                job.error = f"{type(e).__name__}: {e}"
                job.finished_at = _now()
                job.duration = time.monotonic() - job.started_monotonic
                job.append(f"could not start: {job.error}")
                self._history.append(job)
            return job

        job.append(f"$ {shlex.join(argv)}")
        reader = threading.Thread(target=self._pump, args=(job,),
                                 name=f"{job.id}-log", daemon=True)
        reader.start()
        threading.Thread(target=self._wait, args=(job, reader),
                         name=f"{job.id}-wait", daemon=True).start()
        return job

    def _pump(self, job: Job) -> None:
        stream = job.proc.stdout
        try:
            for line in stream:
                with self._lock:
                    job.append(line.rstrip("\n"))
        except (OSError, ValueError):
            pass
        finally:
            try:
                stream.close()
            except OSError:
                pass

    def _wait(self, job: Job, reader: threading.Thread) -> None:
        code = job.proc.wait()
        # Joined before the job is marked finished, so a page that sees
        # `live: false` has already been shown every line the run printed. Bounded
        # in case the child left a grandchild holding the pipe open.
        reader.join(timeout=10)
        with self._lock:
            job.returncode = code
            job.finished_at = _now()
            # Frozen here, so a finished job on the page keeps saying how long it
            # took rather than counting up for as long as the panel is open.
            job.duration = time.monotonic() - job.started_monotonic
            job.drain_progress()
            if job not in self._history:
                self._history.append(job)
        if self._on_finish is not None:
            self._on_finish(job)

    def stop(self, force: bool = False) -> bool:
        """SIGINT for "finish this item and stop"; SIGKILL only if asked twice."""
        with self._lock:
            job = self._current
            if job is None or not job.live:
                return False
            job.stopping = True
            if force:
                job.force_stopped = True
            job.append("(stop requested: "
                       + ("SIGKILL, immediately" if force
                          else "SIGINT, after the item in flight") + ")")
        job.send(signal.SIGKILL if force else signal.SIGINT)
        return True

    def cleanup(self):
        """Remove the temporary directory, unless a job is still writing to it.

        Returns the path left behind, or None if there was nothing to keep. A live
        job's heartbeat file lives in there: deleting it would not break the job --
        POSIX keeps the descriptor valid, the writes simply go nowhere -- but a
        panel that is shutting down while a fetch continues should leave the run's
        own files where the run put them.
        """
        if not self._tmpdir:
            return None
        if self.busy():
            return Path(self._tmpdir)
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        self._tmpdir = None
        return None
