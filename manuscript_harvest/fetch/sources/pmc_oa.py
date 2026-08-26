"""The PMC Open Access package.

    GET https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi?id=PMC3258128

returns, verified live:

    <link format="tgz" href="ftp://ftp.ncbi.nlm.nih.gov/pub/pmc/oa_package/.../PMC3258128.tar.gz"/>
    <link format="pdf" href="ftp://ftp.ncbi.nlm.nih.gov/pub/pmc/oa_pdf/.../gkr715.PMC3258128.pdf"/>

Two things this module has to get right:

- The hrefs are `ftp://`. NCBI serves the identical paths over HTTPS, so they are
  rewritten rather than fetched over FTP.
- **Being in PMC is not the same as being in the OA subset.** Articles deposited
  under a funder mandate live in the separate Author Manuscript Collection, and
  for those `oa.fcgi` answers with an `<error>` element. That is a routing fact,
  not a failure, so it is reported as `not_in_oa_subset` and the fetcher moves on
  to the next tier.

The package is a tarball holding the JATS XML, the article's figure images, and
any supplementary files.

**Current caveat, measured not assumed.** The hrefs point into
`ftp.ncbi.nlm.nih.gov/pub/pmc/oa_package/...`, and that tree is being retired --
its HTTPS root now lists only `deprecated/`. Both articles tested (PMC8426186 and
PMC3258128) return 404 for their advertised package over HTTPS, and 550 over FTP.
So the tarball download is off by default (`fetch.try_oa_package`), and this tier
runs mainly for what `oa.fcgi` still answers reliably: whether the article is in
the OA subset at all, plus a PDF link when one is listed. The unpack path is kept
and tested because the service still advertises the format.
"""

import io
import re
import tarfile
from typing import List, Optional, Tuple
from xml.etree import ElementTree

from ..http import HttpError
from ..validate import validate_pdf
from .base import (
    ROLE_MEDIA,
    ROLE_PDF,
    ROLE_SUPPLEMENT,
    ROLE_XML,
    FetchedFile,
    Source,
    SourceResult,
)

OA_SERVICE = "https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi"
_FTP_PREFIX = "ftp://ftp.ncbi.nlm.nih.gov"
_HTTPS_PREFIX = "https://ftp.ncbi.nlm.nih.gov"

_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".tif", ".tiff", ".bmp", ".eps"}
# Names PMC uses for genuine supplementary material, as opposed to the article's
# own figure images which also sit in the package.
#
# Related to but deliberately not `adapters.base.SUPPLEMENT_HINT`, which answers the
# same question about a *rendered page*. The inputs differ, and so does what is safe:
# that one matches href plus anchor text, where a bare `suppl` also prefixes
# "supplier", "supply" and "supplant", so it anchors on whole path segments. This one
# only ever sees a deposited file's basename, where bare `suppl` is unambiguous. If
# either list gains a publisher pattern, review both.
_SUPPLEMENT_MARKERS = re.compile(
    r"(suppl|_s\d+\b|-s\d+\b|moesm|esm|additional[_-]?file|media-?\d|table[_-]?s\d|data[_-]?s\d)",
    re.IGNORECASE,
)


def _extension(name: str) -> str:
    """The lowercased extension including its dot, or `""` when there is none."""
    lowered = name.lower()
    return lowered[lowered.rfind("."):] if "." in lowered else ""


def supplement_or_media(name: str) -> str:
    """`ROLE_SUPPLEMENT` or `ROLE_MEDIA` for one deposited file, from its name alone.

    The tail of `_classify`, lifted out so `pmc_s3` decides this the same way rather
    than growing a second copy of the policy. Both tiers see the same deposit -- the
    tarball and the S3 object listing hold the same files -- so two answers for one
    filename would mean the same article sorting differently depending on which tier
    reached it first, which is the drift `_classify`'s docstring already records
    against `europepmc._unpack_zip`.

    The marker check comes *first*, and PMC8941949 is why: it deposits both
    `NIHMS1758707-supplement-1.jpg` and `nihms-1758707-f0001.jpg`. They are the same
    file type and only the name separates a supplementary figure from one of the
    article's own, so an extension-first order would file the supplement under
    `media/` where no curator looks for it.

    Unknown extensions fall to `ROLE_SUPPLEMENT` deliberately: the cost of a figure
    landing in `supplementary/` is clutter, and the cost of a supplementary table
    landing in `media/` is a curator not finding it.
    """
    if _SUPPLEMENT_MARKERS.search(name):
        return ROLE_SUPPLEMENT
    return ROLE_MEDIA if _extension(name) in _IMAGE_EXTENSIONS else ROLE_SUPPLEMENT


def ftp_to_https(href: str) -> str:
    """NCBI serves the same paths over HTTPS; prefer that over FTP."""
    if href.startswith(_FTP_PREFIX):
        return _HTTPS_PREFIX + href[len(_FTP_PREFIX):]
    if href.startswith("ftp://"):
        # Some records use a bare host; upgrade the scheme and hope for a mirror.
        return "https://" + href[len("ftp://"):]
    return href


class PmcOaSource(Source):
    name = "pmc_oa"

    def applies(self, ids) -> bool:
        return bool(ids.pmcid)

    def fetch(self, ids, need_pdf: bool, need_supplements: bool) -> SourceResult:
        result = SourceResult(tier=self.name)

        links, error = self._lookup(ids, result)
        if error:
            # Not in the OA subset (or no record at all): a routing outcome.
            if need_pdf:
                result.pdf_status = "not_in_oa_subset"
            return result
        if not links:
            return result

        # The explicit pdf link is authoritative for the article PDF.
        if need_pdf and "pdf" in links:
            self._fetch_pdf_url(links["pdf"], result)

        wants_package = (need_supplements or (need_pdf and result.pdf is None))
        if wants_package and "tgz" in links:
            if self.config.get("try_oa_package", False):
                self._fetch_package(links["tgz"], result, need_pdf=need_pdf and result.pdf is None)
            else:
                result.note(
                    "package", url=links["tgz"], status="skipped",
                    detail="fetch.try_oa_package is off; the oa_package FTP tree is deprecated",
                )

        if need_pdf and result.pdf is None and result.pdf_status is None:
            result.pdf_status = "not_found"

        return result

    # -- oa.fcgi ------------------------------------------------------------

    def _lookup(self, ids, result: SourceResult) -> Tuple[dict, Optional[str]]:
        try:
            resp = self.http.get(OA_SERVICE, params={"id": ids.pmcid}, accept="application/xml")
        except HttpError as e:
            result.problems.append(f"pmc oa service failed: {e}")
            result.note("oa_lookup", status="request_failed", error=str(e))
            return {}, "request_failed"

        if not resp.ok:
            result.note("oa_lookup", status="http_error", http_status=resp.status)
            return {}, "http_error"

        try:
            root = ElementTree.fromstring(resp.content)
        except ElementTree.ParseError as e:
            result.problems.append(f"pmc oa service returned unparseable XML: {e}")
            result.note("oa_lookup", status="unparseable_xml", error=str(e))
            return {}, "unparseable_xml"

        error = root.find("error")
        if error is not None:
            code = error.get("code", "unknown")
            result.problems.append(
                f"{ids.pmcid} is not in the PMC Open Access subset (oa.fcgi: {code})"
            )
            result.note("oa_lookup", status="not_in_oa_subset", code=code,
                        detail=(error.text or "").strip())
            return {}, code

        links = {}
        for link in root.iter("link"):
            fmt = link.get("format")
            href = link.get("href")
            if fmt and href:
                links[fmt] = ftp_to_https(href)

        result.note("oa_lookup", status="ok", formats=sorted(links))
        return links, None

    # -- .tar.gz package ----------------------------------------------------

    def _fetch_package(self, url: str, result: SourceResult, need_pdf: bool) -> None:
        try:
            resp = self.http.get(url, accept="application/gzip")
        except HttpError as e:
            result.problems.append(f"pmc oa package download failed: {e}")
            result.note("package", url=url, status="download_failed", error=str(e))
            return

        if not resp.ok:
            result.note("package", url=url, status="download_failed", http_status=resp.status)
            return

        try:
            members = _unpack_tgz(resp.content, self.max_files, self.max_file_bytes)
        except (tarfile.TarError, ValueError, EOFError) as e:
            result.problems.append(f"pmc oa package unreadable: {e}")
            result.note("package", url=url, status="unreadable_archive", error=str(e))
            return

        supplements, media, xml_member, pdf_member = _classify(members)

        if xml_member:
            name, content = xml_member
            result.files.append(
                FetchedFile(role=ROLE_XML, name="fulltext.nxml", content=content, url=url,
                            label=f"JATS XML ({name})")
            )

        if need_pdf and pdf_member:
            name, content = pdf_member
            accepted, status, meta = validate_pdf(content, url=url)
            result.pdf_status = status
            result.note("pdf_from_package", member=name, status=status, **meta)
            if accepted:
                result.files.append(
                    FetchedFile(role=ROLE_PDF, name="fulltext.pdf", content=content, url=url)
                )

        for name, content in supplements:
            result.files.append(
                FetchedFile(role=ROLE_SUPPLEMENT, name=name, content=content, url=url,
                            label="PMC OA package")
            )
        for name, content in media:
            result.files.append(
                FetchedFile(role=ROLE_MEDIA, name=name, content=content, url=url,
                            label="PMC OA package (article media)")
            )

        if supplements:
            # Plain `fetched`: the OA package is the deposit, so unpacking it
            # bounds the set. See `store.SUPPL_SETTLED`.
            result.suppl_status = "fetched"
        result.note(
            "package",
            url=url,
            status="unpacked",
            supplements=len(supplements),
            media=len(media),
            has_xml=xml_member is not None,
        )


def _unpack_tgz(content: bytes, max_files: int, max_file_bytes: int) -> List[Tuple[str, bytes]]:
    """Return [(basename, bytes)] for regular files in a .tar.gz.

    Only regular files are read, and only their basenames are kept, so neither an
    absolute path, a `..` traversal, nor a symlink member can write outside the
    corpus directory.
    """
    out: List[Tuple[str, bytes]] = []
    with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as archive:
        for member in archive:
            if not member.isfile():
                continue
            if member.size > max_file_bytes:
                raise ValueError(
                    f"member {member.name!r} is {member.size} bytes, "
                    f"over the {max_file_bytes}-byte cap"
                )
            handle = archive.extractfile(member)
            if handle is None:
                continue
            name = member.name.replace("\\", "/").rsplit("/", 1)[-1]
            if not name:
                continue
            out.append((name, handle.read()))
            if len(out) >= max_files:
                break
    return out


def _classify(members: List[Tuple[str, bytes]]):
    """Split package members into supplements, article media, XML, and the PDF.

    The article's own figure images travel in the same package as genuine
    supplementary material. Keeping them apart matters: `supplementary/` is where
    a curator looks for supplementary tables, and padding it with figure JPEGs
    would bury them.

    **The split is no longer this tier's alone: `pmc_s3` shares its tail through
    `supplement_or_media`, and that tier does deliver files.** Until it existed the
    policy described nothing on disk, because `europepmc._unpack_zip` marks every ZIP
    member a supplement and the OA-package route is off by default
    (`fetch.try_oa_package`). Measured over the 36 local articles before `pmc_s3`
    landed: 435 supplementary entries, 382 of them from `europepmc` and none from
    here, and 297 of those 382 are `.jpg`/`.gif` -- so the burying this docstring
    warns about is what a corpus actually looks like, and no `media/` directory
    exists. `10.1038_s41586-021-03465-8` holds the mixture plainly, with an article
    figure (`01_..._Fig5_HTML.gif`) beside a real supplement
    (`02_..._Fig9_ESM.gif`).

    Giving `europepmc` the same split is the obvious fix and is deliberately still
    not done: it would move those files out of `supplementary/` on every future
    fetch, which changes per-file extraction statuses and can move an article's own
    status, and `_unpack_zip`'s caller sets `suppl_status = "fetched"` unconditionally
    where this one guards on `if supplements:` -- so a figures-only ZIP needs a
    decision first. Recorded rather than done, so the inconsistency is at least not
    silent. `pmc_s3` was safe to share with because it is new: it has no files on
    disk to reclassify, and it makes the same `if supplements:` judgement this one
    does.
    """
    supplements: List[Tuple[str, bytes]] = []
    media: List[Tuple[str, bytes]] = []
    xml_member = None
    pdfs: List[Tuple[str, bytes]] = []

    for name, content in members:
        lowered = name.lower()
        if lowered.endswith(".nxml") or lowered.endswith(".xml"):
            if xml_member is None:
                xml_member = (name, content)
            continue
        if lowered.endswith(".pdf") and not _SUPPLEMENT_MARKERS.search(name):
            pdfs.append((name, content))
            continue
        bucket = media if supplement_or_media(name) == ROLE_MEDIA else supplements
        bucket.append((name, content))

    # The article PDF is the shortest-named candidate; supplementary PDFs carry
    # extra qualifiers in their names and were already routed above.
    pdf_member = None
    if pdfs:
        pdfs.sort(key=lambda item: len(item[0]))
        pdf_member = pdfs[0]
        supplements.extend(pdfs[1:])

    return supplements, media, xml_member, pdf_member
