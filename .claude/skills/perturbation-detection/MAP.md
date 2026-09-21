# Map of this skill

What every file is for, what the system already guarantees, what it does not, and
the layout proposed to fix it.

Written 2026-09-21, against task version 0.0.25 and a 392-paper corpus.
Revised the same day after review — see "Decisions taken" at the end.

**Nothing here has been built.** This is a map and a proposal.

---

## Why this file exists

The skill works and is unusually well guarded, but it grew one version at a time.
Twice this repo has been bitten by one rule written in several places and changed
in fewer — at version 0.0.7 and again at 0.0.12 — so "which file is the real one"
is not a tidiness question here. It is the failure mode.

## Where the files actually are

95 files. They are not 95 files of rules, and saying so was misleading. The split
matters, because it is the answer to "why doesn't this look organised":

| Group | Files | Lines | What it is |
|---|---|---|---|
| The prose documents | 5 | 3,110 | criteria, ground truth, explanation |
| `task/` — rules and tables | 9 | 2,972 | the decision procedure |
| `pe/` — the machinery | 15 | 4,144 | reusable, question-blind |
| Tests | 15 | 5,718 | the guards |
| **Acceptance documents** | **12** | 1,968 | **development history** |
| **Paper lists** | **19** | 1,045 | **development history** |
| **Scorers** | **2** | 572 | **development history** |
| Second-pack example | 13 | 1,486 | proof the machinery is reusable |
| Other notes | 3 | 663 | one-off measurements |

**The rules and criteria are 14 files. The other 81 are machinery, tests and
history.** And 33 of those — every acceptance document, paper list and scorer —
sit loose at the top level, in the same folder as the rules, with nothing marking
them as history. That is why the directory does not look organised. The rules are
not scattered; they are buried in the scaffolding.

## The four kinds of content

The files are organised by format. The content inside them is four different
things, and the four cut across the files rather than lining up with them.

| Kind | Plain meaning | Changing it means |
|---|---|---|
| **Criteria** | what counts as a perturbation | verdicts may move; budget a re-score |
| **Record shape** | what an answer must contain | old records may stop validating |
| **Decision procedure** | how filled-in fields become a verdict | verdicts may move with no criterion touched |
| **Evidence** | what proves the three above are right | nothing moves; confidence changes |

A fifth kind, **explanation**, is written for people and decides nothing.

---

## The index

### What the model actually reads

One file, and only part of it.

| File | Lines | What it is |
|---|---|---|
| `prompt.md` | 757 | Four documents in one. Lines 98–599 go to the model: the criteria (steps 0–3) and the output schema. Lines 1–52 are a changelog, 53–97 are notes on running it, and 600–757 specify how the machinery should behave. **That last group is read by nothing** — not the model, not the code. |

`pe/prepare.py` cuts the file by searching for heading text. The headings are an
interface: they are declared as anchors in `task/task.yaml`, and renaming one
without updating the anchor breaks every run.

### The decision procedure — `task/`

The question-specific half. Swap this folder and the same machinery answers a
different question. **The folder name and these five filenames are hardcoded** in
`pe/pack.py`, so this is the socket any future question must fit.

| File | Lines | What it is |
|---|---|---|
| `task/task.yaml` | 112 | Pack identity. **The one place the version is written.** Prompt anchors, output filenames. |
| `task/record.yaml` | 309 | What a record must contain, and the validation rules. |
| `task/decide.yaml` | 124 | Which fields the verdict depends on, the degraded-text cap, the check wording. |
| `task/report.yaml` | 322 | Triage tiers and reporting. |
| `task/change.yaml` | 134 | What counts as a change between two runs. |
| `task/rules.py` | 956 | The decision procedure in code: `stage_a`, `stage_b`, `decide`, `checks`, `metrics`. |
| `task/screens.py` | 343 | The six review screens. |
| `task/change.py` | 336 | Classifying what moved between two runs. |
| `task/report.py` | 336 | Rendering the summary. |

### The machinery — `pe/`

15 files, 4,144 lines. It is meant to be the reusable, question-blind half, and
`tests/test_seam.py` enforces that by reading every module and rejecting a task
word in any name, string or key.

**Nothing in this repository says what `pe` stands for.** There is no
`__init__.py`, no package docstring, and no expansion in any document. It was
created in the skill's first commit on 2026-08-20, before the three-layer split
existed, and it almost certainly meant *perturbation extraction*.

That is worth fixing rather than documenting. The layer whose whole purpose is to
not know what question it is answering is named after the question — and the seam
test cannot catch it, because it reads inside the files and a directory name is
not inside a file. A second question's author inherits a folder called `pe`.

### The evidence

| What | Count | State |
|---|---|---|
| `CURATOR-RULINGS.md` | 26 rulings, 21 papers | Prose. **Nothing reads it programmatically.** |
| `ACCEPTANCE-v*.md` | 12 | Prose, one per version, 0.0.11 to 0.0.25. |
| `score-acceptance-*.py` | 2 | Each hardcodes one version's papers. Versions 0.0.11–0.0.22 have no runnable scorer. |
| `papers-*.txt` | 19 | Paper lists. **Three are referenced by nothing.** |
| `RESCORE-`, `EVAL-30-` | 2 | One-off measurement records. |

### Explanation

| File | Lines | Fate |
|---|---|---|
| `SKILL.md` | 426 | Stays. The entry point. |
| `EXPLAINED.md` | 696 | **To be deleted** — written to explain the system to colleagues, not part of the product. One external link, in the main README. |
| `DESIGN-stage-b-gate.md` | 428 | Moves to history. |

### The guards

15 test files. The four that hold the structure rather than testing behaviour:

- `test_seam.py` — the machinery may not name the task.
- `test_task_version.py` — the version is declared once and cannot be restated.
- `test_prompt_pack_agree.py` — values written in both the prompt and the pack must match.
- `test_spec_self_consistency.py` — the prompt must not say two different things about one case.

---

## The ground truth holds four different kinds of answer

**This is the finding that shapes everything else, and it is written nowhere but
here.**

`CURATOR-RULINGS.md` records what the curator decided about a paper, and is
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

**Ruling 11 is the one to understand**, because it is all of the traps at once.
Paper `10.1038/s41467-021-21783-3`. The summary table says `no`. The prose says
the question actually put to the curator was only whether timed mating is a
perturbation, and then states: *"The paper-level call is therefore open."* Three
days later, ruling 14 on the same paper says `yes`. So one paper carries three
different answers in one file, and nothing on ruling 11 points forward to 14.

**The risk this creates is worse than a wrong number.** An automatic check built
without these distinctions gets two permanent failures, and the obvious way to
clear them is to change the criteria so the classifier matches the curator. That
would make it guess at species and at deposit membership — the two things this
project decided it must not do. A contributor could make the system worse by
making a test go green.

### The rule for a changed ruling

**Decided on review.** When a paper is ruled on twice and the verdicts differ,
the checker must **stop and surface it**. It may not silently prefer the later
date. A changed ruling is sealed as the decisive answer only by explicit manual
approval, recorded in the ledger against both entries.

Three papers currently carry two rulings. Two re-confirm; ruling 11 → 14 is the
one reversal, and it is unsealed.

---

## What is already guaranteed

More than most systems of this kind have.

- The version exists in one place and is substituted in, so it cannot go stale.
- A hash over every rule-bearing file says whether two records were produced
  under identical rules — a comparison, not an opinion.
- Every quote is checked against the source it claims; unverifiable quotes are
  dropped and the verdict recomputed.
- The machinery contains no task vocabulary, enforced by reading the code.
  (Except the folder name — see above.)
- Prompt and pack must agree, enforced.
- The prompt must not contradict itself, enforced over 20 cases.
- The skill's tests run in the automated checks, and a run that discovers no
  tests fails rather than passing.

## What is not guaranteed

**1. Nothing checks the ground truth.** No test, script or automated check reads
`CURATOR-RULINGS.md`. A criteria change can contradict a four-version-old ruling
and nothing notices. Twenty-one papers of curator judgement sit outside the loop.

**2. Acceptance evidence is rebuilt from scratch each version.** Each new script
is a fresh chance at the mistake that already happened: the 0.0.25 gate read a
key the checker never returned, rejected all fourteen defect claims, and would
have certified a pass over a mechanism that never ran.

**3. Development history is indistinguishable from the product.** 33 files at the
top level.

**4. One fact, many copies.** The 0.0.12 story is told in five files.

**5. Dead weight.** Three paper lists referenced by nothing.

---

## The proposed layout

Separating the product from the scaffolding, which is the point.

```
SKILL.md              how to run it, how to change it — the entry point
MAP.md                this file
config.yaml

criteria/             THE PRODUCT — what counts
  criteria.md           steps 0–3, the governing question, tricky cases  [model reads]
  schema.md             the output record                                [model reads]
  rulings.md            curator ground truth, every entry marked
                          binding / out-of-scope / partial / not-adopted
                          and unsealed reversals flagged for approval

task/                 THE DECISION PROCEDURE — unchanged, loaded by exact name
pe/                   THE MACHINERY — reusable; see the naming question below
tests/

history/              DEVELOPMENT — not the product
  CHANGELOG.md          lifted out of prompt.md
  acceptance/           the 12 documents, and new ones from now on
  sets/                 the 19 paper lists
  notes/                rescore, eval, and the Stage B design record
```

Top level goes from about 41 files to six entries. "Where are the criteria?"
becomes one folder.

`prompt.md` splits three ways: the model-facing text becomes `criteria/criteria.md`
and `criteria/schema.md`, the changelog moves to `history/`, and the machinery
specification at the end — read by nothing — is deleted or folded into `SKILL.md`.
This is the one step that can break a run, because the heading anchors in
`task/task.yaml` must move with it, so it needs its own verification.

### Then make the ground truth executable

Give each ruling a small machine-readable header beside its prose — paper,
verdict, kind, date, supersedes, sealed-by. The reasoning stays prose and stays
the important part. One checker scores results against the **binding** entries,
reports out-of-scope and partial ones separately with their reason, and **fails
on any unsealed reversal** rather than picking a side.

Run it in the automated checks, blocking on binding entries only.

**Why this matters more than anything about versions.** A criteria change is
expensive to validate — a full re-score is 392 papers of model time — which is
why every change grew its own bespoke acceptance set. The curator ground truth is
**21 papers**. Executable, that is a fast regression check that catches most
breakage in minutes, and it makes the heavyweight comparison machinery needed
*less* often.

### What should not be done

**Do not turn the decision rules into data.** `task/decide.yaml` argues against it
and the argument holds: a rule table needs a vocabulary of conditions, that
vocabulary is a guess until a second question needs one, and the recorded
decision of 2026-08-31 says so. Nothing found here changes that.

**Do not make rulings 4 and 23 pass by changing the criteria.**

**Do not make version differences prominent.** Comparing runs across versions is
development scaffolding, not the product. It should keep working and stay in
`history/`.

---

## Decisions taken on review

1. **Out-of-scope rulings stay in the ledger**, marked rather than moved. The
   reasoning belongs next to the paper, and the ledger is the handoff document.
2. **A changed ruling on the same paper must be surfaced for manual approval.**
   Only approval seals it as decisive. No silent latest-date rule.
3. **Acceptance documents in the new format from now on. No back-filling.** The
   twelve existing ones move to `history/acceptance/` as the written record.
4. **`EXPLAINED.md` is dropped.** Written for colleagues; not part of the product.
5. **Version comparison is developmental** and must not be made prominent. The
   proposed third layer — a declared "did this change criteria" field — is
   **dropped**.

## Decisions still open

1. **Should `pe/` be renamed?** It is an undocumented abbreviation of the task, on
   the one layer that is supposed not to know the task. Something like `harness/`
   or `runner/` would say what it is. The cost is 427 references across
   documents, tests, shell scripts and the pack — mechanical, but wide, and it
   touches the second-pack example that proves the seam works.

2. **Does `criteria/` holding both the model-facing text and the ground truth
   match how you think about it**, or does ground truth deserve its own folder
   beside it?
