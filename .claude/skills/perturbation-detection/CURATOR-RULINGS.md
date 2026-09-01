# Curator rulings

Ground truth for this skill. Every entry is a determination the curator made by
reading the paper, with the reasoning that produced it.

**Why this file exists.** `prompt.md` states the rules; it does not record the
judgments the rules were derived from. Three of the rulings below turned on
reasoning that is not recoverable from the prompt text — twice the written rule
pointed the *opposite* way and the extraction reached the curator's answer anyway.
Without this file the next boundary change has to re-derive the curator's line
from scratch, which is how v0.0.9 and v0.0.10 each moved determinations nobody
intended.

**How to use it.** Before changing a Step 2 criterion, check whether a ruling here
already constrains it. When a change is proposed, these papers are the first
acceptance-set candidates. A ruling supersedes the prompt where they disagree —
and where they disagree, that is a bug to fix in the prompt, not a discrepancy to
tolerate.

**Scope note (2026-08-31).** The corpus is "human primarily, but... we SHOULD NOT
limit to human only. The paper could be mice, or zebrafish or killifish, or some
other specie." So no rule may hard-code human.

| # | paper | ruling | date |
|---|---|---|---|
| 1 | `10.1016/j.stem.2022.11.013` | no | 2026-08-28 |
| 2 | `10.1038/s41467-025-65049-8` | no | 2026-08-28 |
| 3 | `10.1038/s41467-025-67643-2` | no | 2026-08-31 |
| 4 | `10.1038/s41586-022-05060-x` | no | 2026-08-31 |
| 5 | `10.1126/science.aay3224` | no | 2026-08-31 |

---

## 1. `10.1016/j.stem.2022.11.013` — differentiation is a cell type, not a perturbation

**Ruling: `no`.** 2026-08-28.

> "different target cell type, not perturbation. I don't really care what has been
> applied to get to the final diff cell type."

Fetal lung tip organoids. LinPOS organoids in self-renewing medium and alveolar
organoids in AT2 differentiation medium were **both** sequenced (Methods, "Organoid
single-cell RNA sequencing": "Four biological replicates of the LinPOS and alveolar
organoids"). Relative to the comparator arm the AT2 medium adds dexamethasone,
cAMP, IBMX and DAPT, withdraws EGF, Noggin, FGF10 and FGF7, and raises CHIR99021
from 3 µM. So an applied contrast existed inside the sequenced set.

**What the prompt said at the time:** v0.0.9 rule 4 excluded a derivation cocktail
only when "given uniformly to every sample". It was not uniform here, so the rule's
own precondition failed — and the extraction reached `no` anyway by asserting both
arms "received the same standard differentiation cocktail", which the Methods
contradict. Right answer, false premise.

**Consequence:** v0.0.11 dropped the uniformity precondition. The test became *what
is the manipulation FOR* — a different target cell identity is the model; a named
factor varied at fixed identity is a perturbation. v0.0.6's carve-out is untouched;
it was always about a contrast in a factor, never in cell identity.

## 2. `10.1038/s41467-025-65049-8` — applied therapy can be the setting, not the variable

**Ruling: `no`.** 2026-08-28.

> "in reality all the kids have been treated, even though as reference point they
> indeed use day 0. but the main point of the paper is to identify mechanism that
> has not been engaged, despite the treatment — the focus of the study was not to
> learn how chemo or other treatment makes the cells behave, but to identify the
> outlier."

T-ALL. Eight children have paired day-0 and day-28 scRNA-seq spanning induction
chemotherapy. Cohort axis is responder vs non-responder across 58 children; the
payload is a **day-0** biomarker for refractoriness, useful only before treatment;
and P058's ZBTB16+ blasts go 0.67% at day 0 to 97.6% at day 28, which the authors
read as selection of a pre-existing clone rather than chemotherapy changing cells.
Day-28 samples are 8 of 84 and confirmatory.

**What the prompt said at the time:** v0.0.10's guard 5 said "where two sequenced
timepoints straddle the therapy... report it as a chemical perturbation, do not
suppress it", while the incidental rule offered three signals "any one of which is
enough" to suppress. This paper satisfied both and nothing said which won — an
unstated contradiction of the kind v0.0.7 was written to remove. The extraction
picked the curator's side, reasoning that "the paper's own analysis axis is
response status... rather than a named applied therapy."

**The line, and the paper that holds the other side of it:**
`10.7554/elife.104978.2` states relapse involves a "chemotherapy-driven lineage
switch" — the drug changed the cells, so it stays `yes`. Both papers have two
sequenced timepoints straddling chemotherapy; only attribution separates them.

**Consequence:** v0.0.11 replaced the structural test with the governing question —
*is the applied thing the study's VARIABLE, or its SETTING?* Guard 5 now settles
only whether a condition was unintended and hands the perturbation question on.
Verified twice on 22 papers: `elife.104978.2` held at `yes` in both runs.

**Shares one principle with ruling 1.** Differentiation is setting because it
defines what the cells are; induction chemotherapy here is setting because it is
the backdrop against which an intrinsic property is measured.

## 3. `10.1038/s41467-025-67643-2` — sequenced organoids with nothing under investigation

**Ruling: `no`, confirming the extraction.** 2026-08-31.

> "the organoids from patients have been subjected to scRNA-seq - but no
> perturbation has been investigated."

Four drugs are applied in the paper — neratinib (immunoblot), trastuzumab
deruxtecan (MTT), exatecan (flow cytometry), enfortumab vedotin (mouse xenograft) —
and every readout is non-qualifying. scRNA-seq and DLP were run on untreated
patient organoids.

**Why it is recorded even though the extraction agreed.** It is the first curator
check on the *older* half of the derivation rule — a **maintenance** formulation
(PDO culture medium: hepatocyte media + EGF, CS-FBS, Primocin, Glutamax, Y-27632)
where nothing becomes a different cell type, so ruling 1's identity framing does
not apply. That half holds. It also confirms the pairing filter on a paper with
four real drugs, which is the mechanism doing the work here.

**Noted defect, determination unaffected:** the suppression's `evidence_quote` cites
a transport step ("tumor specimens were transported on ice... in organoid culture
media") when the same medium is used for dissociation and Matrigel culture. Weak
quote selection, correct candidate.

## 4. `10.1038/s41586-022-05060-x` — a mouse model paralleling human data does not make the paper perturbed

**Ruling: `no`, overturning the extraction's `yes`.** 2026-08-31.

> "the surgical coronary artery ligation and mice models were indeed performed but
> more as comparisons or parallels to human data. the CellXGene contains the human,
> and not mouse data."

Human heart infarction atlas. The `yes` rested entirely on surgical LAD coronary
artery ligation in mice with sham controls, paired to mouse scRNA-seq. The human
snRNA-seq / snATAC-seq / Visium cohort is naturally occurring MI, suppressed under
`observational_disease_state`. The Pdgfrb-CreER;tdTomato lineage-tracing allele was
suppressed under `reporter_or_marker`; RUNX1 overexpression was reported but
unpaired (bulk RNA extraction).

**Consequence:** raised the organism gap. See `PROPOSAL-organism.md`. The agreed
shape is to record the organism of the paired material and **not** to call on it —
the determination stays species-agnostic and a human interpreter applies the
filter, because a non-human dataset can be a legitimate curation target and the
paper generally cannot say which species was deposited.

## 5. `10.1126/science.aay3224` — atlas paper, animal knockout carrying the call

**Ruling: `no`, overturning the extraction's `yes`.** 2026-08-31.

> "it is a paper building single cell atlas from thymus derived cells. no
> perturbation of human T-cells."

Human thymus atlas. The `yes` rested entirely on a Rag1 knockout **mouse**, on an
evidence quote that is a figure-legend abbreviation gloss ("Rag1KO: Rag1 knockout
mouse") with a pairing quote from a supplementary UMAP caption. Lowest confidence
in the 50-paper run at 0.45, and flagged P3 for that reason.

**Same gap as ruling 4** — an animal perturbation carrying a paper whose human data
is observational. Both were independently flagged in review as the least
trustworthy calls in the set without the reviewer identifying the common cause.

**Also noted:** the thymic fibroblast explant medium (DMEM + 15% FBS) was suppressed
under `derivation_formulation`. That is ambient Methods with no bioactive factor —
mild over-population, harmless here (`would_have_paired: no`).
