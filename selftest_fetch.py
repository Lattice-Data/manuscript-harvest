"""Run the acquisition test suite (no network, no browser).

    python selftest_fetch.py            # everything
    python selftest_fetch.py -k budget  # just the matching tests

The tests themselves live in `tests/` and run under pytest, which gives per-test
failures and selective runs as more journals and sources get added. This wrapper
exists so the documented one-command entry point keeps working, and so the suite
can be run without remembering pytest's invocation.

`selftest.py` is separate and still covers the extraction pipeline.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main(argv=None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "tests", "-q", *args], cwd=ROOT
        )
    except FileNotFoundError:  # pragma: no cover - depends on the environment
        print("pytest is not installed:  pip install -r requirements-dev.txt",
              file=sys.stderr)
        return 2
    if completed.returncode == 0:
        print("SELFTEST_FETCH PASSED")
    return completed.returncode


if __name__ == "__main__":
    sys.exit(main())
