#!/usr/bin/env python3
"""Stage 3: verify evidence quotes source-by-source, prune, then recompute.

    python -m pe.validate [--work work/] [--write-corpus]

prompt.md v0.0.5 batch spec step 6 changes this stage's job. Under v0.0.4 the
validator only *flagged*: it checked quotes, downgraded confidence, and left the
model's `perturbation_present` alone. v0.0.5 requires the harness to prune
unverified evidence and then RECOMPUTE the determination over what survives,
because "a determination resting on a hallucinated quote must not survive the
removal of that quote". Both values are kept -- `perturbation_present_model` and
`perturbation_present_final` -- and their disagreement rate is the corpus-level
signal for evidence fabrication.

v0.0.10 adds `suppressed_candidates` (schema 0.0.6). Its quotes are verified
exactly like the rest -- the field would be worthless if it were the one place a
quote went unchecked -- but an unverifiable quote drops the QUOTE and keeps the
ENTRY, because the suppression still happened and deleting the entry restores the
silence the field exists to remove. Nothing about suppressed candidates reaches
Stage A or Stage B: see `_validate_suppressed`.

No LLM calls.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pe.paper_text import split_assembled, verify_quote_sourced  # noqa: E402
from pe.pack import (  # noqa: E402
    PackError, load as load_pack, read_back_marker, tables,
)

try:
    import yaml
except ImportError:
    yaml = None

from pe.runroot import output_name, work_default  # noqa: E402
from pe.runstate import (  # noqa: E402
    RunError, entry_paths, load_manifest, resolve_run_dir,
)

ROOT = Path(__file__).resolve().parent.parent

# The harness holds no task vocabulary. Every closed set, every field name and
# every rule below came out of this module in the 0.0.13 split and now lives in
# `task/` -- record.yaml and decide.yaml for the lists, task/rules.py for the
# predicates a list cannot express. Imported here only where this module
# genuinely uses one; everything else is imported from `task.rules` directly by
# whoever needs it, so the seam shows at the import line rather than being
# laundered through the harness.
#
# What is left in this file: parsing the model's JSON however it arrives,
# recovering the exact bytes the model was shown, verifying a quote against the
# source it claims, the pruning bookkeeping, the recomputation, and assembling
# the record. None of it knows what a perturbation is.
from task.rules import (  # noqa: E402
    CC_TEXT, checks as consistency_checks, decide, extra_field_issues, metrics,
    progress_line, validate_items, validate_secondary,
)

_REC, _DEC = tables()["record"], tables()["decide"]

#: The item array's name, for the two `validation` keys that carry it.
ITEM_PATH = _REC["item_array"]["path"]
#: The field a curator reads, and the two the harness writes beside it.
PRIMARY_FIELD = _REC["primary_field"]
MODEL_FIELD = _REC["model_field"]
#: Codes the harness raises itself during quote checking, rather than computing
#: from the record. CC-7 -- a quote citing a source_id that was never supplied --
#: is only knowable here, because only here is the supplied set known.
HARNESS_RAISED_CHECKS = tuple(_DEC.get("harness_raised_checks") or ())
#: Post-conditions on the reason an `unclear` gives for itself.
REASON_RULES = dict(_DEC["reason_rules"])
#: The non-determinative array's name, for the one write-back the harness makes.
#: None when the pack declares none -- a "considered and rejected" array is one
#: task's answer to keeping its exclusions visible, not a requirement of the
#: shape. Indexing [0] unconditionally made it one, and a pack without it died at
#: IMPORT with `IndexError: list index out of range`.
_SECONDARY = _REC.get("secondary_arrays") or []
SECONDARY_PATH = _SECONDARY[0]["path"] if _SECONDARY else None


def _field_checks() -> list[dict]:
    """record.yaml's flat checks, with each `in:` resolved to its value set.

    Resolved once at import rather than per paper, and resolved by path so a
    check can point at a nested set (`run_states.processing_status`) without the
    validator knowing the shape of the table.
    """
    resolved = []
    for check in _REC["field_checks"]:
        node = _REC
        for part in str(check["in"]).split("."):
            node = node[part]
        resolved.append({"path": check["path"], "values": tuple(node),
                         "message": check["message"]})
    return resolved


FIELD_CHECKS = _field_checks()


def parse_raw(text: str) -> dict:
    """Accept the model's JSON whether or not it arrived wrapped in prose/fences."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json)?\s*\n(.*?)\n\s*```", text, re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            pass

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return json.loads(text[start : end + 1])
    raise ValueError("no JSON object found")


def paper_text_from_prompt(prompt_file: Path, marker: str | None = None) -> str:
    """Recover exactly the PAPER_TEXT the model saw, so validation is honest.

    `marker` is the pack's `spec.read_back_marker` -- the line `pe.prepare` wrote
    before the paper text. It was hardcoded here as `"\nPAPER_TEXT:"` while
    `task.yaml` declared it and `pack.py` parsed it into
    `TaskPack.read_back_marker`, which **nothing read**. A dead pack key looks
    like it works and does not: a pack declaring any other marker was silently
    ignored, and the recovered "paper text" would have been the whole prompt file
    including the instructions -- so every quote would verify against the spec as
    readily as against the paper.

    Defaulted rather than required only so the tests that call this with a
    hand-built prompt stay readable; every caller in `pe/` passes the pack's
    value. The default is `read_back_marker()`, which comes from the pack too.
    """
    body = prompt_file.read_text()
    needle = marker if marker is not None else read_back_marker()
    idx = body.rindex(needle)
    return body[idx + len(needle):].lstrip("\n")


#: The record field a pre-0.0.13 result carries instead of `task_version`.
LEGACY_VERSION_FIELD = "schema_version"


def expected_task_version() -> str:
    """The `task_version` a record must echo, from the pack's one declaration.

    Deliberately NOT taken from the manifest the way the run's own recorded
    version is. The two answer different questions: the manifest records what the
    extraction ran under and must stay pinned to that history, while this asks
    whether the record in hand matches the rules the harness applies *now* -- so
    re-validating a superseded record should say so rather than quietly grade it
    on its own curve.
    """
    try:
        return load_pack().version
    except PackError:
        return "unknown"


def record_version(result: dict, run_version: str | None = None) -> tuple[str | None, bool]:
    """(version this record claims, whether it came from the legacy field).

    Records written before 0.0.13 carry `schema_version` (0.0.5-0.0.7) and no
    `task_version`, and their run's version lives in the manifest. Reading them
    as their run version is what keeps the 392 papers already scored comparable
    without a re-run -- the alternative was flagging every one of them as
    off-schema, which is the same noise the version collapse was undertaken to
    remove.
    """
    if result.get("task_version") is not None:
        return str(result["task_version"]), False
    if result.get(LEGACY_VERSION_FIELD) is not None:
        return (str(run_version) if run_version else None), True
    return None, False


def model_of(work: Path, doi: str) -> str | None:
    """Which model produced this paper's raw result, if the runner recorded it.

    `pe/run_headless.sh` pins the model so results are attributable across
    machines and across time, and wrote that pin nowhere -- so the record could
    not answer "which model said this", the only question the pin exists to make
    answerable. The runner writes it beside the result rather than into it,
    because the raw JSON is the model's own output and the harness does not edit
    it before reading it back. `None` for every run that predates the sidecar,
    which is the honest answer there rather than a guess at the default.
    """
    path = work / "meta" / f"{doi}.model"
    if not path.is_file():
        return None
    return path.read_text().strip() or None


def validate_result(result: dict, sources_text: dict[str, str], threshold: float,
                    version: str = "unknown", truncated_by_harness: bool = False,
                    expected_schema: str | None = None,
                    model_id: str | None = None,
                    needs_section_pass: bool = False,
                    pack_sha256: str | None = None) -> dict:
    """Verify quotes per source, prune, recompute the determination."""
    issues: list[str] = []
    evidence_flags: set[str] = set()
    source_ids = set(sources_text)

    model_present = result.get(PRIMARY_FIELD)

    # ---- flat enum checks, from record.yaml's field_checks -----------------
    # A loop over data rather than six hand-written comparisons. The value sets
    # were six module constants here and six lists in prompt.md, with no guard
    # that any pair agreed; there is one statement of each now.
    for check in FIELD_CHECKS:
        value = result.get(check["path"])
        if value not in check["values"]:
            issues.append(check["message"].format(path=check["path"], value=value))
    expected = expected_schema or expected_task_version()
    claimed, version_is_legacy = record_version(result, version)
    if expected == "unknown":
        # Never skip silently: a check that quietly stops firing is worse than one
        # that complains, because the record then looks clean for the wrong reason.
        issues.append("task_version not checked — task/task.yaml declares no version")
    elif claimed is None:
        issues.append(
            f"record carries neither `task_version` nor `{LEGACY_VERSION_FIELD}`, so "
            f"there is no way to say which rules produced it; expected {expected!r}")
    elif version_is_legacy:
        # Deliberately NOT an issue. A pre-0.0.13 record is correctly labelled
        # for its own time, so there is nothing for anyone to fix -- and the
        # first draft of this branch DID file a note, which put one entry on all
        # 392 records and took `issues` from 146 to 532. That is precisely the
        # failure the version collapse was undertaken to remove: `issues` is
        # where real problems surface, and one entry on every paper makes the
        # column unreadable. The fact is recorded structurally in
        # `validation.task_version_source` and tallied by pe.summarize, which is
        # the same lesson `suppressed_candidates` taught -- a per-paper note is
        # neither enforceable nor countable; a structured field plus a corpus
        # counter is both.
        pass
    elif claimed != expected:
        issues.append(f"task_version={claimed!r}, expected {expected!r}")
    if not isinstance(result.get("consistency_flags"), list):
        issues.append("consistency_flags missing or not a list")

    seen = result.get("sources_seen")
    if not isinstance(seen, list) or set(map(str, seen)) != source_ids:
        issues.append(f"sources_seen={seen!r} does not match the supplied "
                      f"{sorted(source_ids)} — the assembly step may have dropped a file")

    issues.extend(extra_field_issues(result))

    # prompt.md batch spec step 3: truncation is a harness fact the model cannot
    # see, so it is enforced here rather than trusted.
    if truncated_by_harness and result.get("text_completeness") == "full":
        issues.append("harness truncated the text but the model reported "
                      "text_completeness='full'; treating it as 'truncated'")
        result["text_completeness"] = "truncated"
        result["text_completeness_source"] = "harness"

    # The truncation ladder ran out. pe.prepare wrote this into the manifest and
    # nothing read it, so the flag was decoration: both papers that hit it on the
    # 392-paper run were capped at "unclear"/degraded_text like any truncated
    # paper and sorted to triage P4 -- "route to re-fetch, not to reading", which
    # is the WRONG QUEUE. Re-fetching cannot help. The text arrived complete and
    # does not fit the budget even after dropping Discussion, Introduction and
    # every supplement but the largest. prompt.md batch spec step 3 asks for a
    # section-level second pass, and this is what makes that distinction visible
    # to a curator instead of implied by a manifest key nobody reads.
    if needs_section_pass:
        issues.append(
            "harness truncation ran out of ladder (needs_section_pass): the text "
            "does not fit the budget with Methods preserved, so the cap at "
            "'unclear' is NOT a fetch problem and re-fetching will not change it. "
            "prompt.md batch spec step 3: run a section-level second pass")
        result["needs_section_pass"] = True

    # ---- quote verification, pruning, reference renumbering ----------------
    # The loops moved to task/rules.py: they are mostly field NAMES, and
    # threading a dozen of them plus their message wording through here would
    # have put the task vocabulary back into the harness in a less readable
    # form. What stays generic is the thing passed IN -- verifying a quote
    # against the source it claims, and correcting the attribution when it
    # verifies elsewhere.
    def verify(quote: str, claimed_source: str) -> dict:
        return verify_quote_sourced(quote, claimed_source, sources_text, threshold)

    ctx = validate_items(result, verify, issues, evidence_flags)
    kept, dropped = ctx["kept"], ctx["dropped"]
    checked, failed, wrong_source = ctx["checked"], ctx["failed"], ctx["wrong_source"]

    # ---- suppressed candidates (v0.0.10) ----------------------------------
    # Placed after the perturbation loop so its quotes join the same counters,
    # and before the recomputation only for readability: it writes nothing the
    # recomputation reads. See `_validate_suppressed`.
    suppressed, s_checked, s_failed, s_wrong = validate_secondary(
        result, verify, issues, evidence_flags)
    if SECONDARY_PATH:
        result[SECONDARY_PATH] = suppressed
    checked += s_checked
    failed += s_failed
    wrong_source += s_wrong

    # ---- consistency + recomputation --------------------------------------
    cc_codes = consistency_checks(result)
    for code in HARNESS_RAISED_CHECKS:
        if code in evidence_flags:
            cc_codes.append(code)
    for code in cc_codes:
        issues.append(f"{code}: {CC_TEXT[code]}")

    model_flags = [str(f) for f in (result.get("consistency_flags") or [])]
    undeclared = sorted(set(cc_codes) - set(model_flags))
    if undeclared:
        issues.append(f"consistency_flags did not declare {undeclared} "
                      f"(model reported {model_flags or 'none'})")

    final, a_result, capped = decide(result)

    result[MODEL_FIELD] = model_present
    result[PRIMARY_FIELD] = final if final is not None else model_present
    # Both kept, and their disagreement rate over a corpus is the direct measure
    # of fabricated evidence: a determination resting on a hallucinated quote
    # must not survive the removal of that quote.
    result[f"{PRIMARY_FIELD}_final"] = result[PRIMARY_FIELD]

    # unresolved_reason: "none" unless the final call is unclear; Stage B owns
    # the degraded_text case outright.
    if capped:
        result["unresolved_reason"] = REASON_RULES["cap_reason"]
    elif result[PRIMARY_FIELD] != "unclear" and result.get("unresolved_reason") != "none":
        issues.append(f"unresolved_reason={result.get('unresolved_reason')!r} but "
                      f"{PRIMARY_FIELD}={result[PRIMARY_FIELD]!r}; "
                      f"forcing 'none'")
        result["unresolved_reason"] = "none"
    elif result[PRIMARY_FIELD] == "unclear" and result.get("unresolved_reason") in (None, "none"):
        issues.append(f"{PRIMARY_FIELD}='unclear' with unresolved_reason='none'; "
                      "the unclear bucket is not triageable without a reason")

    if final is not None and model_present != final:
        issues.append(
            f"determination recomputed: model said {model_present!r}, harness Stage "
            f"A/B over the pruned evidence gives {final!r}")

    result["validation"] = {
        "quotes_checked": checked,
        "quotes_failed": failed,
        "quotes_wrong_source": wrong_source,
        f"{ITEM_PATH}_kept": len(kept),
        f"{ITEM_PATH}_dropped": len(dropped),
        # The task's own counters, in the order the record has always carried
        # them. Interleaved rather than appended because the records on disk are
        # compared byte for byte: `validation`'s key order is part of the output.
        **metrics(result, {"kept": kept, "dropped": dropped,
                           "secondary": suppressed,
                           "secondary_checked": s_checked,
                           "secondary_failed": s_failed}),
        "stage_a": a_result,
        "stage_b_capped": capped,
        "determination_changed_by_harness": final is not None and model_present != final,
        "consistency_flags": cc_codes,
        "evidence_flags": sorted(evidence_flags),
        "issues": issues,
        "threshold": threshold,
        # One version, plus the hash that says whether the rules were identical.
        # `prompt_version` is an alias written from the same variable, kept
        # because every preserved baseline and all 392 scored records are keyed
        # on that name -- dropping it would make them incomparable, which is a
        # re-score nobody asked for. Written together, so they cannot drift.
        "task_version": version,
        "prompt_version": version,
        "task_version_source": ("legacy_schema_version" if version_is_legacy
                                else "record"),
        "pack_sha256": pack_sha256,
        "model_id": model_id,
        "sources_verified_against": sorted(source_ids),
    }
    # User decision: every paper gets human review regardless of confidence.
    result["needs_review"] = True
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", default=str(work_default()))
    parser.add_argument("--threshold", type=float, default=None,
                        help="overrides config.yaml fuzzy_match.threshold")
    parser.add_argument("--config", default=str(ROOT / "config.yaml"))
    parser.add_argument("--prompt", default=str(ROOT / "prompt.md"))
    parser.add_argument("--corpus", default=None,
                        help="overrides config.yaml corpus_dir (default ./corpus)")
    parser.add_argument("--write-corpus", action="store_true",
                        help="also write the per-paper result into the corpus tree")
    args = parser.parse_args()

    config = {}
    if yaml and Path(args.config).exists():
        config = yaml.safe_load(Path(args.config).read_text()) or {}
    threshold = args.threshold
    if threshold is None:
        threshold = (config.get("fuzzy_match") or {}).get("threshold", 0.85)
    # Read the same key pe.prepare reads. This used to be a hardcoded "./corpus"
    # while prepare honoured `corpus_dir`, so a config pointing anywhere else made
    # prepare read the right tree and --write-corpus mkdir a wrong one beside the
    # cwd -- which is how the 382 stale v0.0.8 records under
    # .claude/skills/perturbation-detection/corpus/ came to exist.
    corpus = Path(args.corpus or config.get("corpus_dir") or "./corpus")

    pack = None
    try:
        pack = load_pack()
        expected_schema, pack_sha = pack.version, pack.sha256()
    except PackError as exc:
        # Named, not defaulted. A run that cannot say which rules it is applying
        # must say THAT, rather than grading records against nothing.
        print(f"  WARNING: {exc}; task_version cannot be checked", file=sys.stderr)
        expected_schema, pack_sha = "unknown", None
    # The fallback for a manifest entry that records no version of its own. It
    # used to read the spec's `Version:` line -- which is a placeholder since
    # 0.0.13, so this would have stamped the literal "{{TASK_VERSION}}" onto a
    # record. The pack is the one declaration; there is nothing else to read.
    version = expected_schema

    # From the pack, once, rather than per paper. `pe.prepare` wrote it; this is
    # the other half of that agreement.
    marker = pack.read_back_marker if pack is not None else None
    work = resolve_run_dir(Path(args.work))
    manifest = load_manifest(work)
    (work / "validated").mkdir(parents=True, exist_ok=True)
    if args.write_corpus:
        # Named before the first write, not after. --write-corpus creates
        # directories, so a wrong corpus root is silent unless it is announced.
        print(f"--write-corpus: {corpus.resolve()}")

    done = missing = broken = 0
    for entry in manifest:
        doi = entry["doi"]
        if "error" in entry:
            continue
        prompt_file, raw_file = entry_paths(entry, work)
        if not raw_file.exists():
            print(f"  PENDING {doi}: no raw output yet")
            missing += 1
            continue

        try:
            result = parse_raw(raw_file.read_text())
        except (ValueError, json.JSONDecodeError) as exc:
            print(f"  BAD JSON {doi}: {exc}")
            broken += 1
            continue

        result.setdefault("paper_id", doi)
        try:
            paper_text = paper_text_from_prompt(prompt_file, marker)
        except (FileNotFoundError, ValueError) as exc:
            # Unguarded, this aborted the entire run on the FIRST such paper and
            # wrote nothing to validated/ -- including for every paper already
            # verified. The raw-JSON read beside it had always degraded politely;
            # this one had not. A quote cannot be verified against text nobody
            # has, so the paper is skipped and named, not scored.
            print(f"  NO PROMPT {doi}: {type(exc).__name__} {prompt_file} — cannot "
                  f"verify quotes without the text the model was shown")
            broken += 1
            continue
        sources_text = split_assembled(paper_text)
        # The manifest records the version the prompts were built from; prefer it
        # over whatever prompt.md says right now.
        entry_version = entry.get("prompt_version") or version
        result = validate_result(
            result, sources_text, threshold, entry_version,
            truncated_by_harness=bool(entry.get("truncation", {}).get("truncated")),
            expected_schema=expected_schema, model_id=model_of(work, doi),
            pack_sha256=pack_sha,
            needs_section_pass=bool(
                entry.get("truncation", {}).get("needs_section_pass")))

        payload = json.dumps(result, indent=2)
        (work / "validated" / f"{doi}.json").write_text(payload)
        if args.write_corpus:
            corpus_out = (corpus / doi / "extracted"
                          / output_name("per_paper_file"))
            corpus_out.parent.mkdir(parents=True, exist_ok=True)
            corpus_out.write_text(payload)
        done += 1

        # Which conditions are worth shouting about is the pack's call, not the
        # harness's -- STAGE-B-CAP and ASSAY-FILTERED mean nothing to a different
        # question.
        print(progress_line(doi, result))

    print(f"\nvalidated {done} | pending {missing} | unparseable {broken}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RunError as exc:
        print(f"pe.validate: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
