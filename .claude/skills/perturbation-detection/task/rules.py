"""The predicates a lookup table cannot express, for this task and no other.

Everything here was a module constant or a function in `harness/`, where a reader had
no way to tell the harness's own machinery from this task's judgment. `harness/` now
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

The interface `harness/` relies on, and all a second pack must supply:

    decide(record)                        -> (label, stage_label, downgraded)
    checks(record)                        -> [code, ...]
    metrics(record, ctx)                  -> ordered dict of task counters
    validate_secondary(record, verify, issues, flags)
                                          -> (entries, checked, failed, wrong)
    validate_defects(record, verify, issues, flags, section_chars,
                     harness_withheld)    -> (entries, checked, rejected)
"""

from __future__ import annotations

from harness.pack import tables

_T = tables()
_REC = _T["record"]
_DEC = _T["decide"]

#: The classification's own values, and the enum three fields are checked against.
LABELS = tuple(_REC["labels"])
#: Kept under their historical names because the tests and prompt.md use them.
TRISTATE = LABELS
#: `perturbation_present` only -- the tri-state plus `not_applicable`. Kept apart
#: from TRISTATE because stage_a tests `has_single_cell_assay` and
#: `perturbation_present_any_assay` against TRISTATE, and neither may be
#: `not_applicable`.
DETERMINATION_LABELS = tuple(_REC["determination_labels"])
PRIMARY_RESEARCH = tuple(_REC["primary_research"])
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
_DOWNGRADE = _DEC["damaged_text_downgrade"]

#: Where a record stores Stage B's result. Records written before 0.0.26 call it
#: `stage_b_capped`, and every run directory on disk from before then is one of
#: those, so anything that reads the field goes through `downgraded()` rather
#: than indexing either name -- otherwise a comparison against an old baseline
#: reads every old downgrade as False and reports it released.
DOWNGRADE_FIELD = "damaged_text_downgrade"
LEGACY_DOWNGRADE_FIELD = "stage_b_capped"


def downgraded(validation: dict | None, default=None):
    """Stage B's result from a record's `validation` block, under either name."""
    v = validation or {}
    if DOWNGRADE_FIELD in v:
        return v[DOWNGRADE_FIELD]
    return v.get(LEGACY_DOWNGRADE_FIELD, default)

#: The text-quality array, v0.0.25. Field names and closed sets only -- the rules
#: that read them are `validate_defects` and `downgrading_defects` below.
_DEFECTS = _REC["defect_array"]
_DEFECT_PATH = _DEFECTS["path"]
_DEFECT_SOURCE = _DEFECTS["source_field"]
_DEFECT_KIND = _DEFECTS["kind_field"]
_DEFECT_QUOTE = _DEFECTS["quote_field"]
DEFECT_KINDS = tuple(_DEFECTS["kinds"])
_UNQUOTABLE = frozenset(_DEFECTS["unquotable_kinds"])
_METHODS_NAMES = tuple(n.lower() for n in _DEFECTS["methods_section_names"])
_METHODS_MIN_CHARS = int(_DEFECTS["methods_min_chars"])

#: The harness's own fields, by role. Named here so nothing writes them under a
#: literal of its own, and keyed rather than ordered so inserting one cannot
#: silently repoint another.
HARNESS_FIELDS = dict(_REC["harness_written_fields"])
HARNESS_WITHHELD_FIELD = HARNESS_FIELDS["withheld"]
HARNESS_UNREADABLE_FIELD = HARNESS_FIELDS["defect_unreadable"]


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
    """prompt.md Stage A: evidence-based determination, ordered A0, A-1, A1-A6.

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

    # A-1. The paper reports no study of its own: a review, commentary,
    # perspective or editorial. Nothing here is this paper's evidence, so there
    # is nothing to pair and nothing to assess.
    #
    # Numbered A-1 because it logically precedes the evidence rules, but
    # evaluated AFTER A0 on purpose: a failed extraction cannot support a
    # judgment about article type. A paywall page is "unclear", not "a review".
    #
    # Not folded into A1 (empty `perturbations`). A review describes other
    # people's perturbation experiments, so its array is often NON-empty --
    # `10.1016/j.coi.2022.102188` yielded six perturbations and twelve samples
    # harvested from work the authors did not do. A1 would have called that
    # "unclear" and a naive "no" would assert the opposite of what the text
    # plainly shows.
    if result.get("reports_primary_research") == "no":
        return "not_applicable"

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
    # A3. An unconfirmed assay limits the paper-level call to "unclear".
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


def downgrading_defects(result: dict) -> list[dict]:
    """The verified defects that are in scope for the downgrade, per `decide.yaml`.

    Scope is a curator decision of 2026-09-16, and it follows from what the downgrade
    is FOR: missing text can hide the sentence that would pair a perturbation to
    a qualifying assay. A garbled table in a supplementary reporting summary
    cannot hide that sentence, so it does not withhold a negative; an absent
    methods section can, wherever it lives, so it does from any source.

    Reads only entries still on the record. `validate_defects` has already
    dropped the ones whose quote did not verify, so an unevidenced claim is not
    in this list to begin with.
    """
    in_scope = set(_DOWNGRADE["when_defect_in_sources"])
    any_source = set(_DOWNGRADE["when_defect_kind_any_source"])
    return [d for d in (result.get(_DEFECT_PATH) or []) if isinstance(d, dict)
            and (d.get(_DEFECT_KIND) in any_source
                 or str(d.get(_DEFECT_SOURCE)) in in_scope)]


def stage_b(stage_a_result: str | None, harness_withheld: bool = False,
            defects: list | None = None) -> tuple[str | None, bool]:
    """prompt.md Stage B: downgrade a negative drawn from text that is missing content.

    Returns (determination, downgraded). The asymmetry is deliberate and unchanged:
    missing text can hide the sentence that would have paired a perturbation to a
    single-cell assay, but it cannot invent one, so only "no" is downgraded.

    **What changed at v0.0.25 is the ENTRY CONDITION, not the downgrade.** It was
    `processing_status == "partial" OR text_completeness != "full"` -- two
    paper-level self-reports of the text's quality, with no evidence behind
    either. Those flipped on byte-identical input in two consecutive acceptance
    passes: 3 of 30 papers under v0.0.23, and 4 of 24 under v0.0.24 after the
    assembly block had removed the largest single cause. A safety mechanism whose
    trigger is an adjective is not reproducible, and the papers it protects are
    exactly the ones whose determination then depends on which pass you ran.

    So the trigger is now two auditable facts, either of which is sufficient:

    `harness_withheld` -- the truncation ladder ran. The model cannot see this
    and cannot dispute it, and it is what keeps the downgrade on the two corpus papers
    that do not fit the budget with Methods preserved.

    `defects` -- in-scope entries from `downgrading_defects`, i.e. observations the
    model made AND the harness could verify against the source they cite. A
    claim whose quote does not verify never reaches here.
    """
    gate = bool(harness_withheld and _DOWNGRADE["when_harness_withheld"]) or bool(defects)

    if gate and stage_a_result == _DOWNGRADE["from"]:
        return _DOWNGRADE["to"], True
    return stage_a_result, False


def decide(result: dict) -> tuple[str | None, str | None, bool]:
    """The harness's entry point: (final label, stage-A label, downgraded).

    Two values rather than one because the record keeps both -- a curator reading
    a downgraded paper needs to see what the evidence alone said.
    """
    a = stage_a(result)
    if a is None:
        return None, None, False
    # Either harness fact opens the gate. `defect_claim_unreadable` is the
    # fail-closed half: a defect claimed and botched is a claim nobody could
    # check, which is not the same as a claim checked and found false.
    withheld = bool(result.get(HARNESS_WITHHELD_FIELD)
                    or result.get(HARNESS_UNREADABLE_FIELD))
    final, downgraded = stage_b(a, withheld, downgrading_defects(result))
    return final, a, downgraded


def expected_determination(result: dict) -> str | None:
    """Stage A then Stage B, as the harness applies them."""
    return decide(result)[0]


def checks(result: dict) -> list[str]:
    """CC-1 .. CC-6 and CC-8. CC-7 is raised by the harness during quote checking."""
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
    # CC-8. The article-type gate returns before Step 2, so a gated paper cannot
    # have reached a perturbation by following the steps in order. Either the
    # gate over-fired on a paper with its own experiments, or the experiments
    # described belong to the papers under review.
    if result.get("reports_primary_research") == "no" and perts:
        codes.append("CC-8")
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
# The text-quality array, which IS determinative -- it gates Stage B
# ---------------------------------------------------------------------------

def methods_claim_refuted(section_chars: dict | None) -> bool:
    """Is "there is no methods content" contradicted by what was supplied?

    **One-way on purpose.** `True` means a substantial methods-labelled section
    reached the model, so the claim is false. `False` means only that this cannot
    refute it -- NOT that the claim is true. 15 of the 392 corpus papers supply
    zero methods-labelled characters and most of them are correctly "full",
    because JATS and Science put methods under labels the extractor never
    matched. The label measures labelling, not content.

    A quantity rather than a presence test, because a label can arrive with
    nothing under it: see `methods_min_chars` in record.yaml for the measurement
    and for the paper whose whole methods section is two copies of its heading.
    """
    if not isinstance(section_chars, dict):
        return False
    total = sum(int(n or 0) for label, n in section_chars.items()
                if any(name in str(label).lower() for name in _METHODS_NAMES))
    return total >= _METHODS_MIN_CHARS


def validate_defects(result: dict, verify, issues: list[str], flags: set[str],
                     section_chars: dict | None = None,
                     harness_withheld: bool = False) -> tuple[list, int, int]:
    """Verify and normalize `text_defects`. Returns (kept, checked, rejected).

    `verify(quote, claimed_source)` is the harness's own quote check, passed in
    for the same reason `validate_secondary` takes it: the matching logic stays
    in `harness/` and only the field names and the wording live here.

    **An unverifiable claim is dropped, not kept.** That is the opposite of
    `validate_secondary`, which keeps a suppressed candidate whose quote failed,
    and the asymmetry is the point: a suppression is a record of a decision
    already made, while a defect entry is a LICENCE TO DOWNGRADE. Keeping one the
    harness could not confirm would put the unevidenced adjective back -- which
    is the entire defect v0.0.25 exists to remove. `atvbaha.122.317953` claimed
    "truncated" in one v0.0.24 run and named no locus at all; under this function
    that claim is dropped and the negative stands.

    `no_methods_content` is the one kind that cannot be quoted, because it is an
    ABSENCE -- there is no substring to find. It is FALSIFIED instead, against
    the `section_chars` the manifest records, and only ever in the direction that
    kills a false claim: see `methods_claim_refuted`. Both papers that reported
    it in the v0.0.24 runs survive that check -- `2021.09.16.460628` supplies no
    methods-labelled text at all and `science.aat1699` supplies 228 characters of
    it, which is two copies of the heading.
    """
    # The harness's own finding, recorded under the name this pack declares. It
    # is written here rather than in `harness/` so the harness never names a field
    # only one pack has -- and it is a field of its own rather than an overwrite
    # of `text_completeness`, because prompt.md is explicit that two owners for
    # one field would make it unreadable.
    result[HARNESS_WITHHELD_FIELD] = bool(harness_withheld)

    raw = result.get(_DEFECT_PATH)
    if raw is None:
        issues.append(
            f"{_DEFECT_PATH} missing; the schema requires it. Use [] when the "
            f"text has no defect -- which is the normal and common answer")
        raw = []
    elif not isinstance(raw, list):
        issues.append(f"{_DEFECT_PATH} is not a list")
        raw = []

    kept, checked, rejected, unreadable = [], 0, 0, 0
    for entry in raw:
        if not isinstance(entry, dict):
            issues.append(f"{_DEFECT_PATH} entry is not an object: {entry!r}")
            rejected += 1
            unreadable += 1
            continue
        kind = entry.get(_DEFECT_KIND)
        source = str(entry.get(_DEFECT_SOURCE) or "")
        if kind not in DEFECT_KINDS:
            issues.append(
                f"{_DEFECT_PATH}: {kind!r} is not one of {list(DEFECT_KINDS)}")
            rejected += 1
            unreadable += 1
            continue

        if kind in _UNQUOTABLE:
            # Cannot be confirmed; can be refuted.
            if methods_claim_refuted(section_chars):
                issues.append(
                    f"{_DEFECT_PATH}: {kind!r} claimed, but at least "
                    f"{_METHODS_MIN_CHARS:,} characters of methods-labelled text "
                    f"WERE supplied, so the claim is refuted and dropped")
                flags.add("EV-DEFECT-REFUTED")
                rejected += 1
                continue
            kept.append(entry)
            continue

        quote = entry.get(_DEFECT_QUOTE)
        if not isinstance(quote, str) or not quote.strip():
            issues.append(
                f"{_DEFECT_PATH}: {kind!r} in {source!r} carries no quote. Only "
                f"{sorted(_UNQUOTABLE)} may omit one, because only they are an "
                f"absence; everything else must point at the text it found")
            flags.add("EV-DEFECT-UNVERIFIED")
            rejected += 1
            unreadable += 1
            continue

        checked += 1
        check = verify(quote, source)
        # `verify_quote_sourced` reports a `status`; there has never been a
        # `verified` key. Reading the absent one dropped EVERY quotable defect in
        # the v0.0.25 acceptance run -- 14 of 14, one of them matching its own
        # cited source at ratio 1.0. The gate was then left firing on
        # `harness_withheld` and `no_methods_content` alone, both deterministic,
        # so `ACCEPTANCE-v0.0.25.md` criterion 2 -- "the downgrade agrees across two
        # runs" -- would have passed over a mechanism that never once ran. A
        # criterion that cannot distinguish a working gate from an absent one is
        # the shape this pack keeps re-learning; the companion guard is that the
        # gate must also be EXERCISED, not merely agree.
        #
        # Only "unverified" means the text is not in the paper. "wrong_source"
        # and "unknown_source" mean the quote IS there under another id, which is
        # precisely what the attribution fix below exists to correct -- and which
        # was unreachable for as long as this line rejected all four statuses.
        if check["status"] == "unverified":
            issues.append(
                f"{_DEFECT_PATH}: the quote for {kind!r} does not verify against "
                f"{source!r}, so the claim is dropped and does not downgrade")
            flags.add("EV-DEFECT-UNVERIFIED")
            rejected += 1
            continue
        if check.get("source_id") and check["source_id"] != source:
            # Same treatment the item array gets: the text is real, the
            # attribution is not, so correct it rather than dropping evidence.
            entry[_DEFECT_SOURCE] = check["source_id"]
            flags.add("EV-WRONG-SOURCE")
        kept.append(entry)

    result[_DEFECT_PATH] = kept
    # Fails closed. See `decide.yaml: inputs.defect_unreadable` for why this is
    # the opposite treatment from a quote that simply did not verify.
    result[HARNESS_UNREADABLE_FIELD] = unreadable > 0
    if unreadable:
        issues.append(
            f"{unreadable} {_DEFECT_PATH} entr{'y' if unreadable == 1 else 'ies'} "
            f"could not be read at all, so the downgrade is applied rather than "
            f"released: an unreadable claim is not a refuted one")

    # A degraded self-report with nothing behind it. Not a consistency code: the
    # model has not contradicted itself, it has simply asserted something the
    # harness cannot see, and the assertion no longer decides anything. Reported
    # so a curator can tell "the text is fine" from "nobody could check".
    completeness = result.get("text_completeness")
    if completeness not in (None, "full") and not kept and not harness_withheld:
        issues.append(
            f"text_completeness={completeness!r} with no verified {_DEFECT_PATH} "
            f"entry: the claim carries no evidence, so it does not downgrade")
    return kept, checked, rejected


# ---------------------------------------------------------------------------
# The non-determinative array
# ---------------------------------------------------------------------------

def validate_secondary(result: dict, verify, issues: list[str],
                       flags: set[str]) -> tuple[list, int, int, int]:
    """Verify and normalize `suppressed_candidates`. Returns (entries, n, fail, wrong).

    `verify(quote, claimed_source)` is the harness's quote check, passed in so the
    matching logic stays in `harness/` and only the field names and the wording live
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
    if v[DOWNGRADE_FIELD]:
        flags += "  DAMAGED-TEXT-DOWNGRADE"
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
# Moved here verbatim from harness/validate.py rather than parameterised into it.
# These loops are ~55% field NAMES -- `category`, `paired_organism`,
# `single_cell_paired`, `assay_evidence`, `perturbation_refs` -- and ~45%
# generic mechanism, and threading a dozen names plus their message wording
# through the harness would have put this task's vocabulary back into `harness/` in a
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
            # The item's own confidence is left as the model stated it. It used
            # to be overwritten with a floor and preserved under
            # `confidence_original` -- a rewrite that existed only because the
            # rewrite destroyed the original, and that nothing ever read. A
            # dropped item is removed from `perturbations` entirely, so it
            # advertises nothing and there is nothing to walk back.
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
        # so "unclear" is no longer an issue. Only harness.summarize's `is true` test
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
