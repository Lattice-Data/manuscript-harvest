"""Config merging, shared by both stages' CLIs.

Neither stage owns this. It lived in `fetch/cli.py` as `_merge`, and
`extract/cli.py` imported that private name across the stage boundary -- which
meant loading the extract CLI pulled in the fetch orchestrator, the HTTP client
and all five tiers to merge two dictionaries.
"""

import sys
from pathlib import Path


def warn_if_config_missing(path) -> None:
    """Say so when the config file is not where we looked, instead of proceeding mute.

    Both CLIs default `--config` to the bare name `config.yaml`, so the file is
    resolved against the *current working directory*. Run from a subdirectory --
    a per-ticket folder under the repo, say -- and every key silently falls back
    to the built-in defaults: a different `corpus_dir`, so the fetch lands
    somewhere other than the corpus the extract stage will read, and none of the
    tuned values in the repo's `config.yaml`.

    Nothing about that run looks wrong. It reports `complete`, writes real files
    and exits 0, and the only symptom is a corpus directory that is not the one
    anybody meant. A note on stderr is cheap next to that; it does not fail the
    run, because running on defaults is legitimate when you meant to.

    Same shape and same reason as `_warn_if_no_session` in `fetch/cli.py`.
    """
    if Path(path).exists():
        return
    print(
        f"note: no config file at {Path(path).resolve()} -- using built-in defaults.\n"
        "      Both CLIs resolve --config against the current directory, so run\n"
        "      them from the repo root, or pass --config /path/to/config.yaml.",
        file=sys.stderr,
    )


def merge_config(base: dict, override: dict) -> dict:
    """`override` on top of `base`, recursing into nested dicts.

    Unknown keys in `override` are kept rather than dropped. That is deliberate and
    load-bearing: `config.yaml` documents keys the built-in defaults do not list --
    `fetch.try_oa_package`, `fetch.max_challenge_failures`, the browser deadlines --
    and they reach the code only because a user's value survives this merge without
    a default to sit on.
    """
    out = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge_config(out[key], value)
        else:
            out[key] = value
    return out
