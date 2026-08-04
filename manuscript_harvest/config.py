"""Config merging, shared by both stages' CLIs.

Neither stage owns this. It lived in `fetch/cli.py` as `_merge`, and
`extract/cli.py` imported that private name across the stage boundary -- which
meant loading the extract CLI pulled in the fetch orchestrator, the HTTP client
and all five tiers to merge two dictionaries.
"""


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
