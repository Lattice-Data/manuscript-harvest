"""Offline tests for the comparison against hand-fetched papers.

The comparison itself needs the publisher's bytes and a network; these tests need
neither. They pin the rules that decide what counts as a match, against synthetic
files, so that changing a rule fails here rather than surprising someone the next
time `verify` runs against real papers.

The filenames are the real ones from the first three papers -- Nature's MOESM series,
Elsevier's mmc and PII conventions, Science's zip of tables -- because those
conventions are the whole reason the classifier is not a one-liner.
"""

import hashlib
import zipfile

import pytest

from manuscript_harvest.fetch import manual_fetch, store

from .fakes import make_pdf

DOI = "10.1126/science.adt8307"


def _zip(path, entries):
    with zipfile.ZipFile(path, "w") as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return path


def _article_dir(tmp_path, pdf=b"", supplements=None):
    """A fetched article on disk, shaped the way store.py lays one out."""
    directory = tmp_path / "corpus" / "article"
    (directory / store.SUPPLEMENT_DIR).mkdir(parents=True)
    if pdf:
        (directory / store.FULLTEXT_PDF).write_bytes(pdf)
    for index, (name, payload) in enumerate(sorted((supplements or {}).items()), start=1):
        (directory / store.SUPPLEMENT_DIR / store.supplement_filename(index, name)).write_bytes(payload)
    return directory


def _record(pdf_status="ok", suppl_status="fetched"):
    return {
        "fulltext": {"status": pdf_status, "path": store.FULLTEXT_PDF},
        "supplementary_status": suppl_status,
    }


def _manual_root(tmp_path, source_dir="Science"):
    (tmp_path / source_dir).mkdir(parents=True, exist_ok=True)
    return tmp_path


# -- archives ----------------------------------------------------------------

def test_zip_members_are_listed(tmp_path):
    """Science ships its 28 tables as one zip, so the members have to be reachable."""
    path = _zip(tmp_path / "tables.zip", {"t/Table_S1.tsv": "a\tb\n", "t/Table_S2.tsv": "c\td\n"})
    names = [name for name, _ in manual_fetch.archive_members(path)]
    assert names == ["t/Table_S1.tsv", "t/Table_S2.tsv"]


def test_xlsx_is_not_treated_as_an_archive(tmp_path):
    """A regression pin. Every Office format is a zip, so `is_zipfile` alone recorded
    25 spreadsheet internals as supplement members for one Nature workbook. Worse
    than noise: parts like `[Content_Types].xml` are identical across unrelated
    workbooks, so member matching would call a manual file found on the strength of
    boilerplate belonging to a different one."""
    path = _zip(tmp_path / "MOESM4_ESM.xlsx", {
        "[Content_Types].xml": "<Types/>",
        "xl/worksheets/sheet1.xml": "<worksheet/>",
    })
    assert zipfile.is_zipfile(path), "the fixture must really be a zip for this to mean anything"
    assert manual_fetch.archive_members(path) == []


def test_a_corrupt_archive_reports_no_members_rather_than_raising(tmp_path):
    """A truncated download must degrade, not break the comparison it informs."""
    path = tmp_path / "truncated.zip"
    path.write_bytes(b"PK\x03\x04 and then nothing useful")
    assert manual_fetch.archive_members(path) == []


def test_fingerprint_records_pages_for_pdfs_and_members_for_archives(tmp_path):
    pdf = tmp_path / "article.pdf"
    pdf.write_bytes(make_pdf(pages=4))
    assert manual_fetch.fingerprint(pdf)["pages"] == 4
    assert "members" not in manual_fetch.fingerprint(pdf)

    archive = _zip(tmp_path / "tables.zip", {"a.tsv": "x"})
    assert len(manual_fetch.fingerprint(archive)["members"]) == 1


# -- classifying a download folder -------------------------------------------

def test_nature_article_is_found_by_its_doi(tmp_path):
    for name in ("s41588-025-02433-6.pdf", "41588_2025_2433_MOESM1_ESM.pdf",
                 "41588_2025_2433_MOESM4_ESM.xlsx"):
        (tmp_path / name).write_bytes(b"x")
    main, supplements = manual_fetch.classify(tmp_path, "10.1038/s41588-025-02433-6")
    assert main.name == "s41588-025-02433-6.pdf"
    assert len(supplements) == 2


def test_supplementary_materials_are_not_mistaken_for_the_article(tmp_path):
    """`science.adt8307_sm.pdf` contains the DOI tail but is the supplement, which
    is why the rule is stem equality rather than containment."""
    for name in ("science.adt8307.pdf", "science.adt8307_sm.pdf",
                 "science.adt8307_tables_s1_to_s28.zip"):
        (tmp_path / name).write_bytes(b"x")
    main, supplements = manual_fetch.classify(tmp_path, DOI)
    assert main.name == "science.adt8307.pdf"
    assert sorted(p.name for p in supplements) == [
        "science.adt8307_sm.pdf", "science.adt8307_tables_s1_to_s28.zip"]


def test_elsevier_article_is_found_by_house_style_not_by_doi(tmp_path):
    """Elsevier names the PDF after the PII, so the DOI rule cannot find it."""
    for name in ("1-s2.0-S2666979X26001667-main.pdf", "mmc1.pdf", "mmc12.pdf", "mmc2.xlsx"):
        (tmp_path / name).write_bytes(b"x")
    main, supplements = manual_fetch.classify(tmp_path, "10.1016/j.xgen.2026.101304")
    assert main.name == "1-s2.0-S2666979X26001667-main.pdf"
    assert len(supplements) == 3


def test_an_mmc_file_is_never_promoted_to_the_article(tmp_path):
    """mmc12.pdf is the extended article and opens with the word "Article", but it
    is shipped as a supplementary component and a folder without the real PDF
    should say so rather than promote it."""
    for name in ("mmc1.pdf", "mmc12.pdf"):
        (tmp_path / name).write_bytes(b"x")
    main, supplements = manual_fetch.classify(tmp_path, "10.1016/j.xgen.2026.101304")
    assert main is None
    assert len(supplements) == 2


def test_a_main_hint_overrides_the_rules(tmp_path):
    for name in ("mmc1.pdf", "mmc12.pdf"):
        (tmp_path / name).write_bytes(b"x")
    main, supplements = manual_fetch.classify(tmp_path, "10.1016/j.xgen.2026.101304", main_hint="mmc12.pdf")
    assert main.name == "mmc12.pdf"
    assert [p.name for p in supplements] == ["mmc1.pdf"]


def test_a_missing_hint_is_an_error_not_a_silent_fallback(tmp_path):
    (tmp_path / "mmc1.pdf").write_bytes(b"x")
    with pytest.raises(FileNotFoundError):
        manual_fetch.classify(tmp_path, "10.1016/j.xgen.2026.101304", main_hint="nope.pdf")


def test_build_article_flags_a_folder_with_no_article_pdf(tmp_path):
    (tmp_path / "mmc1.pdf").write_bytes(b"x")
    entry = manual_fetch.build_article("10.1016/j.xgen.2026.101304", tmp_path, "CellGenomics")
    assert entry["main_pdf"] is None
    assert "note" in entry


# -- matching ----------------------------------------------------------------

def test_a_zip_matches_its_own_unpacked_contents(tmp_path):
    """The archive question in both directions: a human saved the zip whole, a tier
    unpacked it, and either has to count as the same 28 tables."""
    archive = _zip(tmp_path / "tables.zip", {"Table_S1.tsv": "a\tb\n", "Table_S2.tsv": "c\td\n"})
    spec_entry = manual_fetch.fingerprint(archive)

    unpacked = tmp_path / "unpacked"
    unpacked.mkdir()
    (unpacked / "Table_S1.tsv").write_text("a\tb\n")
    (unpacked / "Table_S2.tsv").write_text("c\td\n")

    universe = manual_fetch.hash_universe(sorted(unpacked.iterdir()))
    assert manual_fetch._found(spec_entry, universe)


def test_a_partially_unpacked_zip_does_not_count_as_matched(tmp_path):
    archive = _zip(tmp_path / "tables.zip", {"Table_S1.tsv": "a\tb\n", "Table_S2.tsv": "c\td\n"})
    spec_entry = manual_fetch.fingerprint(archive)

    unpacked = tmp_path / "unpacked"
    unpacked.mkdir()
    (unpacked / "Table_S1.tsv").write_text("a\tb\n")

    assert not manual_fetch._found(spec_entry, manual_fetch.hash_universe(sorted(unpacked.iterdir())))


# -- the comparison ----------------------------------------------------------

def test_a_missing_supplement_is_named(tmp_path):
    article = {
        "doi": DOI, "source_dir": "Science",
        "main_pdf": None,
        "supplements": [{"file": "science.adt8307_sm.pdf",
                         "sha256": hashlib.sha256(b"sm").hexdigest()}],
        "expect": {"supplementary_status": "fetched"},
    }
    directory = _article_dir(tmp_path, supplements={"other.pdf": b"other"})
    checks = manual_fetch.compare(article, _record(), directory, root=_manual_root(tmp_path))
    matched = next(c for c in checks if c["check"] == "supplements_matched")
    assert matched["ok"] is False
    assert "science.adt8307_sm.pdf" in matched["detail"]


def test_a_silently_wrong_supplement_status_fails(tmp_path):
    """The check the whole harness exists for: the article really has supplements,
    fetch came away with none, and called the question settled."""
    article = {
        "doi": DOI, "source_dir": "Science", "main_pdf": None, "supplements": [],
        "expect": {"supplementary_status": "expected_but_missing"},
    }
    directory = _article_dir(tmp_path)
    checks = manual_fetch.compare(article, _record(suppl_status="none_listed"), directory,
                          root=_manual_root(tmp_path))
    status = next(c for c in checks if c["check"] == "supplementary_status")
    assert status["ok"] is False
    assert "none_listed" in status["detail"]


def test_page_count_is_asserted_for_the_published_version(tmp_path):
    article = {
        "doi": DOI, "source_dir": "Science",
        "main_pdf": {"file": "a.pdf", "pages": 19, "version": manual_fetch.PUBLISHED},
        "supplements": [],
    }
    directory = _article_dir(tmp_path, pdf=make_pdf(pages=5))
    checks = manual_fetch.compare(article, _record(), directory, root=_manual_root(tmp_path))
    pages = next(c for c in checks if c["check"] == "pdf_pages")
    assert pages["ok"] is False
    assert "manual 19pp, fetched 5pp" in pages["detail"]


def test_page_count_is_reported_but_not_asserted_for_other_renditions(tmp_path):
    """Cell Genomics' mmc12.pdf runs 59 pages against a 37-page typeset article.
    Both are the same paper, so a length difference is not an error."""
    article = {
        "doi": DOI, "source_dir": "Science",
        "main_pdf": {"file": "a.pdf", "pages": 59, "version": "extended"},
        "supplements": [],
    }
    directory = _article_dir(tmp_path, pdf=make_pdf(pages=5))
    checks = manual_fetch.compare(article, _record(), directory, root=_manual_root(tmp_path))
    pages = next(c for c in checks if c["check"] == "pdf_pages")
    assert pages["ok"] is None
    assert "extended" in pages["detail"]


def test_the_wrong_paper_fails_identity(tmp_path):
    article = {
        "doi": DOI, "source_dir": "Science",
        "main_pdf": {"file": "a.pdf", "pages": 2, "version": manual_fetch.PUBLISHED},
        "supplements": [],
    }
    directory = _article_dir(tmp_path, pdf=make_pdf(pages=2, text="an entirely different paper"))
    checks = manual_fetch.compare(article, _record(), directory, root=_manual_root(tmp_path))
    assert next(c for c in checks if c["check"] == "pdf_identity")["ok"] is False


def test_the_right_paper_passes_identity(tmp_path):
    article = {
        "doi": DOI, "source_dir": "Science",
        "main_pdf": {"file": "a.pdf", "pages": 2, "version": manual_fetch.PUBLISHED},
        "supplements": [],
    }
    directory = _article_dir(tmp_path, pdf=make_pdf(pages=2, text=f"Research article {DOI} aging"))
    checks = manual_fetch.compare(article, _record(), directory, root=_manual_root(tmp_path))
    assert next(c for c in checks if c["check"] == "pdf_identity")["ok"] is True


def test_extra_fetched_files_are_reported_never_failed(tmp_path):
    """Fetch legitimately collects what a human skipped -- reporting summaries, peer
    review files -- and failing the better result would be backwards."""
    article = {"doi": DOI, "source_dir": "Science", "main_pdf": None, "supplements": []}
    directory = _article_dir(tmp_path, supplements={"bonus.xlsx": b"bonus"})
    checks = manual_fetch.compare(article, _record(), directory, root=_manual_root(tmp_path))
    extra = next(c for c in checks if c["check"] == "supplements_extra")
    assert extra["ok"] is None
    assert not manual_fetch.failures(checks)


def test_a_manual_pdf_absent_from_the_folder_is_reported_not_asserted(tmp_path):
    """The Cell Genomics case before the article PDF was added: nothing to compare,
    but fetch finding one the manual copy lacks is worth saying out loud."""
    article = {"doi": DOI, "source_dir": "Science", "main_pdf": None, "supplements": []}
    directory = _article_dir(tmp_path, pdf=make_pdf(pages=3))
    checks = manual_fetch.compare(article, _record(), directory, root=_manual_root(tmp_path))
    present = next(c for c in checks if c["check"] == "pdf_present")
    assert present["ok"] is None
    assert "manual copy lacks" in present["detail"]


# -- the bootstrap command ---------------------------------------------------

def _download_folder(tmp_path):
    folder = tmp_path / "Science"
    folder.mkdir()
    (folder / "science.adt8307.pdf").write_bytes(make_pdf(pages=19))
    (folder / "science.adt8307_sm.pdf").write_bytes(make_pdf(pages=29))
    _zip(folder / "science.adt8307_tables_s1_to_s28.zip", {"Table_S1.tsv": "a\tb\n"})
    return folder


def test_bootstrap_writes_a_reviewable_spec(tmp_path):
    _download_folder(tmp_path)
    out = tmp_path / "manual_fetch.yaml"
    assert manual_fetch.main(["bootstrap", f"{DOI}=Science", "--root", str(tmp_path),
                      "--out", str(out)]) == 0

    spec = manual_fetch.load_spec(out)
    entry = spec["articles"][0]
    assert entry["doi"] == DOI
    assert entry["main_pdf"]["file"] == "science.adt8307.pdf"
    assert entry["main_pdf"]["pages"] == 19
    assert entry["main_pdf"]["version"] == manual_fetch.PUBLISHED
    assert len(entry["supplements"]) == 2
    # The generated expectation is a proposal for a human to confirm, not an
    # answer -- bootstrap cannot know which tier will reach the paper, and that is
    # what decides between `fetched` and `fetched_unverified`. It guesses the
    # latter because a paper worth fetching by hand is one the open-access tiers
    # missed, so it arrives via a page scrape.
    assert entry["expect"]["supplementary_status"] == "fetched_unverified"


def test_bootstrap_records_a_hinted_version(tmp_path):
    folder = _download_folder(tmp_path)
    (folder / "mmc12.pdf").write_bytes(make_pdf(pages=59))
    out = tmp_path / "manual_fetch.yaml"
    assert manual_fetch.main(["bootstrap", f"{DOI}=Science", "--main", f"{DOI}=mmc12.pdf@extended",
                      "--root", str(tmp_path), "--out", str(out)]) == 0

    entry = manual_fetch.load_spec(out)["articles"][0]
    assert entry["main_pdf"]["file"] == "mmc12.pdf"
    assert entry["main_pdf"]["version"] == "extended"


@pytest.mark.parametrize("argv", [
    pytest.param(["bootstrap", DOI], id="no-subdir"),
    pytest.param(["bootstrap", f"{DOI}=Nope"], id="subdir-absent"),
    pytest.param(["bootstrap", "not-a-doi=Science"], id="not-a-doi"),
    pytest.param(["bootstrap", f"{DOI}=Science", "--main", "not-a-doi=x.pdf"], id="hint-not-a-doi"),
])
def test_bootstrap_rejects_bad_input(tmp_path, argv):
    """A mistyped argument should be an error message and exit 2, not a traceback --
    the same stance fetch_publication takes on an unreachable paper."""
    _download_folder(tmp_path)
    assert manual_fetch.main(argv + ["--root", str(tmp_path), "--out", str(tmp_path / "o.yaml")]) == 2


def test_bootstrap_rejects_a_main_hint_it_cannot_find(tmp_path):
    _download_folder(tmp_path)
    assert manual_fetch.main(["bootstrap", f"{DOI}=Science", "--main", f"{DOI}=absent.pdf",
                      "--root", str(tmp_path), "--out", str(tmp_path / "o.yaml")]) == 2


def test_bootstrap_rejects_a_malformed_main_hint(tmp_path):
    _download_folder(tmp_path)
    assert manual_fetch.main(["bootstrap", f"{DOI}=Science", "--main", DOI,
                      "--root", str(tmp_path), "--out", str(tmp_path / "o.yaml")]) == 2


def test_bootstrap_needs_a_root_that_exists(tmp_path):
    assert manual_fetch.main(["bootstrap", f"{DOI}=Science", "--root", str(tmp_path / "gone"),
                      "--out", str(tmp_path / "o.yaml")]) == 2


def test_an_absent_spec_reads_as_empty_rather_than_raising(tmp_path):
    assert manual_fetch.load_spec(tmp_path / "nothing.yaml") == {"articles": []}


def test_manual_root_prefers_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv(manual_fetch.MANUAL_DIR_ENV, str(tmp_path))
    assert manual_fetch.manual_root() == tmp_path
    # An explicit argument still wins, so a caller can override the environment.
    assert manual_fetch.manual_root("elsewhere") == manual_fetch.Path("elsewhere")


def test_missing_manual_files_skip_rather_than_fail(tmp_path):
    """On a machine without the downloads there is nothing to say, and saying
    "failed" would be a lie."""
    article = {"doi": DOI, "source_dir": "NotHere", "main_pdf": None, "supplements": []}
    checks = manual_fetch.compare(article, _record(), _article_dir(tmp_path), root=tmp_path)
    assert [c["check"] for c in checks] == ["manual_files_present"]
    assert not manual_fetch.failures(checks)
