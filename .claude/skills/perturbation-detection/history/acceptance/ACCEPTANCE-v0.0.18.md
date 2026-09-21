# v0.0.18 confirming run

Run 2026-09-06, twice, on the 10 papers of `papers-accept-v0018.txt`. Under test:
one clause — the physiological-state clause, which v0.0.17 shipped as a blanket
exclusion and which destabilised a paper it had no business touching.

Deliberately not a re-measurement. One target and nine controls, because a clause
edit that fixes one case by breaking another is the failure this session produced
twice.

20 of 20 paper-runs clean: both runs validated 10/10 at 96/96 and 104/104 quotes
verified, 0 failed, 0 misattributed, 0 perturbations dropped.

## Result: the regression is closed; one precedence gap is now visible

| | paper | want | r1 | r2 | |
|---|---|---|---|---|---|
| **target** | `s41586-024-07069-w` — C-section | yes | yes | yes | **PASS** |
| control | `s41467-021-21783-3` — pregnancy + `Brca1` | yes | **no** | yes | **UNSTABLE** |
| control | `2023.10.25.23297558` — `Trem2`/`5XFAD` | yes | yes | yes | PASS |
| control | `j.cell.2021.12.018` — diet (ruling 9) | yes | yes | yes | PASS |
| control | `s41467-022-33184-1` — contusion (ruling 10) | yes | yes | yes | PASS |
| control | `j.ccell.2025.12.003` (ruling 7) | no | no | no | PASS |
| control | `j.cell.2021.11.031` (ruling 8) | no | no | no | PASS |
| control | `fimmu.2023.1211505` (ruling 12) | no | no | no | PASS |
| control | `j.isci.2022.104097` (ruling 13) | no | no | no | PASS |
| control | `science.aay3224` (ruling 5) | no | no | no | PASS |

**The target passes.** Caesarean delivery is a stable `yes`, carried in both runs
by the C-section itself against the vaginally-delivered littermate arm. The
clause no longer reads as excluding a category.

**The control that mattered most also passes.** Qualifying the clause could have
over-corrected and made pregnancy read as a perturbation, breaking ruling 11 from
the other side. It did not: `timed mating` is suppressed under
`disease_model_establishment` in **both** runs. The clause is two-sided rather
than tilted, which is the whole point of the edit.

**Eight of nine controls pass**, covering every ruling the governing question
governs, on both sides.

## The unstable paper is a PRECEDENCE GAP, not an unruled preference

`10.1038/s41467-021-21783-3` was a stable `yes` under v0.0.17 and is unstable
here. Reading the two runs shows they are not disagreeing about the paper — they
are applying **two different parts of the same rule**, and nothing arbitrates:

> **r1**, suppressing `Brca1/p53`: *"The floxed/Cre genotype is a germline
> configuration the animals were bred into and **nothing was applied during the
> study**; it is the manipulation that produces the TNBC the paper then
> characterises."*

> **r2**, reporting it: *"A functional Cre-lox deletion of `Brca1` with `p53`
> heterozygosity is **what the paper is trying to learn about** — it attributes
> the aberrant alveolar differentiation and the immune changes TO `Brca1/p53`
> loss."*

r1 is the **cheap germline check** ("nothing applied -> model", rulings 12-13).
r2 is the **attribution test** (the governing question). Both are in the rule.
For a germline lesion that BOTH creates the disease AND is what the paper
attributes its findings to, the rule does not say which wins.

**This is the v0.0.7 shape** — one question answered two ways with no tiebreak —
and it is the defect this repo treats as cardinal, because the model arbitrates
silently rather than failing. Sharpening 3 was written to arbitrate exactly this
collision, but it is scoped to a manipulation applied *on top of an
already-established model* (`Trem2−/−` on `5XFAD`, which passes here). The
`Brca1` lesion is not on top of a model — it **is** the model-maker. So
sharpening 3 does not reach it and the collision is unarbitrated.

**It cannot be closed without a ruling, because the fix depends on the answer:**

- If the paper is **`yes`**, attribution beats the germline check whenever the
  paper attributes — and rulings 7 and 8 remain consistent, since those papers
  characterise *pre-malignant programs by cell of origin* rather than concluding
  about what the alleles do.
- If the paper is **`no`**, the germline check beats attribution when the lesion
  is what creates the disease — and the rule must say that a paper attributing its
  findings to its own disease model does not thereby promote the model.

Both are coherent. Guessing would encode a boundary the curator has not drawn,
which is what rulings 9-13 were needed to undo.

## What this licenses, and what it does not

**Licensed.** The v0.0.17 regression is closed and the clause is demonstrated
two-sided on the two papers that pull it in opposite directions. Every curator
ruling from 5 through 13 that this set can test is reproduced in both runs.

**Not licensed.**

- **One precedence gap is open** and one paper is unstable because of it. Until it
  is ruled, that paper's determination is not reproducible and must not be relied
  on.
- **10 papers.** This run measures one clause. It does not re-measure v0.0.17's 92
  or v0.0.16's 52; those stand on their own records.

## Protocol note

The four earlier notes were about designing the test. This one is about reading
it: **an unstable paper is worth more than a passing one when the two runs cite
different rules.** Here the disagreement was not noise and not a judgment call —
each run quoted a different clause of the same rule, which located a missing
tiebreak precisely. A single run, or two runs reported only as a count, would
have shown "1 of 10 unstable" and hidden the reason.
