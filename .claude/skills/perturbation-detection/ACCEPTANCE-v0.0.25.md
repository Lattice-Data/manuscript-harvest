# v0.0.25 acceptance — PROTOCOL and PREDICTIONS

**Status: written 2026-09-16, BEFORE any run.** Results are appended at the
bottom with nothing above them edited. Score with
`python score-acceptance-stageb.py`, which exits non-zero when any criterion
fails.

Set: **`papers-accept-stageb.txt`, the same 24 papers as v0.0.24.** Reused
deliberately — it is the degraded-text population, it is where all four of
v0.0.24's flips were, and reusing it makes the self-report stability numbers
directly comparable across three versions. **Two runs.**

## What changed

Stage B's trigger. It was `processing_status = "partial" OR text_completeness !=
"full"`; it is now the harness having withheld text, **or** a `text_defects`
entry whose quote the harness verified against the source it cites. Scope, by
curator decision: a defect in the **main** source caps, and `no_methods_content`
caps from **any** source. Both paper-level fields stay on the record and stop
deciding anything.

Nothing below Step 0 is touched. No criterion moved.

## The gate

| # | criterion | why |
|---|---|---|
| 1 | **self-report agreement across the two runs** | still reported, but **no longer the blocker** — the determination does not read those fields any more. Measured for continuity: 27/30 at v0.0.23, 20/24 at v0.0.24 |
| 2 | **`stage_b_capped` agrees across the two runs, on all 24** — and the gate must have been **EXERCISED**: at least one cap in either run decided by a QUOTE-VERIFIED `text_defects` entry | the NEW blocker. This is what the version is for, and it is the first version in which the cap has a trigger that can be checked. The exercise half is not decoration — see below |
| 3 | every kept defect carries a verified quote | asserted by `pe.validate`; a failure here means the verifier was bypassed |
| 4 | the two rung-3 papers stay capped | `harness_withheld` alone, independent of anything the model says |
| 5 | no non-negative is capped | the asymmetry |
| 6 | `stage_a` does not move | a Step 0 change must not reach the criteria |

## Why criterion 2 carries an exercise count

Agreement over a mechanism that never ran is not evidence about the mechanism,
and at v0.0.25 that was not hypothetical. `task.rules.validate_defects` read
`check["verified"]`, a key `verify_quote_sourced` has never returned, so **all 14
quotable defect claims in run 1 were dropped** — including one scoring 1.0
against the source it cited. The cap fell back to `harness_withheld` and
`no_methods_content`, both deterministic, and criterion 2 as originally written
would have agreed 24/24 and certified a dead gate. Fixed in `task/rules.py`; the
run is re-scorable from stored `raw/` at no model cost.

So the count excludes two routes. A cap from `harness_withheld` would have fired
under v0.0.24 too. A cap from `no_methods_content` proves nothing either — that
kind is *falsified* against `section_chars`, never quote-checked, so it is the
one defect that survives a broken quote path untouched. Under the bug it capped
`2021.09.16.460628` on its own and made a naive counter read "exercised by 1".
**Only a quote-verified defect shows the path is alive.**

The bar is **≥ 1**, and zero is a FAIL rather than a remark — unlike criterion 3,
whose property is independently held by `task.rules.stage_b`. Nothing holds this
one but the run. A higher threshold would be a number chosen rather than
measured, and could fail a set that is legitimately clean.

**Measured baseline**, run 1 re-validated after the fix: 13 verified defects
across 10 papers (6 `garbled_run`, 6 `ends_mid_sentence`, 1 `no_methods_content`),
7 of 24 papers capped — **4 by a quote-verified defect**, 2 by `harness_withheld`,
1 by `no_methods_content`. The scorer prints that count beside the verdict.

Verified both ways: against run 1's pre-fix records the criterion reports
`FAIL: NOT EXERCISED` and exits 1; against the re-validated records it reports
`PASS (exercised by 4 paper(s))`.

## Predictions

**Cap stability: 24 of 24.** The claim is that a cap keyed on a verifiable quote
is reproducible where one keyed on an adjective was not. If criterion 2 fails,
the instability is in what the model *notices*, not in how it *reports* it, and
no amount of evidence plumbing will fix that — the next move would be to make
the cap advisory, the option recorded and rejected in
`DESIGN-stage-b-gate.md` §6.

**`science.aat1699` → `no`, and this is the prediction to read rather than
score.** Its defect is three garbled runs in a supplementary source, which the
scope rule says does not cap. That lands the paper on **curator ruling 6's own
answer**, which the old trigger overrode to `unclear`. It also means the paper
is no longer protected by the cap at all, on the strength of a rule about which
sources can hide a pairing sentence. **If the curator disagrees, this is the
decision to revisit, not the code.**

**The `methods_missing` claims survive falsification.** `2021.09.16.460628`
supplies 0 methods-labelled characters and `science.aat1699` supplies 228 —
both below the 2,000 threshold, so neither is refuted. A refutation of either
would mean the threshold is wrong, and the measurement behind it is in
`record.yaml`.

**`partial` + `full` should now APPEAR rather than being an anomaly.** It is the
legal way to say "one source is garbage, the article is whole", and two papers
said exactly that in the v0.0.24 runs. A version where it vanishes has probably
talked the model out of a distinction it was making correctly.

**Caps released: 10 to 13 of the 15.** v0.0.24 released 10. The scope rule
should release `aat1699` as well; the two rung-3 papers cannot be released.

**Stage A: 24 of 24 stable, 0 movements.**

## What would falsify the design

- **Criterion 2 fails.** The cap still moves between runs even keyed on a quote.
  Then the variance is in noticing, and the fix is not more evidence.
- **The defect array is populated where v0.0.24 said `full`.** Asking for
  defects made the model hunt for them — the v0.0.10 attractor, in the shape the
  guards were written against. Watch the six anchors: all six were `full` twice
  under v0.0.24, and all six should carry `[]`.
- **A `no_methods_content` claim on a paper with a real methods section.** The
  falsification check should catch it; if one survives, the threshold is wrong.

## Cost

24 papers x 2 runs at the $1.50/paper measured in the v0.0.24 pass ≈ **$72**.
The corpus run stays behind this gate, at ~$721.

## How to run it

From the skill directory:

```bash
W=~/.manuscript-harvest/perturbation/work-accept-v0025-r1
python -m pe.prepare --set papers-accept-stageb.txt --work "$W" --corpus /abs/path/to/corpus
./pe/run_headless.sh "$W"
python -m pe.validate --work "$W"
```

Then the same for `-r2`, and `python score-acceptance-stageb.py --r1 ... --r2 ...`.
No `--write-corpus` on either. Pass the corpus as an **absolute** path: the
relative form in SKILL.md is wrong from a git worktree, where it resolves to a
root with no corpus tree.
