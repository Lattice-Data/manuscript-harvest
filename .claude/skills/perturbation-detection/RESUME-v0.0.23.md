# v0.0.23 — where this stopped, and how to resume

Written 2026-09-15 with a credit limit imminent. **Run 1 is complete, validated
and safe on disk. Run 2 was interrupted mid-flight.**

## State

| | work dir | raw results | validated | usage |
|---|---|---|---|---|
| run 1 | `work-accept-v0023-r1` | **30/30**, 0 FAIL | yes, 30/30, all quotes verified | **$54.72**, 406 requests, 81m |
| run 2 | `work-accept-v0023-r2` | **10/30** at interruption | not yet | $17.63 and counting |

Both work dirs are under `~/.manuscript-harvest/perturbation/`. Neither was run
with `--write-corpus`, so **the shared corpus is untouched** — an acceptance run
should not overwrite corpus results, and the full run is what earns that.

## Resume

Run 2 is idempotent: `run_headless.sh` re-reads the manifest and runs only what
has no result, so re-running costs the remainder and not the whole set.

```bash
cd .claude/skills/perturbation-detection
./pe/run_headless.sh ~/.manuscript-harvest/perturbation/work-accept-v0023-r2 3
python -m pe.validate --work ~/.manuscript-harvest/perturbation/work-accept-v0023-r2
python pe/score_acceptance.py
```

**`pe.validate` MUST run on both work dirs before scoring.** The scorer reads
`validation.stage_a` for the two cap-masked papers, and on an unvalidated run
that field is absent, so it falls back to the capped determination and prints a
spurious UNSTABLE. That is exactly what `j.healun.2026.02.1666` showed at the
moment of interruption — an artifact of run 2 being unvalidated, not a finding.

If a usage limit tripped the abort sentinel, `work-accept-v0023-r2/.session-dead`
holds the verdict and the reset time. Wait for the reset and re-run the same
command; papers already done are skipped.

## Run 1 result, for the record

**The headline check passed.** `j.cell.2021.07.023` is `yes` carried by
`SARS-CoV-2 infection (naturally acquired, PCR-confirmed)`, with corticosteroids
moved to `incidental_clinical_therapy`. Under v0.0.22 the same `yes` rested on
the corticosteroids while the infection sat suppressed. Right answer, right
premise — and a label-only comparison could not have seen the difference.

- All 8 anchors held (rulings 2, 6, 9, 10, 12, 13, 14, plus `elife.104978.2`).
- All 6 predicted infection movers moved `no` → `yes`.
- Contraception `yes`→`no`; the review → `not_applicable`;
  `immuni.2020.03.019` `unclear`→`no`.
- Both cap-masked anchors read `stage_a=no, det=unclear, capped=True` — the
  criteria answer we ruled, with Stage B doing what it is for.

### Three corrections, all to PREDICTIONS rather than to the code

1. **`s41467-024-49037-y` (periodontitis) moved `no`→`yes`, unpredicted, and the
   reasoning is sound.** Nobody applied the bacteria, but the paper's stated
   question IS the host response: it coins "keratokines" for cytokine
   upregulation in response to challenge, with three results sections and
   abstract billing. C1's acquired-exposure subject test, correctly applied. I
   had grouped it as a non-mover off a TITLE REGEX, and its title does not name
   infection. Recorded as `PASS*` — passed on inspection, still counted against
   criterion 3, because a gate that lets its author relabel failures is not a
   gate.
2. **`s41586-021-03852-1` did not move, and C4 was right not to move it.** The
   DSS arm, whose readout is nameable, got `paired=no` as C4 asks. The TNF/IFNγ
   arms did not, because the paper counts "three organoid growth conditions" and
   never identifies them — the genuine textual gap C4 reserves `unclear` for.
   The curator's `no` here rested on the no-UMAP / not-in-CELLxGENE reasoning
   that is out of scope by decision D5.
3. **The ruling-6 and ruling-21 expectations were written against the wrong
   field.** Both papers are Stage-B capped; the criteria claim is `stage_a`.
   Fixed in the scorer and in `ACCEPTANCE-v0.0.23.md`.

## Two things this changes for the full corpus run

**The mover estimate is LOW.** ~8 of 21 came from title-matching, and the
periodontitis paper shows the same under-count reaches papers whose subject is
an infection response without saying so in the title. Expect more than 8, and do
not treat the predicted set as a ceiling when reading the full run.

**Cost is higher than planned.** $54.72 for 30 papers is $1.82/paper, not the
$1.30 the smoke run suggested. At that rate the 392-paper corpus is **~$715**,
not the $500-600 in `ACCEPTANCE-v0.0.23.md`. That number needs saying out loud
before the full run is authorised.

## Not done

- Run 2, and therefore the two-run stability check. **No gate criterion is
  settled on one run** — the whole point of two runs is that a single pass
  cannot distinguish an attractor from variance.
- The full corpus run. Not authorised, and should not be until run 2 scores.
- Branch `curator-batch-v0023` is committed but **not pushed**, and no PR is
  open.
