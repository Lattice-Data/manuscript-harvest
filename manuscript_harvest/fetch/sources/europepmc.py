"""Tier 1: Europe PMC.

Two endpoints, both verified against the live service:

    GET /{PMCID}/supplementaryFiles          -> ZIP of every supplementary file
    GET /{PMCID}/fullTextXML                 -> JATS XML
    fullTextUrlList[].url (documentStyle=pdf) -> open-access PDF

The supplements endpoint is the cheapest complete answer available anywhere: one
request, one ZIP, no page scraping. But it is not universal -- verified live,
PMC3258128 returns a 3 MB ZIP while PMC8426186 returns 404 despite the search API
reporting `hasSuppl: Y`. So a 404 here means "Europe PMC holds no supplementary
archive", NOT "this article has no supplements". The fetcher reconciles that
against `hasSuppl` and the next tier tries elsewhere.

The XML is fetched because it is free and structured. The pipeline reads PDFs
today, but JATS is what would replace `pdf_loader`'s heuristic section detection.
"""

import io
import zipfile
from typing import List

from ..http import HttpError
from ..validate import validate_pdf
from .base import ROLE_PDF, ROLE_SUPPLEMENT, ROLE_XML, FetchedFile, Source, SourceResult

REST_BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"
RENDER_PDF = "https://europepmc.org/articles/{pmcid}?pdf=render"


class EuropePmcSource(Source):
    name = "europepmc"

    def applies(self, ids) -> bool:
        # Needs a PMCID for the supplements endpoint; the PDF URLs come from the
        # Tier 0 lookup and can exist even when the article is not in PMC.
        return bool(ids.pmcid) or bool(ids.open_access_pdf_urls())

    def fetch(self, ids, need_pdf: bool, need_supplements: bool) -> SourceResult:
        result = SourceResult(tier=self.name)

        if need_pdf:
            self._fetch_pdf(ids, result)
            if ids.pmcid:
                self._fetch_xml(ids, result)
        if need_supplements and ids.pmcid:
            self._fetch_supplements(ids, result)

        return result

    # -- JATS XML -----------------------------------------------------------

    def _fetch_xml(self, ids, result: SourceResult) -> None:
        url = f"{REST_BASE}/{ids.pmcid}/fullTextXML"
        try:
            resp = self.http.get(url, accept="application/xml")
        except HttpError as e:
            result.note("xml", url=url, status="request_failed", error=str(e))
            return
        if resp.status == 404:
            result.note("xml", url=url, status="not_available", http_status=404)
            return
        if not resp.ok or not resp.content:
            result.note("xml", url=url, status="http_error", http_status=resp.status)
            return
        result.files.append(
            FetchedFile(role=ROLE_XML, name="fulltext.nxml", content=resp.content,
                        url=url, label="Europe PMC JATS XML")
        )
        result.note("xml", url=url, status="ok", bytes=len(resp.content))

    # -- PDF ----------------------------------------------------------------

    def _candidate_pdf_urls(self, ids) -> List[str]:
        urls = list(ids.open_access_pdf_urls())
        if ids.pmcid:
            # Europe PMC's own renderer, used when fullTextUrlList has no PDF
            # entry but the article is in EPMC.
            fallback = RENDER_PDF.format(pmcid=ids.pmcid)
            if fallback not in urls:
                urls.append(fallback)
        return urls

    def _fetch_pdf(self, ids, result: SourceResult) -> None:
        candidates = self._candidate_pdf_urls(ids)
        if not candidates:
            result.pdf_status = "not_found"
            result.note("pdf", status="not_found", detail="no open-access PDF URL known")
            return

        for url in candidates:
            try:
                resp = self.http.get(url, accept="application/pdf")
            except HttpError as e:
                result.note("pdf", url=url, status="download_failed", error=str(e))
                continue

            if not resp.ok:
                result.note("pdf", url=url, status="download_failed", http_status=resp.status)
                continue

            accepted, status, meta = validate_pdf(
                resp.content, content_type=resp.content_type, url=resp.url
            )
            result.note("pdf", url=url, status=status, **meta)
            if accepted:
                result.pdf_status = status
                result.files.append(
                    FetchedFile(
                        role=ROLE_PDF,
                        name="fulltext.pdf",
                        content=resp.content,
                        url=resp.url,
                        content_type=resp.content_type,
                    )
                )
                return
            result.pdf_status = status  # keep the most informative failure

        if result.pdf_status is None:
            result.pdf_status = "download_failed"

    # -- supplements --------------------------------------------------------

    def _fetch_supplements(self, ids, result: SourceResult) -> None:
        url = f"{REST_BASE}/{ids.pmcid}/supplementaryFiles"
        try:
            resp = self.http.get(url, accept="application/zip")
        except HttpError as e:
            result.suppl_status = "partial_failure"
            result.problems.append(f"europepmc supplementaryFiles failed: {e}")
            result.note("supplements", url=url, status="download_failed", error=str(e))
            return

        if resp.status == 404:
            # Endpoint says it has no archive. Leave the status unset so the
            # fetcher can decide, using hasSuppl, whether that is expected.
            result.note("supplements", url=url, status="not_available", http_status=404)
            return

        if not resp.ok:
            result.suppl_status = "partial_failure"
            result.note("supplements", url=url, status="download_failed", http_status=resp.status)
            return

        # The endpoint sometimes answers 200 with something that is not an archive
        # (an error page, for instance). Check the magic bytes before unpacking so
        # the manifest says "not a zip" rather than leaking a BadZipFile.
        if not resp.content[:2] == b"PK":
            result.suppl_status = "partial_failure"
            result.problems.append(
                "europepmc supplementaryFiles returned a non-ZIP body "
                f"({resp.content_type or 'unknown type'}, {len(resp.content)} bytes)"
            )
            result.note("supplements", url=url, status="not_a_zip",
                        content_type=resp.content_type, bytes=len(resp.content))
            return

        try:
            members = _unpack_zip(resp.content, self.max_files, self.max_file_bytes)
        except (zipfile.BadZipFile, ValueError) as e:
            result.suppl_status = "partial_failure"
            result.problems.append(f"europepmc supplement ZIP unreadable: {e}")
            result.note("supplements", url=url, status="unreadable_zip", error=str(e))
            return

        if not members:
            result.note("supplements", url=url, status="empty_zip", count=0)
            return

        for name, content in members:
            result.files.append(
                FetchedFile(
                    role=ROLE_SUPPLEMENT,
                    name=name,
                    content=content,
                    url=url,
                    label="Europe PMC supplementary archive",
                )
            )
        # Plain `fetched`, one of only two places that earns it: the archive IS
        # the deposit, so its member list bounds the set rather than guessing at
        # it. Nothing here pattern-matches a page. See `store.SUPPL_SETTLED`.
        result.suppl_status = "fetched"
        result.note("supplements", url=url, status="fetched", count=len(members))


def _unpack_zip(content: bytes, max_files: int, max_file_bytes: int):
    """Return [(name, bytes)] for the real files in a ZIP.

    Directory entries are skipped, only basenames are kept (so no member can
    write outside the corpus directory), and both the per-file size and the file
    count are capped -- an archive should not be able to fill the disk.
    """
    out = []
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            if info.file_size > max_file_bytes:
                raise ValueError(
                    f"member {info.filename!r} is {info.file_size} bytes, "
                    f"over the {max_file_bytes}-byte cap"
                )
            out.append((info.filename, archive.read(info)))
            if len(out) >= max_files:
                break
    return out
