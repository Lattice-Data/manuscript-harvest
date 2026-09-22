# v0.0.20 and v0.0.21 acceptance

Run 2026-09-07, twice each, on the 13 papers of `papers-accept-v0020.txt` — the
10 from the v0.0.18 set plus the SARS-CoV-2 infection paper and two of v0.0.16's
exposure reversals. Under test: the exposure/construction default written from the
curator's clarifications of 2026-09-06 and 2026-09-07, and then the contrast
requirement it turned out to need.

52 of 52 paper-runs clean across both versions.

## v0.0.21: PASS — 13/13, both runs, every ruling 5 through 14 reproduced

| paper | kind | expected | result |
|---|---|---|---|
| `j.cell.2021.11.031` (r8) | construction + uncontrasted DSS | no | **PASS** |
| `s41467-021-21783-3` (r14) | construction, goal names the lesion | yes | PASS |
| `j.ccell.2025.12.003` (r7) | construction | no | PASS |
| `j.isci.2022.104097` (r13) | construction | no | PASS |
| `science.aay3224` (r5) | construction | no | PASS |
| `fimmu.2023.1211505` (r12) | construction | no | PASS |
| `j.cell.2021.12.018` (r9) | exposure — diet | yes | PASS |
| `s41467-022-33184-1` (r10) | exposure — trauma | yes | PASS |
| `s41586-024-07069-w` | exposure — C-section | yes | PASS |
| `2023.10.25.23297558` | construction on a model, attributed | yes | PASS |
| `s41586-024-07376-2` | exposure — APAP | yes | PASS |
| `s41467-024-52052-8` | exposure — nerve constriction | yes | PASS |
| `j.cell.2024.02.020` | **exposure — SARS-CoV-2** | yes | PASS |

**Reproducibility 13/13.** Every paper agrees with itself across two runs, which
no version of this rule has managed before: v0.0.18 left one unstable, v0.0.19
left three, v0.0.20 left none unstable but one wrong.

**The pathogen ruling lands.** `intranasal SARS-CoV-2 infection (6×10⁴ PFU) of
K18-hACE2 mice` is reported as a perturbation in both runs, where every version
through v0.0.19 suppressed it as disease-model establishment.

## v0.0.20: FAILED on one paper, and the failure was informative

12 of 13 passed. `j.cell.2021.11.031` — ruling 8 — flipped to a stable `yes` on
**DSS**, with both runs citing the new rule correctly:

> *"DSS is a deliberately administered chemical insult at a stated dose and
> duration that the colonic epithelium reacts to, i.e. an EXPOSURE rather than a
> construction, and it is reported because exposures stay perturbations even when
> the paper is named after something else."*

That is the exposure half working as written. What it was missing is in the
curator's own ruling-8 quote: *"Control mice received PBS injections followed by
**DSS**."* **Both arms get the DSS**, so it is how every animal reached a damaged
colon and the contrast is carried entirely by the alleles. A variable requires
variation, and the exposure half had not said so.

v0.0.21 adds it: report an exposure only where some sequenced material had it and
some did not, or where dose or duration varied across sequenced arms.

**Explicitly distinguished from the precondition ruling 1 removed.** v0.0.9 asked
whether a CONSTRUCTION was given uniformly and used that to decide it was the
model; ruling 1 killed that, because a construction stays the model whether
uniform or not. This clause asks only whether an EXPOSURE has a comparison at
all. Conflating the two would re-open ruling 1, so the prompt says which is which
and a guard asserts the disclaimer stays.

## The rule that now holds, and why it took four attempts

One question, one default, in this order:

1. **Is the manipulation's effect what the paper set out to learn?** -> perturbation.
2. Otherwise the **kind** decides: **exposure** (external factor the material
   reacts to — diet, temperature, light, irradiation, drug, trauma, surgery,
   pathogen) -> perturbation, provided it has a contrast across sequenced arms;
   **construction** (engraftment, germline or induced genotype, derivation
   formulation, timed mating) -> the model.
3. The line between the kinds: **does the applied thing BECOME the material, or
   does the material REACT to it?**

**Four wording attempts, three failures, and they were one mistake made three
ways.** v0.0.17 shipped the physiological clause as a blanket exclusion — no
exposure half, so a C-section got suppressed. v0.0.19 demoted the mechanical
check with nothing behind it — no construction half, so engraftments and germline
lesions got promoted. v0.0.20 had both halves but no contrast requirement, so an
induction protocol given to every arm got promoted. Each attempt removed or
omitted one part of a rule that needs all of them.

**What separated the attempt that worked.** The three failures each rested on an
inference about what a paper is "really" studying. The fix that held rests on a
fact in the Methods that the curator had already quoted: both arms got the DSS.
Checkable beats interpretive, and where the rule must be interpretive — step 1 —
it now points at the paper's own statement of what it did not know.

## What this licenses, and what it does not

**Licensed.** The full rule reproduces all ten curator rulings this set can test,
in both runs, with no instability. The two kinds are demonstrated on both sides:
five constructions stay models, six exposures stay perturbations, one
construction is promoted by step 1, and one exposure is held back for want of a
contrast.

**Not licensed.**

- **13 papers.** This is a rule-verification set, not a corpus measurement. The
  92-paper v0.0.17 run is the largest measurement of this rule family and it
  predates two of the four changes.
- **The corpus has not been re-scored** under v0.0.21. 392 papers still carry
  v0.0.12-era determinations; the tier-7 population has moved twice since.
- Two papers still want curator calls, neither blocking: the `Matn4` labelling
  knock-in that disrupts its locus, and whether a paper attributing distant-tissue
  changes to its own tumour graft is doing so *as its subject*.

## Protocol note

Seventh note. **Two consecutive failures on one rule was the signal to stop
editing and ask — and asking is what produced the rule that works.** The
exposure/construction distinction is not something the acceptance protocol could
have found; it came from the curator in two sentences, after the protocol had
established that no single-test version could hold. The protocol's job was to
prove the ground was unstable, not to discover what to build there.
