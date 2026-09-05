# v0.0.16 acceptance test

Run 2026-09-03, twice, on the 52 papers of `papers-accept-v0016.txt` — the 46
tier-7 papers plus the 6 tier-3 stragglers, i.e. the whole population whose `yes`
rested entirely on a non-human pairing. Under test: the re-keying of the
disease-model rule from *purpose* to *attribution*, and the cheap first test that
curator rulings 12 and 13 supplied.

104 of 104 paper-runs clean: 0 FAIL, 0 unparseable, both runs validated 52/52 at
720/720 and 728/728 quotes verified, 0 failed, 1 misattributed (auto-corrected),
0 perturbations dropped.

## Result: the predictions held, and ONE CURATOR RULING IS VIOLATED

**Reproducibility improved: 51/52**, against 43/46 under v0.0.15. One paper
unstable, down from three, and the two tests exist precisely to settle the three.

The five predictions were written into the v0.0.16 changelog before the runs so
they could fail. Four held exactly. The fifth exposed a gap that was flagged when
the rule was written and not closed.

### Prediction 1 — response-studying papers return to `yes`: 6 of 6, exactly as named

| paper | what is applied |
|---|---|
| `s41467-024-52052-8` | sciatic nerve constriction (pain atlas) |
| `s41586-024-07376-2` | APAP (liver regeneration) |
| `s41467-026-69587-7` | topical MC903 vs ethanol (skin atlas) |
| `s41467-025-59997-4` | ischaemia-reperfusion, R-UUO (kidney) |
| `s42003-024-07315-x` | LAD ligation + cryoinjury (cardiac response) |
| `s41586-022-05060-x` | LAD ligation (**ruling 4**) |

### Prediction 2 — model papers stay `no`: 5 of 5

`j.ccell.2025.12.003` (ruling 7), `j.cell.2021.11.031` (ruling 8),
`j.isci.2022.104097` (ruling 13), `fimmu.2023.1211505` (ruling 12),
`brain_awaf129` (RCAS-PDGFB glioma model).

### Prediction 3 — `science.aay3224` newly moves: confirmed

`yes` in both v0.0.15 runs, `no` in both v0.0.16 runs. A `Rag1` knockout is a
germline genotype, so Test 1 reaches it. **Ruling 5 is now `no` on both axes** —
criteria and scope — where before it rested on scope alone.

### Prediction 4 — the three unstable papers stabilise: all three, but see below

`j.cell.2021.12.018` -> stable `yes` (matches **ruling 9**).
`s41467-022-33184-1` -> stable `yes` (matches **ruling 10**).
`s41467-021-21783-3` -> stable `yes`, and **this contradicts ruling 11.**

## FAILURE: ruling 11

`10.1038/s41467-021-21783-3` is a stable `yes` in both runs, on:

> `timed mating to generate pregnancy (gestation time course)` — *"Pregnancy was
> deliberately induced by the investigators… and the paper studies the
> transcriptional response to that applied condition"*

The curator ruled on 2026-09-03 that **timed mating to induce pregnancy is not a
perturbation.** The `Brca1/p53` model is correctly suppressed in both runs; it is
what remains that is wrong.

**The rule produced this correctly, which is the point.** Test 2 says an applied
manipulation whose response the paper studies is a perturbation. Timed mating is
applied, and the paper does characterise the gestational response. So the rule is
behaving as written and the writing is incomplete: **it covers the establishment
of a DISEASE state and says nothing about a normal PHYSIOLOGICAL state.** That
gap was named when the rule was drafted — *"That would need a home in the
taxonomy — either widening `disease_model_establishment` to 'state
establishment', or a tenth value"* — and was not closed. This is the cost of
leaving it.

**Needs a taxonomy decision**, the same shape as the one that produced the ninth
value: widen `disease_model_establishment` past disease, or add a tenth value for
physiological-state establishment. One paper in this set turns on it, so the
evidence for a whole new bucket is thin, and that is an argument the curator
should weigh rather than the author of the rule.

## DEFECT: Test 1 over-reaches when a germline lesion MODIFIES an established model

The one paper still unstable is `10.1101/2023.10.25.23297558` — `Trem2−/−` on the
`5XFAD` Alzheimer's background.

| | 5XFAD | `Trem2−/−` | result |
|---|---|---|---|
| r1 | suppressed as the model | **also suppressed** | `no` |
| r2 | suppressed as the model | reported as a perturbation | `yes` |

r2 is right, and its reasoning is the rule's own "what survives" clause: *"Trem2
loss is a functional genetic edit compared against its Trem2+/+ counterpart at a
fixed 5XFAD disease state and the paper attributes the loss of GPNMB expansion to
it."*

**But Test 1 reaches `Trem2−/−` too, because it is germline — nothing was applied
during the study.** So Tests 1 and 2 give opposite answers on the same candidate,
and the model arbitrates. That is an ordering error in the rule as written:
rulings 12 and 13 concern a genotype that **IS** the disease; `Trem2−/−` is a
genotype that **MODIFIES** an already-modelled disease, with the paper attributing
an effect to it. Test 1 must exclude that case and hand it to Test 2.

This one is not a curatorial question — it follows from rulings 9 and 10 read
together with the surviving-perturbation clause — so it is a fix to make rather
than a ruling to seek.

## Two unpredicted movements, both accounted for

**`s41590-023-01584-0`, `yes` -> `no` in both runs. This is the re-key
generalising.** Germline `B2m−/−` and `H2-Ab1−/−` genotypes suppressed under Test
1 — rulings 12 and 13 reaching a paper the curator never read. Evidence the cheap
test is not over-fitted to the two papers that produced it.

**`2024.10.27.620502`, `yes` -> `no` in both runs. Correct, and NOT attributable
to this version.** Its `yes` had rested on a `Matn4-mEGFP` knock-in — MARCKS,
EGFP, WPRE and bGH-polyA inserted by CRISPR — reported as a genetic perturbation
under v0.0.12 and v0.0.15. Both v0.0.16 runs reclassify it as
`reporter_or_marker`, which is the v0.0.9 rule that has been in the prompt for
seven versions: a pure labelling edit is not a perturbation. The call is right and
the movement is real, but the rule that fired predates this change and no claim
is made that the re-key caused it.

## What this licenses, and what it does not

**Licensed.** The attribution key reproduces the curator's line on 11 of the 12
papers it was tested against, improves reproducibility from 43/46 to 51/52,
returns exactly the six papers predicted and keeps exactly the five predicted,
and generalises to a paper nobody had read. `science.aay3224` closes as predicted.

**Not licensed.**

- **Ruling 11 is violated.** v0.0.16 should not be treated as accepted until the
  physiological-state gap is closed and re-measured.
- **One paper remains unstable** for the Test 1 / Test 2 conflict above.
- **52 of 392 papers**, all from the population where the rule was expected to
  act. Nothing here measures the other 340.
- The tier-7 **scope** question is untouched by this version and still open. Note
  the population it applies to has now changed again: papers keep leaving the
  animal-carried-`yes` set for criteria reasons, so the scope question should be
  re-posed against a fresh count rather than the earlier 38.

## Protocol note

`ACCEPTANCE-v0.0.14.md` added *check the acceptance set can act on the change*.
`ACCEPTANCE-v0.0.15.md` added *choose controls by what a category contains, not
by its name*. This run adds a third: **when a rule is written with a known gap,
the gap is a prediction of failure — write it down as one.** The
physiological-state hole was recorded in prose as an aside when the rule was
drafted. Had it been listed among the predictions, the pregnancy paper would have
been read as a confirmed forecast rather than found as a surprise.
