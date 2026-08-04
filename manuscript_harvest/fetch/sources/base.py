"""What a source is, and what it hands back.

A source is one way of getting an article: an API, a package download, or a
browser session. Each is asked only for what is still missing, reports what it
attempted even when it failed, and never decides on its own that a paper has no
supplements -- that judgement belongs to the fetcher, which has the publisher's
`hasSuppl` flag.
"""

from dataclasses import dataclass, field
from typing import List, Optional

from ..http import HttpError
from ..validate import validate_pdf

ROLE_PDF = "fulltext_pdf"
ROLE_XML = "fulltext_xml"
ROLE_SUPPLEMENT = "supplement"
ROLE_MEDIA = "media"
ROLE_LANDING = "landing_html"


@dataclass
class FetchedFile:
    role: str
    name: str                       # suggested filename, sanitised later
    content: bytes
    url: Optional[str] = None
    content_type: str = ""
    label: Optional[str] = None     # publisher's description, when one exists
    tier: Optional[str] = None      # set by the fetcher, for manifest provenance


@dataclass
class SourceResult:
    """One source's contribution.

    `pdf_status` and `suppl_status` are None when the source did not try for
    that artifact, which is different from trying and failing.
    """

    tier: str
    files: List[FetchedFile] = field(default_factory=list)
    pdf_status: Optional[str] = None
    suppl_status: Optional[str] = None
    attempts: List[dict] = field(default_factory=list)
    problems: List[str] = field(default_factory=list)

    def by_role(self, role: str) -> List[FetchedFile]:
        return [f for f in self.files if f.role == role]

    @property
    def pdf(self) -> Optional[FetchedFile]:
        found = self.by_role(ROLE_PDF)
        return found[0] if found else None

    def note(self, action: str, **fields) -> None:
        """Record an attempt, successful or not, for the manifest."""
        entry = {"tier": self.tier, "action": action}
        entry.update(fields)
        self.attempts.append(entry)


class Source:
    """Base class. Subclasses set `name` and implement `applies` and `fetch`."""

    name = "base"

    def __init__(self, http, config: Optional[dict] = None):
        self.http = http
        self.config = config or {}

    @property
    def max_file_bytes(self) -> int:
        return int(self.config.get("max_file_mb", 200)) * 1024 * 1024

    @property
    def max_files(self) -> int:
        return int(self.config.get("max_files", 50))

    def apply_files_cap(self, items: list, result, via: Optional[str] = None,
                        noun: str = "file") -> list:
        """Trim `items` to `max_files`, recording anything dropped. Returns the kept.

        Nothing a cap drops is silent, and this was written out in three tiers whose
        wording had already come apart -- `pmc_supplements` said "supplementary
        file(s)" while `biorxiv` and the browser tier said "link(s)". No test pinned
        either, so the drift was free.

        `noun` stays a parameter rather than being settled on one word, because the
        difference is real: `pmc_supplements` counts files PMC listed, while bioRxiv
        and the browser tier count anchors matched on a rendered page, and a dropped
        anchor may not have been a distinct file at all. Saying "file" there would
        claim more than the tier knows.
        """
        kept = items[: self.max_files]
        dropped = len(items) - len(kept)
        if dropped > 0:
            result.problems.append(
                f"{dropped} supplementary {noun}(s) not fetched: max_files cap "
                f"({self.max_files}) reached"
            )
            note = {"status": "truncated", "dropped": dropped, "max_files": self.max_files}
            if via is not None:
                note["via"] = via
            result.note("cap", **note)
        return kept

    def _fetch_pdf_url(self, url: str, result) -> None:
        """GET one URL, validate it as the article PDF, and record the outcome.

        Deliberately *not* named `_fetch_pdf`. Three subclasses already define that
        name with three different signatures -- `EuropePmcSource._fetch_pdf(ids,
        result)` loops over candidate URLs and folds their failures with
        `better_pdf_failure`, and `ProxyBrowserSource._fetch_pdf(context, page,
        adapter, ids, referer, result, denial)` drives a browser -- so a base method
        under that name would be shadowed by two subclasses for which its contract is
        false, which is worse than the duplication it removes.

        The bioRxiv and PMC-OA bodies were token-identical, which is what makes them
        safe to share: one URL, already known, fetched over plain HTTP. Europe PMC's
        candidate loop is not this function and is left alone -- collapsing it would
        lose the `better_pdf_failure` folding that picks the most useful of several
        refusals.

        A refusal is never written as `fulltext.pdf`: acceptance needs PDF magic
        bytes, a successful parse, and a body that does not read like a purchase page.
        """
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

    def applies(self, ids) -> bool:
        raise NotImplementedError

    def fetch(self, ids, need_pdf: bool, need_supplements: bool) -> SourceResult:
        raise NotImplementedError
