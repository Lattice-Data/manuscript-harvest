"""The one sentence three statuses become, and the two properties it must keep.

This module exists because `partial` means nothing until you know which of three
columns it came from, so the tests worth writing are about *coverage* and
*honesty* rather than about any single phrase.

Coverage: every value `fetcher._supplement_status` can return has a clause here,
and an unknown one still produces a readable line rather than a KeyError. A
manifest written by a future version of the fetch stage lands in this function.

Honesty: a settled-but-unbounded supplement set must not read as plainly
complete, because 237 of 392 articles in the development corpus sit there.
"""

import re

import pytest

from manuscript_harvest import article_state
from manuscript_harvest.fetch import store

# Every value `_supplement_status` returns, read off its `return` statements. If a
# new one is added there, this list is what makes the omission visible here.
SUPPLEMENT_VALUES = [
    "not_requested", "none_listed", "fetched", "fetched_unverified",
    "partial_failure", "none_text_bearing", "expected_but_missing",
    "none_retrieved", "page_not_parsed", "unknown_none_found",
]


def test_every_supplement_value_has_a_clause():
    """No fetch-stage vocabulary value falls through to its own raw token."""
    for value in SUPPLEMENT_VALUES:
        described = article_state.describe("complete", value, "complete")
        clause = described["clauses"][1]["text"]
        assert value not in clause, f"{value} was printed rather than phrased"
        assert re.search(r"supplement", clause), clause


def test_supplement_vocabulary_here_matches_the_fetch_stage():
    """The list above is the fetch stage's, not a copy that can drift from it.

    Read out of `fetcher.py`'s source rather than imported, because
    `_supplement_status` returns its words from ten separate branches -- there is
    no set to import, and the alternative is a list nobody updates.
    """
    from manuscript_harvest.fetch import fetcher
    import inspect
    source = inspect.getsource(fetcher._supplement_status)
    returned = set(re.findall(r'return "([a-z_]+)"', source))
    assert returned == set(SUPPLEMENT_VALUES)


def test_settled_supplements_read_as_settled():
    """`store.SUPPL_SETTLED` is the authority on what needs no more fetching."""
    for value in store.SUPPL_SETTLED:
        described = article_state.describe("complete", value, "complete")
        assert described["clauses"][1]["level"] != "outstanding", value


def test_an_unbounded_set_is_not_reported_as_complete():
    """The distinction the extraction stage raises `supplement_set_unverified` for.

    `fetched_unverified` is settled -- it blocks nothing -- but it means "every
    file we identified arrived", not "the deposit was enumerated". Flattening it
    into the `fetched` phrase would claim a completeness no tier established, over
    the majority of the corpus.
    """
    bounded = article_state.describe("complete", "fetched", "complete")
    unbounded = article_state.describe("complete", "fetched_unverified", "complete")
    assert bounded["clauses"][1]["text"] != unbounded["clauses"][1]["text"]
    assert bounded["level"] == "ok"
    assert unbounded["level"] == "caution"


def test_every_clause_names_its_own_stage():
    """The whole point: no clause is decodable only by its position."""
    described = article_state.describe("partial", "partial_failure", "partial")
    fetch, supplements, extraction = described["clauses"]
    assert "fetch" in fetch["text"]
    assert "supplement" in supplements["text"]
    assert "extraction" in extraction["text"]
    # And the bare word that made the three columns ambiguous appears nowhere.
    assert "partial" not in described["summary"], described["summary"]


def test_all_three_clauses_are_always_present():
    """An omitted clause would have to be decoded from its absence."""
    for extract in ("complete", "partial", None, "crashed"):
        described = article_state.describe("complete", "fetched", extract)
        assert len(described["clauses"]) == 3
        assert described["summary"].count(",") == 2


@pytest.mark.parametrize("fetch,supplements,extract", [
    (None, None, None),
    ("", "", ""),
    ("some_future_word", "some_future_status", "some_future_state"),
])
def test_unknown_and_missing_values_still_produce_a_line(fetch, supplements, extract):
    """A manifest from another version of the fetch stage must not raise.

    An unrecognised word is named rather than mapped onto a known one: reporting
    a new status as an old one is how a vocabulary change goes unnoticed.
    """
    described = article_state.describe(fetch, supplements, extract)
    assert len(described["clauses"]) == 3
    assert described["summary"]
    assert described["level"] == "outstanding"


# `store.finalize_status` writes the first three; `store.evict_article` the fourth.
FETCH_VALUES = ["complete", "partial", "failed", "evicted"]


def test_every_fetch_value_has_a_clause():
    """Each names its stage, and none is left as the ambiguous `partial`.

    `fetch failed` is allowed to reuse the raw token, because there `failed` is
    already the plain English word -- unlike `partial`, which is the value this
    whole module exists to stop printing on its own.
    """
    for value in FETCH_VALUES:
        clause = article_state.describe(value, "fetched", "complete")["clauses"][0]
        assert clause["text"].startswith("fetch ")
        assert "partial" not in clause["text"], clause


def test_fetch_vocabulary_here_matches_the_fetch_stage():
    """Read out of `store.py`, so a new status value shows up as a failure here."""
    import inspect
    source = inspect.getsource(store)
    written = set(re.findall(r'record\["status"\] = "([a-z_]+)"', source))
    assert written == set(FETCH_VALUES)


def test_an_evicted_article_is_neither_complete_nor_incomplete():
    """`manifest_is_complete` counts it as fetched; its bytes are off the disk.

    Reported as complete it would leave a reader unable to explain an empty
    extraction; reported as incomplete it would contradict the fetch stage, which
    will not re-fetch it without `--force`.
    """
    described = article_state.describe("evicted", "fetched", "partial")
    assert "evicted" in described["clauses"][0]["text"]
    assert described["clauses"][0]["level"] == "caution"


# `extract_article` writes the first three; `no_manifest` comes from its guard.
# `crashed` is not here: `cmd_all` invents it for an article that raised, so it
# never appears in an extraction record and cannot be read out of the extractor.
EXTRACT_VALUES = ["complete", "partial", "failed", "no_manifest"]


def test_extract_vocabulary_here_matches_the_extract_stage():
    import inspect
    from manuscript_harvest.extract import extractor
    source = inspect.getsource(extractor)
    written = set(re.findall(r'\bstatus = "([a-z_]+)"', source))
    written |= set(re.findall(r'"status": "([a-z_]+)"', source))
    assert written == set(EXTRACT_VALUES)


def test_every_extract_value_and_the_two_invented_ones_have_a_clause():
    """Including the two states no extraction record ever holds.

    `crashed` is `cmd_all`'s word for an article whose extraction raised, and
    `None` means there is no record on disk. Both have to read differently from
    `failed`, which means the extractor ran and found nothing to read -- that is
    three distinct things a reader would otherwise have to guess between.
    """
    seen = {}
    for value in EXTRACT_VALUES + ["crashed", None]:
        clause = article_state.describe("complete", "fetched", value)["clauses"][2]
        assert "partial" not in clause["text"], clause
        assert clause["level"] == ("ok" if value == "complete" else "outstanding")
        seen[value] = clause["text"]
    assert len(set(seen.values())) == len(seen), f"two states share a phrase: {seen}"


def test_level_is_the_worst_clause():
    assert article_state.describe("complete", "fetched", "complete")["level"] == "ok"
    assert article_state.describe(
        "complete", "fetched_unverified", "complete")["level"] == "caution"
    # One outstanding clause outranks a caution: there is work to do either way.
    assert article_state.describe(
        "complete", "fetched_unverified", "partial")["level"] == "outstanding"


def test_raw_keeps_the_tokens_the_clauses_came_from():
    """The panel's tooltip and any `--raw` reader need the original words."""
    raw = article_state.describe("partial", "partial_failure", "complete")["raw"]
    assert "fetch=partial" in raw
    assert "supplementary=partial_failure" in raw
    assert "extract=complete" in raw


def test_summarize_is_describes_sentence():
    args = ("complete", "fetched_unverified", "partial")
    assert article_state.summarize(*args) == article_state.describe(*args)["summary"]


def test_not_extracted_is_said_rather_than_left_blank():
    described = article_state.describe("complete", "fetched", None)
    assert described["clauses"][2]["text"] == "not extracted yet"
