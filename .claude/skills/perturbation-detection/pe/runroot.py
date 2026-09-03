"""Where run artifacts go, and why it is not beside the skill.

`claude -p` subagents cannot write under `.claude/`: the permission layer
treats that tree as sensitive and declines the write, silently as far as the
shell is concerned -- the CLI still exits 0. Stage 2 wrote every result into
`<skill>/work/raw/`, so on the first six-paper run two subagents finished
their judgment, failed to save it, and reported success. One had left valid
JSON in its own scratchpad; the other lost the work entirely. At corpus scale
that is a third of a run failing on a permissions gate rather than on
anything about the papers.

So the skill directory now holds only what is versioned and shared -- the
prompt, the code, the config -- and every artifact a run produces lives
outside it. `PERTURBATION_RUN_ROOT` overrides the location; `--work` and
`--out` still override per invocation, and an explicit path is honoured
verbatim, including one inside the skill if that is what a caller asks for.
"""

from __future__ import annotations

import os
from pathlib import Path

def _outputs() -> dict:
    """`task.yaml: outputs`, or empty if the pack cannot be read.

    Imported lazily and tolerantly, unlike everywhere else the pack is required.
    This module is imported by every entry point including the ones whose job is
    to REPORT that the pack is broken, so raising here would replace a clear
    message with an import error. The fallbacks below are this task's historical
    values, so an unreadable pack keeps working exactly as before rather than
    relocating a run somewhere new.
    """
    try:
        from task import load
        return dict(load()._config.get("outputs") or {})
    except Exception:  # noqa: BLE001 - a broken pack must not break the path
        return {}


_OUT = _outputs()

#: Set this to move every default work/ and output/ directory somewhere else.
#: Named by the pack, because two packs in one repo must not share a run root --
#: each would read the other's papers as pending.
ENV_VAR = _OUT["env_var"]

#: Beside the fetcher's own state directory, which already lives here.
DEFAULT_RUN_ROOT = Path.home() / ".manuscript-harvest" / _OUT["run_root_subdir"]


def run_root() -> Path:
    return Path(os.environ.get(ENV_VAR) or DEFAULT_RUN_ROOT).expanduser()


def work_default() -> Path:
    return run_root() / "work"


def output_default(name: str) -> Path:
    return run_root() / "output" / name


def output_name(key: str) -> str:
    """A default output filename, from `task.yaml: outputs`.

    No fallback argument. One existed and it was this task's own filename, which
    put `perturbations_summary.csv` back into the harness as a string literal --
    the seam conceded in the one place nobody would look for it.
    """
    return str(_OUT[key])
