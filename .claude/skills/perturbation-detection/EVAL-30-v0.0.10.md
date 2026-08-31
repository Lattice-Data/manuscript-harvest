# v0.0.10 evaluation on 30 unseen papers

Prompt v0.0.10 / `schema_version` 0.0.6. Run 2026-08-28.
First unbiased measurement of `suppressed_candidates`: the six papers it had been
exercised on were hand-picked to hit every rule, so every prior number was a
demonstration, not a rate.

## Set

`papers-30.txt`, drawn with `random.seed(30)` from the 342 corpus papers that have
`extracted/blocks.jsonl` and are not in `papers-50.txt`. Zero overlap with the
50-paper set or the 6-paper regression subset. All 30 carry a v0.0.8
`perturbations.json` baseline (`prompt_version: "0.0.8"` verified on each), so the
diff base is uniform.

Stage 2: 30/30 OK, no FAILs, no retries. 176 quotes, 176 verified.

## Determinations

| | yes | unclear | no |
|---|---|---|---|
| v0.0.8 baseline | 11 | 0 | 19 |
| v0.0.10 | 9 | 0 | 21 |

28/30 unchanged. Both changes are `yes` -> `no`. **No UNEXPLAINED.**

## What the NOT list cost, corpus-scale

38 suppressed candidates over 30 papers (mean 1.27, max 3). 25/30 papers have at
least one; 5 have none.

| rule | papers | entries | of which `would_have_paired: yes` |
|---|---|---|---|
| observational_disease_state | 17 | 19 | 17 |
| incidental_clinical_therapy | 5 | 5 | 2 |
| derivation_formulation | 4 | 4 | 4 |
| sample_handling_protocol | 4 | 4 | 4 |
| reporter_or_marker | 3 | 3 | 1 |
| readout_reagent | 2 | 2 | 1 |
| unintended_condition | 1 | 1 | 1 |

`would_have_paired: yes` on 30/38 entries; 17 papers where the rule held a
would-have-paired candidate back from `yes`; **5 of those under a rule still in
review (triage P2 = 5/30, 17%)** — against 3/6 on the set built to exercise every
rule.

## Findings

**Over-firing: not observed.** None of the items the attractor produced on its
first run appear as a candidate anywhere: no ROCK inhibitor, no preservation
solution or 4 °C hold, no freezing DMSO, no bare dissociation enzyme, no
pen/strep, no library kit, nothing in-silico. The two entries naming a
dissociation enzyme or an isolation kit are both papers that *benchmark* those
protocols as their design, which is deliberation, not enumeration. Guard 3 held.

**Empty-field rate 5/30 is legitimate.** Four are atlases with nothing to
deliberate (normal fetal cerebellum, healthy macaque brain, two with no
perturbation language at all); the fifth is a `yes` with all three perturbations
reported. Guard 2 held.

**`would_have_paired` still discriminates, but the all-yes guard over-fires.**
5 of the 12 papers with >=2 entries trip it. The concentration of `yes` is
structural, not degradation: `observational_disease_state` (17/19 yes) and
`derivation_formulation` (4/4 yes) attach by construction to the sequenced
material, so `yes` is the correct answer. Where discrimination is possible the
column delivers it — `reporter_or_marker` 1/3 yes, `incidental_clinical_therapy`
2/5 yes.

**Two suppressions were flagged as wrong during review. The curator ruled on both,
and both are correct.** They are the only two determination changes in the run, so
v0.0.10 is 30/30 on determinations against curator judgment — and both moves off
v0.0.8 are v0.0.10 *correcting* v0.0.8, not breaking it.

1. `10.1038/s41467-025-65049-8` — `incidental_clinical_therapy`, v0.0.8 `yes` ->
   v0.0.10 `no`. 8 children have paired day 0 and day 28 scRNA-seq samples
   spanning induction chemotherapy, which satisfies v0.0.9 rule 1's per-sample-tie
   test, and that is why review flagged it.

   **CURATOR RULING, 2026-08-28: `no` is correct.** Chemotherapy for T-ALL is not
   a perturbation here. "In reality all the kids have been treated, even though as
   reference point they indeed use day 0... the main point of the paper is to
   identify mechanism that has not been engaged, despite the treatment — the focus
   of the study was not to learn how chemo or other treatment makes the cells
   behave, but to identify the outlier." The paper supports this: the stated axis
   is responder vs non-responder across "58 children (84 samples) who did, or did
   not respond to initial treatment"; the payload is a **day-0** biomarker
   ("at diagnosis, ZBTB16 expression may delineate a blast population that resists
   induction treatment"), motivated by refractoriness not being predictable at
   diagnosis; and P058's ZBTB16+ population goes 0.67% at day 0 to 97.6% at day
   28, which the authors read as selection of a pre-existing clone rather than
   chemo changing cells. Day-28 samples are 8 of 84 and are confirmatory.

   **The model's recorded reason was sound** — "the paper's own analysis axis is
   response status (responsive vs induction failure) rather than a named applied
   therapy" is the curator's own argument, reached independently. Review rejected
   it by applying rule 1's literal per-sample-tie test; the model was reasoning to
   the curator's actual line instead, and was right to.

2. `10.1016/j.stem.2022.11.013` — `derivation_formulation`, v0.0.8 `yes` ->
   v0.0.10 `no`. The suppression asserts "both the LinPOS and alveolar-
   differentiated organoids that went into scRNA-seq received the same standard
   differentiation cocktail with no withheld/varied-factor comparator arm within
   the sequenced set." The paper says the opposite: LinPOS organoids are
   maintained in self-renewing medium, alveolar organoids are LinPOS after 7 days
   in AT2 differentiation medium, and the UMAP is "single-cell RNA sequencing
   profile from LinPOS and alveolar organoids". Relative to the self-renewing
   medium the comparator arm stayed in, the AT2 medium adds dexamethasone, cAMP,
   IBMX and DAPT, withdraws EGF, Noggin, FGF10 and FGF7, and raises CHIR99021
   from 3 µM. So v0.0.9 rule 4's own precondition ("given uniformly to every
   sample") does not hold.

   **CURATOR RULING, 2026-08-28: `no` is correct.** Differentiating an organoid to
   a new target cell type is not a perturbation — "different target cell type, not
   perturbation. I don't really care what has been applied to get to the final
   diff cell type." The determination stands; what does not stand is the recorded
   reason, which asserts both sequenced arms "received the same standard
   differentiation cocktail with no withheld/varied-factor comparator arm within
   the sequenced set." That is contradicted by the Methods, and a rule that fires
   on a misreading will fire the same way where the misreading points the other
   direction.

   The ruling is also broader than rule 4 as written: rule 4 conditions the
   exclusion on uniformity, and the ruling drops that condition. The
   disambiguating question becomes v0.0.7-shaped — is the manipulation defining
   what the cells become, or changing what already-defined cells do? Rewording is
   NOT done here: dropping a precondition only widens an exclusion, so it can only
   move papers toward `no`, the lossy direction for a recall-biased task. Blast
   radius measured on this set is zero (the other three `derivation_formulation`
   papers were uniform anyway); beyond these 30 it is unmeasured.

The other three P2 papers are correct suppressions, and all three were already
`no` under v0.0.8 — there the field only made a silent call visible, which is
what it is for. Every settled-toggle suppression (12 papers) reads correctly.

**Net after both rulings: 30/30 determinations correct, 38/38 suppressions
correct.** No over-suppression survives. 37 of 38 rationales are sound; the one
exception is the AT2 medium claim in case 2, which reaches the right answer on a
false premise.

**The finding that outlived the two disputes is about the rule text, not the
model.** In both cases the written operational test pointed the opposite way from
the curator's actual line — rule 1 asks for a per-sample tie, and day 0/day 28
supplies one; rule 4 asks for uniformity, and the AT2 cocktail was not uniform.
The model landed on the curator's answer both times anyway, once by reasoning
past the rule text (case 1) and once by misstating the paper to satisfy it
(case 2). Both rulings reduce to a single question that neither rule asks:

> Is the applied thing the study's **variable**, or its **setting**?

Differentiation is setting because it defines what the cells are. Induction
chemotherapy here is setting because it is the backdrop against which an
intrinsic property is measured. That question is v0.0.7-shaped (cf. "what is the
temperature FOR") and would replace both preconditions. It is also a softer test
than either — intent is less reproducible than structure, and arbitration
instability is this pipeline's documented failure mode — so it is proposed, not
written.

## Tooling notes

- `pe.compare` reports SUPPRESSED=1 where a human reads 2. On
  `j.stem.2022.11.013` the still-reported test matches the baseline's "AT2
  differentiation medium" against the new run's "Withdrawal of DAPT, DCI, or
  SB431542 from the AT2 differentiation medium", so the paper falls to
  PERT-SET-CHANGED. Defensible — that sibling perturbation is the v0.0.6
  carve-out firing correctly — but the class undercounts.
- `10.1101/2025.09.26.678707` emitted `samples[0].perturbed` as the string
  `"false"` rather than boolean `false`.
- `10.1016/j.celrep.2017.10.030` reasons about its tumour-core-vs-peritumour
  contrast in `ambiguities` instead of filing it as `observational_disease_state`,
  where 17 other papers filed the equivalent. Determination unaffected; the rule's
  cost is undercounted by one.

## No prompt change proposed here

Both disputed calls are now settled as correct, so nothing in the output needs
fixing and nothing in `prompt.md` changes here. What the two rulings expose is a
rule-text/curator-line mismatch in `incidental_clinical_therapy` (rule 1) and
`derivation_formulation` (rule 4), documented above as the variable-vs-setting
question.

Bringing the text into line is a documentation-alignment change, not a bug fix:
determinations are already right on all 30. It is worth doing so a future reader
or run is not relying on the model to out-reason the prompt, but it should not be
written blind. Both edits widen an exclusion, which can only move papers toward
`no` — the lossy direction for a recall-biased task — and the replacement test is
softer than the ones it replaces. Sequence: draft, then a determination-only
acceptance run against the preserved v0.0.10 baseline, twice, on a set that
includes these two papers plus papers that must NOT move (`s41467-026-69587-7`,
MC903/dupilumab; `celrep.2019.03.099`, App knock-in).
