"""Find the things a regex finds better than a model does.

The accession finder is the clearest case in this corpus, and measuring it is what
set the shape of this whole stage. Over the 27 development-corpus articles whose
text `readiness` says can be believed, it finds 69 distinct study-level accessions
in 21 of them, and it is close to exhaustive: these identifiers have rigid syntax,
so recall is a property of the pattern list rather than of anybody's judgement, and
a missing repository family is a fixable bug rather than a bad day.

What it cannot do is say what any of them *are*, and the gap is not small:

- 10.1002/ctm2.1356 -- five accessions found. One, GSE208532, is the paper's own
  deposit ("The dataset supporting the conclusions of this article is publicly
  available ... under the accession GSE208532"). The other four were downloaded as
  reference data for cell-type annotation. A finder that reports five gets a
  precision of 0.20.
- 10.1016/j.isci.2023.106877 -- ten accessions found, and every one is a public
  dataset the paper reanalysed. Precision 0.00.

Deciding `own` from `reused` needs the sentence around the identifier, which is a
judgement, which is why nothing in this module makes it. A candidate carries
`role: None` and the mentions that would let something else decide -- and that
split, code for the finding and judgement for the role, is what the whole stage is
arranged around.

The other half of the measurement matters as much: the remaining 6 of those 27
articles contain no study-level accession at all. Those are where a hand label is
nearly free and where `complete` in a truth file is doing all the work, since an
empty result from a broken pattern list and an empty result from a paper that
deposited nothing look identical from here.
"""

import re
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence

from ..extract.blocks import TABLE

ACCESSIONS = "accessions"

STUDY = "study"
SAMPLE = "sample"
"""The two levels an accession can name, and the distinction is load-bearing.

"Where did this paper deposit its data" is answered by a **study** accession --
one GSE, one BioProject, one ArrayExpress id. A **sample** accession names one
library inside such a study, and there are as many as there were samples.

They must not be pooled, for a reason that is about bounding rather than taste. On
10.1126/science.aat5031 the per-sample ids live in a supplementary table whose card
enumerates a column's distinct values only up to `limits.max_unique_values`, so the
finder sees `ERS3493332 (SAMEA5689352), ERS3493333 (SAMEA5689353), ...` and five
more of a column the card itself reports as holding 43. Whatever it returns is a
sample of an enumeration cap, not the paper's deposit -- and reporting five of 43
as though they were the answer is precisely the plausible-looking success both
earlier stages are arranged to refuse. So sample-level hits are found, marked, and
kept out of the set anything adjudicates.
"""

#: Repository patterns, by the name a curator would use, each with the level it
#: names. Every pattern anchors on `\b` at both ends and requires a minimum digit
#: count, because the prefixes are short enough to occur in prose: a bare `\d+` on
#: `syn` would match "syn" in a gene name, and four digits is the floor for a real
#: GEO series.
#:
#: Deliberately absent, each for a reason:
#: - **GPL** (GEO platform). A platform is an instrument, never a deposit, so every
#:   hit would be a false positive on the only question being asked.
#: - **Zenodo / figshare / Dryad**. Their identifiers are DOIs, so a pattern for
#:   them matches the article's own DOI and every DOI in its reference list. Telling
#:   a data DOI from a citation is a different problem from this one.
#: - **CELLxGENE / HCA dataset ids**. Bare UUIDs: no shape to key on, so a pattern
#:   would either miss them or match any hex string.
_PATTERNS = (
    ("GEO", r"GSE\d{4,}", STUDY),
    ("GEO", r"GSM\d{4,}", SAMPLE),
    ("SRA", r"SRP\d{5,}", STUDY),
    ("SRA", r"SR[RXS]\d{5,}", SAMPLE),
    ("ENA", r"ERP\d{5,}", STUDY),
    ("ENA", r"ER[RXS]\d{5,}", SAMPLE),
    ("DDBJ", r"DRA\d{5,}", STUDY),
    ("DDBJ", r"DR[RXS]\d{5,}", SAMPLE),
    ("BioProject", r"PRJ(?:NA|EB|DB|CA)\d{3,}", STUDY),
    ("BioSample", r"SAM[NED][A-Z]?\d{5,}", SAMPLE),
    ("ArrayExpress", r"E-[A-Z]{4}-\d+", STUDY),
    ("EGA", r"EGAS\d{6,}", STUDY),
    ("EGA", r"EGA[DN]\d{6,}", SAMPLE),
    ("dbGaP", r"phs\d{6}(?:\.v\d+(?:\.p\d+)?)?", STUDY),
    ("Synapse", r"syn\d{7,}", STUDY),
    ("GSA", r"(?:HRA|CRA|OEP)\d{6}", STUDY),
    ("ProteomeXchange", r"PXD\d{6}", STUDY),
    ("MassIVE", r"MSV\d{9}", STUDY),
    ("BioStudies", r"S-BSST\d+", STUDY),
    ("ImmPort", r"SDY\d{3,}", STUDY),
)

#: One alternation over every pattern, so a block is scanned once. Group names
#: cannot repeat and cannot hold a hyphen, so they are positional and
#: `_BY_GROUP` maps back to `(repository, level)`.
#:
#: `(?i)` is deliberately *not* set -- these identifiers are uppercase by convention
#: in every publisher's text, and matching case-insensitively turned "Gsm" inside
#: ordinary words into candidates without finding one additional real accession
#: across the corpus.
_FINDER = re.compile("|".join(
    f"(?P<g{index}>\\b{pattern}\\b)"
    for index, (_, pattern, _) in enumerate(_PATTERNS)))

_BY_GROUP = {f"g{index}": (repository, level)
             for index, (repository, _, level) in enumerate(_PATTERNS)}

#: How far to look either side of a hit for a sentence boundary. A data-availability
#: statement runs long -- 10.1002/ctm2.1356's is 261 characters -- and a cap keeps a
#: hit inside a wall of prose from returning the wall.
_SENTENCE_WINDOW = 400


@dataclass
class Candidate:
    """One distinct identifier, and everywhere it was said.

    Deduplicated by identifier rather than by mention on purpose: the label a human
    or a model applies is a property of the accession within this paper -- GSE208532
    is this paper's deposit no matter how many times it is named -- and one decision
    per accession is what makes the labelling pass affordable. The mentions are then
    the evidence for that one decision.
    """

    accession: str
    repository: str
    level: str = STUDY
    mentions: List[dict] = field(default_factory=list)
    role: Optional[str] = None
    """Always `None` from this module. `own` / `reused` / `not_an_accession` are
    assigned downstream, by a person in `sheet.py` or by whatever reads the
    mentions. Present in the dataclass so the two shapes match and a labelled
    candidate needs no conversion."""

    @property
    def sections(self) -> List[str]:
        """Distinct sections it was mentioned in, `-` standing for unlabelled.

        Worth having beside the sentence, because where an accession is said carries
        real signal: across the corpus 67 study-level mentions sit in `methods` and
        45 in `data_availability`, and the two read very differently -- a deposit is
        announced in the latter and other people's data is downloaded in the former.
        Signal, not a rule: 10.1002/ctm2.1356 announces its own GSE208532 in a data
        availability statement *and* names four downloaded datasets in Methods, but
        plenty of papers state their deposit in Methods and nowhere else.
        """
        seen = []
        for mention in self.mentions:
            section = mention.get("section") or "-"
            if section not in seen:
                seen.append(section)
        return seen

    def to_dict(self) -> dict:
        return {"accession": self.accession, "repository": self.repository,
                "level": self.level, "role": self.role, "sections": self.sections,
                "mentions": self.mentions}


def sentence_around(text: str, start: int, end: int, kind: Optional[str] = None) -> str:
    """The sentence containing `text[start:end]`, or the line for a table card.

    Table cards have no sentences -- a card is a column profile, one fact per line --
    so a sentence walk over one returns a paragraph of unrelated columns. The line
    is the right unit there and is what a human needs to see to judge the mention.
    """
    if kind == TABLE:
        line_start = text.rfind("\n", 0, start) + 1
        line_end = text.find("\n", end)
        return text[line_start:line_end if line_end != -1 else len(text)].strip()

    left = max(0, start - _SENTENCE_WINDOW)
    right = min(len(text), end + _SENTENCE_WINDOW)
    window = text[left:right]
    at = start - left

    # Walk out to the nearest boundary on each side. ". " rather than "." so that
    # "GSE208532." at the end of a statement and the "." in "e.g." or "10.1016"
    # are not both treated as stops -- a decimal point inside an accession's own
    # dbGaP version (phs000424.v8) would otherwise cut it in half.
    begin = 0
    for stop in (". ", ".\n", "? ", "! ", "\n\n"):
        found = window.rfind(stop, 0, at)
        if found != -1:
            begin = max(begin, found + len(stop))
    finish = len(window)
    for stop in (". ", ".\n", "? ", "! ", "\n\n"):
        found = window.find(stop, at)
        if found != -1:
            finish = min(finish, found + 1)
    return " ".join(window[begin:finish].split())


def find(blocks: Iterable[dict], repositories: Optional[Sequence[str]] = None,
         levels: Optional[Sequence[str]] = None) -> List[Candidate]:
    """Every distinct accession in these blocks, with its mentions.

    `blocks` is whatever the caller selected. Passing the whole article is the right
    default for this aspect -- see the section rule in `query` -- but excluding
    `references` is worth doing, since an accession in a reference list belongs to
    the paper being cited and not to this one.

    Ordered by first appearance rather than alphabetically: an article's own deposit
    is usually announced once, late, in a data-availability statement, and reading
    the candidates in document order keeps that statement's neighbours next to it.
    """
    wanted = frozenset(repositories) if repositories else None
    wanted_levels = frozenset(levels) if levels else None
    found: dict = {}
    order: List[str] = []
    for block in blocks:
        text = block.get("text") or ""
        for match in _FINDER.finditer(text):
            repository, level = _BY_GROUP[match.lastgroup]
            if wanted and repository not in wanted:
                continue
            if wanted_levels and level not in wanted_levels:
                continue
            accession = match.group()
            if accession not in found:
                found[accession] = Candidate(accession=accession,
                                             repository=repository, level=level)
                order.append(accession)
            found[accession].mentions.append({
                "block_id": block.get("block_id"),
                "section": block.get("section"),
                "role": block.get("role"),
                "kind": block.get("kind"),
                "source_file": block.get("source_file"),
                "locator": block.get("locator"),
                "sentence": sentence_around(text, match.start(), match.end(),
                                            block.get("kind")),
            })
    return [found[accession] for accession in order]


def by_level(found: Sequence[Candidate]) -> dict:
    """`{"study": [...], "sample": [...]}`, because only one of them gets labelled.

    Splitting here rather than at each call site keeps the reason in one place: the
    sample list is unboundable when it came out of a table card (see `SAMPLE`), so it
    is evidence a human may want to look at and never a set to adjudicate one by one.
    Labelling the 43 per-sample ids of 10.1126/science.aat5031 would also be most of
    an afternoon for an answer nobody asked for.
    """
    return {STUDY: [c for c in found if c.level == STUDY],
            SAMPLE: [c for c in found if c.level == SAMPLE]}


def naive_own(found: Sequence[Candidate]) -> List[str]:
    """The baseline worth beating: assume every study accession found is a deposit.

    This is what a pipeline does when nobody adjudicates the role, and it lives in
    the package rather than in a notebook so `eval --baseline` scores the real thing
    instead of a remembered figure.
    """
    return [c.accession for c in found if c.level == STUDY]
