"""DOI normalisation and identifier resolution.

Deterministic and code-only: no model is involved in deciding what a paper is or
where it lives.

Europe PMC's search endpoint answers, in a single request, everything needed to
route a fetch: the PMCID and PMID, whether the article is in PMC at all, and --
critically -- whether supplementary files exist. `hasSuppl` is publisher-supplied
metadata, and it is what lets the fetcher tell "this article has no supplements"
apart from "we failed to find them". Guessing that distinction from our own scraping
is exactly the kind of silent ambiguity this pipeline is built to avoid.
`hasPDF` is recorded in the manifest for provenance and drives nothing -- see
`suppl_flag_is_authoritative` for the conditions under which even `hasSuppl` is
believed.

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

    Accepts `10.1038/x`, `doi:10.1038/x`, `https://doi.org/10.1038/x`, and the
    same with surrounding whitespace or a trailing period. The registrant code --
    the digits between `10.` and the `/` -- must be 4 to 9 long, so a short
    prefix like `10.1/x` is refused rather than normalised. DOIs are
    case-insensitive per the DOI handbook, so lowercasing is safe and makes the
    corpus one-to-one.
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
    #: Europe PMC's own `pubTypeList`, lowercased. It is how the index says what
    #: kind of document a DOI is, and for 10.1038/s41586-024-08560-0 it answers
    #: `['published erratum', 'correction']` -- before a byte is downloaded. See
    #: `validate.not_research_article`, which reads it.
    pub_types: List[str] = field(default_factory=list)
    #: The DOI this record is a notice *about*, when Europe PMC says it is one.
    #: Structured, from `commentCorrectionList`, and the reason it is worth having
    #: over reading the notice's body: a notice cites its own references too, so
    #: "the first other DOI in the text" is a guess where this is the answer.
    corrects_doi: Optional[str] = None
    full_text_urls: List[Dict] = field(default_factory=list)
    landing_url: Optional[str] = None          # publisher page, from Crossref or doi.org
    lookup_doi: Optional[str] = None           # set when a variant DOI resolved instead
    resolved_by: List[str] = field(default_factory=list)
    #: Things a lookup service told us that are neither a resolution nor a failure.
    #: "PMC has no deposit for this DOI" is the whole motivating case: it is the
    #: correct answer for most paywalled papers, so it is not a `problems` entry, but
    #: `resolved_by` is a provenance list of services that *did* resolve something and
    #: a sentence is not a service name. Kept apart so a manifest consumer reading
    #: `resolved_by` still sees only `europepmc`, `ncbi_idconv`, `crossref`.
    lookup_notes: List[str] = field(default_factory=list)
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
            # `source` and `id` are one composite key -- `MED/12345`, `PPR/998877` --
            # so recording the source alone left the manifest with half an
            # identifier. It matters most for preprints, where the `PPR` number is
            # the only handle Europe PMC answers to: neither `pmid` nor `pmcid` is
            # set for them.
            "epmc_id": self.epmc_id,
            "title": self.title,
            "journal": self.journal,
            "year": self.year,
            "publisher": self.publisher,
            "license": self.license,
            "is_open_access": self.is_open_access,
            "in_epmc": self.in_epmc,
            "in_pmc": self.in_pmc,
            "has_pdf": self.has_pdf,
            "pub_types": self.pub_types,
            "corrects_doi": self.corrects_doi,
            "has_suppl": self.has_suppl,
            "is_preprint": self.is_preprint,
            "landing_url": self.landing_url,
            "lookup_doi": self.lookup_doi,
            "resolved_by": self.resolved_by,
            "lookup_notes": self.lookup_notes,
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
    ids.pub_types = [str(t).strip().lower() for t in
                     (record.get("pubTypeList", {}) or {}).get("pubType", []) or []]
    ids.corrects_doi = _corrected_article_doi(record)
    ids.full_text_urls = (record.get("fullTextUrlList", {}) or {}).get("fullTextUrl", []) or []
    ids.resolved_by.append("europepmc")


#: `commentCorrectionList` relation types that mean *this* record is the notice.
#: Europe PMC states the relation from both ends and only the direction matters:
#: the Author Correction 10.1038/s41586-024-08560-0 carries `Erratum for`, while
#: the article it corrects carries `Erratum in`. Reading the wrong direction would
#: reject every article that has ever been corrected, which is backwards and would
#: be a far worse bug than the one this is here to fix.
_NOTICE_RELATIONS = ("erratum for", "retraction of", "correction of",
                     "expression of concern for")


def _corrected_article_doi(record: dict) -> Optional[str]:
    """The DOI of the article this record is a correction or retraction *of*.

    Europe PMC gives the relation as a type plus a citation string, so the DOI has
    to come out of the citation: `Nature. 2025 Jan;637(8047):947-954.
    doi: 10.1038/s41586-024-08150-0.` None when this record is not a notice.
    """
    entries = (record.get("commentCorrectionList", {}) or {}).get("commentCorrection", [])
    for entry in entries or []:
        relation = str(entry.get("type") or "").strip().lower()
        if not any(relation.startswith(prefix) for prefix in _NOTICE_RELATIONS):
            continue
        match = _DOI_RX.search(str(entry.get("reference") or ""))
        if match:
            return match.group(0).rstrip(".,;)").lower()
    return None


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
        # NOT a problem. "Identifier not found in PMC" is the correct answer for any
        # paper without a PMC deposit, which is most paywalled ones -- so promoting it
        # to a `problems` entry printed a `!` line for nearly every DOI in a batch,
        # with the same visual weight as a real refusal. On
        # 10.1016/j.oraloncology.2021.105348 it was the *only* line the user got, which
        # made a genuine browser-tier failure look like a PMC lookup miss.
        #
        # Not `resolved_by` either, which is where d09d7b2 put it: that is a list of
        # services that resolved something, and `ncbi_idconv:Identifier not found in
        # PMC` is not a service name. `lookup_notes` is for exactly this -- a true
        # thing a service said that is neither a resolution nor a failure.
        #
        # The HTTP and exception cases above stay in `problems`: those are the service
        # failing, which is a different thing from the service answering "no".
        ids.lookup_notes.append(f"ncbi_idconv: {record.get('errmsg', 'no match')}")
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
