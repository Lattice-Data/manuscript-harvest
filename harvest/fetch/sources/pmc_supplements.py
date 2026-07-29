"""Tier 2: supplementary files listed by PMC, fetched from the publisher.

This tier exists because of a split discovered while testing against the live
services:

- The PMC article page is plain HTML and fetches fine, and it lists every
  supplementary file as `/articles/instance/<id>/bin/<filename>`.
- Those `/bin/` URLs do **not** serve the file to a plain HTTP client. NCBI
  fronts them with a proof-of-work page ("Preparing to download ...", a `pow-*.js`
  bundle) that only a real browser clears.
- But for open-access articles the publisher's own static host serves the same
  files with no challenge and no credentials. Verified: Springer's
  `static-content.springer.com/esm/...` returns the MOESM files directly.

So the file *list* comes from PMC and the *bytes* come from the publisher. When no
publisher pattern is known, the `/bin/` URL is tried anyway and a proof-of-work
response is reported as `javascript_challenge` -- an accurate statement that the
browser tier is needed, rather than a silent zero.
"""

import re
from typing import List, Optional, Tuple
from urllib.parse import quote, urljoin

from ..http import HttpError
from ..validate import classify_denial
from .base import ROLE_SUPPLEMENT, FetchedFile, Source, SourceResult

PMC_BASE = "https://pmc.ncbi.nlm.nih.gov"
ARTICLE_URL = PMC_BASE + "/articles/{pmcid}/"

_BIN_RX = re.compile(r"""/articles/instance/\d+/bin/[^"'\s>)]+""", re.IGNORECASE)

# Springer/Nature name every supplementary object `<journal>_<year>_<art>_MOESM<n>_ESM.<ext>`
# and serve it from a predictable static path keyed on the article DOI.
_SPRINGER_ESM = "https://static-content.springer.com/esm/art%3A{doi}/MediaObjects/{filename}"


def _springer_url(doi: str, filename: str) -> Optional[str]:
    if "MOESM" not in filename.upper():
        return None
    return _SPRINGER_ESM.format(doi=quote(doi, safe=""), filename=filename)


# (name, builder) pairs. Builders return None when the pattern does not apply.
_PUBLISHER_BUILDERS = [("springer", _springer_url)]


class PmcSupplementsSource(Source):
    name = "pmc_supplements"

    def applies(self, ids) -> bool:
        return bool(ids.pmcid)

    def fetch(self, ids, need_pdf: bool, need_supplements: bool) -> SourceResult:
        result = SourceResult(tier=self.name)
        if not need_supplements:
            return result

        listing = self._list_files(ids, result)
        if listing is None:
            return result
        if not listing:
            # Page read successfully and named no supplementary files. Combined
            # with hasSuppl the fetcher can tell whether that is expected.
            result.note("listing", status="no_files_listed", count=0)
            return result

        attempted = listing[: self.max_files]
        dropped = len(listing) - len(attempted)
        if dropped > 0:
            result.problems.append(
                f"{dropped} supplementary file(s) not fetched: max_files cap "
                f"({self.max_files}) reached"
            )
            result.note("cap", status="truncated", dropped=dropped, max_files=self.max_files)

        fetched, challenged, failed = 0, 0, 0
        for bin_path, filename in attempted:
            content, url, why = self._download(ids, bin_path, filename, result)
            if content is not None:
                result.files.append(
                    FetchedFile(role=ROLE_SUPPLEMENT, name=filename, content=content,
                                url=url, label="listed by PMC")
                )
                fetched += 1
            elif why == "javascript_challenge":
                challenged += 1
            else:
                failed += 1

        if fetched and not (challenged or failed):
            result.suppl_status = "fetched"
        elif fetched:
            result.suppl_status = "partial_failure"
        elif challenged:
            # Everything is behind the proof-of-work page: the browser tier is
            # the only way through. Say so instead of reporting nothing found.
            result.suppl_status = "partial_failure"
            result.problems.append(
                f"{challenged} supplementary file(s) are behind NCBI's "
                "proof-of-work page; the browser tier is required for them"
            )
        elif failed:
            result.suppl_status = "partial_failure"

        result.note("supplements", status=result.suppl_status or "none",
                    listed=len(listing), attempted=len(attempted), fetched=fetched,
                    javascript_challenge=challenged, failed=failed)
        return result

    # -- listing ------------------------------------------------------------

    def _list_files(self, ids, result: SourceResult) -> Optional[List[Tuple[str, str]]]:
        """Return [(bin_path, filename)] from the PMC article page, or None."""
        url = ARTICLE_URL.format(pmcid=ids.pmcid)
        try:
            resp = self.http.get(url, accept="text/html")
        except HttpError as e:
            result.problems.append(f"pmc article page failed: {e}")
            result.note("listing", url=url, status="request_failed", error=str(e))
            return None
        if not resp.ok:
            result.note("listing", url=url, status="http_error", http_status=resp.status)
            return None

        denial = classify_denial(resp.url, resp.content, resp.content_type)
        if denial == "javascript_challenge":
            result.suppl_status = "page_not_parsed"
            result.problems.append("pmc article page returned a proof-of-work challenge")
            result.note("listing", url=url, status="javascript_challenge")
            return None

        seen, out = set(), []
        for path in _BIN_RX.findall(resp.text):
            filename = path.rsplit("/", 1)[-1]
            if filename and filename not in seen:
                seen.add(filename)
                out.append((path, filename))
        result.note("listing", url=url, status="ok", count=len(out))
        return out

    # -- download -----------------------------------------------------------

    def _download(self, ids, bin_path: str, filename: str, result: SourceResult):
        """Try the publisher's static host first, then PMC's /bin/ URL."""
        candidates = []
        for label, builder in _PUBLISHER_BUILDERS:
            built = builder(ids.doi, filename)
            if built:
                candidates.append((label, built))
        candidates.append(("pmc_bin", urljoin(PMC_BASE, bin_path)))

        last_reason = "download_failed"
        for label, url in candidates:
            try:
                resp = self.http.get(url)
            except HttpError as e:
                result.note("supplement_file", file=filename, via=label,
                            status="request_failed", error=str(e))
                continue
            if not resp.ok or not resp.content:
                result.note("supplement_file", file=filename, via=label,
                            status="http_error", http_status=resp.status)
                continue

            denial = classify_denial(resp.url, resp.content, resp.content_type)
            if denial:
                last_reason = denial
                result.note("supplement_file", file=filename, via=label, status=denial,
                            bytes=len(resp.content))
                continue

            result.note("supplement_file", file=filename, via=label, status="ok",
                        bytes=len(resp.content), content_type=resp.content_type)
            return resp.content, resp.url, None

        return None, None, last_reason
