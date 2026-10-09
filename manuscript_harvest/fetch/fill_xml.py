"""Fetch the JATS XML for stored articles that have none, touching nothing else.

Why this exists rather than `--force`. 79 of the 330 PMC articles in this corpus were
on disk without a JATS: Europe PMC's PDF route had succeeded, its `fullTextXML` had
answered 404, and until the XML became a need of its own (`need_xml` on
`Source.fetch`) no tier was ever asked for it alone. The fix in the tier loop covers
the next fetch. For the articles already here, `--force` would re-run every tier --
55 of the 79 got their PDF through the proxy browser, so that means a live Cardinal
Key session, a browser launch apiece, and the chance of a re-fetch coming back with a
*smaller* supplement set than the one on disk, which is the loss
`orphans.sweep_article` records. This pass asks only the tiers that can supply XML,
only for the XML, and writes only `fulltext.nxml` and its manifest entry. The PDF,
the supplements, their statuses and the article's own status are not read, let alone
changed.

`revalidate`'s shape: report by default, `--apply` to write. Unlike `revalidate` the
report costs the same requests as the write -- the only way to know a JATS is there
is to fetch it -- so the report mode exists to see the per-article outcome before any
manifest changes, not to save bandwidth.

The checks a fresh fetch would run on a JATS run here too: identity
(`validate.identify_fulltext`) and the article-type notice check. A JATS that is a
correction notice is stored with `status: not_research_article` and the problem
line, and the article's own status is left for `revalidate` to re-derive -- this pass
does not write a verdict about a PDF it never looked at.

The extraction stage keys its cache on the manifest's sha256, so every article this
fills is re-extracted once by the next `manuscript-extract all`, from the JATS it now
has.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from . import store
from .fetcher import build_http
from .identifiers import Identifiers
from .sources import DEFAULT_TIERS, build_sources
from .sources.base import ROLE_XML
from .validate import (
    identify_fulltext,
    identity_problem,
    jats_article_type,
    jats_sample_text,
    not_research_article,
)


def identifiers_from_record(record: dict) -> Identifiers:
    """The `Identifiers` a manifest recorded, rebuilt without a lookup.

    `Identifiers.to_dict` wrote these; only the dataclass's own fields are read back,
    so the `is_preprint` property it also writes is derived again rather than passed,
    and a key a later version adds falls away rather than raising. Keys a manifest
    written before a field existed does not carry fall to the field's default, and no
    lookup is repeated for them: this pass needs the PMCID, the DOI and the title,
    and every manifest has those.
    """
    stored = record.get("identifiers") or {}
    fields = Identifiers.__dataclass_fields__
    known = {key: value for key, value in stored.items()
             if key in fields and value is not None}
    known.setdefault("doi", record["doi"])
    known.setdefault("doi_raw", record.get("doi_raw") or record["doi"])
    return Identifiers(**known)


def xml_sources(config: dict) -> list:
    """The configured tiers that can supply a JATS, in tier order, sharing one client.

    `provides_xml` is the filter, so the browser tier is dropped before a browser is
    ever opened, and `elsevier_tdm`, `pmc_supplements` and `pmc_oa`, which have no XML
    to offer, go with it. `--tiers` narrows the list further.
    """
    fetch_cfg = config.get("fetch", {}) or {}
    names = list(fetch_cfg.get("tiers", DEFAULT_TIERS))
    http = build_http(config)
    return [source for source in build_sources(names, http, fetch_cfg)
            if source.provides_xml]


def fill_xml_article(directory, sources, apply: bool = False) -> dict:
    """Report -- and with `apply`, perform -- one article's fill.

    Returns `{"slug", "doi", "candidate", "filled", "tier", "status", "bytes",
    "tiers_tried", "problems", "note"}`. `candidate` is False for an article that
    needed nothing: a JATS already on disk, no manifest, or evicted.
    """
    directory = Path(directory)
    record = store.read_manifest(directory)
    report: dict = {"slug": directory.name, "doi": None, "candidate": False,
                    "filled": False, "tier": None, "status": None, "bytes": 0,
                    "tiers_tried": [], "problems": [], "note": None}
    if record is None:
        report["note"] = "no manifest"
        return report
    report["doi"] = record.get("doi")
    if record.get("status") == "evicted":
        report["note"] = "evicted: the bytes are gone and the record stands"
        return report
    existing = record.get("fulltext_xml") or {}
    if existing.get("path") and (directory / existing["path"]).exists():
        report["note"] = "has a JATS"
        return report

    report["candidate"] = True
    ids = identifiers_from_record(record)
    attempts: List[dict] = []
    found = None
    for source in sources:
        if not source.applies(ids):
            continue
        report["tiers_tried"].append(source.name)
        try:
            result = source.fetch(ids, need_pdf=False, need_supplements=False,
                                  need_xml=True)
        except Exception as e:  # one broken tier must not end the pass
            attempts.append({"tier": source.name, "action": "fetch",
                             "status": "tier_error",
                             "error": f"{type(e).__name__}: {e}"})
            continue
        attempts.extend(result.attempts)
        xml = result.by_role(ROLE_XML)
        if xml:
            found = (source.name, xml[0])
            break

    if not report["tiers_tried"]:
        report["note"] = ("no configured tier can supply a JATS for this article: "
                          "no PMCID, and not a preprint")
        return report
    if found is None:
        report["note"] = f"no JATS at {', '.join(report['tiers_tried'])}"
        return report

    tier, item = found
    verified, meta = identify_fulltext(jats_sample_text(item.content), ids.doi,
                                       ids.title or "")
    status = "ok" if verified else "identity_unverified"
    problems: List[str] = []
    if not verified:
        problems.append(identity_problem("JATS XML", ids.doi, ids.title or "", meta))
    notice = not_research_article(article_type=jats_article_type(item.content))
    if notice:
        status = "not_research_article"
        problems.append(notice + "; this pass wrote only the JATS, so run "
                        "`manuscript-fetch revalidate` to re-derive the article's status")
    report.update(filled=True, tier=tier, status=status, bytes=len(item.content),
                  problems=problems)
    if not apply:
        return report

    entry = store.save_file(directory, store.FULLTEXT_XML, item.content)
    entry.update({"status": status, "url": item.url, "label": item.label, "tier": tier})
    record["fulltext_xml"] = entry
    # The tier's own notes travel with the entry, as they would have from a fetch:
    # the listing, the version chosen, the object taken.
    record["attempts"] = list(record.get("attempts") or []) + attempts
    already = list(record.get("problems") or [])
    record["problems"] = already + [line for line in problems if line not in already]
    record["xml_filled"] = {
        "at": datetime.now(timezone.utc).isoformat(),
        "tier": tier,
        "status": status,
        "tiers_tried": report["tiers_tried"],
    }
    store.write_manifest(directory, record)
    return report


def fill_xml_corpus(corpus_dir, config: Optional[dict] = None, apply: bool = False,
                    slugs=None, sources=None) -> List[dict]:
    """Walk `corpus_dir` (or just `slugs`), oldest-first by directory name.

    `revalidate_corpus`'s shape, including a report for an article with nothing to
    do, so the closing line can say "392 checked, 79 without a JATS".
    """
    root = Path(corpus_dir).expanduser()
    if not root.exists():
        return []
    if sources is None:
        sources = xml_sources(config or {})
    wanted = set(slugs or ())
    reports = []
    for directory in sorted(p for p in root.iterdir() if p.is_dir()):
        if wanted and directory.name not in wanted:
            continue
        reports.append(fill_xml_article(directory, sources, apply=apply))
    return reports


def summarize(reports: List[dict]) -> dict:
    """Corpus totals for the closing line."""
    candidates = [r for r in reports if r["candidate"]]
    filled = [r for r in candidates if r["filled"]]
    by_tier: dict = {}
    for report in filled:
        by_tier[report["tier"]] = by_tier.get(report["tier"], 0) + 1
    return {
        "articles": len(reports),
        "candidates": len(candidates),
        "filled": len(filled),
        "flagged": sum(1 for r in filled if r["status"] != "ok"),
        "remaining": len(candidates) - len(filled),
        "by_tier": by_tier,
    }
