"""Can a negative answer from this article be believed?

Every other module here helps find evidence. This one bounds what its absence is
allowed to mean, and it exists because the failure it prevents is invisible.

Ask "which datasets did this paper deposit?" of 10.1016/j.cell.2019.08.008 and the
honest answer is *unknown*: that article's `fulltext.status` is `download_failed`,
its main text is the publisher's saved landing page, and its extraction carries
`landing_page_only`. There is no Methods section and no data-availability statement
in what was extracted, so nothing could have been found in them. A pipeline that
reports "no accessions" for that article and "no accessions" for a fully extracted
paper that genuinely deposited nothing has erased the only distinction that
matters.

So this returns a state, and the states are deliberately not a quality score --
they answer one question, which is whether emptiness is informative:

| state | a negative answer means |
|---|---|
| `ready` | the text was there and the thing is not in it |
| `ready_with_caveats` | the same, bounded by the named gaps |
| `text_unavailable` | nothing; do not record a negative |
| `not_extracted` | nothing; run `manuscript-extract one` |
| `not_fetched` | nothing; run `manuscript-fetch get` |

The vocabulary is drawn from the two stages' own records rather than invented
beside them: `fulltext.status` and `supplementary_status` from `manifest.json`,
`status` and `caveats` from `extraction.json`. A third parallel taxonomy would be
one more thing to keep in step.
"""

from typing import Optional

from ..extract import extractor
from ..fetch import store

READY = "ready"
READY_WITH_CAVEATS = "ready_with_caveats"
TEXT_UNAVAILABLE = "text_unavailable"
NOT_EXTRACTED = "not_extracted"
NOT_FETCHED = "not_fetched"

STATES = frozenset({READY, READY_WITH_CAVEATS, TEXT_UNAVAILABLE, NOT_EXTRACTED,
                    NOT_FETCHED})

#: States where a "not found" is worth recording. `ready_with_caveats` is included
#: on purpose: an unverified supplement set is the *ordinary* outcome for any
#: page-scraping fetch tier, and holding out for `ready` would refuse to answer
#: anything about most of the corpus. The caveats travel with the answer instead.
TRUSTWORTHY = frozenset({READY, READY_WITH_CAVEATS})

#: Extraction caveats that describe missing or substituted *text*, as opposed to a
#: missing file. `landing_page_only` is the sharpest of them -- a landing page has
#: an abstract and a reference list where the article had Methods -- and
#: `main_text_thin` means front matter was extracted instead of a body.
_TEXT_SUBSTITUTED = {extractor.LANDING_PAGE_ONLY, extractor.MAIN_TEXT_THIN}

#: Per-file extraction statuses that mean a supplement's text was never read. A
#: donor table inside one of these is not absent, it is unreachable -- and unlike a
#: figure image, something a human or an OCR pass could still recover.
_FILE_TEXT_LOST = {extractor.SCANNED, extractor.UNSUPPORTED, extractor.TOO_LARGE,
                   extractor.MISSING, extractor.UNREADABLE, extractor.PARSER_ERROR}


def assess(article_dir, extraction: Optional[dict] = None,
           manifest: Optional[dict] = None) -> dict:
    """The readiness verdict for one article directory.

    `extraction` and `manifest` may be passed in by a caller that has already read
    them -- `eval` over a whole corpus reads each once -- and are read from disk
    otherwise.

    Returns `{state, why, gaps, doi, slug, fetch, extraction}`. `why` is prose for
    a human; `gaps` is the machine-readable list a consumer attaches to a negative
    answer so the answer carries its own bound.
    """
    manifest = manifest if manifest is not None else store.read_manifest(article_dir)
    if manifest is None:
        return {"state": NOT_FETCHED, "why": ["no manifest.json in the article directory"],
                "gaps": [], "doi": None, "slug": None}

    slug = manifest.get("slug")
    doi = manifest.get("doi")
    record = extraction if extraction is not None else extractor.read_extraction(article_dir)
    if record is None:
        return {"state": NOT_EXTRACTED,
                "why": ["fetched but not extracted; run `manuscript-extract one`"],
                "gaps": [], "doi": doi, "slug": slug}

    caveats = set(record.get("caveats") or [])
    main_text = record.get("main_text") or {}
    why: list = []
    gaps: list = []

    # -- the disqualifying cases -------------------------------------------------
    # `failed` is the extractor's own word for "there is nothing here to ask a
    # question of", so it needs no second opinion.
    if record.get("status") == "failed":
        why.append("extraction failed: there is no usable text")
    if main_text.get("status") not in {extractor.OK, None} or not main_text.get("blocks"):
        why.append(f"main text yielded no blocks (status "
                   f"{main_text.get('status') or 'absent'})")
    substituted = sorted(caveats & _TEXT_SUBSTITUTED)
    for caveat in substituted:
        why.append(extractor.CAVEATS.get(caveat, caveat))
    if why:
        return {"state": TEXT_UNAVAILABLE, "why": why,
                "gaps": substituted or ["main_text_missing"],
                "doi": doi, "slug": slug,
                "fetch": _fetch_summary(manifest),
                "extraction": _extraction_summary(record)}

    # -- the bounded cases ------------------------------------------------------
    # Everything from here yielded a real body. What follows is an inventory of
    # what a search over it could not have covered.
    if extractor.SUPPLEMENTS_MISSING in caveats:
        gaps.append(extractor.SUPPLEMENTS_MISSING)
        why.append("the publisher listed supplementary files that were not retrieved")
    if extractor.SUPPLEMENTS_UNVERIFIED in caveats:
        gaps.append(extractor.SUPPLEMENTS_UNVERIFIED)
        why.append("supplements were fetched but nothing bounds the set")
    if extractor.MANIFEST_ENTRY_WITHOUT_PATH in caveats:
        gaps.append(extractor.MANIFEST_ENTRY_WITHOUT_PATH)
        why.append("a supplementary entry has no file on disk to read")

    lost = {status: count
            for status, count in (record.get("supplementary_by_status") or {}).items()
            if status in _FILE_TEXT_LOST}
    if lost:
        gaps.append("supplement_text_unread")
        why.append("supplementary text not read from "
                   + ", ".join(f"{n} {s}" for s, n in sorted(lost.items())))

    # An unlabelled body is not a missing one, so this never disqualifies -- but a
    # question routed at Methods has nothing to route to, and `query.prefer` falls
    # back to the whole article precisely because of these. Corpus-wide 599 of
    # 4,009 main-text paragraphs carry no section, and it is not only the broken
    # articles: 39 of 86 on 10.1126/science.abo0510, which is otherwise `ready`.
    labelling = (main_text.get("section_labelling") or {}).get("confidence")
    if labelling in {"low", "none"}:
        gaps.append("section_labelling_" + labelling)
        why.append(f"main-text section labelling is {labelling}: a section filter "
                   f"cannot be relied on for this article")

    return {"state": READY_WITH_CAVEATS if gaps else READY,
            "why": why, "gaps": gaps, "doi": doi, "slug": slug,
            "fetch": _fetch_summary(manifest),
            "extraction": _extraction_summary(record)}


def _fetch_summary(manifest: dict) -> dict:
    return {"status": manifest.get("status"),
            "fulltext": (manifest.get("fulltext") or {}).get("status"),
            "supplementary": manifest.get("supplementary_status")}


def _extraction_summary(record: dict) -> dict:
    return {"status": record.get("status"),
            "source": (record.get("main_text") or {}).get("origin"),
            "blocks": (record.get("totals") or {}).get("blocks"),
            "caveats": sorted(record.get("caveats") or [])}


def trustworthy(verdict: dict) -> bool:
    """Whether a "nothing found" from this article should be recorded as a finding.

    A caller that skips this check does not get a wrong answer, it gets an answer
    whose emptiness it cannot account for -- which is the one thing both earlier
    stages are built to refuse.
    """
    return verdict.get("state") in TRUSTWORTHY
