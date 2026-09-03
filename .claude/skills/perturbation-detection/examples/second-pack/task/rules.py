"""Tissue-of-the-sequenced-material: the predicates a table cannot express."""

from __future__ import annotations

from pe.pack import tables

_T = tables()
_REC, _DEC = _T["record"], _T["decide"]

LABELS = tuple(_REC["labels"])
TRISTATE = LABELS
_ITEMS = _REC["item_array"]
_ITEM_PATH = _ITEMS["path"]
_ITEM_LABEL = _ITEMS["label_field"]
STATED_WHERE = tuple(_ITEMS["enums"]["stated_where"])
#: The values that count as the paper actually SAYING it, as opposed to us
#: working it out. The whole question turns on this line.
STATED_EXPLICITLY = tuple(w for w in STATED_WHERE if w != "inferred")
CC_TEXT = dict(_DEC["checks"])
_CAP = _DEC["cap"]
_DOWNGRADE = _REC["downgrade_confidence"]


def _items(record: dict) -> list[dict]:
    return [t for t in (record.get(_ITEM_PATH) or []) if isinstance(t, dict)]


def stage_a(record: dict) -> str | None:
    """Evidence-based determination. A different table from the other pack's."""
    status = record.get("processing_status")
    has_assay = record.get("has_sequencing_assay")
    items = _items(record)
    paired = [t.get(_ITEM_LABEL) for t in items]

    if status == "failed":
        return "unclear"
    if has_assay not in TRISTATE:
        return None
    if not items:
        return "no" if has_assay == "yes" else "unclear"
    if has_assay == "no":
        return "no"
    # A stated tissue paired to sequenced material is the answer.
    if any(t.get(_ITEM_LABEL) == "yes"
           and t.get("stated_where") in STATED_EXPLICITLY for t in items):
        return "yes"
    # A `yes` pairing carried only by an inference is not a stated tissue.
    if "yes" in paired or "unclear" in paired or has_assay == "unclear":
        return "unclear"
    return "no"


def stage_b(stage_a_result, processing_status, text_completeness):
    degraded = (processing_status == _CAP["when_status"]
                or text_completeness != _CAP["when_completeness_not"])
    if degraded and stage_a_result == _CAP["from"]:
        return _CAP["to"], True
    return stage_a_result, False


def decide(record: dict):
    a = stage_a(record)
    if a is None:
        return None, None, False
    final, capped = stage_b(a, record.get("processing_status"),
                            record.get("text_completeness"))
    return final, a, capped


def checks(record: dict) -> list[str]:
    codes = []
    items = _items(record)
    paired = [t.get(_ITEM_LABEL) for t in items]
    if record.get("has_sequencing_assay") == "no" and "yes" in paired:
        codes.append("TC-1")
    if record.get("tissue_stated") == "yes" and not items:
        codes.append("TC-2")
    if any(p not in TRISTATE for p in paired):
        codes.append("TC-3")
    return codes


def extra_field_issues(record: dict) -> list[str]:
    if record.get("has_sequencing_assay") == "yes" \
            and not record.get("sequencing_assay_types"):
        return ["has_sequencing_assay='yes' but sequencing_assay_types is empty"]
    return []


def validate_items(record: dict, verify, issues: list[str], flags: set[str]) -> dict:
    """Verify each tissue's quotes, prune the unverifiable."""
    raw = record.get(_ITEM_PATH) or []
    if not isinstance(raw, list):
        issues.append(f"{_ITEM_PATH} is not a list")
        raw = []

    checked = failed = wrong_source = 0
    kept, dropped, index_map = [], [], {}

    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            issues.append(f"tissues[{i}] is not an object")
            continue
        if item.get("stated_where") not in STATED_WHERE:
            issues.append(f"tissues[{i}].stated_where="
                          f"{item.get('stated_where')!r} off-schema")
        paired = item.get(_ITEM_LABEL)
        if paired not in TRISTATE:
            issues.append(f"tissues[{i}].is_sequenced={paired!r} not in yes/no/unclear")
        if not str(item.get("name") or "").strip():
            issues.append(f"tissues[{i}].name is empty")

        raw_quotes = item.get("evidence_quotes") or []
        if isinstance(raw_quotes, (str, dict)):
            raw_quotes = [raw_quotes]
        verified, quote_checks = [], []
        for entry in raw_quotes:
            if isinstance(entry, dict):
                source_id = str(entry.get("source_id") or "main")
                quote = str(entry.get("quote") or "")
            else:
                issues.append(f"tissues[{i}] evidence quote is a bare string")
                source_id, quote = "main", str(entry or "")
            outcome = verify(quote, source_id)
            checked += 1
            quote_checks.append({"claimed_source": source_id, "quote": quote, **outcome})
            if outcome["status"] == "verified":
                verified.append({"source_id": source_id, "quote": quote})
            elif outcome["status"] in ("wrong_source", "unknown_source"):
                wrong_source += 1
                flags.add("EV-WRONG-SOURCE")
                verified.append({"source_id": outcome["source_id"], "quote": quote,
                                 "source_id_corrected_from": source_id})
                issues.append(f"tissues[{i}] quote attributed to {source_id!r} but "
                              f"found in {outcome['source_id']!r} (EV-WRONG-SOURCE)")
            else:
                failed += 1
                flags.add("EV-UNVERIFIED")
                issues.append(f"tissues[{i}] quote unverifiable in any source "
                              f"(best ratio {outcome['ratio']}) — dropped")
        item["quote_checks"] = quote_checks
        item["evidence_quotes"] = verified
        item["quotes_validated"] = bool(verified)

        pairing_ev = item.get("sequenced_evidence")
        if isinstance(pairing_ev, dict) and str(pairing_ev.get("quote") or "").strip():
            outcome = verify(str(pairing_ev.get("quote")),
                             str(pairing_ev.get("source_id") or "main"))
            checked += 1
            item["sequenced_quote_check"] = outcome
            if outcome["status"] == "unverified":
                failed += 1
                if paired in ("yes", "no"):
                    item[_ITEM_LABEL] = "unclear"
                    item["pairing_downgraded_from"] = paired
                    paired = "unclear"
                    flags.add("EV-PAIRING-DOWNGRADED")
                    issues.append(f"tissues[{i}].sequenced_evidence unverifiable — "
                                  f"pairing downgraded to 'unclear'")
        elif paired in ("yes", "no"):
            issues.append(f"tissues[{i}].is_sequenced={paired!r} asserted with no "
                          f"sequenced_evidence (pairing is inferred, not quoted)")

        if not verified:
            flags.add("EV-ITEM-DROPPED")
            issues.append(f"tissues[{i}] ({item.get('name')!r}) DROPPED: no evidence "
                          f"quote could be verified against any source")
            item["dropped_reason"] = "no verifiable evidence quote"
            item["confidence_original"] = item.get("confidence")
            item["confidence"] = _DOWNGRADE
            dropped.append(item)
        else:
            index_map[i] = len(kept)
            kept.append(item)

    record[_ITEM_PATH] = kept
    if dropped:
        record[f"{_ITEM_PATH}_dropped"] = dropped
    return {"kept": kept, "dropped": dropped, "index_map": index_map,
            "checked": checked, "failed": failed, "wrong_source": wrong_source}


def validate_secondary(record, verify, issues, flags):
    """This task has no considered-and-rejected array: `is_sequenced: "no"` on
    the item itself already records the exclusion, so a second array would be a
    second way to say the same thing."""
    return [], 0, 0, 0


def metrics(record: dict, ctx: dict) -> dict:
    kept = ctx["kept"]
    paired = [t.get(_ITEM_LABEL) for t in kept]
    yes = [t for t in kept if t.get(_ITEM_LABEL) == "yes"]
    where = [t.get("stated_where") for t in yes]
    strongest = next((w for w in STATED_WHERE if w in where), None)
    return {
        "n_sequenced_yes": paired.count("yes"),
        "n_sequenced_no": paired.count("no"),
        "n_sequenced_unclear": paired.count("unclear"),
        "tissue_names": sorted({str(t.get("name")) for t in yes if t.get("name")}),
        "stated_where": sorted({str(w) for w in where if w}),
        # The single most explicit statement behind a `yes`. The point of the task.
        "strongest_statement": strongest,
        "inferred_only": bool(yes) and all(w == "inferred" for w in where),
        "multi_tissue": len(yes) > 1,
    }


def progress_line(doi: str, record: dict) -> str:
    v = record.get("validation") or {}
    flags = ""
    if v[f"{_ITEM_PATH}_dropped"]:
        flags += f"  DROPPED={v[f'{_ITEM_PATH}_dropped']}"
    if v["stage_b_capped"]:
        flags += "  STAGE-B-CAP"
    if v["inferred_only"]:
        flags += "  INFERRED-ONLY"
    if v["consistency_flags"]:
        flags += "  " + ",".join(v["consistency_flags"])
    if v["evidence_flags"]:
        flags += "  " + ",".join(v["evidence_flags"])
    return (f"  {doi:38} {str(record.get('processing_status','?')):8}"
            f"{str(record.get('text_completeness','?')):16}"
            f"seq={str(record.get('has_sequencing_assay','?')):8}"
            f"{str(record.get('tissue_stated','?')):8}"
            f"where={str(v.get('strongest_statement')):20}"
            f"tissues={len(v.get('tissue_names') or []):<3}"
            f"q={v['quotes_checked'] - v['quotes_failed']}/{v['quotes_checked']}{flags}")
