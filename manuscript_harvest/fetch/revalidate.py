"""Re-ask "is this the article?" of a corpus that was fetched before anything did.

`validate.identify_fulltext` and `validate.not_research_article` run inside the
tier loop now, so a fresh fetch cannot record `complete` over a document that is
not the requested paper. That does nothing for the articles already on disk: 392
of them were fetched by a version that never asked, and two of those were wrong
in exactly the way the new checks catch. Re-fetching all of them to find out
would cost hundreds of publisher requests and a live proxy session to answer a
question that the bytes in `corpus/` can answer for free.

So this reads what is already there. Same functions, same thresholds, no network:
for each article it re-derives the identity verdict from `fulltext.pdf`,
`fulltext.nxml` and the manifest's own indexed title, and -- only with `apply` --
rewrites the statuses and appends the problem lines the original fetch should
have written.

It never deletes and never downloads. The worst it can do is move an article from
`complete` to `failed`, which is the direction that makes a corpus more honest.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from . import store
from .validate import (
    IDENTITY_FAILURES,
    cited_dois,
    identify_fulltext,
    identity_problem,
    jats_article_type,
    jats_sample_text,
    not_research_article,
    pdf_sample_text,
)

#: Statuses `fulltext.status` may already hold that this pass will not touch.
#: A file that never arrived cannot be misidentified, and re-labelling `paywalled`
#: as `identity_unverified` would replace a true diagnosis with a vacuous one.
_ONLY_IF_PRESENT = store.PDF_USABLE | set(IDENTITY_FAILURES)


def _read(directory: Path, entry: Optional[dict]) -> Optional[bytes]:
    relative = (entry or {}).get("path")
    if not relative:
        return None
    path = directory / relative
    if not path.exists():
        return None
    return path.read_bytes()


def revalidate_article(directory) -> dict:
    """Re-derive one article's identity verdict from the files on disk.

    Returns a report; nothing is written. `verdict` is the `fulltext.status` this
    article should have, `changed` says whether that differs from what it has.
    """
    directory = Path(directory)
    record = store.read_manifest(directory)
    report = {"slug": directory.name, "doi": None, "changed": False,
              "before": None, "verdict": None, "problems": [], "note": None}
    if record is None:
        report["note"] = "no manifest"
        return report

    doi = record.get("doi") or ""
    title = (record.get("identifiers") or {}).get("title") or ""
    before = (record.get("fulltext") or {}).get("status")
    report.update({"doi": doi, "before": before, "verdict": before})

    if record.get("status") == "evicted":
        report["note"] = "evicted: the bytes are gone and the record stands"
        return report

    pdf = _read(directory, record.get("fulltext"))
    xml = _read(directory, record.get("fulltext_xml"))
    if pdf is None and xml is None:
        report["note"] = "no full text on disk to identify"
        return report

    problems: List[str] = []
    verdict = before
    xml_verdict = (record.get("fulltext_xml") or {}).get("status")

    # The notice check first, because it is the one that a passing identity check
    # cannot see: a correction notice carries its own DOI and its own title.
    identifiers = record.get("identifiers") or {}
    notice = not_research_article(title=title,
                                 pub_types=identifiers.get("pub_types") or ())
    if xml is not None:
        notice = notice or not_research_article(
            article_type=jats_article_type(xml))
    if notice is not None:
        # `corrects_doi` is only in manifests written after it existed, so fall
        # back to reading the notice's own text -- hedged, because that is a guess.
        corrects = identifiers.get("corrects_doi")
        if corrects:
            notice += (f"; it is a notice about {corrects}, which is the DOI to "
                       f"fetch instead")
        else:
            named = cited_dois(jats_sample_text(xml) if xml is not None
                               else pdf_sample_text(pdf), exclude=doi, limit=1)
            if named:
                notice += (f"; the notice names {named[0]}, which is probably the "
                           f"DOI to fetch instead")
        problems.append(notice)
        verdict = "not_research_article"
        xml_verdict = "not_research_article" if xml is not None else xml_verdict
    else:
        if pdf is not None and before in _ONLY_IF_PRESENT:
            ok, meta = identify_fulltext(pdf_sample_text(pdf), doi, title)
            if not ok:
                problems.append(identity_problem("PDF", doi, title, meta))
                verdict = "identity_unverified"
        if xml is not None:
            ok, meta = identify_fulltext(jats_sample_text(xml), doi, title)
            xml_verdict = "ok" if ok else "identity_unverified"
            if not ok:
                problems.append(identity_problem("JATS XML", doi, title, meta))

    report["verdict"] = verdict
    report["xml_verdict"] = xml_verdict
    report["problems"] = problems
    # A *verdict* change, not a key appearing. Manifests written before this
    # existed carry no `status` on `fulltext_xml` at all, so comparing raw values
    # called all 374 of them "corrected" on the first run -- 372 of which were
    # `ok -> ok` with an added key. Filling a blank is not a correction.
    xml_before = (record.get("fulltext_xml") or {}).get("status")
    report["changed"] = verdict != before or (
        xml_verdict != xml_before
        and (xml_verdict in IDENTITY_FAILURES or xml_before in IDENTITY_FAILURES
             or xml_verdict == "not_research_article"))
    return report


def apply_report(directory, report: dict) -> dict:
    """Write a `revalidate_article` verdict into the manifest. Returns the record.

    Problem lines are appended only if absent, so running this twice does not say
    the same thing twice. `status` is re-derived through `store.finalize_status`
    rather than set here, so the top-level word stays the one function's answer.
    """
    directory = Path(directory)
    record = store.read_manifest(directory)
    if record is None or not report.get("changed"):
        return record or {}

    fulltext = record.get("fulltext") or {}
    if fulltext.get("path"):
        fulltext["status"] = report["verdict"]
        record["fulltext"] = fulltext
    xml_entry = record.get("fulltext_xml")
    if isinstance(xml_entry, dict) and report.get("xml_verdict"):
        xml_entry["status"] = report["xml_verdict"]

    existing = record.get("problems") or []
    for line in report["problems"]:
        if line not in existing:
            existing.append(line)
    record["problems"] = existing
    # A record of when this pass ran and what it was, so a reader can tell a
    # corrected manifest from one that was right the first time.
    record["revalidated"] = {
        "at": datetime.now(timezone.utc).isoformat(),
        "was": report["before"],
        "now": report["verdict"],
    }
    store.finalize_status(record)
    store.write_manifest(directory, record)
    return record


def revalidate_corpus(corpus_dir, apply: bool = False, slugs=None) -> List[dict]:
    """Re-check every article in `corpus_dir` (or just `slugs`), oldest first."""
    root = Path(corpus_dir).expanduser()
    if not root.exists():
        return []
    wanted = set(slugs or ())
    reports = []
    for directory in sorted(p for p in root.iterdir() if p.is_dir()):
        if wanted and directory.name not in wanted:
            continue
        report = revalidate_article(directory)
        if apply and report["changed"]:
            record = apply_report(directory, report)
            report["status"] = record.get("status")
        reports.append(report)
    return reports
