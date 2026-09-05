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
| 6 | `10.1126/science.aat1699` | no | 2026-09-03 |
| 7 | `10.1016/j.ccell.2025.12.003` | no | 2026-09-03 |
| 8 | `10.1016/j.cell.2021.11.031` | no | 2026-09-03 |
| 9 | `10.1016/j.cell.2021.12.018` | **yes** | 2026-09-03 |
| 10 | `10.1038/s41467-022-33184-1` | **yes** | 2026-09-03 |
| 11 | `10.1038/s41467-021-21783-3` | no | 2026-09-03 |
| 12 | `10.3389/fimmu.2023.1211505` | no | 2026-09-03 |
| 13 | `10.1016/j.isci.2022.104097` | no | 2026-09-03 |

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

**Consequence:** raised the organism gap. See `ACCEPTANCE-v0.0.12.md`. The agreed
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

## 6. `10.1126/science.aat1699` — a therapy that ENRICHED for a cell type is still not the variable

**Ruling: `no`, confirming the v0.0.14 extraction.** 2026-09-03.

> "it is not perturbation that was measured. it is a condition that enriched for
> certain cell type, but the patient treatment was not goal of the experiment nor
> was there an attempt to compare with untreated patients or anything along these
> lines."

Wilms' tumour / kidney atlas. Children received neoadjuvant cytotoxic
chemotherapy before nephrectomy, and the resected tumours were profiled by
scRNA-seq — so the sequenced material genuinely is post-treatment tissue.

**Why this ruling is worth recording even though the extraction agreed.** It is
the **first curator check on the v0.0.14 clinical-therapy fix**, and the fix was
written for it: under v0.0.12 this paper was `yes`, and its own recorded
reasoning cited *"(worked example 5)"* — the worked example that resolved a
neoadjuvant-chemotherapy case on conditions (i) and (ii) while predating (iii).
Rewriting that example moved the paper to a suppression under
`incidental_clinical_therapy`, reproducibly in both acceptance runs, with
`would_have_paired: "yes"` so it surfaces at triage P2 rather than flipping
silently. See `ACCEPTANCE-v0.0.14.md`.

**The new ground, which the prompt does not yet state.** The extraction reached
`no` on the AXIS argument — the paper's comparison is tumour clusters against
normal fetal/mature cell identities, and nothing is attributed to the drug. The
curator reached `no` on a different and stronger one: the treatment **enriched
for a cell population** rather than being studied. The paper says so plainly —
pre-treatment "reduced yield" and the recovered cells "represent therapeutically
relevant surviving cancer cells".

That matters because under the governing question as written, *"the drug changed
which cells are here"* can read as attribution, and attribution means report.
This ruling says it does not: **selecting or enriching a population is not the
same as the therapy being the studied variable.**

**This is the same principle as ruling 2, now stated positively.** There, P058's
ZBTB16+ blasts went from 0.67% at day 0 to 97.6% at day 28 and the authors read
it as selection of a pre-existing clone rather than chemotherapy changing cells.
Two papers, both with a large treatment-associated shift in cell composition,
both ruled `no` — so the shift itself carries no weight. What decides is whether
the paper sets out to characterise what the drug did. Absent a comparator arm and
absent that intent, a therapy is the setting no matter how much it moved the
composition.

**Open, and deliberately not acted on here:** whether to state the
enrichment-versus-attribution line in `prompt.md`. It would strengthen a rule the
extraction already applies correctly on both known papers, and every criteria
addition in this project's history has carried an attractor risk, so it needs its
own two-run acceptance rather than being folded into a ruling record.

## 7. `10.1016/j.ccell.2025.12.003` — establishing a disease model is not perturbing it

**Ruling: `no`, overturning the extraction's `yes`.** 2026-09-03.
**This ruling CONTRADICTS a written rule.** See "The bug it names" below.

> "engraftment of AKPS tumor organoids (Apc-LOF..) in mice is not perturbation.
> it is introduction of tumorous cells into the mice - to allow -> 'Tumors
> developed within six to eight weeks and progressively expanded through the
> colonic layers toward the peritoneal cavity, mirroring some of the invasive
> characteristics of late-stage human CRC'. When the single cell is performed -
> there is no real treatment or perturbation - it is cancer or no cancer and not
> the fact of the injection/engraftment/ etc. comparison of tumor/non tumor from
> adjacent sites is again not treatment or perturbation."

Colorectal cancer / tumour-associated neutrophil paper. The record is worth
reading closely, because **the paper's three real perturbations are all correctly
unpaired** and only the engraftment carried the call:

| # | perturbation | paired | readout |
|---|---|---|---|
| 0 | orthotopic transplantation of CRISPR-engineered AKPS organoids (Apc-LOF, p53-LOF, Kras-GOF, Smad4-LOF) | **yes** | BD Rhapsody scRNA-seq of CD45-enriched leukocytes, 6 weeks post-injection |
| 1 | anti-Ly6G neutrophil depletion vs isotype control | no | flow cytometry |
| 2 | CRC-organoid conditioned medium on donor neutrophils | no | flow cytometry, RT-qPCR |
| 3 | T Cell TransAct polyclonal activation | no | flow cytometry (CD69) |

So the assay-pairing filter worked exactly as designed on the three treatments,
and the `yes` rested entirely on **how the mice came to have tumours**. The
sequenced contrast is AKPS-injected against naive mice — cancer or no cancer.

**The bug it names.** `prompt.md` states, in the tricky-cases list: *"Cell/animal
model where the engineering is the studied point (e.g., an oncogene-transformed
line, a transgenic disease model) = perturbation."* Under that rule the
extraction was right, and it said so: "Deliberate engraftment of genetically
engineered tumor organoids is the study's experimental manipulation of the
animals (with naive mice as the comparator arm)." The curator's ruling says
otherwise, so **the rule is the thing to fix**, per this file's own instruction.

**Why the model had nowhere else to put it.** The eight-value suppression set has
no home for an *experimentally established* disease state:
`observational_disease_state` is defined as a "**naturally occurring** disease
state or genotype in patient/donor samples with **NO experimental
manipulation**". An engineered tumour model is neither naturally occurring nor
free of manipulation, so the taxonomy left the model a choice between reporting a
perturbation and inventing a ninth value it is told not to invent. It reported.
That is a gap in the closed set, not a misreading.

**Fourth instance of one principle.** Rulings 1, 2, 6 and 7 all reduce to *what
is the applied thing FOR?*

- ruling 1: a differentiation cocktail produces a target **cell identity** — the model.
- ruling 2: induction chemotherapy is the **backdrop** an intrinsic property is measured against.
- ruling 6: a therapy that **enriched** for a cell type is not thereby the variable.
- ruling 7: an engraftment produces a target **disease state** — the model.

In every case the applied thing is the route to the sample rather than the
variable under study, and in every case the sequenced axis is an identity or a
state rather than a treatment contrast. Ruling 7 is ruling 1 with "disease state"
substituted for "cell type" — including the curator's indifference to the route:
*"not the fact of the injection/engraftment/ etc."*

**Measured blast radius, so the fix can be sized before it is written.** Of the
115 papers determined `yes` in the 392-paper corpus, **7** have EVERY
`single_cell_paired = "yes"` perturbation reading as model establishment. They
are three different questions, not one:

- **This ruling's shape (3).** `j.ccell.2025.12.003` (ruled here);
  `s42003-021-02562-8` (heterotopic xenotransplantation of human hepatoblastoma);
  `s41586-022-05060-x` (surgical LAD ligation) — **already ruled `no` as ruling 4**,
  on the organism ground, and this ruling supplies a second and independent one.
- **Genuinely ambiguous (1).** `s41590-023-01504-2` — diphtheria-toxin ablation
  and intranasal bleomycin. Bleomycin against control mice is a drug applied to
  elicit a response, which is a textbook perturbation; it is also how the
  fibrosis model is established. This is the case that will decide how the
  reworded rule has to be phrased, and it needs reading.
- **Not this boundary at all (3).** `s41591-018-0269-2` (bortezomib, melphalan,
  ASCT), `s12943-025-02430-7` (FOLFOX and targeted antibody),
  `s41556-019-0446-7` (LVAD implantation) — applied clinical therapy in patients,
  which is rulings 2 and 6's territory.

So the rule change costs about 2-3 determinations beyond the papers already
ruled, and every candidate carries an `observational_disease_state` suppression
already, which is where the disease contrast would move.

**Not acted on here.** Fixing this needs a decision the ruling does not settle:
whether to widen `observational_disease_state` past "naturally occurring", or to
open the closed set to a ninth value for an experimentally established disease
state. That changes the taxonomy every corpus count is built on, so it is a
v0.0.15 with its own two-run acceptance.

## 8. `10.1016/j.cell.2021.11.031` — the same rule, keyed on the STATE and not the route

**Ruling: `no`, overturning the extraction's `yes`.** 2026-09-03.

> "similar to the CRC before. I don't see evidence for perturbations. it is a
> bunch of tumors and not tumors from various patients and conditions - not
> treatment or perturbation."

and, on being shown that the `yes` rested on the mouse arm rather than the
patient polyps:

> "Lrig1CreERT2/+;Apc2lox14/2lox14 were injected with 0.01mM 4-hydroxytamoxifen
> through colonoscopy-guided orthotopic injections into the mucosal lining of the
> distal colon, and were administered 2.5% DSS in drinking water for the
> following 6 days. Control mice received PBS injections followed by DSS." — the
> paper describes injection of mice intestines with cells caring mutations and
> inducible tumor - but that is not perturbation, it is a disease model.

Colonic tumour cell-of-origin paper. The human side was already handled: patient
polyps are suppressed under `observational_disease_state` ("spontaneous patient
lesions collected at routine screening... no manipulation applied by the
investigators"). The `yes` rested on three paired mouse perturbations — biallelic
`Apc` deletion driven from Lrig1+ stem cells, the same deletion driven from
Mist1+ non-stem cells, and 2.5% DSS.

**Why this ruling was needed even though ruling 7 exists.** Ruling 7 could have
been read narrowly, as being about *introducing diseased material* — engrafting
tumour organoids. Here **nothing is introduced**: 4-hydroxytamoxifen activates
Cre in the animal's own resident cells, against a PBS-injected control. A rule
keyed on the route would have caught the AKPS engraftment and missed this. The
ruling therefore fixes the rule's key: **what matters is that the manipulation's
purpose is to produce the disease state under study**, by any route —
engraftment, an induced mutation, a chemical or surgical induction protocol, a
transgenic model.

**And comparing two ways of making the model does not promote it.** This paper
sequences Lrig1-derived against Mist1-derived tumours, i.e. it varies the cell of
origin — a contrast *between* models. The curator still says no. That is exactly
the move ruling 1 made when two differentiation media were both sequenced and
compared: the applied thing does not become the variable just because the paper
ran two versions of it.

**Where the curator's phrasing and the record diverged, recorded because it
matters.** The first message described the human cohort ("a bunch of tumors and
not tumors from various patients and conditions"), which is the part the pipeline
had already excluded. The mouse `Apc`/DSS models were what actually carried the
call. Asking rather than assuming turned out to be worth it: the answer settled
that this is a **criteria** ruling (the induced model is not a perturbation) and
not a **scope** ruling (the mouse arm is out of curation scope), and those two
readings differ by an order of magnitude in how many papers they touch.

**Consequence:** v0.0.15. The tricky-cases rule that called an engineered disease
model a perturbation is rewritten; the Step 2 genetic bullet defers to it; Step
3's canonical pairing example stops using "tumors from Brca1-deleted mice
underwent snRNA-seq"; and `disease_model_establishment` becomes the ninth `rule`
value, because `observational_disease_state` excludes anything with experimental
manipulation and the case had nowhere else to go.

---

# The two axes these rulings live on

Eight rulings is enough to be confusing without a map, and two of them were
initially filed on the wrong axis. There are exactly two questions, and only the
first one changes a determination.

**Axis 1 — is there a perturbation at all?** A criteria question. Five rulings,
one principle: *what is the applied thing FOR?* If its job is to produce the
sample — the cell identity, or the disease state — it is the model. If its job is
to elicit a response in a sample that already exists, it is a perturbation.

| ruling | the applied thing | produces | verdict |
|---|---|---|---|
| 1 | differentiation cocktail | a target **cell identity** | model |
| 2 | induction chemotherapy | the **backdrop** an intrinsic property is measured against | setting |
| 6 | neoadjuvant chemotherapy | an **enriched** population, not an attributed effect | setting |
| 7 | engraftment of tumour organoids | a target **disease state** | model |
| 8 | induced `Apc` deletion + DSS | a target **disease state** | model |

**Axis 2 — is the paper in curation SCOPE?** Not a criteria question: the
perturbation is real and correctly found, but the material it was applied to is
not what reaches the curated deposit. Two rulings, and they are the reason triage
tier 7 exists.

| ruling | paper | the `yes` rested on | scope problem |
|---|---|---|---|
| 4 | `s41586-022-05060-x` | mouse LAD coronary ligation | human MI cohort is observational; the deposit is human |
| 5 | `science.aay3224` | Rag1-knockout mouse | human thymus atlas is observational |

**The thing worth noticing, and the reason to fix Axis 1 before answering Axis
2.** Rulings 4 and 5 may not be scope rulings at all. LAD ligation produces an
infarct; a Rag1 knockout produces an immunodeficient animal. Both are Axis 1
model establishment under ruling 7/8's rule, which did not exist when they were
made. If that is right, then part of triage tier 7 is a **criteria gap wearing a
scope costume**, and the 46 papers in it should be re-scored under v0.0.15 before
anyone rules on whether an animal-carried `yes` is in scope — because some of
them will stop being `yes` at all, for reasons that have nothing to do with
organism.

## 9-11. Three papers that RE-KEY the disease-model rule

Decided together on 2026-09-03, on the three papers that v0.0.15 left unstable —
each of which flipped across two runs of byte-identical input, because the rule
did not answer them. **The first two are the first `yes` rulings in this file.**

### 9. `10.1016/j.cell.2021.12.018` — **`yes`**

> "I read and confirm diet as one of the perturbations that have been introduced
> followed by Visium analysis."

Hepatic macrophage niche atlas. Methods: *"To induce NAFLD and NASH, mice were
fed a western diet (WD) high in fat, sugar and cholesterol for 24 or 36 weeks"* —
58% fat, 1% cholesterol, fructose/sucrose water, against a standard-diet arm.
Both arms sequenced (CITE-seq, snRNA-seq, Visium), diet as the analysis axis.

### 10. `10.1038/s41467-022-33184-1` — **`yes`**

> "I read and i think it is similar to diet in a sense - that the researchers have
> delivered trauma to the spinal cord and then were studying the response to the
> trauma (perturbation)."

Spinal cord injury atlas. Methods: *"A severe contusion was delivered to the
thoracic (vertebral level T9) spinal cord of mice, resulting in paralysis"* —
IH-0400 impactor at 90 kdyn, sequenced injured against uninjured across a
post-injury time course.

### 11. `10.1038/s41467-021-21783-3` — `no`

> "timed mating to induce pregnancy is not perturbation"

The `Brca1/p53` model in this paper was suppressed as a disease model in both
v0.0.15 runs, and that call stands. What was left was *"For the pregnancy time
points, females were mated with studs. Tissues were then harvested... at gestation
day 4.5, 9.5, and 14.5"*, with gestation day as the comparison axis against
nulliparous controls. One run reported it as a perturbation and carried the paper
to `yes`; the other reported nothing. The ruling settles it as **not** a
perturbation — obtaining pregnant animals establishes a physiological state, the
same shape as rulings 1, 7 and 8 applied to normal physiology rather than to
disease or cell identity.

---

**What rulings 9 and 10 refute, precisely.** v0.0.15 keyed the disease-model rule
on the manipulation's *purpose* and offered a structural tell: *"the sequenced
contrast is diseased tissue against healthy — a STATE contrast"*. **Both of these
papers have exactly that structure and both are `yes`.** Western diet against
standard diet is steatotic against lean liver; T9 contusion against uninjured is
injured against intact cord. So the tell is wrong, and a rule that reads "the
purpose was to induce the disease" reaches cases the curator calls perturbations —
the WD paper's own Methods say *"To induce NAFLD and NASH"* in as many words.

**The key the curator is actually using is ATTRIBUTION**, and ruling 10 states it
outright: *"studying the response to the trauma"*. The test is not what the
manipulation was for, nor what the contrast looks like. It is:

> **Does the paper characterise what the manipulation DID, or characterise the
> sample the manipulation produced?**

Attributed to the manipulation -> perturbation. Used to obtain the material that
is then characterised for something else -> model.

On that key every ruling in this file lines up, including the two that look like
counterexamples:

| ruling | manipulation | what the paper characterises | verdict |
|---|---|---|---|
| 1 | differentiation cocktail | the resulting cell type | model |
| 2 | induction chemotherapy | an intrinsic property visible despite it | setting |
| 6 | neoadjuvant chemotherapy | tumour vs normal compartments | setting |
| 7 | AKPS engraftment | tumour-associated neutrophil biology | model |
| 8 | induced `Apc`/`Braf`/`Kras` alleles | pre-malignant programs by cell of origin | model |
| **9** | **western diet** | **how macrophage niches respond to the diet** | **perturbation** |
| **10** | **T9 contusion** | **the response to the trauma over time** | **perturbation** |
| 11 | timed mating | mammary tumourigenesis across gestation | state, not a perturbation |

**And this is the same test the prompt already states elsewhere.** The
clinical-therapy governing question added at v0.0.11 reads *"Attributed to the
treatment = VARIABLE = report it as a perturbation... Used to reveal a difference
that was already there = SETTING = suppress it."* The disease-model rule is that
question asked of a bench manipulation instead of a therapy. v0.0.15 wrote it as
a second, differently-keyed rule when it should have been the same rule with a
wider subject — which is how this repo has generated a contradiction three times
before.

**Consequence, not yet implemented.** Re-keying the rule on attribution will
REVERSE some of the 11 determinations v0.0.15 moved, because several of them are
injury- or compound-induced models whose papers plainly study the response: nerve
constriction in a pain atlas, APAP in a liver-regeneration study, topical MC903
against vehicle in a skin atlas, ischaemia-reperfusion in a kidney study, LAD
ligation in cardiac-response papers. Those are rulings 9 and 10's shape. The
papers that should stay `model` are the ones where the manipulation produced a
sample characterised for something else — rulings 7 and 8. **Which papers move
back is a measurement, and it needs the curator's confirmation of the principle
before the rule is rewritten**, because it partially reverses a version that was
accepted two hours earlier.

## 12-13. A germline disease genotype is the model, and nothing was applied

Decided 2026-09-03 on two papers chosen specifically to test the line rulings 9
and 10 had just drawn, on the curator's request for *"2 more papers to examine to
make sure I got it right"*. Both are **germline genetic disease models where the
paper characterises the diseased tissue** — the one boundary no ruling had
touched, and the one where the extraction's calls rested on nobody's judgment.

### 12. `10.3389/fimmu.2023.1211505` — `no`

> "it is more disease vs healthy, and less of a perturbation. we are studying the
> way sick cells are behaving. So indeed it is a model of the disease, and less of
> a perturbation."

*Inflammation-mediated fibroblast activation and immune dysregulation in collagen
VII-deficient skin.* `Col7a1−/−` mice from targeted ablation of exons 14-18, a
model of recessive dystrophic epidermolysis bullosa, bred from heterozygotes;
front paw skin of 11-day-old KO (n=2) and WT (n=2) on 10x scRNA-seq. A second
hypomorphic allele provides a milder model.

### 13. `10.1016/j.isci.2022.104097` — `no`

> "the analysis was of healthy/non healthy and looking on tissues from kidney
> specifically focusing on tissues with injury or without injury - but **not
> CAUSING the injury** - instead studying the transcriptional signatures of
> tissues that appear to exhibit injury signs. Again they have mouse model for DKD
> that shows injury signs in glomeruli. Not perturbation."

*Slide-seqV2 discovery of disease-specific cell neighborhoods.* Two genotypes,
both sequenced against controls: BTBR `ob/ob` (a purchased inbred leptin-deficient
strain, JAX 004824) against BTBR `wt/wt`, and homozygous `UMOD-C125R` knock-in
against WT littermates.

---

**What these two settle.** Rulings 9 and 10 established that an APPLIED
manipulation whose response the paper studies is a perturbation, even when the
Methods say its purpose was to induce a disease. Rulings 12 and 13 establish the
other end: a genotype the animals were simply BORN with is the model, even though
it is a functional genetic lesion with a wild-type comparator and both arms
sequenced.

So the deciding feature is not the comparator, not the contrast structure, and
not whether the lesion is functional. **It is what the paper is telling you
about.** The curator's own words are the cleanest statement of the test yet
recorded — *"studying the way sick cells are behaving"* and *"not causing the
injury… studying the signatures of tissues that appear to exhibit injury signs"*
are both descriptions of characterising the MATERIAL, where ruling 10's *"studying
the response to the trauma"* is a description of characterising an EFFECT.

**And rulings 12 and 13 hand the rule a cheap first test.** In both papers nothing
was applied during the study at all: the animals were bred or purchased in the
diseased state. That is mechanically checkable and settles the whole germline
class without anyone weighing intent, which leaves the harder attribution
judgment for the cases where something genuinely was applied. A rule that puts
the cheap test first is easier to apply reproducibly, which matters because
intent tests are this pipeline's documented instability.

**They also settle a case the curator had not ruled on.** `science.aay3224`'s Rag1
knockout is still `yes` on criteria, and that was the extraction's call rather
than a ruling. A Rag1 knockout is a germline genotype the animals are born with,
so rulings 12 and 13 reach it: the criteria call should be `no`, and ruling 5's
`no` on scope becomes belt and braces rather than the only ground. **This is a
prediction, not a measurement** — `science.aay3224` was `yes` in both v0.0.15
runs and will need re-scoring under the re-keyed rule to confirm it.
