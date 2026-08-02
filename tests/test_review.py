"""The review layer: what gets asked, in what order, and when an answer expires.

These pin the two claims the layer rests on. First, that the queue is worth a
curator's time -- it is short, it is ordered by value per minute, and it does not
ask about the 76 figure images in this corpus that nobody could judge from a
filename. Second, that an answer is never applied to bytes it was not given
about: a re-fetched file invalidates the answer, a parser change does not.
"""

import json

import pytest

from manuscript_harvest.extract import extractor, review
from manuscript_harvest.extract.blocks import BLOCKS_NAME
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
