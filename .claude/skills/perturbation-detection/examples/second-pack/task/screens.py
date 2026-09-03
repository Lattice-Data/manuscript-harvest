"""Tissue pack: four review screens."""

from __future__ import annotations

import re

from pe.pack import tables

_REP = tables()["report"]
SIGNALS: dict[str, list[str]] = dict(_REP["signals"]["tissue"])
SUSPENSION_TRAP = re.compile(_REP["traps"]["suspension"], re.IGNORECASE)
SCREENS = {str(s["id"]): s for s in _REP["screens"]}
FOOTER = list(_REP["footer"])

COMPILED = {g: [re.compile(p, re.IGNORECASE) for p in pats]
            for g, pats in SIGNALS.items()}


def screen(text: str, compiled) -> dict:
    found = {}
    for group, patterns in compiled.items():
        hits, count = [], 0
        for pattern in patterns:
            matches = list(pattern.finditer(text))
            if not matches:
                continue
            count += len(matches)
            m = matches[0]
            start, end = max(0, m.start() - 90), min(len(text), m.end() + 90)
            hits.append((pattern.pattern, re.sub(r"\s+", " ", text[start:end]).strip()))
        if count:
            found[group] = {"count": count, "examples": hits[:4]}
    return found


def render(loaded, text_for) -> tuple[list[str], dict[str, int]]:
    lines: list[str] = []
    counts = {"A": 0, "B": 0, "C": 0, "D": 0}

    def header(sid):
        lines.extend(["", "=" * 78, f"SCREEN {sid} — {SCREENS[sid]['title']}"])
        if SCREENS[sid].get("blurb"):
            lines.append(SCREENS[sid]["blurb"])
        lines.append("=" * 78)

    header("A")
    for doi, record, _ in loaded:
        excluded = [t for t in (record.get("tissues") or [])
                    if t.get("is_sequenced") == "no"]
        if not excluded:
            continue
        counts["A"] += 1
        lines.append("")
        lines.append(f"{doi}   stated={record.get('tissue_stated')}  "
                     f"{len(excluded)} tissue(s) named but not sequenced")
        for t in excluded:
            lines.append(f"    {str(t.get('name'))[:60]}  "
                         f"where={t.get('stated_where')}")
            lines.append(f"      why: {str(t.get('reasoning') or '(none)')[:150]}")
    if not counts["A"]:
        lines.extend(["", f"  {SCREENS['A']['empty']}"])

    header("B")
    for doi, record, prompt_file in loaded:
        if record.get("tissue_stated") == "yes":
            continue
        text, unavailable = text_for(prompt_file)
        found = screen(text, COMPILED)
        traps = len(SUSPENSION_TRAP.findall(text))
        lines.append("")
        lines.append(f"{doi}   stated={record.get('tissue_stated')} "
                     f"assay={record.get('has_sequencing_assay')}")
        if unavailable:
            counts["B"] += 1
            lines.append(f"  NOT SCREENED -- {unavailable}")
        elif not found:
            lines.append("  no tissue language found — the answer looks well supported")
        else:
            counts["B"] += 1
            for group, data in sorted(found.items(), key=lambda kv: -kv[1]["count"]):
                lines.append(f"  [{group}] {data['count']} hit(s)")
                for pattern, snippet in data["examples"][:2]:
                    lines.append(f"      /{pattern}/  ...{snippet[:170]}...")
        if traps:
            lines.append(f"  note: {traps} x preparation wording — the tissue is what "
                         f"the suspension was made FROM")

    header("C")
    for doi, record, _ in loaded:
        if not (record.get("validation") or {}).get("inferred_only"):
            continue
        counts["C"] += 1
        lines.append("")
        lines.append(f"{doi}   stated={record.get('tissue_stated')}  "
                     f"conf={record.get('paper_confidence')}")
        for t in record.get("tissues") or []:
            if t.get("is_sequenced") == "yes":
                lines.append(f"    INFERRED: {str(t.get('name'))[:60]}")
                lines.append(f"      {str(t.get('reasoning') or '')[:180]}")
    if not counts["C"]:
        lines.extend(["", f"  {SCREENS['C']['empty']}"])

    header("D")
    for doi, record, _ in loaded:
        v = record.get("validation") or {}
        if not v.get("stage_b_capped"):
            continue
        counts["D"] += 1
        lines.append("")
        lines.append(f"{doi}   Stage A said {v.get('stage_a')!r} -> reported "
                     f"{record.get('tissue_stated')!r}")
        lines.append(f"  processing_status={record.get('processing_status')} "
                     f"text_completeness={record.get('text_completeness')}")
    if not counts["D"]:
        lines.extend(["", f"  {SCREENS['D']['empty']}"])

    return lines, counts
