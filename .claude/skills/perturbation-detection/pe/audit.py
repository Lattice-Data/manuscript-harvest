#!/usr/bin/env python3
"""Stage 5 (review aid): target the papers prompt.md says to check first.

    python -m pe.audit [--work work/] [--out output/review_screen.txt]

prompt.md's validation loop (added in v0.0.3) asks for three things. This produces
those three as Screens A, B and C, plus three more that later versions needed --
D (Stage-B caps), E (supplementary-only evidence and quote attribution) and
F (suppressed candidates). Six in total; all of them mechanical.

  Screen A — assay-pairing disagreements. Papers where
    `perturbation_present_any_assay = yes` but `perturbation_present` is
    no/unclear: the assay-pairing requirement alone flipped the call. v0.0.3
    calls this "the highest-value QA pass since it's exactly the failure mode
    this version targets." Prints each perturbation's pairing, assay, and
    assay-pairing quote so the call can be checked without opening the paper.

  Screen B — possible missed assay. Papers with `has_single_cell_assay` no or
    unclear, grepped for qualifying single-cell/nucleus assay names. A hit means
    the assay taxonomy may have been applied too strictly.

  Screen C — possible missed perturbation. Papers with
    `perturbation_present_any_assay = no`, grepped for perturbation language.
    This is the pre-v0.0.3 false-negative screen, still useful.

  Screen F — suppressed candidates (v0.0.10). Every candidate the NOT list
    swallowed, with the `would_have_paired = "yes"` rows first: those are papers
    where one toggle flips the determination, so they are the review queue for
    the boundary rules themselves rather than for the model's reading of a paper.
    Unlike B and C this is not a keyword grep — the model told us it made these
    calls, which is exactly what v0.0.9 could not do.

All six are SCREENS, not verdicts. A keyword hit is not proof of an error:
"treated with" also describes routine processing, and "single-cell suspension"
is a dissociation step, not an assay — the two traps this task turns on.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pe.pack import read_back_marker  # noqa: E402
from pe.validate import paper_text_from_prompt  # noqa: E402

from pe.runroot import output_default, output_name, work_default  # noqa: E402
from pe.runstate import RunError, entry_paths, load_validated  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# The six screens, their keyword banks and their headings are the pack's:
# `task/report.yaml` for the titles and the signal banks, `task/screens.py` for
# the selectors and the per-paper rendering. Every screen prints different fields
# of a task-specific record, and a tier number once went stale inside a screen
# HEADER because the header was prose rather than a reference to the ladder.
#
# What this module does: read a run, refuse to report on an empty set, recover
# the paper text safely, and write the file.
from task.screens import FOOTER, SCREENS, render  # noqa: E402


def _versions(loaded) -> list[str]:
    """The task versions present among the loaded records.

    More than one means the screens below mix rule sets, which is worth seeing in
    the header rather than discovering from a determination that will not
    reconcile.
    """
    return sorted({str((r.get("validation") or {}).get("task_version")
                       or (r.get("validation") or {}).get("prompt_version") or "?")
                   for _, r, _ in loaded}) or ["?"]


def _paper_text(prompt_file: Path) -> tuple[str, str | None]:
    """The assembled paper text for a screen, or a note saying why not.

    Screens B and C grep the text. A prompt file that has been moved or deleted
    is a fact about the run directory, not a reason to lose the other screens --
    and a screen that silently greps an empty string would report "no qualifying
    assay language found", which reads as evidence FOR the model's answer.
    """
    try:
        return paper_text_from_prompt(prompt_file, read_back_marker()), None
    except (FileNotFoundError, ValueError) as exc:
        return "", f"paper text unavailable ({type(exc).__name__}: {prompt_file})"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", default=str(work_default()))
    parser.add_argument("--out", default=str(output_default(output_name("review_txt"))))
    parser.add_argument("--prompt", default=str(ROOT / "prompt.md"))
    args = parser.parse_args()

    run = load_validated(Path(args.work))
    run.require_papers("pe.audit")

    # entry_paths, not entry["prompt_file"]. Screens B and C grep the paper text,
    # so reading it from the manifest's recorded string means a run directory that
    # was copied -- which the acceptance protocol does, `baseline-v0012-50b`'s
    # manifest still points into `work-accept-v0012-r1/` -- greps a DIFFERENT
    # run's assembly, or crashes if that directory is gone. Deriving from
    # (work, doi) is what makes a manifest portable and is what pe.pending and
    # pe.validate already do.
    loaded: list[tuple[str, dict, Path]] = [
        (entry["doi"], run.records[entry["doi"]], entry_paths(entry, run.work)[0])
        for entry in run.entries if entry["doi"] in run.records
    ]

    lines, counts = render(loaded, _paper_text)

    header = [
        run.coverage(),
        # The version the RECORDS were produced under, not whatever the pack
        # says today: a screen re-run after a version bump would otherwise label
        # old results as new. Read the same way pe.summarize reads it.
        f"Review screen — task v{'/'.join(_versions(loaded))} — "
        f"{len(loaded)} paper(s) validated",
        # Titles from task/report.yaml, so the header cannot describe a screen
        # differently from the screen itself -- which is how Screen D came to
        # cite a triage tier four versions out of date.
        *(f"  Screen {sid} ({SCREENS[sid]['summary']}): {counts[sid]}"
          for sid in sorted(counts)),
        "",
        *FOOTER,
        "",
    ]
    if not run.complete:
        # Said twice on purpose -- in the file and on stdout. A short review
        # screen and a clean one look identical, and the whole point of this
        # pipeline is that emptiness has to account for itself.
        header.insert(1, f"  INCOMPLETE RUN: {run.expected - run.loaded} paper(s) have "
                         f"no validated record; the screens below cover the rest.")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(header + lines) + "\n")
    print("\n".join(header[:9]))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RunError as exc:
        print(f"pe.audit: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
