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

#: Set this to move every default work/ and output/ directory somewhere else.
ENV_VAR = "PERTURBATION_RUN_ROOT"

#: Beside the fetcher's own state directory, which already lives here.
DEFAULT_RUN_ROOT = Path.home() / ".manuscript-harvest" / "perturbation"


def run_root() -> Path:
    return Path(os.environ.get(ENV_VAR) or DEFAULT_RUN_ROOT).expanduser()


def work_default() -> Path:
    return run_root() / "work"


def output_default(name: str) -> Path:
    return run_root() / "output" / name
