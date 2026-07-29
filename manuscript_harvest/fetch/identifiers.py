"""DOI normalisation and identifier resolution.

Deterministic and code-only: no model is involved in deciding what a paper is or
where it lives.

Europe PMC's search endpoint answers, in a single request, everything needed to
route a fetch: the PMCID and PMID, whether the article is in PMC at all, and --
critically -- whether a PDF and supplementary files exist. Those last two flags
(`hasPDF`, `hasSuppl`) are publisher-supplied metadata, which is what lets the
fetcher tell "this article has no supplements" apart from "we failed to find
them". Guessing that distinction from our own scraping is exactly the kind of
silent ambiguity this pipeline is built to avoid.

NCBI's ID Converter and Crossref are fallbacks for records Europe PMC lacks.
"""

import hashlib
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .http import Http, HttpError

EUROPEPMC_SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
NCBI_IDCONV = "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/"
CROSSREF_WORKS = "https://api.crossref.org/works/"

# A DOI is "10." + registrant + "/" + suffix. The suffix has no fixed grammar,
# so we take everything up to whitespace and strip trailing sentence punctuation.
_DOI_RX = re.compile(r"10\.\d{4,9}/\S+")
_MAX_SLUG = 150

# eLife reviewed preprints carry a version suffix (10.7554/eLife.104978.2) that
# indexes do not always hold -- verified: the versioned form has no Europe PMC
# record while the unversioned one resolves to PMC12893711. Only 1-2 trailing
# digits count as a version, so an article number like
# 10.1016/j.cell.2021.01.053 is never mistaken for one. This is a fallback tried
# only after the exact DOI comes back empty, so a wrong guess costs one request
# and is recorded either way.
_VERSION_SUFFIX_RX = re.compile(r"^(?P<base>10\.\d{4,9}/.+)\.(?P<version>\d{1,2})$")


def unversioned_doi(doi: str) -> Optional[str]:
    """Strip a trailing version suffix, or None if there is nothing to strip."""
    match = _VERSION_SUFFIX_RX.match(doi)
    return match.group("base") if match else None


# bioRxiv/medRxiv historically minted 10.1101 DOIs, but have migrated to openRxiv's
# 10.64898 prefix -- so every newly posted preprint carries the new one. Verified:
# Crossref reports publisher "openRxiv" for 10.64898/2026.02.15.704933, the bioRxiv
# details API answers for it, and it resolves to biorxiv.org/lookup/doi/...
# Gating only on 10.1101 silently skipped the whole tier for new preprints.
PREPRINT_PREFIXES = ("10.1101/", "10.64898/")


def is_preprint_doi(doi: str) -> bool:
    return any(doi.startswith(prefix) for prefix in PREPRINT_PREFIXES)


def normalize_doi(raw: str) -> str:
    """Return a bare lowercase DOI, or raise ValueError.

    Accepts `10.1/x`, `doi:10.1/x`, `https://doi.org/10.1/x`, and the same with
    surrounding whitespace or a trailing period. DOIs are case-insensitive per
    the DOI handbook, so lowercasing is safe and makes the corpus one-to-one.
    """
    text = (raw or "").strip()
    text = re.sub(r"^https?://(dx\.)?doi\.org/", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^(doi:|info:doi/)\s*", "", text, flags=re.IGNORECASE)
    match = _DOI_RX.search(text)
    if not match:
        raise ValueError(f"not a DOI: {raw!r}")
    return match.group(0).rstrip(".,;)").lower()


def doi_slug(doi: str) -> str:
    """Filesystem-safe directory name for a DOI.

    `/` becomes `_` and anything else outside [A-Za-z0-9._-] becomes `-`. Long
    DOIs are truncated with a hash suffix so two different DOIs can never
    collide on one corpus directory.
    """
    normalized = normalize_doi(doi)
    slug = re.sub(r"[^A-Za-z0-9._-]", "-", normalized.replace("/", "_"))
    if len(slug) > _MAX_SLUG:
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:8]
        slug = f"{slug[: _MAX_SLUG - 9]}-{digest}"
    return slug


def _yes_no(value) -> Optional[bool]:
    """Europe PMC reports booleans as the strings 'Y'/'N'. Unknown stays None."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if value.upper() == "Y":
            return True
        if value.upper() == "N":
            return False
    return None


@dataclass
class Identifiers:
    """What we know about a paper before trying to download anything."""

    doi: str                                   # normalized
    doi_raw: str                               # exactly what the caller passed
    pmid: Optional[str] = None
    pmcid: Optional[str] = None
    epmc_source: Optional[str] = None          # Europe PMC 'source': MED, PMC, PPR...
    epmc_id: Optional[str] = None
    title: Optional[str] = None
    journal: Optional[str] = None
    year: Optional[str] = None
    publisher: Optional[str] = None
    license: Optional[str] = None
    is_open_access: Optional[bool] = None
    in_epmc: Optional[bool] = None
    in_pmc: Optional[bool] = None
    has_pdf: Optional[bool] = None
    has_suppl: Optional[bool] = None
    full_text_urls: List[Dict] = field(default_factory=list)
    landing_url: Optional[str] = None          # publisher page, from Crossref or doi.org
    lookup_doi: Optional[str] = None           # set when a variant DOI resolved instead
    resolved_by: List[str] = field(default_factory=list)
    problems: List[str] = field(default_factory=list)

    @property
    def is_preprint(self) -> bool:
        return is_preprint_doi(self.doi) or self.epmc_source == "PPR"

    def open_access_pdf_urls(self) -> List[str]:
        """PDF URLs from Europe PMC's fullTextUrlList that are marked open."""
        urls = []
        for entry in self.full_text_urls:
            style = (entry.get("documentStyle") or "").lower()
            code = (entry.get("availabilityCode") or "").upper()
            url = entry.get("url")
            if style == "pdf" and code in {"OA", "F"} and url:
                urls.append(url)
        return urls

    def to_dict(self) -> dict:
        return {
            "doi": self.doi,
            "doi_raw": self.doi_raw,
            "pmid": self.pmid,
            "pmcid": self.pmcid,
            "epmc_source": self.epmc_source,
            "title": self.title,
            "journal": self.journal,
            "year": self.year,
            "publisher": self.publisher,
            "license": self.license,
            "is_open_access": self.is_open_access,
            "in_epmc": self.in_epmc,
            "in_pmc": self.in_pmc,
            "has_pdf": self.has_pdf,
            "has_suppl": self.has_suppl,
            "is_preprint": self.is_preprint,
            "landing_url": self.landing_url,
            "lookup_doi": self.lookup_doi,
            "resolved_by": self.resolved_by,
            "problems": self.problems,
        }


def _query_europepmc(ids: Identifiers, http: Http, doi: Optional[str] = None) -> None:
    """Fill `ids` from Europe PMC. Records a problem rather than raising."""
    doi = doi or ids.doi
    try:
        resp = http.get(
            EUROPEPMC_SEARCH,
            params={
                "query": f'DOI:"{doi}"',
                "format": "json",
                "resultType": "core",
                "pageSize": "1",
            },
            accept="application/json",
        )
    except HttpError as e:
        ids.problems.append(f"europepmc lookup failed: {e}")
        return

    if not resp.ok:
        ids.problems.append(f"europepmc lookup returned HTTP {resp.status}")
        return

    try:
        results = resp.json().get("resultList", {}).get("result", [])
    except ValueError as e:
        ids.problems.append(f"europepmc returned unparseable JSON: {e}")
        return

    if not results:
        ids.problems.append(f"europepmc has no record for {doi}")
        return

    record = results[0]
    ids.epmc_id = record.get("id")
    ids.epmc_source = record.get("source")
    ids.pmid = record.get("pmid") or ids.pmid
    ids.pmcid = record.get("pmcid") or ids.pmcid
    ids.title = record.get("title") or ids.title
    ids.journal = (record.get("journalInfo", {}).get("journal", {}) or {}).get("title") or ids.journal
    ids.year = str(record.get("pubYear")) if record.get("pubYear") else ids.year
    ids.license = record.get("license") or ids.license
    ids.is_open_access = _yes_no(record.get("isOpenAccess"))
    ids.in_epmc = _yes_no(record.get("inEPMC"))
    ids.in_pmc = _yes_no(record.get("inPMC"))
    ids.has_pdf = _yes_no(record.get("hasPDF"))
    ids.has_suppl = _yes_no(record.get("hasSuppl"))
    ids.full_text_urls = (record.get("fullTextUrlList", {}) or {}).get("fullTextUrl", []) or []
    ids.resolved_by.append("europepmc")


def _query_ncbi_idconv(ids: Identifiers, http: Http) -> None:
    """Fallback DOI -> PMCID/PMID for records Europe PMC does not index."""
    try:
        resp = http.get(
            NCBI_IDCONV,
            params={"ids": ids.doi, "format": "json", "versions": "no"},
            accept="application/json",
        )
        if not resp.ok:
            ids.problems.append(f"ncbi idconv returned HTTP {resp.status}")
            return
        records = resp.json().get("records", [])
    except (HttpError, ValueError) as e:
        ids.problems.append(f"ncbi idconv failed: {e}")
        return

    if not records:
        return
    record = records[0]
    if record.get("status") == "error" or record.get("errmsg"):
        ids.problems.append(f"ncbi idconv: {record.get('errmsg', 'no match')}")
        return
    ids.pmcid = record.get("pmcid") or ids.pmcid
    ids.pmid = record.get("pmid") or ids.pmid
    ids.resolved_by.append("ncbi_idconv")


def _query_crossref(ids: Identifiers, http: Http) -> None:
    """Crossref for publisher/journal/title and the publisher landing URL.

    Only metadata -- Crossref is not a content source here.
    """
    try:
        resp = http.get(CROSSREF_WORKS + ids.doi, accept="application/json")
        if not resp.ok:
            ids.problems.append(f"crossref returned HTTP {resp.status}")
            return
        message = resp.json().get("message", {})
    except (HttpError, ValueError) as e:
        ids.problems.append(f"crossref lookup failed: {e}")
        return

    ids.publisher = message.get("publisher") or ids.publisher
    titles = message.get("title") or []
    if titles and not ids.title:
        ids.title = titles[0]
    containers = message.get("container-title") or []
    if containers and not ids.journal:
        ids.journal = containers[0]
    if not ids.year:
        parts = (message.get("issued", {}) or {}).get("date-parts") or [[]]
        if parts and parts[0]:
            ids.year = str(parts[0][0])
    primary = ((message.get("resource") or {}).get("primary") or {}).get("URL")
    if primary:
        ids.landing_url = primary
    ids.resolved_by.append("crossref")


def resolve_identifiers(doi: str, http: Http, need_landing_url: bool = False) -> Identifiers:
    """Resolve a DOI to identifiers and availability flags.

    Europe PMC first, because one call covers identifiers *and* the routing
    flags. NCBI's converter is consulted only when Europe PMC produced no
    PMCID; Crossref only when metadata is still missing or the caller needs a
    publisher landing URL for the browser tier.
    """
    ids = Identifiers(doi=normalize_doi(doi), doi_raw=doi)

    _query_europepmc(ids, http)

    # A versioned DOI (eLife reviewed preprints) is often absent from indexes
    # while its unversioned form is present. Retry once before giving up.
    if not ids.pmcid and ids.epmc_source is None:
        base = unversioned_doi(ids.doi)
        if base:
            _query_europepmc(ids, http, doi=base)
            if ids.epmc_source is not None:
                ids.lookup_doi = base
                ids.problems.append(
                    f"resolved via the unversioned DOI {base} "
                    f"(no index record for {ids.doi})"
                )

    if not ids.pmcid:
        _query_ncbi_idconv(ids, http)
    if need_landing_url or not ids.title or not ids.journal:
        _query_crossref(ids, http)

    return ids
