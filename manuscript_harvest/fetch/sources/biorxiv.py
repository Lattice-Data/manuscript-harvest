"""Tier 3: bioRxiv and medRxiv preprints.

Preprints under the 10.1101 prefix are open by definition, so no authentication
is involved. The details API gives the version number and a JATS XML link:

    GET https://api.biorxiv.org/details/biorxiv/<doi>

from which the PDF and supplementary-material URLs are constructed:

    https://www.biorxiv.org/content/<doi>v<version>.full.pdf
    https://www.biorxiv.org/content/<doi>v<version>.supplementary-material

The supplementary-material page is HTML, so this is the one open-access tier that
scrapes. It is a narrow scrape -- media links follow a fixed
`/DC<n>/embed/media-<n>.<ext>` shape -- and when the pattern does not match, the
result is `page_not_parsed` rather than a silent zero.
"""

import re
from typing import List, Optional, Tuple
from urllib.parse import urljoin

from ..http import HttpError
from ..validate import validate_pdf
from .base import ROLE_PDF, ROLE_SUPPLEMENT, ROLE_XML, FetchedFile, Source, SourceResult

DETAILS_API = "https://api.biorxiv.org/details/{server}/{doi}"
CONTENT_BASE = {
    "biorxiv": "https://www.biorxiv.org",
    "medrxiv": "https://www.medrxiv.org",
}

_HREF_RX = re.compile(r"""href=["']([^"']+)["']""", re.IGNORECASE)
_MEDIA_RX = re.compile(r"""/content/[^\s"']*?/DC\d+/embed/media-[^\s"']+""", re.IGNORECASE)


class BiorxivSource(Source):
    name = "biorxiv"

    def applies(self, ids) -> bool:
        # Covers both the historical 10.1101 prefix and openRxiv's 10.64898, plus
        # anything Europe PMC classifies as a preprint (source PPR). A preprint from
        # another server simply fails the details lookup and records that.
        return ids.is_preprint

    def fetch(self, ids, need_pdf: bool, need_supplements: bool) -> SourceResult:
        result = SourceResult(tier=self.name)

        details = self._details(ids, result)
        if not details:
            if need_pdf:
                result.pdf_status = "not_found"
            return result
        server, version, jats_url = details
        base = CONTENT_BASE.get(server, CONTENT_BASE["biorxiv"])
        stem = f"{base}/content/{ids.doi}v{version}"

        if need_pdf:
            self._fetch_pdf(f"{stem}.full.pdf", result)
            if jats_url:
                self._fetch_jats(jats_url, result)
        if need_supplements:
            self._fetch_supplements(f"{stem}.supplementary-material", base, result)

        return result

    # -- details API --------------------------------------------------------

    def _details(self, ids, result: SourceResult) -> Optional[Tuple[str, str, Optional[str]]]:
        """Return (server, version, jats_url). Tries bioRxiv, then medRxiv."""
        for server in ("biorxiv", "medrxiv"):
            url = DETAILS_API.format(server=server, doi=ids.doi)
            try:
                resp = self.http.get(url, accept="application/json")
            except HttpError as e:
                result.note("details", url=url, status="request_failed", error=str(e))
                continue
            if not resp.ok:
                result.note("details", url=url, status="http_error", http_status=resp.status)
                continue
            try:
                collection = resp.json().get("collection") or []
            except ValueError as e:
                result.note("details", url=url, status="unparseable_json", error=str(e))
                continue
            if not collection:
                result.note("details", url=url, status="no_record")
                continue

            # The API lists every version; the newest is the one to fetch.
            latest = max(collection, key=lambda item: _version_key(item.get("version")))
            version = str(latest.get("version") or "1")
            actual_server = (latest.get("server") or server).lower()
            result.note("details", url=url, status="ok", server=actual_server, version=version)
            return actual_server, version, latest.get("jatsxml")

        result.problems.append("neither bioRxiv nor medRxiv has a record for this DOI")
        return None

    # -- artifacts ----------------------------------------------------------

    def _fetch_pdf(self, url: str, result: SourceResult) -> None:
        try:
            resp = self.http.get(url, accept="application/pdf")
        except HttpError as e:
            result.pdf_status = "download_failed"
            result.note("pdf", url=url, status="download_failed", error=str(e))
            return
        if not resp.ok:
            result.pdf_status = "download_failed"
            result.note("pdf", url=url, status="download_failed", http_status=resp.status)
            return

        accepted, status, meta = validate_pdf(
            resp.content, content_type=resp.content_type, url=resp.url
        )
        result.pdf_status = status
        result.note("pdf", url=url, status=status, **meta)
        if accepted:
            result.files.append(
                FetchedFile(role=ROLE_PDF, name="fulltext.pdf", content=resp.content,
                            url=resp.url, content_type=resp.content_type)
            )

    def _fetch_jats(self, url: str, result: SourceResult) -> None:
        """The details API hands us structured XML for free; keep it."""
        try:
            resp = self.http.get(url, accept="application/xml")
        except HttpError as e:
            result.note("jats", url=url, status="request_failed", error=str(e))
            return
        if resp.ok and resp.content:
            result.files.append(
                FetchedFile(role=ROLE_XML, name="fulltext.nxml", content=resp.content, url=url,
                            label="bioRxiv JATS XML")
            )
            result.note("jats", url=url, status="ok", bytes=len(resp.content))
        else:
            result.note("jats", url=url, status="http_error", http_status=resp.status)

    def _fetch_supplements(self, page_url: str, base: str, result: SourceResult) -> None:
        try:
            resp = self.http.get(page_url, accept="text/html")
        except HttpError as e:
            result.suppl_status = "page_not_parsed"
            result.problems.append(f"supplementary-material page failed: {e}")
            result.note("supplements", url=page_url, status="request_failed", error=str(e))
            return

        if resp.status == 404:
            result.note("supplements", url=page_url, status="no_page", http_status=404)
            return
        if not resp.ok:
            result.suppl_status = "page_not_parsed"
            result.note("supplements", url=page_url, status="http_error", http_status=resp.status)
            return

        links = _media_links(resp.text, base)
        if not links:
            # bioRxiv is the authority on its own preprints, so a page that loads
            # and lists nothing really means nothing -- report `none_listed`
            # rather than the false alarm `page_not_parsed`. Verified against
            # 10.1101/2022.01.02.474723, whose supplement page carries no links.
            # This matters because the index flag cannot be trusted here either:
            # Europe PMC says hasSuppl=N for 10.1101/2025.07.21.666016, which
            # does have media-1.pdf and media-2.zip.
            result.suppl_status = "none_listed"
            result.note("supplements", url=page_url, status="none_listed",
                        detail="page loaded; no supplementary links present")
            return

        # "link": these are anchors matched on a rendered page, so a dropped one is
        # not known to have been a distinct file.
        attempted = self.apply_files_cap(links, result, noun="link")

        fetched = 0
        for url in attempted:
            try:
                resp = self.http.get(url)
            except HttpError as e:
                result.problems.append(f"supplement {url} failed: {e}")
                result.note("supplement_file", url=url, status="request_failed", error=str(e))
                continue
            if not resp.ok or not resp.content:
                result.note("supplement_file", url=url, status="http_error",
                            http_status=resp.status)
                continue
            result.files.append(
                FetchedFile(role=ROLE_SUPPLEMENT, name=url, content=resp.content, url=url,
                            content_type=resp.content_type, label="bioRxiv supplementary material")
            )
            fetched += 1

        if fetched and fetched == len(attempted):
            # Not `fetched`, even though bioRxiv owns its preprints. It is the
            # authority on whether any exist -- that is why the empty case above
            # is `none_listed` -- but this list is `_media_links` over rendered
            # HTML, and owning the content does not make a regex over it an
            # enumeration. See `store.SUPPL_SETTLED`.
            result.suppl_status = "fetched_unverified"
        elif fetched:
            result.suppl_status = "partial_failure"
        else:
            result.suppl_status = "page_not_parsed"
        result.note("supplements", url=page_url, status=result.suppl_status,
                    found=len(links), attempted=len(attempted), fetched=fetched)


def _version_key(value) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def _media_links(html: str, base: str) -> List[str]:
    """Absolute URLs of supplementary media on a bioRxiv supplement page."""
    seen, out = set(), []
    candidates = _MEDIA_RX.findall(html) + [
        href for href in _HREF_RX.findall(html) if _MEDIA_RX.search(href)
    ]
    for href in candidates:
        url = urljoin(base, href)
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out
