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

_PDF_MAGIC = b"%PDF"
# Below this much extracted text we assume the PDF is scanned images.
_MIN_TEXT_CHARS = 200
# A PDF this small is only accepted if it reads like a real article.
_SMALL_PDF_BYTES = 30_000


def _contains_any(haystack: str, needles) -> bool:
    return any(needle in haystack for needle in needles)


def looks_like_pdf(content: bytes) -> bool:
    """True if the bytes begin with a PDF header, ignoring leading whitespace."""
    return content.lstrip()[:8].startswith(_PDF_MAGIC)


def classify_denial(url: str, content: bytes, content_type: str = "") -> Optional[str]:
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
    min_bytes: int = 100,
) -> Tuple[bool, str, dict]:
    """Decide whether `content` may be stored as the article PDF.

    Returns `(accepted, status, meta)`. `status` is one of the `fulltext.status`
    values: `ok`, `scanned_pdf_suspected` (accepted, flagged), or one of
    `download_failed`, `not_a_pdf`, `paywalled`, `proxy_not_configured`,
    `session_expired`, `link_resolver_error` (all rejected).
    """
    meta: dict = {"bytes": len(content or b""), "content_type": content_type}

    # Only a genuinely empty or absurdly short body counts as a failed download.
    # The floor is kept low on purpose: a small PDF is usually a "purchase this
    # article" stub, and calling that `download_failed` would hide why it failed.
    if not content or len(content) < min_bytes:
        return False, "download_failed", meta

    if not looks_like_pdf(content):
        # Not a PDF at all. If it is a recognisable refusal, say which one --
        # "paywalled" is far more actionable than "not_a_pdf".
        denial = classify_denial(url, content, content_type)
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
        # `pdf_loader` will get nothing out of it without an OCR step.
        return True, "scanned_pdf_suspected", meta

    return True, "ok", meta
