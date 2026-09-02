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
python -m pe.prepare  --set papers.txt --corpus /path/to/corpus
./pe/run_headless.sh
python -m pe.validate --write-corpus --corpus /path/to/corpus
python -m pe.summarize
```

- `papers.txt` — one paper directory name per line.
- **Run artifacts land outside this directory**, under
  `~/.manuscript-harvest/perturbation/{work,output}` by default. That is not
  tidiness: `claude -p` subagents cannot write under `.claude/`, and the CLI
  exits 0 anyway, so a stage-2 result written beside the skill is lost with no
  error. Two of six papers hit this on the first v0.0.9 run. Override the root
  with `PERTURBATION_RUN_ROOT`, or a single run with `--work` / `--out`; an
  explicit path is honoured verbatim. The skill directory keeps only what is
  versioned and shared — `prompt.md`, `pe/`, `config.yaml`.
- Step 2 runs `claude -p` once per paper. It uses the logged-in Claude Code
  session, **not** an API key. Set `PY=` if `python3` is not the interpreter you
  want.
- To know when a long run has finished: `./pe/watch.sh <work_dir>` prints progress
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
python -m pe.audit       # six targeted review screens (A–F)
python -m pe.compare --baseline <old_run_dir>   # version-to-version diff
```

## Output

- `output/perturbations_summary.csv` — one row per paper, **sorted by triage
  priority**, so read it top-down:

  | priority | meaning |
  |---|---|
  | P1 | `unclear` because the pairing was never stated — most likely to hide a real match, read first |
  | P2 | not `yes`, but a suppressed candidate would have paired `yes` — one toggle flips the paper |
  | P3 | `yes` with confidence < 0.6 |
  | P4 | `unclear` because the text was incomplete — send to re-fetch, do not read |
  | P5 | `no` but a perturbation exists elsewhere in the paper — the pairing filter fired; sample these |
  | P6 | any consistency or evidence flag |
  | P9 | everything else |

  **The ladder renumbered at prompt v0.0.10**, when P2 was inserted: the old
  P2–P5 are now P3–P6. Do not compare a priority column across prompt versions
  without checking which one produced it. The list lives in two places on
  purpose — `prompt.md` step 10 and `pe.summarize.triage_priority` — and they
  must be changed together.

- **`suppressed_candidates`** (added in schema 0.0.6, prompt v0.0.10) — one entry per
  thing the model recognised as a possible perturbation and deliberately did not
  list, so the NOT list stops being silent. Each entry carries `candidate`,
  `rule` (a **closed** set of eight values: `reporter_or_marker`,
  `incidental_clinical_therapy`, `unintended_condition`,
  `derivation_formulation`, `observational_disease_state`,
  `sample_handling_protocol`, `readout_reagent`, `routine_processing`), `why`, a
  verified `evidence_quote`, and `would_have_paired`.

  Two things it buys that a free-text `ambiguities` note could not. A curator can
  tell **"considered the transgene and excluded it under the reporter rule"**
  apart from **"never noticed it"** — v0.0.9 could not, and a regression run of
  `10.1038/s44318-024-00328-6` returned the right answer while never mentioning
  the SFTPC-GFP reporter at all. And the corpus becomes **countable**: "how many
  papers did the reporter rule hold back from `yes`?" is now a column
  (`suppressed_rules`, `n_suppressed`, `suppressed_would_pair_yes`) and a corpus
  counter, rather than nine papers of hand-reading.

  `rule` is closed because an open string cannot be tallied, which is the whole
  point. The threshold for an entry is **deliberation, not presence**: a call you
  actually had to make, not every reagent the Methods name.

- `<corpus>/<paper>/extracted/perturbations.json` — full per-paper result with
  evidence quotes (with `--write-corpus`).
- `output/review_screen.txt` — Screens A–F: pairing flips, possibly-missed
  assays, possibly-missed perturbations, incomplete-text papers,
  supplementary-only evidence, and **Screen F, suppressed candidates** — every
  candidate the NOT list swallowed, `would_have_paired = "yes"` rows first. F is
  a review of the *rules* rather than of the model's reading: the model named the
  candidate and judged its pairing, then excluded it. It is also the one screen
  that is not a keyword grep.

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
- **Adding a structured field changed judgment, with no criterion edited.** On
  `suppressed_candidates`' first run, 2 of 6 papers moved `yes` -> `no`:
  `s41586-024-07571-1` (gluten-free diet, n=2 treated vs n=3 untreated, both
  sequenced) and `elife.104978.2` (chemotherapy at diagnosis vs relapse), both
  reclassified as `incidental_clinical_therapy`, both with their `perturbations`
  array emptied outright. Eight named buckets plus a required field made
  suppression the salient action — making a path structured makes it more
  travelled. What fixed it, and what to copy if you add another field: state the
  precedence first (the new field never shortens `perturbations`), say plainly
  that an empty array is normal and common, name the negative examples rather
  than describing them, and hold any judgment sub-field to its parent's evidence
  standard. A generic "do not enumerate the ambient Methods" was ignored twice in
  one paper; the concrete list held.
- **So run the acceptance test twice.** A single run cannot tell an attractor from
  ordinary variance — that is the same lesson v0.0.9 learned from two runs of one
  paper disagreeing. Keep a determination-only baseline (`pe.compare --baseline`),
  and include at least one paper that must NOT populate whatever you added.
- **A suppressed candidate must never move the determination.** It is not a
  perturbation, so it never enters `perturbations` and Stage A cannot see it —
  which is structural, not a convention: `stage_a` reads only
  `processing_status`, `has_single_cell_assay`,
  `perturbation_present_any_assay` and the pairings inside `perturbations`. If a
  suppression ever changes a call, a write escaped `pe.validate._validate_suppressed`.
  `tests/test_suppressed_candidates.py` asserts this over every Stage A input
  combination.
- **An unverifiable suppression quote drops the quote, not the entry.** The
  suppression still happened; deleting the entry would restore exactly the
  silence the field was added to remove. Flagged `EV-SUPPRESSED-UNVERIFIED`. A
  `null` quote is legitimate when the exclusion rests on the *absence* of a
  statement — the Methods never placing a construct in the sequenced material is
  not quotable.
- **Session limits, not papers, are the constraint at scale.** Expect to run a
  large corpus over several sittings, using `pe.pending` between them.
- `table` blocks are deliberately excluded: a Cell Press KEY RESOURCES TABLE
  lists every reagent in the lab, and this task turns on the *role* a reagent
  plays, not its presence.

## Changing the criteria

**Read `CURATOR-RULINGS.md` first.** It records every determination the curator
made by reading the paper, with the reasoning. Check whether a ruling already
constrains the criterion you are about to edit, and use those papers as the first
acceptance-set candidates. Where a ruling and `prompt.md` disagree, that is a bug
in the prompt — twice the written rule has pointed the opposite way from the
curator while the extraction reached the right answer anyway, which is not a
property to rely on.

Edit `prompt.md` and bump its `Version:` line — nothing hardcodes the version.
The same now goes for `schema_version`: `pe.validate` reads the expected value
out of the **Constants for a run** table, so bumping the schema means editing
that row, the "Echo `schema_version` as" instruction, the schema example and
the JSONL record — all four in prompt.md, and `tests/test_schema_version.py` fails
if they disagree. Leaving one behind is what put a spurious issue on 386 of the
392 corpus records at v0.0.12.
`pe.validate` mirrors the prompt's stated determination logic in
`stage_a`/`stage_b`, so if you change that logic, change both and run
`tests/test_determination_v005.py`. **That filename still says v005 on purpose**
— Stage A, Stage B and the truth table have not changed since v0.0.5, so
renaming it to the current prompt version would assert a contract moved when it
did not. Fields added since have their own files:
`tests/test_suppressed_candidates.py` covers the schema 0.0.6 addition, and its
first job is
to prove the determination logic is unaffected. It also guards the closed `rule`
set against drift between `prompt.md` and `pe.validate` — v0.0.7's precedence bug
was one rule stated in three places and changed in two. `prompt.md` also has a toggle table at the
bottom for the recurring boundary calls (reporter-only genetic edits,
observational disease states, spot-based spatial assays, degraded-text
handling).

When re-scoring an existing run under a new prompt version, use `pe.compare`.
It classifies each changed paper by *which determination input moved*, so
"unexplained" means a genuine logic bug rather than a matter of opinion.

**Keep a two-run baseline, and pass it as `--baseline2`.** The prompt disagrees
with itself: at v0.0.12 it returned different determinations on 3 of 50 papers
across two runs of byte-identical input. Against a single-run baseline a
one-paper movement is *unattributable* — not "no effect", but "cannot tell" — and
that is what happened to three of the four movements in the v0.0.12 acceptance
test. `baseline-v0012-50b/` holds both runs plus `noise-floor.json`;
`pe.compare --baseline2` reports "changed BEYOND the noise floor" and labels the
rest `WITHIN-NOISE`. It refuses a version mismatch between the two baseline runs
and exits 2, because comparing versions there reports a real effect as variance —
the inversion the flag exists to prevent, and a mistake its author made on first
use.
