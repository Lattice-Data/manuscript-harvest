"""What a source is, and what it hands back.

A source is one way of getting an article: an API, a package download, or a
browser session. Each is asked only for what is still missing, reports what it
attempted even when it failed, and never decides on its own that a paper has no
supplements -- that judgement belongs to the fetcher, which has the publisher's
`hasSuppl` flag.
"""

from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence, Tuple

from ... import text_bearing
from ..http import HttpError
from ..validate import validate_pdf

ROLE_PDF = "fulltext_pdf"
ROLE_XML = "fulltext_xml"
ROLE_SUPPLEMENT = "supplement"
ROLE_MEDIA = "media"
ROLE_LANDING = "landing_html"

#: Where a text-bearing refusal happened, which is the one thing a reader of the
#: manifest cannot work out for themselves: it says whether the request was spent.
#: `before_download` is a tier refusing a name it read from a listing, a PMC page,
#: a bioRxiv page or an anchor -- no bytes moved. `on_unpack` is a member of an
#: archive that arrived as one blob, so the transfer was already paid and only the
#: disk write and the manifest entry are saved. `after_download` is
#: `fetcher.fetch_publication` catching a file whose real name only became known
#: from a `Content-Disposition` header after the body was in hand.
BEFORE_DOWNLOAD = "before_download"
ON_UNPACK = "on_unpack"
AFTER_DOWNLOAD = "after_download"


def not_text_bearing_note(tier: str, skipped: Sequence[Tuple[str, str, str]],
                          where: str, via: Optional[str] = None) -> dict:
    """One `attempts` entry for a whole filter pass. `skipped` is (name, role, reason).

    Every filename is listed, not a truncated sample, and that is the point of the
    note: the reader has to be able to see what `text_bearing_only: false` would
    have fetched, and a list of three examples cannot answer that question --
    unlike `pmc_s3`'s `key_shape` note, where three examples of an unparseable key
    are enough to diagnose a shape. Bounded by what a tier can name for one
    article: `max_files` on the download paths, and the largest deposit measured in
    this corpus is 58 objects.

    One note rather than one per file, because 138 articles here hold non-text
    supplements and 71% of their supplement slots are non-text -- per-file entries
    would bury the attempts that record a decision this tool actually made. A tier
    may still emit more than one: `pmc_s3` asks per role, so its supplements and its
    article figures are refused in separate passes and each note carries its own
    `roles` breakdown. That is the division `suppl_status` is decided on, and a merged
    note would hide it.
    """
    reasons: dict = {}
    roles: dict = {}
    for _name, role, reason in skipped:
        reasons[reason] = reasons.get(reason, 0) + 1
        roles[role] = roles.get(role, 0) + 1
    note = {"tier": tier, "action": "text_bearing_filter", "status": "skipped",
            "where": where, "skipped": len(skipped), "reasons": reasons,
            "roles": roles, "files": [name for name, _role, _reason in skipped]}
    if via is not None:
        note["via"] = via
    return note


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
    #: What the user should *do* about a supplement obstacle this tier hit, kept
    #: apart from `problems` because the two have different lifetimes. What
    #: happened is true forever and belongs in the manifest either way; "re-run
    #: with --headed" stops being true the moment a later tier gets the files.
    #: `fetch_publication` emits these only if the run ends with supplements
    #: still missing. See `problems` in `fetcher.fetch_publication`.
    suppl_advice: List[str] = field(default_factory=list)
    #: Files this tier named and did not fetch because no text can be extracted
    #: from them: `[{"name", "role", "reason"}]`. Read by the fetcher, which needs
    #: the count to tell an all-media deposit (`none_text_bearing`, settled) from an
    #: article whose supplements really are missing (`expected_but_missing`, the
    #: alarm). It is *not* in `problems`: nothing went wrong, and a `!` line per
    #: figure across 138 articles would drown the lines that mean something.
    skipped_not_text_bearing: List[dict] = field(default_factory=list)

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

    @property
    def text_bearing_only(self) -> bool:
        """Is this run restricted to files text can come out of? See `text_bearing`."""
        return text_bearing.policy_is_on(self.config)

    def keep_text_bearing(self, items: list, result, name_of: Optional[Callable] = None,
                          role: str = ROLE_SUPPLEMENT, where: str = BEFORE_DOWNLOAD,
                          via: Optional[str] = None) -> list:
        """Drop the items no text can come out of, recording them. Returns the kept.

        Written once here for the same reason `apply_files_cap` is: four tiers know a
        filename before they spend the request, and their wording had already come
        apart the last time three of them counted the same thing
        (`apply_files_cap`'s docstring). `name_of` exists because they hold that name
        differently -- an `_Object`, a `(bin_path, filename)` pair, a bare URL, an
        anchor dict -- and the alternative is four copies of the predicate call.

        Applied *before* `apply_files_cap`, always. The cap is a request budget, and
        spending a slot on a figure that is then refused is the same displacement
        `pmc_s3._fetch_payload` re-orders its payload to avoid: measured there,
        8 figures took cap slots from 8 supplementary tables, two of them over 9 MB.
        That ordering is pinned per tier rather than left to this sentence -- the
        sentence is exactly the kind `apply_files_cap` below records drifting for want
        of a test. `test_a_refused_figure_never_spends_a_cap_slot_a_table_needed`
        (`pmc_s3`) and `test_a_figure_anchor_costs_neither_a_request_nor_a_cap_slot`
        (`proxy_browser`) are the two that reach a cap; the archive tiers keep theirs
        inside `_unpack_zip`/`_unpack_tgz` and are pinned in `tests/test_units.py`.

        A no-op when the policy is off, so `text_bearing_only: false` reproduces
        today's behaviour exactly -- not merely the same files, but the same order,
        the same cap arithmetic and no note.
        """
        if not self.text_bearing_only:
            return list(items)
        name_of = name_of or (lambda item: item)
        kept: list = []
        skipped: List[Tuple[str, str, str]] = []
        for item in items:
            name = name_of(item)
            reason = text_bearing.skip_reason(name)
            if reason is None:
                kept.append(item)
            else:
                skipped.append((name, role, reason))
        self.record_not_text_bearing(result, skipped, where=where, via=via)
        return kept

    def record_not_text_bearing(self, result, skipped: Sequence[Tuple[str, str, str]],
                                where: str = BEFORE_DOWNLOAD,
                                via: Optional[str] = None) -> None:
        """Record what a filter refused: one note, plus the per-file list for the fetcher.

        Taken as a separate method because two tiers do not filter by name in
        advance at all -- `europepmc` gets a ZIP and `pmc_oa` a tarball, one blob
        each -- so their refusals happen inside the unpack and arrive here already
        decided. Same record either way, and `where` is what tells the two apart.
        """
        if not skipped:
            return
        for name, role, reason in skipped:
            result.skipped_not_text_bearing.append(
                {"name": name, "role": role, "reason": reason})
        result.attempts.append(
            not_text_bearing_note(self.name, skipped, where=where, via=via))

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
