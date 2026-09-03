"""Tissue pack: the ladder, the CSV row, the counters."""

from __future__ import annotations

from collections import Counter

from pe.pack import tables

_REP = tables()["report"]
COLUMNS = list(_REP["columns"])
TIERS = [(int(t["n"]), str(t["label"])) for t in _REP["tiers"]]
LOW_CONFIDENCE_YES = float(_REP["low_confidence_yes"])
_LIMITS = _REP.get("column_limits") or {}
TRIAGED_REASONS = tuple(_REP["triaged_unresolved_reasons"])


def tier_labels():
    return TIERS


def _join_truncated(items, per_item: int = 60, total: int = 400) -> str:
    parts = []
    for item in items:
        text = str(item or "")
        if len(text) > per_item:
            text = text[: per_item - 1] + "…"
        parts.append(text)
    joined = "|".join(parts)
    return joined[: total - 1] + "…" if len(joined) > total else joined


def triage_priority(record: dict) -> int:
    v = record.get("validation") or {}
    present = record.get("tissue_stated")
    reason = record.get("unresolved_reason")
    confidence = record.get("paper_confidence")

    if present == "unclear" and reason == "tissue_not_stated":
        return 1
    if present == "yes" and v.get("inferred_only"):
        return 2
    if present == "yes" and isinstance(confidence, (int, float)) \
            and confidence < LOW_CONFIDENCE_YES:
        return 3
    if present == "unclear" and reason == "degraded_text":
        return 4
    if v.get("multi_tissue"):
        return 5
    untriageable = present == "unclear" and reason not in TRIAGED_REASONS
    if v.get("consistency_flags") or v.get("evidence_flags") or untriageable:
        return 6
    return 9


def row_for(doi: str, record: dict, entry: dict) -> dict:
    v = record.get("validation") or {}
    types = record.get("sequencing_assay_types") or []
    if isinstance(types, str):
        types = [types]
    return {
        "triage_priority": triage_priority(record),
        "paper_id": record.get("paper_id", doi),
        "doi": doi,
        "status": "ok",
        "tissue_stated": record.get("tissue_stated", ""),
        "tissue_stated_model": record.get("tissue_stated_model", ""),
        "unresolved_reason": record.get("unresolved_reason", ""),
        "processing_status": record.get("processing_status", ""),
        "text_completeness": record.get("text_completeness", ""),
        "has_sequencing_assay": record.get("has_sequencing_assay", ""),
        "sequencing_assay_types": _join_truncated(
            types, **_LIMITS.get("sequencing_assay_types", {})),
        "paper_confidence": record.get("paper_confidence", ""),
        "n_tissues": len(record.get("tissues") or []),
        "n_sequenced_yes": v.get("n_sequenced_yes", ""),
        "n_sequenced_no": v.get("n_sequenced_no", ""),
        "n_sequenced_unclear": v.get("n_sequenced_unclear", ""),
        "tissue_names": "|".join(v.get("tissue_names") or []),
        "stated_where": "|".join(v.get("stated_where") or []),
        "strongest_statement": v.get("strongest_statement") or "",
        "stage_b_capped": v.get("stage_b_capped", ""),
        "determination_changed_by_harness": v.get("determination_changed_by_harness", ""),
        "consistency_flags": "|".join(v.get("consistency_flags") or []),
        "evidence_flags": "|".join(v.get("evidence_flags") or []),
        "quotes_checked": v.get("quotes_checked", ""),
        "quotes_failed": v.get("quotes_failed", ""),
        "quotes_wrong_source": v.get("quotes_wrong_source", ""),
        "tissues_dropped": v.get("tissues_dropped", ""),
        "sources": "|".join(entry.get("source_ids") or []),
        "n_issues": len(v.get("issues") or []),
        "chars": entry.get("chars", ""),
        "needs_review": record.get("needs_review", True),
    }


def counters(rows: list[dict], results: dict[str, dict]) -> list[str]:
    ok = [r for r in rows if r["status"] == "ok"]
    validations = [r.get("validation") or {} for r in results.values()]
    versions = sorted({str(v.get("task_version") or "?") for v in validations}) or ["?"]
    packs = sorted({str(v.get("pack_sha256"))[:12] for v in validations
                    if v.get("pack_sha256")})
    out = ["", f"corpus counters — task v{'/'.join(versions)}"
               + (f", pack {'/'.join(packs)}" if packs else "")]
    if len(versions) > 1:
        out.append("  MIXED VERSIONS in one run — not comparable")

    def tally(label, key):
        counts = Counter(str(key(r)) for r in ok)
        out.append(f"  {label:<52} "
                   + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))

    tally("papers by processing_status", lambda r: r["processing_status"])
    tally("papers by tissue_stated", lambda r: r["tissue_stated"])
    tally("papers by has_sequencing_assay", lambda r: r["has_sequencing_assay"])
    tally("strongest statement behind a yes", lambda r: r["strongest_statement"] or "(none)")

    inferred = [r for r in ok if (results.get(r["doi"], {}).get("validation") or {})
                .get("inferred_only")]
    out.append(f"  {'yes carried ONLY by an inference (triage P2)':<52} {len(inferred)}"
               + (f" -> {', '.join(r['doi'] for r in inferred)}" if inferred else ""))
    multi = [r for r in ok if (results.get(r["doi"], {}).get("validation") or {})
             .get("multi_tissue")]
    out.append(f"  {'two or more sequenced tissues (triage P5)':<52} {len(multi)}")
    by_tissue = Counter(t for r in ok for t in (r["tissue_names"] or "").split("|") if t)
    out.append(f"  {'tissues of sequenced material':<52} "
               + (", ".join(f"{k}={v}" for k, v in by_tissue.most_common(12)) or "none"))
    q_checked = sum(int(r["quotes_checked"] or 0) for r in ok)
    q_failed = sum(int(r["quotes_failed"] or 0) for r in ok)
    out.append(f"  {'quotes verified':<52} {q_checked - q_failed}/{q_checked} "
               f"(failed {q_failed})")
    changed = [r for r in ok if r["determination_changed_by_harness"] is True]
    out.append(f"  {'model != final after pruning':<52} {len(changed)}/{len(ok)}")

    out += ["", "triage queue"]
    for priority, label in tier_labels():
        bucket = [r for r in ok if r["triage_priority"] == priority]
        out.append(f"  P{priority}  {label:<62} {len(bucket)}")
    return out
