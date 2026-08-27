"""The control panel: what each button runs, what the page is told, and the guards.

No browser here. The panel is deliberately split so that the parts worth testing
do not need one:

`jobs.build_argv` is the command line a button becomes -- pinned because two
mistakes in it are expensive. `--config` is a top-level flag and must precede the
subcommand, and the dry-run polarity is not uniform across the destructive
commands: `prune` acts unless told `--dry-run`, while `revalidate`, `drop-media`
and `drop-orphans` only report unless told `--apply`. Getting that backwards
deletes bytes.

`state.preflight` is the answer the page shows before anything runs, and its
middle case is the one a person would guess wrong: a *partial* paper is re-fetched
by a plain run, with no `--force`.

`server`'s three guards are tested against a real socket, because the thing they
defend against is a browser on this machine carrying a request from a page on the
internet into a process that holds a library session and can delete a corpus.
"""

import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

import pytest

from manuscript_harvest import progress
from manuscript_harvest.fetch.identifiers import doi_slug
from manuscript_harvest.ui import jobs, page, server, state


# -- helpers ------------------------------------------------------------------

def _wait_for(predicate, seconds=10.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def _manifest(status="complete", supplementary=None, pdf_path="fulltext.pdf"):
    return {
        "doi": "10.1/x",
        "status": status,
        "fetched_at": "2026-01-01T00:00:00Z",
        "fulltext": {"path": pdf_path, "status": "ok", "bytes": 10},
        "supplementary": supplementary if supplementary is not None else [],
        "supplementary_status": "fetched",
    }


def _article(corpus, doi, *, status="complete", extraction=None,
             files=("fulltext.pdf",), fetched_at="2026-01-01T00:00:00Z"):
    """One corpus directory: a manifest, the files it claims, and maybe an extraction.

    The directory name comes from `doi_slug`, so a test's article is in the place
    `store.article_dir` will look for it rather than somewhere that only resembles
    it.
    """
    slug = doi_slug(doi)
    directory = corpus / slug
    directory.mkdir(parents=True, exist_ok=True)
    for name in files:
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * 10)
    record = _manifest(status=status)
    record["doi"] = doi
    record["slug"] = slug
    record["fetched_at"] = fetched_at
    record["fulltext"]["path"] = files[0] if files else None
    (directory / "manifest.json").write_text(json.dumps(record))
    if extraction is not None:
        extracted = directory / "extracted"
        extracted.mkdir(exist_ok=True)
        (extracted / "extraction.json").write_text(json.dumps(extraction))
    return directory


def _extraction(status="complete", blocks=10, tables=2, chars=1000):
    return {"status": status, "totals": {"blocks": blocks, "tables": tables,
                                         "chars": chars, "files": 1}}


# -- build_argv ---------------------------------------------------------------

def test_config_comes_before_the_subcommand():
    """`--config` is a top-level flag on both CLIs. After the subcommand it is an
    argparse error, which is at least loud -- but the panel must not rely on that."""
    argv = jobs.build_argv("fetch", config_path="config.yaml", target="dois.txt")
    index = argv.index("--config")
    assert argv[index + 1] == "config.yaml"
    assert argv[index + 2] == "batch"
    assert argv[index + 3] == "dois.txt"


def test_a_fetch_carries_the_flags_it_was_given_and_no_others():
    argv = jobs.build_argv(
        "fetch", config_path="c.yaml", target="dois.txt",
        options={"force": True, "oa_only": True, "no_supplements": False},
        progress_path="/tmp/p.jsonl")
    assert "--force" in argv and "--oa-only" in argv
    assert "--no-supplements" not in argv
    assert argv[-2:] == ["--progress-jsonl", "/tmp/p.jsonl"]


def test_options_a_command_does_not_accept_are_dropped_not_passed_on():
    """`manuscript-extract all` has no `--oa-only`. A request naming it must not
    produce a job that dies on its own argv a second after the page said it ran."""
    argv = jobs.build_argv("extract-all", config_path="c.yaml",
                           options={"oa_only": True, "force": True})
    assert "--oa-only" not in argv
    assert "--force" in argv


def test_prune_is_the_one_that_acts_unless_told_not_to():
    """The polarity trap. `prune --dry-run` reports; plain `prune` deletes."""
    preview = jobs.build_argv("prune", config_path="c.yaml", options={"apply": False})
    applied = jobs.build_argv("prune", config_path="c.yaml", options={"apply": True})
    assert "--dry-run" in preview
    assert "--dry-run" not in applied
    assert "--apply" not in applied


@pytest.mark.parametrize("kind", ["revalidate", "drop-media", "drop-orphans"])
def test_the_other_three_only_report_unless_told_to_apply(kind):
    preview = jobs.build_argv(kind, config_path="c.yaml", options={"apply": False})
    applied = jobs.build_argv(kind, config_path="c.yaml", options={"apply": True})
    assert "--apply" not in preview
    assert "--dry-run" not in preview
    assert "--apply" in applied


def test_an_unknown_command_is_refused_rather_than_assembled():
    with pytest.raises(ValueError):
        jobs.build_argv("rm -rf", config_path="c.yaml")


def test_a_fetch_without_a_dois_file_is_refused():
    with pytest.raises(ValueError):
        jobs.build_argv("fetch", config_path="c.yaml")


def test_the_corpus_override_is_only_passed_when_the_panel_was_given_one():
    without = jobs.build_argv("extract-all", config_path="c.yaml")
    with_override = jobs.build_argv("extract-all", config_path="c.yaml",
                                    corpus_dir="/data/corpus")
    assert "--corpus-dir" not in without
    assert with_override[with_override.index("--corpus-dir") + 1] == "/data/corpus"


def test_login_and_check_are_not_given_a_corpus_dir_they_do_not_accept():
    argv = jobs.build_argv("login", config_path="c.yaml", corpus_dir="/data/corpus")
    assert "--corpus-dir" not in argv


def test_a_console_script_from_another_environment_is_not_used(monkeypatch):
    """A `manuscript-fetch` on PATH from a different venv is a different version of
    this package, reading a different config and writing a different corpus."""
    monkeypatch.setattr(jobs.shutil, "which", lambda name: "/somewhere/else/bin/" + name)
    assert jobs.tool_argv("fetch") == [sys.executable, "-m",
                                       "manuscript_harvest.fetch.cli"]


# -- preflight ----------------------------------------------------------------

def test_a_partial_paper_is_re_fetched_without_force(tmp_path):
    """The answer a person would guess wrong, and the one the table exists for.

    `fetch_publication` skips a paper only when `store.manifest_is_complete` says
    it needs nothing further, which is stricter than `status == "complete"`.
    """
    corpus = tmp_path / "corpus"
    _article(corpus, "10.1038/partial", status="partial")
    _article(corpus, "10.1038/done", status="complete")

    result = state.preflight(corpus, "10.1038/partial\n10.1038/done\n10.1038/new\n")
    by_doi = {row["doi"]: row["state"] for row in result["rows"]}
    assert by_doi["10.1038/partial"] == state.PREFLIGHT_REFETCH
    assert by_doi["10.1038/done"] == state.PREFLIGHT_CACHED
    assert by_doi["10.1038/new"] == state.PREFLIGHT_NEW
    assert result["counts"] == {"new": 1, "refetch": 1, "cached": 1,
                                "total": 3, "truncated": 0}


def test_a_complete_paper_whose_files_have_gone_is_re_fetched_too(tmp_path):
    """`manifest_is_complete` checks the files exist, so a partially copied corpus
    directory is not reported as cached."""
    corpus = tmp_path / "corpus"
    directory = _article(corpus, "10.1038/gone", status="complete")
    (directory / "fulltext.pdf").unlink()

    rows = state.preflight(corpus, "10.1038/gone")["rows"]
    assert rows[0]["state"] == state.PREFLIGHT_REFETCH


def test_repeats_are_collapsed_the_way_the_run_collapses_them(tmp_path):
    """`cmd_batch` dedupes after normalization, so the panel must count the same."""
    result = state.preflight(tmp_path / "corpus",
                             "10.1038/X\nhttps://doi.org/10.1038/x\n10.1038/other\n")
    assert result["counts"]["total"] == 2
    assert result["repeated"] == ["10.1038/x"]


def test_lines_that_are_not_dois_are_named_rather_than_silently_dropped(tmp_path):
    result = state.preflight(tmp_path / "corpus",
                             "# a comment\n\nnot a doi at all\n10.1038/real\n")
    assert result["counts"]["total"] == 1
    assert result["unparseable"] == ["not a doi at all"]


# -- the DOI list picker ------------------------------------------------------

def test_requirements_txt_is_not_offered_as_a_doi_list(tmp_path):
    """Sniffed, not matched on name: this repository's root holds three
    `requirements*.txt`, and the file the DOIs live in is `finish-fetch.dois`."""
    (tmp_path / "requirements.txt").write_text("pymupdf>=1.28.2\nrequests>=2.31\n")
    (tmp_path / "finish-fetch.dois").write_text(
        "10.1126/science.aay3224\n10.1038/s41588-025-02454-1\n")
    (tmp_path / "notes.txt").write_text(
        "We should fetch 10.1038/x at some point.\nAnd think about the rest.\n"
        "Maybe next week. Or not.\n")

    found = {f["name"]: f for f in state.doi_list_files(tmp_path)}
    assert "finish-fetch.dois" in found
    assert found["finish-fetch.dois"]["dois"] == 2
    assert "requirements.txt" not in found
    assert "notes.txt" not in found


def test_a_list_with_comments_counts_only_the_doi_lines(tmp_path):
    (tmp_path / "run.dois").write_text(
        "# the ones left over\n\n10.1038/a\n10.1038/b\n")
    found = state.doi_list_files(tmp_path)
    assert found[0]["dois"] == 2 and found[0]["lines"] == 2


# -- the corpus snapshot ------------------------------------------------------

def test_the_snapshot_counts_articles_the_way_both_stages_count_them(tmp_path):
    corpus = tmp_path / "corpus"
    _article(corpus, "10.1038/a", status="complete", extraction=_extraction("complete"))
    _article(corpus, "10.1038/b", status="partial", extraction=_extraction("partial"))
    _article(corpus, "10.1038/c", status="complete")
    # A directory with no manifest is not an article -- the same test
    # `extract.cli._article_dirs` applies.
    (corpus / "scratch").mkdir()

    snapshot = state.corpus_snapshot(corpus)
    assert snapshot["papers"] == 3
    assert snapshot["fetch"] == {"complete": 2, "partial": 1}
    assert snapshot["extract"] == {"complete": 1, "partial": 1, "not extracted": 1}
    assert snapshot["totals"]["blocks"] == 20
    # Two files each, plus an extraction record for the two that have one.
    assert snapshot["files"] == 8
    assert snapshot["bytes"] > 0
    assert {a["slug"] for a in snapshot["recent"]} == {"10.1038_a", "10.1038_b",
                                                       "10.1038_c"}


def test_recently_added_is_newest_first(tmp_path):
    corpus = tmp_path / "corpus"
    _article(corpus, "10.1038/old", fetched_at="2026-01-01T00:00:00Z")
    _article(corpus, "10.1038/new", fetched_at="2026-08-01T00:00:00Z")
    assert [a["slug"] for a in state.corpus_snapshot(corpus)["recent"]] == \
        ["10.1038_new", "10.1038_old"]


def test_the_snapshot_survives_a_truncated_record(tmp_path):
    """One article of 392 being mid-write must not 500 the whole page."""
    corpus = tmp_path / "corpus"
    _article(corpus, "10.1038/ok", extraction=_extraction())
    bad = corpus / "10.1038_bad"
    bad.mkdir()
    (bad / "manifest.json").write_text('{"doi": "10.1038/bad", "stat')

    snapshot = state.corpus_snapshot(corpus)
    assert snapshot["papers"] == 2
    assert snapshot["fetch"].get("unknown") == 1


def test_the_cache_is_invalidated_rather_than_waited_out(tmp_path):
    corpus = tmp_path / "corpus"
    _article(corpus, "10.1038/a")
    cache = state.SnapshotCache(corpus, ttl=3600)
    assert cache.get()["papers"] == 1

    _article(corpus, "10.1038/b")
    assert cache.get()["papers"] == 1        # still inside the TTL
    cache.invalidate()
    assert cache.get()["papers"] == 2


def test_invalidating_works_on_a_machine_that_just_booted(tmp_path, monkeypatch):
    """`time.monotonic()` counts from the boot, so a "long ago" sentinel timestamp
    means "invalid" only on a host that has been up longer than the TTL.

    This is the shape of the bug CI caught: the first version of `invalidate` set
    the timestamp to 0.0, which on a runner 30 seconds into its life still read as
    fresh. Pinned by pretending to be that runner, so the fix cannot regress on a
    workstation with a week of uptime.
    """
    corpus = tmp_path / "corpus"
    _article(corpus, "10.1038/a")
    monkeypatch.setattr(state.time, "monotonic", lambda: 30.0)
    cache = state.SnapshotCache(corpus, ttl=3600)
    assert cache.get()["papers"] == 1

    _article(corpus, "10.1038/b")
    cache.invalidate()
    assert cache.get()["papers"] == 2


# -- the job runner -----------------------------------------------------------

def _python(code):
    return [sys.executable, "-c", code]


def test_a_jobs_output_is_captured_in_order(tmp_path):
    runner = jobs.JobRunner()
    job = runner.start("check", _python(
        "import sys; print('one'); print('two', file=sys.stderr); print('three')"),
        tmp_path, label="test")
    assert _wait_for(lambda: not job.live)

    lines = runner.snapshot()["job"]["log"]["lines"]
    assert lines[0].startswith("$ ")           # the command, echoed
    assert lines[1:] == ["one", "two", "three"]
    assert job.returncode == 0
    runner.cleanup()


def test_the_cursor_returns_only_what_the_page_has_not_seen(tmp_path):
    runner = jobs.JobRunner()
    job = runner.start("check", _python("print('a'); print('b')"), tmp_path,
                       label="test")
    assert _wait_for(lambda: not job.live)

    first = runner.snapshot(0)["job"]["log"]
    assert first["dropped"] == 0
    again = runner.snapshot(first["cursor"])["job"]["log"]
    assert again["lines"] == []
    assert again["cursor"] == first["cursor"]
    runner.cleanup()


def test_dropped_lines_are_reported_rather_than_quietly_missing(tmp_path,
                                                               monkeypatch):
    monkeypatch.setattr(jobs, "MAX_LOG_LINES", 5)
    runner = jobs.JobRunner()
    job = jobs.Job("j1", "check", ["true"], tmp_path, label="test")
    job._lines.clear()
    for n in range(20):
        job.append(str(n))

    seen = job.log_since(0)
    assert seen["dropped"] == 15
    assert seen["lines"] == ["15", "16", "17", "18", "19"]
    assert seen["cursor"] == 20
    runner.cleanup()


def test_only_one_job_runs_at_a_time(tmp_path):
    """A correctness rule, not a simplification: two fetch runs would hit a
    publisher at twice the configured rate, each obeying its own limit."""
    runner = jobs.JobRunner()
    first = runner.start("check", _python("import time; time.sleep(5)"), tmp_path,
                         label="first")
    with pytest.raises(jobs.Busy):
        runner.start("check", _python("print('second')"), tmp_path, label="second")

    runner.stop(force=True)
    assert _wait_for(lambda: not first.live)
    # Once it has finished, the next one is allowed.
    second = runner.start("check", _python("print('ok')"), tmp_path, label="second")
    assert _wait_for(lambda: not second.live)
    runner.cleanup()


def test_stopping_signals_the_job_and_says_so_in_its_log(tmp_path):
    runner = jobs.JobRunner()
    job = runner.start("check", _python("import time; time.sleep(30)"), tmp_path,
                       label="sleeper")
    assert runner.stop() is True
    assert job.stopping is True
    assert _wait_for(lambda: not job.live)
    assert job.returncode != 0
    assert any("stop requested" in line for line in job.log_since(0)["lines"])
    assert runner.stop() is False           # nothing left to stop
    runner.cleanup()


def test_a_command_that_cannot_start_is_reported_not_raised(tmp_path):
    runner = jobs.JobRunner()
    job = runner.start("check", ["/definitely/not/here"], tmp_path, label="broken")
    assert job.error is not None
    assert not job.live
    assert any("could not start" in line for line in job.log_since(0)["lines"])
    runner.cleanup()


def test_a_finished_jobs_elapsed_time_stops_counting(tmp_path):
    runner = jobs.JobRunner()
    job = runner.start("check", _python("print('done')"), tmp_path, label="quick")
    assert _wait_for(lambda: not job.live)
    first = job.summary(0)["elapsed"]
    time.sleep(0.05)
    assert job.summary(0)["elapsed"] == first
    runner.cleanup()


# -- heartbeat tailing --------------------------------------------------------

def test_the_heartbeat_is_read_as_a_run_goes(tmp_path):
    path = tmp_path / "progress.jsonl"
    job = jobs.Job("j1", "fetch", ["true"], tmp_path, label="t", progress_path=path)
    with progress.ProgressLog(path) as log:
        log.start(total=3)
        log.item(doi="10.1/a", status="complete", files=4, bytes=100)
        job.drain_progress()
        assert job.progress["total"] == 3
        assert job.progress["done"] == 1
        assert job.progress["files"] == 4

        log.item(doi="10.1/b", status="partial", files=1, bytes=50)
        log.end(by_status={"complete": 1, "partial": 1}, stopped=False)
        job.drain_progress()

    assert job.progress["done"] == 2
    assert job.progress["bytes"] == 150
    assert job.progress["by_status"] == {"complete": 1, "partial": 1}
    assert job.progress["ended"] is True
    assert [item["doi"] for item in job.progress["recent"]] == ["10.1/a", "10.1/b"]


def test_each_stage_counts_only_what_it_produces(tmp_path):
    """A fetch adds files and bytes; an extract produces blocks and tables. Each
    leaves the other's fields out, so a zero here means "not counted by this
    stage" rather than "nothing happened" -- and the page picks the pair to show
    from the job kind rather than printing both."""
    path = tmp_path / "progress.jsonl"
    job = jobs.Job("j1", "extract-all", ["true"], tmp_path, label="t",
                   progress_path=path)
    with progress.ProgressLog(path) as log:
        log.start(total=2)
        log.item(slug="a", status="complete", blocks=100, tables=4, files=3)
        log.item(slug="b", status="complete", blocks=50, tables=1, files=2)
    job.drain_progress()

    assert job.progress["blocks"] == 150
    assert job.progress["tables"] == 5
    assert job.progress["bytes"] == 0        # extract reports none, and says so as 0


def test_half_a_line_is_held_until_the_rest_arrives(tmp_path):
    """`ProgressLog` flushes per line, but a line longer than the io buffer -- the
    `start` event naming every slug in a 392-article corpus is about 15 KB --
    reaches disk in pieces. A tail that trusted the flush would parse half an
    object and drop the event."""
    path = tmp_path / "progress.jsonl"
    path.write_text('{"event": "item", "seq": 1, "status": "comp')
    job = jobs.Job("j1", "fetch", ["true"], tmp_path, label="t", progress_path=path)

    job.drain_progress()
    assert job.progress["done"] == 0

    with path.open("a", encoding="utf-8") as handle:
        handle.write('lete", "files": 2}\n')
    job.drain_progress()
    assert job.progress["done"] == 1
    assert job.progress["by_status"] == {"complete": 1}


def test_a_line_that_is_not_json_is_skipped_not_fatal(tmp_path):
    path = tmp_path / "progress.jsonl"
    path.write_text('nonsense\n{"event": "item", "seq": 1, "status": "ok"}\n')
    job = jobs.Job("j1", "fetch", ["true"], tmp_path, label="t", progress_path=path)
    job.drain_progress()
    assert job.progress["done"] == 1


def test_the_eta_is_absent_until_there_is_something_to_base_it_on(tmp_path):
    job = jobs.Job("j1", "fetch", ["true"], tmp_path, label="t")
    assert job.eta_seconds() is None
    job.progress["total"] = 10
    assert job.eta_seconds() is None       # nothing done yet
    job.progress["done"] = 10
    assert job.eta_seconds() is None       # nothing left
    job.progress["done"] = 5
    assert job.eta_seconds() is not None


# -- the server ---------------------------------------------------------------

@pytest.fixture
def panel(tmp_path):
    corpus = tmp_path / "corpus"
    _article(corpus, "10.1038/a", extraction=_extraction())
    (tmp_path / "run.dois").write_text("10.1038/a\n10.1038/new\n")
    config = {"fetch": {"corpus_dir": str(corpus), "tiers": ["europepmc"],
                        "contact_email": "someone@example.edu"}}
    made = server.Panel(root=tmp_path, config_path=tmp_path / "config.yaml",
                        config=config, corpus_dir=corpus, port=0)
    httpd = server.serve(made)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield made
    httpd.shutdown()
    httpd.server_close()
    server.cleanup(made)


def _request(panel, method, path, body=None, *, token=True, host=None, origin=None):
    connection = http.client.HTTPConnection("127.0.0.1", panel.port, timeout=10)
    headers = {}
    if token:
        headers["X-Harvest-Token"] = panel.token if token is True else token
    if origin:
        headers["Origin"] = origin
    payload = None
    if body is not None:
        payload = json.dumps(body)
        headers["Content-Type"] = "application/json"
    if host:
        headers["Host"] = host
    connection.request(method, path, body=payload, headers=headers)
    response = connection.getresponse()
    raw = response.read()
    connection.close()
    return response.status, json.loads(raw) if raw and raw[:1] in b"{[" else raw


def test_the_page_needs_the_token_from_the_printed_url(panel):
    status, _ = _request(panel, "GET", "/", token=False)
    assert status == 403
    status, body = _request(panel, "GET", f"/?t={panel.token}", token=False)
    assert status == 200
    assert b"manuscript-harvest" in body


def test_an_api_call_without_the_token_is_refused(panel):
    assert _request(panel, "GET", "/api/state", token=False)[0] == 403
    assert _request(panel, "GET", "/api/state", token="wrong")[0] == 403
    assert _request(panel, "GET", "/api/state")[0] == 200


def test_a_host_header_naming_somewhere_else_is_refused(panel):
    """The DNS-rebinding guard. A page can point its own hostname at 127.0.0.1 and
    then talk to this server as same-origin -- but it cannot forge `Host`."""
    status, body = _request(panel, "GET", "/api/state", host="evil.example.com")
    assert status == 403
    assert "127.0.0.1" in body["error"]


def test_a_cross_origin_post_is_refused(panel):
    status, _ = _request(panel, "POST", "/api/stop", {"force": False},
                         origin="https://evil.example.com")
    assert status == 403


def test_a_same_origin_post_is_allowed(panel):
    status, _ = _request(panel, "POST", "/api/stop", {"force": False},
                         origin=f"http://127.0.0.1:{panel.port}")
    assert status == 200


def test_the_state_payload_carries_what_the_header_shows(panel):
    _status, body = _request(panel, "GET", "/api/state")
    assert body["corpus"]["papers"] == 1
    assert body["busy"] is False
    assert [f["name"] for f in body["doi_files"]] == ["run.dois"]
    assert body["health"]["contact_email"] == "someone@example.edu"


def test_no_api_response_ever_carries_a_key(panel, monkeypatch):
    """`health` reports whether a key is set and where from, never its value."""
    monkeypatch.setenv("MANUSCRIPT_HARVEST_ELSEVIER_API_KEY", "sekrit-key-value")
    _status, body = _request(panel, "GET", "/api/state")
    assert body["health"]["elsevier_key"] is True
    assert body["health"]["elsevier_key_source"] == "environment"
    assert "sekrit-key-value" not in json.dumps(body)


def test_preflight_answers_for_a_named_list_in_the_panels_own_directory(panel):
    status, body = _request(panel, "POST", "/api/preflight",
                            {"source": "file", "name": "run.dois"})
    assert status == 200
    assert body["counts"]["total"] == 2


def test_a_request_cannot_name_a_file_the_panel_did_not_offer(panel):
    """A request says what the DOIs are, or names one of the lists the panel found.
    It never hands over a path."""
    status, body = _request(panel, "POST", "/api/preflight",
                            {"source": "file", "name": "../../etc/passwd"})
    assert status == 400
    assert "not one of the DOI lists" in body["error"]


def test_an_unknown_command_is_refused(panel):
    status, body = _request(panel, "POST", "/api/run", {"kind": "sudo"})
    assert status == 400
    assert "unknown command" in body["error"]


def test_applying_a_destructive_command_needs_the_typed_word(panel):
    status, body = _request(panel, "POST", "/api/run",
                            {"kind": "drop-orphans", "options": {"apply": True}})
    assert status == 400
    assert "type delete" in body["error"]

    status, body = _request(panel, "POST", "/api/run",
                            {"kind": "drop-orphans", "options": {"apply": True},
                             "confirm": "please"})
    assert status == 400


def test_a_preview_of_a_destructive_command_needs_no_confirmation(panel,
                                                                 monkeypatch):
    started = {}

    def fake_start(kind, argv, cwd, **kwargs):
        started["kind"] = kind
        started["argv"] = argv
        return jobs.Job("j1", kind, argv, cwd, label=kwargs.get("label", ""))

    monkeypatch.setattr(panel.runner, "start", fake_start)
    status, _body = _request(panel, "POST", "/api/run",
                             {"kind": "drop-orphans", "options": {"apply": False}})
    assert status == 200
    assert "--apply" not in started["argv"]


def test_extract_one_needs_a_target(panel):
    status, body = _request(panel, "POST", "/api/run", {"kind": "extract-one"})
    assert status == 400
    assert "name a DOI or slug" in body["error"]


@pytest.mark.parametrize("target", ["--force", "-x", "a" * 400, "one\ntwo"])
def test_a_target_that_argparse_would_misread_is_refused(panel, target):
    """No shell is involved, so this is not injection -- but a leading dash is read
    as a flag, which turns a typo into a job that dies on its own argv."""
    status, body = _request(panel, "POST", "/api/run",
                            {"kind": "extract-one", "target": target})
    assert status == 400
    assert "does not look like" in body["error"]


def test_a_second_run_while_one_is_live_is_a_conflict(panel):
    panel.runner.start("check", _python("import time; time.sleep(5)"), panel.root,
                       label="busy")
    status, body = _request(panel, "POST", "/api/run", {"kind": "check"})
    assert status == 409
    assert "still running" in body["error"]
    panel.runner.stop(force=True)


def test_a_run_reports_the_command_it_actually_spawned(panel, monkeypatch):
    """Shown on the page verbatim, including the panel's own plumbing flag: a panel
    that displayed a command slightly unlike the one it ran would be the first
    thing to mislead somebody debugging a run."""
    monkeypatch.setattr(panel.runner, "start",
                        lambda kind, argv, cwd, **kw: jobs.Job(
                            "j1", kind, argv, cwd, label=kw.get("label", "")))
    status, body = _request(panel, "POST", "/api/run",
                            {"kind": "fetch", "source": "file", "name": "run.dois"})
    assert status == 200
    command = body["job"]["command"]
    assert "batch" in command and "run.dois" in command
    assert "--progress-jsonl" in command
    assert body["job"]["label"] == "fetch 2 DOIs from run.dois"


def test_pasted_dois_are_written_where_the_panel_decides(panel, monkeypatch):
    captured = {}
    monkeypatch.setattr(panel.runner, "start",
                        lambda kind, argv, cwd, **kw: captured.setdefault(
                            "job", jobs.Job("j1", kind, argv, cwd,
                                            label=kw.get("label", ""))))
    status, _body = _request(panel, "POST", "/api/run",
                             {"kind": "fetch", "source": "text",
                              "text": "10.1038/pasted\n"})
    assert status == 200
    written = [arg for arg in captured["job"].argv if arg.endswith(".dois")]
    assert written and str(panel.runner.progress_dir()) in written[0]


def test_an_empty_paste_is_refused(panel):
    status, body = _request(panel, "POST", "/api/run",
                            {"kind": "fetch", "source": "text", "text": "   "})
    assert status == 400
    assert "no DOIs" in body["error"]


def test_a_body_that_is_not_json_is_refused(panel):
    connection = http.client.HTTPConnection("127.0.0.1", panel.port, timeout=10)
    connection.request("POST", "/api/run", body="not json",
                       headers={"X-Harvest-Token": panel.token})
    response = connection.getresponse()
    assert response.status == 400
    assert "not JSON" in json.loads(response.read())["error"]
    connection.close()


def test_a_client_that_hangs_up_is_not_an_incident(panel, capfd):
    """The panel's terminal has one useful line in it, the URL. A page polling
    twice a second and a browser that closes a keep-alive socket must not bury it
    in tracebacks."""
    connection = http.client.HTTPConnection("127.0.0.1", panel.port, timeout=10)
    connection.request("GET", "/api/state", headers={"X-Harvest-Token": panel.token})
    connection.getresponse().read()
    connection.sock.close()          # abrupt: no shutdown, no goodbye
    time.sleep(0.2)
    assert "Traceback" not in capfd.readouterr().err


def test_an_unknown_path_is_a_404(panel):
    assert _request(panel, "GET", "/api/nope")[0] == 404
    assert _request(panel, "POST", "/api/nope", {})[0] == 404


# -- the page -----------------------------------------------------------------

def test_the_page_carries_the_token_and_nothing_else_secret():
    rendered = page.render("t0k3n")
    assert '"t0k3n"' in rendered
    assert "X-Harvest-Token" in rendered
    # Everything user- or publisher-controlled goes in through textContent.
    assert "innerHTML" not in rendered


def test_every_element_the_script_reaches_for_exists():
    """The one bug class a Python suite can catch in the page without a browser.

    `$("m-papers-detail")` on an element that was renamed returns null, and the
    next line throws on `null.textContent` -- which stops the render half-done,
    with no server error and nothing in the log. Cheap to check, since every lookup
    goes through one helper.
    """
    script = re.search(r"<script>(.*)</script>", page.render("t"), re.S).group(1)
    wanted = set(re.findall(r'\$\("([^"]+)"\)', script))
    present = set(re.findall(r'id="([^"]+)"', page._BODY))
    assert wanted - present == set()


def test_the_pages_script_parses():
    """Nothing else in this suite executes the page's JavaScript, so a syntax
    error in it would leave the whole panel dead in a browser and the suite green.

    Skipped where no JS engine is installed: node is not a dependency of this
    package, and neither is a browser. The GitHub runners have one.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("no JavaScript engine installed to parse with")
    script = re.search(r"<script>(.*)</script>", page.render("t"), re.S).group(1)
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as handle:
        handle.write(script)
        path = handle.name
    try:
        done = subprocess.run([node, "--check", path], capture_output=True, text=True)
        assert done.returncode == 0, done.stderr
    finally:
        os.unlink(path)
