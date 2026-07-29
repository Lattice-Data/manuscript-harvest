"""Which part of the paper is this text in?

Section is the cheapest useful filter there is: library kit and organism live in
Methods, sample counts live in Results, and Introduction is mostly other
people's work -- text most likely to make a model attribute someone else's
perturbation to this paper.

Two independent things happen here:

- `normalize` maps a heading string a parser already found (JATS `<title>`, a
  docx Heading style) onto a canonical name.
- `spans` recovers headings from flowed PDF text, where the only signal left is
  that the heading sits alone on its line.
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


def spans(text: str) -> List[Tuple[int, str]]:
    """Locate canonical section headings in flowed text.

    Returns `[(character_offset, section_name), ...]` in document order, one entry
    per heading found on a line of its own. The caller assigns each paragraph the
    section of the nearest preceding entry, which is what makes a PDF paragraph
    attributable without the PDF having any structure.
    """
    found: List[Tuple[int, str]] = []
    for match in re.finditer(r"^[^\n]{1,%d}$" % _MAX_HEADING_CHARS, text, re.MULTILINE):
        name = normalize(match.group(0))
        if name:
            found.append((match.start(), name))
    return found


def section_at(offset: int, ordered_spans: List[Tuple[int, str]]) -> Optional[str]:
    """The section covering `offset`, given the output of `spans`."""
    current = None
    for start, name in ordered_spans:
        if start > offset:
            break
        current = name
    return current
