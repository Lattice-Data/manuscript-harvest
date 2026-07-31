"""Command line for the acquisition stage.

    manuscript-fetch get 10.1038/s41586-021-03852-1
    manuscript-fetch batch dois.txt
    manuscript-fetch login          # one-time Stanford SSO, headed
    manuscript-fetch check          # is the browser session alive?

`get` and `batch` work with no browser and no credentials for open-access papers.
`login` and `check` exist only for the last-resort proxy tier, and `login` is the
only command that waits for a human. `--headed` is for watching a fetch happen;
nothing in the fetch path pauses for a login, so a browser opened by `--headed`
on a dead session closes as soon as the tier has named the refusal.
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
    "max_corpus_gb": None,
    "proxy": {
        "enabled": True,
        "prefix": "https://stanford.idm.oclc.org/login?url=",
    },
    "browser": {
        "profile_dir": "~/.manuscript-harvest/chrome-profile",
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


# How many papers in a row may report a dead proxy session before the tier is
# dropped for the rest of a batch. Three is enough to rule out one odd publisher
# and few enough that a 50-DOI run does not spend itself on a login it never had.
SESSION_FAILURE_LIMIT = 3


def _warn_if_no_session(config: dict) -> None:
    """Say up front when the proxy tier is configured but nobody has logged in.

    Without this the first paywalled paper launches a browser, gets bounced to the
    IdP, reports `session_expired` and closes -- and so does every paper after it.
    The reported case was a 53-DOI batch run with `--headed`, on the assumption
    that the flag offers a chance to log in. It does not: it shows the browser
    during a fetch, and the fetch never waits for a human, so Chrome opened on the
    Stanford login page and closed a second later, over and over.
    """
    fetch_cfg = config["fetch"]
    if "proxy_browser" not in (fetch_cfg.get("tiers") or []):
        return
    # Imported here, not at module scope, for the same reason the tier itself is
    # loaded lazily: this path must stay usable without Playwright installed.
    from .sources.proxy_browser import session_saved, state_path

    if session_saved(fetch_cfg):
        return
    print(
        f"note: no saved proxy session at {state_path(fetch_cfg)}.\n"
        "      Paywalled papers will report session_expired until you run:\n"
        "          manuscript-fetch login\n"
        "      (--headed only shows the browser during a fetch; it does not wait\n"
        "       for a login.)",
        file=sys.stderr,
    )


def _session_expired(record: dict) -> bool:
    """Did the browser tier bounce off the IdP for this paper?

    Read from `attempts` rather than `fulltext.status`, because the tier records
    the diagnosis even on a run that only wanted supplements.
    """
    return any(
        attempt.get("tier") == "proxy_browser" and attempt.get("status") == "session_expired"
        for attempt in record.get("attempts") or []
    )


def _report(record: dict) -> None:
    doi = record.get("doi", "?")
    print(f"{doi}  {store.summarize(record)}", file=sys.stderr)
    for problem in record.get("problems") or []:
        print(f"    ! {problem}", file=sys.stderr)
    if record.get("cached"):
        print("    (cached; use --force to re-fetch)", file=sys.stderr)


def cmd_get(args) -> int:
    config = _apply_cli_overrides(load_config(args.config), args)
    _warn_if_no_session(config)
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
    _warn_if_no_session(config)
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
    # A dead proxy session fails identically for every paper that needs it, and
    # each failure costs a browser launch, a navigation and the per-host wait. So
    # count them and stop, rather than proving the same point fifty times.
    session_failures = 0
    dropped_proxy = False
    for doi in dois:
        record = fetch_publication(
            doi, config, force=args.force,
            want_supplements=not args.no_supplements, http=http,
        )
        _report(record)
        records.append(record)

        tiers = config["fetch"].get("tiers") or []
        if "proxy_browser" not in tiers:
            continue
        if _session_expired(record):
            session_failures += 1
        elif "proxy_browser" in (record.get("tiers_tried") or []):
            # The tier ran and did not bounce, so the session is alive and
            # whatever went wrong here was this paper's problem. Only a run of
            # failures means the login is missing.
            session_failures = 0
        if session_failures >= SESSION_FAILURE_LIMIT:
            config["fetch"]["tiers"] = [t for t in tiers if t != "proxy_browser"]
            dropped_proxy = True
            print(
                f"\n{session_failures} papers in a row reported session_expired, so "
                "proxy_browser is dropped for\nthe rest of this run -- the remaining "
                "DOIs get open-access tiers only. To fix:\n"
                "    manuscript-fetch login && manuscript-fetch check\n"
                "then re-run this batch with --force.\n",
                file=sys.stderr,
            )

    if args.report:
        with Path(args.report).open("w", encoding="utf-8") as handle:
            for record in records:
                clean = {k: v for k, v in record.items() if k != "_directory"}
                handle.write(json.dumps(clean, ensure_ascii=False) + "\n")

    by_status = {}
    for record in records:
        by_status[record.get("status")] = by_status.get(record.get("status"), 0) + 1
    print("\n" + "  ".join(f"{k}={v}" for k, v in sorted(by_status.items())), file=sys.stderr)
    if dropped_proxy:
        # Said again at the end because the mid-run notice scrolls away behind the
        # papers that followed it, and without it these totals read as a verdict
        # on the papers rather than on a missing login.
        print("proxy_browser was dropped mid-run: these totals understate what a "
              "logged-in run would reach.", file=sys.stderr)
    return 0 if by_status.get("complete") == len(records) else 1


def cmd_usage(args) -> int:
    """Report corpus disk usage, largest or oldest first."""
    config = _apply_cli_overrides(load_config(args.config), args)
    corpus_dir = config["fetch"]["corpus_dir"]
    entries = store.corpus_usage(corpus_dir)
    if not entries:
        print(f"{corpus_dir}: empty", file=sys.stderr)
        return 0

    total = sum(e["bytes"] for e in entries)
    max_gb = config["fetch"].get("max_corpus_gb")
    ordered = sorted(entries, key=lambda e: -e["bytes"]) if args.by_size else entries

    for entry in ordered[: args.limit]:
        print(f"  {store.human_bytes(entry['bytes']):>9}  {entry['files']:>4} files  "
              f"{entry['status']:<9} {entry['slug']}")
    if len(ordered) > args.limit:
        print(f"  ... {len(ordered) - args.limit} more", file=sys.stderr)

    line = f"{len(entries)} articles, {store.human_bytes(total)}"
    if max_gb:
        budget = int(float(max_gb) * 1024 ** 3)
        line += f" of {max_gb} GB budget ({100 * total / budget:.0f}%)"
    else:
        line += " (no budget set; see fetch.max_corpus_gb)"
    print(line, file=sys.stderr)
    return 0


def cmd_prune(args) -> int:
    """Evict oldest articles until the corpus fits the budget."""
    config = _apply_cli_overrides(load_config(args.config), args)
    fetch_cfg = config["fetch"]
    max_gb = args.max_gb if args.max_gb is not None else fetch_cfg.get("max_corpus_gb")
    if not max_gb:
        print("no budget: pass --max-gb or set fetch.max_corpus_gb", file=sys.stderr)
        return 2

    outcome = store.enforce_budget(
        fetch_cfg["corpus_dir"], int(float(max_gb) * 1024 ** 3), dry_run=args.dry_run
    )
    verb = "would evict" if args.dry_run else "evicted"
    for item in outcome["evicted"]:
        print(f"  {verb} {item['slug']}  {store.human_bytes(item['freed_bytes'])}")
    print(f"{verb} {len(outcome['evicted'])} article(s), freed "
          f"{store.human_bytes(outcome['freed_bytes'])}; corpus now "
          f"{store.human_bytes(outcome['total_bytes'])} against a {max_gb} GB budget",
          file=sys.stderr)
    if outcome.get("note"):
        print(f"  ! {outcome['note']}", file=sys.stderr)
    print("Manifests are kept; re-fetch an evicted article with --force.", file=sys.stderr)
    return 0


def cmd_login(args) -> int:
    from .sources.proxy_browser import interactive_login

    config = _apply_cli_overrides(load_config(args.config), args)
    return interactive_login(config["fetch"], probe_url=args.url,
                             timeout_seconds=args.timeout)


def cmd_check(args) -> int:
    from .sources.proxy_browser import SESSION_REMEDY, check_session

    config = _apply_cli_overrides(load_config(args.config), args)
    alive, detail = check_session(config["fetch"], probe_url=args.url)
    print(f"session: {'alive' if alive else 'not usable'} -- {detail}", file=sys.stderr)
    if not alive and "session_expired" in detail:
        print(f"  {SESSION_REMEDY}", file=sys.stderr)
    return 0 if alive else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="manuscript-fetch",
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
                         help="show the browser during the fetch (debugging the proxy "
                              "tier). This does NOT wait for you to log in -- for that, "
                              "run 'manuscript-fetch login' first")
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

    usage_parser = subparsers.add_parser("usage", help="report corpus disk usage")
    usage_parser.add_argument("--corpus-dir", default=None)
    usage_parser.add_argument("--by-size", action="store_true",
                              help="largest first (default: oldest first)")
    usage_parser.add_argument("--limit", type=int, default=20)
    usage_parser.set_defaults(func=cmd_usage)

    prune_parser = subparsers.add_parser(
        "prune", help="evict oldest articles until the corpus fits its budget"
    )
    prune_parser.add_argument("--corpus-dir", default=None)
    prune_parser.add_argument("--max-gb", type=float, default=None,
                              help="override fetch.max_corpus_gb")
    prune_parser.add_argument("--dry-run", action="store_true")
    prune_parser.set_defaults(func=cmd_prune)

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
