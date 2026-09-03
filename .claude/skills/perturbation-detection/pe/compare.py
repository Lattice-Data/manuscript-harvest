#!/usr/bin/env python3
"""Version-to-version comparison for prompt.md's validation loop step 1.

    python -m pe.compare --baseline <dir> [--out output/<a>_vs_<b>.txt]

The prompt is explicit that this diff is NOT expected to be empty, and that it
should be *classified* rather than merely counted:

    "Expect movement in exactly two classes: papers capped by Stage B, and
     papers with has_single_cell_assay = 'unclear' plus a 'yes' pairing (CC-5).
     Any change outside those two classes is a bug in this version, not a
     refinement, and should be investigated before the corpus run."

This run adds a third legitimate class the prompt's changelog does not mention,
because the v0.0.4 baseline was main-text-only while v0.0.5 supplies deduplicated
supplementary sources (prompt.md's default): a paper can move because the model
finally saw the Methods. That class is labelled SUPP-EVIDENCE and is evidenced by
a verified quote carrying a supplementary source_id. Anything left over is
labelled UNEXPLAINED and is what the prompt says to investigate.

Two later additions, both of which were previously landing in UNEXPLAINED and so
reading as logic bugs:

  STAGE-B-RELEASED — Stage B's cap is symmetric, but only its ENTRY was
    classified. A paper leaving the cap (`stage_b_capped` True -> False, which is
    what a completed re-extraction looks like) had no class. Observed on
    10.1126/science.adf5357.
  SUPPRESSED — v0.0.10 lets a paper move because a candidate was recorded in
    `suppressed_candidates` instead of `perturbations`. The class only fires when
    a baseline perturbation can actually be matched to a new suppressed
    candidate; an unmatched suppression explains nothing.
  WITHIN-NOISE — the paper also disagrees with ITSELF across two runs of the
    baseline prompt over byte-identical input, so this "change" is not evidence of
    one. Needs --baseline2. Added after the v0.0.12 acceptance test, where 3 of 4
    apparent movements turned out to be run-to-run variance and the single-run
    baseline could not say so: attribution was impossible, not negative.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pe.runroot import output_default, output_name, work_default  # noqa: E402
from pe.runstate import RunError, load_manifest, resolve_run_dir  # noqa: E402

# The change classes, their labels, and the predicates that decide which one
# accounts for a movement are the pack's: `task/change.yaml` and
# `task/change.py`. This module loads two runs, refuses an empty overlap, builds
# the confusion matrix, computes the noise floor and warns about UNEXPLAINED --
# none of which knows what a perturbation is.
from task.change import (  # noqa: E402
    CLASS_LABELS, DIFF_PREAMBLE, NOISE_CLASS, ORDER, PRIMARY_FIELD,
    PRIMARY_FIELD_GLOSS, UNEXPLAINED, classify, render_paper, render_unchanged,
)


def noise_floor(pairs: list[tuple[str, dict, dict]]) -> tuple[set[str], str | None]:
    """Papers two runs of the SAME prompt disagree about, plus a refusal reason.

    `pairs` is [(doi, run_a, run_b)]. Returns (unstable dois, error) -- error is a
    message when the two runs are not the same prompt version, in which case the
    caller must refuse rather than report a version diff as variance. That
    inversion would launder a real effect into "nothing moved", which is the
    opposite of what the floor is for, and it is checked because the flag's author
    made exactly that mistake on first use.
    """
    if not pairs:
        return set(), "no overlapping papers"
    va = {str((a.get("validation") or {}).get("prompt_version")) for _, a, _ in pairs}
    vb = {str((b.get("validation") or {}).get("prompt_version")) for _, _, b in pairs}
    if va != vb:
        return set(), (f"second run is prompt v{'/'.join(sorted(vb))} but the first is "
                       f"v{'/'.join(sorted(va))}; a noise floor needs the same prompt")
    return ({doi for doi, a, b in pairs
             if a.get(PRIMARY_FIELD) != b.get(PRIMARY_FIELD)}, None)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", default=str(work_default()))
    parser.add_argument("--baseline", required=True,
                        help="directory with validated/<doi>.json from the earlier run")
    parser.add_argument("--baseline2", default=None,
                        help="second run of the SAME baseline prompt. Papers where the "
                             "two baseline runs disagree are the noise floor, and a "
                             "change confined to them is not evidence of an effect.")
    # Not "v004_vs_v005.txt": that default outlived the versions in its own name by
    # seven releases. The versions are in the report's first line, read from the
    # records.
    parser.add_argument("--out", default=str(output_default(output_name("diff_txt"))))
    args = parser.parse_args()

    work = resolve_run_dir(Path(args.work))
    # `--baseline` takes r1 and `--baseline2` takes r2 when handed a two-run
    # baseline directory, which is the only reading of that pair that makes sense:
    # the first run IS the baseline and the second is what its self-disagreement
    # is measured against. Before this, only --baseline2 knew about the layout, so
    # `--baseline <that dir>` found no validated/, matched zero papers, and printed
    # "every change is accounted for" over an empty set.
    baseline = resolve_run_dir(Path(args.baseline), prefer="r1")
    manifest = load_manifest(work)

    rows = []
    missing_new, missing_old = [], []
    for entry in manifest:
        if "error" in entry:
            continue
        doi = entry["doi"]
        new_file = work / "validated" / f"{doi}.json"
        old_file = baseline / "validated" / f"{doi}.json"
        if not old_file.exists():
            old_file = baseline / "raw" / f"{doi}.json"
        if not new_file.exists():
            missing_new.append(doi)
            continue
        if not old_file.exists():
            missing_old.append(doi)
            continue
        rows.append((doi, json.loads(old_file.read_text()),
                     json.loads(new_file.read_text()), entry))

    # The rule this module exists to stop breaking. A verdict over zero papers
    # reads exactly like a clean one, and this is the acceptance gate for a prompt
    # version -- so it refuses rather than reporting.
    if not rows:
        raise RunError(
            f"no paper appears in BOTH runs. {work} has "
            f"{len(manifest) - len(missing_new)} validated record(s) of "
            f"{len(manifest)}; {baseline} supplied none of them "
            f"({len(missing_old)} missing there). Refusing to report a comparison "
            f"over an empty set -- it is indistinguishable from 'nothing changed'.")

    # Papers the baseline cannot even agree with itself about. Anything confined
    # to this set is variance, not an effect -- the distinction the v0.0.12
    # acceptance test needed and a single-run baseline structurally cannot make.
    noise: set[str] = set()
    noise_available = False
    if args.baseline2:
        b2 = resolve_run_dir(Path(args.baseline2), prefer="r2")
        pairs = []
        for doi, old, _, _ in rows:
            f2 = b2 / "validated" / f"{doi}.json"
            if f2.exists():
                pairs.append((doi, old, json.loads(f2.read_text())))
        # A noise floor is the disagreement between two runs of the SAME prompt.
        # Handing this flag a different VERSION computes a version diff and calls
        # it variance, which would launder a real effect into "nothing moved" --
        # the exact inversion this flag exists to prevent. So it is checked, not
        # documented. (Caught by making this mistake on first use.)
        noise, err = noise_floor(pairs)
        if err:
            print(f"--baseline2: {err}. Comparing versions here would report a real "
                  f"effect as variance. Refusing.", file=sys.stderr)
            return 2
        noise_available = True

    matrix = Counter((old.get(PRIMARY_FIELD), new.get(PRIMARY_FIELD))
                     for _, old, new, _ in rows)

    lines: list[str] = []
    versions = {str((new.get("validation") or {}).get("prompt_version")) for _, _, new, _ in rows}
    base_versions = {str((old.get("validation") or {}).get("prompt_version"))
                     for _, old, _, _ in rows} or {"?"}
    lines.append(f"baseline v{'/'.join(sorted(base_versions))} -> "
                 f"v{'/'.join(sorted(versions))} comparison over {len(rows)} paper(s)")
    covered = f"coverage: {len(rows)}/{len(manifest)} manifest paper(s) compared"
    if missing_new or missing_old:
        covered += (f"; {len(missing_new)} not validated in {work.name}"
                    f", {len(missing_old)} absent from {baseline.name}")
    lines.append(covered)
    lines.append(f"  new:      {work}")
    lines.append(f"  baseline: {baseline}")
    lines.append("")
    lines.append(f"Both columns are {PRIMARY_FIELD_GLOSS}, so this is a")
    lines.append("like-for-like diff of the primary curation field.")
    # The rest of this preamble is the PACK's, because the caveat worth printing
    # depends on the question. This block used to end by telling every reader to
    # check the SUPP-EVIDENCE class -- a change class only one pack declares, so
    # a second pack's report pointed at a class absent from its own table.
    lines.extend(DIFF_PREAMBLE)
    lines.append("")
    b_lbl = "/".join(sorted(base_versions))
    n_lbl = "/".join(sorted(versions))
    header = f"  {b_lbl} \\ {n_lbl}   " + "".join(f"{c:>10}" for c in ORDER) + "     total"
    lines.append(header)
    lines.append("  " + "-" * (len(header) - 2))
    for old_call in ORDER:
        cells = [matrix.get((old_call, new_call), 0) for new_call in ORDER]
        lines.append(f"  {old_call:>16}   " + "".join(f"{c:>10}" for c in cells)
                     + f"     {sum(cells):>5}")
    lines.append("  " + "-" * (len(header) - 2))
    totals = [sum(matrix.get((o, n), 0) for o in ORDER) for n in ORDER]
    lines.append("             total   " + "".join(f"{c:>10}" for c in totals)
                 + f"     {sum(totals):>5}")

    changed = [(doi, old, new, entry) for doi, old, new, entry in rows
               if old.get(PRIMARY_FIELD) != new.get(PRIMARY_FIELD)]
    lines.append("")
    lines.append(f"unchanged: {len(rows) - len(changed)}/{len(rows)}    "
                 f"changed: {len(changed)}")
    if noise_available:
        in_noise = [doi for doi, _, _, _ in changed if doi in noise]
        real = len(changed) - len(in_noise)
        lines.append(f"noise floor: the baseline disagrees with itself on "
                     f"{len(noise)}/{len(rows)} paper(s)")
        lines.append(f"changed BEYOND the noise floor: {real}"
                     + (f"   (within noise: {', '.join(in_noise)})" if in_noise else ""))
        if not real and changed:
            lines.append("  -> every apparent change is run-to-run variance. Nothing "
                         "is shown to have moved.")

    if not noise_available and changed:
        lines.append("noise floor: NOT AVAILABLE -- the baseline has only one run, so a "
                     "single-paper movement cannot be told from run-to-run variance. "
                     "Pass --baseline2 with a second run of the same prompt.")

    class_counts = Counter(c for doi, old, new, _ in changed
                           for c in ([NOISE_CLASS] if doi in noise
                                     else classify(new, old)))
    lines.append("")
    lines.append("change classes (a paper can fall in more than one)")
    for code, label in CLASS_LABELS.items():
        if class_counts.get(code):
            lines.append(f"  {code:<15} {class_counts[code]:>3}   {label}")
    unexplained = [doi for doi, old, new, _ in changed
                   if doi not in noise and classify(new, old) == [UNEXPLAINED]]
    if unexplained:
        lines.append("")
        lines.append(f"  !! {len(unexplained)} {UNEXPLAINED} change(s): {', '.join(unexplained)}")
        lines.append("     prompt.md validation loop step 1 says to investigate these "
                     "before the corpus run.")
    else:
        lines.append("")
        lines.append("  every change is accounted for by a known mechanism "
                     f"(see the class list above) over {len(rows)} compared paper(s).")

    lines.append("")
    lines.append("=" * 78)
    lines.append("CHANGED PAPERS")
    lines.append("=" * 78)
    for doi, old, new, entry in changed:
        # Every line of this block names a field only this task has, so it is
        # the pack's to render. The harness decides WHICH papers appear here.
        lines.extend(render_paper(doi, old, new, entry, classify(new, old)))

    lines.append("")
    lines.append("=" * 78)
    lines.append("UNCHANGED PAPERS")
    lines.append("=" * 78)
    for doi, old, new, entry in rows:
        call = new.get(PRIMARY_FIELD)
        if old.get(PRIMARY_FIELD) != call:
            continue
        lines.append(render_unchanged(doi, new, entry))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n")
    cut = lines.index("=" * 78) if "=" * 78 in lines else len(lines)
    print("\n".join(lines[:cut]))
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RunError as exc:
        print(f"pe.compare: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
