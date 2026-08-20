---
name: perturbation-detection
description: Classify extracted scientific papers as perturbed / not perturbed / unclear for single-cell biocuration — detecting whether the samples actually profiled by a single-cell or single-nucleus sequencing assay were experimentally perturbed (drug, cytokine, stimulation, knockout/knockdown, hypoxia, diet, etc.). Use when asked to find, detect, score, or curate perturbations across a corpus of papers, to run "the perturbation prompt" or "the perturbation pipeline", to re-score papers under a new prompt version, or to check which papers in a manuscript corpus involve experimental manipulation. Works on directories of extracted paper text (blocks.jsonl), not on PDFs or DOIs directly.
---

# Perturbation detection for single-cell biocuration

Reads extracted paper text and decides, per paper, whether the material that
went into a single-cell/single-nucleus sequencing assay was experimentally
perturbed. Produces a reviewable table plus per-paper JSON with verbatim
evidence quotes.

**The judgment lives in `prompt.md`, not here.** That file is the single source
of truth for what counts as a perturbation, the qualifying assay list, the
pairing rules, the determination logic, and the output schema. Read it before
answering questions about criteria. This file only explains how to run it.

## The one rule that makes this task different

A paper counts as perturbed **only if a perturbed sample was itself profiled by
a single-cell/nucleus sequencing assay.** A perturbation somewhere in the paper
plus a single-cell assay somewhere in the paper is *not* enough. Papers
routinely perturb cells for a bulk RNA-seq / qPCR / Western / flow readout while
the single-cell dataset comes from separate untreated samples — that is a "no".

## Input

A directory with one subdirectory per paper, each containing
`extracted/blocks.jsonl`:

```
corpus/
  10.1038_s41586-024-00000-0/
    extracted/blocks.jsonl
```

Each line is a block with at least `kind`, `section`, `source_file`, `text`.
In this repo, `manuscript-harvest` produces exactly this layout.

## How to run

Four steps. Only step 2 needs a model; the rest is plain Python.

```bash
python -m pe.prepare  --set papers.txt --work work --corpus /path/to/corpus
./pe/run_headless.sh work 4
python -m pe.validate --work work --write-corpus --corpus /path/to/corpus
python -m pe.summarize --work work
```

- `papers.txt` — one paper directory name per line.
- Step 2 runs `claude -p` once per paper. It uses the logged-in Claude Code
  session, **not** an API key. Set `PY=` if `python3` is not the interpreter you
  want.
- To know when a long run has finished: `./pe/watch.sh work` prints progress
  every 30s and raises a desktop notification at the end. `./pe/watch.sh work
  status` prints one line and exits. Both only read state, so they are safe to
  start late or interrupt.
- Everything is resumable. `python -m pe.pending --work work` reports what is
  still missing and why; re-running step 2 picks up only those. A paper counts as
  done only if its result parses, has every required field, and its
  `sources_seen` matches the manifest — so a partial write is re-run, not
  silently accepted.

Alternative for step 2 when working inside an interactive Claude Code session:
`pe/extract_workflow.js` runs one subagent per paper via the Workflow tool.
Faster and gives per-paper progress, but needs an interactive session.

Then review:

```bash
python -m pe.audit --work work      # five targeted review screens
python -m pe.compare --work work --baseline <old_run_dir>   # version-to-version diff
```

## Output

- `output/perturbations_summary.csv` — one row per paper, **sorted by triage
  priority**, so read it top-down:

  | priority | meaning |
  |---|---|
  | P1 | `unclear` because the pairing was never stated — most likely to hide a real match, read first |
  | P2 | `yes` with confidence < 0.6 |
  | P3 | `unclear` because the text was incomplete — send to re-fetch, do not read |
  | P4 | `no` but a perturbation exists elsewhere in the paper — the pairing filter fired; sample these |
  | P5 | any consistency or evidence flag |
  | P9 | everything else |

- `<corpus>/<paper>/extracted/perturbations.json` — full per-paper result with
  evidence quotes (with `--write-corpus`).
- `output/review_screen.txt` — Screens A–E: pairing flips, possibly-missed
  assays, possibly-missed perturbations, incomplete-text papers, and
  supplementary-only evidence.

## Things that will bite you

- **Supplementary files are included but must be deduplicated.** Cell Press
  ships an "accepted manuscript" PDF that is often 83–97% a copy of the article.
  Including it doubles cost and breaks source attribution, because a quote then
  legitimately verifies against two sources. `pe.paper_text.build_sources`
  handles this; don't bypass it.
- **Never `str.replace` a prompt placeholder globally.** `prompt.md` mentions
  `{{PAPER_TEXT}}` twice — once as prose, once as the injection point. Replacing
  both splices the whole paper into the instructions. `pe.prepare` uses
  `rsplit(..., 1)`.
- **Missing text must never read as a negative.** A paper whose text is
  truncated or has no Methods cannot resolve to "no"; it is capped at "unclear"
  with `unresolved_reason = degraded_text`. Positives are not capped — missing
  text can hide evidence but cannot invent it.
- **The determination is recomputed after quote verification.** Any quote that
  cannot be found is dropped; a perturbation left with no verified quote is
  dropped entirely; then the paper-level call is recomputed. Both values are
  kept (`perturbation_present_model` vs `..._final`), and a rising gap between
  them is the early warning for fabricated evidence.
- **Session limits, not papers, are the constraint at scale.** Expect to run a
  large corpus over several sittings, using `pe.pending` between them.
- `table` blocks are deliberately excluded: a Cell Press KEY RESOURCES TABLE
  lists every reagent in the lab, and this task turns on the *role* a reagent
  plays, not its presence.

## Changing the criteria

Edit `prompt.md` and bump its `Version:` line — nothing hardcodes the version.
`pe.validate` mirrors the prompt's stated determination logic in
`stage_a`/`stage_b`, so if you change that logic, change both and run
`tests/test_determination_v005.py`. `prompt.md` also has a toggle table at the
bottom for the recurring boundary calls (reporter-only genetic edits,
observational disease states, spot-based spatial assays, degraded-text
handling).

When re-scoring an existing run under a new prompt version, use `pe.compare`.
It classifies each changed paper by *which determination input moved*, so
"unexplained" means a genuine logic bug rather than a matter of opinion.
