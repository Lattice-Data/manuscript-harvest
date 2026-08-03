"""The review layer: what gets asked, in what order, and when an answer expires.

These pin the two claims the layer rests on. First, that the queue is worth a
curator's time -- it is short, it is ordered by value per minute, and it does not
ask about the 76 figure images in this corpus that nobody could judge from a
filename. Second, that an answer is never applied to bytes it was not given
about: a re-fetched file invalidates the answer, a parser change does not.
"""

import json

import pytest

from manuscript_harvest.extract import review, reviewsheet
from manuscript_harvest.extract.blocks import BLOCKS_NAME, read_blocks
from manuscript_harvest.extract.extractor import EXTRACT_DIR, extract_article
from manuscript_harvest.extract.limits import Limits
from manuscript_harvest.fetch import store
from tests.fakes import DOI, jats_article, make_article, make_pdf_pages, make_xlsx

L = Limits()

METHODS_BODY = (
    '<sec sec-type="methods"><title>Methods</title><p>'
    + "Islets from eight-week-old male C57BL/6 mice were dissociated and loaded "
      "on a 10x Chromium controller with the Single Cell 3' v3 kit. " * 60
    + '</p></sec>'
)

#: All-text rows under all-text headers: `header_confidence` stays `low` because
#: nothing confirms the first row is not a first data row of gene names.
AMBIGUOUS = {"S1": [["gene", "symbol"], ["TP53", "p53"], ["MYC", "myc"]]}


def _extracted(tmp_path, **kwargs):
    directory = make_article(tmp_path / store.doi_slug(DOI), **kwargs)
    record = extract_article(directory, limits=L)
    return directory, record


def _queue(directory, record):
    return review.queue_for(record, directory / EXTRACT_DIR / BLOCKS_NAME,
                            limits=L, manifest=store.read_manifest(directory))


# -- what gets asked ---------------------------------------------------------

def test_a_clean_article_is_asked_only_to_be_signed_off(tmp_path):
    directory, record = _extracted(tmp_path, xml=jats_article(METHODS_BODY))
    queue = _queue(directory, record)
    assert [item["kind"] for item in queue] == [review.SIGN_OFF]


def test_the_queue_order_is_the_documented_one(tmp_path):
    """The only order assertion was `sign_off` last, so the queue and the two places
    that describe it had drifted apart unnoticed: `review.py`'s own numbered list and
    the README both said table headers came first, and the code asks
    `main_text_present` before them. This pins the whole sequence."""
    page = ("Tissue-resident immune cells are important for organ homeostasis. "
            "We profiled the mature and developing human kidney. ") * 20
    directory, record = _extracted(
        tmp_path,
        fulltext=make_pdf_pages([[page]]),
        supplements=[("s1.xlsx", make_xlsx(AMBIGUOUS)), ("notes.rtf", b"{\\rtf1 x}")])
    kinds = [i["kind"] for i in _queue(directory, record)]

    rank = {kind: n for n, kind in enumerate([
        review.MAIN_TEXT_PRESENT, review.TABLE_HEADER, review.FILE_HAS_CONTENT,
        review.SUPPLEMENT_LABEL, review.SECTION_SPAN, review.SIGN_OFF])}
    assert set(kinds) == set(rank), "fixture does not exercise every kind"
    assert [rank[k] for k in kinds] == sorted(rank[k] for k in kinds), kinds


def test_sign_off_is_always_last(tmp_path):
    directory, record = _extracted(
        tmp_path, xml=jats_article(METHODS_BODY),
        supplements=[("s1.xlsx", make_xlsx(AMBIGUOUS)), ("notes.rtf", b"{\\rtf1 x}")])
    queue = _queue(directory, record)
    assert queue[-1]["kind"] == review.SIGN_OFF
    assert sum(1 for i in queue if i["kind"] == review.SIGN_OFF) == 1


def test_a_low_confidence_table_header_is_queued_with_the_card_verbatim(tmp_path):
    """The strongest triage signal there is, ~15 seconds each, and a wrong header
    silently corrupts every metadata answer drawn from that sheet."""
    directory, record = _extracted(tmp_path, xml=jats_article(METHODS_BODY),
                                   supplements=[("s1.xlsx", make_xlsx(AMBIGUOUS))])
    item = next(i for i in _queue(directory, record) if i["kind"] == review.TABLE_HEADER)
    assert item["key"] == {"source_file": record["supplementary"][0]["path"],
                           "locator": "sheet 'S1'"}
    assert item["card_fingerprint"]
    assert "gene" in item["body"] and "TABLE:" in item["body"]
    assert item["source_sha256"] == \
        store.read_manifest(directory)["supplementary"][0]["sha256"]


def test_figure_images_are_never_queued(tmp_path):
    """76 of the 101 supplements in this corpus are figure images. Queuing them
    would be three quarters of the work, and nobody can judge a .jpg by name."""
    directory, record = _extracted(
        tmp_path, xml=jats_article(METHODS_BODY),
        supplements=[("f1.jpg", b"\xff\xd8x"), ("m1.mp4", b"\x00\x00\x00 ftypmp42"),
                     ("counts.h5ad", b"\x89HDF\r\n\x1a\n")])
    assert [i["kind"] for i in _queue(directory, record)] == [review.SIGN_OFF]


def test_an_unparseable_supplement_is_queued(tmp_path):
    directory, record = _extracted(tmp_path, xml=jats_article(METHODS_BODY),
                                   supplements=[("notes.rtf", b"{\\rtf1 text}")])
    item = next(i for i in _queue(directory, record)
                if i["kind"] == review.FILE_HAS_CONTENT)
    assert item["key"]["path"] == record["supplementary"][0]["path"]
    assert "not parsed by this stage" in item["why"]


def test_a_thin_main_text_asks_whether_the_article_is_here(tmp_path):
    directory, record = _extracted(tmp_path, xml=jats_article(
        "<sec><title>Results</title><p>Short.</p></sec>"))
    item = next(i for i in _queue(directory, record)
                if i["kind"] == review.MAIN_TEXT_PRESENT)
    assert "characters of main text" in item["why"]


def test_a_section_span_is_only_asked_where_the_audit_cannot_score_it(tmp_path):
    """`section_audit.py` scores the labeller for free wherever a JATS reference
    exists, so this question is only worth asking where it cannot."""
    page = ("Tissue-resident immune cells are important for organ homeostasis. "
            "We profiled the mature and developing human kidney. ") * 20
    directory, record = _extracted(tmp_path, fulltext=make_pdf_pages([[page]]))
    assert any(i["kind"] == review.SECTION_SPAN for i in _queue(directory, record))

    with_xml, record = _extracted(tmp_path, xml=jats_article(METHODS_BODY),
                                  fulltext=make_pdf_pages([[page]]))
    assert not any(i["kind"] == review.SECTION_SPAN for i in _queue(with_xml, record))


def test_the_per_article_card_cap_is_counted_not_silent(tmp_path):
    sheets = {f"S{i}": [["gene", "symbol"], ["TP53", "p53"]] for i in range(6)}
    directory, record = _extracted(tmp_path, xml=jats_article(METHODS_BODY),
                                   supplements=[("s.xlsx", make_xlsx(sheets))])
    limits = Limits(max_review_cards_per_article=2)
    blocks = directory / EXTRACT_DIR / BLOCKS_NAME
    queue = review.queue_for(record, blocks, limits=limits)
    assert sum(1 for i in queue if i["kind"] == review.TABLE_HEADER) == 2
    assert review.queue_truncated(record, blocks, limits) == 4


def test_every_queued_key_is_unique_within_an_article(tmp_path):
    directory, record = _extracted(
        tmp_path, xml=jats_article(METHODS_BODY),
        supplements=[("s1.xlsx", make_xlsx({f"S{i}": AMBIGUOUS["S1"] for i in range(4)})),
                     ("notes.rtf", b"{\\rtf1 x}")])
    keys = [review.answer_key(i["kind"], i["key"]) for i in _queue(directory, record)]
    assert len(keys) == len(set(keys))


def test_every_queued_kind_is_in_the_taxonomy(tmp_path):
    directory, record = _extracted(tmp_path, xml=jats_article(METHODS_BODY),
                                   supplements=[("s1.xlsx", make_xlsx(AMBIGUOUS))])
    for item in _queue(directory, record):
        assert item["kind"] in review.ITEM_KINDS


# -- when an answer expires --------------------------------------------------

def _answer(item, verdict="confirmed", **extra):
    return {"kind": item["kind"], "key": item["key"],
            "source_sha256": item.get("source_sha256"),
            "card_fingerprint": item.get("card_fingerprint"),
            "verdict": verdict, "by": "gabdank@stanford.edu",
            "at": "2026-08-01T10:14:00Z", **extra}


def test_an_answer_survives_a_parser_change_but_not_a_re_fetch(tmp_path):
    """The two staleness kinds are not the same thing. A human's claim is about
    the bytes: a re-fetch invalidates it, a parser change does not."""
    directory, record = _extracted(tmp_path, xml=jats_article(METHODS_BODY),
                                   supplements=[("s1.xlsx", make_xlsx(AMBIGUOUS))])
    manifest = store.read_manifest(directory)
    queue = _queue(directory, record)
    item = next(i for i in queue if i["kind"] == review.TABLE_HEADER)
    reviewed = {**review.empty_review(record), "answers": [_answer(item)]}

    state, stale = review.state_of(reviewed, record, manifest, queue)
    assert (state, stale) == ("partially_reviewed", [])

    # A parser change moved the header: the shape differs, the bytes do not.
    moved = [dict(i) for i in queue]
    for entry in moved:
        if entry["kind"] == review.TABLE_HEADER:
            entry["card_fingerprint"] = "0000000000000000"
    state, stale = review.state_of(reviewed, record, manifest, moved)
    assert state == "stale" and stale[0]["why"] == "stale_shape"

    # The file was re-fetched: the answer is about bytes that are gone.
    manifest["supplementary"][0]["sha256"] = "f" * 64
    state, stale = review.state_of(reviewed, record, manifest, queue)
    assert state == "stale" and stale[0]["why"] == "stale_bytes"


def test_a_sign_off_goes_stale_when_the_manifest_changes(tmp_path):
    directory, record = _extracted(tmp_path, xml=jats_article(METHODS_BODY))
    manifest = store.read_manifest(directory)
    queue = _queue(directory, record)
    signed = {**review.empty_review(record),
              "sign_off": {"verdict": "fit", "by": "x", "at": "2026-08-01T10:14:00Z"},
              "signed_manifest_sha256": record["source_manifest_sha256"]}
    assert review.state_of(signed, record, manifest, queue)[0] == "reviewed"

    signed["signed_manifest_sha256"] = "0" * 64
    assert review.state_of(signed, record, manifest, queue)[0] == "stale"


def test_an_unreviewed_article_with_questions_is_queued(tmp_path):
    directory, record = _extracted(tmp_path, xml=jats_article(METHODS_BODY),
                                   supplements=[("s1.xlsx", make_xlsx(AMBIGUOUS))])
    queue = _queue(directory, record)
    assert review.state_of(None, record, store.read_manifest(directory), queue)[0] \
        == "queued"


def test_every_state_is_in_the_closed_set(tmp_path):
    directory, record = _extracted(tmp_path, xml=jats_article(METHODS_BODY))
    state, _ = review.state_of(None, record, store.read_manifest(directory),
                               _queue(directory, record))
    assert state in review.STATES


def test_the_review_file_lives_at_the_repo_root_not_beside_the_article():
    """`store.evict_article` deletes everything but manifest.json, and `corpus/`
    is gitignored, so a review kept beside the article would die with an eviction
    and could never be committed."""
    assert review.review_path("10.1_x") == \
        pytest.importorskip("pathlib").Path("reviews/10.1_x.json")
    assert review.review_path("10.1_x", {"extract": {"review_dir": "elsewhere"}}) \
        .parent.name == "elsewhere"


def test_a_review_file_round_trips(tmp_path):
    path = tmp_path / "10.1_x.json"
    payload = {"review_format": 1, "slug": "10.1_x", "answers": []}
    path.write_text(json.dumps(payload))
    assert review.read_review(path) == payload
    assert review.read_review(tmp_path / "nope.json") is None
    (tmp_path / "bad.json").write_text("{not json")
    assert review.read_review(tmp_path / "bad.json") is None


# -- feeding the answers back ------------------------------------------------

def _write_review(tmp_path, record, answers, sign_off=None):
    """A `reviews/<slug>.json` in a config-pointed directory, as the CLI writes it."""
    directory = tmp_path / "reviews"
    directory.mkdir(exist_ok=True)
    payload = {**review.empty_review(record), "answers": answers}
    if sign_off:
        payload["sign_off"] = sign_off
        payload["signed_manifest_sha256"] = record["source_manifest_sha256"]
    (directory / f"{record['slug']}.json").write_text(json.dumps(payload))
    return {"extract": {"review_dir": str(directory)}}


def test_the_applied_breakdown_sums_to_the_applied_total(tmp_path):
    """`review --apply` printed a total counted over the whole stored file beside a
    breakdown counted over the incoming batch, so the two measured different sets.
    Because the file is append-only, a second apply against an article with fourteen
    stored answers read "14 override(s) applied: 1 table header"."""
    directory, record = _extracted(
        tmp_path, xml=jats_article(METHODS_BODY),
        supplements=[("s1.xlsx", make_xlsx(AMBIGUOUS)), ("notes.rtf", b"{\\rtf1 x}")])
    queue = _queue(directory, record)
    header = next(i for i in queue if i["kind"] == review.TABLE_HEADER)
    content = next(i for i in queue if i["kind"] == review.FILE_HAS_CONTENT)

    config = _write_review(tmp_path, record, [
        _answer(header, "corrected", override={"header_row": 1}),
        _answer(content, "confirmed", override={"has_content": False})])
    after = extract_article(directory, limits=L, force=True, config=config)

    kinds = after["review"]["overrides_applied_kinds"]
    assert sum(kinds.values()) == after["review"]["overrides_applied"]
    assert set(kinds) == {review.TABLE_HEADER, review.FILE_HAS_CONTENT}


def test_a_confirmed_header_row_changes_the_next_extraction(tmp_path):
    """A correction that does not change the next extraction is a note, not a
    correction."""
    directory, record = _extracted(
        tmp_path, xml=jats_article(METHODS_BODY),
        supplements=[("s1.xlsx", make_xlsx(
            {"S1": [["Supplementary Table 1", None], ["gene", "symbol"],
                    ["TP53", "p53"], ["MYC", "myc"]]}))])
    item = next(i for i in _queue(directory, record) if i["kind"] == review.TABLE_HEADER)
    config = _write_review(tmp_path, record, [
        _answer(item, "corrected", override={"header_row": 2},
                note="row 1 is a title line")])

    after = extract_article(directory, limits=L, force=True, config=config)
    assert after["review"]["overrides_applied"] == 1
    card = next(b["table"] for b in read_blocks(directory / EXTRACT_DIR / BLOCKS_NAME)
                if b.get("table"))
    assert card["header_confidence"] == "confirmed"
    assert card["header"] == ["gene", "symbol"]
    assert any("row 1 is a title line" in note for note in card["notes"])
    # The note is an f-string over values read out of the review file, never
    # datetime.now(), because it lands in blocks.jsonl.
    assert "2026-08-01T10:14:00Z" in " ".join(card["notes"])


def test_a_review_is_part_of_the_extraction_key(tmp_path):
    """Otherwise the first correction is silently discarded by the next
    `manuscript-extract all`."""
    directory, record = _extracted(tmp_path, xml=jats_article(METHODS_BODY),
                                   supplements=[("s1.xlsx", make_xlsx(AMBIGUOUS))])
    config = {"extract": {"review_dir": str(tmp_path / "reviews")}}
    assert extract_article(directory, limits=L, config=config).get("cached") is True
    item = next(i for i in _queue(directory, record) if i["kind"] == review.TABLE_HEADER)
    _write_review(tmp_path, record, [_answer(item, "corrected",
                                             override={"header_row": 1})])
    assert extract_article(directory, limits=L, config=config).get("cached") is None


def test_a_cleared_file_stays_listed_and_the_article_can_be_complete(tmp_path):
    """Nothing disappears: the file is still in `unextracted_text_files`, and one
    key away is the human who cleared it. The per-file status does not move -- the
    taxonomy stays closed and a .rtf stays `unsupported_format`."""
    directory, record = _extracted(tmp_path, xml=jats_article(METHODS_BODY),
                                   supplements=[("notes.rtf", b"{\\rtf1 text}")])
    assert record["status"] == "partial"
    path = record["supplementary"][0]["path"]
    item = next(i for i in _queue(directory, record)
                if i["kind"] == review.FILE_HAS_CONTENT)
    config = _write_review(tmp_path, record, [
        _answer(item, "confirmed", override={"has_content": False},
                note="a licence notice")])

    after = extract_article(directory, limits=L, force=True, config=config)
    assert after["status"] == "complete"
    assert after["unextracted_text_files"] == [path]
    assert after["cleared_by_review"] == [path]
    assert after["supplementary"][0]["status"] == "unsupported_format"


#: Body prose no heading rule recognises, as several layout blocks so the reviewed
#: span has more than one paragraph to label -- one call covering many blocks is the
#: whole point of the counting rule below.
UNLABELLED_PAGES = [[
    ("Tissue-resident immune cells are important for organ homeostasis. "
     "We profiled the mature and developing human kidney. ") * 6,
    ("Cells were clustered and annotated against a reference atlas, giving "
     "twenty-eight populations across the nephron. ") * 6,
    ("Receptor-ligand analysis implicated a signalling axis between epithelium "
     "and resident macrophages. ") * 6,
]]


def _span_answered(tmp_path, section="results", note="pages 3-7 are results"):
    """An article that queues a section span, with that span answered."""
    directory, record = _extracted(tmp_path, fulltext=make_pdf_pages(UNLABELLED_PAGES))
    item = next(i for i in _queue(directory, record)
                if i["kind"] == review.SECTION_SPAN)
    config = _write_review(tmp_path, record, [
        _answer(item, "corrected", override={"section": section}, note=note)])
    return directory, extract_article(directory, limits=L, force=True, config=config)


def test_an_answered_section_span_labels_the_blocks_the_parser_left_unlabelled(tmp_path):
    """The question was asked, rendered and stored, and the answer reached nothing:
    `Overrides.section_for` had no caller. On 10.1126/science.aat5031 that showed up
    as 14 override-bearing answers against `overrides_applied: 13`."""
    directory, after = _span_answered(tmp_path)
    assert after["review"]["overrides_applied"] == 1

    blocks = [b for b in read_blocks(directory / EXTRACT_DIR / BLOCKS_NAME)
              if b["role"] == "main_text"]
    reviewed = [b for b in blocks if b.get("section_source") == "review"]
    assert reviewed, "no block carries the reviewed section"
    assert {b["section"] for b in reviewed} == {"results"}
    # Nothing a rule does is silent, and a reviewed section is not a derived one.
    assert after["main_text"]["sections_from_review"] == {
        "section": "results",
        "blocks": len(reviewed),
        "blocks_already_labelled": len(blocks) - len(reviewed),
    }


def test_a_reviewed_span_never_overwrites_a_section_the_parser_found(tmp_path):
    """One value for a whole unlabelled span is weaker evidence than a heading the
    parser actually recognised, so it only fills gaps. The curator's own note on
    10.1126/science.aat5031 says as much: "overwhelmingly results", while naming two
    introduction paragraphs and one discussion paragraph inside the same span."""
    # The unlabelled prose has to come *first*: a heading owns everything after it,
    # so front matter before the only recognised heading is the shape that leaves
    # both a real label and a gap for the reviewed span to fill.
    pages = [UNLABELLED_PAGES[0] + [
        "Methods " + ("Islets were dissociated and loaded on a 10x Chromium "
                      "controller with the Single Cell 3' v3 kit. " * 6),
    ]]
    directory, record = _extracted(tmp_path, fulltext=make_pdf_pages(pages))
    before = read_blocks(directory / EXTRACT_DIR / BLOCKS_NAME)
    labelled = {b["block_id"]: b["section"] for b in before if b.get("section")}
    assert "methods" in labelled.values(), "fixture did not label anything to protect"

    span = next(i for i in _queue(directory, record)
                if i["kind"] == review.SECTION_SPAN)
    config = _write_review(tmp_path, record, [
        _answer(span, "corrected", override={"section": "results"})])
    extract_article(directory, limits=L, force=True, config=config)

    after = read_blocks(directory / EXTRACT_DIR / BLOCKS_NAME)
    for block in after:
        was = labelled.get(block["block_id"])
        if was is not None:
            assert block["section"] == was, \
                f"the reviewed span overwrote a parser label: {was} -> {block['section']}"
            assert not block.get("section_source")


def test_a_reviewed_span_is_counted_once_however_many_blocks_it_labels(tmp_path):
    """`section_for` counts every call into `applied()`, so calling it per block
    would report one answer as dozens and make `overrides_applied` useless as the
    check that an answer arrived."""
    directory, after = _span_answered(tmp_path)
    labelled = after["main_text"]["sections_from_review"]["blocks"]
    assert labelled > 1, "fixture is too small to tell one call from many"
    assert after["review"]["overrides_applied"] == 1


def test_an_unanswered_section_span_leaves_the_blocks_unlabelled(tmp_path):
    """The gap-filling is the answer's doing, not the extractor's."""
    page = ("Tissue-resident immune cells are important for organ homeostasis. "
            "We profiled the mature and developing human kidney. ") * 20
    directory, record = _extracted(tmp_path, fulltext=make_pdf_pages([[page]]))
    blocks = read_blocks(directory / EXTRACT_DIR / BLOCKS_NAME)
    assert any(b.get("section") is None for b in blocks)
    assert not any(b.get("section_source") for b in blocks)
    assert "sections_from_review" not in (record.get("main_text") or {})


def test_a_file_a_human_says_has_content_blocks_complete(tmp_path):
    directory, record = _extracted(
        tmp_path, xml=jats_article(METHODS_BODY),
        supplements=[("figs.jpg", b"\xff\xd8x")])
    assert record["status"] == "complete"
    path = record["supplementary"][0]["path"]
    config = _write_review(tmp_path, record, [
        {"kind": review.FILE_HAS_CONTENT, "key": {"path": path},
         "source_sha256": store.read_manifest(directory)["supplementary"][0]["sha256"],
         "verdict": "corrected", "override": {"has_content": True},
         "note": "this is a table rendered as an image",
         "by": "x", "at": "2026-08-01T10:14:00Z"}])
    after = extract_article(directory, limits=L, force=True, config=config)
    assert after["unreachable_content"] == [path]
    assert after["status"] == "partial"
    # The status itself is untouched: an image with no text is still that.
    assert after["supplementary"][0]["status"] == "image_no_text"


def test_a_re_fetched_file_drops_its_override(tmp_path):
    directory, record = _extracted(tmp_path, xml=jats_article(METHODS_BODY),
                                   supplements=[("s1.xlsx", make_xlsx(AMBIGUOUS))])
    item = next(i for i in _queue(directory, record) if i["kind"] == review.TABLE_HEADER)
    config = _write_review(tmp_path, record, [
        _answer(item, "corrected", override={"header_row": 1})])
    manifest = store.read_manifest(directory)
    manifest["supplementary"][0]["sha256"] = "f" * 64
    store.write_manifest(directory, manifest)
    after = extract_article(directory, limits=L, force=True, config=config)
    assert after["review"]["overrides_applied"] == 0


# -- the sheet ---------------------------------------------------------------

def test_the_sheet_is_one_self_contained_page(tmp_path):
    directory, record = _extracted(
        tmp_path, xml=jats_article(METHODS_BODY),
        supplements=[("s1.xlsx", make_xlsx(AMBIGUOUS)), ("notes.rtf", b"{\\rtf1 x}")])
    queue = _queue(directory, record)
    page = reviewsheet.render(record, queue, None, article_dir=directory)
    assert "<script src" not in page and "http://" not in page
    assert page.count('<section class="item"') == len(queue) - 1
    assert "TABLE:" in page, "the card a curator is judging is in the page verbatim"
    assert "file://" in page, "and so is a link to open the source beside it"
    assert 'id="out"' in page and "Download the answers" in page


def test_the_sheet_escapes_what_a_publisher_put_in_a_cell(tmp_path):
    directory, record = _extracted(tmp_path, xml=jats_article(METHODS_BODY),
                                   supplements=[("s1.xlsx", make_xlsx(
                                       {"S1": [["gene", "<script>x</script>"],
                                               ["TP53", "p53"]]}))])
    page = reviewsheet.render(record, _queue(directory, record), None)
    assert "<script>x</script>" not in page
    assert "&lt;script&gt;x&lt;/script&gt;" in page


# -- the command line --------------------------------------------------------

def test_the_review_command_round_trips_a_sheet_into_an_extraction(tmp_path, capsys):
    from manuscript_harvest.extract.cli import main

    directory, record = _extracted(
        tmp_path, xml=jats_article(METHODS_BODY),
        supplements=[("s1.xlsx", make_xlsx(
            {"S1": [["Supplementary Table 1", None], ["gene", "symbol"],
                    ["TP53", "p53"]]}))])
    config = tmp_path / "config.yaml"
    config.write_text(f"extract:\n  corpus_dir: {tmp_path}\n"
                      f"  review_dir: {tmp_path / 'reviews'}\n")

    # A sheet with questions on it is a job that has not been done: exit 1.
    assert main(["--config", str(config), "review", DOI, "--out", str(tmp_path)]) == 1
    sheet = tmp_path / f"review-{record['slug']}.html"
    assert sheet.exists() and "TABLE:" in sheet.read_text()
    capsys.readouterr()

    item = next(i for i in _queue(directory, record) if i["kind"] == review.TABLE_HEADER)
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps({
        **review.empty_review(record),
        "answers": [_answer(item, "corrected", override={"header_row": 2},
                            note="row 1 is a title line")],
        "sign_off": {"verdict": "fit", "by": "x", "at": "2026-08-01T10:15:00Z"},
        "signed_manifest_sha256": record["source_manifest_sha256"]}))

    assert main(["--config", str(config), "review", DOI,
                 "--apply", str(answers)]) == 0
    assert "1 override(s) applied: 1 table header" in capsys.readouterr().err

    stored = json.loads((tmp_path / "reviews" / f"{record['slug']}.json").read_text())
    assert len(stored["answers"]) == 1 and stored["sign_off"]["verdict"] == "fit"

    after = json.loads((directory / EXTRACT_DIR / "extraction.json").read_text())
    assert after["review"]["state"] == "reviewed"
    assert after["review"]["overrides_applied"] == 1

    # Answers are appended, never rewritten: the file is an audit log.
    main(["--config", str(config), "review", DOI, "--apply", str(answers)])
    stored = json.loads((tmp_path / "reviews" / f"{record['slug']}.json").read_text())
    assert len(stored["answers"]) == 2
    assert stored["previous_sign_off"]["verdict"] == "fit"
