"""The fetch CLI's handling of a missing proxy login.

Every test here pins a behaviour that was absent when a user ran

    manuscript-fetch batch --headed --force papers.txt

against a machine that had never run `login`. The browser tier did exactly what
it was written to do -- bounce off the IdP, name the refusal `session_expired`,
close -- once per DOI, for fifty-three DOIs, while the flag they had reached for
(`--headed`) showed them a browser that never waited for a login. Nothing was
broken; nothing said what to do either.
"""

import pytest
import yaml

from manuscript_harvest.fetch import cli


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
