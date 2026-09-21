# v0.0.17 acceptance test

Run 2026-09-06, twice, on the 92 papers of `papers-accept-v0017-all.txt` — the 52
from the v0.0.16 set plus the 40 exposed by re-keying the derivation rule. Under
test: one governing question replacing three differently-keyed rules, its three
sharpenings, and the curator's clarification that a differentiation cocktail is a
perturbation when the research is about the cocktail.

184 of 184 paper-runs completed: both runs validated 92/92 at 1069/1069 and
1055/1055 quotes verified, 0 failed, 0 misattributed, 0 perturbations dropped.
Run 2 stopped at 20/92 on a session limit and was resumed; `harness.pending` named the
72 and the re-run took only those.

## Result: two fixes landed, one regression introduced, and the regression is mine

**Reproducibility 90/92**, against 51/52 at v0.0.16 — the same ~2% rate.

## What the change was written to do, and did

**The derivation re-key cost ZERO determinations.** Of the 40 papers exposed —
every paper whose `yes` was not already established and which carried a
`derivation_formulation` suppression — **none flipped to `yes` in both runs**, and
39 of 40 still carry that suppression. The 40th dropped it and stayed `no`.

This is the outcome the curator's clarification predicted: in real organoid and
cell-derivation papers the formulation genuinely is a means to an end, so keying
the rule on *what the research is about* rather than on *identity versus factor*
changes the wording without changing the answers. A measured exposure of 40
realised as 0.

**`Trem2−/−` on `5XFAD` is now stable `yes` in both runs.** It was the one paper
still unstable under v0.0.16, and sharpening 3 — a manipulation applied on top of
an already-established model, whose effect the paper reports, is a perturbation —
exists for it. The germline shortcut no longer swallows it.

**Ruling 11 is satisfied.** `timed mating to induce pregnancy` is suppressed
under `disease_model_establishment` with the physiological-state note, in both
runs, exactly as the curator chose.

**49 of the 52 re-scored papers came back identical to v0.0.16** across all four
runs, which is what licenses reading v0.0.16's six reversals and five retentions
as still true of this pack rather than of the pack they were measured on.

## REGRESSION: the physiological-state clause shipped as a blanket exclusion

`10.1038/s41586-024-07069-w` was a stable `yes` under v0.0.16 and is **unstable**
under v0.0.17 — r1 `no`, r2 `yes`.

The paper delivers pups by caesarean section against vaginally-born littermates,
holds them for defined intervals of extrauterine life, and reports the
transcriptional shift across the first hour. r2 reads that correctly: applied,
with a comparator, and the paper reports its consequences. r1 suppresses it under
`disease_model_establishment`.

**The fault is the wording, and it is mine.** The clause added for pregnancy read
*"a normal physiological state the investigators set up is handled the same way"*
— which one run of two took as licence to exclude any physiological manipulation.
Pregnancy in the `Brca1` paper is the model because that paper attributes nothing
to gestation; C-section here is a perturbation because the paper attributes its
whole finding to it. The clause stated the excluded case and no other, so it read
as a category-level exclusion.

**This is the same defect this prompt removed at v0.0.14** — a category named as
an exclusion with the rules under it needing the opposite — reintroduced two
versions later by the person who removed it. It is now qualified by the governing
question and carries the C-section case as its perturbation-side example, with
`tests/test_spec_self_consistency.py` asserting both. That fix is **v0.0.18 and
is not measured here.**

## One movement that is not this version's doing, and needs ratification

`10.1101/2024.10.27.620502` went `no` -> `yes`, stable across both runs, and the
rule that moved it predates v0.0.17 by eight versions.

Its `Matn4-mEGFP` construct is used purely to label and sort OPCs, which is why
v0.0.16 filed it under `reporter_or_marker`. Both v0.0.17 runs instead invoke that
rule's own carve-out — *"Still perturbations: a reporter knock-in that disrupts or
replaces the endogenous locus"* — because the cassette goes into the **first
coding exon** of `Matn4` with a polyA and the authors themselves call it the
mutant allele.

By the written rule v0.0.17 is right and v0.0.16 was wrong. But the paper's
*purpose* for the construct is labelling, and this paper has now been `yes` at
v0.0.12, `no` at v0.0.16 and `yes` at v0.0.17, so it is plainly on a knife edge:
**is a labelling knock-in that incidentally disrupts its locus a perturbation?**
The rule says yes. Whether the curator agrees is unruled, and this is the paper to
ask it on.

## Open, and not this version's business

`10.1038/s41467-021-21783-3` is a stable `yes` on `Brca1/p53`, with pregnancy
correctly suppressed. The curator ruled on pregnancy and not on the paper; an
earlier version of ruling 11's record overstated that, and the record is
corrected. The paper-level call is open: rulings 7 and 8 concern papers that used
induced alleles to OBTAIN tissue whose programs were then characterised, whereas
this paper's stated finding is what the lesion DOES — *"perturbing Brca1/p53 in
luminal progenitors induces aberrant alveolar differentiation pre-malignancy"*.
The governing question separates those and the curator decides which side.

## What this licenses, and what it does not

**Licensed.** One governing question replacing three keys costs nothing on the 40
papers most exposed to it, fixes the paper v0.0.16 left unstable, satisfies ruling
11, and leaves 49 of 52 earlier results untouched. Reproducibility is unchanged.

**Not licensed.**

- **v0.0.17 must not be treated as accepted.** It destabilised a paper that
  v0.0.16 had right. The fix is v0.0.18 and is unmeasured.
- Two papers need curator calls: the `Matn4` knock-in, and the paper-level call on
  `s41467-021-21783-3`.
- **92 of 392 papers.** Both sub-populations were chosen because the change was
  expected to act on them; nothing here measures the other 300.

## Protocol note

Three previous notes said: check the set can act on the change; choose controls by
what a category contains; write a known gap down as a predicted failure. This run
adds a fourth, and it is the one that would have caught this regression:
**a clause that names only the excluded case will be read as excluding the
category.** Every boundary rule in this prompt that has misfired — the v0.0.5 NOT
list, the v0.0.15 state-contrast tell, and now the physiological-state clause —
stated one side. The rules that hold state both, with an example each way.
