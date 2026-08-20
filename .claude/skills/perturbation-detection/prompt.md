# Perturbation Detection for Paper Curation — Extraction Prompt + Schema

Version: 0.0.5

## Changelog
- **v0.0.5: Generalized from a single paper to a corpus, and closed the remaining logic gaps in v0.0.4.** Three groups of changes. (1) *Corpus operation.* The instruction prompt still runs on exactly one paper per model call, but the document now specifies the batch layer around it: an input manifest, a multi-source `{{PAPER_TEXT}}` assembly with explicit source markers, a truncation policy, deterministic call settings, source-scoped quote verification with harness-side recomputation of the determination after unverified quotes are pruned, idempotency and retry rules, the output JSONL record, and a corpus-level triage table. (2) *Degraded input.* Fetch and extraction failures are no longer able to masquerade as negative determinations. `processing_status` and `text_completeness` are new required fields, and a paper whose text is incomplete cannot resolve to `perturbation_present = "no"`; it is capped at `"unclear"` and routed to review. Positive evidence is not capped, because missing text can conceal evidence but cannot manufacture it. (3) *Gap closure.* `has_single_cell_assay = "unclear"` combined with `single_cell_paired = "yes"` is now a consistency check rather than a silent cap; the empty `perturbations` array has an explicit terminal rule instead of resolving by vacuous truth; `paper_confidence` is redefined so it is meaningful for `"no"` and `"unclear"` determinations, not only `"yes"`; and the determination procedure is restated as two stages whose totality can be checked by inspection.
- **v0.0.5 addendum (curator ruling, 2026-08-19).** `samples[].perturbed` accepts `true | false | "unclear"`, resolving the defect carried unfixed through v0.0.3 and v0.0.4 where the schema showed a boolean while the instruction text said "true/false/unclear". The instruction prose was right: for a recall-biased task, collapsing an unresolvable sample group into `false` is a silent sample-level false negative. `pe.summarize` counts only `perturbed is true`, so "unclear" is never scored as perturbed.
- **Compatibility note for v0.0.5.** Unlike the v0.0.4 changelog, this version does NOT claim that re-scoring is a no-op. v0.0.5 changes determinations in at least two identified classes: (a) papers with truncated, failed, or Methods-missing text that previously resolved to `"no"` now resolve to `"unclear"`; (b) papers with `has_single_cell_assay = "unclear"` and a `single_cell_paired = "yes"` now trigger a resolution step instead of being capped silently. The output schema also changes in a breaking way: `evidence_quotes` and `assay_evidence_quote` become objects carrying `source_id`, and several run-level fields are added. Downstream parsers written against v0.0.3 or v0.0.4 will need updating. Measure the re-scoring delta on the labeled set rather than assuming it (see Validation loop, step 1).
- **v0.0.4: Made the `perturbation_present` determination logic total.** v0.0.3 defined "unclear" for the case where *every* pairing is "unclear" and "no" for the case where *every* pairing resolves to "no", but specified no outcome for a **mix** of "no" and "unclear" — which real papers produce routinely (two perturbations read out by bulk assays plus one whose pairing cannot be resolved). It also left `has_single_cell_assay = "unclear"` unhandled, since the "no" branch keyed only on `"no"`, and gave no precedence rule for contradictory inputs. v0.0.4 replaces the rule list with an ordered decision procedure plus an exhaustive truth table, adds explicit consistency checks, and states that a single unresolved pairing is enough for "unclear" regardless of how many "no" pairings accompany it (consistent with this task's bias toward recall). **No change to the instruction prompt's judgment criteria, the assay taxonomy, the pairing definitions, or the output schema** — v0.0.3 outputs remain valid, and re-scoring the 40-paper validation set under v0.0.4 changes no determination.
- **v0.0.3: Added the single-cell/single-nucleus sequencing assay-pairing requirement.** Detecting a perturbation is no longer sufficient on its own. `perturbation_present` now requires that the perturbed sample was itself profiled by a single-cell or single-nucleus sequencing assay (scRNA-seq, snRNA-seq, scATAC-seq, snATAC-seq, single-cell/nucleus multiome, CITE-seq, Perturb-seq/CROP-seq, etc.), not just that a perturbation and a qualifying assay both appear somewhere in the paper. This was driving false positives: papers that perturb samples for a bulk RNA-seq / qPCR / Western / flow-cytometry readout, while the single-cell dataset itself is built from unperturbed baseline samples, were being scored as perturbed. Added an assay taxonomy, a per-perturbation and per-sample assay-pairing field, a "single-cell suspension" wording trap, worked examples, and a revised determination logic and confidence rubric. Old `perturbation_present` semantics (any perturbation anywhere in the paper, regardless of assay) are preserved under the new field `perturbation_present_any_assay` for QA/debugging.
- v0.0.2: Clarified that `{{PAPER_TEXT}}` must concatenate title + abstract + body (some sources return the abstract separately); strengthened the post-processing substring check to normalize Unicode and tolerate common extraction/OCR artifacts before dropping quotes. No change to the model instruction prompt.
- v0.0.1: Initial prompt + schema.

---

## Scope and execution model

One model call per paper. The instruction prompt below is single-paper by design: batching several papers into one call lets evidence quotes leak between papers, lets a long paper truncate its neighbours, and forces whole-batch retries on a single malformed record. The corpus dimension lives entirely in the harness, which is specified in **Batch wrapper spec** at the end of this document.

Constants for a run:

| Constant | Value |
|---|---|
| `schema_version` | `0.0.5` |
| `prompt_version` | `0.0.5` |
| temperature | `0` |
| calls per paper | 1 (plus at most 1 JSON-repair retry) |

## How to use

Fill `{{PAPER_ID}}` and `{{PAPER_TEXT}}` for one paper, call the model once, verify the quotes, recompute the determination, then write one JSONL record. The model returns exactly one JSON object.

**Assembling `{{PAPER_TEXT}}` from multiple sources.** A paper is generally several extracted files: main text, sometimes a separately returned abstract, and one or more supplementary PDFs (perturbation dose, duration, and conditions frequently appear only there). Concatenate them into a single `{{PAPER_TEXT}}` with an explicit marker before each source, using the `source_id` values from the manifest:

```
<<<SOURCE id=main type=main_text>>>
Title ...
Abstract ...
Body ...
<<<SOURCE id=supp1 type=supplementary>>>
Supplementary Methods ...
```

Rules for assembly:
- Order sources as: title + abstract, main body, then supplementary in manifest order.
- `source_id` must be short, stable, and unique within the paper (`main`, `supp1`, `supp2`).
- Strip the reference list where the extractor makes that possible; record whether it was stripped.
- Do not renumber, reflow, or de-hyphenate the text. Quote verification compares against this exact string, so any normalization must be applied identically on both sides at verification time, not baked into the input.

**Required post-processing step:** after the call, verify that every quote is a substring of the source it claims. Because each quote now carries a `source_id`, verify it against that source's text specifically, and treat a quote that verifies only against a different source as a failure of attribution (flag `EV-WRONG-SOURCE`) rather than a pass. Before comparing, normalize BOTH the quote and the source text:
- Collapse/standardize whitespace (runs of spaces, newlines, non-breaking spaces to a single space).
- Normalize Unicode (NFKC) and standardize punctuation (en/em dashes to hyphen, curly quotes to straight quotes).
- Account for characters commonly lost in text extraction: sub/superscripts, Greek letters, and special symbols. PMC full text frequently drops superscripts, e.g. "1 x 10cells" (should be 10^6) or "prostaglandin E" (missing the "2").

Prefer a normalized-substring match or a high-threshold fuzzy match over exact `==`. Then apply the pruning and recomputation rules in **Batch wrapper spec, step 6**: unverified quotes are the guard against hallucinated evidence, and after pruning them the paper-level determination must be recomputed by the harness rather than trusted as returned.

---

## Instruction prompt

```
You are a biocuration assistant. Your job is to read one scientific paper and report whether the samples/subjects PROFILED BY A SINGLE-CELL OR SINGLE-NUCLEUS SEQUENCING ASSAY were experimentally PERTURBED, and if so, exactly what was perturbed. Your output feeds human curators, so precision of evidence matters, but you should bias toward RECALL: when a perturbation-to-single-cell-assay pairing is plausible but unclear, report it with lower confidence rather than omitting it.

## The central requirement: perturbation AND single-cell assay must apply to the SAME sample
A paper qualifies as perturbed for this task ONLY if a perturbed sample (see categories below) was ITSELF profiled by a single-cell or single-nucleus sequencing assay. It is NOT enough that the paper contains a perturbation somewhere AND a single-cell/nucleus assay somewhere. Many papers run a perturbation experiment validated by bulk RNA-seq, qPCR, Western blot, ELISA, or flow cytometry, while the single-cell/nucleus sequencing dataset in the same paper is built from separate, unperturbed samples (e.g., a baseline cell atlas, or untreated donor tissue). That is NOT a match. You must trace the specific sample/group that went into the single-cell/nucleus assay and determine whether THAT sample/group was perturbed.

## Step 0: Assess the text you were given
`{{PAPER_TEXT}}` may be incomplete: a paywalled paper, a failed or partial extraction, OCR noise, or a body without a Methods section. Assess this FIRST, because it constrains what your determination is allowed to be.

Set `processing_status`:
- **"ok"** — coherent, readable text of an apparently complete article.
- **"partial"** — readable but visibly incomplete or degraded: large runs of garbled characters, sections that end mid-sentence, an obvious front-matter-only or abstract-only extraction, or a body with no Methods-like content.
- **"failed"** — no usable scientific text at all (empty, a publisher access-denied page, a captcha or paywall notice, pure extraction noise).

Set `text_completeness`: **"full"**, **"truncated"** (text ends abruptly or is explicitly marked as cut), **"methods_missing"** (readable article body with no methods/experimental-procedures content anywhere, including in supplementary sources), or **"unknown"**.

If `processing_status` = "failed", return immediately with `perturbation_present` = "unclear", `paper_confidence` = 0.0, empty `perturbations` and `samples`, and a description of what you received in `ambiguities`. Do NOT report "no": absence of retrievable text is not evidence of absence of perturbation.

## Step 1: Identify the single-cell/nucleus sequencing assay(s) used
Qualifying assays (non-exhaustive; use judgment for close variants):
- scRNA-seq / single-cell RNA sequencing (10x Genomics Chromium, Smart-seq2/3, Drop-seq, inDrop, CEL-seq/CEL-seq2, MARS-seq, sci-RNA-seq, Seq-Well)
- snRNA-seq / single-nucleus RNA sequencing
- scATAC-seq / single-cell ATAC-seq; snATAC-seq / single-nucleus ATAC-seq; sci-ATAC-seq
- Single-cell or single-nucleus multiome (joint RNA+ATAC, e.g. 10x Multiome, ISSAAC-seq, SNARE-seq, SHARE-seq)
- CITE-seq, REAP-seq, ASAP-seq, TEA-seq (single-cell RNA/ATAC + surface protein)
- Perturb-seq, CROP-seq, CRISP-seq, Mosaic-seq, sci-Plex (pooled genetic or chemical perturbation screens with a single-cell RNA-seq readout — the perturbation and the single-cell assay are the same experiment by design)
- Patch-seq (single-cell electrophysiology + RNA-seq)
- Single-cell/subcellular-resolution spatial transcriptomics (MERFISH, seqFISH, Xenium, CosMx, STARmap) when the paper reports single-cell segmented data
- Single-cell DNA-seq / single-cell whole genome or exome sequencing

Explicitly NOT single-cell/nucleus sequencing (do not count these as qualifying, even if described near perturbation language):
- Bulk RNA-seq, bulk ATAC-seq, bulk ChIP-seq, CUT&RUN/CUT&Tag (bulk), bulk WGS/WES
- Microarray
- qPCR / RT-qPCR, digital PCR
- Western blot, ELISA, immunostaining/IHC/IF used as a protein readout
- Flow cytometry / FACS used as a readout (single-cell resolution but not a sequencing assay)
- Mass cytometry (CyTOF) (single-cell resolution but not a sequencing assay)
- Spot-based spatial transcriptomics that pools multiple cells per spot (e.g. standard 55-micron Visium) unless the paper explicitly performs single-cell deconvolution and reports it as single-cell data
- Any assay performed on pooled/bulk lysate, even if the input cells were sorted into a defined population first (sorting a population, then pooling for extraction, is still bulk)

**Wording trap:** the phrase "single-cell suspension" almost always refers to a tissue/sample DISSOCIATION step (preparing cells for FACS sorting, loading onto a bulk assay, etc.), not to a single-cell sequencing assay. Do not treat "dissociated into a single-cell suspension" or "single-cell suspension for FACS" as evidence of scRNA-seq/snRNA-seq. Only count it if the text goes on to describe a qualifying single-cell/nucleus sequencing method being applied to that suspension.

If no qualifying single-cell/nucleus sequencing assay appears anywhere in the paper, set `has_single_cell_assay` to "no" and do not proceed to perturbation matching, unless `processing_status` is "partial" or `text_completeness` is not "full" (in which case an assay may simply be missing from the text you were given, and the determination logic will cap the paper at "unclear" rather than "no").

## Step 2: Identify perturbations (same criteria as before)
Any deliberate manipulation applied to the samples/subjects, including:
- Chemical treatment (drugs, compounds, small molecules, agonists, antagonists, inhibitors, toxins, morphogens)
- Biologic treatment (cytokines, growth factors, ligands, recombinant proteins, functional antibodies)
- Activation or stimulation (e.g., TCR/BCR activation, LPS stimulation, receptor ligation)
- Genetic modification: knockout, knockdown, deletion, point mutation, overexpression, shRNA, RNAi/siRNA, CRISPR knockout, CRISPR activation (CRISPRa), CRISPR interference (CRISPRi), base/prime editing
- Physical / environmental conditions applied as a variable: temperature (heat shock, cold shock), pressure, hypoxia/anoxia, oxidative stress, irradiation, mechanical force, starvation
- Dietary intervention (special diet, fasting, supplementation)

### The core distinction you must make
Reagents used for ROUTINE SAMPLE PROCESSING or as a READOUT are NOT perturbations, even though they appear in Methods. A perturbation is a manipulation applied as an experimental variable to the biological system being studied. The same molecule can be a buffer in one paper and the studied perturbation in another. Judge the ROLE, not the mere presence of a word.

#### NOT perturbations by themselves (unless the paper makes the item the manipulated variable):
- Lysis / extraction / homogenization reagents (RIPA, TRIzol, RLT, guanidinium, etc.)
- Wash and dilution buffers (PBS, TBS, saline) used for washing/resuspension
- Fixation and permeabilization for imaging/flow (PFA, formaldehyde, methanol, Triton, saponin) as sample prep
- Blocking reagents (BSA, normal serum) used to reduce staining background
- Detection/staining antibodies and dyes used purely as a readout (IF, IHC, flow, Western)
- Standard culture media, FBS/serum, glutamine, non-essential amino acids for routine maintenance
- Antibiotics for routine culture sterility (pen/strep)
- Standard incubation conditions (e.g., 37C, 5% CO2), routine centrifugation
- Cryopreservation/thaw reagents (DMSO used only for freezing), dissociation enzymes (trypsin, collagenase, dispase, papain) used to prepare cells/tissue
- Library prep, sequencing chemistry, PCR reagents
- Transfection/transduction reagents used only to deliver a reporter or empty/control vector

### Rules for tricky cases
- Antibody: readout/detection (staining) = NOT a perturbation. Functional use (blocking, neutralizing, depleting, activating, agonist) = perturbation.
- Temperature / oxygen: standard culture incubation = NOT. Heat shock, cold shock, hypoxia chamber applied as a condition = perturbation.
- Genetic modification: a FUNCTIONAL edit (KO/KD/overexpression/CRISPRa/CRISPRi/shRNA/RNAi/functional point mutation) = perturbation. A pure LABELING/reporter edit (e.g., GFP knock-in only for tracking) = report as a LOW-confidence candidate and note it in "ambiguities". [TOGGLE: if the curator wants all genetic modifications counted, treat reporter edits as full perturbations.]
- Cell/animal model where the engineering is the studied point (e.g., an oncogene-transformed line, a transgenic disease model) = perturbation. A generic unmodified line (HeLa, HEK293) with no manipulation = NOT.
- Naturally occurring disease state or genotype in patient/donor samples with NO experimental manipulation (tumor vs adjacent normal, a donor carrying a variant) = NOT an experimental perturbation; note it in "ambiguities". [TOGGLE: include if the curator wants observational disease contrasts flagged.]
- Selection antibiotics (puromycin, G418) maintaining a stable line = the selection is NOT the perturbation; the introduced construct may be.
- Transfection/transduction: delivering functional cargo (shRNA, ORF for overexpression, sgRNA) = perturbation. Delivering only a reporter or empty/control vector = NOT.
- Vehicle-only / untreated CONTROL samples: mark those samples as perturbed=false, but their presence indicates the experiment contains a perturbation. Report the perturbation for the treated arm.
- A drug administered to patients as therapy, then sampled = a chemical perturbation (report it), even in a clinical study — PROVIDED the sampled material is what went into the single-cell/nucleus assay (see Step 3).

## Step 3: Pair perturbations to the single-cell/nucleus assay
For EACH perturbation identified in Step 2, determine whether the sample(s) it was applied to are the same sample(s) that went into a qualifying single-cell/nucleus assay from Step 1. Classify each perturbation's `single_cell_paired` as:
- **"yes"** — the text explicitly links the perturbed sample/group to a qualifying single-cell/nucleus assay (e.g., "PBMCs were stimulated with LPS for 4h and then processed for 10x scRNA-seq"; "tumors from Brca1-deleted mice underwent snRNA-seq"). A Perturb-seq/CROP-seq-type screen is always "yes" by construction.
- **"no"** — the text indicates the perturbed sample was assayed by something other than a qualifying single-cell/nucleus method (bulk RNA-seq, qPCR, Western, flow, ELISA, functional/behavioral assay, histology), OR the single-cell/nucleus assay in the paper is explicitly performed on a different, unperturbed sample set.
- **"unclear"** — a qualifying single-cell/nucleus assay exists in the paper and a perturbation exists in the paper, but the text does not make clear whether they were applied to the same sample/group (e.g., separate figures/sections that never state whether the scRNA-seq cohort included treated samples).

Do this per perturbation, and per sample group in the "samples" array (add `assay` and `is_single_cell_assay` there too — see schema). Different perturbations in the same paper can have different pairings (e.g., a genetic knockout validated by scRNA-seq = paired; a separate pharmacologic rescue experiment in the same paper validated only by qPCR = not paired).

## Evidence rules
- Every perturbation you report MUST be supported by at least one VERBATIM quote copied exactly from the paper text. Do not paraphrase quotes. Do not invent text.
- Every quote is an object: `{"source_id": "...", "quote": "..."}`. The `source_id` MUST be the id of the `<<<SOURCE>>>` block the quote was copied from. Never merge text across two source blocks into one quote, and never attribute a quote to a source it did not come from.
- Every `single_cell_paired: "yes"` or `"no"` determination should also be supported by a verbatim quote when the text states the assay used for that sample/group, in `assay_evidence`. Leave `assay_evidence` null if the pairing is inferred rather than explicitly stated (and reflect that in confidence).
- Supplementary sources are first-class evidence. A pairing stated only in supplementary methods is as good as one stated in the main text; cite it with that source's id.
- If you cannot find a verbatim quote supporting a perturbation, do not assert it.
- Ignore the reference/bibliography list, author affiliations, funding/acknowledgments, and competing-interest statements as sources of perturbation or assay evidence.

## Worked examples of the pairing rule
1. "Human keratinocytes were treated with 10 ng/ml IL-1β or vehicle for 6h, then processed for droplet-based scRNA-seq (10x Genomics)." → perturbation = IL-1β treatment; assay = scRNA-seq; **single_cell_paired = yes**, high confidence.
2. "Mice received either doxorubicin or saline. Heart tissue was collected for bulk RNA-seq to confirm cardiotoxicity gene signatures. In a separate cohort of untreated mice, we performed snRNA-seq to build a reference atlas of the healthy heart." → perturbation = doxorubicin; but the snRNA-seq cohort is explicitly the untreated one → **single_cell_paired = no**. `perturbation_present` = "no" for this paper (assuming no other qualifying perturbation exists), even though `perturbation_present_any_assay` = "yes".
3. "Cells were dissociated into a single-cell suspension and stimulated with PMA/ionomycin for 4h prior to intracellular cytokine staining and flow cytometry." → "single-cell suspension" here is a dissociation step, and the readout (flow cytometry) is not a sequencing assay → **single_cell_paired = no**.
4. "We performed a genome-wide CRISPRi Perturb-seq screen in K562 cells." → the perturbation screen and the single-cell RNA-seq readout are the same assay → **single_cell_paired = yes** by construction, high confidence.
5. "Tumor and adjacent normal tissue were profiled by scRNA-seq. Patients had received neoadjuvant chemotherapy prior to resection." → the sequenced tumor tissue is itself the post-treatment (perturbed) sample → chemical perturbation (chemotherapy) applied to the same tissue that underwent scRNA-seq → **single_cell_paired = yes**, though confidence depends on how explicitly the sequenced samples are tied to treated patients vs. a mixed/unclear cohort.

All five examples assume `processing_status` = "ok" and `text_completeness` = "full". On degraded text, example 2 would be capped at "unclear" by Stage B rather than resolving to "no".

## Resolution
- Give a paper-level determination.
- If the paper describes distinct sample groups / conditions / arms, enumerate them in "samples" using the labels the paper uses, and mark each perturbed true/false/unclear, linking to the relevant perturbation(s), and record the assay used for that group. If groups cannot be resolved, leave "samples" empty and report at paper level only.

## Confidence rubric
`paper_confidence` and each perturbation's `confidence` answer ONE question: how likely is it that a careful human curator, reading this same text, would assign the value you assigned? This definition holds for all three values of `perturbation_present`, so a well-evidenced "no" scores high and a coin-flip "yes" scores low. It is NOT a probability that the paper is perturbed.

- **0.80-1.0 (high).** For "yes": explicit statement that a treatment/modification was applied as a condition, with a clear agent and target, AND an explicit statement that the SAME sample/group was profiled by a qualifying single-cell/nucleus assay (or the perturbation is itself a single-cell screen, e.g. Perturb-seq). For "no": complete text in which either no qualifying assay appears, or every perturbed group is explicitly assigned to a non-qualifying readout, with quotes for both halves.
- **0.40-0.79 (medium).** Perturbation-like language and a qualifying assay both exist, but the pairing between the specific perturbed group and the single-cell/nucleus assay is inferred rather than explicitly stated, OR the perturbation's role is itself ambiguous (could be processing/readout). For "no": the negative rests on absence of a statement rather than on an explicit contrary statement.
- **0.20-0.39 (low).** Weak or indirect signal on either the perturbation or the pairing, included only because recall is prioritized. Any determination made on `processing_status` = "partial" text belongs here or lower.
- **For "unclear" determinations**, confidence expresses how confident you are that the pairing is genuinely unresolvable from this text, not how likely a hidden "yes" is. A high-confidence "unclear" means the text demonstrably never states the pairing; a low-confidence "unclear" means you may simply have failed to find where it does.

## Determination logic

### Field definitions
- `has_single_cell_assay` = "yes" if at least one qualifying single-cell/nucleus sequencing assay (Step 1) is used anywhere in the paper; "no" if none is; "unclear" if an assay is mentioned but its single-cell/nucleus status can't be confirmed (e.g., "sequencing was performed" with no method detail).
- `perturbation_present_any_assay` = "yes" if at least one perturbation of interest (Step 2) is applied to at least one sample, REGARDLESS of assay; "no" if none is; "unclear" if perturbation-like signals exist but their role as an experimental variable cannot be determined. This preserves the pre-v0.0.3 behavior for QA purposes and is not affected by any pairing decision.

`perturbation_present` is the primary field driving curation. Compute it in two stages. Stage A reads only the pairing evidence; Stage B applies the text-quality cap. Run the consistency checks before Stage A.

### Stage A: evidence-based determination
Evaluate in order and stop at the first rule that matches.

**A0.** `processing_status` = "failed" → **"unclear"**. Nothing was assessed.
**A1.** `perturbations` is empty:
  - `perturbation_present_any_assay` = "no" → **"no"**.
  - `perturbation_present_any_assay` = "unclear" → **"unclear"**.
  - `perturbation_present_any_assay` = "yes" → contradiction CC-2; resolve it. If genuinely unresolvable, → **"unclear"** and record CC-2 in `consistency_flags`.
**A2.** `has_single_cell_assay` = "no" → **"no"**. (Any `single_cell_paired` = "yes" alongside this is CC-1; resolve before applying.)
**A3.** `has_single_cell_assay` = "unclear" → **"unclear"** if any perturbation has `single_cell_paired` of "yes" or "unclear"; **"no"** if every perturbation has "no". (A "yes" here is CC-5; resolve before applying. A pairing cannot be more certain than the assay it pairs to.)
**A4.** At least one perturbation has `single_cell_paired` = "yes" → **"yes"**. One confirmed pairing is sufficient; the number of unpaired perturbations alongside it is irrelevant.
**A5.** At least one perturbation has `single_cell_paired` = "unclear" → **"unclear"**. This is the rule covering a MIX of "no" and "unclear": a single unresolved pairing is enough no matter how many "no" pairings accompany it, because this task prioritizes recall.
**A6.** Every perturbation has `single_cell_paired` = "no" → **"no"**.

Totality: A0 covers failed input; A1 covers the empty array; for a non-empty array A2, A3, and A4-A6 partition the three values of `has_single_cell_assay`, and within `has_single_cell_assay` = "yes" the rules partition the pairing multiset into (contains a "yes") / (no "yes", contains an "unclear") / (all "no"). No input reaches the end unmatched.

### Stage B: text-quality cap
If `processing_status` = "partial" OR `text_completeness` is anything other than "full":
- Stage A result **"no"** → report **"unclear"**, and state the reason in `ambiguities` and set `unresolved_reason` = "degraded_text".
- Stage A result **"yes"** or **"unclear"** → unchanged.

The asymmetry is deliberate. Missing text can hide the sentence that would have paired a perturbation to a single-cell assay, so a negative drawn from incomplete text is not trustworthy. It cannot invent a perturbation or a pairing, so a positive drawn from the text you do have stands on its own evidence. [TOGGLE: to keep strict negatives on degraded text, disable Stage B and rely on `processing_status` for filtering downstream.]

### Truth table (Stage A)
Rows are evaluated top to bottom; "any" means the value does not affect the outcome.

| `processing_status` | `perturbations` | `perturbation_present_any_assay` | `has_single_cell_assay` | `single_cell_paired` across all perturbations | Stage A |
|---|---|---|---|---|---|
| failed | any | any | any | any | unclear |
| ok or partial | empty | no | any | n/a | no |
| ok or partial | empty | unclear | any | n/a | unclear |
| ok or partial | empty | yes | any | n/a | CC-2, else unclear |
| ok or partial | non-empty | any | no | any | no |
| ok or partial | non-empty | any | unclear | at least one "yes" or "unclear" | unclear |
| ok or partial | non-empty | any | unclear | all "no" | no |
| ok or partial | non-empty | any | yes | at least one "yes" | yes |
| ok or partial | non-empty | any | yes | no "yes", at least one "unclear" | unclear |
| ok or partial | non-empty | any | yes | all "no" | no |

Then apply Stage B to every row whose Stage A result is "no".

### Consistency checks
These combinations indicate a mistake somewhere upstream, not a determination to report. Resolve the contradiction before returning, list the codes you hit in `consistency_flags`, and describe the resolution in `ambiguities`. If a contradiction is genuinely unresolvable, fall through to the stated default rather than emitting the contradictory state.

- **CC-1.** `has_single_cell_assay` = "no" together with any `single_cell_paired` = "yes". Either the assay exists (fix Step 1) or the pairing does not (fix Step 3). Default: A2.
- **CC-2.** `perturbation_present_any_assay` = "yes" with an empty `perturbations` array. Either report the perturbation you found, or set the field to "no". Default: "unclear".
- **CC-3.** `perturbation_present_any_assay` = "no" with a non-empty `perturbations` array. If you reported a perturbation, this field is "yes" (or "unclear" if its role is genuinely undecidable).
- **CC-4.** A `single_cell_paired` value that is anything other than "yes", "no" or "unclear". Every perturbation must carry exactly one of the three.
- **CC-5.** `has_single_cell_assay` = "unclear" together with any `single_cell_paired` = "yes". A "yes" pairing asserts that the sample went into a QUALIFYING assay, which would make `has_single_cell_assay` = "yes". Decide which is right: if the assay is identifiable, set `has_single_cell_assay` = "yes" and proceed to A4; if it is not, downgrade the pairing to "unclear". Default: A3.
- **CC-6.** `processing_status` = "failed" with a non-empty `perturbations` array. You extracted evidence, so the text was not unusable; downgrade to "partial".
- **CC-7.** A quote whose `source_id` is not one of the `<<<SOURCE>>>` ids you were given. Re-attribute it or drop the quote.

## Output
Return ONLY a single JSON object, no prose, no markdown fences, matching the schema below. Echo `schema_version` as "0.0.5".

PAPER_ID: {{PAPER_ID}}

SOURCE_IDS: {{SOURCE_IDS}}

PAPER_TEXT:
{{PAPER_TEXT}}
```

---

## Output schema

```json
{
  "schema_version": "0.0.5",
  "paper_id": "string (echo of PAPER_ID)",
  "sources_seen": ["main", "supp1"],
  "processing_status": "ok | partial | failed",
  "text_completeness": "full | truncated | methods_missing | unknown",
  "has_single_cell_assay": "yes | no | unclear",
  "single_cell_assay_types": ["e.g. '10x scRNA-seq', 'snATAC-seq', 'Perturb-seq'"],
  "perturbation_present": "yes | no | unclear",
  "perturbation_present_any_assay": "yes | no | unclear",
  "paper_confidence": 0.0,
  "unresolved_reason": "degraded_text | pairing_not_stated | assay_type_unconfirmed | perturbation_role_unclear | contradiction_unresolved | none",
  "consistency_flags": ["CC-5"],
  "perturbations": [
    {
      "category": "chemical | biologic | activation_stimulation | genetic | physical_environmental | dietary | other",
      "agent": "what is applied/used to perturb (e.g., 'LPS', 'doxorubicin 100 nM', 'sgRNA targeting TP53', 'anti-CD3/CD28')",
      "target": "what is perturbed (gene, pathway, cell type, organism, process)",
      "modality_detail": "dose / duration / method if stated, else empty string",
      "samples_affected": ["group or condition labels exactly as named in the paper"],
      "evidence_quotes": [
        {"source_id": "main", "quote": "verbatim span 1"},
        {"source_id": "supp1", "quote": "verbatim span 2"}
      ],
      "assay_applied": "assay used on this perturbation's sample(s), as stated in the paper, else empty string",
      "single_cell_paired": "yes | no | unclear",
      "assay_evidence": {"source_id": "main", "quote": "verbatim span tying this perturbation's sample to the assay"},
      "confidence": 0.0,
      "reasoning": "one sentence: why this is a perturbation and not routine processing/readout, AND why it is/isn't paired with a single-cell/nucleus assay"
    }
  ],
  "samples": [
    {
      "label": "group/condition label as named in the paper",
      "perturbed": "true | false | \"unclear\"",
      "perturbation_refs": [0],
      "assay": "assay used for this sample group, else empty string",
      "is_single_cell_assay": "yes | no | unclear"
    }
  ],
  "ambiguities": "free text: boundary calls made (reporter edits, disease-state-only samples, unresolved sample groups, unresolved assay-perturbation pairing, contradictions resolved), or empty string"
}
```

Notes on fields:
- `perturbation_refs` are zero-based indices into the `perturbations` array.
- `samples` may be an empty array when groups are not resolvable.
- `samples[].perturbed` is `true`, `false`, or the string `"unclear"`. Use `"unclear"` when the group exists but whether it was perturbed cannot be resolved from the text; do not collapse that into `false`. Only `true` is counted as perturbed downstream.
- `paper_confidence` is confidence that a careful human curator would assign the same `perturbation_present` value from the same text. See the rubric; it is not a probability of perturbation.
- `perturbation_present_any_assay` is retained from the pre-v0.0.3 logic and is useful for auditing how many perturbations are being filtered out purely by the assay-pairing requirement. A big gap between it and `perturbation_present` is expected and informative, not an error.
- `unresolved_reason` is "none" whenever `perturbation_present` is not "unclear". It is what makes the unclear bucket triageable at corpus scale: "degraded_text" goes to the re-fetch queue, "pairing_not_stated" goes to human curation.
- `assay_evidence` is `null` when the pairing was inferred rather than stated.
- `sources_seen` should echo the ids in the `<<<SOURCE>>>` markers. A mismatch against the manifest means the assembly step dropped a file.
- Run metadata (`model_id`, `prompt_version`, timestamps, token counts, source checksums) is added by the harness, not by the model. See the JSONL record in the batch spec.

**Breaking changes from v0.0.4:** `evidence_quotes` is now an array of objects rather than an array of strings; `assay_evidence_quote` (string) is replaced by `assay_evidence` (object or null); `schema_version`, `sources_seen`, `processing_status`, `text_completeness`, `unresolved_reason`, and `consistency_flags` are new and required.

---

## Toggle decisions to set before the full run

| Case | Default in prompt | Alternative |
|---|---|---|
| Reporter / labeling-only genetic edits (e.g., GFP knock-in) | Low-confidence candidate, flagged in `ambiguities` | Count as full perturbation |
| Naturally occurring disease state / donor genotype, no bench manipulation | Not a perturbation, flagged in `ambiguities` | Flag as perturbation of interest |
| Selection antibiotics maintaining a stable line | Not the perturbation (the construct is) | - |
| Perturbation validated only by bulk RNA-seq/qPCR/Western while a separate cohort is used for single-cell/nucleus sequencing | `single_cell_paired = no`; `perturbation_present = no` for that perturbation | Loosen to allow paper-level co-occurrence (not recommended, this is the behavior fixed in v0.0.3) |
| Spot-based (non-single-cell) spatial transcriptomics, e.g. standard Visium | Not counted as a qualifying single-cell/nucleus assay | Count spot-based spatial as qualifying |
| Degraded or truncated text that would otherwise resolve to "no" | Stage B caps it at "unclear" with `unresolved_reason = degraded_text` | Disable Stage B and keep the "no", filtering on `processing_status` downstream |
| Supplementary sources | Included as separate sources; quotes carry `source_id` | Main text only (faster, but loses dose/duration/condition detail that often appears only in supplementary methods) |
| Papers where quote verification prunes every perturbation | Determination recomputed on the pruned set, `EV-UNVERIFIED` flagged | Fail the paper to `processing_status = partial` and re-run once |

---

## Batch wrapper spec

The instruction prompt handles one paper. This section specifies everything else needed to run it over a corpus reproducibly. Steps are ordered; each is idempotent given the same inputs.

### 1. Input manifest
One JSONL record per paper, produced by the fetch/extract stage:

```json
{
  "paper_id": "10.1038_s41586-024-00000-0",
  "doi": "10.1038/s41586-024-00000-0",
  "pmcid": "PMC1234567",
  "fetch_status": "ok | paywalled | not_found | error",
  "sources": [
    {"source_id": "main", "source_type": "main_text", "path": "...", "extractor": "pmc_xml | pdf_text | ocr",
     "sha256": "...", "char_count": 48213, "references_stripped": true},
    {"source_id": "supp1", "source_type": "supplementary", "path": "...", "extractor": "pdf_text",
     "sha256": "...", "char_count": 9120, "references_stripped": false}
  ]
}
```

- `paper_id` must be stable and filesystem-safe. A DOI with `/` and `:` replaced by `_` is sufficient and keeps the mapping obvious.
- `fetch_status` other than "ok" with no usable source still gets a model-free record written with `processing_status = "failed"` and `perturbation_present = "unclear"`. A paper that was never retrieved must never be silently absent from the output, because absence is indistinguishable from a negative in any downstream count.
- `extractor = "ocr"` should set `processing_status` no better than "partial" by default.

### 2. Text assembly
Concatenate sources with `<<<SOURCE id=... type=...>>>` markers as described in **How to use**. Persist the exact assembled string (or its sha256 plus the ordered source list) so quote verification and any later re-verification compare against the same bytes.

### 3. Truncation policy
If the assembled text exceeds the context budget, drop content in this fixed order and set `text_completeness = "truncated"`:
1. Reference list (should already be stripped).
2. Author contributions, funding, competing interests, data availability.
3. Discussion.
4. Introduction / background.
5. Supplementary sources beyond the first, longest one.

Never drop Methods, Results, figure legends, or the abstract: pairing evidence concentrates there. If the budget cannot be met without cutting Methods, mark the paper for a section-level second pass rather than truncating blindly.

### 4. Model call
Temperature 0, one call per paper, response format constrained to JSON where the API supports it. Record `model_id`, `prompt_version`, `schema_version`, request timestamp, and input/output token counts.

### 5. Parse
On JSON parse failure, retry once with a repair instruction ("return only the JSON object"). A second failure writes `processing_status = "failed"`, `perturbation_present = "unclear"`, `error_code = "PARSE_FAILURE"`.

### 6. Quote verification and recomputation
For each quote, normalize both sides (see **How to use**) and check it against the source named by its `source_id`.

- Verified: keep.
- Verifies against a different source: keep the text, correct the `source_id`, flag `EV-WRONG-SOURCE`.
- Unverified anywhere: drop the quote, flag `EV-UNVERIFIED`.
- A perturbation left with zero verified `evidence_quotes`: drop the perturbation object entirely and flag `EV-PERT-DROPPED`.
- A `single_cell_paired` of "yes" or "no" whose `assay_evidence` failed verification: downgrade that pairing to "unclear" and flag `EV-PAIRING-DOWNGRADED`.

**Then recompute `perturbation_present` in the harness** by re-running Stage A and Stage B over the pruned object. Do not trust the model's returned value after pruning; a determination resting on a hallucinated quote must not survive the removal of that quote. Store both values (`perturbation_present_model`, `perturbation_present_final`) and count disagreements: a rising disagreement rate is the earliest signal of evidence fabrication.

### 7. Idempotency
Key each result on `(paper_id, prompt_version, model_id, sorted source sha256 list)`. A rerun over an unchanged corpus is a no-op unless forced, matching manuscript-harvest's behavior, so a killed run can be resumed safely.

### 8. Retries
Transport and rate-limit errors: exponential backoff, capped attempts, then `processing_status = "failed"` with `error_code`. Never write a failed call as a determination of "no".

### 9. Output record
One JSONL line per paper: the model object, plus a `run` block.

```json
{
  "run": {
    "run_id": "2026-08-19T10:00:00Z_cxg800",
    "model_id": "...",
    "prompt_version": "0.0.5",
    "schema_version": "0.0.5",
    "assembled_text_sha256": "...",
    "input_tokens": 0,
    "perturbation_present_model": "yes",
    "evidence_flags": ["EV-UNVERIFIED"],
    "error_code": null
  },
  "result": { "...": "the model object, post-pruning, with perturbation_present recomputed" }
}
```

### 10. Triage table
Flatten to one row per paper for curator review, sorted by this priority:

1. `perturbation_present = "unclear"` and `unresolved_reason = "pairing_not_stated"` (most likely to hide a real match).
2. `perturbation_present = "yes"` with `paper_confidence < 0.6`.
3. `perturbation_present = "unclear"` and `unresolved_reason = "degraded_text"` (route to re-fetch, not to reading).
4. `perturbation_present = "no"` with `perturbation_present_any_assay = "yes"` (the v0.0.3 filter doing its job; sample it, do not read all of it).
5. Any row with a non-empty `consistency_flags` or `evidence_flags`.

Columns: `paper_id`, `doi`, `perturbation_present`, `perturbation_present_any_assay`, `has_single_cell_assay`, `paper_confidence`, `unresolved_reason`, `n_perturbations`, `processing_status`, `text_completeness`, `consistency_flags`, `evidence_flags`, `agents` (semicolon-joined), `single_cell_assay_types`.

### 11. Corpus-level counters
Emit these per run. They are the acceptance criteria for the version, not decoration.

| Counter | Why it matters |
|---|---|
| Papers by `processing_status` | Separates curation failure from fetch failure |
| Papers by `perturbation_present` | Headline distribution |
| `unclear` split by `unresolved_reason` | Distinguishes "needs a human" from "needs a better PDF" |
| Papers moved "no" to "unclear" by Stage B | Direct cost of the v0.0.5 degraded-text rule |
| Papers hitting each CC code | Whether the contradictions are rare or systematic |
| Papers where `perturbation_present_model` != `perturbation_present_final` | Evidence fabrication rate |
| `any_assay = yes` and `perturbation_present = no` | Size of the assay-pairing filter's effect |
| Mixed no/unclear pairing papers | Frequency of the case v0.0.4 left undefined |

---

## Validation loop

1. **Re-score the 40-paper labeled batch under v0.0.5 and diff against the v0.0.4 output, per paper.** Do not assume the delta is zero. Expect movement in exactly two classes: papers capped by Stage B, and papers with `has_single_cell_assay = "unclear"` plus a "yes" pairing (CC-5). Any change outside those two classes is a bug in this version, not a refinement, and should be investigated before the corpus run.
2. For every paper where `perturbation_present` and `perturbation_present_any_assay` disagree, spot-check the full text to confirm the assay-pairing call. This remains the highest-value QA pass.
3. Hand-label a small stratified set specifically on the pairing dimension (clearly paired, clearly not paired, ambiguous pairing) if the automated pairing calls look unreliable.
4. Validate the multi-source path separately: pick papers whose perturbation detail lives only in supplementary methods and confirm the quotes carry the right `source_id` and that main-text-only runs miss them. This is the one behavior that cannot be checked on a main-text-only sample.
5. Check the counters in step 11 before extending to the full corpus. Two failure modes to watch: an unclear bucket dominated by `degraded_text` means the fetch pipeline is the bottleneck, not the prompt; a non-trivial `perturbation_present_model != final` rate means quotes are being fabricated and the confidence bands are not trustworthy.
6. Once pairing precision/recall look acceptable, extend to the full corpus and route by the triage priority in step 10.
