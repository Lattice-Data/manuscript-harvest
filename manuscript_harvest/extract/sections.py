"""Which part of the paper is this text in?

Section is the cheapest useful filter there is: library kit and organism live in
Methods, sample counts live in Results, and Introduction is mostly other
people's work -- text most likely to make a model attribute someone else's
perturbation to this paper.

Three things happen here:

- `normalize` maps a heading string a parser already found (JATS `<title>`, a
  docx Heading style) onto a canonical name.
- `split_leading_heading` recovers a heading glued to the front of a paragraph,
  which is how Nature's PDFs lay them out.
- `SectionTracker` carries a heading's section forward over the text that follows
  it -- and stops when carrying it further would be a guess.

The section a block is *not* given matters as much as the one it is. A wrong
section is worse than none, because it makes a downstream filter drop the text it
was looking for while reporting a confident answer.
"""

import re
from typing import List, Optional, Tuple

ABSTRACT = "abstract"
INTRODUCTION = "introduction"
METHODS = "methods"
RESULTS = "results"
DISCUSSION = "discussion"
CONCLUSIONS = "conclusions"
FIGURE_LEGENDS = "figure_legends"
SUPPLEMENTARY = "supplementary"
DATA_AVAILABILITY = "data_availability"
BACK_MATTER = "back_matter"
REFERENCES = "references"

# Sections whose text is rarely worth sending to a model: it is either other
# people's findings or machine-readable bookkeeping.
#
# Membership here makes a *wrong* label expensive in a way it is nowhere else: a
# consumer that skips low-value sections does not merely deprioritise this text, it
# drops it. So `SectionTracker` will only carry one of these onto a block that
# looks like the section's own content.
LOW_VALUE = frozenset({REFERENCES})

# The star in Cell Press's "STAR★METHODS". Written with the glyph rather than an
# ASCII asterisk in both the XML and the PDF, and the alias below has to match what
# publishers ship. A small set, because the glyph varies between journals.
_STAR = r"[*★☆✪✩]"

# Ordered: the first pattern that matches a heading wins, so specific phrases
# ("results and discussion", "online methods") must precede the generic word.
_ALIASES: List[Tuple[str, str]] = [
    (ABSTRACT, r"abstract|summary|graphical\s+abstract|one[-\s]sentence\s+summary"),
    (INTRODUCTION, r"introduction|background"),
    # `_STAR` and not a bare `\*`: Cell Press publishes the heading as
    # "STAR★METHODS" with U+2605 BLACK STAR, in the XML as well as the PDF, so the
    # ASCII asterisk this pattern used to allow matched the way the heading is
    # written about and not the way it is written. Measured on
    # 10.1016/j.cell.2025.05.027 and 10.1016/j.cell.2021.01.053: the top-level
    # Methods section of both went unrecognised, leaving 69 and 51 main-text blocks
    # unlabelled -- and in a STAR Methods paper the key resources table, which is
    # where the library kit and every antibody are written down, sits under it.
    # Every addition below is a real top-level heading from a file in this corpus
    # that `normalize` returned None for.
    (METHODS, r"(?:online|extended|supplement(?:al|ary)|detailed|expanded)?\s*"
              r"(?:star\s*" + _STAR + r"?\s*)?(?:materials?\s+and\s+)?methods?"
              r"|methods?\s+and\s+materials?"
              r"|experimental\s+(?:procedures?|methods?|design|model)"
              # Cell Press STAR Methods sub-headings, 10.1016/j.cell.2021.01.053 p.18
              r"|experimental\s+model(?:\s+and\s+subject\s+details?)?"
              r"|quantification\s+and\s+statistical\s+analysis"
              r"|supplement(?:al|ary)\s+experimental\s+procedures?"
              # Nature's short methods block, e.g. 10.1038/s41586-020-2157-4
              r"|methods?\s+summary"
              r"|materials?\s+and\s+methods?"
              r"|method\s+details?"
              r"|star\s*" + _STAR + r"?\s*methods?"),
    (RESULTS, r"results?\s+and\s+discussion|results?|findings"),
    (DISCUSSION, r"discussion"),
    (CONCLUSIONS, r"conclusions?|concluding\s+remarks"),
    (FIGURE_LEGENDS, r"(?:supplementary\s+|extended\s+data\s+)?figure\s+legends?"
                     r"|legends?\s+(?:to|for)\s+figures?"),
    # Cell Press writes "Supplemental Information", not "Supplementary".
    (SUPPLEMENTARY, r"supplement(?:al|ary)\s+"
                    r"(?:information|material|data|notes?|methods?|results?)"
                    r"|extended\s+data|supporting\s+information"),
    (DATA_AVAILABILITY, r"(?:(?:data|code|materials?|software)\s+(?:and\s+\w+\s+)?availability"
                        r"|availability\s+of\s+(?:data|code)(?:\s+and\s+materials?)?)"
                        r"(?:\s+statements?)?"
                        r"|accession\s+(?:codes?|numbers?)"),
    # `references and notes` must come first: `normalize` would backtrack past a
    # bad ordering because of the `$` anchor, but `_leading_patterns` has no
    # anchor, so with the bare `references?` first a glued Science bibliography
    # splits as heading "REFERENCES" and rest "AND NOTES 1. K. W. Wucherpfennig".
    (REFERENCES, r"references?\s+and\s+notes"
                 r"|references?|bibliography|literature\s+cited|works\s+cited"),
    (BACK_MATTER, r"acknowledge?ments?|author\s+contributions?|competing\s+interests?"
                  r"|conflicts?\s+of\s+interest|funding|ethics\s+\w+|declarations?"
                  r"|additional\s+information|reporting\s+summary|abbreviations"),
]

# JATS carries the answer directly in `sec-type` often enough to be worth using.
_SEC_TYPES = {
    "intro": INTRODUCTION, "introduction": INTRODUCTION,
    "methods": METHODS, "materials|methods": METHODS, "methods|materials": METHODS,
    "materials-and-methods": METHODS, "subjects|methods": METHODS,
    "results": RESULTS, "results|discussion": RESULTS,
    "discussion": DISCUSSION, "conclusions": CONCLUSIONS,
    "supplementary-material": SUPPLEMENTARY, "abstract": ABSTRACT,
    "data-availability": DATA_AVAILABILITY, "availability": DATA_AVAILABILITY,
    "COI-statement": BACK_MATTER, "acknowledgement": BACK_MATTER,
    "funding-information": BACK_MATTER, "ref-list": REFERENCES,
}

_MAX_HEADING_CHARS = 120
"""Longer than this and it is a sentence that happens to start with a keyword."""


#: Optional section numbering ("2.", "2.1)", "IV.") or a bullet glyph. The bare
#: `d` is Cell Press's bullet as PyMuPDF renders it: page 18 of
#: 10.1016/j.cell.2021.01.053 emits `d KEY RESOURCES TABLE`,
#: `d EXPERIMENTAL MODEL AND SUBJECT DETAILS`, `d METHOD DETAILS` and
#: `d QUANTIFICATION AND STATISTICAL ANALYSIS` -- 9 such blocks in that file. It
#: is safe only because the body must still match in full afterwards, so the four
#: bulleted highlight lines on page 2 ("d Detailed COVID-19 immune landscape
#: depicted by") are still not headings.
_HEADING_PREFIX = r"(?:(?:\d+(?:\.\d+)*|[IVXLC]+|[d●▪•⁃])\s*[.)]?\s*)?"


def _compiled() -> List[Tuple[str, re.Pattern]]:
    out = []
    for name, body in _ALIASES:
        out.append((name, re.compile(
            rf"^\s*{_HEADING_PREFIX}(?:{body})\s*[:.]?\s*$", re.IGNORECASE)))
    return out


_PATTERNS = _compiled()


def normalize(heading: Optional[str], sec_type: Optional[str] = None) -> Optional[str]:
    """Canonical section name for a heading, or None if it is not a known one.

    An unrecognised heading is left as None on purpose. Guessing would put
    "Single-cell profiling of pancreatic islets" into `results` when it is just
    as likely to be a Methods subsection.
    """
    if sec_type:
        mapped = _SEC_TYPES.get(sec_type.strip())
        if mapped:
            return mapped
    if not heading:
        return None
    text = re.sub(r"\s+", " ", heading).strip()
    if not text or len(text) > _MAX_HEADING_CHARS:
        return None
    for name, pattern in _PATTERNS:
        if pattern.match(text):
            return name
    return None


def looks_like_heading(line: str) -> bool:
    """True for a line that is plausibly a heading of any kind, known or not.

    Used only to decide a block's `kind`; the section stays None unless
    `normalize` recognises it.
    """
    text = line.strip()
    if not text or len(text) > _MAX_HEADING_CHARS or text.endswith((".", ",", ";")):
        return False
    if normalize(text):
        return True
    words = text.split()
    if not 1 <= len(words) <= 12:
        return False
    # Title Case or ALL CAPS, and no sentence punctuation inside.
    alpha = [w for w in words if w[:1].isalpha()]
    if not alpha:
        return False
    if text.isupper():
        return True
    return sum(1 for w in alpha if w[:1].isupper()) >= max(2, int(0.8 * len(alpha)))


def _leading_patterns() -> List[Tuple[str, re.Pattern]]:
    out = []
    for name, body in _ALIASES:
        # The heading itself is case-insensitive, the lookahead deliberately is
        # NOT: under a global re.IGNORECASE, `[A-Z]` also matches lower case, and
        # the "followed by a capital" guard silently stops guarding anything.
        out.append((name, re.compile(rf"^\s*(?i:{body})\b\s*[:.]?\s+(?=[A-Z])")))
    return out


_LEADING = _leading_patterns()

_MIN_GLUED_REMAINDER = 40
"""Below this the block is a heading with a stray word, not a glued paragraph."""


def split_leading_heading(text: str) -> Optional[Tuple[str, str, str]]:
    """Split a heading off the front of a paragraph: `(section, heading, rest)`.

    Nature's PDFs put the section heading in the same layout block as the
    paragraph that follows it -- "Methods Data collection Nuclei isolation from
    adult heart tissue..." is one block -- so a whole-line match finds no sections
    at all in them. Wiley keeps the heading in its own block; both shapes occur.

    The guard against splitting an ordinary sentence is that a heading is followed
    by the start of a new one: "Results Overview of the experimental approach"
    splits, "Results of the assay were consistent" does not, because `of` is
    lower case.
    """
    for name, pattern in _LEADING:
        match = pattern.match(text)
        if not match:
            continue
        heading = text[: match.end()].strip(" :.")
        rest = text[match.end():].strip()
        if len(rest) < _MIN_GLUED_REMAINDER:
            return None
        return name, heading, rest
    return None


# Sections that are a *statement* rather than a body of the paper. Wherever they
# appear they run a paragraph or two: an abstract, a closing conclusion, a data
# availability sentence naming an accession. Everything else here -- methods,
# results, discussion, introduction, references, back matter -- legitimately runs
# for pages, and back matter in particular is long in Nature journals (author
# lists, affiliations, contributions, competing interests: 15,600 characters in
# 10.1038/s41588-025-02433-6), so it is deliberately not in this set.
BOUNDED_SECTIONS = frozenset({ABSTRACT, CONCLUSIONS, DATA_AVAILABILITY})

# What a reference-list entry looks like: numbered, or carrying a year in
# parentheses, or an "et al.", or a DOI. Any one is enough, and none of them
# appears in a row of a key resources table.
_CITATION = re.compile(
    r"^\s*\d{1,4}\s*[.)]\s+\S"
    r"|\(\d{4}[a-z]?\)"
    r"|\bet\s+al\b"
    r"|\bdoi\s*:|doi\.org/",
    re.IGNORECASE,
)


def looks_like_citation(text: str) -> bool:
    """Is this block plausibly an entry in a reference list?

    Used only to decide whether a `references` heading is still describing what
    follows -- see `SectionTracker.carry`.
    """
    return bool(_CITATION.search(text))


MAX_BOUNDED_SECTION_CHARS = 6000
"""How far a `BOUNDED_SECTIONS` heading may carry before it is abandoned.

Chosen against measurement rather than taste. The longest *legitimate* run seen
over the ground-truth papers is 4,653 characters -- a Cell Press abstract plus its
highlights and eTOC blurb, in 10.1016/j.xgen.2026.101304 -- and the shortest
pathological one is 6,294. This sits between them.
"""


class SectionTracker:
    """Carry a heading's section over the text that follows it, and know when to stop.

    A heading assigns its section to everything up to the next heading, because in
    a flowed PDF that is the only structure left. The failure this class exists for
    is what happens when the next heading is never *recognised*.

    Measured on the ground-truth papers, all three from a heading that was never
    followed by another one this module knows:

        10.1126/science.adt8307   996 of 1,184 main-text blocks labelled
                                  `conclusions`, from the standalone `CONCLUSION`
                                  line in Science's front-page structured summary.
                                  The paper's real Results reported 5 blocks.
        10.1126/science.aat5031   47 blocks labelled `abstract`, including Results
                                  prose four pages later.
        10.1038/s41588-025-02433-6  9,419 characters under `data_availability`.

    Nothing was broken: the carry-forward did exactly what it was written to do.
    The claim was simply larger than the evidence, and `section` is a filter, so a
    confident wrong label costs more than no label -- a search for `results` on the
    first paper above would find five blocks out of a thousand and report success.

    So a bounded section is abandoned once it has carried more text than that kind
    of section ever contains, and the blocks after it are left unlabelled with the
    abandonment recorded. `abandoned` is what the caller reports. An unbounded
    section still flows through its own unrecognised subsection headings, which is
    the behaviour that makes Methods attributable at all.

    A `LOW_VALUE` section is handled differently again, because being wrong about
    one costs more. Measured on 10.1016/j.cell.2025.05.027, a PMC author manuscript:
    the `REFERENCES` heading on page 31 carried 227 of 415 blocks to the end of the
    document, which in that layout is the **key resources table** --

        Punch pliers Total Tools 9070220SB
        micro-Slide 8-well cell culture chamber ibidi 80841
        40 um strainer Cell Strainer PN 43-10040-40

    -- the reagents, kits and antibodies this pipeline exists to find, labelled as
    other people's bibliography. A consumer that skips low-value sections does not
    deprioritise that text, it drops it. A character budget is the wrong instrument
    (a real reference list is legitimately enormous), so these sections are carried
    only onto blocks that look like their own content, and the span stays open so a
    genuine citation after a stray line is still labelled.
    """

    def __init__(self, max_bounded_chars: int = MAX_BOUNDED_SECTION_CHARS):
        self.max_bounded_chars = max_bounded_chars
        self.current: Optional[str] = None
        self.seen: List[str] = []
        """Canonical names met, in document order, deduplicated."""
        self.abandoned: List[str] = []
        """Bounded sections that ran too long to keep claiming."""
        self.withheld = 0
        """Blocks under a LOW_VALUE heading that did not look like its content."""
        self.reopens_refused: List[str] = []
        """Abandoned sections a later heading of the same name tried to reopen."""
        self._carried = 0

    def heading(self, name: str) -> Optional[str]:
        """Open `name` as the current section, and return it for the heading block.

        An abandoned section stays abandoned. Measured on 10.1126/science.aat5031:
        `abstract` runs past its budget at block 33, and then block 70 -- the
        heading `One Sentence Summary`, an ABSTRACT alias -- reopened it, so
        blocks 70-85 came back labelled `abstract`: 16 blocks and 6,272
        characters, including four figure legends totalling 2,844 characters
        beginning "Fig. 1. Mapping the spatial and temporal architecture of the
        mature and developing human kidney". Meanwhile the record said "the
        blocks after it are left unlabelled", which was false for 16 of them.
        """
        if name in self.abandoned:
            self.current = None
            if name not in self.reopens_refused:
                self.reopens_refused.append(name)
            return None
        self.current = name
        self._carried = 0
        if name not in self.seen:
            self.seen.append(name)
        return name

    def carry(self, text: str) -> Optional[str]:
        """The section to label `text` with. Call once per block, in document order.

        The block that crosses the budget keeps its label and the one after it does
        not: an abstract a few hundred characters over is still an abstract, and
        cutting mid-run would be a third answer that is right about neither.
        """
        if self.current is None:
            return None
        if self.current in LOW_VALUE:
            # Positive evidence per block, not a budget. The span stays open, so a
            # citation following a stray line is still labelled.
            if looks_like_citation(text):
                return self.current
            self.withheld += 1
            return None
        if self.current not in BOUNDED_SECTIONS:
            return self.current
        if self._carried > self.max_bounded_chars:
            if self.current not in self.abandoned:
                self.abandoned.append(self.current)
            self.current = None
            return None
        self._carried += len(text)
        return self.current

    def reason(self) -> Optional[str]:
        """One line for the extraction record, or None if there is nothing to report."""
        parts = []
        if self.abandoned:
            parts.append(
                f"section labelling stopped inside {', '.join(self.abandoned)}: more than "
                f"{self.max_bounded_chars} characters ran under a heading that names a "
                f"statement, so the blocks after it are left unlabelled rather than "
                f"attributed to it")
        if self.withheld:
            parts.append(
                f"{self.withheld} block(s) under a low-value heading did not look like "
                f"its content and were left unlabelled rather than dropped with it")
        if self.reopens_refused:
            parts.append(
                f"a later heading tried to reopen {', '.join(self.reopens_refused)} "
                f"after it had been abandoned; it was left unlabelled instead")
        return "; ".join(parts) or None
