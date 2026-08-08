"""Check a quote against the block it claims to come from.

An answer that cites text which is not in the article is the one failure mode worth
catching mechanically, and it is cheap to catch: the quote either appears in the
block or it does not.

**Why it is checked against the block and not the article.** Confirming a quote is a
verbatim substring of a concatenated article is a much weaker statement than it
looks. It cannot tell whether the sentence came from Methods or from the peer-review
file bundled as a supplement, and both are "in the paper". Blocks carry
`source_file` and `locator` for exactly this reason, so a claim citing `block_id`
gets checked against that block's text alone -- and a quote that is real, but real
somewhere else in the article, comes back `wrong_block` rather than passing.

**Why normalisation is not optional.** The text a model reads has been through a PDF
or a JATS parser, and comparing with `==` fails on artefacts nobody meant:

- Publishers set en dashes in numeric ranges ("aged 40-84" vs "aged 40–84"), curly
  quotes in prose, non-breaking spaces before units, and soft hyphens inside
  hyphenated words -- all of which look identical and compare unequal.
- A PDF parser breaks lines wherever the column ended, so a quoted sentence carries
  newlines and runs of spaces the model did not reproduce.
- PMC full text drops superscripts, so the block holds "1 x 10cells" where the paper
  printed "1 x 10^6 cells". A model reading the block usually reproduces the artefact
  and matches exactly -- but one that silently repairs the exponent while quoting
  needs to still be verifiable.

So four levels are tried in order, and **which one succeeded is recorded**. A `fuzzy`
verification is a weaker claim than an `exact` one and has to read that way
downstream; collapsing them into a boolean would hide the difference at the moment
it matters. This is the step the v0.0.2 extraction prompt specified as required
post-processing, moved into code so it is the same every run and has tests.

The tolerance is deliberately calibrated for **sentence-length** quotes, which is
what an evidence quote is. Measured: the repaired-superscript sentence above matches
at `fuzzy` with coverage 0.985, while the 14-character fragment "1 x 10^6 cells"
against "1 x 10cells" reaches only 0.90 and is refused. That asymmetry is correct
rather than unfortunate -- on a short string, coverage stops distinguishing a
quotation from a coincidence, which is what `FUZZY_MIN_CHARS` makes explicit.

Nothing here drops a claim. It labels one, and what to do with a `quote_not_found`
is the consumer's decision -- demote it, re-ask, or discard -- because that policy
depends on a question this module knows nothing about.
"""

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Optional, Sequence

EXACT = "exact"
NORMALIZED = "normalized"
LOOSE = "loose"
FUZZY = "fuzzy"
NOT_FOUND = "quote_not_found"
WRONG_BLOCK = "wrong_block"
NO_SUCH_BLOCK = "no_such_block"
EMPTY_QUOTE = "empty_quote"

#: Verdicts that mean the quote is really in the cited block. Ordered weakest-last
#: so a consumer can pick a floor: `verdict in VERIFIED` accepts everything,
#: `verdict in {EXACT, NORMALIZED}` refuses the tolerant matches.
VERIFIED = (EXACT, NORMALIZED, LOOSE, FUZZY)

#: Below this share of the quote matched, a fuzzy result is not a match at all.
#: 0.92 rather than something looser because the artefacts this is meant to absorb
#: are small: a dropped superscript costs one character in a sentence of a hundred.
FUZZY_THRESHOLD = 0.92

#: A fuzzy match must also contain one unbroken run this long, as a share of the
#: quote. Without it, `get_matching_blocks` can reach 0.92 by summing dozens of
#: two-character fragments scattered through a paragraph -- every "the" and " of "
#: in a long block -- which is a coincidence rather than a quotation.
FUZZY_MIN_RUN = 0.4

#: Fuzzy matching is refused below this many comparable characters. On a short
#: string the thresholds stop meaning anything: the quote "abcd" against the text
#: "abXcd" scores coverage 1.00 and a longest run of 0.5, clearing both bars on what
#: is not a quotation at all. Roughly a short clause, which is the floor at which
#: character-level agreement is evidence of anything. Below it, a quote must match at
#: `loose` or better.
FUZZY_MIN_CHARS = 40

#: Characters publishers use where a model will have typed the plain ASCII form.
#: Dashes and quotes are the two that actually bite; the rest are here because they
#: cost nothing to fold and each has been seen in a corpus paragraph.
_FOLD = {
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-",
    "―": "-", "−": "-",
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"', "″": '"',
    "…": "...",
    " ": " ", " ": " ", " ": " ", " ": " ", " ": " ",
}

#: Zero-width characters that survive NFKC and make an identical-looking string
#: compare unequal. A soft hyphen inside a hyphenated word is the common one.
_STRIP = dict.fromkeys(map(ord, "​‌‍⁠﻿­"), None)

_NON_ALNUM = re.compile(r"[^0-9a-z]+")


def normalize(text: str) -> str:
    """The comparable form of a piece of text: NFKC, folded, whitespace collapsed.

    Case is *kept*. Accession numbers, gene symbols and HGVS notation all carry
    meaning in their case, and this function is used to compare quotes that may
    contain any of them -- `loosen` is where case goes, at the level where it has
    already been decided that precision is being traded for tolerance.
    """
    if not text:
        return ""
    folded = unicodedata.normalize("NFKC", text).translate(_STRIP)
    folded = "".join(_FOLD.get(character, character) for character in folded)
    return " ".join(folded.split())


def loosen(text: str) -> str:
    """Normalised, lowercased, and stripped of everything but letters and digits.

    This absorbs every difference in punctuation, spacing and case -- a soft hyphen
    inside "cell-type", a comma the model dropped, a capitalised sentence start --
    which is why a match at this level is reported as `loose` rather than as a
    verbatim quote. It does *not* absorb a character that is present on one side and
    absent on the other; that is what the fuzzy level is for.
    """
    return _NON_ALNUM.sub("", normalize(text).lower())


def find_quote(quote: str, text: str) -> tuple:
    """`(verdict, detail)` for one quote against one block's text.

    `detail` carries the fuzzy measurements when they were taken and is empty
    otherwise, so a record does not gain fields that mean nothing for the level that
    actually matched.
    """
    if not (quote or "").strip():
        return EMPTY_QUOTE, {}
    if quote in text:
        return EXACT, {}

    quote_n, text_n = normalize(quote), normalize(text)
    if quote_n and quote_n in text_n:
        return NORMALIZED, {}

    quote_l, text_l = loosen(quote), loosen(text)
    if quote_l and quote_l in text_l:
        return LOOSE, {}

    # Last resort, over the loosened forms so the run lengths are not measuring
    # punctuation. autojunk=False: the heuristic treats characters appearing in more
    # than 1% of a long sequence as junk, which for a 3,000-character block is every
    # vowel -- it was silently scoring real quotes at 0.0.
    if not quote_l or not text_l or len(quote_l) < FUZZY_MIN_CHARS:
        return NOT_FOUND, {}
    matcher = SequenceMatcher(None, quote_l, text_l, autojunk=False)
    matched = sum(block.size for block in matcher.get_matching_blocks())
    longest = matcher.find_longest_match(0, len(quote_l), 0, len(text_l)).size
    coverage = matched / len(quote_l)
    run = longest / len(quote_l)
    detail = {"coverage": round(coverage, 4), "longest_run": round(run, 4)}
    if coverage >= FUZZY_THRESHOLD and run >= FUZZY_MIN_RUN:
        return FUZZY, detail
    return NOT_FOUND, detail


def verify_quote(quote: str, block_id: Optional[str], blocks_by_id: dict,
                 search_all: bool = True) -> dict:
    """Verify one quote against the block it cites.

    `search_all` decides what happens when the cited block does not contain the
    quote but another block does. Looking is worth the cost: a model that cites the
    wrong `block_id` for text it genuinely read is making a different and much more
    recoverable mistake than one inventing a sentence, and `wrong_block` names it
    along with where the text actually is. Pass `False` to skip the sweep.
    """
    if block_id not in blocks_by_id:
        return {"block_id": block_id, "verdict": NO_SUCH_BLOCK, "verified": False}

    verdict, detail = find_quote(quote, blocks_by_id[block_id].get("text") or "")
    record = {"block_id": block_id, "verdict": verdict,
              "verified": verdict in VERIFIED}
    if detail:
        record.update(detail)
    if record["verified"] or not search_all:
        return record

    for other_id, block in blocks_by_id.items():
        if other_id == block_id:
            continue
        found, _ = find_quote(quote, block.get("text") or "")
        if found in VERIFIED:
            return {"block_id": block_id, "verdict": WRONG_BLOCK, "verified": False,
                    "found_in": other_id, "found_as": found}
    return record


def verify_claims(claims: Sequence[dict], blocks_by_id: dict,
                  search_all: bool = True) -> dict:
    """Verify every `{block_id, quote}` in a list of claims.

    A claim may carry several quotes under `evidence`; each is checked separately and
    the claim's own verdict is the weakest of them, because a claim resting partly on
    text that is not there is not partly true.

    Returns `{claims, counts, verified, unverified}`. `counts` is by verdict, which is
    the number worth watching over a batch: a run where `quote_not_found` climbs is a
    prompt regression, and one where `wrong_block` climbs is a citation bug, and the
    two want different fixes.
    """
    out = []
    counts: dict = {}
    for claim in claims:
        evidence = claim.get("evidence") or []
        if not evidence and claim.get("quote") is not None:
            evidence = [{"block_id": claim.get("block_id"), "quote": claim.get("quote")}]
        checked = [verify_quote(item.get("quote") or "", item.get("block_id"),
                                blocks_by_id, search_all=search_all)
                   for item in evidence]
        for item in checked:
            counts[item["verdict"]] = counts.get(item["verdict"], 0) + 1
        if not checked:
            verdict, verified = "no_evidence", False
            counts["no_evidence"] = counts.get("no_evidence", 0) + 1
        else:
            verified = all(item["verified"] for item in checked)
            verdict = _weakest(item["verdict"] for item in checked)
        out.append({**claim, "evidence_checked": checked,
                    "verdict": verdict, "verified": verified})
    return {"claims": out, "counts": counts,
            "verified": sum(1 for c in out if c["verified"]),
            "unverified": sum(1 for c in out if not c["verified"])}


def _weakest(verdicts) -> str:
    """The least-good verdict in a set: unverified beats fuzzy beats exact.

    Ranked rather than compared so adding a level later means adding it to one
    tuple, and so an unrecognised verdict sorts to the end rather than being treated
    as strong.
    """
    order = VERIFIED + (WRONG_BLOCK, NO_SUCH_BLOCK, EMPTY_QUOTE, NOT_FOUND)
    return max(verdicts, key=lambda v: order.index(v) if v in order else len(order))
