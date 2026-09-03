"""TABLE 3's other half: the six review screens, and the keyword banks they grep.

Moved out of `pe/audit.py`, which was 80% this task by line -- the worst ratio in
the tree, and the most mechanical to fix, because every screen has the same
shape (select papers by a predicate, optionally grep the text, render some
fields) and only the fields differ. `screen()` and `_compile()` below were
already fully generic and parameterised, so the file already knew where the
boundary was; `main()` just did not respect it.

The tell that the screens were on the wrong side: a tier number went stale
INSIDE a screen header (Screen D said "priority 3" for four versions after the
v0.0.10 renumber) because the header was prose rather than a reference.

Titles, blurbs and the keyword banks are `report.yaml`. What `pe/audit.py` keeps:
reading a run, refusing to report on an empty set, recovering the paper text
safely, and writing the file.

Screens, not verdicts. A keyword hit is not proof of an error: "treated with"
also describes routine processing, and "single-cell suspension" is a dissociation
step rather than an assay -- the two traps this task turns on.
"""

from __future__ import annotations

import re

from pe.pack import tables
from task.rules import RULES_UNDER_REVIEW

_REP = tables()["report"]

#: Indicative, never decisive. A hit means "a human should look", not "the model
#: was wrong".
PERTURBATION_SIGNALS: dict[str, list[str]] = dict(_REP["signals"]["perturbation"])
ASSAY_SIGNALS: dict[str, list[str]] = dict(_REP["signals"]["assay"])

#: The wording trap: a dissociation step, not an assay. Counted separately by
#: Screen B rather than left to read as an assay hit.
SUSPENSION_TRAP = re.compile(_REP["traps"]["suspension"], re.IGNORECASE)

#: Titles, blurbs and empty-notes, keyed by screen id, so a heading cannot go
#: stale the way Screen D's tier number did.
SCREENS = {str(s["id"]): s for s in _REP["screens"]}

#: The caveat printed under the screen summary.
FOOTER = list(_REP["footer"])

def _compile(groups: dict[str, list[str]]) -> dict[str, list[re.Pattern]]:
    return {g: [re.compile(p, re.IGNORECASE) for p in pats] for g, pats in groups.items()}


#: Every keyword bank, keyed by its name in `report.yaml: signals`. A screen
#: names the bank it wants with its own `grep` key rather than the module
#: picking one, so adding a seventh screen with a third bank needs no code here.
COMPILED = {str(name): _compile(dict(bank)) for name, bank in _REP["signals"].items()}

COMPILED_PERT = COMPILED["perturbation"]
COMPILED_ASSAY = COMPILED["assay"]


def _bank(sid: str) -> dict[str, list[re.Pattern]]:
    """The compiled keyword bank this screen's `grep` key names."""
    return COMPILED[str(SCREENS[sid]["grep"])]


def _blurb(sid: str) -> list[str]:
    """The screen's explanation of itself, PRINTED from the table not restated.

    These lines existed twice -- here as literals and in `report.yaml` -- which
    is the shape that let Screen D's header say "priority 3" for four versions
    after the renumber. A screen with no blurb (B and C, which lead with their
    keyword hits) contributes no lines.
    """
    text = SCREENS[sid].get("blurb")
    return str(text).rstrip("\n").splitlines() if text else []


def _empty(sid: str) -> list[str]:
    """What to print when a screen selected no papers, indented as a note.

    Never silently nothing: "no papers matched" and "no papers were looked at"
    have to read differently, which is why every screen declares this string.
    """
    text = SCREENS[sid].get("empty")
    return ["", *(f"  {ln}" for ln in str(text).rstrip("\n").splitlines())] if text else []


def screen(text: str, compiled: dict[str, list[re.Pattern]]) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for group, patterns in compiled.items():
        hits: list[tuple[str, str]] = []
        count = 0
        for pattern in patterns:
            matches = list(pattern.finditer(text))
            if not matches:
                continue
            count += len(matches)
            match = matches[0]
            start, end = max(0, match.start() - 90), min(len(text), match.end() + 90)
            hits.append((pattern.pattern, re.sub(r"\s+", " ", text[start:end]).strip()))
        if count:
            found[group] = {"count": count, "examples": hits[:4]}
    return found




def render(loaded, text_for) -> tuple[list[str], dict[str, int]]:
    """Emit all six screens over the loaded records.

    `text_for(prompt_file)` returns `(text, unavailable_note)` -- the harness
    owns recovering the exact bytes the model was shown, and owns the decision
    that a missing prompt file costs one screen rather than the whole run.
    """
    lines: list[str] = []
    counts = {"A": 0, "B": 0, "C": 0, "D": 0, "E": 0, "F": 0}

    # ---- Screen A: assay-pairing disagreements ------------------------------
    lines.append("=" * 78)
    lines.append(f"SCREEN A — {SCREENS['A']['title']}")
    lines.extend(_blurb("A"))
    lines.append("=" * 78)
    for doi, result, _ in loaded:
        if not (result.get("validation") or {}).get("assay_filtered"):
            continue
        counts["A"] += 1
        lines.append("")
        lines.append(f"{doi}")
        lines.append(f"  present={result.get('perturbation_present')} "
                     f"any_assay={result.get('perturbation_present_any_assay')} "
                     f"has_sc_assay={result.get('has_single_cell_assay')} "
                     f"conf={result.get('paper_confidence')}")
        types = result.get("single_cell_assay_types") or []
        if isinstance(types, str):
            types = [types]
        lines.append(f"  sc assays reported: {', '.join(str(t) for t in types) or '(none)'}")
        for i, pert in enumerate(result.get("perturbations") or []):
            lines.append(f"    [{i}] paired={pert.get('single_cell_paired')} "
                         f"conf={pert.get('confidence')} {str(pert.get('agent'))[:58]}")
            lines.append(f"        assay_applied: {str(pert.get('assay_applied') or '(unstated)')[:110]}")
            assay_ev = pert.get("assay_evidence")
            quote = (assay_ev.get("quote") if isinstance(assay_ev, dict) else "") or ""
            src = (assay_ev.get("source_id") if isinstance(assay_ev, dict) else "") or "?"
            check = pert.get("assay_quote_check") or {}
            status = check.get("status")
            mark = "" if not quote.strip() else (
                "  [QUOTE OK]" if status == "verified" else f"  [{(status or 'UNCHECKED').upper()}]")
            shown = f"[{src}] {quote.strip()[:150]}" if quote.strip() else "(none — pairing inferred)"
            lines.append(f"        pairing quote: {shown}{mark}")
            if pert.get("pairing_downgraded_from"):
                lines.append(f"        NOTE: pairing downgraded from "
                             f"{pert['pairing_downgraded_from']!r} — assay_evidence unverifiable")
    if not counts["A"]:
        lines.extend(_empty("A"))

    # ---- Screen B: possible missed single-cell assay ------------------------
    lines.append("")
    lines.append("=" * 78)
    lines.append(f"SCREEN B — {SCREENS['B']['title']}")
    lines.append("=" * 78)
    for doi, result, prompt_file in loaded:
        if result.get("has_single_cell_assay") == "yes":
            continue
        text, unavailable = text_for(prompt_file)
        found = screen(text, _bank("B"))
        traps = len(SUSPENSION_TRAP.findall(text))
        lines.append("")
        lines.append(f"{doi}   has_single_cell_assay={result.get('has_single_cell_assay')} "
                     f"present={result.get('perturbation_present')}")
        if unavailable:
            counts["B"] += 1
            lines.append(f"  NOT SCREENED -- {unavailable}")
        elif not found:
            lines.append("  no qualifying-assay language found — 'no' looks well supported")
        else:
            counts["B"] += 1
            for group, data in sorted(found.items(), key=lambda kv: -kv[1]["count"]):
                lines.append(f"  [{group}] {data['count']} hit(s)")
                for pattern, snippet in data["examples"][:2]:
                    lines.append(f"      /{pattern}/  ...{snippet[:170]}...")
        if traps:
            lines.append(f"  note: {traps} x 'single-cell suspension' — dissociation wording, "
                         f"not an assay -- prompt.md's wording trap)")

    # ---- Screen C: possible missed perturbation -----------------------------
    lines.append("")
    lines.append("=" * 78)
    lines.append(f"SCREEN C — {SCREENS['C']['title']}")
    lines.append("=" * 78)
    for doi, result, prompt_file in loaded:
        if result.get("perturbation_present_any_assay") != "no":
            continue
        text, unavailable = text_for(prompt_file)
        found = screen(text, _bank("C"))
        total = sum(g["count"] for g in found.values())
        lines.append("")
        lines.append(f"{doi}   any_assay=no conf={result.get('paper_confidence')} hits={total}")
        if unavailable:
            counts["C"] += 1
            lines.append(f"  NOT SCREENED -- {unavailable}")
        elif not found:
            lines.append("  no perturbation language found — 'no' looks well supported")
        else:
            counts["C"] += 1
            for group, data in sorted(found.items(), key=lambda kv: -kv[1]["count"])[:4]:
                lines.append(f"  [{group}] {data['count']} hit(s)")
                for pattern, snippet in data["examples"][:1]:
                    lines.append(f"      /{pattern}/  ...{snippet[:170]}...")
        note = (result.get("ambiguities") or "").strip()
        if note:
            lines.append(f"  model's ambiguities note: {note[:300]}")

    # ---- Screen D: Stage-B caps (route to re-fetch, not to reading) ---------
    lines.append("")
    lines.append("=" * 78)
    lines.append(f"SCREEN D — {SCREENS['D']['title']}")
    lines.extend(_blurb("D"))
    lines.append("=" * 78)
    for doi, result, _ in loaded:
        validation = result.get("validation") or {}
        if not validation.get("stage_b_capped"):
            continue
        counts["D"] += 1
        lines.append("")
        lines.append(f"{doi}   Stage A said {validation.get('stage_a')!r} -> reported "
                     f"{result.get('perturbation_present')!r}")
        lines.append(f"  processing_status={result.get('processing_status')} "
                     f"text_completeness={result.get('text_completeness')} "
                     f"reason={result.get('unresolved_reason')}")
        note = (result.get("ambiguities") or "").strip()
        if note:
            lines.append(f"  model's note: {note[:300]}")
    if not counts["D"]:
        lines.extend(_empty("D"))

    # ---- Screen E: the multi-source path (validation loop step 4) -----------
    lines.append("")
    lines.append("=" * 78)
    lines.append(f"SCREEN E — {SCREENS['E']['title']}")
    lines.extend(_blurb("E"))
    lines.append("=" * 78)
    for doi, result, _ in loaded:
        perts = result.get("perturbations") or []
        by_source: dict[str, int] = {}
        supp_only = []
        for i, pert in enumerate(perts):
            sources = {str(q.get("source_id")) for q in (pert.get("evidence_quotes") or [])
                       if isinstance(q, dict)}
            for src in sources:
                by_source[src] = by_source.get(src, 0) + 1
            if sources and all(s.startswith("supp") for s in sources):
                supp_only.append((i, pert, sorted(sources)))
        validation = result.get("validation") or {}
        misattributed = [f for f in (validation.get("evidence_flags") or [])
                         if f in ("EV-WRONG-SOURCE", "CC-7")]
        if not supp_only and not misattributed:
            continue
        counts["E"] += 1
        lines.append("")
        lines.append(f"{doi}   present={result.get('perturbation_present')}  "
                     f"quotes by source: "
                     + (", ".join(f"{k}={v}" for k, v in sorted(by_source.items())) or "none"))
        if misattributed:
            lines.append(f"  ATTRIBUTION FLAGS: {', '.join(misattributed)}")
            for i, pert in enumerate(perts):
                for check in pert.get("quote_checks") or []:
                    if check.get("status") in ("wrong_source", "unknown_source"):
                        lines.append(f"    [{i}] claimed {check.get('claimed_source')!r} "
                                     f"-> actually {check.get('source_id')!r}: "
                                     f"{str(check.get('quote'))[:110]}")
        for i, pert, sources in supp_only:
            lines.append(f"    [{i}] SUPP-ONLY ({'|'.join(sources)}) "
                         f"paired={pert.get('single_cell_paired')} "
                         f"{str(pert.get('agent'))[:52]}")
            for quote in (pert.get("evidence_quotes") or [])[:1]:
                lines.append(f"        {str(quote.get('quote'))[:160]}")
    if not counts["E"]:
        lines.extend(_empty("E"))

    # ---- Screen F: suppressed candidates (v0.0.10) --------------------------
    lines.append("")
    lines.append("=" * 78)
    lines.append(f"SCREEN F — {SCREENS['F']['title']}")
    lines.extend(_blurb("F"))
    lines.append("=" * 78)

    def _supp_sort_key(item):
        doi, result, _ = item
        supp = result.get("suppressed_candidates") or []
        flips = any(s.get("would_have_paired") == "yes"
                    and s.get("rule") in RULES_UNDER_REVIEW for s in supp) and \
            result.get("perturbation_present") != "yes"
        return (0 if flips else 1, doi)

    rule_tally: dict[str, int] = {}
    for doi, result, _ in sorted(loaded, key=_supp_sort_key):
        supp = result.get("suppressed_candidates") or []
        if not supp:
            continue
        counts["F"] += 1
        present = result.get("perturbation_present")
        # Keyed on the rules under review, matching triage P2. A settled toggle
        # pairing "yes" is a fact worth printing but not a call to review, and
        # marking it would contradict the tier it is supposed to explain.
        flips = [s for s in supp if s.get("would_have_paired") == "yes"
                 and s.get("rule") in RULES_UNDER_REVIEW]
        lines.append("")
        marker = ("   >>> WOULD HAVE PAIRED YES — one toggle flips this paper"
                  if flips and present != "yes" else "")
        lines.append(f"{doi}   present={present}  "
                     f"{len(supp)} suppressed{marker}")
        for i, cand in enumerate(supp):
            rule = str(cand.get("rule"))
            rule_tally[rule] = rule_tally.get(rule, 0) + 1
            would = cand.get("would_have_paired")
            if would != "yes":
                flag = ""
            elif rule in RULES_UNDER_REVIEW:
                flag = "  <<< would have paired YES — rule under review"
            else:
                flag = "  <<< would have paired YES (settled toggle)"
            lines.append(f"    [{i}] {rule}  would_have_paired={would}{flag}")
            lines.append(f"        candidate: {str(cand.get('candidate'))[:110]}")
            lines.append(f"        why: {str(cand.get('why') or '(not given)')[:220]}")
            quote = cand.get("evidence_quote")
            if isinstance(quote, dict) and str(quote.get("quote") or "").strip():
                check = cand.get("quote_check") or {}
                mark = ("  [QUOTE OK]" if check.get("status") == "verified"
                        else f"  [{str(check.get('status') or 'UNCHECKED').upper()}]")
                lines.append(f"        quote: [{quote.get('source_id')}] "
                             f"{str(quote.get('quote')).strip()[:150]}{mark}")
            elif cand.get("evidence_quote_dropped"):
                lines.append("        quote: DROPPED as unverifiable — entry kept, "
                             "because the suppression still happened")
            else:
                lines.append("        quote: (none — exclusion rests on the absence "
                             "of a statement)")
    if not counts["F"]:
        lines.extend(_empty("F"))
    else:
        lines.append("")
        lines.append("  suppression rules used: "
                     + ", ".join(f"{k}={v}" for k, v in sorted(rule_tally.items())))
    return lines, counts
