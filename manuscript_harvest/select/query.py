"""Filter, rank and pack blocks -- the machine-readable half of `extract show`.

`cmd_show` prints blocks to a terminal for a human, truncating at 600 characters
and defaulting `--limit` to 20. Nothing in the package returned them as data, so
anything asking a question of an article had to re-implement reading
`blocks.jsonl`, and every consumer would have re-implemented the section rule
below slightly differently.

**The section rule, which is the only subtle thing here.** A question about
methods wants Methods, so the obvious move is `section == "methods"` -- and on this
corpus that silently loses whole stretches of good articles. Corpus-wide 599 of
4,009 main-text paragraphs carry `section: null`, because an unrecognised heading
leaves the field unset rather than guessing. It is not confined to the broken
articles: 39 of the 86 main-text paragraphs of 10.1126/science.abo0510 are
unlabelled and that article is `ready` with no caveats at all, 36 of 46 on
10.1126/science.adf5357, and all 8 on 10.1038/s41586-024-08560-0. A hard section
filter answers "no data availability statement in this paper" for an article whose
data availability statement is sitting in an unlabelled block.

So `prefer` ranks rather than filters, in three tiers:

    0. the sections asked for
    1. `section is None`      <- unknown, not known-to-be-something-else
    2. everything else

Tier 1 sits above tier 2 deliberately: a null section is missing information about
a block, not information that the block is elsewhere. Nothing is ever dropped for
its section; only the character budget drops anything, and it says what.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

from ..extract import extractor
from ..extract.blocks import BLOCKS_NAME, TABLE, read_blocks

#: Enough of an article to answer a question, small enough to stay affordable. Not
#: a token count: tokenizers differ between models and this stage refuses to know
#: which model is downstream. Callers with a real token budget should pass their
#: own figure.
DEFAULT_BUDGET_CHARS = 60_000

#: A table card can be several thousand characters of column profile, and one card
#: is often the whole answer to "which samples, which assays" -- the card for
#: 10.1016/j.cell.2019.08.008's sample sheet is 3,683 characters and names the
#: tissue, patient, CyTOF and MICSSS columns for 29 samples. Cards are therefore
#: never truncated mid-card; a card that does not fit is dropped whole and counted,
#: because half a column list reads as a complete one.
MIN_CARD_HEADROOM = 400


@dataclass
class Pack:
    """Blocks chosen for one question, plus what was left out and why.

    `dropped` is not diagnostic detail. A pack that quietly lost the block holding
    the answer produces a confident wrong negative, so the count travels with the
    evidence and a consumer can say "answered from 40 of 61 candidate blocks".
    """

    blocks: List[dict] = field(default_factory=list)
    chars: int = 0
    considered: int = 0
    dropped_ids: List[str] = field(default_factory=list)
    dropped_chars: int = 0
    budget: int = DEFAULT_BUDGET_CHARS

    @property
    def dropped(self) -> int:
        return len(self.dropped_ids)

    @property
    def truncated(self) -> bool:
        return bool(self.dropped_ids)

    def to_dict(self) -> dict:
        return {"blocks": self.blocks, "chars": self.chars,
                "considered": self.considered, "dropped": self.dropped,
                "dropped_ids": self.dropped_ids,
                "dropped_chars": self.dropped_chars, "budget": self.budget,
                "truncated": self.truncated}


def blocks_path(article_dir) -> Path:
    return Path(article_dir) / extractor.EXTRACT_DIR / BLOCKS_NAME


def load(article_dir) -> List[dict]:
    """Every block of one article, in document order.

    Returns `[]` when the article is not extracted. That is not the same as an
    article with no blocks, and `readiness.assess` is what tells them apart -- this
    function deliberately does not, so a caller cannot get a bounded answer without
    having asked for one.
    """
    return list(read_blocks(blocks_path(article_dir)))


def select(blocks: Iterable[dict], kinds: Optional[Sequence[str]] = None,
           sections: Optional[Sequence[str]] = None,
           roles: Optional[Sequence[str]] = None,
           files: Optional[Sequence[str]] = None,
           contains: Optional[str] = None,
           exclude_sections: Optional[Sequence[str]] = None) -> List[dict]:
    """Hard filters, for when a caller really means to exclude.

    `sections` here *does* exclude, unlike `prefer`; the two are separate functions
    so that choosing between them is a decision rather than a flag someone forgets.
    `exclude_sections` is the useful one in practice: dropping `references` removes
    284 blocks of other people's titles corpus-wide, and an accession or a donor age
    found in a reference list belongs to a different paper.
    """
    kinds = frozenset(kinds) if kinds else None
    wanted = frozenset(sections) if sections else None
    unwanted = frozenset(exclude_sections) if exclude_sections else frozenset()
    roles = frozenset(roles) if roles else None
    needle = (contains or "").lower() or None

    out = []
    for block in blocks:
        if kinds and block.get("kind") not in kinds:
            continue
        if wanted and block.get("section") not in wanted:
            continue
        if block.get("section") in unwanted:
            continue
        if roles and block.get("role") not in roles:
            continue
        if files and not any(f in (block.get("source_file") or "") for f in files):
            continue
        if needle and needle not in (block.get("text") or "").lower():
            continue
        out.append(block)
    return out


def prefer(blocks: Sequence[dict], sections: Optional[Sequence[str]] = None) -> List[dict]:
    """Rank by section without dropping anything. See the module docstring.

    Document order is preserved inside each tier, so a pack reads top to bottom the
    way the article does. Called with no sections this is a copy, which is the
    honest behaviour for "no preference" and keeps callers from special-casing it.
    """
    if not sections:
        return list(blocks)
    wanted = frozenset(sections)

    def tier(block: dict) -> int:
        section = block.get("section")
        if section in wanted:
            return 0
        return 1 if section is None else 2

    return [b for _, b in sorted(enumerate(blocks),
                                 key=lambda pair: (tier(pair[1]), pair[0]))]


def pack(blocks: Sequence[dict], budget: int = DEFAULT_BUDGET_CHARS) -> Pack:
    """Take blocks in the order given, skipping any that will not fit the budget.

    Order is the caller's -- `prefer` first if section ranking is wanted -- because
    this function cannot know which blocks matter and will not guess.

    **Why it skips rather than stops.** Stopping at the first block too big to fit is
    the more order-faithful rule and is the wrong one: one 16,596-row supplementary
    sheet's card is several thousand characters, and a card ranked first that
    overruns the budget would take the entire rest of the article with it, returning
    an empty pack for an article that had the answer in its second block. Skipping
    keeps the budget useful.

    The cost is that a pack can hold a lower-ranked block while a higher-ranked one
    was dropped, which is exactly the kind of silent reordering that makes a wrong
    negative look considered. So `dropped_ids` records every block left out, not just
    how many: the reordering is auditable rather than merely counted, and a consumer
    that got "no accessions found" can see whether the data-availability block was
    one of the casualties.
    """
    result = Pack(budget=budget, considered=len(blocks))
    for block in blocks:
        text = block.get("text") or ""
        size = len(text)
        headroom = budget - result.chars
        fits = size <= headroom
        if block.get("kind") == TABLE and headroom < MIN_CARD_HEADROOM:
            fits = False
        if not fits:
            result.dropped_ids.append(block.get("block_id") or "")
            result.dropped_chars += size
            continue
        result.blocks.append(block)
        result.chars += size
    return result


def by_id(blocks: Iterable[dict]) -> dict:
    """`{block_id: block}`, for resolving a claim's citation back to its text.

    Duplicate ids would make a verification silently check the wrong block, and
    `number_blocks` exists to guarantee there are none -- its occurrence ordinal was
    added after `(source_file, locator, text_sha256)` was found to collide 416 times
    in one article. Trusting that here rather than re-checking it keeps the
    guarantee in one place.
    """
    return {block["block_id"]: block for block in blocks if block.get("block_id")}


def provenance(block: dict) -> dict:
    """The subset of a block worth carrying beside a claim made from it.

    Not the text: a record that repeats the paragraph it cites is most of a second
    copy of the article. `block_id` is the handle, and `by_id` turns it back into
    text when something needs to check the quote.
    """
    return {"block_id": block.get("block_id"),
            "kind": block.get("kind"),
            "role": block.get("role"),
            "section": block.get("section"),
            "source_file": block.get("source_file"),
            "locator": block.get("locator"),
            "label": block.get("label")}


def dump(pack_or_blocks, indent: int = 2) -> str:
    """JSON for a pack or a block list, sorted and stable.

    Sorted keys and no timestamps for the same reason `blocks.jsonl` has them: a
    pack produced twice from the same extraction is byte-identical, so a change in
    what a question sees is reviewable as a diff rather than inferred from a
    different answer.
    """
    value = pack_or_blocks.to_dict() if isinstance(pack_or_blocks, Pack) else pack_or_blocks
    return json.dumps(value, indent=indent, ensure_ascii=False, sort_keys=True,
                      allow_nan=False)
