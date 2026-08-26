"""PMC's Open Access deposit, read straight out of its S3 bucket.

    GET https://pmc-oa-opendata.s3.amazonaws.com/?list-type=2&prefix=PMC8941949.

answers, verified live, with an S3 `ListObjectsV2` document -- anonymously: no
credentials, no request signature, no proof-of-work page, no browser.

    <ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
      <Contents>
        <Key>PMC8941949.1/PMC8941949.1.pdf</Key><Size>6368896</Size>
        <ETag>...</ETag><LastModified>...</LastModified>
      </Contents>
      ...
      <KeyCount>...</KeyCount><MaxKeys>1000</MaxKeys><IsTruncated>...</IsTruncated>
      <NextContinuationToken>...</NextContinuationToken>

**Why this tier exists.** The `/articles/instance/<id>/bin/<file>` endpoints that
`pmc_supplements` lists files from are fronted by a JavaScript proof-of-work page
and then hard 403 for a plain client, and that is the single most common
supplement blocker in this project's logs. S3 is PMC's own sanctioned bulk
channel and is not challenged. It also replaces the `oa_package` FTP tree that
`pmc_oa` documents as retired -- `oa.fcgi` still advertises packages that 404.
Coverage measured over the local corpus: 322 of 393 articles have a PMCID that is
present in the bucket, so this is a majority route rather than a rescue path.

**Keys are `PMC<id>.<version>/<filename>`, and the version is not always 1.**
Measured on a `PMC1002*` sample: 123 distinct article prefixes, versions 1 and 2
both in use, 95 keys sitting under a non-1 version. So the tier lists the prefix
and takes the highest version it finds rather than assuming `.1`. Storing v1 of a
revised article would put superseded text under a DOI that now means something
else -- the same trap `biorxiv._version_key` exists for.

**But the highest version is not always a deposit.** Measured live on PMC8494648
and PMC8828466, both in the local corpus: `.1` is the publisher's version of record
(29 and 21 objects, a CC BY article PDF of 21,305,885 B and 7,788,183 B, 25 and 17
payload files), while `.2` holds exactly `<prefix>.json`, `.txt` and `.xml` -- the
NIHMS author-manuscript deposit, `is_manuscript` true and TDM-licensed in the
bucket's own per-version JSON, with no PDF and no payload at all, and stable that
way for two months. So the version number is not a revision counter, and
`max(version)` on those two articles reports the article PDF absent over a listing
that names it and contributes nothing to a set of 25 files -- for two DOIs whose
recorded manifests show `europepmc` answering 500 and the PDF arriving only through
a headed browser on an institutional proxy, which is the case this tier exists to
remove. `_latest_version` therefore takes the highest version that *holds the
article* -- one naming a `<prefix>.pdf` or any payload object -- and records what it
passed over. It deliberately does not look past a version that holds a real deposit
with fewer files than an older one (PMC10901738: 29 payload objects against v1's
31): a revision is allowed to drop supplements, and that is the whole reason
versions are not merged.

One article, measured whole (PMC8941949, an NIH author manuscript):

    PMC8941949.1/PMC8941949.1.pdf     6,368,896 B   the article PDF
    PMC8941949.1/PMC8941949.1.xml                   JATS XML
    PMC8941949.1/PMC8941949.1.txt                   plain text  -- skipped
    PMC8941949.1/PMC8941949.1.json                  metadata    -- skipped
    PMC8941949.1/NIHMS1758707-supplement-1.jpg ... -14.xlsx   14 supplements
    PMC8941949.1/nihms-1758707-f0001.jpg ...                  article figures

The article's own four objects are identified *by name*, because PMC names them
and nothing else after the version prefix -- no heuristic needed and none used.
Everything else goes through `pmc_oa.supplement_or_media`, which is why
`NIHMS1758707-supplement-1.jpg` is a supplement while `nihms-1758707-f0001.jpg`
is article media even though both are JPEGs. Elsevier-style deposits occur in the
same bucket (`PMC10020035.2/mmc1.pdf`) and land as supplements, correctly: the
article PDF was already claimed by name, so the shortest-name tie-break
`pmc_oa._classify` needs inside a tarball has nothing to decide here.

**And with `fetch.text_bearing_only` on -- the default -- `media/` goes quiet
again.** Every extension `pmc_oa.supplement_or_media` routes to `ROLE_MEDIA` is an
image extension, so the figures below are refused from the listing before a byte
moves. That is the intended effect and not a side effect: the cap, the requests and
the manifest entries all go to files something downstream can read. The paragraphs
below describe what this tier does with `text_bearing_only: false`, and remain the
reason the role split exists at all -- the split is what makes the refusal
per-role, so a skipped figure cannot be mistaken for a missing supplement.

**This is the first tier whose files actually land in `media/`.**
`pmc_oa._classify` has sorted article figures out of `supplementary/` since it was
written, but its package route is off by default and `europepmc` calls every ZIP
member a supplement, so nothing has ever reached disk under that role -- its
docstring says so. Here the figures are real downloads charged against
`fetch.max_corpus_gb`, so a corpus built with this tier holds article images a
corpus built before it did not. Keeping them is the same judgement `pmc_oa` made:
they are part of the deposit, and the split is what stops them burying the
supplementary tables a curator is looking for.

**Which means the cap has to spend on supplements first, or the split arrives one
step too late to do that.** `max_files` bounds the whole payload, figures included
-- one article, one request budget -- and S3 lists keys in binary order, which puts
`41586_2021_3604_Fig1_HTML.jpg` ahead of `41586_2021_3604_MOESM3_ESM.pdf`. Measured
on two articles of the local corpus at the shipped `max_files: 50`: PMC10232368
lists 58 payload objects, 8 of them `Fig<n>_HTML.jpg` -- and Springer Nature
interleaves those with the supplements, so they fall *inside* the cap rather than
after it. In raw key order those 8 figures took cap slots from 8 supplementary
tables (MOESM3..MOESM9 and MOESM40, two of them over 9 MB), while the payload holds
exactly 50 supplement-classified objects, so ordering by role loses none of them. PMC8494637 lists 57 and lost 7 the same way, where 2 is the floor. So
`_fetch_payload` charges the supplements against the cap before the figures. The
budget is unchanged, still `max_files` requests per article, and what a truncation
costs is figures -- which nothing downstream reads, `extract/extractor.py` iterates
`record["supplementary"]` and never `record["media"]` -- rather than the tables a
curator came for.

**The one thing only this tier can do.** `<Size>` arrives *in the listing*, so a
file over `fetch.max_file_mb` is refused before a byte of it moves. Every other
tier learns a size either from a `Content-Length` it must spend a separate request
asking for (`proxy_browser._oversize_mb`) or from an archive it has already
downloaded whole (`europepmc._unpack_zip`, `pmc_oa._unpack_tgz`) -- by which point
the cap saves disk but no transfer.

Bytes verified against the copies a human downloads: the S3
`NIHMS1758707-supplement-10.xlsx` (26,835 B, `PK` magic) is sha256-identical to
the same file fetched by hand from the PMC article page. `supplement-9.pdf`
(121,584 B, `%PDF`) and the 6.3 MB article PDF also arrived whole.

**Politeness.** The bucket is a bulk AWS object store and publishes no courtesy
interval, unlike NCBI's E-utilities which asks for one -- and one article here is
up to `max_files` separate requests rather than a single archive, so the global
3.0 s interval would spend ~45 s asleep on the 14-supplement article above and
~150 s at the cap. `fetch.min_interval_overrides` lowers it for this host alone;
see `Http._wait_for_host`.
"""

import re
from typing import Dict, List, NamedTuple, Optional, Tuple
from urllib.parse import quote
from xml.etree import ElementTree

from ..http import HttpError
from .base import (
    ROLE_MEDIA,
    ROLE_SUPPLEMENT,
    ROLE_XML,
    FetchedFile,
    Source,
    SourceResult,
)
from .pmc_oa import supplement_or_media

BUCKET = "https://pmc-oa-opendata.s3.amazonaws.com"

#: `ListObjectsV2`. 1000 is the service maximum for `max-keys` and is also its
#: default; sending it makes the page size a stated fact rather than a server one.
_LIST_PARAMS = {"list-type": "2", "max-keys": "1000"}

#: A guard, not a limit. 10 pages is 10,000 objects for one article, orders of
#: magnitude past the largest deposit measured, so tripping it means the bucket is
#: handing back a continuation token that never resolves. Better a recorded
#: incomplete listing -- which costs this tier its `fetched` -- than a loop.
_MAX_LIST_PAGES = 10

#: `PMC8941949.1/NIHMS1758707-supplement-1.jpg` in parts. The version is bounded to
#: four digits, and the four are not slack: measured over the local corpus's PMCIDs
#: the versions in use are 1 (5943 keys), 2 (379), 319 (56) and 358 (23), so a
#: single-digit pattern would have thrown away the 79 keys under the last two.
#: "Small integers" is all the layout promises and anything
#: past four digits is reported rather than guessed at. A filename may itself
#: contain `/` -- none observed, but the
#: layout does not forbid it -- so the group is greedy and the basename is taken
#: separately, for the same reason `pmc_oa._unpack_tgz` keeps only basenames: no key
#: the bucket serves can steer a write out of the article directory.
_KEY_RX = re.compile(r"^(?P<prefix>(?P<pmcid>PMC\d+)\.(?P<version>\d{1,4}))/(?P<filename>.+)$")

#: The article's own objects, by the suffix that follows the version prefix.
_SELF_NAMED_ROLES = {".pdf": "pdf", ".xml": "xml"}

#: Also named after the prefix, and deliberately not taken. `.txt` is the article
#: text and `.json` its metadata record -- both are derived from the PDF and JATS
#: this tier already stores, so fetching them would add corpus bytes and a second
#: source of truth for text the extraction stage produces itself. Skipped, and
#: recorded as skipped, so a manifest never leaves it looking like they were missed.
_SELF_NAMED_SIDECARS = {".txt", ".json"}


class _Object(NamedTuple):
    """One listed object: its key, its declared size, and the key parsed.

    `size` is Optional because a malformed `<Size>` has to mean *unknown* rather
    than zero -- see `_as_int`.
    """

    key: str
    size: Optional[int]
    prefix: str        # "PMC8941949.1"
    version: int       # 1
    filename: str      # "NIHMS1758707-supplement-1.jpg"


def _local(tag: str) -> str:
    """An element's name without its namespace."""
    return tag.rsplit("}", 1)[-1]


def _child_text(element, name: str) -> str:
    """The text of the first direct child with this local name, or `""`."""
    for child in element:
        if _local(child.tag) == name:
            return (child.text or "").strip()
    return ""


def _as_int(text: str) -> Optional[int]:
    """`<Size>` as an int, or None when it is absent or not a number.

    None is *unknown*, not zero, and the difference is the whole point of the
    pre-download refusal: reading a malformed size as 0 would wave a 4 GB object
    through the cap. Unknown skips the early refusal and falls back to the
    post-download length check in `_download`.
    """
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def _read_page(root) -> Tuple[List[Tuple[str, Optional[int]]], Optional[str]]:
    """One `ListObjectsV2` page as `[(key, size)]`, plus its continuation token.

    Children are matched on their *local* name rather than through the
    `{http://s3.amazonaws.com/doc/2006-03-01/}` prefix the bucket actually sends.
    Both the trap and the fix matter here: `root.find("Contents")` silently returns
    nothing against a document carrying a default namespace, so a namespace-blind
    parse would report every article as an empty deposit. Hardcoding the 2006 URI
    fixes that but fails the same silent way if AWS ever versions the schema, and
    one `rsplit` per element cannot.

    The continuation token, not `<IsTruncated>`, decides whether to ask again: a
    truncated page without a token leaves nothing to ask *with*, and the token's
    absence is what actually ends the enumeration.
    """
    keys: List[Tuple[str, Optional[int]]] = []
    token = ""
    for child in root:
        local = _local(child.tag)
        if local == "Contents":
            key = _child_text(child, "Key")
            if key:
                keys.append((key, _as_int(_child_text(child, "Size"))))
        elif local == "NextContinuationToken":
            token = (child.text or "").strip()
    return keys, (token or None)


def _object_url(key: str) -> str:
    """The HTTPS URL for one object.

    `safe="/"` keeps the version directory a path separator and encodes everything
    else, because a deposited filename is publisher-supplied: a space or a `#` is a
    legal S3 key character and either one would truncate or mis-route the request if
    it went out raw.
    """
    return f"{BUCKET}/{quote(key, safe='/')}"


def _parse_key(key: str, size: Optional[int]) -> Optional[_Object]:
    """`PMC<id>.<v>/<filename>` split up, or None if the key is not that shape."""
    match = _KEY_RX.match(key)
    if match is None:
        return None
    return _Object(
        key=key,
        size=size,
        prefix=match.group("prefix"),
        version=int(match.group("version")),
        # Basename only: see `_KEY_RX`.
        filename=match.group("filename").replace("\\", "/").rsplit("/", 1)[-1],
    )


def _self_named_suffix(item: _Object) -> Optional[str]:
    """`".pdf"` for `PMC8941949.1/PMC8941949.1.pdf`, None for anything else.

    Compared against the whole prefix rather than by counting dots, so a supplement
    that happens to start with it (`PMC8941949.1.tables.xlsx`) yields
    `".tables.xlsx"` -- not in either self-named set -- and is classified on its
    name like every other supplement.
    """
    head, tail = item.filename[: len(item.prefix)], item.filename[len(item.prefix):]
    if head.lower() != item.prefix.lower():
        return None
    return tail.lower() or None


class PmcS3Source(Source):
    name = "pmc_s3"

    def applies(self, ids) -> bool:
        return bool(ids.pmcid)

    def fetch(self, ids, need_pdf: bool, need_supplements: bool) -> SourceResult:
        result = SourceResult(tier=self.name)

        listed, complete = self._list_objects(ids, result)
        if not listed:
            if not complete:
                # The listing itself failed, so nothing was learned about either
                # artifact and both statuses stay None for the fetcher to resolve.
                # `pmc_oa` answers its own version of this with
                # `not_in_oa_subset` -- that is right there because the routing
                # signal is the only thing `oa.fcgi` produces, and wrong here:
                # a listing we could not read leaves the question of what the
                # bucket holds genuinely open, and a later tier or a re-run can
                # still answer it.
                return result
            # A complete listing with no keys is an authoritative absence: the
            # bucket *is* the Open Access subset, so this article is not in it.
            # It is the answer for a minority of the corpus -- 322 of its 393
            # articles are in the bucket -- and it is a routing fact rather than a
            # failure, the same one `oa.fcgi` states with an `<error>` element.
            if need_pdf:
                result.pdf_status = "not_in_oa_subset"
            result.note("deposit", status="not_in_bucket", keys=0)
            return result

        # A listing that held keys but none this tier could parse is deliberately
        # *not* short-circuited: it falls through as an empty deposit, which reports
        # `not_found` for the PDF ("we read the enumeration and it names none") --
        # not `not_in_oa_subset`, which would claim the article is absent from a
        # bucket that just answered for it.
        article, payload, skipped = _split_deposit(
            self._latest_version(ids, listed, complete, result))
        result.note(
            "deposit",
            has_pdf="pdf" in article,
            has_xml="xml" in article,
            files=len(payload),
            skipped=skipped,
        )

        if need_pdf:
            if not complete:
                self._withhold_article_files(article, result)
            else:
                self._fetch_article_pdf(article.get("pdf"), result)
                # Same rule as `europepmc`: the XML is taken while the full text is
                # still wanted. It is not free here -- it is one more request per
                # article, not a member of an archive already in hand -- and every
                # article this bucket holds is in PMC, so `europepmc`'s `fullTextXML`
                # has usually answered for it two tiers earlier. This copy earns its
                # request in the case where that endpoint did not, which is the same
                # case in which the PDF is still missing.
                self._fetch_article_xml(article.get("xml"), result)

        if need_supplements:
            self._fetch_payload(payload, complete, result)

        return result

    # -- listing ------------------------------------------------------------

    def _list_objects(self, ids, result: SourceResult):
        """Every key under `<PMCID>.`, and whether the enumeration is complete.

        Returns `([(key, size)], complete)`. `complete` is what licenses this tier's
        `fetched` later, so it is False for every way the walk can end early --
        a dead request, an unreadable body, or the page guard -- and the keys
        gathered so far are still returned, because files we can name are worth
        fetching even when the set around them is not bounded.

        The trailing dot on the prefix is load-bearing. `prefix=PMC1002` matches
        `PMC10020035.2/...` too, so without it another article's deposit would
        arrive as this one's supplements. With it, the next character must be the
        version separator, and `PMC8941949.` cannot reach `PMC89419491.`.
        """
        listed: List[Tuple[str, Optional[int]]] = []
        token: Optional[str] = None

        for page in range(1, _MAX_LIST_PAGES + 1):
            params = dict(_LIST_PARAMS, prefix=f"{ids.pmcid}.")
            if token:
                # Passed as a parameter rather than pasted into the URL so it is
                # percent-encoded on the way out: the token is opaque base64 and
                # carries `+`, `/` and `=`.
                params["continuation-token"] = token
            url = f"{BUCKET}/"
            try:
                resp = self.http.get(url, params=params, accept="application/xml")
            except HttpError as e:
                result.problems.append(f"pmc s3 listing failed: {e}")
                result.note("listing", url=url, status="request_failed", page=page,
                            error=str(e))
                return listed, False
            if not resp.ok:
                # A line of its own, like the three sibling exits around it. This was
                # the only one that recorded an attempt and no problem, so a 503 on
                # page 2 -- which costs the article its `fetched` and, before that,
                # nearly cost it the right *version* -- reached the manifest as
                # nothing a reader of the summary would see.
                result.problems.append(
                    f"pmc s3 listing page {page} returned HTTP {resp.status}; "
                    f"the enumeration is incomplete"
                )
                result.note("listing", url=url, status="http_error", page=page,
                            http_status=resp.status)
                return listed, False
            try:
                root = ElementTree.fromstring(resp.content)
            except ElementTree.ParseError as e:
                result.problems.append(f"pmc s3 listing returned unparseable XML: {e}")
                result.note("listing", url=url, status="unparseable_xml", page=page,
                            error=str(e))
                return listed, False

            page_keys, token = _read_page(root)
            listed.extend(page_keys)
            if not token:
                result.note("listing", url=url, status="ok", keys=len(listed), pages=page)
                return listed, True

        result.problems.append(
            f"pmc s3 listing for {ids.pmcid} was still truncated after "
            f"{_MAX_LIST_PAGES} pages ({len(listed)} keys); treating it as incomplete"
        )
        result.note("listing", status="still_truncated", keys=len(listed),
                    pages=_MAX_LIST_PAGES)
        return listed, False

    def _latest_version(self, ids, listed, complete: bool,
                        result: SourceResult) -> List[_Object]:
        """The files of the highest version that holds the article, and only those.

        Versions are not merged. A v2 deposit is the article as it now stands, and
        pooling it with v1 would mix two revisions' supplement sets under one
        article -- inflating the count with files the current version dropped, and
        picking whichever `<prefix>.pdf` was listed first as the full text.

        **"Highest" is not `max(version)`, because a version directory is not always
        a deposit.** PMC8494648 and PMC8828466 are both in the local corpus and both
        answer with a `.2` holding exactly `<prefix>.json`, `.txt` and `.xml` -- the
        NIHMS author-manuscript record -- over a `.1` holding the version of record,
        its 21 MB CC BY PDF and 25 payload files. `max(version)` there reports
        `not_found` for a PDF named in the same response and contributes nothing to a
        set of 25 files, which is worse than not running at all: the tier looks like
        an answer. So a version whose objects are nothing but the article's own
        metadata sidecars is passed over, and the versions passed over are recorded
        rather than dropped silently -- `_split_deposit` is asked the question, so
        "holds the article" cannot drift from what the rest of the tier means by it.

        Deliberately narrow. A version holding a real deposit is taken even when an
        older one held more files (PMC10901738: 29 payload objects against v1's 31),
        because a revision is allowed to withdraw supplements and reading that as
        damage is how versions would get merged again by the back door.

        `available` is what this walk saw, which is not the same as what the bucket
        holds: keys sort lexicographically, so `PMC<id>.1/...` is served before
        `PMC<id>.2/...` and an enumeration that ended early is *systematically*
        missing the newest version rather than a random one. The note carries
        `complete_listing` for exactly that reason, and `fetch` does not take the
        article's own files off an incomplete walk at all -- see
        `_withhold_article_files`.

        A key that does not match the layout is recorded and dropped, and pointedly
        does *not* cost the article its `fetched`. The tempting argument is that an
        unreadable key might have been a supplement, so the set is not bounded; the
        answer is that an S3 prefix routinely carries a zero-byte directory-marker
        object (`PMC8941949.1/`, no filename), which would then demote every article
        in the bucket forever and make the strong status unreachable. So the loss
        that counts is a file we could name and did not get, which is what
        `_fetch_payload` measures.
        """
        parsed: List[_Object] = []
        unexpected: List[str] = []
        for key, size in listed:
            item = _parse_key(key, size)
            if item is None or item.prefix.split(".")[0].upper() != ids.pmcid.upper():
                # Never fatal: one key the layout did not predict must not cost the
                # article its other twenty.
                unexpected.append(key)
                continue
            parsed.append(item)

        if unexpected:
            result.note("key_shape", status="unexpected", count=len(unexpected),
                        examples=unexpected[:3])
        if not parsed:
            return []

        by_version: Dict[int, List[_Object]] = {}
        for item in parsed:
            by_version.setdefault(item.version, []).append(item)
        versions = sorted(by_version)

        deposits = [version for version in versions
                    if _holds_the_article(by_version[version])]
        chosen = deposits[-1] if deposits else versions[-1]
        passed_over = [version for version in versions if version > chosen]
        files = by_version[chosen]

        note = {"chosen": chosen, "available": versions, "files": len(files),
                "complete_listing": complete}
        if passed_over:
            # Named, and named with the reason: "we took 1 of [1, 2]" is the shape a
            # reader would otherwise have to assume was the version bug.
            note["passed_over"] = passed_over
            note["passed_over_reason"] = "sidecars only, no <prefix>.pdf and no payload"
        result.note("version", status="chosen", **note)
        return files

    # -- the article's own files --------------------------------------------

    def _withhold_article_files(self, article: Dict[str, _Object],
                                result: SourceResult) -> None:
        """Take neither the PDF nor the JATS off an enumeration that did not finish.

        `complete` used to decide three things in this tier and not this one, and
        that asymmetry was an oversight rather than a trade. Keys sort
        lexicographically,
        so `PMC<id>.1/...` is served before `PMC<id>.2/...`: a walk that dies on page
        2 holds *systematically* the oldest version rather than a random one.
        `_latest_version` would then note `available: [1]` -- a positive claim about a
        bucket it did not finish reading -- and this method's other half would hand
        the v1 PDF over as `fulltext.pdf` with `pdf_status: ok`. The fetcher stops
        asking for a PDF the moment one arrives, so with the supplements settled by
        any other tier the record reaches `complete` and `store.manifest_is_complete`
        never looks again: superseded text frozen under a DOI that now means
        something else, which is the exact trap the version walk exists to prevent.
        The fetcher's identity check cannot catch it either -- v1 and v2 carry the
        same DOI and title, so `identify_fulltext` says verified, correctly.

        The other half of the same problem is the mirror image. `<prefix>.pdf` sorts
        *after* `NIHMS1758707-supplement-1.jpg`, so a truncation inside one version's
        own keys leaves the article's PDF on the page that never arrived -- and
        `not_found`, whose comment reads "a real absence, not a pattern that failed
        to match one", is then a false statement about an enumeration nobody read to
        the end. It is also the one claim the `not_in_oa_subset` branch in `fetch` is
        careful to gate on `complete`, forty lines above.

        So neither file is taken and neither status is set: the question stays open
        for a later tier and for the next run, which lists again from the start. The
        cost is a deferral -- an incomplete listing is a dead page, an unparseable
        body or the page guard, and the first two are transient -- weighed against
        freezing the wrong revision, which nothing re-tries. The payload is still
        fetched, because there the status can say what happened: `partial_failure`
        keeps the article unsettled, so those files are replaced rather than trusted.
        """
        result.problems.append(
            "pmc s3: the object listing did not complete, so the article's own "
            "files were not taken from it -- which version is current cannot be "
            "read off a partial enumeration"
        )
        result.note("pdf", status="listing_incomplete",
                    detail="neither the PDF nor the JATS was taken; the version "
                           "walk saw only part of the prefix",
                    listed_pdf=article.get("pdf") is not None,
                    listed_xml=article.get("xml") is not None)

    def _fetch_article_pdf(self, item: Optional[_Object], result: SourceResult) -> None:
        if item is None:
            # An enumeration that does not name a PDF is a real absence, not a
            # pattern that failed to match one. Only reachable for a *complete*
            # enumeration -- `fetch` sends the other case to
            # `_withhold_article_files`, because over an unread tail this word is a
            # guess dressed as a fact.
            result.pdf_status = "not_found"
            result.note("pdf", status="not_listed",
                        detail="the deposit lists no <prefix>.pdf")
            return
        if self._refuse_oversize(item, result, "pdf"):
            result.pdf_status = "too_large"
            return
        self._fetch_pdf_url(_object_url(item.key), result)

    def _fetch_article_xml(self, item: Optional[_Object], result: SourceResult) -> None:
        if item is None:
            result.note("xml", status="not_listed")
            return
        if self._refuse_oversize(item, result, "xml"):
            return
        url = _object_url(item.key)
        try:
            resp = self.http.get(url, accept="application/xml")
        except HttpError as e:
            result.note("xml", url=url, status="request_failed", error=str(e))
            return
        if not resp.ok or not resp.content:
            result.note("xml", url=url, status="http_error", http_status=resp.status)
            return
        result.files.append(
            FetchedFile(role=ROLE_XML, name="fulltext.nxml", content=resp.content,
                        url=url, label=f"PMC OA S3 ({item.filename})")
        )
        result.note("xml", url=url, status="ok", bytes=len(resp.content))

    # -- supplements and article media --------------------------------------

    def _fetch_payload(self, payload: List[_Object], complete: bool,
                       result: SourceResult) -> None:
        """Download everything that is not one of the article's own four objects.

        `suppl_status` is decided here, and this is the load-bearing judgement in
        the tier.

        **`fetched`, not `fetched_unverified`, and an S3 listing is what licenses
        the stronger word.** The taxonomy in `fetcher` splits the two on *what
        bounded the set*: `europepmc`'s ZIP and `pmc_oa`'s tarball earn `fetched`
        because unpacking a deposit archive yields the deposit, while
        `pmc_supplements` regexes PMC's HTML and the browser tier scrapes anchors,
        and a pattern over a rendered page cannot know what it failed to match. An
        object listing is on the archive side of that line, and arguably further
        along it: it is not a document about the deposit, it is the deposit's index,
        served by the store that holds the bytes. `KeyCount` and the absence of a
        continuation token are the bucket stating that these are all the objects
        under this prefix. A markup change cannot shrink it, because there is no
        markup. See `store.SUPPL_SETTLED` for what `fetched` then buys: the article
        is settled and no later run re-fetches it.

        **So the claim has to be withdrawn the moment the enumeration stops being
        one.** Three things do that:

        - the listing did not complete, so an unseen page could hold anything;
        - a download failed, or the size cap refused a file the listing named;
        - `max_files` stopped the walk short of a file the listing named.

        `fetched` over any of those would be the exact lie `fetched_unverified` was
        introduced to stop -- "they exist and we have them" over a set that is short
        by an unknown amount -- and it would be a *worse* lie than the one that
        prompted it, because `fetched` is the strongest word the taxonomy has.

        **The first two demote to `partial_failure` and the third to
        `fetched_unverified`, and that split is deliberate.** A count cap is not a
        failure; it is this tool declining to spend more requests on one article. It
        is also deterministic -- listing order is the bucket's, so a re-run drops the
        identical tail -- and `partial_failure` is not in `store.SUPPL_SETTLED`, so
        calling it one means the article never settles and every batch from here on
        re-lists and re-downloads the whole deposit to arrive in the same place. That
        is the trap `SUPPL_SETTLED`'s own docstring is about, and avoiding it is the
        judgement the other tiers already made: `europepmc._unpack_zip` stops at
        `max_files` and its caller still reports plain `fetched`, and
        `proxy_browser._download_all` says in as many words that returning
        `attempted` is what "keeps the `max_files` cap from masquerading as a partial
        failure". `fetched_unverified` rather than their `fetched` because the set on
        disk really is short of the deposit, and "every file we identified arrived,
        but nothing bounds the set" is a fair description of a capped walk; what was
        dropped is in `problems` and in the `cap` note either way. The size cap stays
        on the failure side of the line: `max_file_mb` refuses one named file over a
        size the listing states, which is a fact about that file and an action its
        reader can take, and it is what every other tier does with an oversize member
        -- `europepmc._unpack_zip` raises, costing the whole archive its status.

        **A loss counts only if it was a supplement.** `suppl_status` is a sentence
        about supplementary material, and the role of every object here is settled
        before any of them is fetched, by the same `pmc_oa.supplement_or_media`
        policy that decides which directory it lands in. Role-blind accounting made
        one 500 on `nihms-1758707-f0001.jpg` report `partial_failure` over a
        supplement set that had arrived whole -- and then, because that word is not
        settled, re-download the other 49 objects on every future run. The policy is
        a filename heuristic and the bucket does not label its objects, which is the
        argument for counting role-blind; the answer is that the same heuristic
        already decides whether a curator can find the file at all, and a heuristic
        trusted with that can be trusted to say which question a loss belongs to.

        **A file the text-bearing policy refused is not a loss either, and does not
        demote `fetched`.** After this change `suppl_status` is a claim about the
        supplementary files text can be extracted from, which is what the whole
        pipeline downstream of it consumes; the refused names are in the
        `text_bearing_filter` note, so the claim stays checkable. Demoting to
        `fetched_unverified` instead was the obvious alternative and is worse in a
        specific way: that status is what `extract/extractor.py` turns into the
        `supplement_set_unverified` caveat, so it would raise "no tier could confirm
        the set is complete" on every illustrated article in the corpus -- borrowing a
        signal that means something else, which is the same defect as letting a
        policy removal raise `manifest_entry_without_a_path`.

        A deposit of nothing but article figures leaves the status None rather than
        claiming anything, exactly as `pmc_oa`'s `if supplements:` guard does. The
        listing does bound the set, so "no key looked like a supplement" is a fact;
        but turning that into `none_listed` would promote a filename heuristic into
        a statement about what the publisher deposited, and the fetcher can already
        reconcile silence here against `hasSuppl`.
        """
        if not payload:
            result.note("supplements", status="none_listed_in_deposit", listed=0)
            return

        # Roles are decided here, before anything is fetched, because the cap has to
        # spend them in order -- see the module docstring on why raw key order costs
        # PMC10232368 eight supplementary tables to eight article figures.
        listed: Dict[str, List[_Object]] = {ROLE_SUPPLEMENT: [], ROLE_MEDIA: []}
        for item in payload:
            listed[supplement_or_media(item.filename)].append(item)

        # The pre-download refusal this tier is uniquely able to make, now for a
        # second reason: `<Size>` already lets it refuse a file that is too big, and
        # the listing's *names* let it refuse a file nothing can read. Per role,
        # because `suppl_status` below is a sentence about supplementary material and
        # the fetcher counts these the same way -- a skipped `f0001.jpg` is an
        # article figure and says nothing about the supplements, while a skipped
        # `NIHMS1758707-supplement-1.jpg` is one of them.
        for role in (ROLE_SUPPLEMENT, ROLE_MEDIA):
            listed[role] = self.keep_text_bearing(
                listed[role], result, name_of=lambda item: item.filename, role=role)

        # "file", not "link": S3 enumerated these, so a dropped one is a known file
        # rather than an anchor that may not have been distinct. Listing order is
        # the bucket's lexicographic key order, so which files a cap drops is at
        # least deterministic across runs -- and within each role the bucket's order
        # is kept, so it stays deterministic here too.
        supplements = self.apply_files_cap(listed[ROLE_SUPPLEMENT], result, noun="file")
        dropped_supplements = len(listed[ROLE_SUPPLEMENT]) - len(supplements)
        media = listed[ROLE_MEDIA][: max(self.max_files - len(supplements), 0)]
        dropped_media = len(listed[ROLE_MEDIA]) - len(media)
        if dropped_media:
            # Reported here rather than through `apply_files_cap`, whose line reads
            # "N supplementary file(s) not fetched" -- true of what it counted and
            # false of these, and a count that disagrees with its noun is how the
            # displacement above stayed invisible in the first place.
            result.problems.append(
                f"{dropped_media} article figure(s) not fetched: the max_files cap "
                f"({self.max_files}) goes to the deposit's supplementary files first"
            )
            result.note("cap", status="truncated_media", dropped=dropped_media,
                        max_files=self.max_files)

        attempted = ([(ROLE_SUPPLEMENT, item) for item in supplements]
                     + [(ROLE_MEDIA, item) for item in media])
        refused = failed = lost_supplements = 0
        for role, item in attempted:
            if self._refuse_oversize(item, result, "supplement_file"):
                refused += 1
                if role == ROLE_SUPPLEMENT:
                    lost_supplements += 1
                continue
            content, url = self._download(item, result)
            if content is None:
                failed += 1
                if role == ROLE_SUPPLEMENT:
                    lost_supplements += 1
                continue
            result.files.append(
                FetchedFile(
                    role=role,
                    name=item.filename,
                    content=content,
                    url=url,
                    label=("PMC OA S3" if role == ROLE_SUPPLEMENT
                           else "PMC OA S3 (article media)"),
                )
            )

        if failed:
            # The refusals above each printed their own line, so this covers only
            # what actually failed -- the same division `proxy_browser` draws
            # between its per-file reports and its aggregate one. Role-blind on
            # purpose: a figure that would not download is worth saying out loud
            # even where it does not move the supplement verdict.
            result.problems.append(
                f"{failed} of {len(attempted)} file(s) listed in the PMC S3 deposit "
                f"could not be fetched; see attempts for the per-file reason"
            )

        kept = len(result.by_role(ROLE_SUPPLEMENT))
        if not kept:
            # Nothing on disk to make a claim about. Silence unless supplements were
            # listed and missed -- a figures-only deposit leaves the question to the
            # fetcher, which has `hasSuppl` to weigh it against.
            #
            # **`or not complete` is in here as well as in the branch below, and the
            # filter is what made it load-bearing.** `complete` is what licenses this
            # tier's `fetched` (see `_list_objects`), and an enumeration that stopped
            # half way has named no deposit -- so this branch may not be silent about
            # it either. Silence used to be safe here because `kept == 0` was only
            # reachable for a payload with no supplement-classified key at all, and
            # every word the fetcher then reached was unsettled. With
            # `text_bearing_only` on, `kept == 0` is reachable for a deposit that
            # *did* name supplements and were all refused, and the fetcher's answer
            # there is `none_text_bearing`, which is settled -- so a truncated
            # listing whose seen page happened to hold only figures would freeze
            # `complete` over an unread continuation page and no later batch would
            # ever re-list it. Measured: page 1 naming the PDF and one
            # `supplement-1.jpg` with a continuation token, page 2 answering 503, gave
            # `none_text_bearing` / `complete` / `cached` with 0 further requests --
            # while the same manifest carried "the enumeration is incomplete".
            #
            # It has to be fixed here. Nothing on a `SourceResult` carries `complete`,
            # so `fetcher._supplement_status` cannot know: it sees `reported == []`
            # and a refusal count, and returns the settled word. `partial_failure` is
            # the sibling branch's word for the same fact and keeps the article
            # unsettled, which is all this needs.
            #
            # Keys sort lexicographically, so this is not a remote shape:
            # `supplement-1.jpg` precedes `supplement-10.xlsx`, which puts the
            # refusable names on the page that was read and the readable ones on the
            # page that was not.
            #
            # It is not gated on anything having been refused, although only a
            # refusal can reach the settled word, because "the listing stopped half
            # way" is true whatever emptied the payload. Measured consequence with
            # `text_bearing_only: false`, the one place this is not a no-op: a
            # truncated listing whose seen page classifies no key as a supplement now
            # ends `none_retrieved` where it ended `unknown_none_found`, and
            # `expected_but_missing` -- the one that matters -- either way. Both of
            # those are outside `store.SUPPL_SETTLED`, so the article is re-fetched
            # next batch exactly as it was before; what changes is which unsettled
            # word the manifest carries, and "a tier tried and came away with
            # nothing" is the truer of the two over a listing that failed mid-walk.
            if lost_supplements or dropped_supplements or not complete:
                result.suppl_status = "partial_failure"
        elif lost_supplements or not complete:
            result.suppl_status = "partial_failure"
        elif dropped_supplements:
            result.suppl_status = "fetched_unverified"
        else:
            result.suppl_status = "fetched"

        result.note("supplements", status=result.suppl_status or "none",
                    listed=len(payload), attempted=len(attempted),
                    supplements=kept, media=len(result.by_role(ROLE_MEDIA)),
                    refused=refused, failed=failed,
                    dropped_supplements=dropped_supplements,
                    dropped_media=dropped_media,
                    # `listed` counts the deposit and `attempted` counts what was
                    # fetched from it, so without this the gap between them for an
                    # illustrated article looks like a loss. The names are in the
                    # `text_bearing_filter` note.
                    not_text_bearing=len(result.skipped_not_text_bearing),
                    complete_listing=complete)

    # -- one object ---------------------------------------------------------

    def _refuse_oversize(self, item: _Object, result: SourceResult, action: str) -> bool:
        """True when `<Size>` puts this object over the cap. Nothing is transferred.

        The wording matches `proxy_browser._refuse_oversize` deliberately: a user
        reading "not fetched: N MB exceeds the 200 MB cap" should not have to know
        which tier refused it to recognise the same cap.
        """
        if item.size is None or item.size <= self.max_file_bytes:
            return False
        megabytes = round(item.size / 1024 / 1024, 1)
        result.problems.append(
            f"{item.filename} not fetched: {megabytes} MB exceeds the "
            f"{self.config.get('max_file_mb', 200)} MB cap (fetch.max_file_mb)"
        )
        result.note(action, key=item.key, status="too_large", megabytes=megabytes)
        return True

    def _download(self, item: _Object, result: SourceResult):
        """GET one object. Returns `(content, url)`, or `(None, None)` with a note.

        No `classify_denial` here, unlike every other supplement path: this host is
        the reason the tier exists, and it does not serve challenge or paywall pages
        to automation. An S3 refusal is a status code with an XML body, which the
        `resp.ok` check already catches.
        """
        url = _object_url(item.key)
        try:
            resp = self.http.get(url)
        except HttpError as e:
            result.note("supplement_file", key=item.key, status="request_failed",
                        error=str(e))
            return None, None
        if not resp.ok or not resp.content:
            result.note("supplement_file", key=item.key, status="http_error",
                        http_status=resp.status)
            return None, None
        if len(resp.content) > self.max_file_bytes:
            # The pre-check is only as good as the `<Size>` the listing carried, so
            # the cap is enforced again on what actually arrived. Reached when
            # `<Size>` was absent or unparseable.
            result.note("supplement_file", key=item.key, status="too_large",
                        megabytes=round(len(resp.content) / 1024 / 1024, 1),
                        detail="no usable <Size> in the listing")
            return None, None
        result.note("supplement_file", key=item.key, status="ok",
                    bytes=len(resp.content), content_type=resp.content_type)
        return resp.content, resp.url


def _split_deposit(files: List[_Object]):
    """Split one version's files into the article's own, the payload, and sidecars.

    Returns `({"pdf": ..., "xml": ...}, [payload], [skipped names])`. The payload
    keeps the listing's order, and the article's four self-named objects are held
    out of it so that `max_files` can never spend the article's PDF on a figure --
    `pmc_oa._unpack_tgz` counts every member against the cap and can. The hold-out
    is only half of that argument: `_fetch_payload` re-orders what comes back, so
    the cap cannot spend a *supplement* on a figure either.
    """
    article: Dict[str, _Object] = {}
    payload: List[_Object] = []
    skipped: List[str] = []
    for item in files:
        suffix = _self_named_suffix(item)
        if suffix in _SELF_NAMED_ROLES:
            # First wins. A version prefix names at most one of each, so a second
            # is a bucket anomaly, and replacing the first would be a coin toss.
            article.setdefault(_SELF_NAMED_ROLES[suffix], item)
        elif suffix in _SELF_NAMED_SIDECARS:
            skipped.append(item.filename)
        else:
            payload.append(item)
    return article, payload, skipped


def _holds_the_article(files: List[_Object]) -> bool:
    """True when this version's objects are more than the article's own metadata.

    The question `_latest_version` has to ask before calling a version the current
    one, and it is asked *through* `_split_deposit` so that "holds the article"
    cannot come to mean something different here than it does thirty lines later.

    A `<prefix>.pdf` or any payload object is enough. `<prefix>.xml` alone is not,
    and that is the whole measured shape: PMC8494648`.2` and PMC8828466`.2` hold
    exactly `<prefix>.json`, `.txt` and `.xml` -- the author-manuscript record, no
    PDF and no payload -- over a `.1` that holds the version of record. Requiring
    the PDF specifically would be stricter than the evidence supports: a deposit of
    supplements without a PDF is still a deposit, and this tier's job there is to
    hand the payload over and let a later tier answer for the full text.
    """
    article, payload, _ = _split_deposit(files)
    return "pdf" in article or bool(payload)
