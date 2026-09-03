"""Tissue pack: which mechanism accounts for a paper moving."""

from __future__ import annotations

import re

from pe.pack import PackError, tables

_T = tables()
_CHG, _DEC = _T["change"], _T["decide"]

ORDER = list(_CHG["order"])
CLASS_LABELS = dict(_CHG["classes"])
UNEXPLAINED = str(_CHG["unexplained_class"])
#: The class the HARNESS assigns, when a paper also disagrees with itself across
#: two runs of the baseline. The harness computes the noise floor, so it owns the
#: concept -- but the label is rendered from this table, so the name has to come
#: from here or a pack omitting it would have papers counted into a class that is
#: never printed.
NOISE_CLASS = str(_CHG["noise_class"])
PRIMARY_FIELD = _T["record"]["primary_field"]
PRIMARY_FIELD_GLOSS = f"`{PRIMARY_FIELD}`"

#: Printed under the confusion matrix. The caveat worth giving a reader depends
#: on the question, so it is the pack's rather than the harness's.
DIFF_PREAMBLE = [
    "A tissue moving between `methods` and `inferred` changes the answer without",
    "changing the tissue, so read STATED-WHERE-CHANGED before TISSUE-SET-CHANGED.",
]
_INPUTS = dict(_DEC["inputs"])
_MATCH = _CHG["match"]
_STOPWORDS = frozenset(str(w) for w in _MATCH["stopwords"])


def determination_inputs(record: dict) -> dict:
    out = {}
    for name, path in _INPUTS.items():
        if "[]." in path:
            array, field = path.split("[].", 1)
            out[name] = sorted(str(t.get(field))
                               for t in (record.get(array) or [])
                               if isinstance(t, dict))
        else:
            out[name] = record.get(path)
    return out


def _tokens(value) -> set[str]:
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9\-']{2,}", str(value or "").lower())
    out = set()
    for w in words:
        out.add(w)
        if "-" in w:
            out.update(p for p in w.split("-") if len(p) >= 3)
    return {w for w in out if w not in _STOPWORDS}


def classify(new: dict, old: dict | None = None) -> list[str]:
    v = new.get("validation") or {}
    classes = []
    old_capped = bool(((old or {}).get("validation") or {}).get("stage_b_capped"))
    if v.get("stage_b_capped"):
        classes.append("STAGE-B")
    elif old_capped:
        classes.append("STAGE-B-RELEASED")
    if v.get("determination_changed_by_harness"):
        classes.append("HARNESS-PRUNE")
    if old is not None:
        before, after = determination_inputs(old), determination_inputs(new)
        if before["paired"] != after["paired"]:
            classes.append("TISSUE-SET-CHANGED")
        if before["stated"] != after["stated"]:
            classes.append("STATED-WHERE-CHANGED")
        if before["has_sequencing_assay"] != after["has_sequencing_assay"]:
            classes.append("ASSAY-CHANGED")
    return classes or [UNEXPLAINED]


def render_paper(doi, old, new, entry, classes) -> list[str]:
    v = new.get("validation") or {}
    lines = ["", f"{doi}:  {old.get(PRIMARY_FIELD)}  ->  {new.get(PRIMARY_FIELD)}"
                 f"   [{', '.join(classes)}]"]
    lines.append(f"  sources={'|'.join(entry.get('source_ids') or [])}  "
                 f"processing={new.get('processing_status')}  "
                 f"completeness={new.get('text_completeness')}")
    lines.append(f"  strongest={v.get('strongest_statement')}  "
                 f"tissues={'|'.join(v.get('tissue_names') or []) or '(none)'}")
    for tissue in old.get("tissues") or []:
        names_new = {str(t.get("name")) for t in (new.get("tissues") or [])}
        if str(tissue.get("name")) not in names_new:
            lines.append(f"    DROPPED vs baseline: {str(tissue.get('name'))[:60]} "
                         f"(was {tissue.get('is_sequenced')})")
    for i, tissue in enumerate(new.get("tissues") or []):
        lines.append(f"    [{i}] {str(tissue.get('name'))[:50]}  "
                     f"sequenced={tissue.get('is_sequenced')}  "
                     f"where={tissue.get('stated_where')}")
    before, after = determination_inputs(old), determination_inputs(new)
    moved = [k for k in before if before[k] != after[k]]
    if moved:
        lines.append("  determination inputs that moved:")
        for key in moved:
            lines.append(f"    {key}: {before[key]!r} -> {after[key]!r}")
    else:
        lines.append("  determination inputs are IDENTICAL — a logic difference.")
    return lines


def render_unchanged(doi, new, entry) -> str:
    v = new.get("validation") or {}
    supp = "supp" if len(entry.get("source_ids") or []) > 1 else "main"
    return (f"  {doi:34} {str(new.get(PRIMARY_FIELD)):<8} "
            f"{'|'.join(v.get('tissue_names') or []) or '(none)':<40} [{supp}]")


_probe = determination_inputs({})
if set(_probe) != set(_INPUTS):
    raise PackError(f"change.py computes {sorted(_probe)} but decide.yaml declares "
                    f"{sorted(_INPUTS)}")
