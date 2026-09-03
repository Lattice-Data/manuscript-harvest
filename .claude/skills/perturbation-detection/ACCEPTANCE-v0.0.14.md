# v0.0.14 acceptance test

Run 2026-09-03, twice, on the 33 papers of `papers-accept-v0014.txt`. Under test:
the fourteen internal contradictions v0.0.14 resolved, of which **one changes a
criterion** — worked example 5 and the precedence note re-decided under the
clinical-therapy rule's third condition.

66/66 papers ran clean: 0 FAIL, 0 ABORT, 0 unparseable. Both runs validated
33/33, at 193/193 and 211/211 quotes verified, 0 failed, 0 misattributed, 0
perturbations dropped.

## Result: PASS

Every determination change is reproducible across two runs and accounted for by
a named mechanism. Zero `UNEXPLAINED`. The controls held. One claim is
**weaker than the rest** and is called out as such: the 8 partial-text papers
have no two-run baseline.

## The set, and why it is not `papers-50b.txt`

25 papers drawn from `papers-50b.txt` with `random.Random(14).sample(...)`, plus
**all 8 papers whose `processing_status` is "partial"**.

The 8 are the point. `papers-50b` contains none of them, so the standard
50-paper run **cannot exercise the partial-text confidence ceiling at all** — it
would have reported "1f changed nothing" where the truth is "1f was never
tried". That is the vacuous-pass shape, inside the acceptance protocol itself,
and it is the reason this set exists rather than the usual one.

The set carries its own controls, because a change that moves everything has
stopped discriminating:

| | expected to move | expected NOT to move |
|---|---|---|
| of the 25 | 6 suppressing under `incidental_clinical_therapy` — the rule this version re-decides | **10** suppressing under `reporter_or_marker`, whose scope did not change |
| of the 8 | 3 positives pinned at confidence **exactly 0.38**, the old ceiling | 5 unclears, which the ceiling still governs |

## Determinations

Against the preserved two-run v0.0.12 baseline, over the 25 papers that have one:

| | changed vs baseline | beyond the noise floor |
|---|---|---|
| r1 | 2 / 25 | **1** — `STAGE-B` |
| r2 | 1 / 25 | **0** |

Noise floor on this subset: the baseline disagrees with itself on 1/25
(`scitranslmed.abh2624`, one of the three papers `baseline-v0012-50b/noise-floor.json`
already names).

**v0.0.14 self-agreement over all 33: 32/33.**

### The one paper that moved beyond the noise floor, and why it is not this change

`10.1038_s41586-020-2496-1`, `no` -> `unclear` in r1 only.

| | `processing_status` | Stage A | capped | reported |
|---|---|---|---|---|
| v0.0.12 baseline r1 | ok | no | false | no |
| v0.0.12 baseline r2 | ok | no | false | no |
| **v0.0.14 r1** | **partial** | no | **true** | **unclear** |
| v0.0.14 r2 | ok | no | false | no |

The model called its own text `partial` on byte-identical input in one run of
four, entering Stage B's cap. This is the `processing_status` / `text_completeness`
self-assessment wobble already recorded in the v0.0.11 and v0.0.12 acceptance
tests (`j.cell.2019.08.008`, `j.jcmgh.2025.101665` — same shape, `full` and
`truncated` on identical input). It is **the same paper disagreeing with itself
under v0.0.14 as well**, which is what makes it that instability rather than an
effect.

**This was worth ruling out rather than assuming**, because v0.0.14 edits Step 0
(the 1e sentinel), and a newly-salient rule making the model more attentive to
degradation is exactly the attractor mechanism this project has measured three
times. r2 falsifies it: same prompt, same paper, `ok` again.

## The criterion change fired, on the right paper, for the stated reason

`10.1126_science.aat1699` (Wilms' tumour atlas), **`yes` -> `unclear` in BOTH
runs**, landing in triage **P2** in both.

Under v0.0.12 the neoadjuvant chemotherapy was reported as a perturbation and
carried the paper to `yes` — one of the papers whose determination rested on
conditions (i) and (ii) with (iii) never applied. Under v0.0.14 it is suppressed
under `incidental_clinical_therapy`, and the model's own `why` applies the
governing question explicitly:

> "The named modality is tied to the sequenced pediatric tumors, but the paper
> treats it as the SETTING rather than its variable — its analysis axis is
> matching tumor cell clusters to normal fetal/mature cell identities, and the
> treatment is mentioned only as standard British practice that reduced cell
> yield, with no treated-versus-untreated comparison and nothing attributed to
> the drug."

That is the reasoning of curator ruling 2 reached from the written rule rather
than in spite of it, which is the property `CURATOR-RULINGS.md` says not to rely
on. It reports `unclear` rather than `no` only because the text is partial and
Stage B caps the negative — and it carries `would_have_paired: "yes"`, so it
**routes to the review queue for a curator to ratify instead of flipping
silently**. That is the design working end to end.

The other 6 clinical-therapy papers in the 25 did not move. The rule is not
firing indiscriminately.

## The partial-text ceiling (1f)

| paper | v0.0.12 | r1 | r2 |
|---|---|---|---|
| `j.mucimm.2026.03.012` | yes **0.38** P3 | yes **0.85** P9 | yes **0.86** P9 |
| `s41586-024-07476-z` | yes **0.38** P3 | yes **0.95** P7 | yes **0.95** P7 |
| `science.aat1699` | yes 0.38 P3 | (moved, above) | (moved, above) |
| the 5 partial unclears | 0.30-0.35 P4 | 0.30-0.30 P4 | 0.30-0.40 P4 |

Two well-evidenced positives were being held at the ceiling rather than at their
evidence, and the ceiling was routing them into the low-confidence review queue
by rubric rather than by any judgment about the paper. Released, they score 0.85
and 0.95 — reproducibly, in both runs, a swing of nearly 0.6.

**The five unclears did not move a tier**, which is the control: the ceiling
still governs negatives and unclears, and it still applies to them.

## The suppression tally: mostly noise, with one real and reproducible shift

`suppressed_candidates` cannot reach a determination — `stage_a` reads four
things and none of them is this array — so nothing above depends on this
section. It matters because the tally is what a boundary argument is made from.

**Measured instability, which is the finding:** the two v0.0.14 runs agree on a
paper's suppression rule SET for only **26 of 33 papers**. Against the v0.0.12
baseline the agreement is 24/33 (r1) and 23/33 (r2). The run-to-run disagreement
is therefore nearly as large as the version-to-version disagreement, so **most of
the apparent re-bucketing is this field's own instability**, not v0.0.14. That is
consistent with its history: it was measured as an attractor when introduced at
v0.0.10.

| | entries | `routine_processing` | `sample_handling_protocol` | `reporter_or_marker` |
|---|---|---|---|---|
| v0.0.12 | 56 | 4 | 5 | 10 |
| r1 | 56 | **1** | 4 | 8 |
| r2 | 52 | **1** | 6 | 7 |

**One shift is reproducible across both runs: `routine_processing` 4 -> 1**, which
is the 1a fix — that rule no longer claims the benchmarking cases
`sample_handling_protocol` owns. Reading the three papers that changed:

- `science.aax6234` — "hash oligo labeling of nuclei" moved `routine_processing`
  -> `sample_handling_protocol`. **This paper carried the defect visibly**: under
  v0.0.12 it had the hashing under `routine_processing` and freeze-thaw under
  `sample_handling_protocol`, one benchmarking population split across the two
  rules inside a single record. Now both sit under one.
- `sciimmunol.adf9988` — M-CSF-supplemented differentiation medium moved
  `routine_processing` -> `derivation_formulation`. A cytokine-supplemented
  medium defining a model is that rule's subject, not routine processing.
- `2024.03.05.583423` — "injection of Pentobarbital to sacrifice pregnant
  females" **dropped entirely**, in both runs. Correct: euthanasia before tissue
  collection is ambient method and was never a candidate for this paper's studied
  perturbation. This is the over-population the "NOT suppressed candidates in an
  ordinary paper" guard exists to prevent.

So the reproducible part is one correct re-homing and one correct de-population.
`sample_handling_protocol` did not reproducibly *gain* the entries
`routine_processing` lost (4 in r1, 6 in r2, bracketing the baseline's 5) — the
two rules stopped double-claiming, but the destination count is inside the noise.

**This is a direct input to the parked tier-2 question.** Triage tier 2 is
defined over exactly this field, and this field's rule set is ~21% unstable
run-to-run. A tier holding 83 of 392 papers therefore carries that instability in
its membership, which is worth knowing before reading ten of them to judge
whether the tier is honest.

## What this licenses, and what it does not

**Licensed.** The clinical-therapy fix changes the determination it was written
to change, reproducibly, with the model stating the governing question as its
reason, and routes the paper to review rather than flipping it silently. The
confidence ceiling releases well-evidenced positives on partial text, by ~0.6,
reproducibly. Neither touched its controls. The thirteen non-criteria fixes moved
nothing: the 392 stored records re-validate to a byte-identical summary CSV, a
byte-identical 5,836-line review screen and 392 byte-identical records apart from
the pack hash.

**Not licensed.**

- **The 8 partial-text papers have no two-run v0.0.12 baseline** — they were
  scored once, in the corpus run, and are absent from every preserved baseline.
  Their changes are shown to be *reproducible* (both v0.0.14 runs agree on all 8)
  but not *attributable* with a measured noise floor the way the 25 are. Closing
  that needs a two-run v0.0.12 baseline over these 8, which nobody has a reason
  to build unless the partial-text rules move again.
- **This is 33 of 392 papers.** The remaining 359 have never been scored under
  v0.0.14. `incidental_clinical_therapy` covers 86 papers corpus-wide and one of
  them moved here; the corpus-wide effect is an extrapolation from a single
  instance, not a measurement.
- The suppression tally's ~21% run-to-run instability is measured but not
  explained, and no change here addresses it.

## The protocol this was run against

`ACCEPTANCE-v0.0.12.md`, with one addition worth keeping: **check that the
acceptance set contains papers the change can act on.** The standard set could
not exercise one of the four fixes at all, and a set that cannot see a change
reports it as harmless. The cheap check is the `processing_status` and
`suppressed_rules` columns of a fresh summary CSV, selected by the rule the
change touches.
