#!/usr/bin/env python3
"""Stage 1: build one self-contained prompt file per paper.

    python -m pe.prepare [--set validation_set.txt] [--work work/]

Writes work/prompts/<doi>.txt (ready to hand to a Claude subagent verbatim)
and work/manifest.json. No LLM calls.

v0.0.5: {{PAPER_TEXT}} is now a multi-source assembly with <<<SOURCE>>> markers
rather than one flat string, {{SOURCE_IDS}} is filled, and the manifest records
the per-source sha256 / char_count that prompt.md's batch spec step 1 requires
and that step 7 keys idempotency on.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pe.paper_text import (  # noqa: E402
    EXCLUDE_SECTIONS, INCLUDE_KINDS, assemble_paper_text, build_sources,
    read_blocks_jsonl,
)

try:
    import yaml
except ImportError:  # config is optional; defaults live in paper_text.py
    yaml = None

from pe.runroot import work_default  # noqa: E402
from pe.pack import PackError, load as load_pack, spec_version_line  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# Assembled-paper-text budget in characters. Scientific prose runs ~3 chars per
# token, so 400k chars is ~100k tokens -- comfortable inside a subagent's window
# alongside the instructions, and reachable in ~11 Read calls. Nothing in the
# 40-paper validation set exceeds it once supplementary duplicates are dropped
# (the largest, science.abl4290, lands at ~373k), so the truncation ladder below
# is dormant here and exists for the 392-paper corpus run.
DEFAULT_BUDGET_CHARS = 400_000


def build_template(pack) -> str:
    """Instruction block + output schema, still holding the {{...}} placeholders.

    The spec document keeps the schema *outside* the fenced instruction block
    even though the instruction says "matching the schema below", so it has to
    be spliced in ahead of PAPER_ID or the model never sees it.

    **This is the pack/harness interface, and it used to be five bare string
    literals across two modules with nothing declaring them.** Three heading
    anchors here, three placeholder names here, and a read-back marker in
    `pe.validate` -- so a replacement spec that renamed a heading failed with an
    unmessaged `ValueError` from a `str.index`, and the only way to learn the
    contract was to read the slicing code. They now come from
    `task.yaml: spec.anchors` / `spec.placeholders`, and every violation names
    the file, the anchor, and what to do about it.
    """
    src = pack.spec_path.read_text()

    def anchor(key: str) -> int:
        needle = pack.anchors[key]
        at = src.find(needle)
        if at < 0:
            raise PackError(
                f"{pack.spec_path.name} has no {needle!r} heading, which "
                f"task.yaml declares as spec.anchors.{key}. Either add the "
                f"heading or point the anchor at the one this spec uses.")
        return at

    head = anchor("instruction")
    match = re.search(r"```\s*\n(.*?)\n\s*```", src[head:], re.DOTALL)
    if not match:
        raise PackError(
            f"{pack.spec_path.name}: no fenced ``` block after "
            f"{pack.anchors['instruction']!r}. The instruction the model is "
            f"given is the contents of that block.")
    instruction = match.group(1)

    start, end = anchor("schema_start"), anchor("schema_end")
    if end <= start:
        raise PackError(
            f"{pack.spec_path.name}: {pack.anchors['schema_end']!r} appears "
            f"before {pack.anchors['schema_start']!r}, so the schema slice would "
            f"be empty. The schema section must precede the end anchor.")
    schema = src[start:end].strip()

    marker = f"PAPER_ID: {pack.placeholders['paper_id']}"
    if marker not in instruction:
        raise PackError(f"the instruction block has no {marker!r} line")
    for key in ("paper_text", "source_ids"):
        placeholder = pack.placeholders[key]
        if placeholder not in instruction:
            raise PackError(
                f"the instruction block has no {placeholder} placeholder "
                f"(task.yaml spec.placeholders.{key})")
    before, after = instruction.split(marker, 1)
    return f"{before.rstrip()}\n\n{schema}\n\n---\n\n{marker}{after}"


# prompt.md batch spec step 3: the fixed order in which content is dropped when
# the assembled text will not fit. References and back matter are already gone
# before this ladder starts; Methods, Results, figure legends and the abstract
# are never dropped.
_TRUNCATION_LADDER = (
    ((), False),
    (("discussion",), False),
    (("discussion", "introduction", "background"), False),
    (("discussion", "introduction", "background"), True),
)


def sources_within_budget(blocks, exclude, include, include_supplementary, budget):
    """Apply the truncation ladder until the assembly fits, or the ladder ends.

    Returns (sources, stats, truncation) where truncation records which rung was
    used. `text_completeness` is the model's call per prompt.md Step 0, but a
    harness-applied truncation is a fact the model cannot see, so it is recorded
    here and enforced by pe.validate.
    """
    last = None
    for rung, (extra_sections, largest_supp_only) in enumerate(_TRUNCATION_LADDER):
        sources, stats = build_sources(
            blocks, tuple(exclude) + extra_sections, include, include_supplementary)

        if largest_supp_only:
            supp = [s for s in sources if s["source_type"] == "supplementary"]
            if len(supp) > 1:
                keep = max(supp, key=lambda s: s["char_count"])
                sources = [s for s in sources if s["source_type"] != "supplementary"]
                keep["source_id"] = "supp1"
                sources.append(keep)
                stats = dict(stats, chars=sum(s["char_count"] for s in sources))

        total = sum(s["char_count"] for s in sources)
        last = (sources, stats, {
            "rung": rung,
            "dropped_sections": list(extra_sections),
            "largest_supp_only": largest_supp_only,
            "truncated": rung > 0,
            "chars": total,
        })
        if total <= budget:
            return last

    # Budget unreachable without cutting Methods. prompt.md says to mark the
    # paper for a section-level second pass rather than truncating blindly.
    sources, stats, truncation = last
    truncation["needs_section_pass"] = True
    return sources, stats, truncation


def main() -> int:
    parser = argparse.ArgumentParser()
    # Required rather than defaulted. The old default, `validation_set.txt`, has
    # never existed in this skill, so `python -m pe.prepare` with no arguments
    # failed with a FileNotFoundError naming a file nobody could have created on
    # purpose. SKILL.md's `--set papers.txt` names one that does not ship either;
    # the sets that do are papers-6/30/50/50b/all.txt.
    parser.add_argument("--set", required=True,
                        help="file of paper directory names, one per line "
                             "(e.g. papers-30.txt)")
    parser.add_argument("--corpus", default=None, help="overrides config.yaml corpus_dir")
    parser.add_argument("--config", default=str(ROOT / "config.yaml"))
    parser.add_argument("--work", default=str(work_default()))
    # No --prompt. The spec's path is `task.yaml: spec.path`, so pointing this
    # module at a different file without its anchors and placeholders would fail
    # in the slicing code rather than at the flag. Swap the pack, not the file.
    parser.add_argument("--budget", type=int, default=None,
                        help=f"assembled text char budget (default {DEFAULT_BUDGET_CHARS:,})")
    parser.add_argument("--no-supplementary", action="store_true",
                        help="main text only (prompt.md v0.0.5 toggle)")
    args = parser.parse_args()

    config = {}
    config_path = Path(args.config)
    if yaml and config_path.exists():
        config = yaml.safe_load(config_path.read_text()) or {}

    exclude = tuple(config.get("exclude_sections") or EXCLUDE_SECTIONS)
    include = tuple(config.get("include_kinds") or INCLUDE_KINDS)
    include_supp = not args.no_supplementary
    if include_supp and "include_supplementary" in config:
        include_supp = bool(config["include_supplementary"])
    budget = args.budget or config.get("budget_chars") or DEFAULT_BUDGET_CHARS

    pack = load_pack()
    template = build_template(pack)
    # Stamped into the manifest so pe.validate can report the version the
    # EXTRACTION ran under. Reading the spec at validate time instead means any
    # re-validation after a version bump silently relabels old results as new.
    stamp = pack.stamp()
    built_version = stamp["task_version"]
    # The spec's own `Version:` line is a placeholder now, so this asserts the
    # substitution below actually happened rather than reading a stale literal.
    declared = spec_version_line(pack.spec_path)
    if declared != pack.placeholders["task_version"]:
        raise PackError(
            f"{pack.spec_path.name}'s `Version:` line reads {declared!r}, but the "
            f"single declaration of the version is task.yaml. Put "
            f"{pack.placeholders['task_version']} there instead -- a literal in "
            f"the spec is the drift that 0.0.13 removed.")
    corpus = Path(args.corpus or config.get("corpus_dir")
                  or "./corpus")
    # Resolved to absolute: the manifest records raw_file/prompt_file as strings,
    # and a relative --work made those readable only from the cwd that created
    # them. Running pe.validate/pe.pending from anywhere else then reported every
    # paper as missing.
    work = Path(args.work).resolve()
    (work / "prompts").mkdir(parents=True, exist_ok=True)
    (work / "raw").mkdir(parents=True, exist_ok=True)

    dois = [line.strip() for line in Path(args.set).read_text().splitlines() if line.strip()]
    manifest = []
    print(f"task {pack.name} {pack.version}  pack {stamp['pack_sha256'][:12]}")
    print(f"supplementary sources: {'INCLUDED (deduped)' if include_supp else 'EXCLUDED'} "
          f"| budget {budget:,} chars\n")

    for doi in dois:
        article_dir = corpus / doi
        blocks = read_blocks_jsonl(article_dir)
        if not blocks:
            print(f"  SKIP {doi}: no blocks.jsonl", file=sys.stderr)
            # prompt.md batch spec step 1: a paper that was never retrieved must
            # never be silently absent from the output.
            manifest.append({"doi": doi, "error": "no blocks.jsonl",
                             "fetch_status": "not_found"})
            continue

        sources, stats, truncation = sources_within_budget(
            blocks, exclude, include, include_supp, budget)
        paper_text = assemble_paper_text(sources)
        source_ids = ", ".join(s["source_id"] for s in sources)

        # Only the LAST {{PAPER_TEXT}} is the injection point. The instruction
        # block also mentions `{{PAPER_TEXT}}` as prose in Step 0 ("may be
        # incomplete"), and a blanket str.replace spliced the whole paper in
        # there too -- doubling every prompt file and burying the instructions.
        head, tail = template.rsplit(pack.placeholders["paper_text"], 1)
        filled = (f"{head}{paper_text}{tail}"
                  .replace(pack.placeholders["paper_id"], doi)
                  .replace(pack.placeholders["source_ids"], source_ids)
                  # The version the model echoes, substituted rather than
                  # written into the spec. This is what makes "one version"
                  # structural instead of a rule somebody has to remember.
                  .replace(pack.placeholders["task_version"], built_version))
        prompt_file = work / "prompts" / f"{doi}.txt"
        prompt_file.write_text(filled)

        manifest.append({
            "doi": doi,
            "paper_id": doi,
            "fetch_status": "ok",
            **stamp,
            # Kept as an alias of task_version, written from the same variable so
            # the two cannot drift. Every preserved baseline and every one of the
            # 392 scored records is keyed on this name; dropping it would make
            # them incomparable, which is a re-score nobody asked for.
            "prompt_version": built_version,
            "prompt_file": str(prompt_file),
            # Recorded so subagents know whether one Read call covers the file
            # or whether they must page through it.
            "prompt_lines": filled.count("\n") + 1,
            # The whole file, not just the paper text -- the stage-2 agent sizes
            # its Read pages from chars-per-line, and using paper chars against
            # whole-file lines underestimates it and overshoots the token cap.
            "prompt_chars": len(filled),
            "raw_file": str(work / "raw" / f"{doi}.json"),
            "blocks_total": len(blocks),
            "chars": stats["chars"],
            "source_ids": [s["source_id"] for s in sources],
            "sources": [{
                "source_id": s["source_id"],
                "source_type": s["source_type"],
                "path": s["source_file"],
                "extractor": "pdf_text" if s["source_file"] != "main" else "blocks_jsonl",
                "sha256": hashlib.sha256(s["text"].encode()).hexdigest(),
                "char_count": s["char_count"],
                "references_stripped": s["references_stripped"],
            } for s in sources],
            "assembled_text_sha256": hashlib.sha256(paper_text.encode()).hexdigest(),
            "truncation": truncation,
            "assembly_stats": {k: v for k, v in stats.items() if v not in ([], 0)},
        })
        supp_note = ""
        if stats.get("supp_duplicate_chars"):
            supp_note += f"  dedup -{stats['supp_duplicate_chars']:,}"
        if stats.get("supp_dropped_admin"):
            supp_note += f"  admin-drop {len(stats['supp_dropped_admin'])}"
        if truncation["truncated"]:
            supp_note += f"  TRUNCATED(rung {truncation['rung']})"
        if truncation.get("needs_section_pass"):
            supp_note += "  NEEDS-SECTION-PASS"
        print(f"  {doi:38} {stats['chars']:>8,} chars  "
              f"[{len(sources)} src: {source_ids}]{supp_note}")

    (work / "manifest.json").write_text(json.dumps(manifest, indent=2))
    ok = [m for m in manifest if "error" not in m]
    total = sum(m["chars"] for m in ok)
    n_supp = sum(1 for m in ok if len(m["source_ids"]) > 1)
    print(f"\n{len(ok)}/{len(dois)} prepared | {total:,} chars (~{total // 4:,} tokens) "
          f"| {n_supp} with supplementary | manifest: {work / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PackError as exc:
        print(f"pe.prepare: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
