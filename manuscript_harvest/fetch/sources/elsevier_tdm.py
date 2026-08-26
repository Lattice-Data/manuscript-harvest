"""Elsevier's TDM object API: supplementary files, and the accepted author manuscript.

Why this tier exists: for the Cell Press and ScienceDirect articles in this corpus,
`proxy_browser` meets a Cloudflare interstitial (`cf-mitigated: challenge`) rather
than a supplement page, and driving a headless browser does not get past it. 99 of
the 393 corpus entries carry a `10.1016` DOI, and their supplementary files have
until now had to be downloaded by hand. This API serves the same bytes to a plain
HTTP client holding a free dev.elsevier.com key.

    GET https://api.elsevier.com/content/object/doi/<DOI>?view=META&httpAccept=application/json
    X-ELS-APIKey: <key>

**Measured 2026-08-26.** Every claim below was checked against the live API rather
than read off Elsevier's documentation, which is wrong about one thing in a way that
made the whole approach look unworkable -- see Trap 1:

- `view=META` is *required*. The default view returns coredata with no attachment
  list at all, so a tier written against it finds nothing and reports an empty
  deposit for every article.
- A DOI addresses the endpoint directly; there is no need to resolve a PII first.
- The envelope and the field names are settled, re-measured through this tier:

      {"attachment-metadata-response": {"coredata": {...}, "attachment": [...]}}

  and each attachment carries `ref`, `filename`, `type`, `mimetype`, `size` and
  `prism:url`, none of them `@`-prefixed. See `_field`.
- **The bytes are the publisher's.** Run end to end against
  10.1016/j.ccell.2021.03.007, whose 6 supplements were hand-downloaded from
  ScienceDirect into the ground-truth set: all 6 arrived, all 6 were
  **sha256-identical**, and the tier reported `fetched` with no problems. That is
  the claim the whole tier rests on, and it is checked rather than assumed.
- **Most of what `view=META` lists is not supplementary material.** On
  10.1016/j.cell.2021.11.031 the response holds 59 attachments, of which 7 are the
  `mmc` supplements: the other 52 are article figures in three renditions
  (`IMAGE-DOWNSAMPLED`, `IMAGE-THUMBNAIL`, `IMAGE-HIGH-RES` -- ~38 MB of JPEG),
  6 `ALTIMG` inline equations, and the AAM. The `ref` filter is what keeps that
  cost off the wire; a tier that fetched every attachment would spend ~60 requests
  and tens of megabytes per article to store figures no text comes out of.
- 13 of 14 sampled corpus Elsevier DOIs enumerated supplements. The 14th,
  10.1016/j.coi.2022.102188, is a Current Opinion review that genuinely has none --
  and the hand-fetched corpus entry has none either.
- Declared `size` equalled the bytes received every time. That is a free integrity
  check, and the one thing this tier can assert that `pmc_s3` cannot: S3's `<Size>`
  is sometimes absent, while this listing always carried one.

**This tier cannot fetch article text, and must not try.** With a free key,
`/content/article/pii/<PII>` returns 403 `Requestor configuration settings
insufficient`, and `view=FULL` is 403 as well -- full text needs a separately
approved ScienceDirect entitlement. Article text stays with `europepmc`'s JATS. A
`view=FULL` path here would fail for every user holding what a free registration
grants, which is why there is not one.

**Three traps, all of them things that looked fine and were not:**

*Trap 1 -- do not filter attachments on `type`.* Supplements arrive as `APPLICATION`
but also as `VIDEO` and `VIDEO-FLASH`, so a `type == "APPLICATION"` filter silently
drops all 4 video supplements of 10.1016/j.cell.2020.11.028. The filter here is on
`ref` beginning `mmc`, which is Elsevier's own identifier for supplementary content.
Elsevier's documented example response shows only `IMAGE-*` types and no supplements
at all, which made this look like it could not work; the example article simply has
none.

*Trap 2 -- the unauthenticated endpoint echoes any ref.* With no key,
`/content/object/pii/<PII>/ref/mmc1` returns 200 with a populated `<attachment>`
element -- and so do `ref/mmc8`, which does not exist, and `ref/zzzNotAReal_9999`.
It is never evidence that a file is there, so nothing here probes availability that
way.

*Trap 3 -- adding the key makes the article endpoint worse, not better.* No key
gives usable minimized coredata; the free key gives 403. Access at Elsevier is not
monotonic in credentials, so "it worked without a key" proves nothing about what
works with one.

**The key travels in a header and must never reach a manifest.** See `Http.get`:
Elsevier also accepts `apiKey` as a query parameter, and every tier records the URL
it asked for into `corpus/*/manifest.json`, so that spelling would copy the secret
onto disk once per Elsevier article. Nothing in this module passes the key to
`note()`, and the `prism:url` values Elsevier hands back carry no credential of
their own.

**Order, and why a credentialed tier is in `OA_TIERS`.** This sits between `pmc_s3`
and `pmc_supplements` for the reason `sources/__init__` gives: `pmc_supplements`
walks into PMC's proof-of-work wall, so anything that can settle the supplements
without that wall is tried first. It is in `OA_TIERS` despite needing a key because
`--oa-only` means "never open a browser" and this opens none -- and because
`ncbi_api_key` is already an optional credential those tiers send when one is
configured. `applies` returns False without a key, so a user who never registered
sees no behaviour change and no failed attempts.

**Status.** A complete `view=META` response earns `fetched` rather than
`fetched_unverified`, on the same argument `fetcher`'s docstring makes for `pmc_s3`:
this is the deposit's own index, served by the party that holds the bytes, not a
regex over a rendered page. Here it is the publisher's index, which is as bounded as
that evidence gets.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional

from ... import text_bearing
from ..http import HttpError
from .base import (
    ROLE_MEDIA,
    ROLE_SUPPLEMENT,
    FetchedFile,
    Source,
    SourceResult,
)

OBJECT_BASE = "https://api.elsevier.com/content/object/doi/"

#: Sent as `X-ELS-APIKey`. Named here rather than inline so the leak test can assert
#: on the header this module is allowed to set and no other.
API_KEY_HEADER = "X-ELS-APIKey"

#: DOI prefixes measured to be Elsevier's, not guessed. `10.1016` is the one that
#: matters -- Cell Press and the ScienceDirect titles, 99 of 393 corpus entries --
#: and the other three are Elsevier imprints seen in the wild. The set is a fast
#: path, not the definition: `applies` also accepts a Crossref `publisher` naming
#: Elsevier, because this list cannot be complete and a missing prefix should cost
#: a slower route rather than the files.
ELSEVIER_DOI_PREFIXES = ("10.1016", "10.1053", "10.1067", "10.1078")

#: Elsevier's identifier prefix for supplementary content. The filter, per Trap 1.
SUPPLEMENT_REF_PREFIX = "mmc"

#: The accepted author manuscript: `type == "AAM-PDF"`, `ref == "am"`. Present on 8
#: of the 14 sampled articles, and verified as a real 66-page PDF on the test
#: article. Not the typeset version of record, which is what `_fetch_pdf_url`'s
#: validation is for -- it confirms the document is this article and parses as a
#: PDF, and the manifest records which rendition arrived.
AAM_TYPE = "AAM-PDF"
AAM_REF = "am"

#: Derived renditions of a *video* supplement -- a poster frame and a downsampled
#: preview -- which carry `mmc` refs and so would otherwise pass the `ref` filter.
#: Excluded rather than fetched, and the exclusion is recorded (see `_partition`): a
#: thumbnail of `mmc4.mp4` is not a supplement the article has, and counting it would
#: inflate the article's supplement count. With `text_bearing_only` on they would be
#: refused as images anyway, so this matters most in a `text_bearing_only: false`
#: run -- the one asking for everything, which would otherwise store poster JPEGs as
#: supplementary material.
#:
#: **Not observed live, and that is the honest status of this constant.** The two
#: names come from the probe that measured this API, on an article with video
#: supplements. 10.1016/j.cell.2021.11.031, re-measured here in full, has none: its
#: derived renditions are `IMAGE-DOWNSAMPLED`, `IMAGE-THUMBNAIL`, `IMAGE-HIGH-RES`
#: and `ALTIMG`, and every one of them carries an *article figure* ref (`gr1`,
#: `figs1`, `fx1`, `si1`) rather than an `mmc` one -- so the `ref` filter already
#: excludes all 52 of them and this set never fires. It stays because the video case
#: it names is real and costs nothing to guard; if a live video article ever shows a
#: third spelling, `_partition`'s note is what will say so.
DERIVED_RENDITION_TYPES = frozenset({"IMAGE-MMC-THUMBNAIL", "IMAGE-MMC-DOWNSAMPLED"})


@dataclass
class _Attachment:
    """One entry of the `view=META` attachment list."""

    ref: str
    filename: str
    url: str
    type: str = ""
    mimetype: str = ""
    size: Optional[int] = None

    @property
    def is_supplement(self) -> bool:
        return (self.ref.lower().startswith(SUPPLEMENT_REF_PREFIX)
                and self.type.upper() not in DERIVED_RENDITION_TYPES)

    @property
    def is_derived_rendition(self) -> bool:
        return (self.ref.lower().startswith(SUPPLEMENT_REF_PREFIX)
                and self.type.upper() in DERIVED_RENDITION_TYPES)

    @property
    def is_author_manuscript(self) -> bool:
        # Either signal alone is enough. The probe saw both on the same entry, and
        # requiring both would lose the PDF to a single renamed field.
        return self.type.upper() == AAM_TYPE or self.ref.lower() == AAM_REF


def _field(entry: dict, name: str) -> str:
    """`entry[name]` as a string, or `""` when absent or empty.

    The spellings are settled, not guessed. Measured live on
    10.1016/j.cell.2021.11.031, whose 59 attachments carry exactly these keys:

        @_fa  eid  filename  height  mimetype  prism:url  ref  size  type  width

    So none of the fields this tier reads is `@`-prefixed -- `@_fa` is Elsevier's
    "full attribute" marker and nothing here wants it. An earlier version of this
    function accepted `ref` *and* `@ref` for each field because the shape had not
    been checked; that tolerance is gone, because it could only hide the one failure
    that matters. A field this cannot find yields `""`, and a tier that silently
    reads `""` for every `ref` reports every Elsevier article as having no
    supplements -- so the shape is asserted in `_attachment_entries` instead, once,
    where it can be recorded.

    `str()` rather than the raw value because `size` arrives as a JSON string.
    """
    value = entry.get(name)
    return "" if value in (None, "") else str(value)


def _as_int(raw: str) -> Optional[int]:
    """`size` arrives as a JSON string. None when it is absent or not a number."""
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _attachment_entries(payload: dict) -> Optional[list]:
    """The raw attachment list from a `view=META` body, or None if there is none.

    None and `[]` are different answers and the caller depends on it: an empty list
    is Elsevier saying this article has no attachments, which licenses `none_listed`,
    while None is a body this code could not read -- and claiming a settled
    "publisher says none" over an unparsed response is how a shape change turns into
    a corpus that quietly loses its supplements.

    The envelope is settled, measured live on 10.1016/j.cell.2021.11.031:

        {"attachment-metadata-response": {"coredata": {...}, "attachment": [...]}}

    `coredata` sits beside `attachment` and is ignored. An earlier version also tried
    a blind descent into any single-key wrapper, on the grounds that the probe had
    pinned the attachment fields but not the wrapper's name; that guess is no longer
    needed and is gone, because a fallback that quietly finds an attachment list
    somewhere else is indistinguishable from one that finds none.
    """
    if not isinstance(payload, dict):
        return None
    envelope = payload.get("attachment-metadata-response")
    if not isinstance(envelope, dict) or "attachment" not in envelope:
        return None
    found = envelope["attachment"]
    # An article with a single attachment returns a **dict**, not a list. Measured on
    # a different article than the one above, and the shape a reasonable
    # implementation gets wrong: iterating a dict yields its *keys*, so the tier
    # would build attachments out of the strings "ref" and "filename" and fetch
    # nothing -- for exactly the articles that have one supplement.
    if isinstance(found, dict):
        return [found]
    if isinstance(found, list):
        return found
    return None


class ElsevierTdmSource(Source):
    name = "elsevier_tdm"

    # -- gating -------------------------------------------------------------

    @property
    def api_key(self) -> Optional[str]:
        """The configured key, or None. Whitespace-only counts as absent.

        A `config.yaml` holding `elsevier_api_key: ""` is a user who has not set one,
        and treating that as present would make `applies` true and then send an empty
        header for a guaranteed 401 on every Elsevier article in the batch.
        """
        key = self.config.get("elsevier_api_key")
        if not key:
            return None
        return str(key).strip() or None

    @property
    def _headers(self) -> Dict[str, str]:
        return {API_KEY_HEADER: self.api_key or ""}

    def applies(self, ids) -> bool:
        """A key is configured and this DOI looks like Elsevier's.

        The key gate comes first and is the important one: without it this tier is a
        no-op, so it adds no requests, no attempts and no failures for anyone who
        never registered. That is what makes it safe to put in `OA_TIERS`.
        """
        if not self.api_key:
            return False
        doi = (ids.doi or "").lower()
        if doi.startswith(ELSEVIER_DOI_PREFIXES):
            return True
        return "elsevier" in (ids.publisher or "").lower()

    # -- the one listing request --------------------------------------------

    def fetch(self, ids, need_pdf: bool, need_supplements: bool) -> SourceResult:
        result = SourceResult(tier=self.name)
        if not need_pdf and not need_supplements:
            # No request at all. The listing is the only thing this tier could ask
            # for, and asking for it to learn nothing is the cost `pmc_s3`'s own
            # "nothing is needed" test exists to prevent.
            return result

        entries = self._list_attachments(ids, result)
        if entries is None:
            # The listing failed or could not be read, so nothing was learned about
            # either artifact and both statuses stay None for the fetcher to resolve.
            return result

        attachments = [
            _Attachment(
                ref=_field(entry, "ref"),
                filename=_field(entry, "filename"),
                url=_field(entry, "prism:url"),
                type=_field(entry, "type"),
                mimetype=_field(entry, "mimetype"),
                size=_as_int(_field(entry, "size")),
            )
            for entry in entries
            if isinstance(entry, dict)
        ]

        if need_supplements:
            self._fetch_supplements(attachments, result)
        if need_pdf:
            self._fetch_author_manuscript(attachments, result)
        return result

    def _list_attachments(self, ids, result: SourceResult) -> Optional[list]:
        """`view=META` for one DOI. Returns the raw entries, or None on any failure.

        The four client-error codes are recorded as four different statuses on
        purpose: they are four different things for the operator to do. 401 means the
        key is wrong, 403 means the key is real and this content is not licensed to
        it, 404 means Elsevier has no object record for the DOI, and 429 means the
        quota this API does not publish has been reached. Folding them into one
        `download_failed` would make the first indistinguishable from the third, and
        the first is the only one a user can fix.
        """
        url = OBJECT_BASE + (ids.doi or "")
        try:
            resp = self.http.get(
                url,
                params={"view": "META", "httpAccept": "application/json"},
                headers=self._headers,
            )
        except HttpError as e:
            result.note("object_listing", url=url, status="request_failed", error=str(e))
            result.problems.append(f"Elsevier object API request failed: {e}")
            return None

        if resp.status == 401:
            result.note("object_listing", url=url, status="auth_failed",
                        http_status=resp.status)
            result.problems.append(
                "Elsevier rejected the API key (401). Check fetch.elsevier_api_key "
                "or MANUSCRIPT_HARVEST_ELSEVIER_API_KEY against dev.elsevier.com."
            )
            return None
        if resp.status == 403:
            result.note("object_listing", url=url, status="not_entitled",
                        http_status=resp.status)
            result.problems.append(
                "Elsevier returned 403 for this article's objects: the key is "
                "accepted but not entitled to them."
            )
            return None
        if resp.status == 404:
            # Not an authoritative "no supplements". It says Elsevier has no object
            # record at this DOI, which is a different fact, so no status is claimed
            # and `pmc_supplements` and the browser tier still get their turn.
            result.note("object_listing", url=url, status="no_object_record",
                        http_status=resp.status)
            return None
        if resp.status == 429:
            # Distinct from a generic failure because it is the one this tier was
            # warned about: Object Retrieval's quota is unpublished and sends no
            # `X-RateLimit-*` headers on success, so a 429 is the only signal the
            # limit exists at all.
            result.note("object_listing", url=url, status="rate_limited",
                        http_status=resp.status)
            result.problems.append(
                "Elsevier returned 429 (rate limited). Raise the "
                "api.elsevier.com entry in fetch.min_interval_overrides."
            )
            return None
        if not resp.ok:
            result.note("object_listing", url=url, status="http_error",
                        http_status=resp.status)
            result.problems.append(
                f"Elsevier object API returned HTTP {resp.status}")
            return None

        try:
            payload = resp.json()
        except ValueError as e:
            result.note("object_listing", url=url, status="unreadable_payload",
                        error=str(e))
            result.problems.append("Elsevier object API returned a body that is not JSON")
            return None

        entries = _attachment_entries(payload)
        if entries is None:
            # The self-reporting arm of `_field`'s two-spelling tolerance. Recording
            # the keys -- not the body, which would put a whole response in the
            # manifest -- is what lets a live run name the real envelope instead of
            # reading zero attachments forever.
            result.note("object_listing", url=url, status="payload_shape",
                        keys=sorted(payload.keys())[:10] if isinstance(payload, dict) else [])
            result.problems.append(
                "Elsevier object API response held no attachment list; see the "
                "payload_shape attempt for the keys it did hold"
            )
            return None

        result.note("object_listing", url=url, status="listed", attachments=len(entries))
        return entries

    # -- supplements ---------------------------------------------------------

    def _partition(self, attachments: List[_Attachment],
                   result: SourceResult) -> List[_Attachment]:
        """The `mmc` supplements, with the derived video renditions recorded and dropped."""
        derived = [a for a in attachments if a.is_derived_rendition]
        if derived:
            result.note("attachment_filter", action_detail="derived_rendition",
                        status="skipped", skipped=len(derived),
                        files=[a.filename for a in derived],
                        types=sorted({a.type for a in derived}))
        return [a for a in attachments if a.is_supplement]

    def _role_of(self, attachment: _Attachment) -> str:
        """`media` for audio/video, `supplement` for everything else.

        Decided on the extension through `text_bearing`'s own set rather than on
        Elsevier's `VIDEO`/`VIDEO-FLASH` type, so that the question "is this
        audio/video" has one answer in this codebase. A second copy of that
        judgement here is exactly the drift `text_bearing`'s module docstring was
        written to prevent -- and it would be a *disagreeing* copy, since the
        `type` field is the one Trap 1 says not to trust.
        """
        if text_bearing.extension(attachment.filename) in text_bearing.AUDIO_VIDEO_EXTENSIONS:
            return ROLE_MEDIA
        return ROLE_SUPPLEMENT

    def _fetch_supplements(self, attachments: List[_Attachment],
                           result: SourceResult) -> None:
        supplements = self._partition(attachments, result)
        if not supplements:
            # Elsevier is the publisher and this is its own attachment list, so an
            # attachment list with no multimedia component in it is the publisher
            # saying there are none -- the definition of `none_listed`.
            #
            # **This diverges from `pmc_s3`, which leaves the equivalent case unset,
            # and the divergence is the point.** That tier declines to because
            # `supplement_or_media` is a filename heuristic, and calling its silence
            # `none_listed` would promote a guess into a statement about what the
            # publisher deposited. The filter here reads `ref` -- Elsevier's own
            # field, the one Trap 1 says to trust over `type` -- so the objection
            # does not transfer.
            #
            # It is settled, and it sits directly above the `hasSuppl` alarm in
            # `fetcher._supplement_status`, so it *suppresses*
            # `expected_but_missing`. That is intended: `hasSuppl` comes from Europe
            # PMC's index, and for an Elsevier article that is frequently a
            # metadata-only record, so letting the index override the publisher's own
            # list would be backwards. This is the case that precedence was written
            # for -- "a source that owns the content can state authoritatively that
            # there are none, even when the index disagrees."
            #
            # Two things keep it honest. The claim needs a *readable* attachment list
            # -- `_list_attachments` returns None for a body it could not parse, and
            # this line is never reached for one. And it does not end the run: the
            # tier loop clears `need_supplements` only when files arrived or every
            # named file was policy-refused, so `pmc_supplements` and the browser
            # tier still get their turn, and anything they fetch outranks this.
            result.suppl_status = "none_listed"
            result.note("supplements", status="none_listed", listed=0,
                        attachments_seen=len(attachments))
            return

        listed: Dict[str, List[_Attachment]] = {ROLE_SUPPLEMENT: [], ROLE_MEDIA: []}
        for attachment in supplements:
            listed[self._role_of(attachment)].append(attachment)

        # Text-bearing before the cap, always -- `keep_text_bearing`'s docstring has
        # the measurement: on `pmc_s3` eight figures took cap slots from eight
        # supplementary tables. Per role, because `suppl_status` is a sentence about
        # supplementary material and a refused video says nothing about it.
        for role in (ROLE_SUPPLEMENT, ROLE_MEDIA):
            listed[role] = self.keep_text_bearing(
                listed[role], result, name_of=lambda a: a.filename, role=role,
                via="object_api")

        # "file", not "link": the API enumerated these, so a dropped one is a known
        # file rather than an anchor that may not have been distinct.
        wanted = self.apply_files_cap(listed[ROLE_SUPPLEMENT], result,
                                      via="object_api", noun="file")
        dropped_supplements = len(listed[ROLE_SUPPLEMENT]) - len(wanted)
        media = listed[ROLE_MEDIA][: max(self.max_files - len(wanted), 0)]
        dropped_media = len(listed[ROLE_MEDIA]) - len(media)
        if dropped_media:
            result.problems.append(
                f"{dropped_media} supplementary video(s) not fetched: the max_files "
                f"cap ({self.max_files}) goes to files text can be extracted from first"
            )
            result.note("cap", status="truncated_media", dropped=dropped_media,
                        max_files=self.max_files)

        attempted = ([(ROLE_SUPPLEMENT, a) for a in wanted]
                     + [(ROLE_MEDIA, a) for a in media])
        refused = failed = lost_supplements = 0
        for role, attachment in attempted:
            if self._refuse_oversize(attachment, result):
                refused += 1
                if role == ROLE_SUPPLEMENT:
                    lost_supplements += 1
                continue
            content = self._download(attachment, result)
            if content is None:
                failed += 1
                if role == ROLE_SUPPLEMENT:
                    lost_supplements += 1
                continue
            result.files.append(
                FetchedFile(
                    role=role,
                    name=attachment.filename,
                    content=content,
                    url=attachment.url,
                    content_type=attachment.mimetype,
                    label=("Elsevier TDM" if role == ROLE_SUPPLEMENT
                           else "Elsevier TDM (supplementary video)"),
                )
            )

        if failed:
            result.problems.append(
                f"{failed} of {len(attempted)} file(s) listed by the Elsevier object "
                f"API could not be fetched; see attempts for the per-file reason"
            )

        kept = len(result.by_role(ROLE_SUPPLEMENT))
        if not kept:
            # Nothing on disk to make a claim about. Silent unless something was
            # lost: a deposit of nothing but videos, all refused by policy, leaves
            # the question to the fetcher, whose answer is `none_text_bearing`.
            if lost_supplements or dropped_supplements:
                result.suppl_status = "partial_failure"
        elif lost_supplements:
            result.suppl_status = "partial_failure"
        elif dropped_supplements:
            result.suppl_status = "fetched_unverified"
        else:
            # `fetched`, not `fetched_unverified`: see the module docstring. This is
            # the publisher's own index of the deposit, which bounds the set.
            result.suppl_status = "fetched"

        result.note("supplements", status=result.suppl_status or "none",
                    listed=len(supplements), attempted=len(attempted),
                    supplements=kept, media=len(result.by_role(ROLE_MEDIA)),
                    refused=refused, failed=failed,
                    dropped_supplements=dropped_supplements,
                    dropped_media=dropped_media,
                    not_text_bearing=len(result.skipped_not_text_bearing))

    # -- the author manuscript ----------------------------------------------

    def _fetch_author_manuscript(self, attachments: List[_Attachment],
                                 result: SourceResult) -> None:
        """The AAM PDF, when one is offered.

        Stored as `fulltext.pdf`. It is the accepted manuscript rather than the
        typeset version of record, which is a real difference and is recorded in the
        attempt -- but for text extraction it is cosmetic, and this is article PDF
        that Cloudflare otherwise puts out of reach entirely. Present on 8 of the 14
        sampled articles.

        Routed through `_fetch_pdf_url` so it gets the same magic-bytes, parse and
        purchase-page validation as every other tier's PDF: an AAM that is really an
        error page must not become `fulltext.pdf` any more than a paywall notice
        would.
        """
        for attachment in attachments:
            if not attachment.is_author_manuscript or not attachment.url:
                continue
            result.note("author_manuscript", rendition="accepted_author_manuscript",
                        ref=attachment.ref, type=attachment.type)
            self._fetch_pdf_url(attachment.url, result, headers=self._headers)
            return

    # -- one file ------------------------------------------------------------

    def _refuse_oversize(self, attachment: _Attachment, result: SourceResult) -> bool:
        """True when the declared `size` puts this file over the cap. Nothing is transferred.

        The wording matches `pmc_s3._refuse_oversize` and `proxy_browser`'s
        deliberately: a user reading "not fetched: N MB exceeds the 200 MB cap"
        should not have to know which tier refused it to recognise the same cap.
        """
        if attachment.size is None or attachment.size <= self.max_file_bytes:
            return False
        megabytes = round(attachment.size / 1024 / 1024, 1)
        result.problems.append(
            f"{attachment.filename} not fetched: {megabytes} MB exceeds the "
            f"{self.config.get('max_file_mb', 200)} MB cap (fetch.max_file_mb)"
        )
        result.note("supplement_file", ref=attachment.ref, status="too_large",
                    megabytes=megabytes)
        return True

    def _download(self, attachment: _Attachment,
                  result: SourceResult) -> Optional[bytes]:
        """GET one attachment. Returns the bytes, or None with a note.

        The declared-size assertion is the part worth keeping: the listing says how
        many bytes to expect, so a truncated transfer is detectable for free, and a
        short body is **not** written to disk. Every other supplement path in this
        codebase has to accept whatever arrives.
        """
        try:
            resp = self.http.get(attachment.url, headers=self._headers)
        except HttpError as e:
            result.note("supplement_file", ref=attachment.ref,
                        status="request_failed", error=str(e))
            return None
        if not resp.ok:
            result.note("supplement_file", ref=attachment.ref,
                        status="download_failed", http_status=resp.status)
            return None
        if attachment.size is not None and len(resp.content) != attachment.size:
            result.note("supplement_file", ref=attachment.ref, status="size_mismatch",
                        declared=attachment.size, received=len(resp.content))
            result.problems.append(
                f"{attachment.filename} not stored: Elsevier declared "
                f"{attachment.size} bytes and sent {len(resp.content)}"
            )
            return None
        result.note("supplement_file", ref=attachment.ref, status="fetched",
                    bytes=len(resp.content))
        return resp.content
