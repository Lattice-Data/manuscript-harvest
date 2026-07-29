"""Tier orchestration.

Each tier is asked only for what is still missing, and the loop stops as soon as
there is a usable PDF and the supplement question is settled.

The status taxonomy is the point of this module. `audit/runs.jsonl` already
contains a run that reported `valid` while extracting nothing, because an empty
result and a failed result looked identical downstream. The equivalent trap here
is an empty `supplementary/` directory, so the two cases are named apart:

    none_listed          the publisher says this article has no supplements
    fetched              it has them and we have them
    partial_failure      we got some; at least one download or archive failed
    expected_but_missing hasSuppl=Y, and we came away with nothing  <-- the bug case
    page_not_parsed      a page loaded but we could not read a file list from it
    unknown_none_found   nobody told us whether supplements exist, and we found none
    not_requested        --no-supplements

`expected_but_missing` is the state the whole taxonomy exists to expose.
"""

from typing import Dict, List, Optional

from . import store
from .http import Http
from .identifiers import Identifiers, normalize_doi, resolve_identifiers
from .sources import DEFAULT_TIERS, build_sources
from .sources.base import (
    ROLE_LANDING,
    ROLE_MEDIA,
    ROLE_PDF,
    ROLE_SUPPLEMENT,
    ROLE_XML,
    SourceResult,
)

# Statuses that mean the PDF is on disk and usable.
_PDF_SUCCESS = {"ok", "scanned_pdf_suspected"}

# Diagnoses that name a cause the user can act on. These win wherever they appear,
# because "your session expired" beats "the last thing we tried returned HTML".
_PDF_DIAGNOSES = ["paywalled", "session_expired", "proxy_not_configured",
                  "publisher_stub_page"]

_SETTLED_SUPPL = {"none_listed", "fetched", "not_requested"}


def _best_pdf_status(reported: List[str]) -> str:
    """Pick the most useful explanation from the statuses each tier reported.

    `reported` is in tier order, so the last entry comes from the most capable tier
    that tried. Preferring it avoids an early routing note drowning out a real
    attempt: for 10.1002/path.5751 the tiers said
    `[download_failed, not_in_oa_subset, not_a_pdf]`, and a static ranking surfaced
    `not_in_oa_subset` when the actual cause was Wiley serving an HTML viewer.
    An actionable diagnosis still wins wherever it appears.
    """
    for status in reported:
        if status in _PDF_SUCCESS:
            return status
    for candidate in _PDF_DIAGNOSES:
        if candidate in reported:
            return candidate
    return reported[-1] if reported else "not_found"


def suppl_flag_is_authoritative(ids: Identifiers) -> bool:
    """Can `hasSuppl: N` be believed as "this article has no supplements"?

    Only when Europe PMC or PMC actually holds the article. The flag describes
    *their* holdings, not the article, and two measured cases prove the
    difference:

    - Preprints: Europe PMC says hasSuppl=N for 10.1101/2025.07.21.666016 and
      10.1101/2024.01.23.576878, which have 2 and 6 supplementary files.
    - Articles it does not hold: 10.1016/j.stem.2023.12.013 and
      10.1038/s41591-018-0269-2 both come back inEPMC=N, inPMC=N, hasSuppl=N --
      which says only that Europe PMC has nothing, not that the publisher does.

    Believing the flag in those cases produced a confident `none_listed` over
    files that exist, which is the exact silent loss this pipeline exists to
    prevent.
    """
    if ids.has_suppl is not False:
        return False
    if ids.is_preprint:
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
        # still in `attempts` and `problems`. Only a tier that got everything it
        # attempted reports "fetched", so seeing it means the set is whole.
        if "fetched" in reported:
            return "fetched"
        return "partial_failure"
    # A source that owns the content (bioRxiv for its own preprints) can state
    # authoritatively that there are none, even when the index disagrees.
    if "none_listed" in reported:
        return "none_listed"
    if ids.has_suppl is True:
        return "expected_but_missing"
    if "page_not_parsed" in reported:
        return "page_not_parsed"
    return "unknown_none_found"


def build_http(config: dict) -> Http:
    fetch_cfg = config.get("fetch", {}) or {}
    return Http(
        contact_email=fetch_cfg.get("contact_email"),
        min_interval_seconds=fetch_cfg.get("min_interval_seconds", 3.0),
        timeout_seconds=fetch_cfg.get("timeout_seconds", 60),
        ncbi_api_key=fetch_cfg.get("ncbi_api_key"),
    )


def fetch_publication(
    doi: str,
    config: dict,
    force: bool = False,
    want_supplements: bool = True,
    tiers: Optional[List[str]] = None,
    http: Optional[Http] = None,
) -> dict:
    """Fetch one publication into the corpus. Returns the manifest record.

    Never raises for an unreachable paper -- a manifest with `status: failed` and
    the attempts that produced it is more useful than a traceback.
    """
    fetch_cfg = config.get("fetch", {}) or {}
    corpus_dir = fetch_cfg.get("corpus_dir", "corpus")
    tier_names = list(tiers if tiers is not None else fetch_cfg.get("tiers", DEFAULT_TIERS))

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

    record = store.new_record(ids, corpus_dir)
    record["_directory"] = str(directory)
    record["tiers_configured"] = tier_names

    need_pdf = True
    # Only skip the supplement search when hasSuppl=N is actually believable --
    # see `suppl_flag_is_authoritative` for the cases where it is not.
    need_supplements = want_supplements and not suppl_flag_is_authoritative(ids)

    pdf_statuses: List[str] = []
    suppl_statuses: List[str] = []
    pdf_file = None
    pdf_tier = None
    xml_file = None
    xml_tier = None
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
        if result.pdf_status:
            pdf_statuses.append(result.pdf_status)
        if result.suppl_status:
            suppl_statuses.append(result.suppl_status)

        for item in result.files:
            if item.role == ROLE_PDF and pdf_file is None:
                pdf_file, pdf_tier = item, source.name
            elif item.role == ROLE_XML and xml_file is None:
                xml_file, xml_tier = item, source.name
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

    # -- write everything out ----------------------------------------------

    directory.mkdir(parents=True, exist_ok=True)

    pdf_status = _best_pdf_status(pdf_statuses)
    if pdf_file is not None:
        entry = store.save_file(directory, store.FULLTEXT_PDF, pdf_file.content)
        entry.update({"status": pdf_status, "url": pdf_file.url,
                      "content_type": pdf_file.content_type, "tier": pdf_tier})
        record["fulltext"] = entry
    else:
        record["fulltext"] = {"status": pdf_status, "path": None}

    if xml_file is not None:
        entry = store.save_file(directory, store.FULLTEXT_XML, xml_file.content)
        entry.update({"url": xml_file.url, "label": xml_file.label, "tier": xml_tier})
        record["fulltext_xml"] = entry

    if landing_file is not None:
        entry = store.save_file(directory, store.LANDING_HTML, landing_file.content)
        entry.update({"url": landing_file.url})
        record["landing_html"] = entry

    record["supplementary"] = _write_group(
        directory, store.SUPPLEMENT_DIR, supplements
    )
    if media:
        record["media"] = _write_group(directory, store.MEDIA_DIR, media)

    record["supplementary_status"] = _supplement_status(
        ids, want_supplements, len(supplements), suppl_statuses
    )
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


def is_settled(record: dict) -> bool:
    """True when a record needs no further tiers."""
    return (
        (record.get("fulltext") or {}).get("status") in _PDF_SUCCESS
        and record.get("supplementary_status") in _SETTLED_SUPPL
    )
