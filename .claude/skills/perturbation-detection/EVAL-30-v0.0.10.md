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

**Two suppressions are wrong, and they are the only two determination changes in
the run.**

1. `10.1038/s41467-025-65049-8` — `incidental_clinical_therapy`, v0.0.8 `yes` ->
   v0.0.10 `no`. Structural twin of `10.7554/elife.104978.2`, the case guard 5 was
   written for. 8 children have **paired day 0 and day 28 scRNA-seq samples**
   spanning induction chemotherapy; the paper compares "all refractory day 28
   blasts against all diagnostic blasts". That is an applied therapy with an
   explicit per-sample tie, both arms sequenced. The model's stated grounds — no
   named drug, and the axis is response status — do not meet v0.0.9 rule 1, which
   asks for a tie to the profiled material, not for a named agent.

2. `10.1016/j.stem.2022.11.013` — `derivation_formulation`, v0.0.8 `yes` ->
   v0.0.10 `no`. The suppression asserts "both the LinPOS and alveolar-
   differentiated organoids that went into scRNA-seq received the same standard
   differentiation cocktail with no withheld/varied-factor comparator arm within
   the sequenced set." The paper says the opposite: LinPOS organoids are
   maintained in self-renewing medium, alveolar organoids are LinPOS after 7 days
   in AT2 differentiation medium, and the UMAP is "single-cell RNA sequencing
   profile from LinPOS and alveolar organoids". The cocktail was applied to one
   sequenced arm and withheld from the other, so v0.0.9 rule 4's own precondition
   ("given uniformly to every sample") does not hold.

The other three P2 papers are correct suppressions, and all three were already
`no` under v0.0.8 — there the field only made a silent call visible, which is
what it is for. Every settled-toggle suppression (12 papers) reads correctly.

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

Both wrong suppressions sit inside rules whose criteria are the curator's call.
Reported as evidence, not acted on.
