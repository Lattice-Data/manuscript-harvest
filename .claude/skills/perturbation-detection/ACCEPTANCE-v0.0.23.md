# v0.0.23 acceptance — protocol and PREDICTIONS

**Status: not yet run.** Written before the run, on purpose. Predicted and
measured are different claims, and every version that conflated them moved
determinations nobody intended.

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
| `science.aat1699` | 6 | no | same |
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

Stage B's 14 degraded-text `unclear`s are untouched by every change here, and
`papers-accept-v0023.txt` deliberately contains none of them — this set cannot
test a Stage B change and must not be read as if it could.

## Cost

Acceptance, 30 papers x 2 runs: ~$75 at the $1.30/paper the v0.0.23 envelope
reporting measures. Full corpus, 392 papers x 1 run: ~$500-600. The acceptance
pass is ~12% of a full run, and it is what stops the full one being paid for
twice.
