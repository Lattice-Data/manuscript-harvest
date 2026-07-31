"""Per-publisher adapters, for the sites where the generic one is not enough.

Each is deliberately thin: locate the PDF, locate the supplement downloads,
report honestly when the expected container is absent. Selectors here are the
part of this codebase most likely to break when a publisher redesigns, which is
why `find_supplements` distinguishes "found none" from "could not read the page".
"""

import re
from typing import List, Optional, Tuple
from urllib.parse import urlparse

# ScienceDirect article URLs carry the Elsevier PII, from which the PDF URL can be
# constructed when the page exposes no link. Public because the browser tier reads
# the same PII out of a URL to reach Cell Press -- one shape, defined once.
PII_RX = re.compile(r"/pii/([A-Z0-9]+)", re.IGNORECASE)

from .base import (
    Adapter,
    collect_links,
    dedupe_by_target,
    is_file_url,
    looks_like_supplement,
    meta_content,
    url_without_fragment,
)


class NatureAdapter(Adapter):
    """nature.com and link.springer.com.

    Supplementary objects are named `..._MOESM<n>_ESM.<ext>` and served from
    `static-content.springer.com/esm/...`. Verified directly fetchable for
    open-access articles, which is why the OA tier tries that host first.
    """

    name = "nature"
    hosts = ("nature.com", "link.springer.com", "biomedcentral.com")

    def find_pdf_url(self, page, doi: str) -> Optional[str]:
        value = meta_content(page, "citation_pdf_url")
        if value:
            return value
        try:
            href = page.get_attribute(
                'a[data-track-action="download pdf"]', "href", timeout=2000
            )
            if href:
                return href
        except Exception:
            pass
        return None

    def find_supplements(self, page, doi: str) -> Tuple[List[dict], bool]:
        links = collect_links(page)
        if not links:
            return [], False
        found = []
        for link in links:
            # Fragments must be stripped first: the article page carries one
            # `#MOESM<n>` anchor per supplementary object, and treating those as
            # downloads fetches the page itself once per anchor.
            url = url_without_fragment(link["url"])
            if not is_file_url(url):
                continue
            on_static_host = (
                "static-content.springer.com/esm" in url.lower()
                or "media.springernature.com" in url.lower()
            )
            if "MOESM" in url.upper() or on_static_host or looks_like_supplement(link):
                found.append({"url": url, "label": link["text"] or None})
        return dedupe_by_target(found), True


class WileyAdapter(Adapter):
    """onlinelibrary.wiley.com (Atypon).

    PDFs live at `/doi/pdfdirect/<doi>`; supplements come from
    `/action/downloadSupplement?doi=...&file=...`.
    """

    name = "wiley"
    hosts = ("onlinelibrary.wiley.com", "wiley.com")

    def find_pdf_url(self, page, doi: str) -> Optional[str]:
        value = meta_content(page, "citation_pdf_url")
        if value:
            # Wiley advertises `/doi/pdf/<doi>`, which is an HTML *viewer* -- 46 KB
            # of text/html for 10.1002/path.5751, rejected as `not_a_pdf`. The bytes
            # live at `/doi/pdfdirect/<doi>`.
            return re.sub(r"/doi/(?:pdf|epdf)/", "/doi/pdfdirect/", value)
        if doi:
            # Built on the current origin so a proxied hostname survives.
            parts = urlparse(page.url or "")
            host = parts.netloc or "onlinelibrary.wiley.com"
            return f"{parts.scheme or 'https'}://{host}/doi/pdfdirect/{doi}"
        return None

    def find_supplements(self, page, doi: str) -> Tuple[List[dict], bool]:
        links = collect_links(page)
        if not links:
            return [], False
        found = [
            {"url": url_without_fragment(link["url"]), "label": link["text"] or None}
            for link in links
            if "downloadsupplement" in link["url"].lower() or looks_like_supplement(link)
        ]
        return dedupe_by_target(found), True


class ElsevierAdapter(Adapter):
    """sciencedirect.com and cell.com.

    The highest-risk adapter by a wide margin: ScienceDirect fingerprints
    automation aggressively and may refuse a headless browser outright even with a
    valid session. Supplements are hosted on `ars.els-cdn.com`.
    """

    name = "elsevier"
    hosts = ("sciencedirect.com", "cell.com", "elsevier.com")

    def looks_blocked(self, page) -> bool:
        """ScienceDirect serves automation a shell page instead of the article.

        Headless gets `<title>ScienceDirect</title>` with zero PDF anchors. A
        headed browser on the same profile *sometimes* gets the real article -- one
        manual run on 10.1016/j.stem.2023.12.013 returned the true title, 34 PDF
        anchors and 96 `ars-els-cdn` supplement anchors -- but a headed batch over
        eight articles was stubbed on every one. So visibility alone is not the
        variable, and the status deliberately does not claim it is.
        """
        try:
            title = (page.title() or "").strip()
        except Exception:
            return False
        if title.lower() not in {"sciencedirect", "sciencedirect.com", ""}:
            return False
        # A real article page always links its own PDF somewhere.
        return not any("pdf" in (link["url"] + link["text"]).lower()
                       for link in collect_links(page))

    def find_pdf_url(self, page, doi: str) -> Optional[str]:
        value = meta_content(page, "citation_pdf_url")
        if value:
            return value
        for selector in ('a[aria-label*="PDF"]', "a.pdf-download-btn-link", 'a[href*="/pdft"]'):
            try:
                href = page.get_attribute(selector, "href", timeout=1500)
                if href:
                    return href
            except Exception:
                continue

        # Nothing to scrape: a ScienceDirect article page carries no
        # `citation_pdf_url` and no PDF href at all -- verified on an 833 KB
        # settled page for 10.1016/j.stem.2023.12.013, which is why that fetch
        # failed with `no_pdf_link`. The link is built client-side, but the PII in
        # the URL is enough to construct it. Built against the *current* origin so
        # the library-proxy hostname is preserved; a bare sciencedirect.com URL
        # would leave the proxy and lose entitlement.
        pii = PII_RX.search(page.url or "")
        if pii:
            parts = urlparse(page.url)
            return (f"{parts.scheme}://{parts.netloc}/science/article/pii/"
                    f"{pii.group(1)}/pdfft?isDTMRedir=true&download=true")
        return None

    def find_supplements(self, page, doi: str) -> Tuple[List[dict], bool]:
        links = collect_links(page)
        if not links:
            return [], False
        found = [
            {"url": url_without_fragment(link["url"]), "label": link["text"] or None}
            for link in links
            if ("ars.els-cdn.com" in link["url"].lower() and is_file_url(link["url"]))
            or looks_like_supplement(link)
        ]
        return dedupe_by_target(found), True


class PmcAdapter(Adapter):
    """pmc.ncbi.nlm.nih.gov.

    Not a paywall adapter. PMC serves its `/bin/` downloads behind a
    proof-of-work page that plain HTTP cannot clear, so the browser tier is used
    to collect public supplementary files -- no credentials involved.
    """

    name = "pmc"
    hosts = ("pmc.ncbi.nlm.nih.gov", "ncbi.nlm.nih.gov")

    def find_pdf_url(self, page, doi: str) -> Optional[str]:
        value = meta_content(page, "citation_pdf_url")
        if value:
            return value
        for link in collect_links(page):
            if "/pdf/" in link["url"].lower() and link["url"].lower().endswith(".pdf"):
                return link["url"]
        return None

    def find_supplements(self, page, doi: str) -> Tuple[List[dict], bool]:
        links = collect_links(page)
        if not links:
            return [], False
        found = [
            {"url": url_without_fragment(link["url"]), "label": link["text"] or None}
            for link in links
            if "/bin/" in link["url"].lower()
        ]
        return dedupe_by_target(found), True
