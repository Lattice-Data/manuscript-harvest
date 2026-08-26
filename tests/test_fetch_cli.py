"""The fetch CLI: the missing-proxy-login story, and what each command writes.

The first half of this file is about one incident (below). The second half covers
the plumbing every command shares -- input parsing, the stdout/stderr split, exit
codes, and the `usage`/`prune`/`check`/`login` commands -- which had no coverage at
all and is the layer a script wraps.

Every test here pins a behaviour that was absent when a user ran

    manuscript-fetch batch --headed --force papers.txt

against a machine that had never run `login`. The browser tier did exactly what
it was written to do -- bounce off the IdP, name the refusal `session_expired`,
close -- once per DOI, for fifty-three DOIs, while the flag they had reached for
(`--headed`) showed them a browser that never waited for a login. Nothing was
broken; nothing said what to do either.
"""

import json

import pytest
import yaml

from manuscript_harvest.fetch import cli, store
from manuscript_harvest.fetch.fetcher import build_http


def _config_file(tmp_path, **fetch_overrides):
    """A config pointing the browser profile somewhere the test owns.

    Necessary: the default profile dir is the developer's own
    `~/.manuscript-harvest`, so a suite that read it would pass or fail
    depending on whether the person running it happened to be logged in.
    """
    fetch = {"browser": {"profile_dir": str(tmp_path / "profile" / "chrome")}}
    fetch.update(fetch_overrides)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"fetch": fetch}))
    return path


def _papers_file(tmp_path, count):
    path = tmp_path / "papers.txt"
    path.write_text("\n".join(f"10.1038/s41586-020-{n:05d}-1" for n in range(count)))
    return path


def _args(tmp_path, count, config=None, extra=()):
    config = config or _config_file(tmp_path)
    return cli.build_parser().parse_args(
        ["--config", str(config), "batch", str(_papers_file(tmp_path, count)), *extra]
    )


def _record(doi, *, proxy_tried=True, expired=True):
    """A manifest of the shape `fetch_publication` returns, minus the bytes."""
    attempts = [{"tier": "europepmc", "action": "search", "status": "not_in_epmc"}]
    tiers_tried = ["europepmc"]
    if proxy_tried:
        tiers_tried.append("proxy_browser")
        attempts.append({
            "tier": "proxy_browser", "action": "landing",
            "status": "session_expired" if expired else "loaded",
        })
    return {
        "doi": doi,
        "status": "failed" if expired else "complete",
        "tiers_tried": tiers_tried,
        "attempts": attempts,
        "problems": [],
        "fulltext": {"status": "session_expired" if expired else "ok"},
        "supplementary_status": "page_not_parsed" if expired else "fetched_unverified",
    }


def _run_batch(monkeypatch, args, outcomes):
    """Run `cmd_batch` against canned outcomes; return the tiers each call saw.

    `outcomes` is one `(proxy_tried, expired)` pair per DOI.
    """
    seen = []
    calls = iter(outcomes)

    def fake_fetch(doi, config, **_kwargs):
        tiers = list(config["fetch"].get("tiers") or [])
        seen.append(tiers)
        proxy_tried, expired = next(calls)
        # A tier that is no longer configured cannot have been tried.
        proxy_tried = proxy_tried and "proxy_browser" in tiers
        return _record(doi, proxy_tried=proxy_tried, expired=expired and proxy_tried)

    monkeypatch.setattr(cli, "fetch_publication", fake_fetch)
    cli.cmd_batch(args)
    return seen


# -- the up-front warning ----------------------------------------------------

def test_batch_warns_when_no_login_has_ever_run(tmp_path, monkeypatch, capsys):
    """The first thing a user without a session should see is the command that
    gives them one -- before fifty browser launches, not after."""
    args = _args(tmp_path, 1)
    _run_batch(monkeypatch, args, [(True, True)])

    warning = capsys.readouterr().err
    assert "no saved proxy session" in warning
    assert "manuscript-fetch login" in warning
    # The flag that was reached for instead has to be named, or the same wrong
    # turn stays available.
    assert "--headed" in warning


def test_no_warning_once_a_session_snapshot_exists(tmp_path, monkeypatch, capsys):
    config = _config_file(tmp_path)
    state = tmp_path / "profile" / "storage_state.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text('{"cookies": []}')

    args = _args(tmp_path, 1, config=config)
    _run_batch(monkeypatch, args, [(True, False)])
    assert "no saved proxy session" not in capsys.readouterr().err


def test_no_warning_when_no_browser_tier_is_configured(tmp_path, monkeypatch, capsys):
    """`--oa-only` promises no browser ever opens, so nagging about a login it
    will never need is noise."""
    args = _args(tmp_path, 1, extra=["--oa-only"])
    _run_batch(monkeypatch, args, [(False, False)])
    assert "no saved proxy session" not in capsys.readouterr().err


# -- the circuit breaker -----------------------------------------------------

def test_batch_stops_using_the_browser_tier_after_repeated_expiry(tmp_path, monkeypatch, capsys):
    """A dead session fails identically for every paper that needs it, and each
    failure costs a browser launch. Prove it three times, then stop."""
    args = _args(tmp_path, 6)
    seen = _run_batch(monkeypatch, args, [(True, True)] * 6)

    assert all("proxy_browser" in tiers for tiers in seen[:cli.SESSION_FAILURE_LIMIT])
    assert all("proxy_browser" not in tiers for tiers in seen[cli.SESSION_FAILURE_LIMIT:])
    # The open-access tiers keep running: the rest of the batch is not abandoned.
    assert all("europepmc" in tiers for tiers in seen)

    err = capsys.readouterr().err
    assert "proxy_browser is dropped" in err
    assert "manuscript-fetch login" in err
    # And again at the end, because the mid-run notice has scrolled away by then
    # and the totals otherwise read as a verdict on the papers.
    assert "understate what a logged-in run would reach" in err


def test_one_working_paper_resets_the_count(tmp_path, monkeypatch):
    """Only a *run* of failures means the login is missing. A tier that reached a
    publisher proves the session is alive, whatever the papers around it did."""
    args = _args(tmp_path, 5)
    seen = _run_batch(monkeypatch, args, [
        (True, True), (True, True), (True, False), (True, True), (True, True),
    ])
    assert all("proxy_browser" in tiers for tiers in seen)


def test_papers_the_browser_tier_never_reached_do_not_reset_the_count(tmp_path, monkeypatch):
    """An open-access paper is satisfied before the browser tier runs. It is
    evidence about neither the session nor the papers that need one, so it must
    not clear the counter -- otherwise one OA paper every other line keeps a dead
    session alive for the whole batch."""
    args = _args(tmp_path, 5)
    seen = _run_batch(monkeypatch, args, [
        (True, True), (False, False), (True, True), (True, True), (True, True),
    ])
    assert "proxy_browser" in seen[3]     # third expiry: the limit is reached here
    assert "proxy_browser" not in seen[4]


def test_batch_without_the_browser_tier_never_trips_the_breaker(tmp_path, monkeypatch):
    args = _args(tmp_path, 5, extra=["--oa-only"])
    seen = _run_batch(monkeypatch, args, [(False, False)] * 5)
    assert all("proxy_browser" not in tiers for tiers in seen)


# -- the flag that caused this -----------------------------------------------

def test_headed_help_says_it_is_not_a_login(capsys):
    """`--headed` was read as "let me sign in". It shows a browser mid-fetch and
    nothing in the fetch path waits for a human, so the help has to say so."""
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["batch", "x", "--help"])
    help_text = capsys.readouterr().out
    assert "does NOT wait for you to log in" in help_text
    assert "manuscript-fetch login" in help_text


@pytest.mark.parametrize("command", ["get", "batch"])
def test_both_fetch_commands_carry_the_warning(command, tmp_path, monkeypatch, capsys):
    """`get` needs it as much as `batch`: a single paywalled DOI fails the same
    way, and it is the command people try first."""
    config = _config_file(tmp_path)
    if command == "get":
        args = cli.build_parser().parse_args(
            ["--config", str(config), "get", "10.1038/s41586-020-00001-1"]
        )
        monkeypatch.setattr(cli, "fetch_publication",
                            lambda *a, **k: _record("10.1038/s41586-020-00001-1"))
        cli.cmd_get(args)
    else:
        _run_batch(monkeypatch, _args(tmp_path, 1, config=config), [(True, True)])
    assert "manuscript-fetch login" in capsys.readouterr().err


# -- input parsing -----------------------------------------------------------

def test_a_dois_file_may_carry_comments_and_blank_lines(tmp_path, monkeypatch, capsys):
    """The format nothing documents but everyone assumes: `#` starts a comment, and
    an unreadable line is reported and skipped rather than aborting the batch."""
    path = tmp_path / "papers.txt"
    path.write_text(
        "# a batch of papers\n"
        "10.1038/s41586-020-00001-1\n"
        "\n"
        "   https://doi.org/10.1038/s41586-020-00002-1   # with a trailing note\n"
        "not a doi at all\n"
    )
    args = cli.build_parser().parse_args(
        ["--config", str(_config_file(tmp_path)), "batch", str(path), "--oa-only"])

    fetched = []

    def fake_fetch(doi, *_a, **_k):
        fetched.append(doi)
        return _record(doi, proxy_tried=False, expired=False)

    monkeypatch.setattr(cli, "fetch_publication", fake_fetch)
    assert cli.cmd_batch(args) == 0
    assert fetched == ["10.1038/s41586-020-00001-1", "10.1038/s41586-020-00002-1"]
    assert "skipping unparseable line: 'not a doi at all'" in capsys.readouterr().err


def test_a_file_with_no_usable_dois_exits_two_without_fetching(tmp_path, monkeypatch, capsys):
    path = tmp_path / "papers.txt"
    path.write_text("# nothing but comments\n\n")
    args = cli.build_parser().parse_args(
        ["--config", str(_config_file(tmp_path)), "batch", str(path), "--oa-only"])

    monkeypatch.setattr(cli, "fetch_publication",
                        lambda *a, **k: pytest.fail("must not fetch"))
    assert cli.cmd_batch(args) == 2
    assert "no DOIs found in input" in capsys.readouterr().err


# -- what the commands write -------------------------------------------------

def test_get_prints_the_directory_on_stdout_and_the_summary_on_stderr(tmp_path, monkeypatch,
                                                                     capsys):
    """The split that makes `DIR=$(manuscript-fetch get ...)` work: the path is the
    only thing on stdout, so a shell can capture it without parsing prose."""
    args = cli.build_parser().parse_args(
        ["--config", str(_config_file(tmp_path)), "get", "10.1038/s41586-020-00001-1",
         "--oa-only"])
    record = _record("10.1038/s41586-020-00001-1", proxy_tried=False, expired=False)
    record["_directory"] = str(tmp_path / "corpus" / "10.1038_s41586-020-00001-1")
    monkeypatch.setattr(cli, "fetch_publication", lambda *a, **k: record)

    assert cli.cmd_get(args) == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == record["_directory"]
    assert "10.1038/s41586-020-00001-1" in captured.err


def test_get_json_omits_the_private_directory_key(tmp_path, monkeypatch):
    """`_directory` is how the CLI finds the article on disk; it is not part of the
    manifest and must not leak into a file another tool reads."""
    out = tmp_path / "record.json"
    args = cli.build_parser().parse_args(
        ["--config", str(_config_file(tmp_path)), "get", "10.1038/s41586-020-00001-1",
         "--oa-only", "--json", str(out)])
    record = _record("10.1038/s41586-020-00001-1", proxy_tried=False, expired=False)
    record["_directory"] = str(tmp_path / "somewhere")
    monkeypatch.setattr(cli, "fetch_publication", lambda *a, **k: record)
    cli.cmd_get(args)

    written = json.loads(out.read_text())
    assert "_directory" not in written
    assert written["doi"] == "10.1038/s41586-020-00001-1"


def test_a_cached_result_says_which_flag_re_fetches_it(tmp_path, monkeypatch, capsys):
    args = cli.build_parser().parse_args(
        ["--config", str(_config_file(tmp_path)), "get", "10.1038/s41586-020-00001-1",
         "--oa-only"])
    record = _record("10.1038/s41586-020-00001-1", proxy_tried=False, expired=False)
    record["cached"] = True
    record["problems"] = ["supplement 3 was truncated"]
    monkeypatch.setattr(cli, "fetch_publication", lambda *a, **k: record)
    cli.cmd_get(args)

    err = capsys.readouterr().err
    assert "! supplement 3 was truncated" in err
    assert "use --force to re-fetch" in err


def test_the_batch_report_is_one_json_object_per_line(tmp_path, monkeypatch):
    """JSONL, not a JSON array: a 500-DOI run should be streamable and greppable,
    and a crash mid-batch should still leave a readable file."""
    report = tmp_path / "run.jsonl"
    args = _args(tmp_path, 3, extra=["--oa-only", "--report", str(report)])
    monkeypatch.setattr(cli, "fetch_publication",
                        lambda doi, *a, **k: _record(doi, proxy_tried=False, expired=False))
    cli.cmd_batch(args)

    lines = report.read_text().strip().splitlines()
    assert len(lines) == 3
    assert all("_directory" not in json.loads(line) for line in lines)
    assert json.loads(lines[0])["doi"].startswith("10.1038/")


def test_batch_exits_nonzero_unless_every_article_is_complete(tmp_path, monkeypatch, capsys):
    args = _args(tmp_path, 3, extra=["--oa-only"])
    statuses = iter(["complete", "partial", "complete"])
    monkeypatch.setattr(cli, "fetch_publication", lambda doi, *a, **k: dict(
        _record(doi, proxy_tried=False, expired=False), status=next(statuses)))

    assert cli.cmd_batch(args) == 1
    assert "complete=2  partial=1" in capsys.readouterr().err


# -- CLI overrides -----------------------------------------------------------

def test_tiers_and_corpus_dir_override_the_config_file(tmp_path):
    config = _config_file(tmp_path, corpus_dir="from-config", tiers=["europepmc"])
    args = cli.build_parser().parse_args(
        ["--config", str(config), "get", "10.1/x",
         "--corpus-dir", "from-flag", "--tiers", "pmc_oa, biorxiv ,"])
    merged = cli._apply_cli_overrides(cli.load_config(config), args)

    assert merged["fetch"]["corpus_dir"] == "from-flag"
    assert merged["fetch"]["tiers"] == ["pmc_oa", "biorxiv"], "whitespace and blanks dropped"


def test_oa_only_replaces_the_tier_list_entirely():
    """The promise is that no browser opens, so it cannot be an additive filter."""
    args = cli.build_parser().parse_args(["get", "10.1/x", "--oa-only", "--tiers", "proxy_browser"])
    merged = cli._apply_cli_overrides(cli.load_config("nonexistent.yaml"), args)
    assert merged["fetch"]["tiers"] == list(cli.OA_TIERS)
    assert "proxy_browser" not in merged["fetch"]["tiers"]


def test_a_run_with_no_config_file_can_still_reach_the_s3_bucket_at_speed(tmp_path):
    """`pmc_s3` is in `DEFAULT_TIERS`, so the interval exception it depends on cannot
    live only in the repo's `config.yaml`.

    `--config` defaults to the bare name `config.yaml` and is resolved against the
    working directory -- which is precisely what `config.warn_if_config_missing`
    exists for -- and `pyproject.toml` packages only `manuscript_harvest*`, so an
    installed `manuscript-fetch` has no `config.yaml` to find at all. On the
    built-in defaults alone every S3 object cost the global 3.0 s: ~45 s of pure
    sleep for a 14-supplement article and ~150 s for one at the `max_files` cap, per
    article, with correct files, correct statuses, a note on stderr and exit 0. The
    only symptom was a batch that took hours.
    """
    fetch_cfg = cli.load_config(tmp_path / "nonexistent.yaml")["fetch"]

    assert "pmc_s3" in fetch_cfg["tiers"]
    assert fetch_cfg["min_interval_overrides"] == \
        {"pmc-oa-opendata.s3.amazonaws.com": 0.2}
    assert build_http({"fetch": fetch_cfg}).min_interval_overrides == \
        {"pmc-oa-opendata.s3.amazonaws.com": 0.2}, "and it reaches the client"


def test_a_users_own_overrides_are_added_to_the_default_not_swapped_for_it(tmp_path):
    """`merge_config` recurses into dicts, which is what makes naming one more host
    a one-line change; the same recursion means the shipped entry cannot be removed
    by deleting it, only by setting it to a slower number. Worth pinning because the
    two readings differ silently."""
    config = _config_file(tmp_path, min_interval_overrides={"api.example": 1.0})
    overrides = cli.load_config(config)["fetch"]["min_interval_overrides"]

    assert overrides == {"pmc-oa-opendata.s3.amazonaws.com": 0.2, "api.example": 1.0}


def test_no_proxy_and_headed_reach_the_nested_config():
    args = cli.build_parser().parse_args(["get", "10.1/x", "--no-proxy", "--headed"])
    merged = cli._apply_cli_overrides(cli.load_config("nonexistent.yaml"), args)
    assert merged["fetch"]["proxy"]["enabled"] is False
    assert merged["fetch"]["browser"]["headless"] is False


# -- usage and prune ---------------------------------------------------------

def _article(corpus, slug, size, *, status="complete", fetched_at="2026-01-01T00:00:00Z"):
    directory = corpus / slug
    directory.mkdir(parents=True)
    (directory / "fulltext.pdf").write_bytes(b"x" * size)
    store.write_manifest(directory, {"doi": slug.replace("_", "/"), "status": status,
                                     "fetched_at": fetched_at,
                                     "fulltext": {"path": "fulltext.pdf"}, "supplementary": []})
    return directory


def _usage_args(tmp_path, corpus, extra=()):
    return cli.build_parser().parse_args(
        ["--config", str(_config_file(tmp_path)), "usage", "--corpus-dir", str(corpus), *extra])


def test_usage_on_an_empty_corpus_is_not_an_error(tmp_path, capsys):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    assert cli.cmd_usage(_usage_args(tmp_path, corpus)) == 0
    assert "empty" in capsys.readouterr().err


def test_usage_is_oldest_first_by_default_and_largest_first_with_by_size(tmp_path, capsys):
    """Oldest-first is the default because that is the order `prune` evicts in, so
    the listing doubles as a preview of what would go."""
    corpus = tmp_path / "corpus"
    _article(corpus, "10.1_small-old", 100, fetched_at="2020-01-01T00:00:00Z")
    _article(corpus, "10.1_big-new", 9000, fetched_at="2026-01-01T00:00:00Z")

    cli.cmd_usage(_usage_args(tmp_path, corpus))
    oldest_first = capsys.readouterr().out
    cli.cmd_usage(_usage_args(tmp_path, corpus, ["--by-size"]))
    biggest_first = capsys.readouterr().out

    assert oldest_first.index("small-old") < oldest_first.index("big-new")
    assert biggest_first.index("big-new") < biggest_first.index("small-old")


def test_usage_reports_the_budget_percentage_when_one_is_set(tmp_path, capsys):
    corpus = tmp_path / "corpus"
    _article(corpus, "10.1_a", 1000)
    config = _config_file(tmp_path, max_corpus_gb=0.000001)   # ~1074 bytes
    args = cli.build_parser().parse_args(
        ["--config", str(config), "usage", "--corpus-dir", str(corpus)])

    cli.cmd_usage(args)
    err = capsys.readouterr().err
    assert "1 articles" in err and "budget" in err and "%" in err


def test_usage_says_so_when_no_budget_is_set(tmp_path, capsys):
    corpus = tmp_path / "corpus"
    _article(corpus, "10.1_a", 1000)
    cli.cmd_usage(_usage_args(tmp_path, corpus))
    assert "no budget set; see fetch.max_corpus_gb" in capsys.readouterr().err


def test_usage_truncates_to_the_limit_and_says_how_many_it_hid(tmp_path, capsys):
    """A silent trim would read as "that is the whole corpus"."""
    corpus = tmp_path / "corpus"
    for n in range(5):
        _article(corpus, f"10.1_a{n}", 100)
    cli.cmd_usage(_usage_args(tmp_path, corpus, ["--limit", "2"]))

    captured = capsys.readouterr()
    assert captured.out.count("files") == 2
    assert "... 3 more" in captured.err


def test_prune_without_a_budget_refuses_rather_than_guessing(tmp_path, capsys):
    corpus = tmp_path / "corpus"
    _article(corpus, "10.1_a", 100)
    args = cli.build_parser().parse_args(
        ["--config", str(_config_file(tmp_path)), "prune", "--corpus-dir", str(corpus)])

    assert cli.cmd_prune(args) == 2
    assert "pass --max-gb or set fetch.max_corpus_gb" in capsys.readouterr().err


def test_prune_evicts_oldest_first_and_keeps_the_manifest(tmp_path, capsys):
    corpus = tmp_path / "corpus"
    old = _article(corpus, "10.1_older", 5000, fetched_at="2020-01-01T00:00:00Z")
    new = _article(corpus, "10.1_newer", 5000, fetched_at="2026-01-01T00:00:00Z")
    args = cli.build_parser().parse_args(
        ["--config", str(_config_file(tmp_path)), "prune", "--corpus-dir", str(corpus),
         "--max-gb", "0.000005"])

    assert cli.cmd_prune(args) == 0
    assert not (old / "fulltext.pdf").exists()
    assert (new / "fulltext.pdf").exists()
    assert store.read_manifest(old)["status"] == "evicted"

    captured = capsys.readouterr()
    assert "evicted 10.1_older" in captured.out
    assert "Manifests are kept" in captured.err


def test_prune_dry_run_frees_nothing_and_says_would(tmp_path, capsys):
    corpus = tmp_path / "corpus"
    old = _article(corpus, "10.1_older", 5000, fetched_at="2020-01-01T00:00:00Z")
    _article(corpus, "10.1_newer", 5000, fetched_at="2026-01-01T00:00:00Z")
    args = cli.build_parser().parse_args(
        ["--config", str(_config_file(tmp_path)), "prune", "--corpus-dir", str(corpus),
         "--max-gb", "0.000005", "--dry-run"])

    assert cli.cmd_prune(args) == 0
    assert (old / "fulltext.pdf").exists(), "a dry run must not delete"
    assert "would evict" in capsys.readouterr().out


def test_prune_reports_when_it_cannot_reach_the_target(tmp_path, capsys):
    """The newest article is never evicted, so an impossible budget has to say why
    rather than reporting success at a size it did not reach."""
    corpus = tmp_path / "corpus"
    _article(corpus, "10.1_only", 50000)
    args = cli.build_parser().parse_args(
        ["--config", str(_config_file(tmp_path)), "prune", "--corpus-dir", str(corpus),
         "--max-gb", "0.000001"])

    cli.cmd_prune(args)
    assert "!" in capsys.readouterr().err


# -- session commands --------------------------------------------------------

def test_check_reports_a_live_session_and_exits_zero(tmp_path, monkeypatch, capsys):
    import manuscript_harvest.fetch.sources.proxy_browser as pb
    monkeypatch.setattr(pb, "check_session", lambda cfg, probe_url=None: (True, "reached nature.com"))
    args = cli.build_parser().parse_args(["--config", str(_config_file(tmp_path)), "check"])

    assert cli.cmd_check(args) == 0
    assert "session: alive -- reached nature.com" in capsys.readouterr().err


def test_check_names_the_remedy_only_for_an_expired_session(tmp_path, monkeypatch, capsys):
    """A proxy that is merely unreachable is not fixed by logging in again, so the
    remedy line has to be conditional or it becomes advice-shaped noise."""
    import manuscript_harvest.fetch.sources.proxy_browser as pb
    args = cli.build_parser().parse_args(["--config", str(_config_file(tmp_path)), "check"])

    monkeypatch.setattr(pb, "check_session", lambda cfg, probe_url=None: (False, "session_expired"))
    assert cli.cmd_check(args) == 1
    assert pb.SESSION_REMEDY in capsys.readouterr().err

    monkeypatch.setattr(pb, "check_session", lambda cfg, probe_url=None: (False, "dns failure"))
    assert cli.cmd_check(args) == 1
    assert pb.SESSION_REMEDY not in capsys.readouterr().err


def test_login_passes_the_probe_url_and_timeout_through(tmp_path, monkeypatch):
    import manuscript_harvest.fetch.sources.proxy_browser as pb
    seen = {}

    def fake_login(fetch_cfg, probe_url=None, timeout_seconds=None):
        seen.update(probe_url=probe_url, timeout_seconds=timeout_seconds)
        return 0

    monkeypatch.setattr(pb, "interactive_login", fake_login)
    args = cli.build_parser().parse_args(
        ["--config", str(_config_file(tmp_path)), "login",
         "--url", "https://www.nature.com/", "--timeout", "42"])

    assert cli.cmd_login(args) == 0
    assert seen == {"probe_url": "https://www.nature.com/", "timeout_seconds": 42}


# -- main --------------------------------------------------------------------

def test_main_turns_a_bad_doi_into_exit_two_not_a_traceback(capsys):
    """`normalize_doi` raises ValueError for anything that is not a DOI, and a user
    typo should not print a stack trace."""
    assert cli.main(["get", "definitely not a doi"]) == 2
    assert "error: not a DOI" in capsys.readouterr().err


def test_main_dispatches_to_the_named_subcommand(tmp_path, monkeypatch, capsys):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    assert cli.main(["--config", str(_config_file(tmp_path)), "usage",
                     "--corpus-dir", str(corpus)]) == 0
    assert "empty" in capsys.readouterr().err


# -- duplicate DOIs in the input ----------------------------------------------

def test_a_repeated_doi_is_fetched_once(tmp_path, monkeypatch, capsys):
    """A real 55-line input file listed one DOI three times. Each copy was fetched,
    counted again in the summary and in `--report`, and -- the reason this matters --
    counted again by the proxy circuit breaker below."""
    path = tmp_path / "papers.txt"
    path.write_text("10.1038/s41586-020-00001-1\n" * 3 + "10.1038/s41586-020-00002-1\n")
    args = cli.build_parser().parse_args(
        ["--config", str(_config_file(tmp_path)), "batch", str(path), "--oa-only"])

    fetched = []

    def fake_fetch(doi, *_a, **_k):
        fetched.append(doi)
        return _record(doi, proxy_tried=False, expired=False)

    monkeypatch.setattr(cli, "fetch_publication", fake_fetch)
    cli.cmd_batch(args)

    assert fetched == ["10.1038/s41586-020-00001-1", "10.1038/s41586-020-00002-1"]
    err = capsys.readouterr().err
    notice = next(line for line in err.splitlines() if "collapsed" in line)
    assert "collapsed 2 duplicate DOI line(s); fetching 2 distinct paper(s)" in notice
    assert "Repeated: 10.1038/s41586-020-00001-1" in notice
    assert "00002" not in notice, "only the ones that actually repeated"


def test_the_collapse_names_dois_the_input_never_spelled_that_way(tmp_path, monkeypatch,
                                                                  capsys):
    """The two lines that collapse need not have looked alike -- normalization folds
    case and strips the resolver prefix -- so a bare count leaves the user unable to
    check the run against their own input."""
    path = tmp_path / "papers.txt"
    path.write_text("https://doi.org/10.1038/S41586-020-00001-1\n"
                    "10.1038/s41586-020-00001-1\n")
    args = cli.build_parser().parse_args(
        ["--config", str(_config_file(tmp_path)), "batch", str(path), "--oa-only"])
    monkeypatch.setattr(cli, "fetch_publication",
                        lambda doi, *a, **k: _record(doi, proxy_tried=False, expired=False))
    cli.cmd_batch(args)

    assert "Repeated: 10.1038/s41586-020-00001-1" in capsys.readouterr().err


def test_the_collapse_notice_stays_short_on_a_long_input(tmp_path, monkeypatch, capsys):
    """It is a warning, not the report. The file that prompted this had 55 lines."""
    path = tmp_path / "papers.txt"
    path.write_text("".join(f"10.1038/s41586-020-{n:05d}-1\n" * 2 for n in range(1, 9)))
    args = cli.build_parser().parse_args(
        ["--config", str(_config_file(tmp_path)), "batch", str(path), "--oa-only"])
    monkeypatch.setattr(cli, "fetch_publication",
                        lambda doi, *a, **k: _record(doi, proxy_tried=False, expired=False))
    cli.cmd_batch(args)

    err = capsys.readouterr().err
    assert "collapsed 8 duplicate DOI line(s)" in err
    assert "and 3 more" in err


def test_duplicates_cannot_trip_the_proxy_breaker_on_their_own(tmp_path, monkeypatch, capsys):
    """The bug this fixes. One paywalled paper listed three times reported "3 papers
    in a row reported session_expired" and dropped the browser tier for everything
    after it -- on the evidence of a single paper."""
    path = tmp_path / "papers.txt"
    path.write_text("10.1038/s41586-020-00001-1\n" * 3 + "10.1038/s41586-020-00002-1\n")
    args = cli.build_parser().parse_args(
        ["--config", str(_config_file(tmp_path)), "batch", str(path)])

    seen = []

    def fake_fetch(doi, config, **_k):
        seen.append(list(config["fetch"].get("tiers") or []))
        return _record(doi, proxy_tried=True, expired=True)

    monkeypatch.setattr(cli, "fetch_publication", fake_fetch)
    cli.cmd_batch(args)

    assert len(seen) == 2, "two distinct papers, two fetches"
    assert all("proxy_browser" in tiers for tiers in seen), \
        "two failures is under the limit; the tier must survive"
    assert "proxy_browser is dropped" not in capsys.readouterr().err


def test_the_notice_still_fires_for_three_genuinely_distinct_papers(tmp_path, monkeypatch,
                                                                   capsys):
    """The breaker is still wanted -- it just has to count papers, not lines."""
    args = _args(tmp_path, 5)
    seen = _run_batch(monkeypatch, args, [(True, True)] * 5)

    assert all("proxy_browser" in t for t in seen[:cli.SESSION_FAILURE_LIMIT])
    assert "proxy_browser is dropped" in capsys.readouterr().err


def test_no_notice_when_there_are_no_duplicates(tmp_path, monkeypatch, capsys):
    args = _args(tmp_path, 3, extra=["--oa-only"])
    monkeypatch.setattr(cli, "fetch_publication",
                        lambda doi, *a, **k: _record(doi, proxy_tried=False, expired=False))
    cli.cmd_batch(args)
    assert "collapsed" not in capsys.readouterr().err
