"""Is this actually the article, or is it a denial page wearing a PDF's clothes?

Publishers and proxies answer refusals with HTTP 200 and a friendly page. Left
unchecked, a "purchase this article" interstitial gets written to disk as
`fulltext.pdf`, the pipeline extracts nothing from it, and the audit log records
a clean run that found no perturbations. That failure mode -- a plausible empty
result -- is the one this module exists to prevent.

Rules:

- Magic bytes decide whether something is a PDF, never the Content-Type header.
  A paywall page served as `application/pdf` is not a PDF.
- A PDF that parses but contains almost no text is flagged, not silently kept,
  because a scanned article needs an OCR pre-step this pipeline does not have.
- Page count alone is NOT used to reject. One-page articles are legitimate
  (correspondence, brief communications), so a short PDF is only rejected when
  it also reads like a purchase page.
"""

import re
from typing import Optional, Tuple

import fitz  # PyMuPDF

# Phrases that appear on publisher access-denied pages, in the PDF stub some
# publishers serve in place of the article, or both.
_PAYWALL_PHRASES = [
    "purchase this article",
    "purchase pdf",
    "buy this article",
    "rent this article",
    "get access to this article",
    "access through your institution",
    "institutional access",
    "sign in to read",
    "subscribe to view",
    "subscription required",
    "this article is available to subscribers",
    "check access to the full text",
    "choose an option to locate",
    "already a subscriber",
    "one-time purchase",
]

# OCLC EZproxy's message for a host it has no stanza for.
_PROXY_NOT_CONFIGURED = [
    "has not been configured for access",
    "attempted to view a page that has not been configured",
    "unknown resource",
]

# Stanford SSO, i.e. the session died and we were bounced to the IdP.
#
# Duo is the second leg of that bounce and shares none of its vocabulary: it is
# served from `api-<id>.duosecurity.com/prompt/...` and reads "Select an option
# to log in / Duo Push / Send to Mobile Phone / Secured by Duo" -- not one word
# of Stanford's own login page. Measured on an expired proxy session: nothing
# below matched, `classify_denial` returned None, and the Duo prompt was handed
# to the generic adapter, which reported `no_pdf_link`. "Your session expired"
# is the most actionable answer this module can give, so it must survive a
# second-factor hop to a host with a different name.
_SSO_MARKERS = [
    "sunet id",
    "stanford login",
    "two-step authentication",
    "duo push",
    "secured by duo",
]
_SSO_HOSTS = ("login.stanford.edu", "idp.stanford.edu", "weblogin.stanford.edu",
              "duosecurity.com")

# A link resolver answering "I have no such article" -- an error document served
# with HTTP 200, not a page. Measured on 10.1016/j.xgen.2026.101304: the proxy
# routed the DOI through linkinghub to ClinicalKey, which is a clinical-content
# platform and does not carry Cell Genomics, and got back 2562 bytes of
# `<ServiceErrorResponse><status>RESOURCE_NOT_FOUND</status>`. The tier recorded
# `loaded`, saved it as landing.html and let the adapter conclude `no_pdf_link`
# -- "we could not find a PDF link on the page" for something that was never an
# article page. Naming it separates "this platform does not have this article"
# from "the page rendered and had no PDF", which point at different fixes.
_LINK_RESOLVER_ERRORS = [
    "serviceerrorresponse",
    "could not find eid for link resolver",
]

# NCBI fronts its file downloads with a proof-of-work page that only a real
# browser can clear. It is not a refusal -- the file is public -- so it gets its
# own status, which tells the caller to route through the browser tier.
_JS_CHALLENGE_MARKERS = [
    "preparing to download",
    "pow-o",            # the proof-of-work bundle NCBI serves
    "checking your browser",
    "enable javascript and cookies to continue",
]

# Magic bytes, not Content-Type: a publisher that serves a paywall page as
# `application/pdf` will also mislabel a supplement.
# `extract/extractor.py`'s `sniff_extension` applies the same ordering for the same
# reason over fifteen formats. Deliberately NOT shared: that one is a dispatcher on
# unstripped `data[:8]` that opens the zip container to separate .xlsx from .docx,
# while this is one whitespace-tolerant question about one format. If either gains a
# format, read the other rather than copying from it.
_PDF_MAGIC = b"%PDF"
# Below this much extracted text we assume the PDF is scanned images.
_MIN_TEXT_CHARS = 200
# A PDF this small is only accepted if it reads like a real article.
_SMALL_PDF_BYTES = 30_000
# Below this a body is a failed download rather than a refusal page. Kept low on
# purpose: a small PDF is usually a "purchase this article" stub, and calling that
# `download_failed` would hide why it failed.
_MIN_DOWNLOAD_BYTES = 100

#: Failure statuses that name a cause the user can act on, most decisive first.
#: These beat a generic miss wherever they appear, because "your session expired"
#: says more than "the last thing we tried returned HTML".
#:
#: This lives here, next to the code that produces the words, because two callers
#: need the same ranking and neither can import the other: a tier has to pick a
#: winner among its own candidate URLs before `fetcher` ever sees one status from
#: it, and `fetcher` imports the tiers. Duplicating the order into the tier is what
#: the previous version did, and the copy drifted -- it kept the *later* diagnosis
#: where `fetcher` keeps the *higher-ranked* one, so `paywalled` then
#: `session_expired` resolved to different words depending on whether the two came
#: from one tier or two. Same set, same order, one definition.
#:
#: `too_large` is first, and it is the only one of these that is not a statement
#: about a publisher: the file exists, it is public, and this tool refused it over
#: `fetch.max_file_mb` before transferring a byte. That stays true whatever a later
#: tier's route reports, and the action it names -- raise the cap -- is the one that
#: gets the file. `pmc_s3` is the tier that produces it, and it produces it while
#: asserting the opposite of what a generic miss would overwrite it with: a complete
#: listing holding keys is that tier saying the article *is* in the Open Access
#: subset, and without a rank here `pmc_oa` running next reported
#: `not_in_oa_subset` over it -- measured, `_best_pdf_status(["too_large",
#: "not_in_oa_subset"])` returned the second, and both tiers key on nothing but
#: `ids.pmcid`, so in the shipped tier order the second one always runs. A word this
#: table does not rank is a word `better_pdf_failure` cannot keep.
PDF_DIAGNOSES = ("too_large", "paywalled", "session_expired", "proxy_not_configured",
                 "publisher_stub_page", "link_resolver_error")


def better_pdf_failure(current: Optional[str], incoming: str) -> str:
    """Which of two PDF failure statuses explains more.

    A named diagnosis beats a generic miss in either order; between two diagnoses
    `PDF_DIAGNOSES` order decides; between two generic misses the later one wins,
    since it comes from the more capable attempt. Folding this over a list is
    exactly `fetcher._best_pdf_status`'s failure branch -- that function is written
    in terms of this one so the two cannot disagree.
    """
    if current is None:
        return incoming
    if current in PDF_DIAGNOSES and incoming in PDF_DIAGNOSES:
        return min(current, incoming, key=PDF_DIAGNOSES.index)
    if incoming in PDF_DIAGNOSES:
        return incoming
    if current in PDF_DIAGNOSES:
        return current
    return incoming


def _contains_any(haystack: str, needles) -> bool:
    return any(needle in haystack for needle in needles)


def looks_like_pdf(content: bytes) -> bool:
    """True if the bytes begin with a PDF header, ignoring leading whitespace."""
    return content.lstrip()[:8].startswith(_PDF_MAGIC)


def classify_denial(url: str, content: bytes) -> Optional[str]:
    """Name the refusal if these bytes are an access-denied page, else None.

    Returns one of `proxy_not_configured`, `session_expired`, `paywalled`,
    `javascript_challenge`, `link_resolver_error`.
    """
    host_matched = any(host in (url or "") for host in _SSO_HOSTS)
    # Only inspect the head of the body; denial pages announce themselves early.
    body = content[:200_000].decode("utf-8", "replace").lower()
    body = re.sub(r"\s+", " ", body)

    if _contains_any(body, _JS_CHALLENGE_MARKERS):
        return "javascript_challenge"
    if _contains_any(body, _PROXY_NOT_CONFIGURED):
        return "proxy_not_configured"
    if _contains_any(body, _LINK_RESOLVER_ERRORS):
        return "link_resolver_error"
    if host_matched or _contains_any(body, _SSO_MARKERS):
        return "session_expired"
    if _contains_any(body, _PAYWALL_PHRASES):
        return "paywalled"
    return None


def validate_pdf(
    content: bytes,
    content_type: str = "",
    url: str = "",
) -> Tuple[bool, str, dict]:
    """Decide whether `content` may be stored as the article PDF.

    Returns `(accepted, status, meta)`. `status` is one of the `fulltext.status`
    values: `ok`, `scanned_pdf_suspected` (accepted, flagged), or one of
    `download_failed`, `not_a_pdf`, `paywalled`, `proxy_not_configured`,
    `session_expired`, `javascript_challenge`, `link_resolver_error` (all rejected).

    `javascript_challenge` is not a refusal: the file is public and behind NCBI's
    proof-of-work page, so it tells the caller to route through the browser tier
    rather than to give up. It comes from `classify_denial` like the others and was
    missing from this list. `publisher_stub_page` is a sibling status this function
    never returns -- the browser tier assigns that one, since only a browser can see
    a rendered shell.
    """
    meta: dict = {"bytes": len(content or b""), "content_type": content_type}

    # Only a genuinely empty or absurdly short body counts as a failed download.
    if not content or len(content) < _MIN_DOWNLOAD_BYTES:
        return False, "download_failed", meta

    if not looks_like_pdf(content):
        # Not a PDF at all. If it is a recognisable refusal, say which one --
        # "paywalled" is far more actionable than "not_a_pdf".
        denial = classify_denial(url, content)
        return False, denial or "not_a_pdf", meta

    try:
        with fitz.open(stream=content, filetype="pdf") as doc:
            meta["pages"] = doc.page_count
            sample = []
            for page in doc.pages(0, min(doc.page_count, 3)):
                sample.append(page.get_text("text"))
    except Exception as e:  # PyMuPDF raises assorted types on damaged files
        meta["error"] = f"{type(e).__name__}: {e}"
        return False, "not_a_pdf", meta

    text = re.sub(r"\s+", " ", "\n".join(sample)).strip()
    meta["text_chars_sampled"] = len(text)
    lowered = text.lower()

    # A short, thin PDF that talks about buying the article is a stub, not a paper.
    if _contains_any(lowered, _PAYWALL_PHRASES) and (
        meta["pages"] <= 2 or len(content) < _SMALL_PDF_BYTES
    ):
        return False, "paywalled", meta

    if len(text) < _MIN_TEXT_CHARS:
        # Parses fine but has no extractable text: almost certainly scanned.
        # Kept, because it is the real article, but flagged so the caller knows
        # `extract/pdf.py` will get nothing out of it without an OCR step.
        return True, "scanned_pdf_suspected", meta

    return True, "ok", meta


# -- is the accepted document the article we asked for? ----------------------
#
# Everything above answers "is this a document at all". Nothing above asks the
# next question, and two papers in the corpus were recorded `status: complete`
# because of it:
#
# - 10.1038/s41586-024-08560-0 resolved to a one-page Nature *Author Correction*.
#   The bytes were a real, well-formed PDF and a real JATS file; the DOI and title
#   both matched, because the correction notice has its own DOI and its own title.
#   Only `article-type="correction"` in the JATS said what the document was.
# - 10.1126/science.adf1226 stored a 71-page 10x Genomics Visium user guide
#   (CG000239) as `fulltext.pdf`. The browser tier found no `citation_pdf_url` on
#   the science.org page, fell through to the first non-supplement `.pdf` anchor,
#   and that anchor pointed at a vendor manual on a third-party CDN. Its first
#   extracted block is `10xGenomics.com`, and the article -- "Comprehensive cell
#   atlas of the first-trimester developing human brain" -- was never fetched.
#
# Both are the failure this package exists to prevent: not an error, a plausible
# success. Downstream, 1,493 blocks of a reagent manual are indistinguishable from
# a paper with nothing to report.

#: JATS `article-type` values for a document that is *about* an article rather than
#: being one. A correction, a retraction notice and an editorial are all legitimate
#: publications with their own DOIs -- they are simply not the research article a
#: reader of this corpus is looking for, and they carry none of its Methods.
#:
#: `partial-retraction` and the `corrigendum`/`erratum` spellings are here because
#: publishers emit them even though the JATS suggested list prefers `correction`.
#: `corrected-article` is deliberately absent: it appears in these files, but on
#: `<related-article related-article-type=...>`, pointing *at* the paper. Matching
#: it would reject the article and keep the notice, exactly backwards -- which is
#: why `jats_article_type` reads the attribute on the root element only.
NOT_RESEARCH_JATS_TYPES = frozenset({
    "correction", "corrigendum", "erratum",
    "retraction", "partial-retraction", "retraction-notice",
    "editorial", "expression-of-concern", "editorial-expression-of-concern",
})

#: Europe PMC `pubTypeList` values that say the same thing its index knows before
#: anything is downloaded. Measured: 10.1038/s41586-024-08560-0 comes back
#: `['published erratum', 'correction']`, while five research articles from this
#: corpus come back `['research-article', 'Journal Article']` and a preprint
#: `['Preprint']`, so the two sets do not overlap.
#:
#: `retracted publication` is deliberately absent, and the distinction is the whole
#: care in this set: Europe PMC puts that on the *article that was retracted*, which
#: is a research article -- while `retraction of publication` is the notice. Whether
#: a retracted paper belongs in a curation corpus is a scientific judgement and not
#: this function's to make; whether a retraction *notice* is a research article is
#: not a judgement at all.
NOT_RESEARCH_PUB_TYPES = frozenset({
    "published erratum", "correction", "corrigendum", "erratum",
    "retraction of publication", "expression of concern",
})

#: The same fact stated by the indexed title, for the case where neither a JATS
#: file nor a Europe PMC record arrives. Publishers prefix these notices in a fixed
#: shape -- "Author Correction: <title of the paper>" -- and Europe PMC and
#: Crossref pass the prefix through.
#:
#: The trailing colon is required, and that is the whole guard against false
#: positives: "Retraction: Progressive plasticity..." is a notice, while
#: "Retraction of the primary cilium during mitosis" is a research article about
#: retraction. Measured over the 392 manifests in `corpus/`, this matches exactly
#: one title -- the Author Correction above -- and no research article.
_NOT_RESEARCH_TITLE = re.compile(
    r"^\s*(author correction|publisher correction|correction( to)?|corrigendum"
    r"|erratum|retraction( note| notice)?|retracted|withdrawn"
    r"|(editorial )?expression of concern|addendum|matters arising)\s*:",
    re.IGNORECASE,
)

#: Any DOI, for reading the *other* article a notice points at.
_DOI_IN_TEXT = re.compile(r"10\.\d{4,9}/[^\s\"'<>()\[\],;]+")

#: Root `<article>` open tag of a JATS file, and its `article-type`. Anchored on
#: `<article` with no `!` after the `<`, so the `<!DOCTYPE article ...>` line that
#: precedes it in every real Europe PMC file cannot be mistaken for it.
_JATS_ROOT = re.compile(rb"<article\b[^>]*>", re.IGNORECASE)
_JATS_ARTICLE_TYPE = re.compile(rb"""\barticle-type\s*=\s*["']([^"']+)["']""",
                                re.IGNORECASE)

#: Title words too common in this field to be evidence of anything. A vendor manual
#: for a single-cell reagent kit shares "single", "cell" and "human" with half the
#: corpus, so counting them would be counting the noise.
_TITLE_STOPWORDS = frozenset({
    "the", "and", "for", "with", "from", "that", "this", "into", "onto", "upon",
    "over", "under", "during", "between", "across", "their", "these", "those",
    "human", "cell", "cells", "cellular", "single", "analysis", "analyses",
    "reveals", "reveal", "identifies", "identify", "study", "studies", "using",
})
#: Shorter than this a title word carries no signal.
_TITLE_TOKEN_CHARS = 4
#: Fraction of a title's significant words that must appear. Only consulted when
#: the DOI is absent, so it is a fallback and not the test -- see
#: `identify_fulltext` for why, and for what was measured.
_TITLE_MATCH_MIN = 0.6
#: Pages of a PDF to read when asking which article it is. The title and the DOI
#: are front-matter; measured over the 633 full-text files in `corpus/`, three
#: pages found the expected DOI in every one that was the right paper.
_IDENTITY_PDF_PAGES = 3

#: `fulltext.status` values meaning the bytes are on disk and are *not* the
#: requested research article. Neither is in `store.PDF_USABLE`, so neither can
#: finish `complete`; both keep the file, because a document we cannot identify
#: may still be the only copy anyone retrieved and deleting it would destroy the
#: evidence for the diagnosis. `store.finalize_status` reads the split from
#: `PDF_USABLE`; this tuple is here so a caller can name the pair.
IDENTITY_FAILURES = ("not_research_article", "identity_unverified")


def jats_article_type(content: bytes) -> Optional[str]:
    """The `article-type` on a JATS file's root `<article>` element, lowercased.

    None when the bytes are not JATS or the attribute is absent -- absent is not a
    finding, since plenty of valid files omit it.
    """
    match = _JATS_ROOT.search(content or b"")
    if match is None:
        return None
    attribute = _JATS_ARTICLE_TYPE.search(match.group(0))
    if attribute is None:
        return None
    return attribute.group(1).decode("utf-8", "replace").strip().lower()


def not_research_article(article_type: Optional[str] = None,
                        title: Optional[str] = None,
                        pub_types=None) -> Optional[str]:
    """Why this DOI is not a research article, or None if nothing says it isn't.

    Three independent signals, because none of them is always available: the JATS
    `article-type` needs a JATS file, `pubTypeList` needs a Europe PMC record, and
    the title prefix needs only a title. 10.1038/s41586-024-08560-0 had all three
    and was caught by none, because nothing asked.

    Ordered strongest first. `pubTypeList` is a curated field on an index record;
    `article-type` is the publisher's own declaration on the document; the title
    prefix is a string convention, which is why it is last and why it insists on
    the colon.

    Returns a sentence for `problems`, so the manifest says which signal fired.
    """
    matched = sorted(NOT_RESEARCH_PUB_TYPES.intersection(
        str(t).strip().lower() for t in (pub_types or ())))
    if matched:
        return (f"Europe PMC types this DOI as {', '.join(matched)}: it is a notice "
                f"about an article, not a research article")
    if article_type and article_type.strip().lower() in NOT_RESEARCH_JATS_TYPES:
        return (f'the retrieved JATS declares article-type="'
                f'{article_type.strip().lower()}": this DOI is a notice about an '
                f"article, not a research article")
    if title and _NOT_RESEARCH_TITLE.match(title):
        return (f"the indexed title begins {title.strip()[:60]!r}: this DOI is a "
                f"notice about an article, not a research article")
    return None


def cited_dois(text: str, exclude: str = "", limit: int = 3) -> list:
    """DOIs printed in `text` other than `exclude`, in order of appearance.

    A correction notice names the article it corrects, so this turns "this is not
    a paper" into "fetch 10.1038/s41586-024-08150-0 instead" -- the difference
    between a rejection and an instruction.
    """
    skip = (exclude or "").strip().lower()
    found = []
    for match in _DOI_IN_TEXT.finditer(text or ""):
        doi = match.group(0).rstrip(".,;:)").lower()
        if doi != skip and doi not in found:
            found.append(doi)
            if len(found) >= limit:
                break
    return found


def _squash(text: str) -> str:
    """Lowercase with every run of whitespace removed, for substring matching.

    PDF text extraction breaks a DOI across a line end; nothing else about it
    changes, so removing the whitespace is enough to find it again.
    """
    return re.sub(r"\s+", "", (text or "")).lower()


def mentions_doi(text: str, doi: str) -> bool:
    """Is `doi` printed anywhere in `text`, ignoring case and line breaks?"""
    if not doi:
        return False
    return _squash(doi) in _squash(text)


def title_overlap(text: str, title: str) -> Optional[float]:
    """Fraction of a title's significant words that appear in `text`.

    None when the title has no significant words left after `_TITLE_STOPWORDS`,
    which is a "cannot tell", not a zero.
    """
    words = re.sub(r"[^0-9a-z]+", " ", (title or "").lower()).split()
    wanted = {w for w in words
              if len(w) >= _TITLE_TOKEN_CHARS and w not in _TITLE_STOPWORDS}
    if not wanted:
        return None
    haystack = set(re.sub(r"[^0-9a-z]+", " ", (text or "").lower()).split())
    return sum(1 for w in wanted if w in haystack) / len(wanted)


def identify_fulltext(text: str, doi: str, title: str = "") -> Tuple[bool, dict]:
    """Does this document plausibly belong to `doi`? Returns `(verified, meta)`.

    The DOI is the test and the title is only a fallback, and that ordering is
    measured rather than assumed. Over the 633 `fulltext.pdf`/`fulltext.nxml`
    files in `corpus/`, the requested DOI appears in the first three pages of
    every single one that is the right document -- 632 of 633 -- and the one
    exception is the 10x Genomics manual. The title, taken alone, is far weaker:
    scored against 1,121 deliberately mismatched paper/title pairs it clears 0.6
    for 59 of them, because biomedical titles share their vocabulary. So it is
    consulted only when the DOI is missing, where the alternative is no check at
    all -- some publisher PDFs genuinely omit the DOI, and rejecting those on a
    strict reading would throw away real articles.

    A `verified: False` never means "delete this". It means the caller must not
    call it the article: see `IDENTITY_FAILURES`.
    """
    meta = {
        "doi_in_text": mentions_doi(text, doi),
        "title_overlap": None,
        "matched_on": None,
        "chars_read": len(text or ""),
    }
    # A document with no text cannot be identified, and "cannot tell" is not
    # "wrong". `validate_pdf` deliberately keeps a scanned article and flags it
    # `scanned_pdf_suspected`, which already says extraction will get nothing out
    # of it; answering `identity_unverified` on top of that would replace a true
    # statement with a false one -- it claims we compared and found a mismatch,
    # when nothing was there to compare. Same threshold as `validate_pdf`'s, and
    # for the same reason, so a file it calls scanned is never one this calls wrong.
    if meta["chars_read"] < _MIN_TEXT_CHARS and not meta["doi_in_text"]:
        meta["undecidable"] = "too little text to identify"
        return True, meta
    if meta["doi_in_text"]:
        meta["matched_on"] = "doi"
        return True, meta

    overlap = title_overlap(text, title)
    if overlap is not None:
        meta["title_overlap"] = round(overlap, 2)
        if overlap >= _TITLE_MATCH_MIN:
            meta["matched_on"] = "title"
            return True, meta

    # Say what the document actually is. "It is not the paper" sends a reader to
    # the file; "it begins 10xGenomics.com CG000239 Rev F USER GUIDE" ends the
    # investigation.
    meta["opening"] = re.sub(r"\s+", " ", (text or "")[:400]).strip()[:200]
    return False, meta


def identity_problem(kind: str, doi: str, title: str, meta: dict) -> str:
    """The manifest line a reader is owed when a stored file is not the paper.

    Says what was looked for, that the bytes were kept, and -- the clause that
    ends an investigation rather than starting one -- what the document actually
    is. For 10.1126/science.adf1226 that last part reads `it begins
    '10xGenomics.com CG000239 Rev F USER GUIDE Visium ...'`, which is a reagent
    manual and not a paper about the first-trimester human brain.

    Lives here rather than in `fetcher` because two callers need the identical
    sentence and one of them must not import `fetcher`: `revalidate` exists to
    correct manifests without touching the network, and importing the tier loop
    would pull Playwright's loader in behind it. A manifest corrected after the
    fact has to read exactly like one a fresh fetch would have written, or the
    corpus grows two vocabularies for one fact.
    """
    wanted = f"the DOI {doi}"
    if title:
        wanted += f" nor the title {title.strip().rstrip('.')!r}"
    overlap = meta.get("title_overlap")
    scored = f" (title words matched: {overlap:.0%})" if overlap is not None else ""
    opening = meta.get("opening")
    begins = f"; it begins {opening!r}" if opening else ""
    return (f"the {kind} retrieved for {doi} contains neither {wanted}"
            f"{scored}, so it is not the requested article. The file is kept on "
            f"disk -- it is the only copy any tier produced, and it is the "
            f"evidence for this line{begins}")


def pdf_sample_text(content: bytes, pages: int = _IDENTITY_PDF_PAGES) -> str:
    """Front-matter text of a PDF, for identification. Empty if it will not parse.

    Deliberately separate from `validate_pdf`, which samples the same pages for a
    different question and throws the text away. Returning it from there instead
    would put a few thousand characters of every article into the `attempts` list
    of every manifest, and `validate_pdf`'s answer is about the file while this
    one is about the DOI: the tiers call the first without knowing which paper
    they were asked for, and only `fetcher` knows both.
    """
    try:
        with fitz.open(stream=content, filetype="pdf") as doc:
            return "\n".join(page.get_text("text")
                             for page in doc.pages(0, min(doc.page_count, pages)))
    except Exception:
        return ""


def jats_sample_text(content: bytes, limit: int = 200_000) -> str:
    """Tag-stripped text of a JATS file, for identification.

    Not `extract/jats.py`: that one builds ordered blocks from a parsed tree and
    would drag the extraction stage into the fetcher. Identity needs only to know
    whether two strings are present.
    """
    head = (content or b"")[:limit].decode("utf-8", "replace")
    return re.sub(r"<[^>]+>", " ", head)
