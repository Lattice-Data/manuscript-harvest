---
name: perturbation-detection
description: Classify extracted scientific papers as perturbed / not perturbed / unclear / not applicable for single-cell biocuration — detecting whether the samples actually profiled by a single-cell or single-nucleus sequencing assay were experimentally perturbed (drug, cytokine, stimulation, knockout/knockdown, hypoxia, diet, etc.). Use when asked to find, detect, score, or curate perturbations across a corpus of papers, to run "the perturbation prompt" or "the perturbation pipeline", to re-score papers under a new prompt version, or to check which papers in a manuscript corpus involve experimental manipulation. Works on directories of extracted paper text (blocks.jsonl), not on PDFs or DOIs directly.
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
  PLUMBING   harness/                 assemble sources · splice the prompt ·  KEEP
                                 one call per paper · verify every
                                 quote · prune · recompute · tabulate ·
                                 diff
  TEXT       manuscript_harvest  DOI -> article + attachments ->        KEEP
                                 labelled text with provenance
```

`harness/` is 3,535 lines that name this task **nowhere in code**, and
`tests/test_seam.py` holds that line by tokenising every module and rejecting a
task word in any identifier, string or key. Swap `task/` and the same machinery
answers a different question — which has been done: a second pack answering
"which tissue did the sequenced material come from?" runs through a
byte-identical `harness/`.

`task/` has **no `__init__.py`**. It is a namespace package, like `harness/`, and holds
nothing but the spec, the four tables and the four rule modules. The loader that
reads a pack is `harness/pack.py`, because it is machinery no pack owns: it spent one
version inside `task/__init__.py`, and the second pack had to copy all 232 lines
of it verbatim. The dependency therefore runs pack → harness, which looks
backwards for a moment and is the ordinary plugin shape — the harness must never
import a task's vocabulary, while a task reading the harness's loader is fine,
and `test_seam.py` enforces exactly that asymmetry.

**The worked example is archived at
[`examples/second-pack/`](examples/second-pack/README.md)** — the whole tissue
pack, its 10-paper result, and the five leaks it found. Read it before writing a
third question. It is a snapshot: not in CI, not linted, not maintained, and its
README says so at the top.

| table | holds |
|---|---|
| `task/record.yaml` | **what counts** — the closed value sets, the required fields, the array shapes, the open fields |
| `task/decide.yaml` | **how to decide** — the determination's inputs, the degraded-text cap and what opens it, CC-1..CC-8 |
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

### What a determination is ABOUT, and what it is not

A determination describes **the paper**, judged from its text. It does not
describe the deposited dataset.

The distinction has already produced a disagreement worth recording. In the
2026-09-15 curator batch, `10.1038/s41586-021-03852-1` was ruled `no` partly
because *"the samples are not included in the cellxgene collection"* — cytokine-
treated organoids that the paper describes and that apparently never reached the
collection. That is a fact about what was deposited, and it is not recoverable
from the article at any prompt version.

So: where collection contents and the paper disagree, **the curator overrides the
pipeline**, and that override is not a pipeline defect. If the determination ever
needs to follow the deposited data, the collection manifest has to become a
second input to the run — a pipeline change, not a criteria change. Ruling 23 in
`criteria/rulings.md` holds the case.

A related consequence, decided in the same batch: a paper that reanalyses or
integrates other groups' public data **is** primary research, and perturbed
samples arriving that way still count. `prompt.md` Step 0b names this explicitly,
because it is the obvious false positive for the article-type gate.

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

**Run every command from this directory.** `harness` and `task` are packages resolved
relative to it, so `python -m harness.prepare` from the repo root is
`ModuleNotFoundError: No module named 'harness'`.

```bash
cd .claude/skills/perturbation-detection
python -m harness.prepare  --set papers-30.txt --corpus ../../../corpus
./harness/run_headless.sh
python -m harness.validate --write-corpus --corpus ../../../corpus
python -m harness.summarize
```

- `--set` is **required** and names a file of paper directory names, one per
  line. The sets that ship are `papers-6.txt`, `papers-30.txt`, `papers-50.txt`,
  `papers-50b.txt` and `papers-all.txt` (the 392 the corpus run used). There is no
  `papers.txt`, and there never was — this line used to name one, and the argparse
  default named `validation_set.txt`, which has never existed either.
- `--corpus` is **required** too, by `harness.prepare` and by `harness.validate
  --write-corpus`. It has no default on purpose. The old default was `./corpus`,
  which resolves against the CWD — and the CWD is this directory, where a stale
  382-paper tree sits beside the real 392-paper one at the repo root. So the
  default could only fire when someone forgot the flag, and it then scored a
  quietly different set of papers: both trees are gitignored, so nothing could
  tell you. A path that does not exist is refused rather than created, which is
  how the stale tree came to exist in the first place (`harness.validate` once
  hardcoded `./corpus` while `harness.prepare` honoured `config.yaml`, so
  `--write-corpus` built a second corpus beside the CWD).
- **Run artifacts land outside this directory**, under
  `~/.manuscript-harvest/perturbation/{work,output}` by default. That is not
  tidiness: `claude -p` subagents cannot write under `.claude/`, and the CLI
  exits 0 anyway, so a stage-2 result written beside the skill is lost with no
  error. Two of six papers hit this on the first v0.0.9 run. Override the root
  with `PERTURBATION_RUN_ROOT`, or a single run with `--work` / `--out`; an
  explicit path is honoured verbatim. The skill directory keeps only what is
  versioned and shared — `prompt.md`, `harness/`, `task/`, `config.yaml`.
  The env var and the directory name are `task.yaml: outputs`, so a second pack
  gets its own run root rather than reading this one's papers as pending.
- Step 2 runs `claude -p` once per paper. It uses the logged-in Claude Code
  session, **not** an API key — the Anthropic SDK and REST API will not work on
  this account. Set `PY=` if `python3` is not the interpreter you want. The model
  is pinned to `claude-opus-5`; override for one run with `PERTURBATION_MODEL`,
  and `harness.validate` records whichever ran as `validation.model_id`.
- **Budget:** 30 papers at 3 parallel is roughly 45-75 minutes. Papers run from
  ~45k to ~1M characters. Session limits, not papers, are the binding constraint.
- **A `FAIL` is usually transient.** One paper in six hit
  `API Error: Connection closed mid-response` on one run. Re-running picks up only
  the missing ones; do not read a FAIL as a content problem without opening the
  log.
- To know when a long run has finished: `./harness/watch.sh <work_dir>` prints progress
  every 30s and raises a desktop notification at the end.
  `./harness/watch.sh <work_dir> status` prints one line and exits. Both only read
  state, so they are safe to start late or interrupt. **Pass a real work
  directory** — with no `manifest.json` there it exits 2 rather than printing
  `0/ done, 0 failed  FINISHED`, which is what it used to do for a path that did
  not exist.
- **Do not poll with `pgrep -f run_headless.sh`.** The pattern matches the waiting
  command's own command line, so the loop never exits. Use `./harness/watch.sh`.
- Everything is resumable. `python -m harness.pending --work <work_dir>` reports what is
  still missing and why; re-running step 2 picks up only those. A paper counts as
  done only if its result parses, has every required field, and its
  `sources_seen` matches the manifest — so a partial write is re-run, not
  silently accepted.

Alternative for step 2 when working inside an interactive Claude Code session:
`harness/extract_workflow.js` runs one subagent per paper via the Workflow tool.
Faster and gives per-paper progress, but needs an interactive session.

Then review:

```bash
python -m harness.audit       # six targeted review screens (A–F)
python -m harness.compare --baseline <old_run_dir>   # version-to-version diff
```

## Output

- `<run_root>/output/perturbations_summary.csv` — one row per paper, **sorted by triage
  priority**, so read it top-down:

  | priority | meaning |
  |---|---|
  | P1 | `unclear` because the pairing was never stated — most likely to hide a real match, read first |
  | P2 | not `yes`, but a suppressed candidate **under one of the four rules still in review** would have paired `yes` — one toggle flips the paper. Restricted on purpose: `observational_disease_state` pairs `yes` on any disease-vs-healthy contrast, and a tier holding most papers is not a queue |
  | P3 | *vacant from 0.0.22.* Held `yes` with confidence < 0.6 — the only rule that read the confidence number. 11 of the 16 papers it selected across paired same-input re-runs flipped in or out of it, so the slot is empty rather than reused; the 7 papers it held moved to P7 (3) and P9 (4) |
  | P4 | `unclear` because the text was incomplete — send to re-fetch, do not read |
  | P5 | `no` but a perturbation exists elsewhere in the paper — the pairing filter fired; sample these |
  | P6 | any consistency or evidence flag — **or** an `unclear` with no usable reason, which used to sink to P9 |
  | P7 | `yes` carried entirely by a non-human model — a scope call, not a defect (v0.0.12) |
  | P9 | everything else — including `not_applicable`, the reviews and commentaries the Step 0b gate turned away (v0.0.23). They are settled, not deferred: there is nothing for a curator to decide. Their count is on the `papers by perturbation_present` line of the run report, which is where to check how often the gate fired |

  **The ladder renumbered at prompt v0.0.10**, when P2 was inserted: the old
  P2–P5 are now P3–P6. Do not compare a priority column across prompt versions
  without checking which version produced it. v0.0.12 did **not** renumber — its
  tier took the unused slot 7 precisely so 1–6 stayed comparable, and that is the
  pattern to copy. The ladder is now ONE list, `task/report.yaml: tiers`, read by
  both the predicate and the queue summary; it used to be written twice inside
  `harness/summarize.py`, forty lines apart, with the only test pinning them
  code-against-code.

- **`suppressed_candidates`** (added in schema 0.0.6, prompt v0.0.10) — one entry per
  thing the model recognised as a possible perturbation and deliberately did not
  list, so the NOT list stops being silent. Each entry carries `candidate`,
  `rule` (a **closed** set, owned by the table under "Recording an exclusion" in
  `prompt.md`: `reporter_or_marker`, `incidental_clinical_therapy`,
  `unintended_condition`, `derivation_formulation`, `disease_model_establishment`,
  `observational_disease_state`, `sample_handling_protocol`, `readout_reagent`,
  `routine_processing`), `why`, a verified `evidence_quote`, and
  `would_have_paired`.

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
  legitimately verifies against two sources. `harness.paper_text.build_sources`
  handles this; don't bypass it.
- **Never `str.replace` a prompt placeholder globally.** `prompt.md` mentions
  `{{PAPER_TEXT}}` twice — once as prose, once as the injection point. Replacing
  both splices the whole paper into the instructions. `harness.prepare` uses
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
  paper disagreeing. Keep a determination-only baseline (`harness.compare --baseline`),
  and include at least one paper that must NOT populate whatever you added.
- **A suppressed candidate must never move the determination.** It is not a
  perturbation, so it never enters `perturbations` and Stage A cannot see it —
  which is structural, not a convention: `stage_a` reads only
  `processing_status`, `has_single_cell_assay`,
  `perturbation_present_any_assay` and the pairings inside `perturbations`. If a
  suppression ever changes a call, a write escaped `harness.validate._validate_suppressed`.
  `tests/test_suppressed_candidates.py` asserts this over every Stage A input
  combination.
- **An unverifiable suppression quote drops the quote, not the entry.** The
  suppression still happened; deleting the entry would restore exactly the
  silence the field was added to remove. Flagged `EV-SUPPRESSED-UNVERIFIED`. A
  `null` quote is legitimate when the exclusion rests on the *absence* of a
  statement — the Methods never placing a construct in the sequenced material is
  not quotable.
- **Session limits, not papers, are the constraint at scale.** Expect to run a
  large corpus over several sittings, using `harness.pending` between them.
- `table` blocks are deliberately excluded: a Cell Press KEY RESOURCES TABLE
  lists every reagent in the lab, and this task turns on the *role* a reagent
  plays, not its presence.
- **Stage B's cap is keyed on a verified quote, not on a self-report (v0.0.25).**
  `text_defects` is a required array: one entry per defect, each naming the
  `<<<SOURCE>>>` it is in, its `kind`, and a quote the harness checks against
  that source with the same verifier and threshold `perturbations[]` gets. **A
  claim whose quote does not verify is dropped and does not cap; an entry that
  cannot be READ at all caps anyway** — refuted and unreadable are opposite
  states, and the old trigger got that backwards. The cap fires on a defect in
  the **main** source, on `no_methods_content` from **any** source, or on the
  harness having withheld text; a garbled supplementary table is recorded and
  does not cap, because it could not have hidden a pairing sentence (curator
  decision, 2026-09-16). `processing_status` and `text_completeness` stay on the
  record and no longer decide anything, so **`partial` + `full` is legal and
  means "one source is garbage, the article is whole"**. Why: that trigger
  flipped on 3 of 30 byte-identical papers at v0.0.23 and 4 of 24 at v0.0.24.
  Protocol in `ACCEPTANCE-v0.0.25.md`; the blocking criterion is now agreement
  of `stage_b_capped` between two runs.
- **The text the model sees is not the published article, and since v0.0.24 the
  prompt says so.** Every source arrives with its reference list,
  acknowledgments, funding, competing-interest and data-availability sections and
  all back matter removed, no tables and no figure images at all, and on a long
  paper no Discussion or Introduction. Step 0 was asking whether the text was
  complete with none of that stated: **154 of the 392 corpus papers end on a bare
  heading with nothing under it** — "Associated Data", "Supplementary Materials" —
  because the exclusion list took the content and left the label. That is what
  made `text_completeness` flip on byte-identical input, and the cap flip with
  it. `harness.prepare.assembly_note` now states the cuts per paper in an `ASSEMBLY:`
  block rendered from the assembly that just ran, and `"full"` is defined as
  *nothing missing beyond what `ASSEMBLY:` says was removed*. **If you add a
  filter to `config.yaml: exclude_sections` or `include_kinds`, the block picks it
  up automatically — do not restate it in `prompt.md`**, which is the drift this
  design avoids. Acceptance set: `papers-accept-stageb.txt`, scored by
  `score-acceptance-stageb.py`, whose blocking criterion is agreement of the
  self-report between two runs rather than any determination.

## Changing the criteria

**Run the ledger check, then read `criteria/rulings.md`.**

    python -m harness.ground_truth --corpus ../../../corpus

It scores the stored results against every `binding` ruling and exits non-zero
on a disagreement, on a binding paper missing from the corpus, or on an unsealed
change. No model calls -- 21 papers, seconds, where a full re-score is 392. Run
it before and after a criteria edit; it is the cheapest evidence you did not
break a ruling from four versions ago.

**Not every verdict is an expectation.** The ledger's `kind` column says what
each one has authority over: `binding` is graded, `out-of-scope` rests on
something outside the paper and is *expected* to differ, `partial` settles a
sub-question, `not-adopted` was declined. Two rulings are out of scope because
they turn on the species of the deposit and on collection membership -- neither
is in the paper, and grading them would push the criteria toward guessing at
exactly the two things this project decided it must not guess. If a ruling
disagrees and you are tempted to change the criteria until it passes, check its
kind first.

**Write the acceptance spec before the run, not the write-up after it.**
Copy `history/acceptance/TEMPLATE.yaml` to `history/acceptance/v0.0.N.yaml`,
declare which papers must not move, which must and to what, and what the change
must be seen to do at least once. Then:

    python -m harness.acceptance history/acceptance/v0.0.N.yaml

Two runs of byte-identical input are required, and the runner refuses a spec
with one: this prompt disagrees with itself on a few percent of papers, so a
single-run delta cannot tell an effect from that noise. Every criterion reports
how many papers it examined, and a blocking criterion that examined **none**
fails -- a gate that evaluates nothing is how a dead mechanism certifies itself.
That is not hypothetical: at v0.0.25 the defect gate read a key the verifier
never returned, dropped all fourteen claims, and would have agreed 24/24 over a
gate that never ran.

The twelve documents in `history/acceptance/` are the record of versions 0.0.11
to 0.0.25 and are **not** being back-filled into this format. Two of them have
a hand-written scorer beside them; those still run.

**A changed ruling is never resolved by date.** Where a later entry supersedes
an earlier one with a different verdict, the check reports the pair and fails
until a human writes an approval into the `sealed` cell. That is a decision
about which reading of a paper is right, and it is not the program's to make.

It records every determination the curator
made by reading the paper, with the reasoning. Check whether a ruling already
constrains the criterion you are about to edit, and use those papers as the first
acceptance-set candidates. Where a ruling and `prompt.md` disagree, that is a bug
in the prompt — twice the written rule has pointed the opposite way from the
curator while the extraction reached the right answer anyway, which is not a
property to rely on.

Edit `prompt.md`, then bump **`task/task.yaml: version`** — the one place a
version is written. `prompt.md` carries `{{TASK_VERSION}}` at every site that
declares it and `harness.prepare` splices the value in, the same way it fills
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
`harness.validate` reads them as their run's recorded version and notes it in
`validation.task_version_source`, and `harness.summarize` counts them once per run —
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
set against drift between `prompt.md` and `harness.validate` — v0.0.7's precedence bug
was one rule stated in three places and changed in two. `prompt.md` also has a toggle table at the
bottom for the recurring boundary calls (reporter-only genetic edits,
observational disease states, spot-based spatial assays, degraded-text
handling).

When re-scoring an existing run under a new prompt version, use `harness.compare`.
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
`harness.compare --baseline2` reports "changed BEYOND the noise floor" and labels the
rest `WITHIN-NOISE`. It refuses a version mismatch between the two baseline runs
and exits 2, because comparing versions there reports a real effect as variance —
the inversion the flag exists to prevent, and a mistake its author made on first
use.
