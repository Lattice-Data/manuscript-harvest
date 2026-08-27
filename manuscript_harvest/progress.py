"""A machine-readable heartbeat for the two batch loops, and a clean way to stop one.

Both `manuscript-fetch batch` and `manuscript-extract all` print one line per item
to stderr, formatted for a person to read. Anything watching a run from outside --
the control panel in `ui/`, a CI job, a shell script -- had two problems with that:

**Progress could only be inferred.** Reading "4 of 12 done" out of those lines
means parsing a layout that exists to be readable, and whose column widths and
wording are free to change. `--progress-jsonl` writes one flushed JSON object per
item alongside the prose, so a watcher reads a fact rather than a rendering.

**Interrupting a run threw away its record.** Ctrl-C raised `KeyboardInterrupt`
wherever the loop had got to, which for `batch --report` meant losing the record of
every paper already fetched: that file was written after the loop, in one pass. So
interrupting a 55-DOI batch at paper 50 discarded 50 manifests' worth of report.
`StopRequest` turns the first Ctrl-C into "finish the item in flight, then stop and
write the summary", and leaves the second as the immediate abort it always was.

Stdlib only, and owned by neither stage, for the reason `config.py` gives.
"""

import json
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path

# Conventional exit code for "terminated by SIGINT" (128 + 2). A stopped run
# returns this rather than 0 or 1 because it has no verdict to give: the items it
# never reached are neither complete nor failed, and 0 would tell a caller the
# batch succeeded. `cmd_batch` cannot fall back on its usual test either -- it
# compares completions against the number of records it *made*, which after an
# early stop is the number of papers reached, so four complete papers out of
# twelve would have exited 0.
STOPPED_EXIT_CODE = 130

# The three `event` values a reader can rely on. Every item event also carries
# `seq` and `total`, so a watcher that missed a line still knows where it is.
EVENT_START = "start"
EVENT_ITEM = "item"
EVENT_END = "end"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JsonlWriter:
    """One JSON object per line, flushed as each is written. A no-op without a path.

    Flushed per line rather than buffered to close, because both users of this
    class exist to be read *while* they run, and because a process that is killed
    should leave behind what it had rather than an empty file.
    """

    def __init__(self, path=None):
        self.path = Path(path).expanduser() if path else None
        self._handle = None

    @property
    def enabled(self) -> bool:
        return self.path is not None

    def open(self) -> "JsonlWriter":
        if self.path is not None and self._handle is None:
            parent = self.path.parent
            if str(parent):
                parent.mkdir(parents=True, exist_ok=True)
            self._handle = self.path.open("w", encoding="utf-8")
        return self

    def write(self, obj: dict) -> None:
        if self._handle is None:
            return
        self._handle.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self._handle.flush()

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def __enter__(self) -> "JsonlWriter":
        return self.open()

    def __exit__(self, *exc) -> bool:
        self.close()
        return False


class ProgressLog:
    """`start`, one `item` per unit of work, then `end`. A no-op without a path.

    Deliberately dumb: it stamps a time, counts the items, and writes whatever
    fields the caller hands it. Each stage decides what is worth reporting about
    one of its items, because only the stage knows -- and a schema enforced here
    would have to be widened for every field either stage learned to record.
    """

    def __init__(self, path=None):
        self._writer = JsonlWriter(path)
        self._seq = 0
        self._total = None

    @property
    def enabled(self) -> bool:
        return self._writer.enabled

    @property
    def done(self) -> int:
        return self._seq

    def start(self, total=None, **fields) -> None:
        self._total = total
        self._writer.write({"event": EVENT_START, "at": _now(), "total": total, **fields})

    def item(self, **fields) -> None:
        self._seq += 1
        self._writer.write({"event": EVENT_ITEM, "at": _now(), "seq": self._seq,
                            "total": self._total, **fields})

    def end(self, **fields) -> None:
        self._writer.write({"event": EVENT_END, "at": _now(), "seq": self._seq,
                            "total": self._total, **fields})

    def __enter__(self) -> "ProgressLog":
        self._writer.open()
        return self

    def __exit__(self, *exc) -> bool:
        self._writer.close()
        return False


class StopRequest:
    """First SIGINT means "stop after this item"; a second one aborts immediately.

    Used as a context manager around a loop that checks `requested` at the top of
    each pass:

        with StopRequest() as stop:
            for item in items:
                if stop.requested:
                    break
                ...

    The second Ctrl-C matters as much as the first. One item can take minutes --
    the browser tier waiting out a 60 s navigation timeout, a 200 MB spreadsheet
    being scanned -- and a class that swallowed every SIGINT would trap the caller
    in a loop they had already asked twice to end. So the second press puts back
    whichever handler was installed before and raises from inside the handler,
    which is the behaviour Python has without this class at all.
    """

    def __init__(self, message=None):
        self.requested = False
        self.message = message or (
            "stopping after the item in flight -- press Ctrl-C again to abort now")
        self._previous = None
        self._installed = False

    def _handle(self, signum, frame) -> None:
        if self.requested:
            self._restore()
            raise KeyboardInterrupt
        self.requested = True
        print(f"\n{self.message}", file=sys.stderr, flush=True)

    def __enter__(self) -> "StopRequest":
        try:
            self._previous = signal.signal(signal.SIGINT, self._handle)
            self._installed = True
        except ValueError:
            # `signal.signal` raises when called off the main thread. A caller
            # driving the loop from a worker thread gets no handler and a
            # `requested` that stays False, so the loop behaves exactly as it did
            # before this class existed. That is the right degradation for
            # something whose only job is to make a run interruptible: no handler
            # is a missing convenience, not a broken run.
            self._installed = False
        return self

    def _restore(self) -> None:
        if self._installed:
            signal.signal(signal.SIGINT, self._previous or signal.default_int_handler)
            self._installed = False

    def __exit__(self, *exc) -> bool:
        self._restore()
        return False
