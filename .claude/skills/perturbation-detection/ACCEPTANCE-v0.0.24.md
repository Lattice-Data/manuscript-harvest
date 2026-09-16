# v0.0.24 acceptance — PROTOCOL and PREDICTIONS

**Status: written 2026-09-16, BEFORE any run.** Predicted and measured are
different claims, so the results go at the bottom of this file, appended, with
nothing above them edited. Score with `python score-acceptance-stageb.py`, which
exits non-zero when any criterion fails.

Set: `papers-accept-stageb.txt`, 30 papers in five labelled groups. **Two runs**,
because one run cannot separate an attractor from run-to-run variance.

## What changed, and what it deliberately did not

v0.0.24 touches Step 0 and nothing below it. `pe.prepare` now splices an
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

## Why `papers-accept-v0023.txt` could not be reused

It holds exactly one degraded-text paper, `science.aat1699`, and that one is
capped — so the cap masks its Stage A answer and the set can neither see a cap
release nor a cap that wrongly fires. Same blind spot `papers-50b` had.

## The gate

| # | criterion | why it is here |
|---|---|---|
| 1 | **the text-quality self-report agrees between the two runs, on all 30** | the whole point of the version. BLOCKER |
| 2 | the two rung-3 papers stay capped | where the harness itself truncated, the cap must not depend on the model's opinion |
| 3 | no `yes`, `unclear` or `not_applicable` is ever capped | Stage B's asymmetry is the rule it exists to express |
| 4 | `stage_a` does not move on any paper | Step 0 only was edited; a Stage A flip means the new block reached the criteria |
| 5 | every strict expectation is met | the anchors and the positives |
| 6 | determinations agree between the two runs | the consequence criterion 1 is aimed at |

Failing 1 means stating the cuts was not enough, and Part 2 of the design (an
evidenced claim the harness verifies) is the next move rather than a fallback.
Failing 4 is the attractor, and is the reason for two runs.

## Predictions

**Self-report stability: 30 of 30.** Baseline is 27 of 30 — `bloodadvances`
(`full` → `truncated`, and the determination moved), `healun`
(`partial`/`full` → `ok`/`truncated`), `s41586-021-03852-1` (`truncated` →
`full`). All three are in group D of the new set.

**Caps released: 5 to 10 of the 15.** This is the loosest prediction in the
document and it is loose on purpose — per paper, the question is whether the
reported defect was the pipeline's cut or the publisher's, and that cannot be
read off a sidecar. What can be stated is the shape of each end of the range:

| must NOT release | why |
|---|---|
| `sciimmunol.adz8650`, `genes15030298` | rung 3: the harness truncated them, and `pe.validate` overrides a `"full"` claim to `"truncated"` on a harness-truncated text. The cap here is a harness fact |
| `science.aat1699` | 323 control characters per 10k and three pages missing — the one corpus text that is genuinely broken. **A release here is a finding to read, not an automatic failure: curator ruling 6 on this paper is `no`, which is what a released cap would produce.** Recorded either way |
| `2021.09.16.460628` | the extractor also found no methods label anywhere, so the model's `methods_missing` has independent support |

The other 11 are where a release is expected, and `s41591-021-01586-1` is the
clearest case: its supplied text ends mid-clause on a medRxiv licence footer
(`"...in perpetuity. It is made"`) that running-header removal cut in half.

**Determination movement against the v0.0.22 baseline: unattributable, by
construction.** There is no v0.0.23 corpus run, so a `no` → `yes` on these 30
could be v0.0.23's infection rule rather than anything here. The scorer says so
rather than absorbing it, and takes `--baseline` for a v0.0.23 run over the same
set if one is made. **This is a cost of the decision not to run the corpus after
v0.0.23, not a defect of this set.**

**Stage A: 30 of 30 stable, and 0 movements.** Step 0 does not feed Stage A
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

## Cost

30 papers x 2 runs at the $1.84/paper measured in the v0.0.23 acceptance ≈
**$110**. A v0.0.23 baseline over the same set, if wanted for criterion 6, is one
more run: **+$55**.

For comparison: the full 392-paper corpus is ~$720 at the same rate, and it has
not been run since v0.0.22.

## How to run it

From the skill directory, not the repo root (`pe` and `task` resolve relative to
it):

```bash
W=~/.manuscript-harvest/perturbation/work-accept-stageb-r1
python -m pe.prepare --set papers-accept-stageb.txt --work "$W" --corpus ../../../corpus
./pe/run_headless.sh "$W"
python -m pe.validate --work "$W"
```

Then the same with `-r2`, and `python score-acceptance-stageb.py`. Two things to
get right, both of which have cost a run before:

- **No `--write-corpus` on either run.** An acceptance run is not a corpus
  update, and the stored records are the v0.0.22 baseline these are compared to.
- **`--corpus ../../../corpus`, explicitly, on `pe.prepare`.** A stale 382-paper
  tree sits inside this directory; both trees are gitignored, so a run over the
  wrong one is invisible in git. Confirm `30/30 prepared` before stage 2.
