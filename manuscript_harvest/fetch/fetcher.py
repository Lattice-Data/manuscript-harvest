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
    partial_failure      we got some; at least one download or archive failed
    expected_but_missing hasSuppl=Y, and we came away with nothing  <-- the bug case
    none_retrieved       a tier tried and every file it went after was lost
    page_not_parsed      a page loaded but we could not read a file list from it
    unknown_none_found   nobody told us whether supplements exist, and we found none
    not_requested        --no-supplements

`expected_but_missing` is the state the whole taxonomy exists to expose.

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

from functools import reduce
from pathlib import Path
from typing import Dict, List, Optional

from . import store
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
    ROLE_LANDING,
    ROLE_MEDIA,
    ROLE_PDF,
    ROLE_SUPPLEMENT,
    ROLE_XML,
)

# Statuses that mean the PDF is on disk and usable. Defined in `store`, which is
# where `finalize_status` reads it, so the orchestrator and the record it writes
# cannot disagree about what counts as having the article.
_PDF_SUCCESS = store.PDF_USABLE


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
) -> str:
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
    fetch_cfg = config.get("fetch", {}) or {}
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

        if pdf_file is not None:
            need_pdf = False
        if supplements:
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
        _still_on_disk(directory, entry) for entry in existing_supplementary
    )
    existing_media_ok = bool(existing_media) and all(
        _still_on_disk(directory, entry) for entry in existing_media
    )

    new_supplementary = _write_group(directory, store.SUPPLEMENT_DIR, supplements)
    if new_supplementary:
        record["supplementary"] = new_supplementary
    elif existing_supplementary_ok:
        record["supplementary"] = existing_supplementary
        record["problems"].append(
            "re-fetch found no supplementary files; kept the existing set already on disk"
        )
    else:
        record["supplementary"] = new_supplementary  # []

    new_media = _write_group(directory, store.MEDIA_DIR, media)
    if new_media:
        record["media"] = new_media
    elif existing_media_ok:
        record["media"] = existing_media

    if new_supplementary:
        record["supplementary_status"] = _supplement_status(
            ids, want_supplements, len(supplements), suppl_statuses
        )
    elif existing_supplementary_ok:
        # The old verdict is still the honest one: nothing this run learned
        # replaces it, so recomputing from an empty `supplements` would claim
        # less than what is actually on disk (e.g. `expected_but_missing` over
        # a set that was in fact retrieved earlier).
        record["supplementary_status"] = (existing or {}).get("supplementary_status") \
            or _supplement_status(ids, want_supplements, 0, suppl_statuses)
    else:
        record["supplementary_status"] = _supplement_status(
            ids, want_supplements, len(supplements), suppl_statuses
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

    # Keep the corpus inside its size budget, evicting oldest-first. The article
    # just fetched is never the one evicted.
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
