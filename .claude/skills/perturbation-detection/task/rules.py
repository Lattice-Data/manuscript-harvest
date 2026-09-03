"""The predicates a lookup table cannot express, for this task and no other.

Everything here was a module constant or a function in `pe/`, where a reader had
no way to tell the harness's own machinery from this task's judgment. `pe/` now
holds no perturbation vocabulary at all; it calls into this module without
knowing what any of it means.

**Why these are functions and not more YAML.** prompt.md publishes Stage A as a
10-row truth table with wildcards, so expressing it as rows plus a small matcher
is possible, and it was the first thing tried. A table needs a predicate
vocabulary; that vocabulary is a guess until a second task has a decide step to
draw it from; and the recorded decision of 2026-08-31 is explicit that `stage_a`
"is the piece expected to be shaped differently per question, so the seam is a
guess until two real cases exist". Inventing an expression language for one
instance buys a generality nobody can check, at the cost of a pack that is
harder to write and a harness that is harder to trust. So: lists and messages are
data (`record.yaml`, `decide.yaml`, `report.yaml`, `change.yaml`), rules are
three or four functions here, and if a third pack's functions turn out to share a
shape, that is when to lift them into rows -- with two real cases to generalise
from instead of one.

The interface `pe/` relies on, and all a second pack must supply:

    decide(record)                        -> (label, stage_label, capped)
    checks(record)                        -> [code, ...]
    metrics(record, ctx)                  -> ordered dict of task counters
    validate_secondary(record, verify, issues, flags)
                                          -> (entries, checked, failed, wrong)
"""

from __future__ import annotations

from pe.pack import tables

_T = tables()
_REC = _T["record"]
_DEC = _T["decide"]

#: The classification's own values, and the enum three fields are checked against.
LABELS = tuple(_REC["labels"])
#: Kept under their historical names because the tests and prompt.md use them.
TRISTATE = LABELS
PROCESSING_STATUS = tuple(_REC["run_states"]["processing_status"])
TEXT_COMPLETENESS = tuple(_REC["run_states"]["text_completeness"])
UNRESOLVED_REASONS = tuple(_REC["unresolved_reasons"])

# The record's shape, READ rather than restated. These names were hardcoded here
# while `record.yaml` declared them beside this file, which is documentation
# pretending to be configuration -- the same rot that left five `config.yaml`
# keys and `spec.read_back_marker` dead. A pack talking to its own rule modules
# fails loudly rather than silently, which is why this was a lower-priority
# finding than the harness contract, not why it was acceptable.
_ITEMS = _REC["item_array"]
_ITEM_PATH = _ITEMS["path"]
_ITEM_LABEL = _ITEMS["label_field"]
_ITEM_NAME = _ITEMS["name_field"]
_ITEM_QUOTES = _ITEMS["quotes_field"]
_ITEM_QUOTE_2 = _ITEMS["secondary_quote_field"]
#: Which sub-field a failed secondary quote downgrades. The same field as
#: `label_field` in this pack; a pack could name a different one.
_ITEM_QUOTE_2_DOWNGRADES = _ITEMS["secondary_downgrades"]
_ITEM_DROP_UNQUOTED = bool(_ITEMS["drop_when_no_verified_quote"])
#: Fields checked for TYPE and never for value: a closed set would have to
#: enumerate every model organism in advance.
_ITEM_OPEN_FIELDS = dict(_ITEMS.get("open_fields") or {})
CATEGORIES = set(_ITEMS["enums"]["category"])

_REFS = _REC["ref_arrays"][0]
_REF_PATH = _REFS["path"]
_REF_FIELD = _REFS["ref_field"]
_REF_POINTS_AT = _REFS["points_at"]
_REF_TRISTATE_BOOLS = tuple(_REFS["tristate_bool_fields"])
_REF_OPEN_FIELDS = dict(_REFS.get("open_fields") or {})

_SUPP = _REC["secondary_arrays"][0]
_SUPP_PATH = _SUPP["path"]
_SUPP_NAME = _SUPP["name_field"]
_SUPP_REASON = _SUPP["reason_field"]
_SUPP_QUOTE = _SUPP["quote_field"]
_SUPP_PAIRING = _SUPP["pairing_field"]
_SUPP_KEEP_ON_QUOTE_FAILURE = bool(_SUPP["keep_entry_on_quote_failure"])
_SUPP_QUOTE_OPTIONAL = bool(_SUPP["quote_optional"])
_SUPP_UNVERIFIED_FLAG = _SUPP["unverified_flag"]
SUPPRESSION_RULES = tuple(_SUPP["reasons"])
RULES_UNDER_REVIEW = tuple(_SUPP["reasons_under_review"])

HUMAN_SYNONYMS = frozenset(str(s) for s in _REC["normalisers"]["human"]["synonyms"])

# Reindexing a ref array onto a pruned item array only means anything if the two
# are the same array. Declared in the table, so it is checked here rather than
# assumed: a pack that mismatches them gets a messaged failure at load instead of
# refs silently remapped onto the wrong list.
if _REF_POINTS_AT != _ITEM_PATH:
    raise ValueError(
        f"record.yaml: ref_arrays[0].points_at is {_REF_POINTS_AT!r} but the pruned "
        f"item_array is {_ITEM_PATH!r}; references would be remapped onto a different "
        f"array than the one being pruned")


CC_TEXT = dict(_DEC["checks"])
_CAP = _DEC["cap"]
_DOWNGRADE = _REC["downgrade_confidence"]


def _open_field_issues(obj: dict, prefix: str, declared: dict) -> list[str]:
    """Type-check the fields the table declares OPEN, never their values.

    A closed set would have to enumerate every model organism in advance, and
    killifish is the case that breaks such a list -- so an unrecognised species
    is not an error and a non-string, non-null value is. Driven off
    `open_fields` rather than one `if` per field, which is how `paired_organism`
    came to be checked while the declaration sat unread beside it.
    """
    out: list[str] = []
    for name, kind in declared.items():
        value = obj.get(name)
        if kind != "string_or_null":
            out.append(f"{prefix}.{name} declares open-field kind {kind!r}, which this "
                       f"pack's rules do not know how to check")
        elif not (value is None or isinstance(value, str)):
            out.append(f"{prefix}.{name}={value!r} must be a string or null")
    return out


# ---------------------------------------------------------------------------
# Open values, normalised for counting only
# ---------------------------------------------------------------------------

def normalise_organism(value) -> str | None:
    """Lowercased, stripped organism string; None for absent/blank/non-string."""
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.strip().lower().split())
    return cleaned or None


def is_human(value) -> bool:
    """Whether a recorded organism denotes human. Unknown is NOT human."""
    return normalise_organism(value) in HUMAN_SYNONYMS


def _paired(record: dict) -> list:
    return [p.get(_ITEM_LABEL) for p in (record.get(_ITEM_PATH) or [])
            if isinstance(p, dict)]


# ---------------------------------------------------------------------------
# The determination
# ---------------------------------------------------------------------------

def stage_a(result: dict) -> str | None:
    """prompt.md Stage A: evidence-based determination, ordered A0-A6.

    Returns the implied `perturbation_present`, or None if a required input is
    missing or off-enum. The numbered comments map 1:1 onto the prompt's rules.
    """
    status = result.get("processing_status")
    has_sc = result.get("has_single_cell_assay")
    any_assay = result.get("perturbation_present_any_assay")
    perts = [p for p in (result.get(_ITEM_PATH) or []) if isinstance(p, dict)]
    paired = [p.get(_ITEM_LABEL) for p in perts]

    # A0. Nothing was assessed.
    if status == "failed":
        return "unclear"
    if any_assay not in TRISTATE or has_sc not in TRISTATE:
        return None

    # A1. Empty perturbations array -- an explicit terminal rule since v0.0.5,
    # rather than v0.0.4's resolution by vacuous truth through the paired list.
    if not perts:
        if any_assay == "no":
            return "no"
        if any_assay == "unclear":
            return "unclear"
        return "unclear"  # any_assay == "yes" is CC-2; stated default.

    # A2. No qualifying assay -> nothing to pair to.
    if has_sc == "no":
        return "no"
    # A3. An unconfirmed assay caps the paper-level call at "unclear".
    if has_sc == "unclear":
        return "unclear" if any(x in ("yes", "unclear") for x in paired) else "no"
    # A4. One confirmed pairing is sufficient.
    if "yes" in paired:
        return "yes"
    # A5. A single unresolved pairing is enough, however many "no"s accompany it.
    if "unclear" in paired:
        return "unclear"
    # A6. Every pairing resolved to "no".
    return "no"


def stage_b(stage_a_result: str | None, processing_status: str,
            text_completeness: str) -> tuple[str | None, bool]:
    """prompt.md Stage B: cap a negative drawn from degraded text.

    Returns (determination, capped). The asymmetry is deliberate: missing text
    can hide the sentence that would have paired a perturbation to a single-cell
    assay, but it cannot invent one, so only "no" is capped.

    `decide.yaml: cap.when_completeness_not` is "full", meaning ANYTHING other
    than "full" -- including a malformed or absent value. The guard here used to
    test membership of the legal enum first, so None, "", "Full" and "truncated "
    all escaped the cap and kept the "no" while an honest "unknown" was capped. A
    safety mechanism a typo switches off is not one.
    """
    degraded = (processing_status == _CAP["when_status"]
                or text_completeness != _CAP["when_completeness_not"])
    if degraded and stage_a_result == _CAP["from"]:
        return _CAP["to"], True
    return stage_a_result, False


def decide(result: dict) -> tuple[str | None, str | None, bool]:
    """The harness's entry point: (final label, stage-A label, capped).

    Two values rather than one because the record keeps both -- a curator reading
    a capped paper needs to see what the evidence alone said.
    """
    a = stage_a(result)
    if a is None:
        return None, None, False
    final, capped = stage_b(a, result.get("processing_status"),
                            result.get("text_completeness"))
    return final, a, capped


def expected_determination(result: dict) -> str | None:
    """Stage A then Stage B, as the harness applies them."""
    return decide(result)[0]


def checks(result: dict) -> list[str]:
    """CC-1 .. CC-6. CC-7 is raised by the harness during quote checking."""
    codes: list[str] = []
    has_sc = result.get("has_single_cell_assay")
    any_assay = result.get("perturbation_present_any_assay")
    perts = [p for p in (result.get(_ITEM_PATH) or []) if isinstance(p, dict)]
    paired = [p.get(_ITEM_LABEL) for p in perts]

    if has_sc == "no" and "yes" in paired:
        codes.append("CC-1")
    if any_assay == "yes" and not perts:
        codes.append("CC-2")
    if any_assay == "no" and perts:
        codes.append("CC-3")
    if any(p not in TRISTATE for p in paired):
        codes.append("CC-4")
    if has_sc == "unclear" and "yes" in paired:
        codes.append("CC-5")
    if result.get("processing_status") == "failed" and perts:
        codes.append("CC-6")
    return codes


#: Kept under the old name for the tests that encode the prompt's truth table.
consistency_checks = checks


def _normalize_quote_entry(entry, default_source: str = "main") -> tuple[str, str, bool]:
    """Return (source_id, quote, was_legacy_string).

    v0.0.4 emitted bare strings; v0.0.5 requires {"source_id", "quote"}. A bare
    string is accepted so an off-schema response is flagged rather than crashing
    the stage, but it is reported.
    """
    if isinstance(entry, dict):
        return str(entry.get("source_id") or default_source), str(entry.get("quote") or ""), False
    return default_source, str(entry or ""), True


# ---------------------------------------------------------------------------
# The non-determinative array
# ---------------------------------------------------------------------------

def validate_secondary(result: dict, verify, issues: list[str],
                       flags: set[str]) -> tuple[list, int, int, int]:
    """Verify and normalize `suppressed_candidates`. Returns (entries, n, fail, wrong).

    `verify(quote, claimed_source)` is the harness's quote check, passed in so the
    matching logic stays in `pe/` and only the field names and the wording live
    here.

    **This function cannot move the determination, and that is structural rather
    than a convention to be careful about.** `stage_a` reads exactly four things
    -- `processing_status`, `has_single_cell_assay`,
    `perturbation_present_any_assay`, and the `single_cell_paired` values inside
    `perturbations` -- and nothing here writes any of them. A suppressed
    candidate is by definition not a perturbation, so it is never appended to
    that array and never promoted out of this one. If a suppressed candidate ever
    changes a determination, the bug is a write that escaped this function.

    An unverifiable quote drops the quote and keeps the entry. The alternative --
    dropping the entry -- would restore exactly the silence the field was added
    to remove, and would make a bad quote look like a decision never made.
    """
    raw = result.get(_SUPP_PATH)
    if raw is None:
        issues.append(
            "suppressed_candidates missing; the schema requires it. Use [] when "
            "nothing was suppressed: a null cannot be told apart from 'the model "
            "never considered the question', which is the ambiguity this field exists "
            "to remove")
        raw = []
    elif not isinstance(raw, list):
        issues.append(f"suppressed_candidates={raw!r} is not a list")
        raw = []

    checked = failed = wrong_source = 0
    entries: list[dict] = []

    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            issues.append(f"suppressed_candidates[{i}] is not an object")
            continue

        quote_failed = False
        rule = item.get(_SUPP_REASON)
        if rule not in SUPPRESSION_RULES:
            issues.append(
                f"suppressed_candidates[{i}].{_SUPP_REASON}={rule!r} is outside the "
                f"closed set {list(SUPPRESSION_RULES)} — an open value cannot be "
                f"tallied, which is the whole point of the field")
        if item.get(_SUPP_PAIRING) not in TRISTATE:
            issues.append(f"suppressed_candidates[{i}].{_SUPP_PAIRING}="
                          f"{item.get(_SUPP_PAIRING)!r} not in yes/no/unclear")
        if not str(item.get(_SUPP_NAME) or "").strip():
            issues.append(f"suppressed_candidates[{i}].{_SUPP_NAME} is empty — the entry "
                          f"names nothing and cannot be reviewed")

        entry = item.get(_SUPP_QUOTE)
        if isinstance(entry, str):
            issues.append(f"suppressed_candidates[{i}].{_SUPP_QUOTE} is a bare string, "
                          f"expected {{source_id, quote}}")
            entry = {"source_id": "main", "quote": entry}

        if isinstance(entry, dict) and str(entry.get("quote") or "").strip():
            quote = str(entry.get("quote"))
            claimed = str(entry.get("source_id") or "main")
            outcome = verify(quote, claimed)
            checked += 1
            item["quote_check"] = outcome

            if outcome["status"] == "verified":
                item[_SUPP_QUOTE] = {"source_id": claimed, "quote": quote}
            elif outcome["status"] in ("wrong_source", "unknown_source"):
                wrong_source += 1
                flags.add("EV-WRONG-SOURCE")
                item[_SUPP_QUOTE] = {"source_id": outcome["source_id"],
                                     "quote": quote,
                                     "source_id_corrected_from": claimed}
                if outcome["status"] == "unknown_source":
                    flags.add("CC-7")
                    issues.append(f"suppressed_candidates[{i}] quote cited unknown source "
                                  f"{claimed!r}; found in {outcome['source_id']!r} (CC-7)")
                else:
                    issues.append(f"suppressed_candidates[{i}] quote attributed to "
                                  f"{claimed!r} but found in {outcome['source_id']!r} "
                                  f"(EV-WRONG-SOURCE)")
            else:
                failed += 1
                quote_failed = True
                flags.add(_SUPP_UNVERIFIED_FLAG)
                # A harness-derived annotation, not a declared field: `screens.py`
                # and `report.py` read this and the other `*_check` keys by the
                # same literal, so it is deliberately not derived from the table.
                item["evidence_quote_dropped"] = {"source_id": claimed, "quote": quote}
                item[_SUPP_QUOTE] = None
                kept_note = ("quote dropped, ENTRY KEPT"
                             if _SUPP_KEEP_ON_QUOTE_FAILURE else "ENTRY DROPPED")
                issues.append(
                    f"suppressed_candidates[{i}] ({str(item.get(_SUPP_NAME))[:50]!r}) "
                    f"quote unverifiable in any source (best ratio {outcome['ratio']}) — "
                    f"{kept_note} ({_SUPP_UNVERIFIED_FLAG})")
        else:
            # Legitimate per the prompt: an exclusion resting on the ABSENCE of a
            # statement has nothing to quote. `why` is expected to say so.
            if not _SUPP_QUOTE_OPTIONAL:
                issues.append(f"suppressed_candidates[{i}].{_SUPP_QUOTE} is absent, and "
                              f"this pack does not allow a quoteless entry")
            item[_SUPP_QUOTE] = None

        if quote_failed and not _SUPP_KEEP_ON_QUOTE_FAILURE:
            # Not this pack: dropping the entry would restore exactly the silence
            # the field was added to remove. Honoured so the declaration is real.
            continue
        entries.append(item)

    # `would_have_paired` is held to Step 3's evidence standard, not used as an
    # emphasis marker. If every entry says "yes" the column has stopped
    # discriminating -- and in practice that pattern travelled with the field
    # over-firing, pulling real perturbations across the line (observed on
    # 10.1038/s41586-024-07571-1 and 10.7554/elife.104978.2, both moved yes ->
    # no by a wrongly-suppressed clinical therapy). Mechanically checkable, so it
    # is checked. This raises an issue only: judgment stays in the prompt.
    if len(entries) >= 2 and all(e.get(_SUPP_PAIRING) == "yes" for e in entries):
        issues.append(
            f"all {len(entries)} suppressed candidates have {_SUPP_PAIRING}='yes'; "
            f"the column has stopped discriminating. Check that none of them is "
            f"actually a perturbation under a Step 2 report rule — filling "
            f"suppressed_candidates must not shorten the perturbations array")

    return entries, checked, failed, wrong_source


def progress_line(doi: str, result: dict) -> str:
    """The one-line-per-paper progress the validator prints.

    Ten task field names in one f-string, which is why it is here. The harness
    decides WHEN to print; this decides what an operator watching a 392-paper run
    is shown, including which conditions are worth shouting about.
    """
    v = result.get("validation") or {}
    quotes_ok = v["quotes_checked"] - v["quotes_failed"]
    flags = ""
    if v[f"{_ITEM_PATH}_dropped"]:
        flags += f"  DROPPED={v[f'{_ITEM_PATH}_dropped']}"
    if v["stage_b_capped"]:
        flags += "  STAGE-B-CAP"
    if v["determination_changed_by_harness"]:
        flags += f"  MODEL={result[_REC['model_field']]}"
    if v["assay_filtered"]:
        flags += "  ASSAY-FILTERED"
    if v["consistency_flags"]:
        flags += "  " + ",".join(v["consistency_flags"])
    if v["evidence_flags"]:
        flags += "  " + ",".join(v["evidence_flags"])
    return (f"  {doi:38} {str(result.get('processing_status', '?')):8}"
            f"{str(result.get('text_completeness', '?')):16}"
            f"sc={str(result.get('has_single_cell_assay', '?')):8}"
            f"{str(result.get('perturbation_present', '?')):8}"
            f"(any={str(result.get('perturbation_present_any_assay', '?')):8}) "
            f"conf={str(result.get('paper_confidence', '?')):<5} "
            f"perts={v['perturbations_kept']:<3} "
            f"y/n/u={v['paired_yes']}/{v['paired_no']}/{v['paired_unclear']:<2} "
            f"q={quotes_ok}/{v['quotes_checked']}{flags}")


# ---------------------------------------------------------------------------
# The task's own counters, in the order the record has always carried them
# ---------------------------------------------------------------------------

def metrics(result: dict, ctx: dict) -> dict:
    """The task half of `validation`, keyed and ordered as the record expects.

    `ctx` carries what the generic core computed: `kept`, `dropped`, `secondary`,
    and the secondary array's quote counts. Order matters -- the records on disk
    are compared byte for byte, so this dict is assembled in the sequence it has
    always appeared in.
    """
    kept = ctx["kept"]
    suppressed = ctx["secondary"]
    paired_values = _paired(result)

    def yes_pairings():
        return [p for p in kept if p.get(_ITEM_LABEL) == "yes"]

    named = [p for p in yes_pairings() if normalise_organism(p.get("paired_organism"))]
    return {
        # What the NOT list swallowed on this paper. `would_pair_yes` is the
        # actionable one -- those papers are one toggle from "yes".
        "n_suppressed": len(suppressed),
        "suppressed_rules": sorted({str(s.get(_SUPP_REASON)) for s in suppressed
                                    if s.get(_SUPP_REASON) in SUPPRESSION_RULES}),
        # Both are reported: the raw fact, and the subset triage acts on. A
        # curator comparing them sees how much of the suppression load comes from
        # settled toggles rather than from the rules in review.
        "suppressed_would_pair_yes": any(
            s.get(_SUPP_PAIRING) == "yes" for s in suppressed),
        "suppressed_would_pair_yes_under_review": any(
            s.get(_SUPP_PAIRING) == "yes"
            and s.get(_SUPP_REASON) in RULES_UNDER_REVIEW for s in suppressed),
        "suppressed_quotes_checked": ctx["secondary_checked"],
        "suppressed_quotes_failed": ctx["secondary_failed"],
        # WHOSE sample the `yes` pairings refer to. Descriptive only -- nothing
        # here feeds the determination, and test_organism.py asserts that over
        # every Stage A input combination. The curation scope is applied
        # downstream by a person, because the corpus is human-primarily but not
        # human-only and the paper often cannot say which species was deposited.
        "paired_organisms": sorted({
            o for o in (normalise_organism(p.get("paired_organism"))
                        for p in yes_pairings()) if o}),
        "n_paired_yes_human": sum(1 for p in yes_pairings()
                                  if is_human(p.get("paired_organism"))),
        # true / false / None: None means no `yes` pairing names an organism at
        # all, which is different from naming a non-human one. Kept tri-state so
        # an unknown never reads as a confident "not human".
        "paired_organism_human": (None if not named
                                  else any(is_human(p.get("paired_organism"))
                                           for p in yes_pairings())),
        "paired_yes": paired_values.count("yes"),
        "paired_no": paired_values.count("no"),
        "paired_unclear": paired_values.count("unclear"),
        "mixed_no_unclear": ("no" in paired_values and "unclear" in paired_values
                             and "yes" not in paired_values),
        "assay_filtered": (result.get("perturbation_present_any_assay") == "yes"
                           and result.get("perturbation_present") in ("no", "unclear")),
    }


# ---------------------------------------------------------------------------
# The determinative array, and the arrays that point into it
#
# Moved here verbatim from pe/validate.py rather than parameterised into it.
# These loops are ~55% field NAMES -- `category`, `paired_organism`,
# `single_cell_paired`, `assay_evidence`, `perturbation_refs` -- and ~45%
# generic mechanism, and threading a dozen names plus their message wording
# through the harness would have put this task's vocabulary back into `pe/` in a
# less readable form. `verify` is the harness's quote check, passed in, so the
# fuzzy matching and the cross-source resolution stay generic where they belong.
#
# What the harness keeps: parsing the model's JSON, splitting the sources,
# verifying and correcting a quote, the pruning bookkeeping, the recomputation,
# and assembling the record. What it no longer knows: what any of these fields
# are called.
# ---------------------------------------------------------------------------

def validate_items(result: dict, verify, issues: list[str],
                   flags: set[str]) -> dict:
    """Verify each item's quotes, prune the unverifiable, renumber references.

    Returns a context dict the harness merges into `validation`, plus the pruned
    arrays it writes back onto the record.

    `verify(quote, claimed_source)` returns the harness's outcome dict. An item
    left with no verifiable quote is dropped whole and its confidence rewritten,
    because a determination resting on a hallucinated quote must not survive the
    removal of that quote.
    """
    perturbations = result.get(_ITEM_PATH) or []
    if not isinstance(perturbations, list):
        issues.append(f"{_ITEM_PATH} is not a list")
        perturbations = []

    # ---- source-scoped quote verification + pruning (batch spec step 6) ----
    checked = failed = wrong_source = 0
    kept: list[dict] = []
    dropped: list[dict] = []
    index_map: dict[int, int] = {}

    for i, pert in enumerate(perturbations):
        if not isinstance(pert, dict):
            issues.append(f"perturbations[{i}] is not an object")
            continue

        if pert.get("category") not in CATEGORIES:
            issues.append(f"perturbations[{i}].category={pert.get('category')!r} off-schema")

        # v0.0.12. Type only; the value set is open. Never rejected for being an
        # unusual species, never inferred when absent.
        issues.extend(_open_field_issues(pert, f"{_ITEM_PATH}[{i}]", _ITEM_OPEN_FIELDS))

        paired = pert.get("single_cell_paired")
        if paired not in TRISTATE:
            issues.append(
                f"perturbations[{i}].single_cell_paired={paired!r} not in yes/no/unclear")

        raw_quotes = pert.get(_ITEM_QUOTES) or []
        if isinstance(raw_quotes, (str, dict)):
            raw_quotes = [raw_quotes]

        verified_quotes, quote_checks = [], []
        for entry in raw_quotes:
            source_id, quote, legacy = _normalize_quote_entry(entry)
            if legacy:
                issues.append(f"perturbations[{i}] evidence_quote is a bare string "
                              f"(v0.0.4 shape), expected {{source_id, quote}}")
            outcome = verify(quote, source_id)
            checked += 1
            check = {"claimed_source": source_id, "quote": quote, **outcome}
            quote_checks.append(check)

            if outcome["status"] == "verified":
                verified_quotes.append({"source_id": source_id, "quote": quote})
            elif outcome["status"] in ("wrong_source", "unknown_source"):
                # Keep the text, correct the attribution, flag it.
                wrong_source += 1
                verified_quotes.append({"source_id": outcome["source_id"], "quote": quote,
                                        "source_id_corrected_from": source_id})
                flags.add("EV-WRONG-SOURCE")
                if outcome["status"] == "unknown_source":
                    flags.add("CC-7")
                    issues.append(f"perturbations[{i}] quote cited unknown source "
                                  f"{source_id!r}; found in {outcome['source_id']!r} (CC-7)")
                else:
                    issues.append(f"perturbations[{i}] quote attributed to {source_id!r} "
                                  f"but found in {outcome['source_id']!r} (EV-WRONG-SOURCE)")
            else:
                failed += 1
                flags.add("EV-UNVERIFIED")
                issues.append(f"perturbations[{i}] quote unverifiable in any source "
                              f"(best ratio {outcome['ratio']}) — dropped")

        pert["quote_checks"] = quote_checks
        pert[_ITEM_QUOTES] = verified_quotes
        pert["quotes_validated"] = bool(verified_quotes)

        # assay_evidence: object or null in v0.0.5.
        assay_ev = pert.get(_ITEM_QUOTE_2)
        if isinstance(assay_ev, str):
            issues.append(f"perturbations[{i}].assay_evidence is a bare string "
                          f"(v0.0.4 assay_evidence_quote shape)")
            assay_ev = {"source_id": "main", "quote": assay_ev}
        if isinstance(assay_ev, dict) and str(assay_ev.get("quote") or "").strip():
            outcome = verify(str(assay_ev.get("quote")),
                             str(assay_ev.get("source_id") or "main"))
            checked += 1
            pert["assay_quote_check"] = outcome
            if outcome["status"] == "unverified":
                failed += 1
                if paired in ("yes", "no"):
                    pert[_ITEM_QUOTE_2_DOWNGRADES] = "unclear"
                    pert["pairing_downgraded_from"] = paired
                    paired = "unclear"
                    flags.add("EV-PAIRING-DOWNGRADED")
                    issues.append(
                        f"perturbations[{i}].assay_evidence unverifiable — pairing "
                        f"downgraded to 'unclear' (EV-PAIRING-DOWNGRADED)")
                else:
                    flags.add("EV-UNVERIFIED")
            elif outcome["status"] in ("wrong_source", "unknown_source"):
                wrong_source += 1
                flags.add("EV-WRONG-SOURCE")
                assay_ev = dict(assay_ev, source_id=outcome["source_id"],
                                source_id_corrected_from=assay_ev.get("source_id"))
                pert[_ITEM_QUOTE_2] = assay_ev
        elif paired in ("yes", "no"):
            # Legitimate per the prompt (an inferred pairing), but recorded so a
            # curator can see the pairing is not quoted.
            issues.append(
                f"perturbations[{i}].single_cell_paired={paired!r} asserted with no "
                f"assay_evidence (pairing is inferred, not quoted)")

        if not verified_quotes and _ITEM_DROP_UNQUOTED:
            # batch spec step 6: zero verified quotes -> drop the perturbation.
            flags.add("EV-PERT-DROPPED")
            issues.append(
                f"perturbations[{i}] ({pert.get(_ITEM_NAME)!r}) DROPPED: no evidence "
                f"quote could be verified against any source")
            pert["dropped_reason"] = "no verifiable evidence quote"
            pert["confidence_original"] = pert.get("confidence")
            pert["confidence"] = _DOWNGRADE
            dropped.append(pert)
        else:
            index_map[i] = len(kept)
            kept.append(pert)

    result["perturbations"] = kept
    if dropped:
        result["perturbations_dropped"] = dropped
    result[_ITEM_PATH] = kept
    if dropped:
        result[f"{_ITEM_PATH}_dropped"] = dropped

    # ---- samples ----------------------------------------------------------
    for j, sample in enumerate(result.get(_REF_PATH) or []):
        if not isinstance(sample, dict):
            issues.append(f"{_REF_PATH}[{j}] is not an object")
            continue
        # v0.0.5 curator ruling: true | false | "unclear" are all schema-legal,
        # so "unclear" is no longer an issue. Only pe.summarize's `is true` test
        # decides what counts as perturbed.
        for field in _REF_TRISTATE_BOOLS:
            if sample.get(field) not in (True, False, "unclear"):
                issues.append(
                    f"{_REF_PATH}[{j}].{field}={sample.get(field)!r} not in "
                    f"true/false/'unclear'")
        if sample.get("is_single_cell_assay") not in TRISTATE:
            issues.append(f"samples[{j}].is_single_cell_assay="
                          f"{sample.get('is_single_cell_assay')!r} not in yes/no/unclear")
        # v0.0.12. Type only -- the value set is open by design, so an
        # unrecognised species is not an error. A non-string, non-null value is.
        issues.extend(_open_field_issues(sample, f"{_REF_PATH}[{j}]", _REF_OPEN_FIELDS))
        # Reindex refs onto the pruned array so they never dangle.
        refs = sample.get(_REF_FIELD) or []
        remapped = []
        for ref in refs:
            if not isinstance(ref, int) or ref not in index_map:
                if isinstance(ref, int) and 0 <= ref < len(perturbations):
                    issues.append(f"{_REF_PATH}[{j}].{_REF_FIELD} -> {ref} pointed at a "
                                  f"dropped perturbation; reference removed")
                else:
                    issues.append(f"{_REF_PATH}[{j}].{_REF_FIELD} contains invalid "
                                  f"index {ref!r}")
                continue
            remapped.append(index_map[ref])
        if remapped != refs:
            sample[f"{_REF_FIELD}_original"] = refs
            sample[_REF_FIELD] = remapped
    return {"kept": kept, "dropped": dropped, "index_map": index_map,
            "checked": checked, "failed": failed, "wrong_source": wrong_source}


def extra_field_issues(result: dict) -> list[str]:
    """Task field dependencies that are not enum checks.

    One rule today: claiming a qualifying assay while naming none is a record
    that cannot be reviewed, since the whole determination hangs on WHICH assay
    was applied to which sample.
    """
    if result.get("has_single_cell_assay") == "yes" \
            and not result.get("single_cell_assay_types"):
        return ["has_single_cell_assay='yes' but single_cell_assay_types is empty"]
    return []
