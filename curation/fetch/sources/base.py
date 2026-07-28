"""What a source is, and what it hands back.

A source is one way of getting an article: an API, a package download, or a
browser session. Each is asked only for what is still missing, reports what it
attempted even when it failed, and never decides on its own that a paper has no
supplements -- that judgement belongs to the fetcher, which has the publisher's
`hasSuppl` flag.
"""

from dataclasses import dataclass, field
from typing import List, Optional

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

    def applies(self, ids) -> bool:
        raise NotImplementedError

    def fetch(self, ids, need_pdf: bool, need_supplements: bool) -> SourceResult:
        raise NotImplementedError
