"""The adapter that handles most publishers.

`<meta name="citation_pdf_url">` is required for Google Scholar indexing, so the
large majority of publisher article pages carry it. That single tag is a better
PDF locator than any per-publisher selector, which is why the specific adapters
exist only for the sites where it is absent or wrong.

Supplement discovery is heuristic: anchors whose URL or text reads as
supplementary AND whose URL ends in a file extension. Anchors that merely link to
a supplement *section* are excluded, or every article would appear to have one
supplementary file called "Supplementary information".
"""

from typing import List, Optional, Tuple

from .base import (
    Adapter,
    collect_links,
    dedupe_by_target,
    is_supplement_url,
    looks_like_supplement,
    meta_content,
    url_without_fragment,
)


class GenericAdapter(Adapter):
    name = "generic"
    hosts = ()  # selected as the fallback, never by hostname

    def matches(self, host: str) -> bool:
        return True

    def find_pdf_url(self, page, doi: str) -> Optional[str]:
        for meta_name in ("citation_pdf_url", "citation_fulltext_html_url"):
            value = meta_content(page, meta_name)
            if value and ".pdf" in value.lower():
                return value

        # Fall back to a link that advertises itself as the PDF -- but never a
        # supplementary one. Measured on 10.1126/science.adt8307, which carries
        # no `citation_pdf_url` at all: the first `.pdf` anchor on the page is
        # `/doi/suppl/<doi>/suppl_file/science.adt8307_sm.pdf`, so the
        # Supplementary Materials PDF was stored as `fulltext.pdf` and reported
        # `ok`, and the 19-page article was never fetched. Link order decides
        # which anchor is seen first, so the exclusion has to be explicit rather
        # than left to the page to get right.
        #
        # Identity is not a substitute for this check: the SM PDF carries the
        # DOI too, so it passed a "is this the right paper?" comparison. Only
        # the page count (29 against 19) caught it.
        for link in collect_links(page):
            if is_supplement_url(link["url"]):
                continue
            url = link["url"].lower()
            text = link["text"].lower()
            if url.endswith(".pdf") or "pdf" in text.split():
                return link["url"]
        return None

    def find_supplements(self, page, doi: str) -> Tuple[List[dict], bool]:
        links = collect_links(page)
        if not links:
            # No anchors at all means the page did not render for us.
            return [], False
        found = [
            {"url": url_without_fragment(link["url"]), "label": link["text"] or None}
            for link in links
            if looks_like_supplement(link)
        ]
        return dedupe_by_target(found), True
