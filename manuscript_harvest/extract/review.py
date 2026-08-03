"""What to ask a human about an extraction, and what to do with the answer.

Everything else in this package measures itself against the files. Some things
cannot be: whether a spreadsheet's first row is a header or a first data row,
whether the body of the article is actually here, whether a `.pptx` nobody can
parse holds the donor table. Those are cheap for a person and impossible for the
parser, so this module names them, ranks them, and applies the answers.

**What to ask, ranked by value per minute** -- the order the queue is built in:

1. **Table header rows.** Bounded (16 low-confidence cards over the six articles
   on this machine), about fifteen seconds each, and a wrong header silently
   corrupts every metadata answer drawn from that sheet.
2. **Is the article body actually here.** One yes/no per article. If it is wrong,
   every answer for that article is wrong.
3. **Files a human thinks do carry content.** A checkbox, and a rare one: every
   non-`ok` supplement in this corpus is a figure image, which is never queued.
   It matters at scale, not on this sample.
4. **Supplement label joins.** Fourth, because most of the win was the code fix
   that stopped the fetch transport's name being used as the publisher's.
5. **Section spans.** Last and narrowly scoped: `section_audit.py` already scores
   this wherever a JATS reference exists, so only ask where it cannot.
6. **Sign-off.** Always, always last: the container, not a competitor, and what
   makes the layer honest.

**Where the answer lives.** `reviews/<doi_slug>.json` at the repo root, beside the
existing `manual_fetch/` precedent. Three structural facts decide that:
`store.evict_article` deletes everything but `manifest.json`, so a review kept
beside the article dies with a budget eviction; `corpus/` is gitignored, so
curator labour would be uncommittable; and the extraction cache would ignore it.
A checked-in file at the root needs no `.gitignore` edit, no `store.py` edit, and
outlives eviction by construction.

Answers are appended, never rewritten -- the file is an audit log, and the last
non-stale answer for a key wins.
"""

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .blocks import TABLE, read_blocks
from .limits import Limits

REVIEW_DIR = "reviews"
REVIEW_FORMAT = 1

# -- what can be asked -------------------------------------------------------
MAIN_TEXT_PRESENT = "main_text_present"
TABLE_HEADER = "table_header"
FILE_HAS_CONTENT = "file_has_content"
SUPPLEMENT_LABEL = "supplement_label"
SECTION_SPAN = "section_span"
SIGN_OFF = "sign_off"

ITEM_KINDS = frozenset({MAIN_TEXT_PRESENT, TABLE_HEADER, FILE_HAS_CONTENT,
                        SUPPLEMENT_LABEL, SECTION_SPAN, SIGN_OFF})

#: What a human may say about one item.
VERDICTS = frozenset({"confirmed", "corrected", "cannot_tell"})

#: An article's review state.
STATES = frozenset({"unreviewed", "queued", "partially_reviewed", "reviewed", "stale"})

#: Per-file statuses that mean "a human might know whether this file holds
#: anything". Deliberately *not* `image_no_text` / `media_no_text` /
#: `data_file_skipped`: 76 of the 101 supplements in this corpus are figure
#: images, so queuing them would be three quarters of the work, and nobody can
#: judge a `.jpg` from its name.
QUEUEABLE_FAILURES = frozenset({
    "no_text", "no_text_scanned_pdf", "unsupported_format", "too_large",
    "unreadable", "missing", "parser_error",
})


def review_path(slug: str, config: Optional[dict] = None) -> Path:
    """`reviews/<slug>.json`, or wherever `extract.review_dir` points."""
    directory = ((config or {}).get("extract") or {}).get("review_dir", REVIEW_DIR)
    return Path(directory) / f"{slug}.json"


def read_review(path) -> Optional[dict]:
    target = Path(path)
    if not target.exists():
        return None
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except ValueError:
        return None


def empty_review(extraction: dict) -> dict:
    return {"review_format": REVIEW_FORMAT,
            "slug": extraction.get("slug"), "doi": extraction.get("doi"),
            "answers": [], "sign_off": None, "signed_manifest_sha256": None}


def card_fingerprint(card: dict) -> str:
    """Identifies the *shape* a human was looking at, not the bytes under it.

    A parser change that moves the header row changes this and not the file's
    sha, which is exactly the distinction `state_of` needs: the human's claim was
    about the bytes, so the override still applies, but the item comes back for
    another look.
    """
    payload = (f"{'|'.join(card.get('header') or [])}"
               f"#{card.get('header_row')}#{card.get('n_columns')}")
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _manifest_shas(manifest: Optional[dict]) -> Dict[str, str]:
    """path -> sha256, over everything the fetch manifest recorded."""
    shas: Dict[str, str] = {}
    for entry in [(manifest or {}).get("fulltext"), (manifest or {}).get("fulltext_xml")]:
        if entry and entry.get("path") and entry.get("sha256"):
            shas[entry["path"]] = entry["sha256"]
    for entry in (manifest or {}).get("supplementary") or []:
        if entry.get("path") and entry.get("sha256"):
            shas[entry["path"]] = entry["sha256"]
    return shas


def answer_key(kind: str, key: dict) -> str:
    """A stable string for one question, for matching answers to items."""
    return kind + "\x00" + json.dumps(key, sort_keys=True)


# -- the queue ---------------------------------------------------------------

def _main_text_question(extraction: dict, limits: Limits) -> Optional[dict]:
    main = extraction.get("main_text") or {}
    source = main.get("source")
    chars = main.get("chars") or 0
    sections = set(main.get("sections") or [])
    reasons = []
    if main.get("landing_page_only"):
        reasons.append("the main text is a saved landing page")
    if source is None:
        reasons.append("no PDF, no XML and no landing page")
    if main.get("status") != "ok":
        reasons.append(f"the main text file is {main.get('status')}")
    elif chars < 4 * limits.min_main_text_chars:
        reasons.append(f"only {chars} characters of main text")
    if main.get("origin") == "pdf" and not {"methods", "results"} & sections:
        reasons.append("no methods or results heading was recognised in the PDF")
    if not reasons:
        return None
    return {
        "kind": MAIN_TEXT_PRESENT,
        "key": {"path": main.get("path") or ""},
        "question": "Is the body of this article actually here?",
        "why": "; ".join(reasons),
        "body": _preview(main),
    }


def _preview(main: dict) -> str:
    return (f"source: {main.get('source')}  origin: {main.get('origin')}  "
            f"blocks: {main.get('blocks')}  chars: {main.get('chars')}\n"
            f"sections: {', '.join(main.get('sections') or []) or '(none)'}\n"
            f"{main.get('note') or ''}").strip()


def queue_for(extraction: dict, blocks_path, limits: Optional[Limits] = None,
              manifest: Optional[dict] = None) -> List[dict]:
    """Every question worth asking a human about one extraction, in order.

    A pure function of the record and the blocks: nothing here reads a review
    file, so the queue is the same whether or not anyone has answered yet. What
    has been answered is decided by `state_of`.
    """
    limits = limits or Limits.from_dict(extraction.get("limits"))
    shas = _manifest_shas(manifest)
    items: List[dict] = []

    question = _main_text_question(extraction, limits)
    if question:
        question["source_sha256"] = shas.get(question["key"]["path"])
        items.append(question)

    # -- table headers, the highest value per minute
    low_confidence = [b for b in read_blocks(blocks_path)
                      if b.get("kind") == TABLE
                      and (b.get("table") or {}).get("header_confidence") == "low"]
    kept = low_confidence[: limits.max_review_cards_per_article]
    for block in kept:
        card = block["table"]
        items.append({
            "kind": TABLE_HEADER,
            "key": {"source_file": block["source_file"], "locator": block["locator"]},
            "source_sha256": shas.get(block["source_file"]),
            "card_fingerprint": card_fingerprint(card),
            "question": "Which row holds the column names?",
            "why": "; ".join(card.get("notes") or []) or "header detected without "
                                                         "type-change confirmation",
            "body": block["text"],
        })

    by_path = {e["path"]: e for e in extraction.get("supplementary") or []}
    sniffed = set((extraction.get("review_signals") or {}).get("supplements_sniffed") or [])
    for path, entry in by_path.items():
        if entry.get("status") not in QUEUEABLE_FAILURES and path not in sniffed:
            continue
        items.append({
            "kind": FILE_HAS_CONTENT,
            "key": {"path": path},
            "source_sha256": shas.get(path),
            "question": "Does this file carry article evidence?",
            "why": entry.get("note") or f"status {entry.get('status')}",
            "body": f"{path}\nstatus: {entry.get('status')}  "
                    f"label: {entry.get('label')}\n{entry.get('caption') or ''}".strip(),
        })

    unjoined = [e for e in by_path.values()
                if e.get("status") == "ok" and e.get("label_source") != "jats"]
    if unjoined:
        items.append({
            "kind": SUPPLEMENT_LABEL,
            "key": {"path": extraction.get("blocks_path") or ""},
            "source_sha256": extraction.get("source_manifest_sha256"),
            "question": "Do these files have publisher names the extraction missed?",
            "why": f"{len(unjoined)} file(s) carry no JATS label",
            "body": "\n".join(f"{e['path']}  label={e.get('label')}  "
                              f"({e.get('label_source')})" for e in unjoined),
        })

    span = _section_span_question(extraction)
    if span:
        span["source_sha256"] = shas.get(span["key"]["source_file"])
        items.append(span)

    items.append({
        "kind": SIGN_OFF,
        "key": {"slug": extraction.get("slug")},
        "source_sha256": extraction.get("source_manifest_sha256"),
        "question": "Is this extraction fit to answer curation questions from?",
        "why": "the container for everything above",
        "body": "",
    })
    return items


def _section_span_question(extraction: dict) -> Optional[dict]:
    """Only where `section_audit.py` cannot score the labeller for free."""
    main = extraction.get("main_text") or {}
    signals = extraction.get("review_signals") or {}
    if main.get("origin") != "pdf" or signals.get("jats_reference_available"):
        return None
    blocks = signals.get("main_text_blocks") or 0
    unlabelled = signals.get("main_text_unlabelled") or 0
    fraction = unlabelled / blocks if blocks else 0.0
    if not main.get("sections_abandoned") and fraction <= 0.25:
        return None
    return {
        "kind": SECTION_SPAN,
        "key": {"source_file": main.get("path") or "",
                "locator": "main_text", "text_sha256": "", "ordinal": 0},
        "question": "Which section does the unlabelled body text belong to?",
        "why": (f"{unlabelled} of {blocks} main-text blocks are unlabelled"
                + (f"; abandoned {', '.join(main['sections_abandoned'])}"
                   if main.get("sections_abandoned") else "")),
        "body": _preview(main),
    }


def queue_truncated(extraction: dict, blocks_path,
                    limits: Optional[Limits] = None) -> int:
    """How many low-confidence cards the per-article cap left out of the queue.

    A cap is never silent, and this one is the difference between "there were 25
    to check" and "there were 25 of 60".
    """
    limits = limits or Limits.from_dict(extraction.get("limits"))
    total = sum(1 for b in read_blocks(blocks_path)
                if b.get("kind") == TABLE
                and (b.get("table") or {}).get("header_confidence") == "low")
    return max(0, total - limits.max_review_cards_per_article)


# -- feeding the answers back ------------------------------------------------

class Overrides:
    """The answers that apply to this extraction, indexed for the parsers.

    A correction that does not change the next extraction is a note, not a
    correction. This is the object every parser consults, threaded through as one
    optional keyword so that omitting it anywhere shows a curator a card they
    cannot answer.

    `stale_bytes` answers are dropped on load -- the file they were about has
    been re-fetched. `stale_shape` answers are kept: the human's claim is about
    the bytes, and a parser change moving a header row does not unmake it.
    """

    def __init__(self, answers: Optional[Dict[str, dict]] = None):
        self._answers = answers or {}
        self._applied = 0
        self._applied_kinds: Counter = Counter()

    @classmethod
    def load(cls, slug: str, manifest: Optional[dict],
             config: Optional[dict] = None) -> "Overrides":
        stored = read_review(review_path(slug, config))
        if not stored:
            return cls()
        shas = _manifest_shas(manifest)
        kept: Dict[str, dict] = {}
        for answer in stored.get("answers") or []:
            key = answer.get("key") or {}
            path = key.get("path") or key.get("source_file")
            recorded = answer.get("source_sha256")
            if recorded and path and shas.get(path) and shas[path] != recorded:
                continue
            # Appended, never rewritten: the last answer for a key wins.
            kept[answer_key(answer.get("kind", ""), key)] = answer
        return cls(kept)

    def applied(self) -> int:
        return self._applied

    def applied_kinds(self) -> Dict[str, int]:
        """`applied()` broken down by question kind, counted where each answer is
        consumed rather than where it was submitted.

        `review --apply` used to print this total beside a breakdown built from the
        *incoming* batch, which measures something else: on a second apply against
        an article with fourteen stored answers, the headline said 14 and the
        breakdown summed to 1.
        """
        return dict(self._applied_kinds)

    def _take(self, kind: str, key: dict) -> Optional[dict]:
        answer = self._answers.get(answer_key(kind, key))
        if answer is None or not answer.get("override"):
            return None
        self._applied += 1
        self._applied_kinds[kind] += 1
        return answer

    def note_for(self, answer: dict) -> str:
        """The card note for an applied override.

        A byte-stability trap: this is an f-string over values *read out of the
        review file*, never `datetime.now()`, because it lands in `blocks.jsonl`.
        """
        return (f"header confirmed by review: {answer.get('note') or 'no note'} "
                f"({answer.get('by')}, {answer.get('at')})")

    def header_for(self, source_file: str, locator: str) -> Optional[dict]:
        return self._take(TABLE_HEADER,
                          {"source_file": source_file, "locator": locator})

    def label_for(self, path: str) -> Optional[dict]:
        return self._take(SUPPLEMENT_LABEL, {"path": path})

    def content_expected(self, path: str) -> Optional[bool]:
        answer = self._take(FILE_HAS_CONTENT, {"path": path})
        return None if answer is None else answer["override"].get("has_content")

    def section_for(self, source_file: str, locator: str, text_sha256: str,
                    ordinal: int) -> Optional[str]:
        answer = self._take(SECTION_SPAN, {"source_file": source_file,
                                           "locator": locator,
                                           "text_sha256": text_sha256,
                                           "ordinal": ordinal})
        return None if answer is None else answer["override"].get("section")

    def main_text_source(self) -> Optional[str]:
        for identity, answer in self._answers.items():
            if answer.get("kind") == MAIN_TEXT_PRESENT and answer.get("override"):
                self._applied += 1
                self._applied_kinds[MAIN_TEXT_PRESENT] += 1
                return answer["override"].get("main_text_source")
        return None

    def evidence_denied(self) -> set:
        """Paths a human marked as not article evidence, e.g. a reporting summary."""
        return {a["key"].get("path") for a in self._answers.values()
                if a.get("kind") in {SUPPLEMENT_LABEL, FILE_HAS_CONTENT}
                and (a.get("override") or {}).get("evidence") is False
                and a["key"].get("path")}


# -- what has been answered, and what has gone stale -------------------------

def state_of(review: Optional[dict], extraction: dict,
             manifest: Optional[dict], queue: List[dict]) -> Tuple[str, List[dict]]:
    """`(state, stale)` for one article. `state` is from `STATES`.

    Two kinds of staleness, and they are not the same thing:

    - `stale_bytes` -- the file the human looked at has been re-fetched. The
      override is **not** applied; the item is re-queued with the previous answer
      shown as context.
    - `stale_shape` -- the bytes match but the card's fingerprint moved, so a
      parser change relocated the header. The override **is** applied, because
      the human's claim is about the bytes and not about the parser, but the item
      is re-queued and listed.
    """
    stale: List[dict] = []
    if not review or not review.get("answers"):
        answered = 0
    else:
        shas = _manifest_shas(manifest)
        fingerprints = {answer_key(i["kind"], i["key"]): i.get("card_fingerprint")
                        for i in queue}
        latest: Dict[str, dict] = {}
        for answer in review["answers"]:
            latest[answer_key(answer["kind"], answer["key"])] = answer
        answered = 0
        for identity, answer in latest.items():
            path = answer["key"].get("path") or answer["key"].get("source_file")
            recorded = answer.get("source_sha256")
            if recorded and path and shas.get(path) and shas[path] != recorded:
                stale.append({**answer, "why": "stale_bytes"})
                continue
            expected = fingerprints.get(identity)
            if expected and answer.get("card_fingerprint") \
                    and answer["card_fingerprint"] != expected:
                stale.append({**answer, "why": "stale_shape"})
            answered += 1

    queued = len([i for i in queue if i["kind"] != SIGN_OFF])
    sign_off = (review or {}).get("sign_off")
    signed = (review or {}).get("signed_manifest_sha256")
    if sign_off and signed and signed != extraction.get("source_manifest_sha256"):
        return "stale", stale
    if stale:
        return "stale", stale
    if sign_off:
        return "reviewed", stale
    if answered:
        return "partially_reviewed", stale
    return ("queued" if queued else "unreviewed"), stale
