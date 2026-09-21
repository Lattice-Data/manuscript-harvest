# Map of this skill

What every file is for, what the system already guarantees, and what it does not.
Written 2026-09-21, against task version 0.0.25 and a 392-paper corpus.

**Nothing here proposes a change that has been made.** The last section lists the
decisions that have to be taken first. Read that before building anything.

---

## Why this file exists

The skill works and is unusually well guarded, but it grew one version at a time.
There are now about 6,100 lines of rules and explanation across 95 files, and a
newcomer cannot tell from the names which of them decide anything.
Twice this repo has been bitten by one rule written in several places and changed
in fewer — at version 0.0.7 and again at 0.0.12 — so "which file is the real one"
is not a tidiness question here. It is the failure mode.

## The four kinds of content

The files are organised by format. The content inside them is four different
things, and the four cut across the files rather than lining up with them. Almost
every confusion in this directory comes from that mismatch.

| Kind | Plain meaning | Changing it means |
|---|---|---|
| **Criteria** | what counts as a perturbation | verdicts may move; budget a full re-score |
| **Record shape** | what an answer must contain | old records may stop validating |
| **Decision procedure** | how filled-in fields become a verdict | verdicts may move without any criterion changing |
| **Evidence** | what proves the three above are right | nothing moves; confidence changes |

A fifth kind, **explanation**, is written for people and decides nothing.

## The index

### What the model actually reads

Only one file reaches the model, and only part of it.

| File | Lines | What it is |
|---|---|---|
| `prompt.md` | 757 | Four documents in one. Lines 98–599 are sent to the model: the criteria (steps 0–3) and the output schema. Lines 1–52 are a changelog, 53–97 are notes on running it, and 600–757 specify how the surrounding machinery should behave. **None of that last group is read by anything** — not by the model, not by the code. |

`pe/prepare.py` cuts the file by searching for heading text (`## Instruction
prompt`, `## Output schema`, `## Toggle decisions`). Those headings are an
interface: the anchors are declared in `task/task.yaml`, and renaming a heading
without updating them breaks the pipeline.

### What the machinery reads — the pack

`task/` is the question-specific half. Swap this folder and the same machinery
answers a different question.

| File | Lines | What it is |
|---|---|---|
| `task/task.yaml` | 112 | The pack's identity. **The one place the version number is written.** Also the prompt anchors and the output filenames. |
| `task/record.yaml` | 309 | What a record must contain, and the validation rules. |
| `task/decide.yaml` | 124 | Which fields the verdict is a function of, the degraded-text cap, and the consistency-check wording. |
| `task/report.yaml` | 322 | Triage tiers and reporting. |
| `task/change.yaml` | 134 | What counts as a change when two runs are compared. |
| `task/rules.py` | 956 | The decision procedure in code: `stage_a`, `stage_b`, `decide`, `checks`, `metrics`. |
| `task/screens.py` | 343 | The six review screens. |
| `task/change.py` | 336 | Classifying what moved between two runs. |
| `task/report.py` | 336 | Rendering the summary. |

### What the machinery is — the harness

`pe/`, 3,535 lines across 12 modules. It names this task nowhere in code, and
`tests/test_seam.py` holds that line by reading every module and rejecting a task
word in any name, string or key. This half is meant to be reusable unchanged.

### The evidence

| What | Count | State |
|---|---|---|
| `CURATOR-RULINGS.md` | 26 rulings, 21 distinct papers | Prose. **Nothing reads it programmatically.** |
| `ACCEPTANCE-v*.md` | 12 documents | Prose. One per version, from 0.0.11 to 0.0.25. |
| `score-acceptance-*.py` | 2 scripts | Each hardcodes one version's papers and expectations. Versions 0.0.11–0.0.22 have no runnable scorer at all. |
| `papers-*.txt` | 19 lists | Plain lists of paper identifiers. **Three are referenced by nothing**: `papers-glyphfix-17`, `papers-glyphfix-51`, `papers-movers-v0021`. |
| `RESCORE-v0.0.21.md`, `EVAL-30-v0.0.10.md` | 2 | One-off measurement records. |

### Explanation

| File | Lines | Audience |
|---|---|---|
| `SKILL.md` | 426 | How to run it and how to change it. Read by both people and Claude. |
| `EXPLAINED.md` | 696 | The whole system in plain language, with every number measured. |
| `DESIGN-stage-b-gate.md` | 428 | One design record, for the text-quality cap. |

### The guards

15 test files. The four that hold the structure, rather than testing behaviour:

- `test_seam.py` — the harness may not name the task.
- `test_task_version.py` — the version is declared once and cannot be restated.
- `test_prompt_pack_agree.py` — every value written in both `prompt.md` and the
  pack must match.
- `test_spec_self_consistency.py` — `prompt.md` must not say two different things
  about one case.

---

## The rulings ledger holds two different kinds of answer

**This is the finding that should shape any work here, and it is not written down
anywhere else.**

`CURATOR-RULINGS.md` records what the curator decided about a paper. It is
described as ground truth. But the curator is answering a different question from
the one the classifier is asked, and in two places the two answers are
*supposed* to differ.

Scoring the current corpus against the ledger naively gives 19 agreements out of
21 papers, with two apparent failures:

| Ruling | Paper | Curator | Classifier |
|---|---|---|---|
| 4 | `10.1038/s41586-022-05060-x` | no | yes |
| 23 | `10.1038/s41586-021-03852-1` | no | yes |

**Neither is a bug. Both are the system working as designed**, and each ruling
says so in its own text.

**Ruling 4** turns on species. The perturbation is surgical coronary ligation in
mice; the deposited data is human. The ruling's own consequence note records the
agreed resolution: *record the organism of the paired material and do not call on
it* — the verdict stays species-agnostic and a person applies the species filter
afterwards, because a non-human dataset can be a legitimate curation target. The
record for that paper carries `paired_organisms: ['mouse']` and
`paired_organism_human: False`. A downstream reader applying the filter gets the
curator's `no`. The classifier is right to say `yes`.

**Ruling 23** turns on whether the treated samples reached the deposited
collection. The ruling says this outright: *"Whether samples reached the
CELLxGENE collection is a fact about the deposited dataset, not about the paper,
and no text-only classifier reaches it at any prompt version."* The classifier
reads papers. It cannot know this and should not guess.

So the ledger contains at least three kinds of entry, currently stored in one
column with no marking:

1. **Binding** — the verdict the classifier must produce. Most entries.
2. **Out of scope** — the curator used information the classifier cannot see.
   The classifier's different answer is correct. Rulings 4 and 23.
3. **Not adopted** — the curator's answer was considered and declined. Rulings
   16 and 21, which *are* marked, in prose.

Two further complications a reader has to handle: three papers have been ruled on
twice and the later ruling wins — ruling 14 reverses ruling 11 on the same paper
— and one verdict is `not_applicable`, which starts with the same two letters as
`no`. Writing the check that produced the table above took two attempts, because
the first one read `not_applicable` as `no` and reported a third failure that did
not exist.

**The risk this creates is worse than a wrong number.** Anyone who builds an
automatic check over this file without the distinction gets two permanent
failures, and the obvious way to make them go away is to change the criteria so
the classifier matches the curator. That would make the classifier worse: it
would be guessing at species and at deposit membership, which are exactly the two
things the project decided it must not do.

---

## What is already guaranteed

Worth stating plainly, because it is more than most systems of this kind have.

- The version number exists in one place and is substituted into the prompt, so
  it cannot go stale.
- A hash over every rule-bearing file says whether two records were produced
  under identical rules. That is a comparison, not an opinion.
- Every quote in a result is checked against the specific source it claims.
  Unverifiable quotes are dropped and the verdict recomputed.
- The harness contains no task vocabulary, enforced by reading the code.
- Values written in both the prompt and the pack must agree, enforced.
- The prompt must not contradict itself, enforced over 20 cases.
- The skill's own tests run in the automated checks, and a run that discovers no
  tests fails rather than passing.

## What is not guaranteed

Ranked by how much it would cost to be wrong.

**1. Nothing checks the ground truth.** No test, script or automated check reads
`CURATOR-RULINGS.md`. A criteria change can contradict a ruling from four
versions ago and nothing notices until a person re-reads the file. Twenty-one
papers of hard-won curator judgement currently sit outside the loop.

**2. Acceptance evidence is rebuilt from scratch each version.** Twelve prose
documents, two scoring scripts, nineteen paper lists. Versions 0.0.11 through
0.0.22 cannot be re-scored today by any uniform means. Each new script is a fresh
opportunity for the mistake that has already happened: the version 0.0.25 gate
read a key the checker never returned, rejected all fourteen defect claims, and
would have reported a clean pass over a mechanism that never ran.

**3. The version number does not say what changed.** `task.yaml`'s comments
carefully distinguish "this changed a criterion" from "this changed no criterion
at all", because that distinction decides whether a change costs a full re-score
or nothing. The number itself — 0.0.24, 0.0.25 — carries none of it, so the
property gets re-stated by hand as a criterion in each acceptance document.

**4. One fact, many copies.** The version 0.0.12 story is told in five files.
Good teaching, and the shape the repo warns about everywhere else.

**5. Dead weight.** Three paper lists referenced by nothing. Two scorers for
twelve versions.

---

## The proposed standard

Three layers, smallest first, each useful alone. None of this is built.

### Layer 1 — make the ground truth executable

Give each ruling a machine-readable header next to its prose: paper, verdict,
date, **kind** (binding / out of scope / not adopted), and what supersedes it.
The prose reasoning stays exactly where it is and stays the important part.

Then one checker scores any set of results against the binding entries only, and
reports the out-of-scope ones separately with the reason they differ. Run it in
the automated checks, blocking on binding rulings.

This is not inventing a rule language. It is putting a structure on a table that
already exists, plus the distinction the ledger currently keeps in prose.

### Layer 2 — one acceptance format

One runner, plus a small expectations file per version. The twelve existing
documents stay as the written record; only the scoring becomes uniform. A version
that declares an expectation the runner cannot evaluate fails, which is the
defence against a gate that passes over nothing.

### Layer 3 — let the version say what it changed

One declared field in `task.yaml`: does this revision touch the criteria. Then
the machinery can enforce automatically what the last two acceptance documents
wrote out by hand — a revision that claims to change no criteria must move no
verdict.

### What should not be done

**Do not turn the decision rules into data.** `task/decide.yaml` argues against
it and the argument holds: a rule table needs a vocabulary of conditions, that
vocabulary is a guess until a second question needs one, and the recorded
decision of 2026-08-31 says so explicitly. Nothing found while writing this map
changes that.

**Do not make rulings 4 and 23 pass by changing the criteria.** See above.

---

## Decisions still open

1. **Does the "out of scope" category belong in the ledger, or in a separate
   file?** Keeping it in the ledger preserves the reasoning next to the paper.
   Splitting it makes the binding set obviously the binding set. The ledger is
   also the handoff document for a new curator, which argues for keeping it
   together.

2. **What happens when a ruling and a later ruling disagree?** Today the later
   one wins by date and a reader works it out. Ruling 14 reverses ruling 11 with
   no marking on 11 itself.

3. **Should the twelve historic acceptance documents be back-filled into the new
   format, or only new ones?** Back-filling makes the whole history re-runnable
   and costs real effort; not doing it leaves a line before which evidence is
   prose only.

4. **Who is the reader of last resort?** The four goals stated for this work —
   change criteria faster, hand the work to another person, reuse the pattern for
   other questions, and publish the method — mostly agree, but they disagree on
   `EXPLAINED.md`. For publication it is the centrepiece. For a working handoff
   it is a second place where facts can drift.

5. **How much of `prompt.md` should stay one file?** Splitting the model-facing
   part from the changelog and the machinery notes is clean, and it touches the
   heading-based cutting the pipeline depends on. Worth doing carefully or not at
   all.
