# v0.0.15 acceptance test

Run 2026-09-03 on the 46 papers of `papers-accept-v0015.txt` (twice) and the 6 of
`papers-accept-v0015b.txt` (once and a half — see **Incomplete** below). Under
test: the disease-model rule of curator rulings 7 and 8, and
`disease_model_establishment` as the ninth `rule` value.

The set is not the usual sample. Tier 7 is `perturbation_present = "yes"` carried
entirely by a non-human model, and the two-axis map added with ruling 8 argued
that part of that tier was a **criteria gap wearing a scope costume**. These
papers are where v0.0.15 should bite, so re-scoring them tests the rule and the
map at the same time.

92 of 92 paper-runs on the 46 ran clean: 0 FAIL, 0 unparseable, both runs
validated 46/46 at 561/561 and 591/591 quotes verified, 0 failed, 0
misattributed, 0 perturbations dropped.

## Result: PASS, with one hypothesis refuted and two questions opened

## Determinations on the 46

| | moved off `yes` |
|---|---|
| r1 | 12 / 46 |
| r2 | 11 / 46 |
| **confirmed in BOTH runs** | **10** |
| r1 vs r2 agreement | **43 / 46** |

Every one of the 10 is suppressed under `disease_model_establishment` and every
one lands in triage **P2** — the ratification queue — rather than flipping
silently. `pe.compare` over the 5 papers that have a preserved two-run v0.0.12
baseline reports 2 changed against a noise floor of 0/5, both classified
`SUPPRESSED`. **Zero `UNEXPLAINED`.**

**The noise floor did not move.** 3 of 46 papers disagree with themselves across
two v0.0.15 runs; the v0.0.12 floor was 3 of 50. A criteria change that widens
exclusions could have made arbitration less reproducible, and it did not.

### The 10 confirmed movements

```
10.1016_j.ccell.2025.12.003     10.1038_s41467-026-69587-7
10.1016_j.cell.2021.11.031      10.1038_s41586-022-05060-x
10.1016_j.isci.2022.104097      10.1038_s41586-024-07376-2
10.1038_s41467-024-52052-8      10.1038_s42003-024-07315-x
10.1093_brain_awaf129           10.3389_fimmu.2023.1211505
```

All three curator-ruling papers in the set are among them — `j.ccell.2025.12.003`
(ruling 7) and `j.cell.2021.11.031` (ruling 8), which generated the rule, and
`s41586-022-05060-x` (ruling 4), which did not.

## The hypothesis, half confirmed and half refuted

The map claimed rulings 4 and 5 might both be Axis 1 (criteria) rather than Axis
2 (scope), because LAD ligation produces an infarct and a Rag1 knockout produces
an immunodeficient animal.

**Ruling 4: confirmed.** `s41586-022-05060-x` comes out `no` under the criteria
rule, in both runs, suppressed as `disease_model_establishment` — *"the ligation
is the surgical induction protocol that creates the myocardial infarction the
study characterises... the sequenced contrast is infarcted versus sham tissue"*.
It had been ruled `no` on scope; it is now `no` on criteria, without anyone
touching the scope question.

**Ruling 5: refuted.** `science.aay3224` is **still `yes`** under v0.0.15. The
Rag1 knockout was not suppressed. That is correct on the rule's own terms: the
paper is a human thymus atlas and the Rag1KO mouse is a comparison, so the
knockout is *"a knockout compared against wild type to ask what the gene does"* —
the clause the rewritten genetic bullet deliberately preserves — rather than a
manipulation producing a disease state the paper characterises. **Ruling 5 stays
a scope ruling, and the prediction that it would not was wrong.** (Single run;
see **Incomplete**.)

**What that means for tier 7.** The criteria gap accounted for part of the tier,
not all of it. Of the 52 papers whose `yes` rested entirely on a non-human
pairing, **11 moved** (10 confirmed + 1 single-run), leaving roughly 40 for which
the scope question is still live and still unanswered. So the tier-7 Group A
question was worth deferring — and it is now worth asking, on a smaller and
cleaner set.

## The controls, and where my predictions were wrong

Written before the run, so they could fail, and two of three did.

**PASS — the three ruling papers moved.** Stated above.

**FAILED, and the fault was the control, not the rule.** I predicted no movement
among `physical_environmental` and `dietary` papers, reasoning that those are
things applied to animals that already exist. Three phys-env papers moved, and
reading them shows why: the category also holds **surgical injury induction** —
T9 spinal cord contusion, LAD ligation, zebrafish cardiac cryoinjury. Those are
disease-model establishment and the rule is right to reach them. The control was
chosen on a category name rather than on what the category contains.

**FAILED, genuinely.** I predicted movement would concentrate in genetic
perturbations. It did not: **4 of 21** papers with a genetic pairing moved,
against **8 of 25** without one. The rule reaches *induction protocols* across
every category — surgical, chemical, dietary, environmental — and the prediction
was over-fitted to the two papers that generated it.

**PASS, unpredicted and the strongest single result.**
`disease_model_establishment` fired on **22** of 46 papers while only 12 moved.
The other 10 kept `yes` because a genuine perturbation on the *established* model
still carried it:

| model step suppressed | what still carries the `yes` |
|---|---|
| SARS-CoV-2 K18-hACE2 infection model | whisker follicle cauterization |
| AppNL-G-F amyloidosis model | `Apoe` deletion |
| TAC pressure overload | cardiomyocyte-specific `Trp53` KO |
| `Brca1/p53` conditional model | timed mating to induce pregnancy |
| high-fat-diet `ApoE`-/- | SMC-specific `Tcf21` KO |

That is the rule's "what survives" clause doing exactly its job: taking the step
that made the diseased sample and leaving the experiment done on it. A rule that
had swallowed these too would have been the attractor this project has measured
three times.

## The 3 unstable papers are the two open questions

The instability is not noise scattered at random. Each of the three flips on one
specific question the rule does not yet answer, and the model lands on both sides
across two runs of byte-identical input. That is what an unsettled boundary looks
like from the inside, and it is more useful than either answer would have been.

**Question 1 — is an induction protocol still "the model" when it is a diet or a
surgery?**

| paper | r1 | r2 |
|---|---|---|
| `j.cell.2021.12.018` — western diet, 24-36 weeks, to induce NAFLD/NASH | model -> `no` | perturbation -> `yes` |
| `s41467-022-33184-1` — T9 contusion, spinal cord injury atlas | model -> `no` | perturbation -> `yes` |

Both fit the rule's letter — the diet's stated purpose is to induce the NASH
state the paper then characterises; the contusion creates the injury the atlas
describes — and both are also textbook applied variables with a control arm. The
5 other dietary-only papers did NOT move, so this is not the rule sweeping a
category.

**Question 2 — is inducing pregnancy a perturbation?** `s41467-021-21783-3`. The
`Brca1/p53` model is suppressed as a disease model in **both** runs, so that call
is stable; what wobbles is what remains. r1 says *"timed mating with studs to
induce pregnancy (gestation)"* carries the `yes`; r2 says nothing does. Under
*what is the applied thing FOR*, timed mating obtains pregnant animals — it
establishes a state rather than perturbing one, which is the same shape as
rulings 1, 7 and 8, applied to a physiological state instead of a disease.

Neither question is settled here, and neither should be settled by the person who
wrote the rule.

## Incomplete: 3 of the 6 stragglers have no second run

Tier 7 is not the same population as "papers with an animal-carried `yes`",
because the triage ladder stops at the first matching tier and tier 3 (`yes` with
confidence < 0.6) is checked first. Six such papers sit in tier 3, including
ruling 5's own. They were run as a supplementary set.

**Run 1 completed 6/6. Run 2 completed 3/6 and stopped on a session limit**
(`You've hit your session limit`), not on anything about the papers. Missing from
r2: `s41467-025-59997-4`, `2023.10.25.23297558`, `science.aay3224`.

| paper | v0.0.12 | r1 | r2 |
|---|---|---|---|
| `s41467-025-59997-4` | yes | **no** (DME) | — |
| `s41586-024-07069-w` | yes | yes | yes |
| `s41586-024-07476-z` | yes | yes | yes |
| `2023.10.25.23297558` | yes | yes (DME recorded) | — |
| `2024.10.27.620502` | yes | yes | yes |
| `science.aay3224` | yes | **yes** | — |

So the refutation of ruling 5's half of the hypothesis rests on **one run**. It is
the least-supported claim in this document. Completing r2 needs nothing but the
session limit to reset; `pe.pending` names the three and re-running picks up only
those.

## What this licenses, and what it does not

**Licensed.** The disease-model rule changes the determinations it was written to
change, reproducibly across two runs, with the model stating the rule's own test
as its reason, and routes every one of them to P2 for ratification rather than
flipping silently. It leaves a genuine perturbation on an established model
alone — demonstrated on 10 papers, not argued. It did not degrade
reproducibility. The ninth `rule` value is populated and countable.

**Not licensed.**

- **Ruling 5's refutation is single-run.** Three of the six supplementary papers
  have no second run.
- **52 of 392 papers.** Everything here is measured on the population where the
  rule was expected to bite. Whether it moves anything in the other 340 — where a
  human pairing carries the call — is untested, and the 22-of-46 firing rate for
  `disease_model_establishment` on this set says nothing about the corpus rate.
- **Questions 1 and 2 above are open**, and two papers of the 46 are unstable
  because of them. Until they are ruled, those two papers' determinations are not
  reproducible and should not be relied on.
- The tier-7 **scope** question is still unanswered for roughly 40 papers. It is
  now a better-posed question than it was, not a settled one.

## The protocol note worth keeping

`ACCEPTANCE-v0.0.14.md` added *check that the acceptance set contains papers the
change can act on*. This run adds a second: **choose controls by what a category
CONTAINS, not by what its name suggests.** Predicting that no
`physical_environmental` paper would move looked like a control and was not one —
the category holds surgical injury induction, which the rule targets. A control
that fails for the wrong reason costs a re-read of every paper in it before the
result can be interpreted.
