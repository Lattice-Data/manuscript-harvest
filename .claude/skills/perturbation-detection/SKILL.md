---
name: perturbation-detection
description: Classify extracted scientific papers as perturbed / not perturbed / unclear for single-cell biocuration — detecting whether the samples actually profiled by a single-cell or single-nucleus sequencing assay were experimentally perturbed (drug, cytokine, stimulation, knockout/knockdown, hypoxia, diet, etc.). Use when asked to find, detect, score, or curate perturbations across a corpus of papers, to run "the perturbation prompt" or "the perturbation pipeline", to re-score papers under a new prompt version, or to check which papers in a manuscript corpus involve experimental manipulation. Works on directories of extracted paper text (blocks.jsonl), not on PDFs or DOIs directly.
---

# Perturbation detection for single-cell biocuration

Reads extracted paper text and decides, per paper, whether the material that
went into a single-cell/single-nucleus sequencing assay was experimentally
perturbed. Produces a reviewable table plus per-paper JSON with verbatim
evidence quotes.

**The judgment lives in `prompt.md` and `task/`, not here.** `prompt.md` is the
single source of truth for what counts as a perturbation, the qualifying assay
list and the pairing rules; `task/` holds the four lookup tables the harness
reads. Read those before answering questions about criteria. This file only
explains how to run it.

## Three layers, and only the top one is about perturbations

```
  JUDGMENT   task/ + prompt.md   the spec + four lookup tables         SWAP
  PLUMBING   pe/                 assemble sources · splice the prompt ·  KEEP
                                 one call per paper · verify every
                                 quote · prune · recompute · tabulate ·
                                 diff
  TEXT       manuscript_harvest  DOI -> article + attachments ->        KEEP
                                 labelled text with provenance
```

`pe/` is 1,517 lines that name this task **nowhere in code**, and
`tests/test_seam.py` holds that line by tokenising every module and rejecting a
task word in any identifier, string or key. Swap `task/` and the same machinery
answers a different question.

| table | holds |
|---|---|
| `task/record.yaml` | **what counts** — the closed value sets, the required fields, the array shapes, the open fields |
| `task/decide.yaml` | **how to decide** — the determination's inputs, the degraded-text cap, CC-1..CC-7 |
| `task/report.yaml` | **what to read first** — the triage ladder, the CSV columns, the six screens, the keyword banks |
| `task/change.yaml` | **what counts as a change** — the 12 change classes and the cross-run match rule |

`task/task.yaml` carries the identity: one `version` for the whole pack, and the
spec contract (`anchors`, `placeholders`, `read_back_marker`, `outputs`) that any
replacement prompt must satisfy. The predicates a list cannot express are
`task/rules.py`, `report.py`, `screens.py` and `change.py` — `decide.yaml`
explains why those are functions rather than more YAML.

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

**Run every command from this directory.** `pe` and `task` are packages resolved
relative to it, so `python -m pe.prepare` from the repo root is
`ModuleNotFoundError: No module named 'pe'`.

```bash
cd .claude/skills/perturbation-detection
python -m pe.prepare  --set papers-30.txt --corpus ../../../corpus
./pe/run_headless.sh
python -m pe.validate --write-corpus --corpus ../../../corpus
python -m pe.summarize
```

- `--set` is **required** and names a file of paper directory names, one per
  line. The sets that ship are `papers-6.txt`, `papers-30.txt`, `papers-50.txt`,
  `papers-50b.txt` and `papers-all.txt` (the 392 the corpus run used). There is no
  `papers.txt`, and there never was — this line used to name one, and the argparse
  default named `validation_set.txt`, which has never existed either.
- **Run artifacts land outside this directory**, under
  `~/.manuscript-harvest/perturbation/{work,output}` by default. That is not
  tidiness: `claude -p` subagents cannot write under `.claude/`, and the CLI
  exits 0 anyway, so a stage-2 result written beside the skill is lost with no
  error. Two of six papers hit this on the first v0.0.9 run. Override the root
  with `PERTURBATION_RUN_ROOT`, or a single run with `--work` / `--out`; an
  explicit path is honoured verbatim. The skill directory keeps only what is
  versioned and shared — `prompt.md`, `pe/`, `task/`, `config.yaml`.
  The env var and the directory name are `task.yaml: outputs`, so a second pack
  gets its own run root rather than reading this one's papers as pending.
- Step 2 runs `claude -p` once per paper. It uses the logged-in Claude Code
  session, **not** an API key — the Anthropic SDK and REST API will not work on
  this account. Set `PY=` if `python3` is not the interpreter you want. The model
  is pinned to `claude-opus-5`; override for one run with `PERTURBATION_MODEL`,
  and `pe.validate` records whichever ran as `validation.model_id`.
- **Budget:** 30 papers at 3 parallel is roughly 45-75 minutes. Papers run from
  ~45k to ~1M characters. Session limits, not papers, are the binding constraint.
- **A `FAIL` is usually transient.** One paper in six hit
  `API Error: Connection closed mid-response` on one run. Re-running picks up only
  the missing ones; do not read a FAIL as a content problem without opening the
  log.
- To know when a long run has finished: `./pe/watch.sh <work_dir>` prints progress
  every 30s and raises a desktop notification at the end.
  `./pe/watch.sh <work_dir> status` prints one line and exits. Both only read
  state, so they are safe to start late or interrupt. **Pass a real work
  directory** — with no `manifest.json` there it exits 2 rather than printing
  `0/ done, 0 failed  FINISHED`, which is what it used to do for a path that did
  not exist.
- **Do not poll with `pgrep -f run_headless.sh`.** The pattern matches the waiting
  command's own command line, so the loop never exits. Use `./pe/watch.sh`.
- Everything is resumable. `python -m pe.pending --work <work_dir>` reports what is
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

- `<run_root>/output/perturbations_summary.csv` — one row per paper, **sorted by triage
  priority**, so read it top-down:

  | priority | meaning |
  |---|---|
  | P1 | `unclear` because the pairing was never stated — most likely to hide a real match, read first |
  | P2 | not `yes`, but a suppressed candidate would have paired `yes` — one toggle flips the paper |
  | P3 | `yes` with confidence < 0.6 |
  | P4 | `unclear` because the text was incomplete — send to re-fetch, do not read |
  | P5 | `no` but a perturbation exists elsewhere in the paper — the pairing filter fired; sample these |
  | P6 | any consistency or evidence flag — **or** an `unclear` with no usable reason, which used to sink to P9 |
  | P7 | `yes` carried entirely by a non-human model — a scope call, not a defect (v0.0.12) |
  | P9 | everything else |

  **The ladder renumbered at prompt v0.0.10**, when P2 was inserted: the old
  P2–P5 are now P3–P6. Do not compare a priority column across prompt versions
  without checking which version produced it. v0.0.12 did **not** renumber — its
  tier took the unused slot 7 precisely so 1–6 stayed comparable, and that is the
  pattern to copy. The ladder is now ONE list, `task/report.yaml: tiers`, read by
  both the predicate and the queue summary; it used to be written twice inside
  `pe/summarize.py`, forty lines apart, with the only test pinning them
  code-against-code.

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

- **`paired_organism` / `organism`** (prompt v0.0.12) — whose sample was
  perturbed. `perturbation_present` asked whether a perturbed sample was profiled
  by a qualifying assay and never asked **whose**, so a paper could be `yes` on an
  animal model while the human data — the material that reaches the curated
  deposit — was purely observational. Measured on the 50-paper v0.0.11 run: 5 of
  15 positives rested on an animal-only pairing, the largest single source of
  false positives found in any run. Both values are **open** strings, never
  rejected for naming an unusual species, and `null` is legitimate. Nothing here
  reaches the determination — `tests/test_organism.py` asserts that over every
  Stage A input combination, because the curation scope is a call a person makes
  downstream: the corpus is human-primarily but not human-only. Surfaces as
  triage **P7** and as `paired_organisms` / `paired_organism_human` /
  `n_paired_yes_human` in the CSV.

- **`validation.model_id`, `task_version`, `pack_sha256`** — which model produced
  the record, which rules it was graded against, and whether those rules were
  byte-identical to another run's.

- `<corpus>/<paper>/extracted/perturbations.json` — full per-paper result with
  evidence quotes (with `--write-corpus`). The destination honours
  `config.yaml: corpus_dir`; it used to hardcode `./corpus`, which is how a stale
  382-record shadow corpus came to exist inside this directory.
- `<run_root>/output/review_screen.txt` — Screens A–F: pairing flips, possibly-missed
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

Edit `prompt.md`, then bump **`task/task.yaml: version`** — the one place a
version is written. `prompt.md` carries `{{TASK_VERSION}}` at every site that
declares it and `pe.prepare` splices the value in, the same way it fills
`{{PAPER_ID}}`, so a stale version in the spec is not a bug to catch but a state
the file cannot be in. `tests/test_task_version.py` asserts the absence of a
literal rather than the agreement of copies.

That replaced a pair, `prompt_version` + `schema_version`, which were separate
only because somebody had to judge per revision whether the record shape had
moved. At v0.0.12 that judgment was made and applied to three of the four
declaration sites; the model split on the contradiction — **386 of 392 records
followed the schema example and emitted 0.0.7, 6 followed the instruction line
and emitted 0.0.6** — and the validator was calibrated to the minority. The
judgment is gone, replaced by `task_version` and by `pack_sha256`, a hash over
every rule-bearing file: a version bump says the author thought something
changed, the hash says whether anything did.

Records written before 0.0.13 carry `schema_version` and no `task_version`.
`pe.validate` reads them as their run's recorded version and notes it in
`validation.task_version_source`, and `pe.summarize` counts them once per run —
**not** once per paper, which is what the first draft did, taking the corpus
issue count from 146 to 532 and making the column where real problems appear
unreadable again.

`task/rules.py` mirrors the prompt's stated determination logic in
`stage_a`/`stage_b`, so if you change that logic, change both and run
`tests/test_determination_v005.py`. **That filename still says v005 on purpose**
— Stage A, Stage B and the truth table have not changed since v0.0.5, so
renaming it to the current prompt version would assert a contract moved when it
did not. Fields added since have their own files:
`tests/test_suppressed_candidates.py` covers the `suppressed_candidates` addition, and its
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
test. `$PERTURBATION_RUN_ROOT/baseline-v0012-50b/` holds both runs plus
`noise-floor.json` — it is in the **run root**, not in this directory, and its
layout is `{manifest.json, r1/, r2/}` rather than a `validated/` of its own.
`--baseline` resolves `r1` and `--baseline2` resolves `r2`; a bare `--work`
pointed at the parent names both options rather than guessing. All three review
tools used to report **zero papers and exit 0** on that directory, which is a
PASS over an empty set;
`pe.compare --baseline2` reports "changed BEYOND the noise floor" and labels the
rest `WITHIN-NOISE`. It refuses a version mismatch between the two baseline runs
and exits 2, because comparing versions there reports a real effect as variance —
the inversion the flag exists to prevent, and a mistake its author made on first
use.
