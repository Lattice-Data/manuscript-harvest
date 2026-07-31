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
LOW_VALUE = frozenset({REFERENCES})

# Ordered: the first pattern that matches a heading wins, so specific phrases
# ("results and discussion", "online methods") must precede the generic word.
_ALIASES: List[Tuple[str, str]] = [
    (ABSTRACT, r"abstract|summary|graphical\s+abstract|one[-\s]sentence\s+summary"),
    (INTRODUCTION, r"introduction|background"),
    (METHODS, r"(?:online|extended|supplementar\w+|detailed|expanded)?\s*"
              r"(?:star\s*\*?\s*)?(?:materials?\s+and\s+)?methods?"
              r"|methods?\s+and\s+materials?"
              r"|experimental\s+(?:procedures?|methods?|design|model)"
              r"|materials?\s+and\s+methods?"
              r"|method\s+details?"
              r"|star\s*\*?\s*methods?"),
    (RESULTS, r"results?\s+and\s+discussion|results?|findings"),
    (DISCUSSION, r"discussion"),
    (CONCLUSIONS, r"conclusions?|concluding\s+remarks"),
    (FIGURE_LEGENDS, r"(?:supplementary\s+|extended\s+data\s+)?figure\s+legends?"
                     r"|legends?\s+(?:to|for)\s+figures?"),
    (SUPPLEMENTARY, r"supplementary\s+(?:information|material|data|notes?|methods?|results?)"
                    r"|extended\s+data|supporting\s+information"),
    (DATA_AVAILABILITY, r"(?:data|code|materials?|software)\s+(?:and\s+\w+\s+)?availability"
                        r"|availability\s+of\s+(?:data|code)"
                        r"|accession\s+(?:codes?|numbers?)"),
    (REFERENCES, r"references?|bibliography|literature\s+cited|works\s+cited"),
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


def _compiled() -> List[Tuple[str, re.Pattern]]:
    out = []
    for name, body in _ALIASES:
        # Optional section numbering ("2.", "2.1)", "IV."), optional trailing colon.
        out.append((name, re.compile(
            rf"^\s*(?:(?:\d+(?:\.\d+)*|[IVXLC]+)\s*[.)]?\s*)?(?:{body})\s*[:.]?\s*$",
            re.IGNORECASE)))
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
    """

    def __init__(self, max_bounded_chars: int = MAX_BOUNDED_SECTION_CHARS):
        self.max_bounded_chars = max_bounded_chars
        self.current: Optional[str] = None
        self.seen: List[str] = []
        """Canonical names met, in document order, deduplicated."""
        self.abandoned: List[str] = []
        """Bounded sections that ran too long to keep claiming."""
        self._carried = 0

    def heading(self, name: str) -> Optional[str]:
        """Open `name` as the current section, and return it for the heading block."""
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
        if self.current is None or self.current not in BOUNDED_SECTIONS:
            return self.current
        if self._carried > self.max_bounded_chars:
            if self.current not in self.abandoned:
                self.abandoned.append(self.current)
            self.current = None
            return None
        self._carried += len(text)
        return self.current

    def reason(self) -> Optional[str]:
        """One line for the extraction record, or None if nothing was abandoned."""
        if not self.abandoned:
            return None
        return (f"section labelling stopped inside {', '.join(self.abandoned)}: more than "
                f"{self.max_bounded_chars} characters ran under a heading that names a "
                f"statement, so the blocks after it are left unlabelled rather than "
                f"attributed to it")
