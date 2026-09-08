# v0.0.19 confirming run — FAILED

Run 2026-09-07, twice, on the same 10 papers as v0.0.18. Under test: ruling 14's
demotion of the "nothing was applied" check from a test to a signal that the
governing question overrides.

20 of 20 paper-runs clean, 114/114 and 107/107 quotes verified.

## Result: FAIL. Ruling 14 is satisfied and three other rulings lost their stability

| | paper | want | r1 | r2 | v0.0.18 | |
|---|---|---|---|---|---|---|
| **ruling 14** | `s41467-021-21783-3` — `Brca1/p53` | yes | yes | yes | *unstable* | **FIXED** |
| ruling 7 | `j.ccell.2025.12.003` | no | **yes** | no | no | **DESTABILISED** |
| ruling 13 | `j.isci.2022.104097` | no | **yes** | no | no | **DESTABILISED** |
| ruling 5 | `science.aay3224` | no | no | **yes** | no | **DESTABILISED** |
| ruling 12 | `fimmu.2023.1211505` | no | no | no | no | held |
| ruling 8 | `j.cell.2021.11.031` | no | no | no | no | held |
| ruling 9 | `j.cell.2021.12.018` — diet | yes | yes | yes | yes | held |
| ruling 10 | `s41467-022-33184-1` — contusion | yes | yes | yes | yes | held |
| — | `s41586-024-07069-w` — C-section | yes | yes | yes | yes | held |
| — | `2023.10.25.23297558` — `Trem2` | yes | yes | yes | yes | held |

**Net: v0.0.18 had one unstable paper; v0.0.19 has three.** The change fixed the
paper it was written for and traded one instability for three. It must not ship.

## Why: attribution alone is too easy to satisfy

Removing the mechanical check left the governing question carrying the whole
load, and the model finds attribution nearly everywhere — because most papers do
say *something* causal about the material they made. Two of the three flips are
literal attributions that the curator has already ruled are models:

> **ruling 7's paper, r1:** *"The engraftment is reported as a perturbation rather
> than as pure disease-model establishment because the paper attributes a change in
> undiseased distant tissue TO it — neutrophils and progenitors in the blood and
> bone marrow of tumor-bearing versus non-tumor-bearing mice."*

> **ruling 13's paper, r1:** *"The paper states in its own voice that the Umod
> mutation CAUSES the medullary fibroblast/macrophage expansion and that misfolded
> mutant UMOD triggers the 77-gene UPR signature it reports."*

Both are true readings of those papers. Both are `no` by curator ruling. So the
curator's test is **not** "does the paper attribute anything to it" — it is
narrower, and the gap between the two is where v0.0.19 fell in.

The distinction the rulings imply, stated as precisely as the evidence allows:
`Brca1/p53` is the **paper's central claim** (*"perturbing Brca1/p53 in luminal
progenitors induces aberrant alveolar differentiation"* is its headline finding),
whereas the AKPS engraftment and the `UMOD` mutation are the **source of the
material** in papers whose subjects are cancer-associated neutrophil production
and a spatial method for finding disease-specific neighbourhoods. **A causal
statement about your model is not the same as the model being your subject.**
That sharpening is not in the prompt, and writing it from two data points is
guessing at the curator's line rather than recording it.

**The third flip is a different failure and not about attribution at all.**
`science.aay3224` r2 reports the `Rag1` knockout with no attribution argument
whatsoever: *"Rag1 knockout is a functional genetic loss-of-function edit rather
than a reporter… the legend places Rag1KO cells inside the mouse thymic
single-cell atlas UMAP."* The evidence is one mention in a supplementary figure
legend. That paper's criteria-`no` under v0.0.16-18 rested entirely on the
mechanical check; with the check demoted, nothing holds it. It is a human thymus
atlas and the `Rag1KO` mouse is a comparator, so under a correctly applied
governing question it is still `no` — the model simply did not ask the question.

## What this says about the two changes, taken together

v0.0.18 and v0.0.19 are the second and third wording errors on this one rule in
one session, both by the same author, and they fail in opposite directions:

- **v0.0.17** wrote the physiological clause as a blanket exclusion. Too broad on
  the model side; a perturbation got suppressed.
- **v0.0.19** demoted the mechanical check with nothing to replace its stabilising
  work. Too broad on the perturbation side; three models got promoted.

The rule is under-specified in the middle, and each edit has pushed it off one
side. The mechanical check was doing real work regardless of its theoretical
status: in rulings 5, 7 and 13 it was the only thing keeping those papers stable.

## Recommended next step, and it is deliberately the conservative one

Restore v0.0.18's structure — the mechanical check as a **test** — and add a
single narrow exception for ruling 14's shape: *the check does not apply when what
the lesion does is the paper's central finding.* That satisfies ruling 14 and
nothing more, and it leaves the three destabilised papers on the footing that kept
them stable.

The general version — replacing the check outright with a properly sharpened
attribution test that distinguishes a causal remark from a subject — is better
design and needs the curator's words for the distinction, not the author's.

## Protocol note

Sixth note, and the first about the author rather than the test. **Two consecutive
failed wording changes on one rule is a signal to stop editing and ask.** Both
edits were locally reasonable, both were verified by the acceptance protocol
working exactly as intended, and both were wrong. The protocol caught them, which
is the system succeeding; continuing to iterate unilaterally after the second one
would be the system failing.
