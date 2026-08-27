"""Tier orchestration.

Each tier is asked only for what is still missing, and the loop stops as soon as
there is a usable PDF and the supplement question is settled.

The status taxonomy is the point of this module. An empty result and a failed
result look identical downstream unless something names them apart, and the trap
here is an empty `supplementary/` directory:

    none_listed          the publisher says this article has no supplements
    fetched              the deposit itself was enumerated and all of it arrived
    fetched_unverified   every file we identified arrived, but nothing bounds the
                         set -- or `max_files` stopped us short of one that does
    none_text_bearing    supplements were named, and no text can be extracted from
                         any of them -- so none was fetched
    partial_failure      we got some; at least one download or archive failed
    expected_but_missing hasSuppl=Y, and we came away with nothing  <-- the bug case
    none_retrieved       a tier tried and every file it went after was lost
    page_not_parsed      a page loaded but we could not read a file list from it
    unknown_none_found   nobody told us whether supplements exist, and we found none
    not_requested        --no-supplements

`expected_but_missing` is the state the whole taxonomy exists to expose.

`none_text_bearing` exists because `fetch.text_bearing_only` can empty a deposit
that was read perfectly. 47% of the 5116 supplementary entries in this corpus are
images, audio or video, and for some articles that is *all* of them -- a
supplement set of four figure JPEGs and nothing else. With the filter on, the tier
enumerates the deposit, names every file, refuses every one, and comes away with
zero. Every other empty-handed word in this list would be a lie about that
article: `expected_but_missing` is the alarm, and nothing is missing; `none_listed`
is a claim about what the *publisher* says, and the publisher said four files;
`none_retrieved` and `page_not_parsed` blame a route that worked. So the state gets
its own word, and the word says what actually happened -- we know what the
supplements are, and no text can come out of them. Which files those were is in the
`text_bearing_filter` attempts note, so a reader can see exactly what a
`text_bearing_only: false` run would have fetched.

It is settled (`store.SUPPL_SETTLED`): re-running skips the same files by the same
rule and arrives in the same place, so leaving it unsettled would make every later
batch re-list and re-refuse the 138 articles that hold one of these files forever.
That is the trap `SUPPL_SETTLED`'s own docstring is about.

**The filter also changes what the other words claim, and this is the load-bearing
part.** With `text_bearing_only` on, every status here is a statement about the
supplementary files *text can be extracted from*: `fetched` still means "the deposit
was enumerated and all of it arrived" for that set, and does not demote to
`fetched_unverified` because some members were refused. The alternative was tried on
paper and rejected: `fetched_unverified` is what `extract/extractor.py` turns into
the `supplement_set_unverified` caveat, so demoting would raise "no tier could
confirm the set is complete" over every illustrated article in the corpus --
borrowing a signal that means something else, the same defect that keeps a policy
removal from raising `manifest_entry_without_a_path`. The refusals are recorded per
file instead, which is a stronger record than a weaker status.

`fetched_unverified` exists because the taxonomy told its own version of that lie.
For 10.1016/j.xgen.2026.101304 the adapter matched 1 of 12 supplementary links,
downloaded that one, and the article was recorded `fetched` -- "they exist and we
have them" -- while eleven files were missing. Nothing was broken: the tier really
did get everything it found. The claim being made was simply larger than the
evidence, because a regex over page anchors cannot know what it failed to match.

So the two are split by *what bounded the set*, which is the only thing the code
can actually know. Europe PMC's supplementary ZIP and the PMC OA tarball are
self-delimiting -- unpacking the archive yields the deposit, and a member list is
not a guess -- and `pmc_s3`'s object listing is the same kind of evidence one step
earlier: it is the deposit's index, served by the store that holds the bytes.
Every other route pattern-matches a rendered page: `pmc_supplements`
regexes PMC's HTML for `/bin/` paths, the browser tier scrapes anchors, bioRxiv
regexes its supplement page. Those get `fetched_unverified` even when they are in
fact complete -- as most of the eight ground-truth papers are. That is not an alarm.
It is the difference between "we counted" and "we looked and this is what we saw",
and only the first licenses "they exist and we have them".

Both are settled: see `store.SUPPL_SETTLED`. An unbounded set is not a failed one,
and re-running would scrape the same page and get the same answer.

`max_files` lands in `fetched_unverified` for that second reason rather than in
`partial_failure`, and every tier that can hit it agrees: `europepmc._unpack_zip`
truncates and its caller still says `fetched`, `proxy_browser._download_all`
measures success against what it attempted so the cap cannot masquerade as a
failure, and `pmc_s3` demotes one notch to `fetched_unverified` because there the
listing did say how much was left. A count cap is this tool declining to spend more
requests on one article, not a file that would not come; it is deterministic, so
calling it a failure leaves the article unsettled and makes every later batch
re-download the whole deposit to drop the identical tail again. A file refused over
`max_file_mb` is a failure, because that is one named file and raising the cap gets
it.
"""

import os
from functools import reduce
from pathlib import Path
from typing import Dict, List, Optional

from .. import text_bearing
from . import orphans, store
from .http import Http
from .identifiers import Identifiers, normalize_doi, resolve_identifiers
from .sources import DEFAULT_TIERS, build_sources
from .validate import (
    better_pdf_failure,
    cited_dois,
    identify_fulltext,
    identity_problem,
    jats_article_type,
    jats_sample_text,
    not_research_article,
    pdf_sample_text,
)
from .sources.base import (
    AFTER_DOWNLOAD,
    ROLE_LANDING,
    ROLE_MEDIA,
    ROLE_PDF,
    ROLE_SUPPLEMENT,
    ROLE_XML,
    not_text_bearing_note,
)

# Statuses that mean the PDF is on disk and usable. Defined in `store`, which is
# where `finalize_status` reads it, so the orchestrator and the record it writes
# cannot disagree about what counts as having the article.
_PDF_SUCCESS = store.PDF_USABLE

#: Tier `suppl_status` values that mean *another route may still hold the files*: a
#: download or an archive that failed, or a page nothing could read. Exactly the
#: statuses a tier can set that `store.SUPPL_SETTLED` does not contain, and the one
#: fact two decisions in this module turn on -- so it is written once here rather
#: than restated at each, which is how they came apart in the first place:
#:
#: - `_supplement_status` refuses to claim the settled `none_text_bearing` when one
#:   of these was reported, because a re-run *can* change it.
#: - the tier loop refuses to stop asking for supplements when one of these was
#:   reported, because a later tier is the only thing that can change it. The two
#:   have to agree: a run that gives up on the tier chain and then records an
#:   unsettled verdict re-fetches the same truncated run on every later batch.
#:
#: Everything else a tier can report -- `fetched`, `fetched_unverified`,
#: `none_listed`, or nothing at all -- is a tier that accounted for what it saw.
SUPPL_RECOVERABLE = {"partial_failure", "page_not_parsed"}


def _best_pdf_status(reported: List[str]) -> str:
    """Pick the most useful explanation from the statuses each tier reported.

    `reported` is in tier order, so the last entry comes from the most capable tier
    that tried. Preferring it avoids an early routing note drowning out a real
    attempt: for 10.1002/path.5751 the tiers said
    `[download_failed, not_in_oa_subset, not_a_pdf]`, and a static ranking surfaced
    `not_in_oa_subset` when the actual cause was Wiley serving an HTML viewer.
    An actionable diagnosis still wins wherever it appears.

    The failure branch is a fold of `validate.better_pdf_failure`, which is the same
    function a tier uses to choose among its own candidate URLs. Sharing it is the
    point: the word a user reads must not depend on whether two statuses came from
    one tier or from two.
    """
    for status in reported:
        if status in _PDF_SUCCESS:
            return status
    if not reported:
        return "not_found"
    return reduce(better_pdf_failure, reported)


def _no_tier_applied(ids: Identifiers, tier_names: List[str]) -> str:
    """Why a run ended without a single tier having tried.

    The one explanation that has to survive every tier list, because it is the case
    where no tier ran to produce any other. `--oa-only` over a paywalled non-PMC DOI
    is the shape that exposed it: every OA tier keys on a PMCID or a Europe PMC
    open-access URL, this paper has neither, and the row read

        failed  pdf=not_found  suppl=unknown_none_found  files=0  tiers=-

    with nothing after it. d09d7b2 caused that by demoting the idconv miss out of
    `problems` -- correctly, since "no PMC deposit" is the normal answer for a
    paywalled paper, but it was the only line that row had, and the compensating
    problem lines that commit added live in tiers which do not run here.

    States the facts the `applies` methods key on rather than restating their rules,
    so this cannot drift as tiers change.
    """
    known = f"pmcid={ids.pmcid or 'none'}, " \
            f"europepmc open-access pdf urls={len(ids.open_access_pdf_urls())}, " \
            f"preprint={'yes' if ids.is_preprint else 'no'}"
    hint = ""
    if "proxy_browser" not in tier_names:
        hint = ("; the browser tier, which needs none of those, is not in this run's "
                "tier list")
    return (f"no configured tier could try this paper ({known}). Tiers: "
            f"{', '.join(tier_names) or 'none'}{hint}")


def _still_on_disk(directory, entry: Optional[dict]) -> bool:
    """Does the file an existing manifest entry names still exist?

    A re-fetch (typically `--force`) that comes away with nothing must not
    erase the record of a file that is still sitting right there: the bytes
    survive a failed refetch (nothing here deletes them), but the manifest
    used to be rewritten to `{"status": failure, "path": None}` regardless,
    orphaning a good `fulltext.pdf` or `supplementary/` set from the record
    that points at it. Every fallback to `existing` below is guarded by this.
    """
    path = (entry or {}).get("path")
    return bool(path) and (Path(directory) / path).exists()


def _entry_accounted_for(directory, entry: Optional[dict]) -> bool:
    """Is this existing manifest entry still a true statement about the corpus?

    `_still_on_disk` asks the narrower question and cannot answer this one: a
    supplement the `drop-media` pruner removed names no file *by design* -- keeping a
    `path` key over a deleted file is what would make every batch re-fetch the
    article forever, which is why the pruner drops it and leaves `name`, `bytes`,
    `sha256` and its marker (`store.mark_entry_removed`).

    Asked of the whole existing set before a re-fetch decides whether to keep it. Ask
    `_still_on_disk` there instead and one removed entry makes the whole set look
    gone, so a `--force` re-fetch that comes away empty-handed replaces it with `[]`
    -- discarding the spreadsheets still on disk from the record *and* the account of
    what was removed and why. That is the same loss 186b2e4 fixed for a failed
    re-fetch of a good set, one policy later.
    """
    return store.entry_removed_by_policy(entry) or _still_on_disk(directory, entry)


def suppl_flag_is_authoritative(ids: Identifiers) -> bool:
    """Can `hasSuppl: N` be believed as "this article has no supplements"?

    Only when Europe PMC or PMC actually holds the article *and its files*. The
    flag describes *their* holdings, not the article, and three measured cases
    prove the difference:

    - Preprints: Europe PMC says hasSuppl=N for 10.1101/2025.07.21.666016 and
      10.1101/2024.01.23.576878, which have 2 and 6 supplementary files.
    - Articles it does not hold: 10.1016/j.stem.2023.12.013 and
      10.1038/s41591-018-0269-2 both come back inEPMC=N, inPMC=N, hasSuppl=N --
      which says only that Europe PMC has nothing, not that the publisher does.
    - Articles it holds only as a metadata record: 10.1038/s41586-026-10510-x is
      inEPMC=Y, inPMC=Y, hasSuppl=N, isOpenAccess=N -- PMC13186389 is outside the
      Open Access subset, so Europe PMC has the record and none of the files. The
      publisher's page carries MOESM1 through MOESM13, thirteen supplements the
      flag truthfully denies holding and misleadingly denies existing.

    `is False`, not falsy: isOpenAccess absent means unknown, which is not the
    same claim as a measured N.

    Believing the flag in those cases produced a confident `none_listed` over
    files that exist, which is the exact silent loss this pipeline exists to
    prevent.
    """
    if ids.has_suppl is not False:
        return False
    if ids.is_preprint:
        return False
    if ids.is_open_access is False:
        return False
    return bool(ids.in_epmc) or bool(ids.in_pmc)


def _supplement_status(
    ids: Identifiers,
    want_supplements: bool,
    collected: int,
    reported: List[str],
    skipped_supplements: int = 0,
) -> str:
    """The one place that names what happened to this article's supplements.

    `skipped_supplements` counts supplementary files `fetch.text_bearing_only`
    refused, across every tier and both filter points. It defaults to 0 so that the
    other arguments still read as the whole story when nothing was refused -- and
    because a caller passing four positional arguments is asking the question this
    function has always answered.

    Only files that were going to be *supplements* count. A refused article figure
    (`pmc_s3`'s `media/`) says nothing about supplementary material, which is the
    same division `pmc_s3._fetch_payload` draws when it decides whether a lost
    download cost the article its `fetched`.
    """
    if not want_supplements:
        return "not_requested"
    if suppl_flag_is_authoritative(ids):
        return "none_listed"
    if collected:
        # Judge on the outcome, not on the journey. An earlier tier failing and a
        # later tier succeeding is a complete result -- the failed attempts are
        # still in `attempts` and `problems`.
        #
        # `fetched` beats `fetched_unverified` because it is the stronger
        # evidence, not merely the better news: if any tier unpacked the deposit
        # archive, the set is bounded no matter what a scrape elsewhere saw. And
        # both beat `partial_failure` for the reason above -- a later tier
        # succeeding settles what an earlier one could not.
        if "fetched" in reported:
            return "fetched"
        if "fetched_unverified" in reported:
            return "fetched_unverified"
        return "partial_failure"
    # Nothing arrived, and files we could name are the reason. Above every other
    # empty-handed word because it is the only one with direct evidence behind it:
    # a refused file is a file some tier *saw*, which outranks `none_listed`'s claim
    # that none exist (bioRxiv's supplement page can be stale where a Europe PMC ZIP
    # is not) and outranks the `hasSuppl` alarm below, which this explains away.
    #
    # Guarded on nothing having been lost (`SUPPL_RECOVERABLE`, which the tier loop
    # reads too), and that guard is the whole safety of the word. `none_text_bearing`
    # is settled, so claiming it over a run that also lost a spreadsheet would freeze
    # that loss into the manifest and no later batch would ever look again. A deposit
    # of three JPEGs and one .xlsx that 500s must stay `expected_but_missing`:
    # re-running *can* change that, which is exactly what settled means and why this
    # cannot be claimed there. `page_not_parsed` is in the guard for the same reason
    # -- a wall another route hit may still hold files, and `--headed` may get them.
    #
    # The guard only holds if the tier that reported nothing really did account for
    # what it saw. `pmc_s3` is the tier that can fail to: its listing can stop half
    # way, and an enumeration that stopped has named no deposit. That is fixed where
    # the fact lives, in `pmc_s3._fetch_payload`, which reports `partial_failure` on
    # an incomplete listing whether or not anything was kept -- nothing about a
    # `SourceResult` carries "the listing was truncated" up to here.
    if skipped_supplements and not SUPPL_RECOVERABLE & set(reported):
        return "none_text_bearing"
    # A source that owns the content (bioRxiv for its own preprints) can state
    # authoritatively that there are none, even when the index disagrees.
    if "none_listed" in reported:
        return "none_listed"
    if ids.has_suppl is True:
        return "expected_but_missing"
    # A tier that tried and came away with nothing *looked*, and that is the whole
    # difference from `unknown_none_found`, which means nobody did. Both produced the
    # same word for 10.1016/j.oraloncology.2021.105348, so the summary line could not
    # distinguish "we lost everything" from "no tier ever tried".
    #
    # "Tried", not "listed files and lost them", which is what d09d7b2's comment
    # claimed: `europepmc` reaches `partial_failure` when the archive endpoint errors,
    # answers with a non-archive, or yields an unreadable ZIP, and none of those
    # involves a listing. Having tried is the fact all the producers share.
    #
    # Not `partial_failure`, which is what d09d7b2 returned here: that word is
    # documented in the legend above and in the README as "some arrived; at least one
    # failed", and it is the only way a consumer can tell from the status alone that
    # a file made it. Reusing it for the zero-file case would put two facts under one
    # name, which is the defect that commit set out to fix.
    #
    # The position is load-bearing. Above `has_suppl is True` it would swallow
    # `expected_but_missing`, which is the stronger statement when the publisher says
    # the files exist; above `none_listed` it would override a source that owns the
    # content. Both of those are pinned in `test_supplement_status_precedence`.
    #
    # Above `page_not_parsed` because it claims more: something was there to retrieve
    # and we lost it, where `page_not_parsed` says we never learned whether anything
    # was. Reachable when Europe PMC's archive endpoint answers with a non-archive
    # and the browser tier then cannot read the publisher's page.
    if "partial_failure" in reported:
        return "none_retrieved"
    if "page_not_parsed" in reported:
        return "page_not_parsed"
    return "unknown_none_found"


def build_http(config: dict) -> Http:
    fetch_cfg = config.get("fetch", {}) or {}
    # `max_response_mb` is the only ceiling on a plain-HTTP body. The per-file cap
    # (`max_file_mb`) is enforced by the tiers against a Content-Length they asked
    # for first, so it does not bound a response that arrives without one, or one
    # from a lookup endpoint rather than a file. Left unset the client is unbounded,
    # which is the behaviour this had while nothing could set it.
    response_mb = fetch_cfg.get("max_response_mb")
    return Http(
        contact_email=fetch_cfg.get("contact_email"),
        min_interval_seconds=fetch_cfg.get("min_interval_seconds", 3.0),
        # Per-host exceptions to that interval. Absent means an empty mapping, which
        # is the single global interval this had before -- so a config that does not
        # mention the key behaves exactly as it did.
        min_interval_overrides=fetch_cfg.get("min_interval_overrides"),
        timeout_seconds=fetch_cfg.get("timeout_seconds", 60),
        ncbi_api_key=fetch_cfg.get("ncbi_api_key"),
        max_bytes=int(response_mb * 1024 ** 2) if response_mb else None,
    )


#: Credentials that may come from the environment instead of `config.yaml`, mapped
#: to the config key each one fills.
ENV_CREDENTIALS = {
    "MANUSCRIPT_HARVEST_ELSEVIER_API_KEY": "elsevier_api_key",
}


def _with_env_credentials(fetch_cfg: dict) -> dict:
    """`fetch_cfg` with any credential the environment supplies filled in.

    **The environment wins over the file, and that direction is the point.**
    `config.yaml` is tracked in git and ships `elsevier_api_key: null`, so a
    file-wins rule would let that committed null blank out a real key on every run --
    and the failure would look like a 401 from Elsevier rather than a config
    precedence bug.

    Here rather than in `cli.py` so that a library caller of `fetch_publication` gets
    the same precedence a CLI user does. Not on `Http`, unlike `ncbi_api_key`: that
    one is injected per-request by `_ncbi_params`, while this one is held by the tier
    that authenticates with it, and tiers are handed `fetch_cfg` as `self.config`.

    A copy, never a mutation: callers pass a `config` dict they may reuse across
    DOIs -- `batch` does -- and writing a secret into it would put the key somewhere
    the caller did not ask for it to be.
    """
    supplied = {
        key: os.environ[name].strip()
        for name, key in ENV_CREDENTIALS.items()
        if os.environ.get(name, "").strip()
    }
    if not supplied:
        return fetch_cfg
    merged = dict(fetch_cfg)
    merged.update(supplied)
    return merged


def fetch_publication(
    doi: str,
    config: dict,
    force: bool = False,
    want_supplements: bool = True,
    http: Optional[Http] = None,
) -> dict:
    """Fetch one publication into the corpus. Returns the manifest record.

    Never raises for an unreachable paper -- a manifest with `status: failed` and
    the attempts that produced it is more useful than a traceback.
    """
    fetch_cfg = _with_env_credentials(config.get("fetch", {}) or {})
    corpus_dir = fetch_cfg.get("corpus_dir", "corpus")
    tier_names = list(fetch_cfg.get("tiers", DEFAULT_TIERS))

    normalized = normalize_doi(doi)
    directory = store.article_dir(corpus_dir, normalized)

    existing = store.read_manifest(directory)
    if existing is not None and not force:
        existing["_directory"] = str(directory)
        if store.manifest_is_complete(existing):
            existing["cached"] = True
            return existing

    http = http or build_http(config)
    needs_landing = "proxy_browser" in tier_names
    ids = resolve_identifiers(normalized, http, need_landing_url=needs_landing)

    record = store.new_record(ids)
    record["_directory"] = str(directory)
    record["tiers_configured"] = tier_names

    need_pdf = True
    # Only skip the supplement search when hasSuppl=N is actually believable --
    # see `suppl_flag_is_authoritative` for the cases where it is not.
    need_supplements = want_supplements and not suppl_flag_is_authoritative(ids)

    pdf_statuses: List[str] = []
    suppl_statuses: List[str] = []
    suppl_advice: List[str] = []
    pdf_file = None
    pdf_tier = None
    # A PDF that arrived, parsed, and is not this paper. Held rather than adopted
    # so the loop keeps asking later tiers -- and kept rather than dropped, since
    # for 10.1126/science.adf1226 it was the only file the browser tier produced
    # and deleting it would delete the evidence for saying so.
    unverified_pdf = None
    unverified_tier = None
    unverified_meta: dict = {}
    xml_file = None
    xml_tier = None
    xml_status = "ok"
    xml_identity: dict = {}
    # Why this DOI is not a research article, from whichever signal saw it first.
    # Europe PMC's own typing is checked before any tier runs, because it is the
    # one signal that needs no download.
    not_article: Optional[str] = not_research_article(
        title=ids.title, pub_types=ids.pub_types)
    if not_article and ids.corrects_doi:
        not_article += (f"; it is a notice about {ids.corrects_doi}, which is the "
                        f"DOI to fetch instead")
    landing_file = None
    supplements: List = []
    media: List = []
    seen_digests: Dict[tuple, str] = {}
    # Every file any tier declined under `fetch.text_bearing_only`, theirs and this
    # function's, in one list. Not written to the manifest as a key of its own:
    # `attempts` already carries the names with the tier and the point in the flow
    # that refused them, and this is only needed to tell `_supplement_status` why the
    # supplement set is empty. Writing them into `record["supplementary"]` as
    # path-less entries was the tempting alternative and is exactly what the pruner's
    # marker is careful *not* to look like -- an entry in that list is a promise that
    # a file arrived, and there are no bytes here to promise.
    skipped_files: List[dict] = []
    text_bearing_only = text_bearing.policy_is_on(fetch_cfg)

    for source in build_sources(tier_names, http, fetch_cfg):
        if not need_pdf and not need_supplements:
            break
        if not source.applies(ids):
            record["attempts"].append(
                {"tier": source.name, "action": "skipped", "status": "not_applicable"}
            )
            continue

        record["tiers_tried"].append(source.name)
        try:
            result = source.fetch(ids, need_pdf=need_pdf, need_supplements=need_supplements)
        except Exception as e:  # a broken tier must not sink the whole fetch
            record["problems"].append(f"tier {source.name} raised {type(e).__name__}: {e}")
            record["attempts"].append(
                {"tier": source.name, "action": "fetch", "status": "tier_error",
                 "error": f"{type(e).__name__}: {e}"}
            )
            continue

        record["attempts"].extend(result.attempts)
        record["problems"].extend(result.problems)
        suppl_advice.extend(result.suppl_advice)
        skipped_files.extend(result.skipped_not_text_bearing)
        # What this tier refused before spending the request, so the central filter
        # below can add only what it caught itself.
        skipped_here: List[tuple] = []
        if result.pdf_status:
            pdf_statuses.append(result.pdf_status)
        if result.suppl_status:
            suppl_statuses.append(result.suppl_status)

        for item in result.files:
            if item.role == ROLE_PDF and pdf_file is None:
                # `validate_pdf` in the tier asked whether these bytes are a PDF.
                # This asks whether they are *this* PDF, which no tier can: a tier
                # is handed a URL, not a DOI to compare against.
                verified, meta = identify_fulltext(
                    pdf_sample_text(item.content), ids.doi, ids.title or "")
                record["attempts"].append(
                    {"tier": source.name, "action": "identify_pdf",
                     "status": "verified" if verified else "unverified", **meta})
                if verified:
                    pdf_file, pdf_tier = item, source.name
                elif unverified_pdf is None:
                    unverified_pdf, unverified_tier = item, source.name
                    unverified_meta = meta
            elif item.role == ROLE_XML and xml_file is None:
                xml_file, xml_tier = item, source.name
                xml_text = jats_sample_text(item.content)
                article_type = jats_article_type(item.content)
                record["attempts"].append(
                    {"tier": source.name, "action": "jats_article_type",
                     "status": article_type or "absent"})
                verified, xml_identity = identify_fulltext(
                    xml_text, ids.doi, ids.title or "")
                xml_status = "ok" if verified else "identity_unverified"
                not_article = not_article or not_research_article(
                    article_type=article_type)
                if not_article and not ids.corrects_doi:
                    # Turning "this is not a paper" into "fetch
                    # 10.1038/s41586-024-08150-0 instead" is the difference between
                    # a rejection and an instruction. Only reached when Europe PMC
                    # did not already name it: reading the first other DOI out of the
                    # document is a guess, since a notice cites references too, and
                    # `Identifiers.corrects_doi` is the same fact stated properly.
                    named = cited_dois(xml_text, exclude=ids.doi, limit=1)
                    if named:
                        not_article += (f"; the notice names {named[0]}, which is "
                                        f"probably the DOI to fetch instead")
            elif item.role == ROLE_LANDING and landing_file is None:
                landing_file = item
            elif item.role in (ROLE_SUPPLEMENT, ROLE_MEDIA):
                # The guarantee. Whatever a tier hands back, this is what decides
                # whether it lands, and it is deliberately a second check rather than
                # a first: four tiers know a filename in advance and refuse it before
                # spending the request, which this cannot do, and none of them knows
                # the *final* name. `proxy_browser` reads it from
                # `Content-Disposition` after the body is in hand -- ClinicalKey
                # serves twelve supplements from one extensionless endpoint -- and
                # `store.sanitize_filename` has not run yet either. So the tiers save
                # the requests and this decides what the corpus holds, which is the
                # only place a future tier cannot get wrong by omission.
                #
                # Only these two roles. The article's own PDF, JATS and landing page
                # are never asked: a policy about supplementary material must not be
                # able to refuse the article, whatever a publisher names the file.
                reason = text_bearing.skip_reason(item.name) if text_bearing_only \
                    else None
                if reason is not None:
                    skipped_here.append((item.name, item.role, reason))
                    continue
                # Europe PMC's ZIP and the PMC listing overlap, so the same file
                # can arrive twice. The key is (bytes, name), not bytes alone:
                # distinct supplements legitimately share content (empty
                # templates, repeated controls), and dropping one of those would
                # be exactly the kind of silent loss this pipeline avoids.
                # Storing a rare duplicate is the cheaper mistake.
                key = (store.sha256_bytes(item.content), store.sanitize_filename(item.name))
                if key in seen_digests:
                    continue
                seen_digests[key] = item.name
                item.tier = source.name
                (supplements if item.role == ROLE_SUPPLEMENT else media).append(item)

        if skipped_here:
            record["attempts"].append(
                not_text_bearing_note(source.name, skipped_here, where=AFTER_DOWNLOAD))
            skipped_files.extend(
                {"name": name, "role": role, "reason": why}
                for name, role, why in skipped_here)
        refused_supplements = sum(
            1 for entry in result.skipped_not_text_bearing
            if entry["role"] == ROLE_SUPPLEMENT
        ) + sum(1 for _name, role, _why in skipped_here if role == ROLE_SUPPLEMENT)

        if pdf_file is not None:
            need_pdf = False
        if supplements:
            need_supplements = False
        elif refused_supplements and result.suppl_status not in SUPPL_RECOVERABLE:
            # This tier named the article's supplementary files and every one of them
            # was a file no text can come out of. Stopping here is not a new early
            # exit: the branch above stops the loop as soon as *any* supplement
            # arrives, so a run that kept those JPEGs would have stopped in exactly
            # this place. Carrying on instead would send `pmc_supplements` into PMC's
            # proof-of-work wall and then open a browser, to find the same figures --
            # and each of those tiers can report `page_not_parsed` or
            # `partial_failure` on the way, which would block `none_text_bearing`
            # (see `_supplement_status`) and end the article on the
            # `expected_but_missing` alarm with nothing wrong with it.
            #
            # **`SUPPL_RECOVERABLE` is the whole guard, and without it this exit is a
            # data loss.** "A run that kept those JPEGs would have stopped here" is
            # only true when the refused file would in fact have *arrived*. A tier
            # that refused a figure and *lost* a spreadsheet beside it did not
            # account for the set, and the branch above would not have fired for it
            # -- so stopping there abandons the rescue chain the tier order exists
            # for. Measured on two shapes, both of which lose a `.xlsx` forever
            # without this condition: `pmc_supplements` refusing `fig1.jpg` and then
            # meeting NCBI's proof-of-work page on `supplement-2.xlsx` -- the normal
            # case for a non-Springer publisher, and `proxy_browser` is the only
            # route through that wall, while the manifest prints "the browser tier is
            # required for them"; and `pmc_s3` refusing a listed figure while the S3
            # copy of the table 500s, where the next tier serves it from `/bin/`.
            # Both end `expected_but_missing`, which is not settled, so every later
            # batch repeats the identical truncated run.
            #
            # A tier that means "I saw the set and refused all of it" says so by
            # leaving `suppl_status` unset -- `pmc_s3`'s `if not kept`, `europepmc`'s
            # `if not members`, `pmc_supplements`' and `biorxiv`'s `if not wanted`,
            # `proxy_browser`'s `if not attempted`. `fetched`/`fetched_unverified`
            # also stop the loop and must: the tier bounded the set, and this
            # function's own `AFTER_DOWNLOAD` filter is what emptied it.
            need_supplements = False

    if not record["tiers_tried"]:
        record["problems"].append(_no_tier_applied(ids, tier_names))

    # -- write everything out ----------------------------------------------

    directory.mkdir(parents=True, exist_ok=True)

    pdf_status = _best_pdf_status(pdf_statuses)

    existing_fulltext = (existing or {}).get("fulltext") or {}
    existing_pdf_ok = (existing_fulltext.get("status") in store.PDF_USABLE
                       and _still_on_disk(directory, existing_fulltext))

    # A PDF that is not this paper is adopted only once every tier has failed to
    # produce one that is, and only when nothing already on disk beats it --
    # otherwise a re-fetch that stumbles onto a wrong document would displace a
    # verified good file with an unverified bad one. Keeping the wrong-document
    # fallback itself is still deliberate: for 10.1126/science.adf1226 the vendor
    # manual was the only file any tier ever returned, and it is the evidence for
    # the problem line.
    if pdf_file is None and unverified_pdf is not None and not existing_pdf_ok:
        pdf_file, pdf_tier = unverified_pdf, unverified_tier
        pdf_status = "identity_unverified"
        record["problems"].append(identity_problem("PDF", ids.doi, ids.title or "", unverified_meta))
    # A notice about an article is not the article, whatever its bytes parse as, so
    # this outranks every other verdict -- including a good existing file, since a
    # freshly-detected notice is evidence the existing file should not have this
    # status either. 10.1038/s41586-024-08560-0 is why: the Author Correction
    # produced a valid one-page PDF and valid JATS, and both carry the
    # *correction's* own DOI and title, so the identity check above said
    # `verified` about them -- correctly, and uselessly.
    if not_article is not None:
        pdf_status = "not_research_article"
        xml_status = "not_research_article"
        record["problems"].append(not_article)

    if pdf_file is not None:
        entry = store.save_file(directory, store.FULLTEXT_PDF, pdf_file.content)
        entry.update({"status": pdf_status, "url": pdf_file.url,
                      "content_type": pdf_file.content_type, "tier": pdf_tier})
        record["fulltext"] = entry
    elif existing_pdf_ok and not_article is None:
        record["fulltext"] = existing_fulltext
        record["problems"].append(
            f"re-fetch found no usable PDF ({pdf_status}); kept the existing one already on disk"
        )
    else:
        record["fulltext"] = {"status": pdf_status, "path": None}

    existing_xml = (existing or {}).get("fulltext_xml") or {}
    existing_xml_ok = (existing_xml.get("status") == "ok"
                       and _still_on_disk(directory, existing_xml))
    if xml_file is not None:
        entry = store.save_file(directory, store.FULLTEXT_XML, xml_file.content)
        entry.update({"status": xml_status, "url": xml_file.url,
                      "label": xml_file.label, "tier": xml_tier})
        record["fulltext_xml"] = entry
        if xml_status == "identity_unverified":
            record["problems"].append(identity_problem("JATS XML", ids.doi, ids.title or "", xml_identity))
    elif existing_xml_ok and not_article is None:
        record["fulltext_xml"] = existing_xml

    existing_landing = (existing or {}).get("landing_html") or {}
    if landing_file is not None:
        entry = store.save_file(directory, store.LANDING_HTML, landing_file.content)
        entry.update({"url": landing_file.url})
        record["landing_html"] = entry
    elif _still_on_disk(directory, existing_landing):
        record["landing_html"] = existing_landing

    existing_supplementary = (existing or {}).get("supplementary") or []
    existing_media = (existing or {}).get("media") or []
    existing_supplementary_ok = bool(existing_supplementary) and all(
        _entry_accounted_for(directory, entry) for entry in existing_supplementary
    )
    existing_media_ok = bool(existing_media) and all(
        _entry_accounted_for(directory, entry) for entry in existing_media
    )

    new_supplementary = _write_group(directory, store.SUPPLEMENT_DIR, supplements)
    if new_supplementary:
        record["supplementary"] = new_supplementary
    elif existing_supplementary_ok:
        record["supplementary"] = existing_supplementary
        # Two sentences because `_entry_accounted_for` accepts two different true
        # statements, and only one of them is about bytes on disk. For an article
        # `drop-media` swept end to end -- a supplement set that was all figures --
        # every kept entry is a removal marker and `supplementary/` is not even there,
        # so "the existing set already on disk" would be a durable falsehood in the
        # one field that is prose about the corpus. The record is still the right thing
        # to keep, which is what the swapped guard is for; it is the claim that had to
        # narrow with it. Reachable for a minority of the 138 articles that hold a
        # non-text supplement -- the ones where 100% rather than the measured 71% of
        # the supplement slots are non-text -- and for the rest the first sentence
        # stays true, because a spreadsheet is still there.
        record["problems"].append(
            "re-fetch found no supplementary files; kept the existing record, whose "
            "files a policy sweep had already removed"
            if all(store.entry_removed_by_policy(entry)
                   for entry in existing_supplementary)
            else "re-fetch found no supplementary files; kept the existing set "
                 "already on disk"
        )
    else:
        record["supplementary"] = new_supplementary  # []

    new_media = _write_group(directory, store.MEDIA_DIR, media)
    if new_media:
        record["media"] = new_media
    elif existing_media_ok:
        record["media"] = existing_media

    skipped_supplements = sum(1 for entry in skipped_files
                              if entry["role"] == ROLE_SUPPLEMENT)
    if new_supplementary:
        record["supplementary_status"] = _supplement_status(
            ids, want_supplements, len(supplements), suppl_statuses, skipped_supplements
        )
    elif existing_supplementary_ok:
        # The old verdict is still the honest one: nothing this run learned
        # replaces it, so recomputing from an empty `supplements` would claim
        # less than what is actually on disk (e.g. `expected_but_missing` over
        # a set that was in fact retrieved earlier).
        record["supplementary_status"] = (existing or {}).get("supplementary_status") \
            or _supplement_status(ids, want_supplements, 0, suppl_statuses,
                                  skipped_supplements)
    else:
        record["supplementary_status"] = _supplement_status(
            ids, want_supplements, len(supplements), suppl_statuses, skipped_supplements
        )
    # Advice outlives its obstacle unless something retires it. A tier that hit
    # PMC's bot check says "re-run with --headed"; if a later tier then collected
    # the supplements from the publisher, that sentence sends the user to spend a
    # headed run on files already on disk. Measured on 10.1016/j.cell.2021.04.038,
    # which finished `fetched_unverified` with all 6 of its supplements -- matching
    # the hand-verified count in `manual_fetch.yaml` -- and still advised a re-run.
    #
    # Only the instruction is conditional. What each tier ran into stays in
    # `problems` either way, because that is a record of the run and stays true.
    if want_supplements and record["supplementary_status"] not in store.SUPPL_SETTLED:
        record["problems"].extend(suppl_advice)
    store.finalize_status(record)
    store.write_manifest(directory, {k: v for k, v in record.items() if k != "_directory"})

    # Delete what this run's numbering just abandoned. `_write_group` names files
    # `<subdir>/<NN>_<name>` with `NN` from `enumerate()`, so a re-fetch returning a
    # different-sized or differently-ordered set writes new names and leaves the old
    # ones behind; measured at 202 files and 1.37 GB across 29 articles before this
    # existed, growing by 50 files with a single 38-article `--force` batch.
    #
    # **After the manifest, never before, and that is the whole of the ordering
    # argument.** A crash between the two leaves unreferenced files, which is what
    # this sweep is for and what the next one removes. A crash the other way round
    # leaves files deleted while the record still names them, which makes
    # `manifest_is_complete` false and sends every later batch re-fetching the whole
    # article -- the same trap `drop_media` writes its manifest per file to stay out
    # of, arrived at from the opposite direction (see `store.mark_entry_removed` on
    # why a removal must not keep its `path`).
    #
    # `record` and not `new_supplementary`: the preservation branches above keep the
    # *existing* entry list when a re-fetch came away empty, so the referenced set is
    # then the previous numbering and sweeping what this run wrote would delete the
    # very set that branch exists to save. `orphans.unreferenced_files` reads the
    # final record for that reason.
    swept = orphans.sweep_article(directory, record)
    if swept["files"]:
        record["orphans_swept"] = {"files": len(swept["files"]),
                                   "bytes": swept["bytes"]}
    for failure in swept["failed"]:
        # Recorded but not fatal: the manifest is already written and correct, and a
        # stale file that could not be unlinked is exactly what `drop-orphans` is for.
        record["problems"].append(
            f"could not remove the unreferenced file {failure['path']}: "
            f"{failure['error']}"
        )
    if swept["files"] or swept["failed"]:
        store.write_manifest(directory,
                             {k: v for k, v in record.items() if k != "_directory"})

    # Keep the corpus inside its size budget, evicting oldest-first. The article
    # just fetched is never the one evicted. After the sweep above, so the budget
    # measures the article as it now stands rather than counting 1.37 GB of files
    # that are about to go and evicting a neighbour to make room for them.
    max_gb = fetch_cfg.get("max_corpus_gb")
    if max_gb:
        outcome = store.enforce_budget(corpus_dir, int(float(max_gb) * 1024 ** 3))
        if outcome["evicted"]:
            record["budget"] = outcome
            for item in outcome["evicted"]:
                record["problems"].append(
                    f"evicted {item['slug']} ({store.human_bytes(item['freed_bytes'])}) "
                    f"to stay under the {max_gb} GB corpus budget"
                )

    return record


def _write_group(directory, subdir: str, files: List) -> List[dict]:
    entries = []
    for index, item in enumerate(files, start=1):
        relative = f"{subdir}/{store.supplement_filename(index, item.name)}"
        entry = store.save_file(directory, relative, item.content)
        entry.update({
            "index": index,
            "url": item.url,
            "label": item.label,
            "tier": item.tier,
            "content_type": item.content_type,
            "original_name": item.name,
        })
        entries.append(entry)
    return entries
