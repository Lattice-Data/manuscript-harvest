# v0.0.24 acceptance — PROTOCOL and PREDICTIONS

**Status: written 2026-09-16, BEFORE any run.** Predicted and measured are
different claims, so the results go at the bottom of this file, appended, with
nothing above them edited. Score with `python score-acceptance-stageb.py`, which
exits non-zero when any criterion fails.

Set: `papers-accept-stageb.txt`, **24 papers** in three labelled groups. **Two
runs**, because one run cannot separate an attractor from run-to-run variance —
and because a single run cannot measure run-to-run agreement at all, which is
this version's whole subject.

## What changed, and what it deliberately did not

v0.0.24 touches Step 0 and nothing below it. `harness.prepare` now splices an
`ASSEMBLY:` block into every prompt stating what the text pipeline removed —
which sources were supplied and how large, how many supplementary files of how
many found, which sections were stripped, which content kinds reached the model,
which budget rung ran — and Step 0 defines `text_completeness = "full"` as
*nothing missing beyond what `ASSEMBLY:` says was removed*, with the pipeline's
own cuts named as non-defects.

**No criterion is edited. No field is added to the schema.** The evidence
sub-object and the `processing_status`/`text_completeness` de-duplication
proposed in `DESIGN-stage-b-gate.md` are deliberately NOT in this version: if
stating the cuts is enough, they are unnecessary, and the v0.0.10 episode is why
a new required field does not ride along with the change that may obviate it.

## Why `papers-accept-v0023.txt` could not be reused, and why this set is 24

It holds exactly one degraded-text paper, `science.aat1699`, and that one is
capped — so the cap masks its Stage A answer and the set can neither see a cap
release nor a cap that wrongly fires. Same blind spot `papers-50b` had.

The set was drafted at 30 and cut to 24. Out went the four `yes` papers on
degraded text, the review, and `j.ccell.2023.08.015`: they were there to show the
cap never reaches a non-negative, and that is a **code invariant rather than a
model behaviour** — `task.rules.stage_b` rewrites a `"no"` and nothing else, and
`tests/test_harness_guards.py` asserts it over every Stage A value and every
legal `text_completeness`. Paying the model to re-confirm a parametrized test was
the weakest six papers in the set.

**What the cut costs, stated rather than glossed:** criterion 3 is now exercised
by exactly one paper, `s41586-021-03852-1` — `unclear` through A5, with a
self-report that flipped between the v0.0.23 runs. The scorer prints **NOT
EXERCISED** rather than PASS if even that paper fails to reach a non-negative
Stage A, because a criterion that passes over zero applicable papers is the
vacuous-pass shape this repo keeps finding.

## The gate

| # | criterion | why it is here |
|---|---|---|
| 1 | **the text-quality self-report agrees between the two runs, on all 24** | the whole point of the version. BLOCKER |
| 2 | the two rung-3 papers stay capped | where the harness itself truncated, the cap must not depend on the model's opinion |
| 3 | no `yes`, `unclear` or `not_applicable` is ever capped | Stage B's asymmetry is the rule it exists to express. One paper exercises it; the scorer says so and refuses to call an unexercised criterion a pass |
| 4 | `stage_a` does not move on any paper | Step 0 only was edited; a Stage A flip means the new block reached the criteria |
| 5 | every strict expectation is met | the six false-positive anchors, the three flippers, and the two harness-truncated papers |
| 6 | determinations agree between the two runs | the consequence criterion 1 is aimed at |

Failing 1 means stating the cuts was not enough, and Part 2 of the design (an
evidenced claim the harness verifies) is the next move rather than a fallback.
Failing 4 is the attractor, and is the reason for two runs.

## Predictions

**Self-report stability: 24 of 24.** The baseline is 3 flips across the 30
papers of the v0.0.23 acceptance — `bloodadvances`
(`full` → `truncated`, and the determination moved), `healun`
(`partial`/`full` → `ok`/`truncated`), `s41586-021-03852-1` (`truncated` →
`full`). All three are in group D of this set, and 22 of these 24 papers are ones that
flipped or could.

**Caps released: 5 to 10 of the 15.** This is the loosest prediction in the
document and it is loose on purpose — per paper, the question is whether the
reported defect was the pipeline's cut or the publisher's, and that cannot be
read off a sidecar. What can be stated is the shape of each end of the range:

| must NOT release | why |
|---|---|
| `sciimmunol.adz8650`, `genes15030298` | rung 3: the harness truncated them, and `harness.validate` overrides a `"full"` claim to `"truncated"` on a harness-truncated text. The cap here is a harness fact |
| `science.aat1699` | 323 control characters per 10k and three pages missing — the one corpus text that is genuinely broken. **A release here is a finding to read, not an automatic failure: curator ruling 6 on this paper is `no`, which is what a released cap would produce.** Recorded either way |
| `2021.09.16.460628` | the extractor also found no methods label anywhere, so the model's `methods_missing` has independent support |

The other 11 are where a release is expected, and `s41591-021-01586-1` is the
clearest case: its supplied text ends mid-clause on a medRxiv licence footer
(`"...in perpetuity. It is made"`) that running-header removal cut in half.

**Determination movement against the v0.0.22 baseline: unattributable, by
construction.** There is no v0.0.23 corpus run, so a `no` → `yes` on these 24
could be v0.0.23's infection rule rather than anything here. The scorer says so
rather than absorbing it, and takes `--baseline` for a v0.0.23 run over the same
set if one is made. **This is a cost of the decision not to run the corpus after
v0.0.23, not a defect of this set.**

**Stage A: 24 of 24 stable, and 0 movements.** Step 0 does not feed Stage A
except through the cap.

## What would falsify the diagnosis

The diagnosis is that the self-report flips because the prompt never said which
cuts were the pipeline's. If criterion 1 still fails after the block is in
place — particularly on `bloodadvances`, whose dangling `Associated Data /
Supplementary Materials` tail is the artefact the block was written for — then
the instability is not an information gap and the evidence requirement is what
the problem needs.

The other visible falsification: caps releasing on all 15, including the two
rung-3 papers and `science.aat1699`. That would mean the block reads as
permission to report `"full"` rather than as a definition of it, which is the
v0.0.10 attractor in the opposite direction. Criterion 2 catches exactly that.

## Size

24 papers x 2 runs at the ~13 requests and ~11.6k output tokens per paper
measured in the v0.0.23 acceptance ≈ **48 spawns, ~620 requests, ~36M tokens**.
A v0.0.23 baseline over the same set, if wanted for criterion 6, is one more
run: **+24 spawns**.

For comparison: the full 392-paper corpus is **~5,100 requests and ~430M tokens**
for a single pass, and it has not been run since v0.0.22. So this pass is **12%**
of the corpus run — and the argument for running it first is not caution but
arithmetic: **a single corpus run cannot measure run-to-run agreement**, so a
full pass would buy an updated corpus while leaving this version's own question
unanswered. If the flip rate has
not moved, Part 2 of the design is the next version, and a version bump means
scoring the corpus again.

Approved order, 2026-09-16: this pass, then the corpus run.

## How to run it

From the skill directory, not the repo root (`harness` and `task` resolve relative to
it):

```bash
W=~/.manuscript-harvest/perturbation/work-accept-stageb-r1
python -m harness.prepare --set papers-accept-stageb.txt --work "$W" --corpus ../../../corpus
./harness/run_headless.sh "$W"
python -m harness.validate --work "$W"
```

Then the same with `-r2`, and `python score-acceptance-stageb.py`. Two things to
get right, both of which have cost a run before:

- **No `--write-corpus` on either run.** An acceptance run is not a corpus
  update, and the stored records are the v0.0.22 baseline these are compared to.
- **`--corpus <absolute path to the repo's corpus>`, explicitly, on
  `harness.prepare`.** A stale 382-paper tree sits inside this directory; both trees
  are gitignored, so a run over the wrong one is invisible in git. Confirm
  `24/24 prepared` before stage 2. **The relative `../../../corpus` that
  SKILL.md documents resolves against the CWD, so it is wrong from a git
  worktree** — there it points at the worktree root, which has no corpus tree at
  all, both trees being gitignored. `harness.prepare` refuses rather than preparing 0
  papers and exiting 0, which is how this was caught on the first attempt at the
  r1 run; pass the absolute path from anywhere but the main checkout.


---

# RESULTS, 2026-09-16

Two runs, `work-accept-stageb-r1` and `-r2`, 24/24 each, validated, every quote
verified, neither given `--write-corpus`. Run in parallel; neither hit a session
limit and no abort sentinel fired. **Input identity verified before the runs
rather than after**: all 24 prompt files byte-identical by sha256 across the two
work dirs, `assembled_text_sha256` equal on all 24, one `pack_sha256`
(`29fe1a8f…`) throughout.

**Verdict: criterion 1 FAILS at 20 of 24. The diagnosis was half right, and the
half that was right is now fixed.**

| criterion | result |
|---|---|
| 1. self-report agrees across runs (BLOCKER) | **FAIL — 20/24** |
| 2. harness-proved cap still holds | PASS — but via `harness.validate`'s override, see below |
| 3. no non-negative was capped | PASS (exercised by 3 papers) |
| 4. `stage_a` did not move | **FAIL — 1 paper** |
| 5. expectations met | **FAIL — 2 papers, both prediction errors** |
| 6. determination stable across runs | FAIL — 5 papers, downstream of 1 and 4 |

## Where the instability went, by group

| group | agreement | flips |
|---|---|---|
| **D — the 3 previously-known flippers** | **3/3** | none |
| **C — the 6 harness-false-positive anchors** | **6/6** | none, all `full` |
| **A — the 15 formerly-capped papers** | **11/15** | `science.aat1699`, `2021.09.16.460628`, `s41586-023-06981-x`, `atvbaha.122.317953` |

**The three papers the change was written for all stopped flipping.**
`bloodadvances.2023011445` — whose dangling `Associated Data / Supplementary
Materials` tail is the artefact `ASSEMBLY:` was written to name — is `ok`/`full`
and `no` in both runs. `healun.2026.02.1666`, which previously took two different
routes to one cap, is `ok`/`full` in both. So the dangling-heading diagnosis was
correct and naming the cuts fixed it.

**And none of the six anchors over-fired**, which is the attractor the block
could have caused and did not: every paper whose *harness* facts look degraded
still reported `full`, in both runs.

**10 of 15 caps released**, the predicted effect, landing inside the predicted
5-to-10 range.

## But the residual instability is a DIFFERENT mechanism, and it is measurable

All four flips are in the degraded-text population, which **no previous
acceptance set could measure** — `papers-accept-v0023.txt` held one such paper
and `papers-50b` held none. So this is not a regression from 3/30; it is the
first measurement of a rate that was never observed. Reading the four:

| paper | r1 | r2 | what the two runs actually said |
|---|---|---|---|
| `science.aat1699` | `partial`/`full` | `ok`/`full` | r1: *"three large runs of garbled, non-linguistic characters"* in the SUPPLEMENT. r2: the main source is *"a coherent, complete Science report"*. **Both statements are true, about different sources.** The flip is a choice of which source to characterise, not a disagreement about facts |
| `2021.09.16.460628` | `partial`/`methods_missing` | `ok`/`full` | r1: *"no methods content anywhere in either source, although the body repeatedly points to one ('see Computational Methods', 'see Methods')"* — specific, checkable, and corroborated by the extractor's own `body_sections_missing`. r2 did not check |
| `s41586-023-06981-x` | `ok`/`full` | `partial`/`truncated` | r2 quotes the break verbatim: the Results *"break off mid-sentence inside the article at 'Between 18 and 22 GW, tissue growth had pressed these ventricular walls close together (Fig. 3b,c and'"*. **r2 is right and r1 is wrong** |
| `atvbaha.122.317953` | `ok`/`truncated` | `ok`/`full` | r1 claims `truncated` and names no locus at all |

**Three of the four are adjudicable by a quote, and the fourth is the unevidenced
claim that would be normalised away.** That is Part 2 of `DESIGN-stage-b-gate.md`
arriving as a measurement rather than an argument, and this document's own
falsification clause named it in advance.

**One correction to Part 2 as designed, from `s41586-023-06981-x`:** it specifies
that `ends_mid_sentence` evidence must sit in the cited source's TAIL. This
paper's break is mid-article, with content after it. The tail rule would have
rejected a correct claim, so it must go.

## Criterion 2 passed for the wrong reason, and that is a spec defect

`sciimmunol.adz8650` and `genes15030298` — the two rung-3 papers — **both
reported `ok`/`full` in both runs**, and `harness.validate` overrode each to
`truncated` and re-capped (`STAGE-B-CAP MODEL=no`). The cap held, so the
criterion passes.

But the model was not wrong by the rule as written. Step 0 says `"full"` means
*nothing missing beyond what `ASSEMBLY:` says was removed* — and for a
budget-truncated paper, what is missing is exactly what `ASSEMBLY:` reports as
removed. **`full` is the literally correct answer to the question v0.0.24
asks**, while `harness.validate` treats that same answer as an error to correct. Two
parts of one spec, disagreeing, with the harness winning silently. A one-line
carve-out fixes it: a budget truncation `ASSEMBLY:` reports is not among the cuts
that may be disregarded.

## Criterion 4: one Stage A flip, unattributed

`s41586-021-03852-1`: self-report stable (`ok`/`full` both runs), pairing moved —
`stage_a` `unclear` in r1 and `yes` in r2, as two of its three perturbations went
from `unclear` to `yes`. This is the *"three organoid growth conditions"* paper
the v0.0.23 acceptance recorded as a genuine textual gap, and it was `unclear` in
both v0.0.23 runs. One observation cannot say whether `ASSEMBLY:` moved it —
telling a model that every table was removed is a plausible mechanism for
re-weighing an unidentified condition — or whether a documented knife-edge simply
fell the other way. **Recorded as open, not explained.**

## Criterion 5: two prediction errors, not code defects

Both were verified by reading the paired perturbation rather than the label:

- `s41586-021-04345-x` (group C anchor, expected `no`) → `yes`, paired on
  *"SARS-CoV-2 infection (naturally acquired, RT-qPCR-confirmed)"*.
- `s41598-022-17832-6` (group A, `yes` disallowed) → `yes`, paired on *"cigarette
  smoking (healthy current smokers versus never smokers)"*, with the record citing
  the acquired-exposure subject test explicitly.

Both are **v0.0.23's C1 applied correctly**; both expectations were written from
the v0.0.22 baseline, which predates C1. This is the attribution gap this
document predicted would be unavailable — and reading the agent closed it without
paying for a v0.0.23 baseline run. The gate stays failed on them: an author who
can relabel his own misses is not running a gate.

## Part 3's premise is refuted by its own evidence

The design called `processing_status: partial` with `text_completeness: full`
internally contradictory and proposed collapsing the two fields. Both instances
in these runs carry a located justification that says otherwise —
`s41586-021-04345-x`: *"supp2 (the Nature Reporting Summary) arrived as a large
run of mojibake and is essentially unreadable; the main article, including a
complete Methods section, supp1 and supp3 are complete"*. That pair is the only
way the current schema can say **"one source is garbage, the article is whole"**,
and `aat1699`'s flip is caused by the absence of a per-source way to say it.
Collapsing the fields would delete a distinction the model is using correctly.
Part 3 needs rewriting: per-source text quality, and a home for `garbled_run`.

## What the pass spent

| | papers | requests | output tokens | total tokens | wall-clock | model time |
|---|---|---|---|---|---|---|
| r1 | 24 | 262 | 208,948 | 18,862,331 | 47m | 45m (96%) |
| r2 | 24 | 260 | 211,506 | 18,605,742 | 47m | 45m (96%) |

**Per paper: median 8,690 output tokens over 10 requests in r1 (p10 4,388, p90
13,206); 8,838 over 10 in r2.** Below the v0.0.23 set's 11,610 because this set
is shorter papers, not because less was done per paper. Floor in both runs is
`10.1096_fj.202300601rrr` — 2,676 and 3,067 output tokens over 6 requests — so
every paper here was reasoned about rather than assigned. Run in parallel, so
~50 minutes of wall clock for both. No price-drift warning in either run.

## What this means for the corpus run

**It does not start.** The order agreed on 2026-09-16 was this pass and then the
corpus, conditional on the gate; the gate failed on its blocking criterion. A
full corpus run under v0.0.24 would score 15-odd degraded papers whose cap is
still a coin flip, and Part 2 is a prompt change, which means a version bump and
a second full pass. This pass was 12% of one corpus run and it has already paid
for itself twice over: it found the spec defect in criterion 2 and it refuted Part 3's premise.
