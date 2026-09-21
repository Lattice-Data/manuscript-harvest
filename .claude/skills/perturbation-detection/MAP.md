# Map of this skill

What every file is for, what the system guarantees, and what it does not.

Written 2026-09-21 against task version 0.0.25 and a 392-paper corpus, then
revised twice: once after review, once after the layout below was built.

**The layout is now the layout on disk.** What is still only proposed is the
executable ground truth and the single acceptance runner, at the end.

---

## Why this file exists

The skill works and is unusually well guarded, but it grew one version at a time.
Twice this repo has been bitten by one rule written in several places and changed
in fewer — at version 0.0.7 and again at 0.0.12 — so "which file is the real one"
is not a tidiness question here. It is the failure mode.

## The shape, and what was wrong with it

Ninety-odd files, but not ninety files of rules. That distinction is the whole
answer to "why doesn't this look organised":

| Group | Files | Lines | What it is |
|---|---|---|---|
| `criteria/` | 2 | 1,512 | **the product** — what counts, and what is correct |
| `task/` | 9 | 2,972 | the decision procedure |
| `harness/` | 15 | 4,144 | reusable machinery, question-blind |
| `tests/` | 15 | 5,718 | the guards |
| `history/` | 37 | 4,449 | development record |
| `examples/` | 13 | 1,486 | proof the machinery is reusable |

The rules and criteria are a handful of files. Before the reorganisation, 33
files of development history — every acceptance document, paper list and scorer —
sat loose at the top level in the same folder, with nothing marking them as
history. The rules were not scattered; they were buried in the scaffolding. Top
level is now 13 entries.

## The four kinds of content

Files are organised by format. The content inside them is four different things,
and the four cut across the files rather than lining up with them.

| Kind | Plain meaning | Changing it means |
|---|---|---|
| **Criteria** | what counts as a perturbation | verdicts may move; budget a re-score |
| **Record shape** | what an answer must contain | old records may stop validating |
| **Decision procedure** | how filled-in fields become a verdict | verdicts may move with no criterion touched |
| **Evidence** | what proves the three above are right | nothing moves; confidence changes |

---

## The index

### `criteria/` — the product

| File | Lines | What it is |
|---|---|---|
| `criteria/prompt.md` | 709 | The criteria (steps 0–3), the output schema, the toggles, and the batch specification. |
| `criteria/rulings.md` | 803 | Curator ground truth: 26 rulings over 21 papers, with the reasoning. |

**Only part of `prompt.md` reaches the model.** `harness/prepare.py` cuts it by
searching for heading text — the fenced block after `## Instruction prompt`, then
everything between `## Output schema` and `## Toggle decisions`. Those headings
are an interface, declared as anchors in `task/task.yaml`; renaming one without
updating the anchor breaks every run.

The batch specification at the end is **not** inert documentation, which is worth
knowing before anyone moves it. Step 10 defines the triage tiers and
`tests/test_prompt_pack_agree.py` checks `task/report.yaml` against it, so it is
rule-bearing and it is inside the hash. An attempt to move it out during this
reorganisation broke four tests and was reverted.

### `task/` — the decision procedure

The question-specific half. Swap this folder and the same machinery answers a
different question. **The folder name and these filenames are hardcoded** in
`harness/pack.py`, so this is the socket any future question must fit.

| File | Lines | What it is |
|---|---|---|
| `task/task.yaml` | 112 | Pack identity. **The one place the version is written.** Anchors, output filenames. |
| `task/record.yaml` | 309 | What a record must contain, and the validation rules. |
| `task/decide.yaml` | 124 | Which fields the verdict depends on, the degraded-text cap, check wording. |
| `task/report.yaml` | 322 | Triage tiers and reporting. |
| `task/change.yaml` | 134 | What counts as a change between two runs. |
| `task/rules.py` | 956 | `stage_a`, `stage_b`, `decide`, `checks`, `metrics`. |
| `task/screens.py` | 343 | The six review screens. |
| `task/change.py` | 336 | Classifying what moved between two runs. |
| `task/report.py` | 336 | Rendering the summary. |

### `harness/` — the machinery

15 files, 4,144 lines. The reusable, question-blind half. `tests/test_seam.py`
enforces that by reading every module and rejecting a task word in any
identifier, string or key.

**It was called `pe/` until 2026-09-21, and nothing in the repository said what
that meant.** No `__init__.py`, no package docstring, no expansion anywhere. It
was created in the skill's first commit on 2026-08-20, before the three-layer
split existed, and almost certainly meant *perturbation extraction*.

That was the wrong name for what it became: the layer whose entire purpose is not
knowing which question it is answering, named after the question. The seam test
could not catch it, because a directory name is not inside a file. Renamed in
443 places.

### `history/` — the development record

Not the product. Nothing here decides anything.

| What | Count | Note |
|---|---|---|
| `history/CHANGELOG.md` | 1 | Lifted out of `prompt.md`, where nothing read it. |
| `history/acceptance/` | 14 | 12 acceptance documents and 2 scorers. |
| `history/sets/` | 19 | Paper lists. **Three are referenced by nothing.** |
| `history/notes/` | 3 | The rescore, the 30-paper eval, the Stage B design record. |

`EXPLAINED.md` was deleted here — 696 lines written to explain the system to
colleagues, not part of the product.

### The guards

15 test files. The four that hold the structure rather than testing behaviour:

- `test_seam.py` — the machinery may not name the task.
- `test_task_version.py` — the version is declared once and cannot be restated.
- `test_prompt_pack_agree.py` — values written in both the prompt and the pack must match.
- `test_spec_self_consistency.py` — the prompt must not say two different things about one case.

---

## The ground truth holds four different kinds of answer

**The finding that shapes everything else.**

`criteria/rulings.md` records what the curator decided about a paper, and is
described as ground truth. But the curator answers a different question from the
one the classifier is asked, and sometimes the two are *supposed* to differ.

Scored naively, the corpus agrees with 19 of 21 papers. Both apparent failures
are the system working correctly:

| Ruling | Paper | Curator | Classifier | Why they differ |
|---|---|---|---|---|
| 4 | `10.1038/s41586-022-05060-x` | no | yes | species of the deposited data |
| 23 | `10.1038/s41586-021-03852-1` | no | yes | whether samples reached the collection |

**Ruling 4** turns on species — the perturbation is in mice, the deposited data
is human. Its own consequence note records the agreed resolution: *record the
organism of the paired material and do not call on it.* The verdict stays
species-agnostic and a person applies the filter, because a non-human dataset can
be a legitimate curation target. The record carries `paired_organisms: ['mouse']`
and `paired_organism_human: False`, so the downstream filter reaches the
curator's answer. The classifier is right to say `yes`.

**Ruling 23** turns on whether the treated samples reached the deposited
collection. That ruling states it outright: *"a fact about the deposited dataset,
not about the paper, and no text-only classifier reaches it at any prompt
version."*

So entries come in four kinds, currently stored in one column with no marking:

1. **Binding** — the verdict the classifier must produce. Most entries.
2. **Out of scope** — the curator used information the classifier cannot see, so
   a different answer is correct. Rulings 4 and 23.
3. **Partial** — the curator settled a sub-question, not the paper. Ruling 11.
4. **Not adopted** — considered and declined. Rulings 16 and 21, marked in prose.

**Ruling 11 is the one to understand**, because it is every trap at once. Paper
`10.1038/s41467-021-21783-3`. The summary table says `no`. The prose says the
question actually put to the curator was only whether timed mating is a
perturbation, then states: *"The paper-level call is therefore open."* Three days
later ruling 14 on the same paper says `yes`. One paper, three answers in one
file, and nothing on ruling 11 points forward to 14.

**The risk is worse than a wrong number.** An automatic check built without these
distinctions gets two permanent failures, and the obvious way to clear them is to
change the criteria so the classifier matches the curator. That would make it
guess at species and at deposit membership — the two things this project decided
it must not do. A contributor could make the system worse by making a test green.

### The rule for a changed ruling

**Curator decision.** When a paper is ruled on twice and the verdicts differ, the
checker must **stop and surface it**. It may not silently prefer the later date.
A changed ruling becomes the decisive answer only by explicit manual approval,
recorded in the ledger against both entries.

Three papers carry two rulings. Two re-confirm; ruling 11 → 14 is the one
reversal, and it is unsealed.

---

## What is already guaranteed

- The version exists in one place and is substituted in, so it cannot go stale.
- A hash over every rule-bearing file says whether two records were produced
  under identical rules — a comparison, not an opinion.
- Every quote is checked against the source it claims; unverifiable quotes are
  dropped and the verdict recomputed.
- The machinery contains no task vocabulary, enforced by reading the code, and
  since the rename the folder name no longer contradicts that.
- Prompt and pack must agree, enforced.
- The prompt must not contradict itself, enforced over 20 cases.
- The skill's tests run in the automated checks, and a run that discovers no
  tests fails rather than passing.

## What is not guaranteed

**1. Nothing checks the ground truth.** No test, script or automated check reads
`criteria/rulings.md`. A criteria change can contradict a four-version-old ruling
and nothing notices. Twenty-one papers of curator judgement sit outside the loop.

**2. Acceptance evidence is rebuilt from scratch each version.** Each new script
is a fresh chance at the mistake that already happened: the 0.0.25 gate read a
key the checker never returned, rejected all fourteen defect claims, and would
have certified a pass over a mechanism that never ran.

**3. One fact, many copies.** The 0.0.12 story is told in five files.

**4. Dead weight.** Three paper lists referenced by nothing.

---

## What is still proposed

### Make the ground truth executable

Give each ruling a small machine-readable header beside its prose — paper,
verdict, kind, date, supersedes, sealed-by. The reasoning stays prose and stays
the important part. One checker scores results against the **binding** entries,
reports out-of-scope and partial ones separately with their reason, and **fails
on any unsealed reversal** rather than picking a side.

Run it in the automated checks, blocking on binding entries only.

**Why this matters more than anything about versions.** A criteria change is
expensive to validate — a full re-score is 392 papers of model time — which is
why every change grew its own bespoke acceptance set. The ground truth is **21
papers**. Executable, that is a fast regression check that catches most breakage
in minutes, and it makes the heavyweight comparison machinery needed *less*
often.

### One acceptance format, from now on

One runner plus a small expectations file per version. **No back-filling** — the
twelve existing documents stay in `history/acceptance/` as the written record. A
version that declares an expectation the runner cannot evaluate fails, which is
the defence against a gate that passes over nothing.

### What should not be done

**Do not turn the decision rules into data.** `task/decide.yaml` argues against it
and the argument holds: a rule table needs a vocabulary of conditions, that
vocabulary is a guess until a second question needs one, and the recorded
decision of 2026-08-31 says so.

**Do not make rulings 4 and 23 pass by changing the criteria.**

**Do not make version differences prominent.** Comparing runs across versions is
development scaffolding, not the product. It keeps working and it stays in
`history/`.

---

## Decisions taken

1. **Out-of-scope rulings stay in the ledger**, marked rather than moved.
2. **A changed ruling must be surfaced for manual approval.** No silent
   latest-date rule.
3. **New acceptance format from now on, no back-filling.**
4. **`EXPLAINED.md` dropped.**
5. **Version comparison is developmental** and is not made prominent. The
   proposed "did this change criteria" field is dropped.
6. **`pe/` renamed to `harness/`.**
7. **Ground truth lives in `criteria/`**, beside the criteria it constrains.

## The pack hash, and how it was cleared

`pack_sha256` covers `criteria/prompt.md` and `task/*`, and it hashes paths as
well as contents. The rename and the move both changed it — `e598d73e` before,
`017cc03d` now — while changing no rule. The 392 stored records carried the old
value, so a re-validation would have reported *"MIXED PACK HASHES at the same
task_version — the rules changed without the version being bumped"*, which would
have been false.

**Cleared on 2026-09-21 by re-validating, which costs no model time.**
`harness.validate` re-reads the stored raw responses; there is no second run and
no extraction. Both runs the corpus is made of had to be replayed, in order:

    harness.validate --work <run-root>/work-corpus-v0025-r1 --write-corpus
    harness.validate --work <run-root>/work-glyphfix-17     --write-corpus

**The order is the trap.** The corpus is not one run's output. 375 records come
from `work-corpus-v0025-r1` and 17 from `work-glyphfix-17`, which re-scored the
papers the PDF glyph repair actually moved. Re-validating only the first would
have silently reverted those 17 to their pre-glyph-fix answers — a regression
with no error message, in a directory git does not track.

**Result: 0 of 392 determinations moved**, and Stage A, the Stage B cap and the
model's own answer are unchanged on every paper. 123 yes, 261 no, 7 unclear, 1
not applicable, before and after. The corpus and the pack now agree on
`017cc03d`.

That zero is the evidence the whole reorganisation was inert. It is a stronger
statement than the byte-identical prompt on its own, because it exercises the
pack loader, the validator and the decision rules rather than only the text
handed to the model.
