"""The heartbeat both batch loops write, and what a first Ctrl-C now does.

Two behaviours are pinned here that a watcher outside the process depends on:

- one `item` event per unit of work, flushed as it happens, with `seq`/`total` --
  so progress is read rather than inferred from the prose on stderr;
- a first SIGINT that finishes the item in flight and *keeps the run's record*,
  where before it raised through the loop and lost `--report` entirely, because
  that file was written only after the loop had finished.

The exit code matters as much as the record. `cmd_batch` decides success by
comparing completions against the number of records it made, so a run stopped
after four complete papers out of twelve would otherwise have exited 0.
"""

import json
import os
import signal

import pytest
import yaml

from manuscript_harvest import progress
from manuscript_harvest.extract import cli as extract_cli
from manuscript_harvest.fetch import cli as fetch_cli


# -- the module ---------------------------------------------------------------

def test_a_log_with_no_path_writes_nothing_and_raises_nothing(tmp_path):
    """The no-op case is the common one: neither CLI is usually given the flag."""
    log = progress.ProgressLog(None)
    with log:
        log.start(total=3)
        log.item(slug="a", status="complete")
        log.end(by_status={"complete": 1})
    assert not log.enabled
    assert list(tmp_path.iterdir()) == []


def test_events_carry_the_sequence_and_the_total(tmp_path):
    path = tmp_path / "progress.jsonl"
    with progress.ProgressLog(path) as log:
        log.start(total=2, command="fetch batch")
        log.item(doi="10.1038/a", status="complete")
        log.item(doi="10.1038/b", status="partial")
        log.end(by_status={"complete": 1, "partial": 1}, stopped=False)

    events = [json.loads(line) for line in path.read_text().splitlines()]
    assert [e["event"] for e in events] == ["start", "item", "item", "end"]
    assert [e.get("seq") for e in events] == [None, 1, 2, 2]
    assert all(e["total"] == 2 for e in events)
    assert all("at" in e for e in events)
    assert events[3]["by_status"] == {"complete": 1, "partial": 1}


def test_each_line_is_on_disk_before_the_next_one_is_written(tmp_path):
    """The whole point: a reader tailing the file sees an item as it completes,
    and a killed process leaves behind what it had rather than an empty file."""
    path = tmp_path / "progress.jsonl"
    with progress.ProgressLog(path) as log:
        log.start(total=2)
        log.item(doi="10.1038/a")
        assert len(path.read_text().splitlines()) == 2
        log.item(doi="10.1038/b")
        assert len(path.read_text().splitlines()) == 3


def test_a_second_interrupt_aborts_rather_than_being_swallowed():
    """One item can take minutes. A class that ate every SIGINT would trap the
    caller in a loop they had already asked twice to end."""
    with progress.StopRequest() as stop:
        assert not stop.requested
        os.kill(os.getpid(), signal.SIGINT)
        assert stop.requested
        with pytest.raises(KeyboardInterrupt):
            os.kill(os.getpid(), signal.SIGINT)


def test_the_previous_handler_is_put_back_on_the_way_out():
    before = signal.getsignal(signal.SIGINT)
    with progress.StopRequest():
        assert signal.getsignal(signal.SIGINT) is not before
    assert signal.getsignal(signal.SIGINT) is before


# -- fetch batch --------------------------------------------------------------

def _config(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"fetch": {
        "corpus_dir": str(tmp_path / "corpus"),
        "browser": {"profile_dir": str(tmp_path / "profile" / "chrome")},
    }}))
    return path


def _dois_file(tmp_path, count):
    path = tmp_path / "papers.txt"
    path.write_text("\n".join(f"10.1038/s41586-020-{n:05d}-1" for n in range(count)))
    return path


def _batch_args(tmp_path, count, extra=()):
    return fetch_cli.build_parser().parse_args([
        "--config", str(_config(tmp_path)), "batch", str(_dois_file(tmp_path, count)),
        "--oa-only", *extra,
    ])


def _fetch_record(doi, status="complete"):
    return {
        "doi": doi,
        "slug": doi.replace("/", "_"),
        "status": status,
        "tiers_tried": ["europepmc"],
        "attempts": [],
        "problems": [],
        "fulltext": {"status": "ok", "bytes": 1000},
        "supplementary": [{"bytes": 500, "path": "supplementary/01_a.xlsx"},
                          {"bytes": 200, "name": "supplementary/02_b.gif",
                           "removed": "not_text_bearing"}],
        "supplementary_status": "fetched",
    }


def test_batch_writes_one_heartbeat_item_per_paper(tmp_path, monkeypatch):
    heartbeat = tmp_path / "progress.jsonl"
    args = _batch_args(tmp_path, 3, extra=["--progress-jsonl", str(heartbeat)])
    monkeypatch.setattr(fetch_cli, "fetch_publication",
                        lambda doi, *a, **k: _fetch_record(doi))
    assert fetch_cli.cmd_batch(args) == 0

    events = [json.loads(line) for line in heartbeat.read_text().splitlines()]
    assert [e["event"] for e in events] == ["start", "item", "item", "item", "end"]
    assert events[0]["total"] == 3 and len(events[0]["dois"]) == 3
    assert [e["seq"] for e in events[1:4]] == [1, 2, 3]
    assert events[-1]["by_status"] == {"complete": 3}
    assert events[-1]["stopped"] is False


def test_the_heartbeat_counts_every_file_but_only_the_bytes_still_on_disk(tmp_path,
                                                                         monkeypatch):
    """`files` matches what `store.summarize` prints, so the log line and the
    heartbeat cannot disagree; `bytes` answers the other question, what landed."""
    heartbeat = tmp_path / "progress.jsonl"
    args = _batch_args(tmp_path, 1, extra=["--progress-jsonl", str(heartbeat)])
    monkeypatch.setattr(fetch_cli, "fetch_publication",
                        lambda doi, *a, **k: _fetch_record(doi))
    fetch_cli.cmd_batch(args)

    item = [json.loads(line) for line in heartbeat.read_text().splitlines()][1]
    assert item["files"] == 2                 # both entries, removed one included
    assert item["bytes"] == 1000 + 500        # the removed 200 is not on disk
    assert item["status"] == "complete"


def test_the_report_is_written_as_the_run_goes_not_after_it(tmp_path, monkeypatch):
    """A batch that dies on paper 50 of 55 used to leave no report at all, having
    done all of the work."""
    report = tmp_path / "run.jsonl"
    args = _batch_args(tmp_path, 3, extra=["--report", str(report)])
    seen = []

    def fake_fetch(doi, *_a, **_k):
        # Read from inside the loop: this is the assertion that the file is being
        # appended to rather than collected in memory.
        seen.append(len(report.read_text().splitlines()) if report.exists() else 0)
        return _fetch_record(doi)

    monkeypatch.setattr(fetch_cli, "fetch_publication", fake_fetch)
    fetch_cli.cmd_batch(args)

    assert seen == [0, 1, 2]
    lines = report.read_text().strip().splitlines()
    assert len(lines) == 3
    assert all("_directory" not in json.loads(line) for line in lines)


def test_a_first_interrupt_stops_after_the_paper_in_flight(tmp_path, monkeypatch,
                                                           capsys):
    heartbeat = tmp_path / "progress.jsonl"
    report = tmp_path / "run.jsonl"
    args = _batch_args(tmp_path, 5, extra=["--progress-jsonl", str(heartbeat),
                                           "--report", str(report)])
    calls = []

    def fake_fetch(doi, *_a, **_k):
        calls.append(doi)
        if len(calls) == 2:
            os.kill(os.getpid(), signal.SIGINT)
        return _fetch_record(doi)

    monkeypatch.setattr(fetch_cli, "fetch_publication", fake_fetch)
    code = fetch_cli.cmd_batch(args)

    # The paper that was in flight when the signal arrived is finished and kept.
    assert len(calls) == 2
    assert len(report.read_text().strip().splitlines()) == 2
    assert code == progress.STOPPED_EXIT_CODE

    events = [json.loads(line) for line in heartbeat.read_text().splitlines()]
    assert events[-1]["stopped"] is True
    assert events[-1]["by_status"] == {"complete": 2}
    err = capsys.readouterr().err
    assert "stopped at your request" in err


def test_a_stopped_batch_does_not_exit_zero_just_because_what_it_reached_worked(
        tmp_path, monkeypatch):
    """The trap this exit code exists for: `complete == len(records)` is true of a
    run stopped after two good papers out of five."""
    args = _batch_args(tmp_path, 5)

    def fake_fetch(doi, *_a, **_k):
        os.kill(os.getpid(), signal.SIGINT)
        return _fetch_record(doi)

    monkeypatch.setattr(fetch_cli, "fetch_publication", fake_fetch)
    assert fetch_cli.cmd_batch(args) == progress.STOPPED_EXIT_CODE


def test_batch_without_the_flag_writes_no_heartbeat(tmp_path, monkeypatch):
    args = _batch_args(tmp_path, 2)
    monkeypatch.setattr(fetch_cli, "fetch_publication",
                        lambda doi, *a, **k: _fetch_record(doi))
    assert fetch_cli.cmd_batch(args) == 0
    assert not (tmp_path / "progress.jsonl").exists()


# -- extract all --------------------------------------------------------------

def _corpus(tmp_path, slugs):
    corpus = tmp_path / "corpus"
    for slug in slugs:
        directory = corpus / slug
        directory.mkdir(parents=True)
        (directory / "manifest.json").write_text(json.dumps(
            {"doi": slug.replace("_", "/"), "slug": slug, "status": "complete"}))
    return corpus


def _extraction(slug, status="complete"):
    return {
        "doi": slug.replace("_", "/"),
        "slug": slug,
        "status": status,
        "totals": {"blocks": 12, "tables": 3, "chars": 4000, "files": 2},
        "main_text": {"section_labelling": {"confidence": "declared"}},
        "problems": [],
    }


def _all_args(tmp_path, corpus, extra=()):
    return extract_cli.build_parser().parse_args([
        "--config", str(tmp_path / "missing.yaml"), "all",
        "--corpus-dir", str(corpus), *extra,
    ])


def test_extract_all_writes_one_heartbeat_item_per_article(tmp_path, monkeypatch):
    corpus = _corpus(tmp_path, ["10.1_a", "10.1_b"])
    heartbeat = tmp_path / "progress.jsonl"
    args = _all_args(tmp_path, corpus, extra=["--progress-jsonl", str(heartbeat)])
    monkeypatch.setattr(extract_cli.extractor, "extract_article",
                        lambda directory, **_k: _extraction(directory.name))

    assert extract_cli.cmd_all(args) == 0
    events = [json.loads(line) for line in heartbeat.read_text().splitlines()]
    assert [e["event"] for e in events] == ["start", "item", "item", "end"]
    assert events[0]["total"] == 2 and events[0]["slugs"] == ["10.1_a", "10.1_b"]
    assert events[1]["blocks"] == 12 and events[1]["tables"] == 3
    assert events[1]["sections"] == "declared"
    assert events[-1]["by_status"] == {"complete": 2}


def test_a_crashed_article_is_reported_to_the_heartbeat_too(tmp_path, monkeypatch):
    """A watcher that only heard about articles which survived would show a run
    stalling rather than one crashing."""
    corpus = _corpus(tmp_path, ["10.1_a", "10.1_b"])
    heartbeat = tmp_path / "progress.jsonl"
    args = _all_args(tmp_path, corpus, extra=["--progress-jsonl", str(heartbeat)])

    def fake_extract(directory, **_kwargs):
        if directory.name == "10.1_a":
            raise RuntimeError("mupdf said no")
        return _extraction(directory.name)

    monkeypatch.setattr(extract_cli.extractor, "extract_article", fake_extract)
    extract_cli.cmd_all(args)

    events = [json.loads(line) for line in heartbeat.read_text().splitlines()]
    crashed = [e for e in events if e.get("status") == "crashed"]
    assert len(crashed) == 1
    assert crashed[0]["slug"] == "10.1_a"
    assert "mupdf said no" in crashed[0]["error"]
    assert events[-1]["by_status"] == {"crashed": 1, "complete": 1}


def test_extract_all_stops_after_the_article_in_flight(tmp_path, monkeypatch, capsys):
    corpus = _corpus(tmp_path, ["10.1_a", "10.1_b", "10.1_c", "10.1_d"])
    heartbeat = tmp_path / "progress.jsonl"
    args = _all_args(tmp_path, corpus, extra=["--progress-jsonl", str(heartbeat)])
    seen = []

    def fake_extract(directory, **_kwargs):
        seen.append(directory.name)
        if len(seen) == 2:
            os.kill(os.getpid(), signal.SIGINT)
        return _extraction(directory.name)

    monkeypatch.setattr(extract_cli.extractor, "extract_article", fake_extract)
    code = extract_cli.cmd_all(args)

    assert seen == ["10.1_a", "10.1_b"]
    assert code == progress.STOPPED_EXIT_CODE
    events = [json.loads(line) for line in heartbeat.read_text().splitlines()]
    assert events[-1]["stopped"] is True
    assert "stopped at your request" in capsys.readouterr().err
