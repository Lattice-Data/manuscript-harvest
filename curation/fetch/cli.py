"""Command line for the acquisition stage.

    python -m curation.fetch.cli get 10.1038/s41586-021-03852-1
    python -m curation.fetch.cli batch dois.txt
    python -m curation.fetch.cli login          # one-time Stanford SSO, headed
    python -m curation.fetch.cli check          # is the browser session alive?

`get` and `batch` work with no browser and no credentials for open-access papers.
`login` and `check` exist only for the last-resort proxy tier.
"""

import argparse
import json
import sys
from pathlib import Path

import yaml

from . import store
from .fetcher import build_http, fetch_publication
from .identifiers import normalize_doi
from .sources import DEFAULT_TIERS, OA_TIERS

DEFAULT_FETCH_CONFIG = {
    "corpus_dir": "corpus",
    "tiers": DEFAULT_TIERS,
    "contact_email": None,
    "ncbi_api_key": None,
    "min_interval_seconds": 3.0,
    "timeout_seconds": 60,
    "max_file_mb": 200,
    "max_files": 50,
    "proxy": {
        "enabled": True,
        "prefix": "https://stanford.idm.oclc.org/login?url=",
    },
    "browser": {
        "profile_dir": "~/.curation-harness/chrome-profile",
        "headless": True,
        "channel": "chrome",
        "nav_timeout_seconds": 60,
        "check_url": "https://www.nature.com/articles/s41586-026-10510-x",
    },
}


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path) -> dict:
    """Load config.yaml and fill in the fetch defaults it does not specify."""
    config = {}
    config_path = Path(path)
    if config_path.exists():
        config = yaml.safe_load(config_path.read_text()) or {}
    config["fetch"] = _merge(DEFAULT_FETCH_CONFIG, config.get("fetch") or {})
    return config


def _apply_cli_overrides(config: dict, args) -> dict:
    fetch_cfg = config["fetch"]
    if getattr(args, "corpus_dir", None):
        fetch_cfg["corpus_dir"] = args.corpus_dir
    if getattr(args, "tiers", None):
        fetch_cfg["tiers"] = [t.strip() for t in args.tiers.split(",") if t.strip()]
    if getattr(args, "oa_only", False):
        fetch_cfg["tiers"] = list(OA_TIERS)
    if getattr(args, "no_proxy", False):
        fetch_cfg["proxy"]["enabled"] = False
    if getattr(args, "headed", False):
        fetch_cfg["browser"]["headless"] = False
    return config


def _exit_code(record: dict) -> int:
    return {"complete": 0, "partial": 1}.get(record.get("status"), 2)


def _report(record: dict, directory=None) -> None:
    doi = record.get("doi", "?")
    print(f"{doi}  {store.summarize(record)}", file=sys.stderr)
    for problem in record.get("problems") or []:
        print(f"    ! {problem}", file=sys.stderr)
    if record.get("cached"):
        print("    (cached; use --force to re-fetch)", file=sys.stderr)


def cmd_get(args) -> int:
    config = _apply_cli_overrides(load_config(args.config), args)
    record = fetch_publication(
        args.doi,
        config,
        force=args.force,
        want_supplements=not args.no_supplements,
    )
    _report(record)
    directory = record.get("_directory")
    if directory:
        print(directory)
    if args.json:
        clean = {k: v for k, v in record.items() if k != "_directory"}
        Path(args.json).write_text(json.dumps(clean, indent=2, ensure_ascii=False))
    return _exit_code(record)


def cmd_batch(args) -> int:
    config = _apply_cli_overrides(load_config(args.config), args)
    lines = Path(args.file).read_text().splitlines()

    dois = []
    for line in lines:
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        try:
            dois.append(normalize_doi(line))
        except ValueError:
            print(f"skipping unparseable line: {line!r}", file=sys.stderr)

    if not dois:
        print("no DOIs found in input", file=sys.stderr)
        return 2

    # One shared Http so the per-host interval applies across the whole batch.
    http = build_http(config)
    records = []
    for doi in dois:
        record = fetch_publication(
            doi, config, force=args.force,
            want_supplements=not args.no_supplements, http=http,
        )
        _report(record)
        records.append(record)

    if args.report:
        with Path(args.report).open("w", encoding="utf-8") as handle:
            for record in records:
                clean = {k: v for k, v in record.items() if k != "_directory"}
                handle.write(json.dumps(clean, ensure_ascii=False) + "\n")

    by_status = {}
    for record in records:
        by_status[record.get("status")] = by_status.get(record.get("status"), 0) + 1
    print("\n" + "  ".join(f"{k}={v}" for k, v in sorted(by_status.items())), file=sys.stderr)
    return 0 if by_status.get("complete") == len(records) else 1


def cmd_login(args) -> int:
    from .sources.proxy_browser import interactive_login

    config = _apply_cli_overrides(load_config(args.config), args)
    return interactive_login(config["fetch"], probe_url=args.url,
                             timeout_seconds=args.timeout)


def cmd_check(args) -> int:
    from .sources.proxy_browser import check_session

    config = _apply_cli_overrides(load_config(args.config), args)
    alive, detail = check_session(config["fetch"], probe_url=args.url)
    print(f"session: {'alive' if alive else 'not usable'} -- {detail}", file=sys.stderr)
    return 0 if alive else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m curation.fetch.cli",
        description="Fetch a publication PDF and its supplementary files from a DOI.",
    )
    parser.add_argument("--config", default="config.yaml")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(sub):
        sub.add_argument("--corpus-dir", default=None)
        sub.add_argument("--tiers", default=None,
                         help=f"comma-separated tier order (default: {','.join(DEFAULT_TIERS)})")
        sub.add_argument("--oa-only", action="store_true",
                         help="open-access tiers only; never open a browser")
        sub.add_argument("--no-proxy", action="store_true",
                         help="do not prepend the library proxy prefix")
        sub.add_argument("--headed", action="store_true",
                         help="show the browser (debugging the proxy tier)")
        sub.add_argument("--force", action="store_true", help="re-fetch even if cached")
        sub.add_argument("--no-supplements", action="store_true")

    get_parser = subparsers.add_parser("get", help="fetch one DOI")
    get_parser.add_argument("doi")
    get_parser.add_argument("--json", default=None, help="also write the manifest here")
    add_common(get_parser)
    get_parser.set_defaults(func=cmd_get)

    batch_parser = subparsers.add_parser("batch", help="fetch a file of DOIs, one per line")
    batch_parser.add_argument("file")
    batch_parser.add_argument("--report", default=None, help="write one manifest per line here")
    add_common(batch_parser)
    batch_parser.set_defaults(func=cmd_batch)

    login_parser = subparsers.add_parser(
        "login", help="open a headed browser to complete Stanford SSO once"
    )
    login_parser.add_argument("--url", default=None, help="probe URL to land on after login")
    login_parser.add_argument("--timeout", type=int, default=600,
                              help="seconds to wait for login to complete (default 600)")
    login_parser.set_defaults(func=cmd_login)

    check_parser = subparsers.add_parser("check", help="test whether the saved session still works")
    check_parser.add_argument("--url", default=None)
    check_parser.add_argument("--headed", action="store_true")
    check_parser.add_argument("--no-proxy", action="store_true",
                              help="probe the publisher directly instead of via the proxy")
    check_parser.set_defaults(func=cmd_check)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
