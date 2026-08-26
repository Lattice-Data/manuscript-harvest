"""Fetching only what text can come out of, and removing what already came in.

Two halves of one policy. `manuscript_harvest/text_bearing.py` answers "can text be
extracted from a file with this name?" for the fetch stage, and
`manuscript-fetch drop-media` asks the same question of the 2428 image, audio and
video files already on disk -- 47% of every supplementary entry in this corpus.

The test that matters most in this file is
`test_a_swept_article_is_still_complete`. `store.manifest_is_complete` walks every
supplementary entry and calls the article incomplete when one names a file that is
not there, so a removal that kept its `path` key would make every later batch
re-fetch all 138 affected articles, re-download the figures, and undo the sweep --
forever. Everything else here is about not lying: what was removed stays in the
record, and a policy removal must not borrow the signal that means "this manifest
is malformed".
"""

import json
from pathlib import Path

import pytest

from manuscript_harvest import text_bearing
from manuscript_harvest.extract.extractor import (
    MANIFEST_ENTRY_WITHOUT_PATH,
    extract_article,
)
from manuscript_harvest.extract.limits import Limits
from manuscript_harvest.fetch import store
from manuscript_harvest.fetch.drop_media import (
    drop_media_article,
    drop_media_corpus,
    human_reasons,
    summarize,
)
from tests.fakes import DOI, jats_article, make_article, make_pdf, make_xlsx

L = Limits()

#: Long enough to clear `Limits.min_main_text_chars`, so the extraction under test is
#: `complete` on its own merits rather than carrying `main_text_thin` -- which would
#: hide whether a policy removal moved the status.
METHODS_BODY = (
    '<sec sec-type="methods"><title>Methods</title><p>'
    + "Islets from eight-week-old male C57BL/6 mice were dissociated and loaded "
      "on a 10x Chromium controller with the Single Cell 3' v3 kit. " * 25
    + '</p></sec>'
)

JPEG = b"\xff\xd8\xff\xe0" + b"figure bytes" * 4
MP4 = b"\x00\x00\x00 ftypisom" + b"movie bytes" * 4
TABLE = make_xlsx({"S1": [["gene", "log2fc"], ["TP53", 1.4]]})


def _article(tmp_path, supplements=(), **kwargs):
    """One corpus article, with `_directory` filled in as the fetcher fills it.

    `manifest_is_complete` reads that key to check the files an entry names are
    really there, and it is injected at run time rather than stored -- so a test
    that omits it gets the defensive "complete on the record alone" branch and
    proves nothing about the paths.
    """
    kwargs.setdefault("fulltext", make_pdf())
    directory = make_article(tmp_path / store.doi_slug(DOI),
                            supplements=list(supplements), **kwargs)
    return directory


def _record(directory) -> dict:
    record = store.read_manifest(directory)
    record["_directory"] = str(directory)
    return record


# -- the predicate -----------------------------------------------------------

def test_every_extension_the_policy_keeps_is_fetchable():
    """`KEPT_EXTENSIONS` is documentation of an intent, and this is what makes it a
    constraint: the decision is taken by the *skip* sets, so nothing else would
    notice an extension quietly leaving the keep list."""
    for extension in sorted(text_bearing.KEPT_EXTENSIONS):
        name = f"supplement{extension}"
        assert text_bearing.skip_reason(name) is None, name
        assert text_bearing.text_can_be_extracted(name), name


def test_archives_are_kept_although_no_extension_in_them_is_text():
    """`.zip` alone is 5.05 GB of this corpus's 5.11 GB of archives, and those zips
    are mostly supplementary tables: `extract/extractor.py` unpacks them and reads
    the members, so a zip is a text-bearing file with a lid on."""
    for name in ("tables.zip", "data.tar", "counts.tsv.gz", "bundle.tgz"):
        assert text_bearing.skip_reason(name) is None, name


def test_images_and_audio_video_are_the_two_refusals():
    assert text_bearing.skip_reason("41586_Fig1_HTML.jpg") == text_bearing.SKIP_IMAGE
    assert text_bearing.skip_reason("panel.TIFF") == text_bearing.SKIP_IMAGE
    assert text_bearing.skip_reason("movie1.mp4") == text_bearing.SKIP_AUDIO_VIDEO
    assert text_bearing.skip_reason("audio-s1.WAV") == text_bearing.SKIP_AUDIO_VIDEO
    assert text_bearing.text_can_be_extracted("movie1.mp4") is False


def test_svg_is_refused_although_it_is_xml_on_the_wire():
    """The predicate has to agree with the parser it protects. `extract/extractor.py`
    dispatches `.svg` to `image_no_text` -- it has no SVG parser -- so fetching one
    buys a file whose only trace downstream is that word."""
    assert text_bearing.skip_reason("scheme.svg") == text_bearing.SKIP_IMAGE
    assert ".svg" not in text_bearing.KEPT_EXTENSIONS


@pytest.mark.parametrize("name", [
    "01_url",                       # the browser tier's extensionless saves
    "supplementary_material",
    "S1_Data",
    "mmc1.newformat",
])
def test_a_name_the_predicate_cannot_read_is_kept(name):
    """13 supplements here were saved by the browser tier as `NN_url` with no
    extension at all, several of them real PDFs and spreadsheets. Unknown means
    unknown: the skip sets are closed and the keep side is open, so a format a
    publisher adopts tomorrow arrives rather than being silently refused."""
    assert text_bearing.skip_reason(name) is None


def test_a_url_is_read_as_readily_as_a_filename():
    """At fetch time the only name a tier has is often an anchor href or an S3 key.
    Without cutting the query first, `media-1.mp4?download=true` has the extension
    `.mp4?download=true` and every skip silently misses."""
    assert text_bearing.extension(
        "https://host/content/DC1/embed/media-1.mp4?download=true") == ".mp4"
    assert text_bearing.skip_reason(
        "https://host/f1.JPG#fig1") == text_bearing.SKIP_IMAGE
    assert text_bearing.extension("PMC8941949.1/NIHMS-supplement-10.xlsx") == ".xlsx"
    assert text_bearing.extension("no-dots-at-all") == ""


def test_the_policy_is_on_unless_a_config_says_otherwise():
    """Default here as well as in `cli.DEFAULT_FETCH_CONFIG`, because a `fetch`
    mapping reaches the tiers from places that never pass through the CLI."""
    assert text_bearing.policy_is_on(None) is True
    assert text_bearing.policy_is_on({}) is True
    assert text_bearing.policy_is_on({"text_bearing_only": False}) is False


# -- drop-media: reporting ---------------------------------------------------

def test_reporting_names_the_files_and_touches_nothing(tmp_path):
    directory = _article(tmp_path, supplements=[
        ("table_s1.xlsx", TABLE), ("fig1.jpg", JPEG), ("movie1.mp4", MP4)])

    report = drop_media_article(directory)

    assert [f["path"] for f in report["files"]] == [
        "supplementary/02_fig1.jpg", "supplementary/03_movie1.mp4"]
    assert [f["reason"] for f in report["files"]] == ["image", "audio_video"]
    assert report["bytes"] == len(JPEG) + len(MP4)
    assert report["removed"] is False
    assert (directory / "supplementary/02_fig1.jpg").exists()
    assert store.read_manifest(directory)["supplementary"][1]["path"] == \
        "supplementary/02_fig1.jpg", "a report must not rewrite the manifest"


def test_an_article_of_readable_supplements_has_nothing_to_report(tmp_path):
    directory = _article(tmp_path, supplements=[("table_s1.xlsx", TABLE),
                                                ("notes.pdf", make_pdf(pages=1))])
    assert drop_media_article(directory)["files"] == []


# -- drop-media: applying ----------------------------------------------------

def test_applying_deletes_the_bytes_and_keeps_the_record(tmp_path):
    directory = _article(tmp_path, supplements=[
        ("table_s1.xlsx", TABLE), ("fig1.jpg", JPEG)])
    before = store.read_manifest(directory)["supplementary"][1]

    report = drop_media_article(directory, apply=True)

    assert report["removed"] is True and report["bytes"] == len(JPEG)
    assert not (directory / "supplementary/02_fig1.jpg").exists()
    assert (directory / "supplementary/01_table_s1.xlsx").exists()

    entry = store.read_manifest(directory)["supplementary"][1]
    assert "path" not in entry, "the path key is what makes the article incomplete"
    assert entry["name"] == "supplementary/02_fig1.jpg"
    assert entry["bytes"] == before["bytes"] and entry["sha256"] == before["sha256"]
    assert entry["removed"] == store.NOT_TEXT_BEARING
    assert entry["removed_reason"] == "image" and entry["removed_at"]
    assert entry["original_name"] == "fig1.jpg", "the publisher's own name survives"


def test_a_swept_article_is_still_complete(tmp_path):
    """The regression this whole shape exists for.

    `manifest_is_complete` returns False when any supplementary entry names a path
    whose file is gone, and nothing downstream distinguishes that from a half-fetched
    article. A removal that kept its path would therefore make every later batch
    re-fetch all 138 articles that hold a figure, re-download the bytes, and remove
    them again -- the sweep undoing itself on a loop.
    """
    directory = _article(tmp_path, supplements=[
        ("table_s1.xlsx", TABLE), ("fig1.jpg", JPEG), ("movie1.mp4", MP4)])
    assert store.manifest_is_complete(_record(directory)) is True

    drop_media_article(directory, apply=True)

    assert store.manifest_is_complete(_record(directory)) is True
    record = _record(directory)
    assert record["status"] == "complete", "and the top-level word is untouched"
    assert len(record["supplementary"]) == 3, "nothing was forgotten, only deleted"


def test_the_supplement_verdict_is_left_alone(tmp_path):
    """`supplementary_status` is a claim about the supplementary files text can be
    extracted from, and this command removes none of those. Rewriting it for an
    article whose remaining supplements are a spreadsheet would be false, and
    rewriting only the all-media articles would make the status depend on when the
    sweep happened to run."""
    directory = _article(tmp_path, supplements=[("fig1.jpg", JPEG)])
    record = store.read_manifest(directory)
    record["supplementary_status"] = "fetched"
    store.write_manifest(directory, record)

    drop_media_article(directory, apply=True)

    assert store.read_manifest(directory)["supplementary_status"] == "fetched"


def test_a_second_sweep_has_nothing_to_do(tmp_path):
    directory = _article(tmp_path, supplements=[("fig1.jpg", JPEG)])
    first = drop_media_article(directory, apply=True)
    manifest = (directory / store.MANIFEST_NAME).read_text()

    again = drop_media_article(directory, apply=True)

    assert first["files"] and again["files"] == []
    assert again["bytes"] == 0 and again["removed"] is False
    assert (directory / store.MANIFEST_NAME).read_text() == manifest, \
        "an idempotent pass does not even rewrite the manifest"


def test_emptying_the_directory_removes_it(tmp_path):
    """An empty `supplementary/` reads as an article whose supplements were never
    fetched, which is the one thing this corpus's status taxonomy exists to keep
    apart from an article that has none."""
    directory = _article(tmp_path, supplements=[("fig1.jpg", JPEG), ("f2.png", JPEG)])

    report = drop_media_article(directory, apply=True)

    assert report["emptied_dirs"] == [store.SUPPLEMENT_DIR]
    assert not (directory / store.SUPPLEMENT_DIR).exists()


def test_a_directory_that_still_holds_a_file_stays(tmp_path):
    directory = _article(tmp_path, supplements=[("table_s1.xlsx", TABLE),
                                                ("fig1.jpg", JPEG)])
    assert drop_media_article(directory, apply=True)["emptied_dirs"] == []
    assert (directory / store.SUPPLEMENT_DIR).is_dir()


def test_the_article_itself_is_never_removed(tmp_path):
    """Only the `supplementary` and `media` entry lists are walked, which is the
    structural half; a sweep beside the article's own four files leaves all four."""
    directory = _article(tmp_path, supplements=[("fig1.jpg", JPEG)],
                         xml=b"<article><body/></article>", landing=b"<html></html>")

    drop_media_article(directory, apply=True)

    assert (directory / store.FULLTEXT_PDF).exists()
    assert (directory / store.FULLTEXT_XML).exists()
    assert (directory / store.LANDING_HTML).exists()
    assert (directory / store.MANIFEST_NAME).exists()


def test_the_articles_own_files_are_refused_by_name_as_well(tmp_path):
    """The guard behind that one, pinned directly because nothing can reach it:
    all four protected names carry text-bearing extensions, so the predicate never
    nominates one. It is kept for the day a name joins `_NEVER_REMOVED` or an
    extension joins the skip sets -- and the cost of that mistake is the article, so
    a test says what it does rather than a comment claiming it.

    `mark_entry_removed` is the second line and runs *after* the unlink;
    `path_is_protected` is what `_removable` asks before it.
    """
    for name in (store.FULLTEXT_PDF, store.FULLTEXT_XML, store.LANDING_HTML,
                 store.MANIFEST_NAME):
        assert store.path_is_protected(name) is True
        assert store.path_is_protected(f"supplementary/{name}") is True, \
            "the basename is matched too, and a stored supplement is always prefixed"
        entry = {"path": name, "bytes": 1, "sha256": "x"}
        assert store.mark_entry_removed(entry, "image") is None
        assert entry["path"] == name and "removed" not in entry

    assert store.path_is_protected("supplementary/01_fig1.jpg") is False
    assert store.path_is_protected(None) is True, "nothing there to remove"


def test_article_media_is_swept_too(tmp_path):
    """`media/` holds the article's own figures, and with the policy on every
    extension that routes there is an image extension -- so sweeping
    `supplementary/` and leaving `media/` full would be the same corpus with a
    tidier index."""
    directory = _article(tmp_path)
    record = store.read_manifest(directory)
    target = directory / store.MEDIA_DIR / "01_f0001.jpg"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(JPEG)
    record["media"] = [{"path": "media/01_f0001.jpg", "bytes": len(JPEG),
                        "sha256": store.sha256_bytes(JPEG), "index": 1,
                        "original_name": "f0001.jpg"}]
    store.write_manifest(directory, record)

    report = drop_media_article(directory, apply=True)

    assert [f["path"] for f in report["files"]] == ["media/01_f0001.jpg"]
    assert not (directory / store.MEDIA_DIR).exists()
    assert store.read_manifest(directory)["media"][0]["removed"] == \
        store.NOT_TEXT_BEARING


def test_an_evicted_article_is_left_to_its_own_record(tmp_path):
    """The budget sweep already took every byte and its manifest records what was
    there, paths included. Rewriting those entries would overwrite one removal's
    record with another's."""
    directory = _article(tmp_path, supplements=[("fig1.jpg", JPEG)])
    store.evict_article(directory)

    report = drop_media_article(directory, apply=True)

    assert report["files"] == [] and "evicted" in report["note"]
    assert store.read_manifest(directory)["supplementary"][0]["evicted"] is True


def test_a_swept_entry_is_not_also_marked_evicted(tmp_path):
    """The same two removals in the other order, which is the order the corpus takes.

    `drop_media_article` never touches `record["status"]`, so a swept article stays
    `complete` and remains an ordinary budget candidate -- the guard in
    `store.evict_article` is on the live path, not a defensive one. Without it the
    figure's entry carries `removed: not_text_bearing` *and* `evicted: true`: two
    removals' names on one file, and the file implied under `evicted_bytes`, which
    counts only what that eviction actually freed. `evict_article`'s own docstring is
    that a corpus which forgets what it deleted is worse than one that never had it,
    and remembering it twice is the same kind of wrong.

    Driven through `enforce_budget` rather than by calling `evict_article` directly,
    because the claim is that the budget pass reaches a swept article at all.
    `manifest_is_complete` is not the assertion: it short-circuits on
    `status: evicted` and answers True either way.
    """
    corpus = tmp_path / "corpus"
    make_article(corpus / "01-old", fulltext=make_pdf(),
                 supplements=[("table_s1.xlsx", TABLE), ("fig1.jpg", JPEG)])
    make_article(corpus / "02-new", fulltext=make_pdf())
    assert summarize(drop_media_corpus(corpus, apply=True))["files"] == 1

    outcome = store.enforce_budget(corpus, max_bytes=store.article_size(
        corpus / "02-new") + 1)

    assert [item["slug"] for item in outcome["evicted"]] == ["01-old"]
    table, figure = store.read_manifest(corpus / "01-old")["supplementary"]
    assert table["evicted"] is True, "the eviction really did run over this article"
    assert figure["removed"] == store.NOT_TEXT_BEARING
    assert "evicted" not in figure, "the sweep already took it, and said so"
    assert figure["name"] == "supplementary/02_fig1.jpg" and figure["bytes"] == len(JPEG)


def test_a_file_that_is_already_missing_is_not_marked_removed(tmp_path):
    """A missing file with a live `path` is the signal that makes the next batch
    re-fetch this article. Marking it removed here would silence that, over bytes
    this command never deleted."""
    directory = _article(tmp_path, supplements=[("fig1.jpg", JPEG)])
    (directory / "supplementary/01_fig1.jpg").unlink()

    assert drop_media_article(directory, apply=True)["files"] == []
    assert store.read_manifest(directory)["supplementary"][0]["path"] == \
        "supplementary/01_fig1.jpg"
    assert store.manifest_is_complete(_record(directory)) is False


# -- drop-media: the corpus pass --------------------------------------------

def test_a_corpus_pass_reports_every_article_and_sweeps_only_the_ones_with_media(tmp_path):
    corpus = tmp_path / "corpus"
    make_article(corpus / "with-figures", fulltext=make_pdf(),
                 supplements=[("table_s1.xlsx", TABLE), ("fig1.jpg", JPEG)])
    make_article(corpus / "text-only", fulltext=make_pdf(),
                 supplements=[("table_s1.xlsx", TABLE)])

    reports = drop_media_corpus(corpus, apply=True)
    totals = summarize(reports)

    assert totals == {"articles": 2, "affected": 1, "files": 1,
                      "bytes": len(JPEG), "by_reason": {"image": 1}}
    assert human_reasons(totals["by_reason"]) == "1 image"
    assert human_reasons({}) is None
    assert not (corpus / "with-figures/supplementary/02_fig1.jpg").exists()
    assert (corpus / "text-only/supplementary/01_table_s1.xlsx").exists()


def test_only_the_named_slugs_are_swept(tmp_path):
    corpus = tmp_path / "corpus"
    for name in ("one", "two"):
        make_article(corpus / name, fulltext=make_pdf(),
                     supplements=[("fig1.jpg", JPEG)])

    reports = drop_media_corpus(corpus, apply=True, slugs=["one"])

    assert [r["slug"] for r in reports] == ["one"]
    assert (corpus / "two/supplementary/01_fig1.jpg").exists()


def test_a_corpus_that_is_not_there_is_not_an_error(tmp_path):
    assert drop_media_corpus(tmp_path / "nothing") == []


# -- what the extraction stage makes of a swept article ---------------------

def test_a_swept_article_extracts_complete_and_without_the_malformed_caveat(tmp_path):
    """TRAP 2. `MANIFEST_ENTRY_WITHOUT_PATH` means "this manifest is malformed", and
    a policy removal is the opposite of that -- so it must not raise it, or the
    caveat would come to mean "malformed, or perfectly fine" on 138 articles and
    stop being worth reading.

    No caveat of its own either: every file the sweep takes is one this stage would
    have dispatched to `image_no_text`, which is benign and yields no block, so the
    blocks are identical before and after. The record says what went, in
    `removed_not_text_bearing`.
    """
    directory = _article(tmp_path, xml=jats_article(METHODS_BODY),
                         supplements=[("table_s1.xlsx", TABLE), ("fig1.jpg", JPEG)])
    before = extract_article(directory, limits=L, write_markdown=False)
    blocks_before = (directory / "extracted/blocks.jsonl").read_text()

    drop_media_article(directory, apply=True)
    after = extract_article(directory, limits=L, force=True, write_markdown=False)

    assert MANIFEST_ENTRY_WITHOUT_PATH not in after["caveats"]
    assert after["caveats"] == before["caveats"]
    assert after["status"] == before["status"] == "complete"
    assert after["removed_not_text_bearing"] == ["supplementary/02_fig1.jpg"]
    assert (directory / "extracted/blocks.jsonl").read_text() == blocks_before, \
        "a removal that changes a block is not a removal of something unreadable"


def test_an_entry_with_no_path_and_no_marker_is_still_a_malformed_manifest(tmp_path):
    """The other half of TRAP 2: the caveat has to keep firing on the case it was
    written for, or teaching the extractor about removals would just have deleted a
    signal."""
    directory = _article(tmp_path, xml=jats_article(METHODS_BODY),
                         supplements=[("table_s1.xlsx", TABLE)])
    record = store.read_manifest(directory)
    record["supplementary"].append({"index": 2, "original_name": "lost.xlsx"})
    store.write_manifest(directory, record)

    extraction = extract_article(directory, limits=L, write_markdown=False)

    assert MANIFEST_ENTRY_WITHOUT_PATH in extraction["caveats"]
    assert extraction["removed_not_text_bearing"] == []


def test_the_cli_reports_then_removes(tmp_path, capsys):
    """`revalidate`'s shape: report by default, delete only with `--apply`. Deleting
    bytes should be a decision rather than a side effect of looking."""
    from manuscript_harvest.fetch.cli import main

    corpus = tmp_path / "corpus"
    make_article(corpus / "art", fulltext=make_pdf(),
                 supplements=[("table_s1.xlsx", TABLE), ("fig1.jpg", JPEG)])
    config = tmp_path / "config.yaml"
    config.write_text(json.dumps({"fetch": {"corpus_dir": str(corpus)}}))

    assert main(["--config", str(config), "drop-media"]) == 0
    reported = capsys.readouterr()
    assert "would remove art: 1 file(s)" in reported.out
    assert "1 with files no text can be extracted from" in reported.err
    assert "--apply" in reported.err
    assert (corpus / "art/supplementary/02_fig1.jpg").exists()

    assert main(["--config", str(config), "drop-media", "--apply"]) == 0
    applied = capsys.readouterr()
    assert "removed art: 1 file(s)" in applied.out
    assert not (corpus / "art/supplementary/02_fig1.jpg").exists()


def test_a_sweep_the_filesystem_refuses_says_so_instead_of_reporting_a_clean_corpus(
        tmp_path, capsys):
    """The one way this command can fail, and the way it used to fail silently.

    `drop_media_article` rewrites `files` to what it actually deleted, so an article
    where every `unlink` raised comes back with `files == []` -- indistinguishable, to
    a loop keyed on that list, from an article with nothing to sweep. The closing
    totals then read `0 with files no text can be extracted from` over a corpus the
    report-only pass had just said held one, the `failed` list the module builds was
    discarded, and the exit code was 0. An operator sweeping a read-only mount or a
    corpus owned by another account was told it was clean.

    `unlink` needs write permission on the *containing* directory, so one unwritable
    `supplementary/` fails every candidate in that article -- there is no partial
    outcome to fall back on. The bytes and the manifest are untouched either way,
    which is the safe direction; what is under test is that the report says so.
    """
    import os

    from manuscript_harvest.fetch.cli import main

    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root ignores the directory mode this needs")

    corpus = tmp_path / "corpus"
    make_article(corpus / "art", fulltext=make_pdf(),
                 supplements=[("fig1.jpg", JPEG)])
    config = tmp_path / "config.yaml"
    config.write_text(json.dumps({"fetch": {"corpus_dir": str(corpus)}}))
    locked = corpus / "art" / store.SUPPLEMENT_DIR
    locked.chmod(0o500)
    try:
        code = main(["--config", str(config), "drop-media", "--apply"])
        printed = capsys.readouterr()
    finally:
        locked.chmod(0o700)

    assert code == 1, "exit 0 would tell a script the sweep finished"
    assert "01_fig1.jpg not deleted" in printed.err, "name the file and the errno"
    assert "Permission denied" in printed.err
    assert "still on disk" in printed.err
    assert (locked / "01_fig1.jpg").exists()

    entry, = _record(corpus / "art")["supplementary"]
    assert entry["path"] == "supplementary/01_fig1.jpg", \
        "a record saying `removed` over bytes on disk is the one worse outcome"
    assert "removed" not in entry


# -- drop-media: surviving an interrupt --------------------------------------
#
# The sweep deletes files one at a time. Where it persists the manifest decides what
# an interrupt leaves behind, and getting that wrong is not a cosmetic bug: a file
# deleted while its entry still names it makes `manifest_is_complete` false, so every
# later batch re-fetches the article and re-downloads the media, and the sweep undoes
# itself on a loop. These pin the property rather than the implementation -- whatever
# the write strategy, an interrupt must leave the record and the disk agreeing.

def _orphans(directory) -> list:
    """Entries still naming a supplementary file that is no longer on disk."""
    record = store.read_manifest(directory)
    return [entry["path"] for entry in record["supplementary"]
            if entry.get("path") and not (directory / entry["path"]).exists()]


def _sweep_interrupted_on_third_unlink(monkeypatch, directory, error):
    """Run `--apply`, blowing up on the third `unlink` the way a Ctrl-C would."""
    real_unlink = Path.unlink
    calls = {"n": 0}

    def flaky(self, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 3:
            raise error("interrupted mid-sweep")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", flaky)
    try:
        drop_media_article(directory, apply=True)
    except BaseException as raised:      # noqa: BLE001 -- the point is that it escapes
        assert isinstance(raised, error)
    return calls["n"] - 1


@pytest.mark.parametrize("error", [KeyboardInterrupt, SystemExit])
def test_an_interrupted_sweep_leaves_no_entry_naming_a_deleted_file(tmp_path, monkeypatch,
                                                                   error):
    """The regression. Measured before the fix: two files gone, two entries still
    naming them, `manifest_is_complete` False, and a second `--apply` unable to
    repair it because `_removable` skips an entry whose file is already missing."""
    directory = _article(tmp_path, supplements=[
        ("fig1.jpg", JPEG), ("fig2.jpg", JPEG), ("movie1.mp4", MP4)])
    assert store.manifest_is_complete(_record(directory)) is True

    deleted = _sweep_interrupted_on_third_unlink(monkeypatch, directory, error)

    assert deleted, "the probe has to actually delete something to be a test"
    assert _orphans(directory) == [], "a deleted file is never left with a live path"
    assert store.manifest_is_complete(_record(directory)) is True, \
        "so no later batch re-fetches this article and undoes the sweep"


def test_an_interrupted_sweep_leaves_a_manifest_that_still_parses(tmp_path, monkeypatch):
    """`read_manifest` answers None for a manifest that will not parse, at which point
    the article reads as never fetched -- so a truncated write costs the whole record,
    not one entry. Writing per file multiplies the chances, which is why the write is
    atomic."""
    directory = _article(tmp_path, supplements=[
        ("fig1.jpg", JPEG), ("fig2.jpg", JPEG), ("movie1.mp4", MP4)])

    _sweep_interrupted_on_third_unlink(monkeypatch, directory, KeyboardInterrupt)

    json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert store.read_manifest(directory) is not None
    assert (directory / "manifest.json.tmp").exists() is False, "no staging left behind"


def test_a_resumed_sweep_finishes_what_the_interrupted_one_started(tmp_path, monkeypatch):
    """Convergence: whatever an interrupt left, running it again completes the job."""
    directory = _article(tmp_path, supplements=[
        ("table_s1.xlsx", TABLE), ("fig1.jpg", JPEG), ("fig2.jpg", JPEG),
        ("movie1.mp4", MP4)])

    _sweep_interrupted_on_third_unlink(monkeypatch, directory, KeyboardInterrupt)
    drop_media_article(directory, apply=True)

    assert _orphans(directory) == []
    assert store.manifest_is_complete(_record(directory)) is True
    kept = [entry for entry in store.read_manifest(directory)["supplementary"]
            if entry.get("path")]
    assert [entry["path"] for entry in kept] == ["supplementary/01_table_s1.xlsx"], \
        "the spreadsheet survives and every figure and movie is gone"
