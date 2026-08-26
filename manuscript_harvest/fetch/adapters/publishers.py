"""Per-publisher adapters, for the sites where the generic one is not enough.

Each is deliberately thin: locate the PDF, locate the supplement downloads,
report honestly when the expected container is absent. Selectors here are the
part of this codebase most likely to break when a publisher redesigns, which is
why `find_supplements` distinguishes "found none" from "could not read the page".
"""

import re
from typing import List, Optional, Tuple
from urllib.parse import urljoin, urlparse

# ScienceDirect article URLs carry the Elsevier PII, from which the PDF URL can be
# constructed when the page exposes no link. Public because the browser tier reads
# the same PII out of a URL to reach Cell Press -- one shape, defined once.
PII_RX = re.compile(r"/pii/([A-Z0-9]+)", re.IGNORECASE)

from .base import (
    Adapter,
    collect_links,
    is_file_url,
    looks_like_supplement,
    meta_content,
    supplements_from_links,
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
                # `get_attribute` returns the raw HTML attribute, not the
                # resolved DOM property -- an anchor written as a site-relative
                # path (e.g. `/articles/s41586-...pdf`) comes back exactly that
                # way, and a relative path handed straight to a downloader is
                # not a valid URL.
                return urljoin(page.url or "", href)
        except Exception:
            pass
        return None

    @staticmethod
    def _is_supplement(link: dict, url: str) -> bool:
        """Matched against `url`, the fragment-stripped href, not the raw one.

        The article page carries one `#MOESM<n>` anchor per supplementary object, and
        it is the `MOESM` test below that the fragment would fool: `#MOESM4` puts the
        string in the raw href while the target is the article itself. `is_file_url`
        is not at risk -- it strips the fragment internally, by construction -- so on
        a normal article URL, which names no file, those anchors are already refused
        at the gate. The stripped form is what keeps that true for a publisher whose
        article URL does end in something the extension check accepts.

        A named function rather than a lambda so the distinction survives being read.
        """
        if not is_file_url(url):
            return False
        on_static_host = (
            "static-content.springer.com/esm" in url.lower()
            or "media.springernature.com" in url.lower()
        )
        return "MOESM" in url.upper() or on_static_host or looks_like_supplement(link)

    def find_supplements(self, page, doi: str) -> Tuple[List[dict], bool]:
        return supplements_from_links(page, self._is_supplement)


class WileyAdapter(Adapter):
    """onlinelibrary.wiley.com (Atypon).

    PDFs live at `/doi/pdfdirect/<doi>`; supplements come from
    `/action/downloadSupplement?doi=...&file=...`.
    """

    name = "wiley"
    hosts = ("wiley.com",)

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
        # Raw href, as below: only Nature needs the stripped form.
        return supplements_from_links(page, lambda link, url: (
            "downloadsupplement" in link["url"].lower() or looks_like_supplement(link)))


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
                    # Same raw-attribute trap as `NatureAdapter`: ScienceDirect
                    # writes these as a site-relative path
                    # (`/science/article/pii/.../pdfft?...`), and passing that
                    # straight to a downloader raised `Invalid URL` rather than
                    # ever reaching the network. Resolved against `page.url` so
                    # the proxied hostname is preserved, same as the
                    # PII-constructed fallback below.
                    return urljoin(page.url or "", href)
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
        return supplements_from_links(page, lambda link, url: (
            ("ars.els-cdn.com" in link["url"].lower() and is_file_url(link["url"]))
            or looks_like_supplement(link)))


class ScienceAdapter(Adapter):
    """science.org (AAAS: Science, Science Advances, Science Immunology, ...).

    Exists because the generic adapter got this wrong in the worst way available.
    Science pages carry no `citation_pdf_url`, so the fallback there is "the first
    anchor on the page whose URL ends in `.pdf` and is not a supplement" -- and on
    10.1126/science.adf1226 that anchor pointed at
    `assets.ctfassets.net/.../CG000239_Visium_Spatial_Gene_Expression_User_Guide_Rev_F.pdf`,
    a 71-page 10x Genomics reagent manual on a third-party CDN. It was stored as
    `fulltext.pdf`, recorded `ok`, and extracted to 1,493 blocks whose first one is
    `10xGenomics.com`; the article, "Comprehensive cell atlas of the first-trimester
    developing human brain", was never fetched. Link order decided that, and link
    order is not something a page owes us.

    So the URL is constructed rather than discovered. `/doi/pdf/<doi>?download=true`
    is what 14 of the 16 Science papers this corpus fetched through the browser tier
    resolved to on their own -- the generic fallback was finding the right anchor by
    luck, and this makes it the rule. Built on the *page's* host so it keeps working
    through the proxy, whose rewritten hostname is the only one the browser can
    reach.

    Supplements need no help: they are all under `/doi/suppl/<doi>/suppl_file/`,
    which `looks_like_supplement` already matches, so this defers to the generic
    behaviour rather than restating it.
    """

    name = "science"
    hosts = ("science.org", "sciencemag.org")

    def find_pdf_url(self, page, doi: str) -> Optional[str]:
        # Honoured if it ever appears -- a publisher's own declaration beats a
        # pattern -- but measured absent on every Science page seen so far.
        value = meta_content(page, "citation_pdf_url")
        if value and ".pdf" in value.lower():
            return value
        if not doi:
            return None
        parsed = urlparse(page.url or "")
        if not parsed.scheme or not parsed.netloc:
            return None
        return f"{parsed.scheme}://{parsed.netloc}/doi/pdf/{doi}?download=true"

    def find_supplements(self, page, doi: str) -> Tuple[List[dict], bool]:
        return supplements_from_links(page, lambda link, url: looks_like_supplement(link))


class PmcAdapter(Adapter):
    """pmc.ncbi.nlm.nih.gov.

    Not a paywall adapter. PMC serves its `/bin/` downloads behind a
    proof-of-work page that plain HTTP cannot clear, so the browser tier is used
    to collect public supplementary files -- no credentials involved.
    """

    name = "pmc"
    hosts = ("ncbi.nlm.nih.gov",)

    def find_pdf_url(self, page, doi: str) -> Optional[str]:
        value = meta_content(page, "citation_pdf_url")
        if value:
            return value
        for link in collect_links(page):
            if "/pdf/" in link["url"].lower() and link["url"].lower().endswith(".pdf"):
                return link["url"]
        return None

    def find_supplements(self, page, doi: str) -> Tuple[List[dict], bool]:
        return supplements_from_links(
            page, lambda link, url: "/bin/" in link["url"].lower())
