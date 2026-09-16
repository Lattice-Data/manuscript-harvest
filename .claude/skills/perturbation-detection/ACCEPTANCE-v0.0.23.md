# v0.0.23 acceptance — PROTOCOL, PREDICTIONS, and RESULTS

**Status: run 2026-09-15, twice, 30/30 both times.** The predictions below were
written before the run and have not been edited since; the results are appended
at the bottom. Predicted and measured are different claims.

**Verdict: criteria 1-3 PASS. Criterion 4 fails on one paper, entirely inside
Stage B.** Stage A — the layer these changes touch — agreed on **30 of 30**
papers across both runs. Score it yourself with `python score-acceptance-v0023.py`,
which exits non-zero when any criterion fails.

Set: `papers-accept-v0023.txt`, 30 papers in four labelled groups. **Two runs**,
because one run cannot separate an attractor from run-to-run variance — the
v0.0.10 `suppressed_candidates` episode moved 2 of 6 determinations with no
criterion edited, and the suppression tally alone is ~21% unstable between
byte-identical runs.

Baseline for every comparison: `work-corpus-v0022-revalidate` (2026-09-09).

## Gate to proceed to the full corpus

1. Every anchor in group 2 holds its ruling, in **both** runs.
2. Every predicted mover moves, in both runs.
3. Nothing outside the predicted set moves.
4. No paper disagrees with itself across the two runs.

Failing 1 is a blocker. Failing 2 is a re-read of the rule. Failing 3 is the
attractor, and is what the two-run protocol exists to detect.

## Group 1 — the 12 curator papers (rulings 15-26)

| paper | v0.0.22 | expected | why it moves, or does not |
|---|---|---|---|
| `j.immuni.2020.03.019` (r15) | unclear | **no** | C4: the readout is named (flow cytometry) |
| `s41467-021-25125-1` (r16) | yes | **yes** | ruling NOT adopted; reused data still counts. Also must NOT trip the article-type gate |
| `j.molmet.2023.101746` (r17) | no | no | confirms |
| `j.cell.2021.11.031` (r18) | no | no | re-confirms ruling 8 |
| `j.coi.2022.102188` (r19) | unclear | **not_applicable** | C3: the only review in the corpus |
| `j.cell.2021.07.023` (r20) | yes | **yes** | must stay yes, but now carried by the INFECTION, not corticosteroids |
| `j.healun.2026.02.1666` (r21) | no | no | curator leaned `yes?`; not adopted |
| `j.ccell.2025.12.003` (r22) | no | no | re-confirms ruling 7 |
| `s41586-021-03852-1` (r23) | unclear | **no** | C4. The cxg-collection half of the ruling is out of scope |
| `s41467-024-55440-2` (r24) | yes | **no** | C2: prominence |
| `bloodadvances.2023011445` (r25) | no | no | confirms |
| `j.immuni.2022.09.002` (r26) | no | no | confirms |

**`j.cell.2021.07.023` is the one to read rather than just score.** It was `yes`
for the wrong reason. A `yes` whose paired perturbation is still corticosteroids
is a FAIL even though the determination is right — that is ruling 1's shape, and
scoring the label alone cannot see it.

## Group 2 — anchors that must NOT move

| paper | ruling | expected | what would break it |
|---|---|---|---|
| `s41467-025-65049-8` | 2 | no | C2 over-reaching into applied clinical therapy |
| `science.aat1699` | 6 | **unclear**, not `no` | the RULING is `no`, but this paper is `methods_missing` and Stage B caps its negative — baseline `unclear` / `degraded_text`. Reading `unclear` here as a failure would be reading the cap as a regression. What must not happen is `yes`: Stage B never caps upward, so a `yes` would mean C2 failed to suppress the therapy |
| `j.cell.2021.12.018` | 9 | yes | C1 losing the investigator-applied half — a fed diet must stay a perturbation |
| `s41467-022-33184-1` | 10 | yes | same, for a contusion |
| `fimmu.2023.1211505` | 12 | no | C1 widening the observational rule's promotion beyond infection |
| `j.isci.2022.104097` | 13 | no | same |
| `s41467-021-21783-3` | 14 | yes | C2's prominence clause suppressing an attributed lesion |
| `elife.104978.2` | — | yes | holds the other side of ruling 2's line: chemotherapy the paper attributes its finding TO |

## Group 3 — predicted infection movers, `no` → `yes`

Six of the eight papers where the paper's own subject is the infection response.
Sized by title; the rule is the paper's stated question, so a miss here is a
judgment to inspect rather than an automatic failure.

`j.cell.2021.01.053`, `j.cell.2022.01.012`, `sciimmunol.abd1554`,
`s13073-021-00933-8`, `j.immuni.2021.03.005`, `j.cell.2021.02.018`

## Group 4 — predicted infection NON-movers, must stay `no`

The other side of the same rule, and the more important side: these are why C1
was written as a subject test rather than "infection counts however acquired",
which would have moved all 21.

| paper | what it is |
|---|---|
| `s41591-023-02327-2` | *An integrated cell atlas of the lung in health and disease* — COVID-19 is one donor state beside IPF |
| `s41588-022-01243-4` | a spatial lung atlas characterising a gland-associated immune niche |
| `s41467-024-49037-y` | periodontitis keratinocyte interactomics; the periopathogens are donor state |
| `pnas.2023333118` | ATP1A3 in brain development; a germline variant, nothing acquired |

## Corpus-wide expectation, for the full run afterwards

From the v0.0.22 baseline of 392 papers (273 `no`, 101 `yes`, 18 `unclear`):

| change | direction | predicted |
|---|---|---|
| C1 | `no` → `yes` | ~8 of 21 suppressed-infection papers |
| C2 | `yes` → `no` | 1 measured (contraception); 8-12 share the shape, most staying `yes` |
| C3 | `unclear` → `not_applicable` | 1 |
| C4 | `unclear` → `no` | ≤4, the whole `pairing_not_stated` population |

Stage B's 14 degraded-text `unclear`s are untouched by every change here.

**One of them IS in this set, and it is the ruling-6 anchor.** `science.aat1699`
is `methods_missing`, so its Stage A `no` is capped to `unclear` and the baseline
reads `unclear` / `degraded_text`. That makes it a WEAKER anchor than the others:
the cap masks the Stage A answer, so it can only prove C2 did not push the paper
to `yes`, not that C2 left its `no` intact. Checked against the record's
`validation.stage_a` rather than its determination for that reason.

Nothing in this set exercises a Stage B change, and the set must not be read as
if it could — see the acceptance-set blind spot that `papers-50b` had for exactly
this reason.

## Cost

Acceptance, 30 papers x 2 runs: ~$75 at the $1.30/paper the v0.0.23 envelope
reporting measures. Full corpus, 392 papers x 1 run: ~$500-600. The acceptance
pass is ~12% of a full run, and it is what stops the full one being paid for
twice.


---

# RESULTS, 2026-09-15

Two runs, `work-accept-v0023-r1` and `-r2`, 30/30 each, validated, every quote
verified, neither given `--write-corpus`. Run 2 hit a session limit at 19/30 and
was resumed after the reset; the abort sentinel cost 3 wasted spawns (one per
parallel worker) and skipped 10.

| criterion | result |
|---|---|
| 1. anchors hold (BLOCKER) | **PASS** — all 8, both runs |
| 2. predicted movers moved | **PASS** — all 6, both runs |
| 3. nothing else moved (ATTRACTOR) | **PASS** — 2 unpredicted moves, both inspected and sound |
| 4. stable across both runs | **FAIL** — 1 paper, and it is Stage B |

| stability | measured |
|---|---|
| `stage_a`, the criteria answer | **30/30** — zero flips |
| text-quality self-report | 3/30 flipped, on byte-identical input |

## The headline check passed, in both runs

`10.1016/j.cell.2021.07.023` is `yes` carried by **"SARS-CoV-2 infection
(naturally acquired, PCR-confirmed)"** in r1 and r2 alike, with corticosteroids
moved to `incidental_clinical_therapy`. Under v0.0.22 the same `yes` rested on
the corticosteroids while the infection sat suppressed. A label-only comparison
would have called the two versions identical on this paper.

## The one instability is Stage B's, not the criteria's

`10.1182/bloodadvances.2023011445` — r1 `no`, r2 `unclear`.

| | r1 | r2 |
|---|---|---|
| prompt sha256 | `48699b45…` | `48699b45…` (identical) |
| `stage_a` | `no` | `no` |
| perturbation | IL-6 20 ng/mL, `paired=no` | IL-6 20 ng/mL, `paired=no` |
| `text_completeness` | `full` | **`truncated`** |
| Stage B | not capped | **capped** |

Same input, same criteria answer, different self-report of text quality. This is
the documented Stage B entry instability and nothing in v0.0.23 touches it. Two
other papers flipped their self-report without the determination moving
(`healun` reported `partial`/`full` then `ok`/`truncated` — two different routes
to the same cap; `s41586-021-03852-1` flipped `truncated`→`full` while staying
`unclear` through A5).

**It is worth its own fix, separately.** 3 of 30 papers, ~10%, is a higher flip
rate than the gate can absorb, and on this run it moved one determination.

## Two moves against prediction, both sound

Recorded as `PASS*` in the scorer and still counted against criterion 3 — a gate
whose author can relabel its failures is not a gate.

**`10.1038/s41467-024-49037-y`, `no`→`yes`, in both runs.** Nobody applied the
periopathogens, but the paper's stated question IS the host response: it coins
"keratokines" for cytokine upregulation in response to challenge, with three
results sections and abstract billing. C1's acquired-exposure subject test,
correctly applied. I had put it in group 4 off a **title regex**, and the title
does not name infection.

**`10.1038/s41586-021-03852-1` did not move, and C4 was right.** The DSS arm,
whose readout is nameable, got `paired=no` as C4 asks. The TNF/IFNγ arms did
not, because the paper counts "three organoid growth conditions" and never
identifies them — the genuine textual gap C4 reserves `unclear` for. The
curator's `no` rested on the no-UMAP / not-in-CELLxGENE reasoning that decision
D5 puts out of scope.

## Measured movement, v0.0.22 → v0.0.23

Both runs agreeing, 9 papers moved on the criteria:

| change | movement | papers |
|---|---|---|
| C1 | `no` → `yes` | 7 (6 predicted + periodontitis) |
| C2 | `yes` → `no` | 1 (contraception) |
| C3 | `unclear` → `not_applicable` | 1 (the review) |
| C4 | `unclear` → `no` | 1 (`immuni.2020.03.019`) |

## Cost, and what it means for the corpus

| | papers | requests | wall-clock | model time | cost |
|---|---|---|---|---|---|
| r1 | 30 | 406 | 81m | 79m (97%) | **$54.72** |
| r2 | 30 | 405 | 82m | 80m (97%) | **$55.52** |

**$110.24 for the acceptance pass, $1.84/paper.** No price-drift warning in
either run, so `pe/pricing.py` still agrees with the CLI's own figure.

**The 392-paper corpus is therefore ~$720, not the $500-600 estimated above.**
That estimate came from a one-paper smoke run at $1.30 and was 40% low.

## Two things the full run should expect

**The mover count is a floor.** ~8 of 21 came from title-matching, and the
periodontitis paper shows the same under-count reaches papers whose subject is an
infection response without saying so in the title. Expect more than 8.

**One determination in 30 may move for text-quality reasons alone**, independent
of these criteria. Budget for it when reading the corpus diff rather than
attributing it to v0.0.23.
