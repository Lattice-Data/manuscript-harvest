"""Command line for the acquisition stage.

    manuscript-fetch get 10.1038/s41586-021-03852-1
    manuscript-fetch batch dois.txt
    manuscript-fetch usage --by-size     # what is taking the space
    manuscript-fetch revalidate          # is each stored full text the right paper?
    manuscript-fetch prune --dry-run     # what a budget sweep would evict
    manuscript-fetch drop-media          # stored files no text can come out of
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

from ..config import merge_config, warn_if_config_missing
from . import store
from .fetcher import build_http, fetch_publication
from .identifiers import normalize_doi
from .sources import DEFAULT_TIERS, OA_TIERS

DEFAULT_FETCH_CONFIG = {
    "corpus_dir": "corpus",
    "tiers": DEFAULT_TIERS,
    "contact_email": None,
    "ncbi_api_key": None,
    # Read from MANUSCRIPT_HARVEST_ELSEVIER_API_KEY when set, which overrides this --
    # see `fetcher._with_env_credentials`. Declared here so `elsevier_tdm.applies`
    # finds the key absent rather than missing, and a run without one is a no-op for
    # that tier instead of an error.
    "elsevier_api_key": None,
    "min_interval_seconds": 3.0,
    # Belongs with the defaults rather than only in `config.yaml`, because
    # `pmc_s3` is in `DEFAULT_TIERS` and is the one tier that spends a request per
    # *file*: at the 3.0 s default a 14-supplement article sleeps ~45 s and one at
    # the `max_files` cap ~150 s, against a bulk object store that asks for no
    # interval at all. A run that does not find a config file -- `manuscript-fetch`
    # from a subdirectory, or from an install, since `config.yaml` sits outside the
    # package -- lands on these defaults and is otherwise 15x slower per request
    # than designed, silently: `warn_if_config_missing` prints to stderr and the
    # files and statuses are all correct. Every other knob this tier needs
    # (`max_files`, `max_file_mb`) was already here.
    #
    # `merge_config` recurses into dicts, so a user's own map is merged onto this
    # one rather than replacing it: adding hosts works, and *removing* this entry
    # takes editing it to a slower number rather than deleting the line.
    "min_interval_overrides": {"pmc-oa-opendata.s3.amazonaws.com": 0.2},
    "timeout_seconds": 60,
    "max_file_mb": 200,
    "max_files": 50,
    "max_corpus_gb": None,
    # Fetch only supplementary files text can be extracted from -- see
    # `manuscript_harvest/text_bearing.py` for the sets and the measurement. Here as
    # well as in `config.yaml` for the reason `min_interval_overrides` is: a run that
    # finds no config file lands on these defaults, and this one changes which files
    # a corpus holds, so the two must not disagree. `text_bearing.policy_is_on` has
    # the same default again, because a `fetch` mapping can also arrive from
    # somewhere that never passed through here (`tests/fakes.fetch_config` does).
    "text_bearing_only": True,
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


def load_config(path) -> dict:
    """Load config.yaml and fill in the fetch defaults it does not specify."""
    config = {}
    config_path = Path(path)
    if config_path.exists():
        config = yaml.safe_load(config_path.read_text()) or {}
    else:
        warn_if_config_missing(config_path)
    config["fetch"] = merge_config(DEFAULT_FETCH_CONFIG, config.get("fetch") or {})
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

    parsed = []
    for line in lines:
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        try:
            parsed.append(normalize_doi(line))
        except ValueError:
            print(f"skipping unparseable line: {line!r}", file=sys.stderr)

    # Deduplicated, order preserved. A repeated DOI is not free even though
    # `fetch_publication` caches it: each copy is counted again in the summary and in
    # `--report`, and -- the reason this matters -- the proxy circuit breaker below
    # counts *records*, so one paywalled paper listed three times reported
    # "3 papers in a row" and dropped the browser tier for the rest of the run. A
    # real 55-line input file had exactly that shape.
    dois = list(dict.fromkeys(parsed))
    collapsed = len(parsed) - len(dois)
    if collapsed:
        # Named, not just counted. Normalization means the two lines that collapsed
        # need not have looked alike -- `10.1038/X` and `https://doi.org/10.1038/x`
        # are one paper -- so a bare count leaves the user unable to check the run
        # against their own input. Truncated because the file that prompted this had
        # 55 lines and the message is a warning, not the report.
        repeated = [d for d in dois if parsed.count(d) > 1]
        shown = ", ".join(repeated[:5])
        if len(repeated) > 5:
            shown += f", and {len(repeated) - 5} more"
        print(f"collapsed {collapsed} duplicate DOI line(s); fetching {len(dois)} distinct "
              f"paper(s). Repeated: {shown}", file=sys.stderr)

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


def cmd_revalidate(args) -> int:
    """Re-ask "is this the article?" of a corpus fetched before anything did.

    Read-only unless `--apply`, because it can move an article from `complete` to
    `failed` and that should be a decision rather than a side effect of looking.
    Runs no network requests: everything it needs is the bytes and the manifest.
    """
    from .revalidate import revalidate_corpus

    config = _apply_cli_overrides(load_config(args.config), args)
    corpus_dir = config["fetch"]["corpus_dir"]
    reports = revalidate_corpus(corpus_dir, apply=args.apply, slugs=args.slug or None)
    if not reports:
        print(f"{corpus_dir}: no articles", file=sys.stderr)
        return 0

    changed = [r for r in reports if r["changed"]]
    for report in changed:
        verb = "corrected" if args.apply else "would correct"
        print(f"  {verb} {report['slug']}: fulltext {report['before']} -> "
              f"{report['verdict']}")
        for line in report["problems"]:
            print(f"    ! {line}", file=sys.stderr)
    print(f"\n{len(reports)} article(s) checked, {len(changed)} "
          f"{'corrected' if args.apply else 'to correct'}", file=sys.stderr)
    if changed and not args.apply:
        print("re-run with --apply to write these verdicts into the manifests",
              file=sys.stderr)
    return 0


def cmd_drop_media(args) -> int:
    """Delete stored files no text can be extracted from; keep their record.

    Report-only unless `--apply`, like `revalidate` and for the same reason: it
    deletes bytes, and that should be a decision rather than a side effect of
    looking. Not folded into `prune`, which is the size-budget sweep -- that one
    evicts whole articles to stay under `fetch.max_corpus_gb` and this one removes
    figures from every article, so one flag set could not describe both.

    **Unlike `revalidate`, this one can fail, so it reports failures and exits 1.**
    `revalidate` writes verdicts and nothing it does can be refused by the
    filesystem; an `unlink` can, and `drop_media_article` rewrites `files` to what it
    actually deleted -- so an article where *every* deletion raised comes back with
    `files == []` and looks, to a loop keyed on that list, exactly like an article
    with nothing to sweep. Measured with one article's `supplementary/` at mode 0500,
    which is the read-only-mount and other-owner case: `drop-media --apply` printed
    `1 article(s) checked, 0 with files no text can be extracted from: 0 file(s), 0B`
    and exited 0 with the JPEG still on disk -- byte-identical to a clean corpus, and
    an unwritable directory fails every candidate inside it, so the whole sweep can go
    silent. So the per-file errors `drop_media` takes care to collect are printed
    outside that guard, and the exit code says the corpus is not in the state the
    closing line describes. `prune`'s "nothing to do" is 2; 1 here, because the sweep
    ran and did not finish.
    """
    from .drop_media import drop_media_corpus, human_reasons, summarize

    config = _apply_cli_overrides(load_config(args.config), args)
    corpus_dir = config["fetch"]["corpus_dir"]
    reports = drop_media_corpus(corpus_dir, apply=args.apply, slugs=args.slug or None)
    if not reports:
        print(f"{corpus_dir}: no articles", file=sys.stderr)
        return 0

    verb = "removed" if args.apply else "would remove"
    stuck = 0
    for report in reports:
        if report["files"]:
            print(f"  {verb} {report['slug']}: {len(report['files'])} file(s), "
                  f"{store.human_bytes(report['bytes'])}")
        # Outside the guard above, and naming the file rather than repeating
        # `report["note"]`'s count: the error string is the actionable half (`Errno 13`
        # against `Errno 30` is a different fix) and nothing else in this tool would
        # ever print it. The `no manifest` and `evicted` notes stay unprinted, as
        # `revalidate` leaves its own notes -- an `evicted` line for every evicted
        # article on every pass is noise, and neither is a failure.
        for item in report.get("failed") or ():
            stuck += 1
            print(f"  ! {report['slug']}: {item['path']} not deleted: {item['error']}",
                  file=sys.stderr)

    totals = summarize(reports)
    reasons = human_reasons(totals["by_reason"])
    breakdown = f" ({reasons})" if reasons else ""
    print(f"\n{totals['articles']} article(s) checked, {totals['affected']} with files "
          f"no text can be extracted from: {totals['files']} file(s), "
          f"{store.human_bytes(totals['bytes'])}{breakdown}", file=sys.stderr)
    if stuck:
        # Said again after the totals, because the totals line is the one that would
        # otherwise be read as a description of the corpus. The manifests are the
        # reassuring half: a file that survived kept its `path`, so nothing claims it
        # is gone and the next pass will offer it again.
        print(f"{stuck} file(s) could not be deleted and are still on disk; their "
              f"manifest entries were left untouched, so re-running after fixing the "
              f"permissions will offer them again", file=sys.stderr)
        return 1
    if totals["files"] and not args.apply:
        print("re-run with --apply to delete them and record the removals in the "
              "manifests", file=sys.stderr)
    elif totals["files"]:
        print("Manifests keep each file's name, size and sha256; the path is dropped "
              "so the next batch does not re-fetch them.", file=sys.stderr)
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

    revalidate_parser = subparsers.add_parser(
        "revalidate",
        help="re-check that each stored full text is the article its DOI asked for",
    )
    revalidate_parser.add_argument("slug", nargs="*",
                                   help="corpus directory names; default: all")
    revalidate_parser.add_argument("--corpus-dir", default=None)
    revalidate_parser.add_argument("--apply", action="store_true",
                                   help="write the verdicts into the manifests "
                                        "(default: report only)")
    revalidate_parser.set_defaults(func=cmd_revalidate)

    drop_media_parser = subparsers.add_parser(
        "drop-media",
        help="delete stored supplementary images, audio and video that no text can "
             "be extracted from (keeps each file's record)",
    )
    drop_media_parser.add_argument("slug", nargs="*",
                                   help="corpus directory names; default: all")
    drop_media_parser.add_argument("--corpus-dir", default=None)
    drop_media_parser.add_argument("--apply", action="store_true",
                                   help="delete the files and record the removals "
                                        "(default: report only)")
    drop_media_parser.set_defaults(func=cmd_drop_media)

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
