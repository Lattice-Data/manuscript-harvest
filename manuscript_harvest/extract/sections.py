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
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .limits import Limits

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


#: Optional section numbering ("2.", "2.1)", "IV.", "2 |") or a bullet glyph. The
#: bare `d` is Cell Press's bullet as PyMuPDF renders it: page 18 of
#: 10.1016/j.cell.2021.01.053 emits `d KEY RESOURCES TABLE`,
#: `d EXPERIMENTAL MODEL AND SUBJECT DETAILS`, `d METHOD DETAILS` and
#: `d QUANTIFICATION AND STATISTICAL ANALYSIS` -- 9 such blocks in that file. It
#: is safe only because the body must still match in full afterwards, so the four
#: bulleted highlight lines on page 2 ("d Detailed COVID-19 immune landscape
#: depicted by") are still not headings.
#:
#: The `|` in the separator class is Wiley's, and it is the whole numbering scheme
#: rather than an ornament: `10.1002/pros.24020` writes every heading in the paper
#: as `1 | INTRODUCTION`, `2 | MATERIALS AND METHODS`, `2.1 | Human subjects`. With
#: only `[.)]` allowed after the number, not one of them matched, so that article's
#: real sections were invisible and every block from its abstract to page 21
#: carried a label from the *abstract's* `Conclusions` heading instead -- 693
#: blocks, none of them labelled `methods`.
_HEADING_PREFIX = r"(?:(?:\d+(?:\.\d+)*|[IVXLC]+|[d●▪•⁃])\s*[.)|]?\s*)?"


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
        #
        # A digit counts as the start of what follows, because a reference list
        # starts with one. With `[A-Z]` alone, `REFERENCES AND NOTES 1. K. W.
        # Wucherpfennig...` on page 83 of 10.1126/science.aat5031's supplement
        # could only split as heading `REFERENCES` and rest `AND NOTES 1. K. W.`,
        # leaving 19,265 characters of bibliography glued to the first citation.
        #
        # `_HEADING_PREFIX` is here for the same reason it is in `_compiled()`, and
        # its absence was a gap between the two: a heading that is *both* numbered
        # and glued matched neither path. `_compiled()` needs the whole line, so
        # `2. Materials and Methods 2.1. Study Population` fails its `$`; this
        # function needed the alias at offset zero, so the leading `2. ` failed it
        # too. Measured on 10.3390/genes15030298, an MDPI PDF that glues each
        # section heading to its first subheading: `2. Materials and Methods ...`
        # and `3. Results ...` both fell through, so every block from 2.1 to 3.4 --
        # the entire Methods and Results, two thirds of the paper -- carried the
        # `introduction` label from the heading before them. Coverage read 96%
        # because the labels were all there and all wrong, which is the failure
        # mode a coverage number cannot see.
        out.append((name, re.compile(
            rf"^\s*{_HEADING_PREFIX}(?i:{body})\b\s*[:.]?\s+(?=[A-Z0-9])")))
    return out


_LEADING = _leading_patterns()

_MIN_GLUED_REMAINDER = 40
"""Below this the block is a heading with a stray word, not a glued paragraph."""


_TRAILING_LINE_NUMBER = re.compile(r"\s+\d{1,4}\s*$")


def strip_line_number(text: str) -> str:
    """Drop the line number a line-numbered manuscript PDF puts on every line.

    A manuscript submitted for review carries its own line numbering, and PyMuPDF
    reads that number as the last word of the line: 10.21203/rs.3.rs-7535904_v2
    yields `Discussion 361` and `Methods 606`, 10.1101/2022.05.18.492547 the same
    shape. Both fell through every matcher here -- `normalize` anchors on `$` so
    the trailing number breaks a full-line match, and `split_leading_heading` sees
    a 3-character remainder and reads it as a stray word -- which is why those two
    articles came out with 12% and 0% of their characters labelled and no `methods`
    label anywhere.

    Only ever applied to a *probe* copy of the text, and only for a document
    `pdf._line_numbered` has already identified, never to the text that is stored.
    That guard is the whole safety of it: the same regex over an ordinary PDF would
    eat the `1` off `Extended Data Fig. 1` and the year off a citation. See the
    threshold measurement on `pdf._line_numbered`.
    """
    return _TRAILING_LINE_NUMBER.sub("", text)


#: `_CITATION` below, minus its numbered-entry rule: a parenthesised year, an
#: `et al.`, a DOI. Kept separate rather than reusing `_CITATION` because the two
#: questions differ by exactly that rule -- `SectionTracker.carry` wants to know
#: whether a block is a reference, and a bare `12. Foo` is; `split_leading_heading`
#: wants to know whether a *numbered* line is a reference rather than a heading, and
#: there the numbering is the thing both shapes have in common.
_CITATION_BESIDES_NUMBERING = re.compile(
    r"\(\d{4}[a-z]?\)"
    r"|\bet\s+al\b"
    r"|\bdoi\s*:|doi\.org/",
    re.IGNORECASE,
)

#: Does the heading that just matched owe the match to `_HEADING_PREFIX`? Only those
#: are newly reachable, so only those need the citation guard above.
_NUMBERED_HEADING = re.compile(r"^\s*(?:\d+(?:\.\d+)*|[IVXLC]+)\s*[.)]?\s")


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
        # Allowing `_HEADING_PREFIX` above widened this function onto reference-list
        # entries, which begin with a number too: `3. Discussion Of Sampling Bias In
        # Cohort Studies, Am J Epidemiol (2021).` would otherwise split as the
        # heading `3. Discussion` and open that section over the bibliography, and a
        # heading's section carries forward. `looks_like_citation` is not usable on
        # its own here -- its numbered-entry rule matches the real MDPI headings
        # this change exists for -- so the test is the *other* citation signals: a
        # parenthesised year, an `et al.`, a DOI.
        #
        # Applied only where the numbering is what let the match through, and only
        # off the reference list. Both halves are load-bearing. Restricting it to
        # numbered headings leaves every split that worked before this change
        # working exactly as it did; exempting `references` is what keeps
        # `REFERENCES AND NOTES 1. K. W. Wucherpfennig, ... (2001).` splitting,
        # where a citation after the heading is evidence *for* the split rather
        # than against it, and where refusing costs 19,265 characters.
        if (name != REFERENCES and _NUMBERED_HEADING.match(heading)
                and _CITATION_BESIDES_NUMBERING.search(text)):
            return None
        # A short remainder is normally a heading with a stray word on the end, and
        # splitting there would invent a paragraph out of nothing. The exception is
        # a remainder that is itself a heading: MDPI glues a section heading to its
        # first *subheading*, so 10.3390/genes15030298 carries
        # `2. Materials and Methods 2.1. Study Population` -- 21 characters of
        # remainder, under the bound, and every one of them a heading rather than a
        # stray word. Refusing that split is what left the paper's Methods labelled
        # `introduction`.
        if len(rest) < _MIN_GLUED_REMAINDER and not looks_like_heading(rest):
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

# The sections a paper's own body is built out of, as opposed to the statements and
# apparatus around it. Only these are what a structured abstract imitates.
BODY_SECTIONS = frozenset({ABSTRACT, INTRODUCTION, METHODS, RESULTS,
                           DISCUSSION, CONCLUSIONS})

# Body sections a paper reaches only at the end. Seeing one of these on page 1
# *together with* an earlier body section is the signal `structured_abstract`
# turns on, because a paper cannot have both its opening and its closing section
# on its first page -- but its abstract can.
_TERMINAL_SECTIONS = frozenset({DISCUSSION, CONCLUSIONS})

# Introduction is the one section a paper never comes back to, which makes a
# second one evidence about the heading rather than about the paper. See
# `SectionTracker.heading`.
_AFTER_INTRODUCTION = frozenset({METHODS, RESULTS, DISCUSSION, CONCLUSIONS})


@dataclass(frozen=True)
class StructuredAbstract:
    """Where a front-page summary is, if the first page holds one.

    `headings` holds the caller's keys for the headings that belong to the summary
    rather than to the paper, and `page` is the page the summary ends on -- None
    when there is no summary, which is the "nothing to do" answer the caller tests
    for.
    """

    headings: frozenset = frozenset()
    page: Optional[int] = None


def structured_abstract(recognised) -> StructuredAbstract:
    """Which of a PDF's recognised headings are a structured abstract's, not the paper's.

    A structured abstract labels its own paragraphs with the paper's section
    names -- `BACKGROUND`, `METHODS`, `RESULTS`, `CONCLUSIONS` -- and Science
    prints a whole "RESEARCH ARTICLE SUMMARY" page in that shape before the
    article starts. Nothing in the *text* of those headings distinguishes them
    from the real ones, so the labeller opened `conclusions` on page 1 and carried
    it over the paper. `SectionTracker`'s character budget was the mitigation and
    is not a fix: it stops the bleeding 6,000 characters in, and everything before
    that is still labelled from the abstract while everything after is labelled
    not at all.

    What does distinguish them is that they are all on page 1. A paper cannot
    open *and* close on its first page, so a first page that declares a terminal
    section (`discussion`, `conclusions`) alongside an earlier body section is
    summarising rather than sectioning.

    Measured over the 124 PDF-sourced articles in the development corpus: 15
    match, and they are exactly the 15 that have one --

        10.1161/atvbaha.122.317953   BACKGROUND METHODS RESULTS CONCLUSIONS
        10.1002/pros.24020           Abstract Background Methods Results Conclusions
        10.1164/rccm.202207-1384oc   Abstract Methods Conclusions
        10.1126/science.*  (12)      INTRODUCTION RESULTS CONCLUSION

    -- while 10.1126/sciimmunol.abe6291, whose real `INTRODUCTION` and `RESULTS`
    genuinely are both on page 1, does not, because it never reaches a terminal
    section there. That article is the reason the rule is "declares an ending"
    rather than "declares two sections".

    `recognised` is `(key, page, section)` for every heading the caller's own
    matcher recognised, in document order; `key` is whatever the caller wants to
    look the answer up by. The walk stops at the first heading that is not a body
    section, because a summary contains no Data Availability statement and no
    reference list -- those belong to the paper, and in
    10.1164/rccm.202207-1384oc the `Author Contributions` heading that follows the
    abstract on page 1 is the paper's, not the abstract's.
    """
    first_page = [(key, section) for key, page, section in recognised if page == 1]
    run = []
    for key, section in first_page:
        if section not in BODY_SECTIONS:
            break
        run.append((key, section))
    named = {section for _, section in run}
    if len(run) < 2 or not (named & _TERMINAL_SECTIONS):
        return StructuredAbstract()
    # A terminal section on its own is a one-heading page, not a summary: the run
    # has to reach an ending *from* somewhere.
    if not (named - _TERMINAL_SECTIONS):
        return StructuredAbstract()
    return StructuredAbstract(frozenset(key for key, _ in run), 1)


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

    def __init__(self, limits: Optional[Limits] = None):
        self.max_bounded_chars = (limits or Limits()).max_bounded_section_chars
        self.current: Optional[str] = None
        self.seen: List[str] = []
        """Canonical names met, in document order, deduplicated."""
        self.abandoned: List[str] = []
        """Bounded sections that ran too long to keep claiming."""
        self.withheld = 0
        """Blocks under a LOW_VALUE heading that did not look like its content."""
        self.reopens_refused: List[str] = []
        """Abandoned sections a later heading of the same name tried to reopen."""
        self.resumed: List[str] = []
        """Sections resumed after a bounded subsection of theirs ran too long."""
        self.subsections_refused: List[str] = []
        """Headings whose section a paper cannot have reached again, read as subheadings."""
        self._carried = 0
        self._shadowed: Optional[str] = None

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

        A second `introduction` is refused too, and for a different reason: not
        that the section is spent but that a paper never returns to its opening.
        Once methods, results, discussion or conclusions has been met, a heading
        reading `Background` is a subheading of wherever it sits, so it is labelled
        with the current section rather than opening one. Measured on
        10.1161/atvbaha.122.317953, whose Methods contain the subsection
        "Background Contamination Heuristic" -- laid out on two lines, so the first
        of them is the bare word `Background` and matched the `introduction` alias.
        It reopened `introduction` on page 5 and relabelled the rest of that
        paper's Methods, 4,600 characters, as its introduction.
        """
        if name in self.abandoned:
            self.current = None
            if name not in self.reopens_refused:
                self.reopens_refused.append(name)
            return None
        if (name == INTRODUCTION and self.current != INTRODUCTION
                and set(self.seen) & _AFTER_INTRODUCTION):
            if name not in self.subsections_refused:
                self.subsections_refused.append(name)
            return self.current
        # A bounded section opened inside an unbounded one *shadows* it rather than
        # replacing it, so `carry` has something to go back to. See `carry`.
        self._shadowed = (self.current if name in BOUNDED_SECTIONS
                          and self.current is not None
                          and self.current not in BOUNDED_SECTIONS else None)
        self.current = name
        self._carried = 0
        if name not in self.seen:
            self.seen.append(name)
        return name

    def close_summary(self) -> None:
        """The span the caller opened as a structured abstract is over.

        Called when the page holding a `structured_abstract` ends. Without it the
        abstract would keep carrying onto the paper's first real page, which for
        10.1161/atvbaha.122.317953 is 4,900 characters of introduction: the summary
        is bounded by the page it is printed on, and only the caller can see where
        that is.

        Deliberately not a general `close()`: it does nothing unless `abstract` is
        still the open section, so a real heading that has since opened something
        else is left alone.
        """
        if self.current == ABSTRACT:
            self.current = None
            self._shadowed = None

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
            # Back to the section this one interrupted, when there was one. A
            # bounded section is usually a *subsection*: AHA journals open Methods
            # with a `Data Availability` heading, so on
            # 10.1161/atvbaha.122.317953 the statement's budget ran out six
            # paragraphs into the Methods and, before this, took the remaining
            # 14 blocks and 11,000 characters of Methods down with it -- the
            # nuclei isolation, the 10x chemistry, the clustering parameters, all
            # unlabelled with `methods` nowhere in the article. Resuming is the
            # weaker claim of the two on offer: the enclosing heading is still the
            # last one the page actually declared.
            resumed, self.current = self._shadowed, self._shadowed
            self._shadowed = None
            self._carried = 0
            if resumed is not None and resumed not in self.resumed:
                self.resumed.append(resumed)
            return self.current
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
        if self.resumed:
            parts.append(
                f"{', '.join(self.resumed)} resumed after a bounded statement inside "
                f"it ran past {self.max_bounded_chars} characters, so the text after "
                f"the statement is attributed to the section that contained it")
        if self.reopens_refused:
            parts.append(
                f"a later heading tried to reopen {', '.join(self.reopens_refused)} "
                f"after it had been abandoned; it was left unlabelled instead")
        if self.subsections_refused:
            parts.append(
                f"a heading naming {', '.join(self.subsections_refused)} appeared after "
                f"the paper had moved past it and was read as a subheading of the "
                f"section it sits in")
        return "; ".join(parts) or None

    def record(self, meta: dict) -> None:
        """Write what this tracker did into a parser's `meta`.

        Every counter here is something a rule withheld or refused, and the promise
        is that none of it is silent -- so the dump belongs to the tracker rather
        than being restated by each parser that owns one. It was byte-identical in
        `pdf.py` and `docxfile.py`, and the docx copy was uncovered, so a new counter
        added to this class would have reached the PDF record and not the docx one.

        Keys are set only when non-empty, which is what keeps `blocks.jsonl` and the
        extraction record free of a row of zeroes on a clean article.
        """
        meta["sections"] = self.seen
        if self.abandoned:
            meta["sections_abandoned"] = self.abandoned
        if self.withheld:
            meta["low_value_blocks_withheld"] = self.withheld
        if self.resumed:
            meta["sections_resumed"] = self.resumed
        if self.reopens_refused:
            meta["reopens_refused"] = self.reopens_refused
        if self.subsections_refused:
            meta["subsections_refused"] = self.subsections_refused
        if self.reason():
            meta["reason"] = self.reason()
