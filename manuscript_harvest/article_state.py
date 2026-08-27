"""Where one article has got to, in words that name their own stage.

An article carries three independent statuses -- the manifest's `status` and
`supplementary_status`, and the extraction record's `status` -- and every reader
of them had the same problem: the word `partial` is meaningless until you know
which of the three columns it was sitting in. Printed side by side they read as
three vocabularies to memorise rather than one answer.

So this renders them as clauses that each say what they are about:

    fetch complete, supplements fetched but set unconfirmed, extraction complete
    fetch incomplete, some supplements failed, extraction incomplete

Two rules hold this together.

**All three clauses, always.** An omitted clause has to be decoded from its
absence, which is the defect being fixed -- so a settled supplement set says so
out loud rather than staying quiet. It costs a wider column and buys a line
nobody has to interpret.

**The raw words are not replaced, only phrased.** `fetcher._supplement_status`
spends sixty lines of comment keeping ten values distinct, and
`test_supplement_status_precedence` pins their order; a phrase per value is a
display for that vocabulary, not a collapse of it. Nothing here is written to a
manifest or an extraction record, and every clause keeps the token it came from
in `raw` so a caller can show both.

Levels are for colour, and there are three because there are three questions a
reader has: is anything outstanding (`outstanding`), is any claim here weaker
than it looks (`caution`), or is this settled (`ok`).
"""

# Keyed on the manifest's `supplementary_status`. The right-hand side is the
# README's legend for that value, said as a clause; the level is
# `store.SUPPL_SETTLED` with one deliberate exception, below.
_SUPPLEMENTS = {
    "fetched": ("supplements complete", "ok"),
    # Settled -- `manifest_is_complete` will not re-fetch it -- but the claim is
    # weaker than `fetched`: every file *we identified* arrived, and nothing
    # bounds the set. 237 of 392 articles in the development corpus sit here, so
    # flattening it into "complete" would report a completeness the record cannot
    # back over 60% of the corpus. Amber rather than red: worth seeing once, not
    # worth chasing. The extraction stage draws the same distinction, as its
    # `supplement_set_unverified` caveat.
    "fetched_unverified": ("supplements fetched but set unconfirmed", "caution"),
    "none_listed": ("no supplements exist", "ok"),
    "none_text_bearing": ("supplements hold no text", "ok"),
    "not_requested": ("supplements not requested", "ok"),
    "partial_failure": ("some supplements failed", "outstanding"),
    "expected_but_missing": ("supplements listed but none arrived", "outstanding"),
    "none_retrieved": ("every supplement was lost", "outstanding"),
    "page_not_parsed": ("supplement list unread", "outstanding"),
    "unknown_none_found": ("no tier looked for supplements", "outstanding"),
}


def _clause(text: str, level: str) -> dict:
    return {"text": text, "level": level}


# `store.finalize_status` writes three of these and `store.evict_article` the
# fourth. `evicted` is the one worth a branch of its own: `manifest_is_complete`
# counts it as needing no further fetching, so calling it incomplete would be
# wrong -- but its bytes are off the disk, so calling it plainly complete would
# leave a reader wondering why extraction found nothing.
def _fetch_clause(status) -> dict:
    if status == "complete":
        return _clause("fetch complete", "ok")
    if status == "partial":
        return _clause("fetch incomplete", "outstanding")
    if status == "failed":
        return _clause("fetch failed", "outstanding")
    if status == "evicted":
        return _clause("fetch complete but files evicted", "caution")
    if not status:
        return _clause("not fetched", "outstanding")
    # An unrecognised word is still named rather than mapped to "incomplete".
    # Reporting an unknown status as a known one is how a new vocabulary value
    # would go unnoticed for as long as it took someone to read a manifest.
    return _clause(f"fetch {status}", "outstanding")


def _supplements_clause(status) -> dict:
    known = _SUPPLEMENTS.get(status)
    if known:
        return _clause(*known)
    if not status:
        return _clause("supplements unrecorded", "outstanding")
    return _clause(f"supplements {status}", "outstanding")


# `extract_article` writes `complete`, `partial` and `failed`, plus `no_manifest`
# from its guard clause. `None` is the fourth real case -- no extraction record on
# disk at all -- and `cmd_all` invents `crashed` for an article whose extraction
# raised. All six are named, because the difference between "we read it and there
# was nothing to read", "we never read it" and "it blew up" is exactly what a
# reader is trying to find out.
def _extraction_clause(status) -> dict:
    if status == "complete":
        return _clause("extraction complete", "ok")
    if status == "partial":
        return _clause("extraction incomplete", "outstanding")
    if status == "failed":
        return _clause("extraction failed", "outstanding")
    if status == "crashed":
        return _clause("extraction crashed", "outstanding")
    if status == "no_manifest":
        return _clause("no manifest to extract from", "outstanding")
    if not status or status == "not extracted":
        return _clause("not extracted yet", "outstanding")
    return _clause(f"extraction {status}", "outstanding")


def describe(fetch_status, supplementary_status, extract_status) -> dict:
    """One article's three statuses as clauses, a sentence, and the raw words.

    Every argument is whatever was on disk, including `None`: this is called over
    manifests written by older versions of the fetch stage, so an absent or
    unrecognised value has to produce a clause rather than a KeyError.
    """
    clauses = [
        _fetch_clause(fetch_status),
        _supplements_clause(supplementary_status),
        _extraction_clause(extract_status),
    ]
    levels = [c["level"] for c in clauses]
    return {
        "clauses": clauses,
        "summary": ", ".join(c["text"] for c in clauses),
        # The tokens the clauses were built from, for a tooltip or a `--raw` run.
        # Named as the fields they came from, because "partial partial_failure
        # partial" is exactly the string this module exists to stop printing.
        "raw": (f"fetch={fetch_status or '-'}  "
                f"supplementary={supplementary_status or '-'}  "
                f"extract={extract_status or '-'}"),
        # The worst clause, for sorting and for one colour on a whole row.
        "level": ("outstanding" if "outstanding" in levels
                  else "caution" if "caution" in levels else "ok"),
    }


def summarize(fetch_status, supplementary_status, extract_status) -> str:
    """`describe`'s sentence, for the callers that want nothing else."""
    return describe(fetch_status, supplementary_status, extract_status)["summary"]
