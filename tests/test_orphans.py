"""Files on disk that no manifest entry points at.

`_write_group` names a supplement `<subdir>/<NN>_<name>` with `NN` from
`enumerate()`, so a re-fetch returning a different-sized set renumbers the files and
-- before `fetch/orphans.py` -- abandoned the old names on disk. 202 files and 1.37
GB of them accumulated across 29 of 393 articles, growing by 50 files in a single
38-article `--force` batch, and no command could see any of it: `drop-media` walks
entries, `manifest_is_complete` asks only whether a named file is present, and
`usage` counted the bytes against the budget without being able to say what they
were.

Two tests here carry the design, and they are the two the fetch side can get
catastrophically wrong:

- `test_a_refetch_that_fails_keeps_the_previous_set_and_stays_complete` -- the
  preservation branches at `fetcher.py:737`/`:773` keep the *existing* entry list
  when a re-fetch came away empty, so the referenced set is then the previous
  numbering. Sweeping what the run just wrote instead of what the record finally
  says would delete the entire supplement set of exactly the articles that branch
  exists to protect.
- `test_the_manifest_is_written_before_the_sweep` -- a crash after the sweep and
  before the manifest would leave files deleted while entries still named them,
  which is `manifest_is_complete` False and every later batch re-fetching the whole
  article. `drop_media` writes per file to stay out of the same trap.

The rest is about not deleting bytes that exist in no other copy: 51 of those 202
files, 0.869 GB, were content a manifest had merely lost track of.
"""

import json
import os
from pathlib import Path

import pytest

from manuscript_harvest.fetch import fetcher, orphans, store
from manuscript_harvest.fetch.identifiers import Identifiers
from manuscript_harvest.fetch.orphans import (
    REDUNDANT,
    REDUNDANT_ARCHIVE,
    UNIQUE,
    classify,
    landing_html_state,
    summarize,
    sweep_article,
    sweep_article_report,
    sweep_corpus,
    unreferenced_files,
)
from manuscript_harvest.fetch.sources.base import (
    ROLE_LANDING,
    ROLE_MEDIA,
    ROLE_PDF,
    ROLE_SUPPLEMENT,
    FetchedFile,
    SourceResult,
)
from tests.fakes import DOI, make_article, make_pdf, make_xlsx, make_zip

TITLE = "Single-cell atlas of the human pancreas"
#: `identify_fulltext` compares the PDF's own text against the DOI and title it was
#: asked for, so a fixture PDF has to carry one of them or every fetch below lands on
#: `identity_unverified` -- which is not in `store.PDF_USABLE`, and the article would
#: never reach `complete` for reasons that have nothing to do with this sweep.
PDF = make_pdf(text=f"{TITLE}. doi:{DOI}. Methods. Islets were dissociated. " * 6)
TABLE_A = make_xlsx({"S1": [["gene", "log2fc"], ["TP53", 1.4]]})
TABLE_B = make_xlsx({"S2": [["cell", "count"], ["beta", 812]]})
TABLE_C = make_xlsx({"S3": [["donor", "age"], ["D1", 47]]})


def _article(tmp_path, supplements=(), **kwargs):
    kwargs.setdefault("fulltext", PDF)
    return make_article(tmp_path / store.doi_slug(DOI),
                        supplements=list(supplements), **kwargs)


def _record(directory) -> dict:
    record = store.read_manifest(directory)
    record["_directory"] = str(directory)
    return record


def _orphan(directory, relative: str, content: bytes) -> Path:
    """Write a file no manifest entry names -- what a previous numbering left."""
    target = Path(directory) / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return target


def _skip_if_root():
    """`unlink` needs write permission on the containing directory, and root has it
    whatever the mode says -- so the tests that lock a directory prove nothing there.
    Same guard `test_text_bearing` uses for `drop-media`'s refusal path."""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root ignores the directory mode this needs")


def _config(tmp_path, corpus) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps({"fetch": {"corpus_dir": str(corpus)}}))
    return path


# -- the referenced set ------------------------------------------------------

def test_the_referenced_set_is_every_path_a_record_names():
    """All four single-artifact keys and both group keys, which is what makes the
    subtraction safe: a key this misses is a file the sweep would delete."""
    record = {
        "fulltext": {"path": "fulltext.pdf"},
        "fulltext_xml": {"path": "fulltext.nxml"},
        "landing_html": {"path": "landing.html"},
        "supplementary": [{"path": "supplementary/01_a.xlsx"}],
        "media": [{"path": "media/01_movie.mp4"}],
    }
    assert store.referenced_paths(record) == {
        "fulltext.pdf", "fulltext.nxml", "landing.html",
        "supplementary/01_a.xlsx", "media/01_movie.mp4"}


def test_no_path_anywhere_in_a_record_escapes_the_referenced_set(monkeypatch, tmp_path):
    """`store.SINGLE_ARTIFACTS`/`GROUP_ARTIFACTS` name the keys the walk subtracts, and
    this is what stops them drifting from what a fetch actually writes: a fifth
    artifact key added to the manifest and not to those tuples is a file the sweep
    would start deleting the moment it appeared.

    Asserted against a generic search for every `"path"` anywhere in a real fetched
    record -- rather than against `new_record`, which carries neither `landing_html`
    nor `media` until a tier returns one, so listing its keys would have proved the
    opposite of the intent.
    """
    record = _fetch(monkeypatch, tmp_path, [("media-1.xlsx", TABLE_A)],
                    media=[("movie1.mp4", b"\x00\x00\x00 ftypisom movie")],
                    landing=b"<html/>", text_bearing_only=False)

    found = set()

    def walk(node):
        if isinstance(node, dict):
            if isinstance(node.get("path"), str):
                found.add(node["path"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk({k: v for k, v in record.items() if k != "_directory"})
    assert found == store.referenced_paths(record)
    assert "media/01_movie1.mp4" in found and store.LANDING_HTML in found, \
        "the fixture has to exercise both of the keys a fresh record omits"


def test_a_policy_removal_leaves_nothing_referenced_and_nothing_to_delete(tmp_path):
    """`drop-media` drops the `path` key on purpose (`store.mark_entry_removed`), so
    a swept entry names nothing. It must not therefore look like licence to delete
    something, and the file it used to name is already gone -- so the two commands
    compose to nothing rather than to a fight."""
    directory = _article(tmp_path, supplements=[("table_s1.xlsx", TABLE_A)])
    record = _record(directory)
    store.mark_entry_removed(record["supplementary"][0], "image")
    (directory / "supplementary/01_table_s1.xlsx").unlink()

    assert store.referenced_paths(record) == {"fulltext.pdf"}
    assert unreferenced_files(directory, record) == []


# -- finding them ------------------------------------------------------------

def test_a_renumbered_leftover_is_the_thing_this_finds(tmp_path):
    """`10.64898/2026.02.15.704933` in miniature: the same bytes under an old number
    that the current record no longer names. It held `media-7.xlsx` four times over
    -- `02_`, `05_`, `06_` and `07_`, all 1057643 bytes -- with only `07_`
    referenced."""
    directory = _article(tmp_path, supplements=[("media-7.xlsx", TABLE_A)])
    _orphan(directory, "supplementary/02_media-7.xlsx", TABLE_A)
    _orphan(directory, "supplementary/05_media-7.xlsx", TABLE_A)

    found = unreferenced_files(directory, _record(directory))

    assert [f["path"] for f in found] == ["supplementary/02_media-7.xlsx",
                                         "supplementary/05_media-7.xlsx"]
    assert all(f["bytes"] == len(TABLE_A) for f in found)


def test_the_articles_own_files_are_out_of_scope_structurally(tmp_path):
    """`fulltext.pdf`, `fulltext.nxml`, `landing.html` and `manifest.json` live at the
    article root under fixed names that no `enumerate()` touches, and the walk never
    goes there. Proven with a record that names *none* of them, which is the state a
    path-based guard alone would have to catch."""
    directory = _article(tmp_path, xml=b"<article/>", landing=b"<html/>",
                         supplements=[("table_s1.xlsx", TABLE_A)])
    record = _record(directory)
    record["fulltext"] = {"status": "not_found", "path": None}
    record["fulltext_xml"] = None

    assert unreferenced_files(directory, record) == []
    assert (directory / "fulltext.pdf").exists()
    assert (directory / "fulltext.nxml").exists()
    assert (directory / "landing.html").exists()
    assert (directory / "manifest.json").exists()


def test_an_evicted_article_has_nothing_to_sweep(tmp_path):
    """`store.evict_article` takes every byte and leaves the paths in place, so the
    subtraction would be over an empty directory and a full record. `drop_media`
    declines the same article for the same reason."""
    directory = _article(tmp_path, supplements=[("table_s1.xlsx", TABLE_A)])
    store.evict_article(directory)
    _orphan(directory, "supplementary/09_stray.xlsx", TABLE_B)

    assert unreferenced_files(directory, _record(directory)) == []
    assert sweep_article_report(directory, apply=True)["note"].startswith("evicted")
    assert (directory / "supplementary/09_stray.xlsx").exists()


# -- classifying them --------------------------------------------------------

def test_a_leftover_whose_bytes_are_still_stored_is_redundant(tmp_path):
    """136 of the 202, 0.485 GB. Deleting one loses nothing, and `same_as` is the
    evidence for saying so."""
    directory = _article(tmp_path, supplements=[("media-7.xlsx", TABLE_A)])
    _orphan(directory, "supplementary/02_media-7.xlsx", TABLE_A)

    row, = classify(directory, _record(directory))

    assert row["kind"] == REDUNDANT
    assert row["same_as"] == ["supplementary/01_media-7.xlsx"]


def test_a_leftover_whose_bytes_are_stored_nowhere_is_unique(tmp_path):
    """51 of the 202, and 0.869 GB -- two thirds of the bytes against a third of the
    count, which is why one number for "unreferenced" would have been the wrong
    thing to report. `10.1126/science.aat1699` is the extreme: 326.9 MB of
    supplementary PDFs and tables on disk, referenced by nothing, on an article stuck
    at `suppl=expected_but_missing`."""
    directory = _article(tmp_path, supplements=[("media-7.xlsx", TABLE_A)])
    _orphan(directory, "supplementary/02_media-9.xlsx", TABLE_B)

    row, = classify(directory, _record(directory))

    assert row["kind"] == UNIQUE and "same_as" not in row


def test_copies_of_an_unreferenced_file_are_still_unique(tmp_path):
    """Bytes stored twice and referenced not at all -- `media-9.xlsx` is in
    `10.64898/2026.02.15.704933` three times this way and `media-16.xlsx` twice.
    Keeping one copy would preserve the content, so `copies` reports the redundancy
    and the verdict stays `unique`: choosing which copy survives is a human's call,
    and a sweep that took all of them would lose the file."""
    directory = _article(tmp_path, supplements=[("media-7.xlsx", TABLE_A)])
    _orphan(directory, "supplementary/03_media-9.xlsx", TABLE_B)
    _orphan(directory, "supplementary/07_media-9.xlsx", TABLE_B)

    rows = classify(directory, _record(directory))

    assert {r["kind"] for r in rows} == {UNIQUE}
    assert all(r["copies"] == ["supplementary/03_media-9.xlsx",
                               "supplementary/07_media-9.xlsx"] for r in rows)


def test_an_archive_whose_every_member_is_stored_is_redundant(tmp_path):
    """`10.1126/science.abo1984`'s orphaned `tables_s1_to_s71.zip`: 31.7 MB of bytes
    that appear nowhere else, and all 264 members byte-identical to files already
    stored. PMC flattens and renames the deposit, so not one member *name* matches --
    only content shows it, which is why this check compares digests and not names."""
    directory = _article(tmp_path, supplements=[("table_s1.xlsx", TABLE_A),
                                                ("table_s2.xlsx", TABLE_B)])
    _orphan(directory, "supplementary/09_tables_s1_to_s2.zip",
            make_zip([("renamed_by_pmc_1.xlsx", TABLE_A),
                      ("renamed_by_pmc_2.xlsx", TABLE_B)]))

    row, = classify(directory, _record(directory))

    assert row["kind"] == REDUNDANT_ARCHIVE
    assert row["members"] == 2 and row["members_stored"] == 2


def test_an_archive_with_one_member_of_its_own_is_not(tmp_path):
    """`science.adf5357`'s is 63.6 MB with 9 of 19 members stored, and
    `sciimmunol.adf9988`'s `movies_s1_to_s6.zip` is 107 MB with 0 of 6. A rule that
    resolved an archive on any containment would have deleted both."""
    directory = _article(tmp_path, supplements=[("table_s1.xlsx", TABLE_A)])
    _orphan(directory, "supplementary/09_tables.zip",
            make_zip([("stored.xlsx", TABLE_A), ("never_stored.xlsx", TABLE_C)]))

    row, = classify(directory, _record(directory))

    assert row["kind"] == UNIQUE
    assert row["members"] == 2 and row["members_stored"] == 1


def test_an_unreadable_archive_is_unique_rather_than_a_crash(tmp_path):
    """"I cannot open this" must not arrive as "there is nothing inside", because the
    second reads as full containment and a `.zip` with zero members would classify
    redundant. Refusing to guess is the whole value of the member walk."""
    directory = _article(tmp_path, supplements=[("table_s1.xlsx", TABLE_A)])
    _orphan(directory, "supplementary/09_truncated.zip", b"PK\x03\x04 not really")

    row, = classify(directory, _record(directory))

    assert row["kind"] == UNIQUE
    assert "members" not in row, "an archive that would not open reports no count"


def test_an_empty_archive_is_not_read_as_fully_contained(tmp_path):
    """The other half of the same edge: a zip that opens and holds nothing has 0 of 0
    members stored, and `0 == 0` would have made it redundant."""
    directory = _article(tmp_path, supplements=[("table_s1.xlsx", TABLE_A)])
    _orphan(directory, "supplementary/09_empty.zip", make_zip([]))

    row, = classify(directory, _record(directory))

    assert row["kind"] == UNIQUE and row["members"] == 0


def test_a_referenced_file_that_is_missing_makes_nothing_redundant(tmp_path):
    """A referenced path with no file is the signal that makes `manifest_is_complete`
    false and the next batch re-fetch. It is not this command's business, and it must
    never become the evidence that another file is a duplicate -- the entry's recorded
    sha256 would have said the bytes were safe when they are not on disk at all,
    which is why `_referenced_hashes` hashes files rather than reading the record."""
    directory = _article(tmp_path, supplements=[("media-7.xlsx", TABLE_A)])
    (directory / "supplementary/01_media-7.xlsx").unlink()
    _orphan(directory, "supplementary/02_media-7.xlsx", TABLE_A)

    row, = classify(directory, _record(directory))

    assert row["kind"] == UNIQUE, "the last copy of these bytes is not a duplicate"


# -- sweeping them -----------------------------------------------------------

def test_a_report_touches_nothing(tmp_path):
    directory = _article(tmp_path, supplements=[("media-7.xlsx", TABLE_A)])
    _orphan(directory, "supplementary/02_media-7.xlsx", TABLE_A)
    before = (directory / "manifest.json").read_text()

    report = sweep_article_report(directory)

    assert report["removed"] is False and len(report["files"]) == 1
    assert (directory / "supplementary/02_media-7.xlsx").exists()
    assert (directory / "manifest.json").read_text() == before


def test_applying_deletes_only_the_lossless_ones_by_default(tmp_path):
    """The safety property, and the reason `--include-unique` is a separate flag:
    `corpus/` is gitignored and unrecoverable, and two thirds of the bytes measured
    were stored in no other copy."""
    directory = _article(tmp_path, supplements=[("media-7.xlsx", TABLE_A)])
    _orphan(directory, "supplementary/02_media-7.xlsx", TABLE_A)
    _orphan(directory, "supplementary/03_media-9.xlsx", TABLE_B)

    report = sweep_article_report(directory, apply=True)

    assert [f["path"] for f in report["files"]] == ["supplementary/02_media-7.xlsx"]
    assert [f["path"] for f in report["kept"]] == ["supplementary/03_media-9.xlsx"]
    assert not (directory / "supplementary/02_media-7.xlsx").exists()
    assert (directory / "supplementary/03_media-9.xlsx").exists()
    assert (directory / "supplementary/01_media-7.xlsx").exists()


def test_include_unique_is_what_it_takes_to_delete_the_rest(tmp_path):
    directory = _article(tmp_path, supplements=[("media-7.xlsx", TABLE_A)])
    _orphan(directory, "supplementary/03_media-9.xlsx", TABLE_B)

    report = sweep_article_report(directory, apply=True, include_unique=True)

    assert [f["path"] for f in report["files"]] == ["supplementary/03_media-9.xlsx"]
    assert not (directory / "supplementary/03_media-9.xlsx").exists()


def test_no_manifest_write_when_files_are_deleted(tmp_path):
    """The inverse of `drop_media`, which writes the manifest once per file. The
    files here have no entries, so there is nothing to mark and the record is already
    true the moment they are gone -- and an interrupt mid-loop leaves fewer orphans
    rather than a record naming files that are not there."""
    directory = _article(tmp_path, supplements=[("media-7.xlsx", TABLE_A)])
    _orphan(directory, "supplementary/02_media-7.xlsx", TABLE_A)
    before = (directory / "manifest.json").read_text()

    sweep_article_report(directory, apply=True)

    assert (directory / "manifest.json").read_text() == before
    assert store.manifest_is_complete(_record(directory)) is True


def test_emptying_the_directory_removes_it(tmp_path):
    """An empty `supplementary/` reads as an article whose supplements were never
    fetched, which is the one thing this corpus's status taxonomy exists to
    distinguish from an article that has none. `drop_media` prunes for the same
    reason."""
    directory = _article(tmp_path)
    _orphan(directory, "supplementary/01_stray.xlsx", TABLE_A)

    report = sweep_article_report(directory, apply=True, include_unique=True)

    assert report["emptied_dirs"] == [store.SUPPLEMENT_DIR]
    assert not (directory / store.SUPPLEMENT_DIR).exists()


def test_a_directory_that_still_holds_a_file_stays(tmp_path):
    directory = _article(tmp_path, supplements=[("media-7.xlsx", TABLE_A)])
    _orphan(directory, "supplementary/02_media-7.xlsx", TABLE_A)

    report = sweep_article_report(directory, apply=True)

    assert report["emptied_dirs"] == []
    assert (directory / store.SUPPLEMENT_DIR).is_dir()


def test_a_file_that_cannot_be_deleted_is_reported_not_raised(tmp_path):
    """A fetch has already written a good manifest by the time the sweep runs, so an
    unlink it cannot do must not sink the run. `cmd_drop_orphans` exits 1 on this for
    the reason `cmd_drop_media` records: the totals line would otherwise read as a
    description of a corpus that is not in that state."""
    _skip_if_root()
    directory = _article(tmp_path, supplements=[("media-7.xlsx", TABLE_A)])
    _orphan(directory, "supplementary/02_media-7.xlsx", TABLE_A)
    (directory / store.SUPPLEMENT_DIR).chmod(0o500)
    try:
        report = sweep_article_report(directory, apply=True)
    finally:
        (directory / store.SUPPLEMENT_DIR).chmod(0o700)

    assert report["files"] == [] and report["bytes"] == 0
    assert [f["path"] for f in report["failed"]] == ["supplementary/02_media-7.xlsx"]
    assert report["note"] == "1 file(s) could not be deleted"
    assert (directory / "supplementary/02_media-7.xlsx").exists()


def test_a_second_sweep_has_nothing_to_do(tmp_path):
    directory = _article(tmp_path, supplements=[("media-7.xlsx", TABLE_A)])
    _orphan(directory, "supplementary/02_media-7.xlsx", TABLE_A)

    assert len(sweep_article_report(directory, apply=True)["files"]) == 1
    assert sweep_article_report(directory, apply=True)["files"] == []


def test_the_fetch_path_sweep_keeps_bytes_stored_nowhere_else(tmp_path):
    """**The regression test for 49 MB of deleted supplements.**

    This asserted the opposite until 2026-08-27, on the argument that a file
    unreferenced *during a fetch* is one the run's own renumbering just abandoned, so
    its content is either still on disk under the new number or was never wanted. The
    second half is false, and a `--force` batch found out: `10.1126/science.aax6234`
    had seven referenced supplements at 58.4 MB, a re-fetch returned three at 9.4 MB
    while its own manifest said "5 of 8 supplementary file(s) listed on the page could
    not be fetched", and `sweep_article` deleted the other four.

    The renumbering case it was built for still works -- that is the `02_` file below,
    byte-identical to `01_` and gone. What changed is that bytes stored under no other
    name survive, for `drop-orphans` to report to a human.
    """
    directory = _article(tmp_path, supplements=[("media-7.xlsx", TABLE_A)])
    _orphan(directory, "supplementary/02_media-7.xlsx", TABLE_A)   # renumbering residue
    _orphan(directory, "supplementary/03_media-9.xlsx", TABLE_B)   # nowhere else

    result = sweep_article(directory, _record(directory))

    assert result["files"] == ["supplementary/02_media-7.xlsx"]
    assert result["bytes"] == len(TABLE_A)
    assert result["kept"] == ["supplementary/03_media-9.xlsx"]
    assert result["kept_bytes"] == len(TABLE_B)
    assert result["failed"] == []
    assert _files_on_disk(directory) == ["01_media-7.xlsx", "03_media-9.xlsx"]


def test_the_fetch_path_sweep_does_not_open_archives(tmp_path):
    """`inspect_archives=False`, and the article that prompted the fix is why.

    A renumbered archive is byte-identical to its new copy, so the plain hash resolves
    it and the member walk buys nothing on this path. Opening one would have cost a
    347 MB decompression on `10.1126/science.aat1699`'s new `.gz` supplement, during a
    fetch. The command still opens them -- that is where the question matters.
    """
    directory = _article(tmp_path, supplements=[("table_s1.xlsx", TABLE_A)])
    _orphan(directory, "supplementary/09_tables.zip",
            make_zip([("renamed_by_pmc.xlsx", TABLE_A)]))

    result = sweep_article(directory, _record(directory))

    assert result["kept"] == ["supplementary/09_tables.zip"], \
        "fully contained, but not resolved as such without the walk"
    assert result["files"] == []
    # The command, asked the same question, does open it.
    row, = classify(directory, _record(directory))
    assert row["kind"] == REDUNDANT_ARCHIVE


def test_the_corpus_pass_reports_every_article(tmp_path):
    """`revalidate_corpus` and `drop_media_corpus`'s shape: "393 checked, 29 holding
    unreferenced files" is the line that tells a user the pass ran over the corpus
    they meant."""
    corpus = tmp_path / "corpus"
    dirty = make_article(corpus / "dirty", fulltext=PDF,
                         supplements=[("media-7.xlsx", TABLE_A)])
    _orphan(dirty, "supplementary/02_media-7.xlsx", TABLE_A)
    make_article(corpus / "clean", fulltext=PDF,
                 supplements=[("media-1.xlsx", TABLE_B)])

    reports = sweep_corpus(corpus)
    totals = summarize(reports)

    assert totals["articles"] == 2 and totals["affected"] == 1
    assert totals["files"] == 1 and totals["bytes"] == len(TABLE_A)
    assert totals["by_kind"] == {REDUNDANT: [1, len(TABLE_A)]}


def test_a_named_slug_limits_the_pass(tmp_path):
    corpus = tmp_path / "corpus"
    for name in ("one", "two"):
        article = make_article(corpus / name, fulltext=PDF,
                               supplements=[("media-7.xlsx", TABLE_A)])
        _orphan(article, "supplementary/02_media-7.xlsx", TABLE_A)

    reports = sweep_corpus(corpus, apply=True, slugs=["one"])

    assert [r["slug"] for r in reports] == ["one"]
    assert (corpus / "two/supplementary/02_media-7.xlsx").exists()


def test_an_article_with_no_manifest_is_left_alone(tmp_path):
    """No record is not the same as a record that references nothing. Without a
    manifest there is no referenced set to subtract, so every file in the directory
    would look unreferenced -- which is a whole article, deleted for the crime of a
    read that failed."""
    directory = tmp_path / "corpus" / "no-manifest"
    _orphan(directory, "supplementary/01_table.xlsx", TABLE_A)

    report = sweep_article_report(directory, apply=True, include_unique=True)

    assert report["note"] == "no manifest" and report["files"] == []
    assert (directory / "supplementary/01_table.xlsx").exists()


# -- the unreferenced landing page -------------------------------------------

def test_a_landing_page_with_no_entry_is_reported_not_deleted(tmp_path):
    """26 articles here hold one, and 0 have the reverse -- the asymmetry a re-fetch
    before commit 186b2e4 left behind, when a run that came away without a landing
    page simply did not set the key. None of the 26 files is an article: thirteen are
    ~1 KB Stanford "EZProxy error page" bodies, one a 27 KB Cloudflare challenge, and
    eight 813 KB Elsevier TDM-policy pages. That is the evidence `proxy_browser` saves
    the page for, so it is adopted rather than swept."""
    directory = _article(tmp_path, landing=b"<html><title>EZProxy error page</title>")

    assert landing_html_state(directory, _record(directory)) == "unreferenced"
    report = sweep_article_report(directory, apply=True, include_unique=True)
    assert report["landing"] == "unreferenced"
    assert (directory / store.LANDING_HTML).exists(), "reported, never deleted"


def test_adopting_records_bytes_and_sha256_and_no_url(tmp_path):
    """`url` is the one field a reader would use to decide whether the page is the
    article, and the run that fetched it is gone from the record -- so it stays absent
    and `adopted_reason` says why, rather than being filled in from the DOI as though
    it had been observed."""
    body = b"<html><title>Just a moment...</title>"
    directory = _article(tmp_path, landing=body)

    report = sweep_article_report(directory, apply=True, adopt_landing=True)

    assert report["landing"] == "adopted"
    entry = store.read_manifest(directory)["landing_html"]
    assert entry["path"] == store.LANDING_HTML
    assert entry["bytes"] == len(body)
    assert entry["sha256"] == store.sha256_bytes(body)
    assert entry["adopted"] is True and "predates" in entry["adopted_reason"]
    assert "url" not in entry


def test_an_adopted_landing_page_is_no_longer_unreferenced(tmp_path):
    directory = _article(tmp_path, landing=b"<html/>")
    sweep_article_report(directory, apply=True, adopt_landing=True)

    assert landing_html_state(directory, _record(directory)) is None
    assert sweep_article_report(directory)["landing"] is None


def test_a_recorded_landing_page_is_not_reported(tmp_path):
    directory = _article(tmp_path, landing=b"<html/>")
    record = _record(directory)
    record["landing_html"] = {"path": store.LANDING_HTML, "url": "https://example.org"}
    store.write_manifest(directory, record)

    assert landing_html_state(directory, _record(directory)) is None


# -- the command -------------------------------------------------------------

def test_the_cli_reports_then_removes(tmp_path, capsys):
    """`revalidate` and `drop-media`'s shape: report by default, delete only with
    `--apply`, because `corpus/` is gitignored and deleting bytes should be a decision
    rather than a side effect of looking."""
    from manuscript_harvest.fetch.cli import main

    corpus = tmp_path / "corpus"
    article = make_article(corpus / "art", fulltext=PDF,
                           supplements=[("media-7.xlsx", TABLE_A)])
    _orphan(article, "supplementary/02_media-7.xlsx", TABLE_A)
    config = _config(tmp_path, corpus)

    assert main(["--config", str(config), "drop-orphans"]) == 0
    reported = capsys.readouterr()
    assert "would remove art: 1 file(s)" in reported.out
    assert "1 holding files no manifest entry points at" in reported.err
    assert "--apply" in reported.err
    assert (article / "supplementary/02_media-7.xlsx").exists()

    assert main(["--config", str(config), "drop-orphans", "--apply"]) == 0
    applied = capsys.readouterr()
    assert "removed art: 1 file(s)" in applied.out
    assert not (article / "supplementary/02_media-7.xlsx").exists()
    assert (article / "supplementary/01_media-7.xlsx").exists()


def test_the_cli_keeps_bytes_stored_nowhere_else_until_told_otherwise(tmp_path, capsys):
    """The line a human acts on. `10.1126/science.aat1699` holds 326.9 MB of
    supplementary PDFs and tables its manifest references not at all, and reporting it
    inside a corpus-wide "1.37 GB unreferenced" total is how it would get deleted."""
    from manuscript_harvest.fetch.cli import main

    corpus = tmp_path / "corpus"
    article = make_article(corpus / "art", fulltext=PDF,
                           supplements=[("media-7.xlsx", TABLE_A)])
    _orphan(article, "supplementary/03_media-9.xlsx", TABLE_B)
    config = _config(tmp_path, corpus)

    assert main(["--config", str(config), "drop-orphans", "--apply"]) == 0
    kept = capsys.readouterr()
    assert "kept art: 1 file(s)" in kept.out and "stored nowhere else" in kept.out
    assert "--include-unique" in kept.err
    assert (article / "supplementary/03_media-9.xlsx").exists()

    assert main(["--config", str(config), "drop-orphans", "--apply",
                 "--include-unique"]) == 0
    assert not (article / "supplementary/03_media-9.xlsx").exists()


def test_the_cli_reports_a_landing_page_it_could_adopt(tmp_path, capsys):
    from manuscript_harvest.fetch.cli import main

    corpus = tmp_path / "corpus"
    article = make_article(corpus / "art", fulltext=PDF, landing=b"<html/>")
    config = _config(tmp_path, corpus)

    assert main(["--config", str(config), "drop-orphans"]) == 0
    assert "hold a landing.html no manifest entry names" in capsys.readouterr().err

    assert main(["--config", str(config), "drop-orphans", "--apply",
                 "--adopt-landing"]) == 0
    assert "now have a manifest entry" in capsys.readouterr().err
    assert store.read_manifest(article)["landing_html"]["adopted"] is True
    assert (article / store.LANDING_HTML).exists()


def test_a_sweep_the_filesystem_refuses_exits_1(tmp_path, capsys):
    """`cmd_drop_media`'s lesson, applied before it could be relearned: the report is
    rewritten to what was actually deleted, so an article whose every `unlink` raised
    comes back with `files == []` and reads exactly like an article with nothing to
    sweep. Exit 0 there would tell a script the corpus is clean."""
    _skip_if_root()
    from manuscript_harvest.fetch.cli import main

    corpus = tmp_path / "corpus"
    article = make_article(corpus / "art", fulltext=PDF,
                           supplements=[("media-7.xlsx", TABLE_A)])
    _orphan(article, "supplementary/02_media-7.xlsx", TABLE_A)
    config = _config(tmp_path, corpus)
    locked = article / store.SUPPLEMENT_DIR
    locked.chmod(0o500)
    try:
        code = main(["--config", str(config), "drop-orphans", "--apply"])
        printed = capsys.readouterr()
    finally:
        locked.chmod(0o700)

    assert code == 1
    assert "02_media-7.xlsx not deleted" in printed.err
    assert "Permission denied" in printed.err
    assert "no record is now wrong" in printed.err, \
        "the reassuring half: nothing ever claimed these files existed"
    assert (article / "supplementary/02_media-7.xlsx").exists()


def test_an_empty_corpus_says_so(tmp_path, capsys):
    from manuscript_harvest.fetch.cli import main

    config = _config(tmp_path, tmp_path / "corpus")
    assert main(["--config", str(config), "drop-orphans"]) == 0
    assert "no articles" in capsys.readouterr().err


# -- the fetch path ----------------------------------------------------------
#
# `fetch_publication` end to end with one fake tier, because the two behaviours that
# matter are about the *interaction* between the preservation branches and the sweep
# and neither is visible from either side alone.

class _FakeTier:
    """One tier that hands back exactly what a test tells it to."""

    name = "fake"

    def __init__(self, supplements, with_pdf=True, suppl_status="fetched",
                 media=(), landing=None):
        self.supplements = supplements
        self.with_pdf = with_pdf
        self.suppl_status = suppl_status
        self.media = media
        self.landing = landing

    def applies(self, ids):
        return True

    def fetch(self, ids, need_pdf: bool, need_supplements: bool) -> SourceResult:
        result = SourceResult(tier=self.name)
        if self.with_pdf:
            result.files.append(FetchedFile(role=ROLE_PDF, name="fulltext.pdf",
                                           content=PDF, url="https://example.org/pdf"))
            result.pdf_status = "ok"
        else:
            result.pdf_status = "not_found"
        if self.landing is not None:
            result.files.append(FetchedFile(role=ROLE_LANDING, name="landing.html",
                                            content=self.landing,
                                            url="https://example.org/article"))
        for role, group in ((ROLE_SUPPLEMENT, self.supplements),
                            (ROLE_MEDIA, self.media)):
            for name, content in group:
                result.files.append(FetchedFile(role=role, name=name, content=content,
                                                url=f"https://example.org/{name}"))
        result.suppl_status = self.suppl_status
        return result


def _fetch(monkeypatch, tmp_path, supplements, with_pdf=True,
           suppl_status="fetched", force=False, media=(), landing=None,
           text_bearing_only=True):
    """One `fetch_publication` run against one fake tier.

    `text_bearing_only` defaults to the real default, so a `.jpg` or `.mp4` a test
    hands the tier is refused before it lands -- which is correct, and is why the two
    tests that need a media entry or a stored figure turn it off rather than
    wondering where their fixture went.
    """
    monkeypatch.setattr(fetcher, "resolve_identifiers",
                        lambda *a, **k: Identifiers(doi=DOI, doi_raw=DOI, title=TITLE))
    monkeypatch.setattr(fetcher, "build_http", lambda cfg: object())
    monkeypatch.setattr(fetcher, "build_sources",
                        lambda names, http, cfg: [_FakeTier(supplements, with_pdf,
                                                            suppl_status, media,
                                                            landing)])
    config = {"fetch": {"corpus_dir": str(tmp_path / "corpus"), "tiers": ["fake"],
                        "contact_email": None, "max_corpus_gb": None,
                        "text_bearing_only": text_bearing_only}}
    return fetcher.fetch_publication(DOI, config, force=force)


def _files_on_disk(directory) -> list:
    group = Path(directory) / store.SUPPLEMENT_DIR
    return sorted(p.name for p in group.iterdir()) if group.is_dir() else []


def test_a_fetch_stores_what_the_tier_returned(monkeypatch, tmp_path):
    """The baseline the two tests below are deltas from -- if this shape is wrong,
    they prove nothing."""
    record = _fetch(monkeypatch, tmp_path,
                    [("media-1.xlsx", TABLE_A), ("media-2.xlsx", TABLE_B)])
    directory = Path(record["_directory"])

    assert record["status"] == "complete"
    assert _files_on_disk(directory) == ["01_media-1.xlsx", "02_media-2.xlsx"]
    assert unreferenced_files(directory, record) == []


def test_a_refetch_that_renumbers_leaves_nothing_behind(monkeypatch, tmp_path):
    """The 1.37 GB, reproduced and then swept.

    The same two supplements come back in the opposite order, which is all it takes:
    `_write_group` numbers from `enumerate()`, so the set is written as
    `01_media-2`/`02_media-1` while the previous `01_media-1`/`02_media-2` stay on
    disk under names nothing now references. Four files against two referenced, from a
    re-fetch that lost nothing at all -- the measured corpus reached 27-against-15 on
    `10.64898/2026.02.15.704933` this way over four runs, holding `media-7.xlsx` four
    times over.

    Every abandoned file here is byte-identical to one the new record names, so the
    sweep takes all of them and the article is left exactly as clean as a first fetch.
    """
    first = _fetch(monkeypatch, tmp_path, [("media-1.xlsx", TABLE_A),
                                           ("media-2.xlsx", TABLE_B)])
    directory = Path(first["_directory"])
    assert _files_on_disk(directory) == ["01_media-1.xlsx", "02_media-2.xlsx"]

    second = _fetch(monkeypatch, tmp_path, [("media-2.xlsx", TABLE_B),
                                            ("media-1.xlsx", TABLE_A)], force=True)

    assert _files_on_disk(directory) == ["01_media-2.xlsx", "02_media-1.xlsx"]
    assert unreferenced_files(directory, second) == []
    assert second["orphans_swept"] == {"files": 2,
                                       "bytes": len(TABLE_A) + len(TABLE_B)}
    assert "orphans_kept" not in second
    assert store.manifest_is_complete(second) is True


def test_a_refetch_that_returns_less_keeps_what_it_did_not_replace(
        monkeypatch, tmp_path):
    """**The 49 MB regression, at the level where it actually happened.**

    `10.1126/science.aax6234` had seven referenced supplements at 58.4 MB. A `--force`
    re-fetch returned three at 9.4 MB -- its own manifest still saying "5 of 8
    supplementary file(s) listed on the page could not be fetched" and "the browser
    tier is required for them" -- so the smaller list won, four entries left the
    record, and the sweep deleted their bytes. `10.1038/s41588-025-02454-1` lost two
    files the same way in the same batch.

    The preservation branch does not reach this: it fires only when a re-fetch returns
    *nothing*. Returning *less* is a different case and the record cannot tell which
    of the two happened -- so the bytes stay, and `problems` says the set shrank.
    """
    first = _fetch(monkeypatch, tmp_path, [("media-1.xlsx", TABLE_A),
                                           ("media-2.xlsx", TABLE_B),
                                           ("media-3.xlsx", TABLE_C)])
    directory = Path(first["_directory"])

    second = _fetch(monkeypatch, tmp_path, [("media-3.xlsx", TABLE_C)], force=True)

    assert [e["path"] for e in second["supplementary"]] == \
        ["supplementary/01_media-3.xlsx"]
    assert _files_on_disk(directory) == [
        "01_media-1.xlsx", "01_media-3.xlsx", "02_media-2.xlsx"], \
        "the two the re-fetch did not replace are still there"
    assert second["orphans_swept"] == {"files": 1, "bytes": len(TABLE_C)}, \
        "only the renumbered copy of media-3 was redundant"
    assert second["orphans_kept"] == {"files": 2,
                                     "bytes": len(TABLE_A) + len(TABLE_B)}
    assert any("stored nowhere else" in p and "smaller supplement set" in p
               for p in second["problems"])
    assert store.manifest_is_complete(second) is True, \
        "the kept files have no entries, so the record is still true"


def test_a_refetch_that_fails_keeps_the_previous_set_and_stays_complete(
        monkeypatch, tmp_path):
    """**The trap this whole ordering exists for.**

    `fetcher.py:727` computes `existing_supplementary_ok`, and `:737`/`:773` keep the
    *existing* entry list when a re-fetch came away empty-handed. The referenced set
    is then the previous numbering -- so a sweep computed from what this run wrote,
    which is `[]`, would delete every supplement of exactly the articles that branch
    exists to protect. `--force` against a dead proxy session is the ordinary way to
    reach it.
    """
    first = _fetch(monkeypatch, tmp_path, [("media-1.xlsx", TABLE_A),
                                           ("media-2.xlsx", TABLE_B)])
    directory = Path(first["_directory"])

    second = _fetch(monkeypatch, tmp_path, [], with_pdf=False, suppl_status=None,
                    force=True)

    assert _files_on_disk(directory) == ["01_media-1.xlsx", "02_media-2.xlsx"], \
        "the previous set is the referenced set, and must survive"
    assert [e["path"] for e in second["supplementary"]] == [
        "supplementary/01_media-1.xlsx", "supplementary/02_media-2.xlsx"]
    assert "orphans_swept" not in second
    assert store.manifest_is_complete(second) is True
    assert second["status"] == "complete"


def test_a_refetch_that_keeps_a_swept_record_sweeps_nothing(monkeypatch, tmp_path):
    """The same branch, one policy later. `drop-media` has already deleted the files
    and dropped their `path` keys, so `_entry_accounted_for` accepts the set on its
    markers alone -- and a sweep must not read those path-less entries as licence to
    delete whatever is left in the directory."""
    first = _fetch(monkeypatch, tmp_path, [("media-1.xlsx", TABLE_A),
                                           ("fig1.jpg", b"\xff\xd8\xff\xe0 figure")],
                   text_bearing_only=False)
    directory = Path(first["_directory"])
    record = _record(directory)
    store.mark_entry_removed(record["supplementary"][1], "image")
    (directory / "supplementary/02_fig1.jpg").unlink()
    store.write_manifest(directory, record)

    second = _fetch(monkeypatch, tmp_path, [], with_pdf=False, suppl_status=None,
                    force=True)

    assert _files_on_disk(directory) == ["01_media-1.xlsx"]
    assert store.manifest_is_complete(second) is True


def test_the_manifest_is_written_before_the_sweep(monkeypatch, tmp_path):
    """Order matters, and this is the half that cannot be recovered from.

    A crash between the manifest and the sweep leaves unreferenced files -- which is
    what `drop-orphans` is for. A crash the other way leaves files deleted while the
    record still names them, which is `manifest_is_complete` False and every later
    batch re-fetching the whole article. `store.mark_entry_removed`'s docstring is
    the same argument from the other direction.
    """
    first = _fetch(monkeypatch, tmp_path, [("media-1.xlsx", TABLE_A),
                                           ("media-2.xlsx", TABLE_B)])
    directory = Path(first["_directory"])

    def explode(directory, record):
        raise KeyboardInterrupt("killed between the manifest and the sweep")

    monkeypatch.setattr(orphans, "sweep_article", explode)
    with pytest.raises(KeyboardInterrupt):
        _fetch(monkeypatch, tmp_path, [("media-3.xlsx", TABLE_C)], force=True)

    on_disk = store.read_manifest(directory)
    on_disk["_directory"] = str(directory)
    assert store.manifest_is_complete(on_disk) is True, \
        "the record on disk names only files that are there"
    assert [e["path"] for e in on_disk["supplementary"]] == \
        ["supplementary/01_media-3.xlsx"]
    assert unreferenced_files(directory, on_disk) == [
        {"path": "supplementary/01_media-1.xlsx", "bytes": len(TABLE_A)},
        {"path": "supplementary/02_media-2.xlsx", "bytes": len(TABLE_B)}], \
        "the leftovers survive the crash, for the next sweep to take"


def test_a_sweep_that_cannot_delete_records_it_and_finishes(monkeypatch, tmp_path):
    """The manifest is already written and correct, so a stale file that could not be
    unlinked is a note in `problems` rather than a failed fetch."""
    _skip_if_root()
    first = _fetch(monkeypatch, tmp_path, [("media-1.xlsx", TABLE_A),
                                           ("media-2.xlsx", TABLE_B)])
    directory = Path(first["_directory"])

    real = orphans.sweep_article

    def locked(target, record):
        (Path(target) / store.SUPPLEMENT_DIR).chmod(0o500)
        try:
            return real(target, record)
        finally:
            (Path(target) / store.SUPPLEMENT_DIR).chmod(0o700)

    monkeypatch.setattr(orphans, "sweep_article", locked)
    # `media-2` again, so the abandoned `02_media-2.xlsx` is byte-identical to the new
    # `01_media-2.xlsx` and the sweep genuinely attempts the unlink. Handing back
    # `media-1` instead would classify the leftover as bytes stored nowhere else, and
    # a file that is kept on purpose cannot exercise a refusal.
    second = _fetch(monkeypatch, tmp_path, [("media-2.xlsx", TABLE_B)], force=True)

    assert second["status"] == "complete"
    assert any("could not remove the unreferenced file" in p
               for p in second["problems"])
    assert "supplementary/02_media-2.xlsx" in json.dumps(second["problems"])
    assert (directory / "supplementary/02_media-2.xlsx").exists(), \
        "the file the sweep could not take is still there for the next pass"
    assert store.manifest_is_complete(second) is True, \
        "and no record claims it is gone, so no batch re-fetches the article"
