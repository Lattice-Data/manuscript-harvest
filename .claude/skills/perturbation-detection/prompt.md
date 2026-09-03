# Perturbation Detection for Paper Curation — Extraction Prompt + Schema

Version: {{TASK_VERSION}}

## Changelog

- **0.0.14: the spec stops answering one question twice.** No new field, no new value set, no change to Stage A, Stage B or the truth table. What changed is that **fourteen places where this file said two different things about the same case now say one thing** — and one of those fourteen moves determinations, so it is stated first. **The clinical-therapy boundary is re-decided.** v0.0.11 added condition (iii) — *does the paper treat the therapy as its VARIABLE or its SETTING?* — because (i) and (ii) alone returned the wrong answer on `10.1038/s41467-025-65049-8`. But every clinical-therapy precedent in the file predated (iii) and none had been re-decided under it: **worked example 5** resolved a neoadjuvant-chemotherapy case to `single_cell_paired = yes` on (i) and (ii), and its own stated axis (tumour versus adjacent normal) is one of the signals the spec elsewhere lists as evidence the therapy is the SETTING — so the file's one worked positive for clinical therapy contradicted the rule that governs it. Worse, the `suppressed_candidates` precedence note declared that "report rules win" and gave "a named therapy tied to specific sequenced samples" as one of them — conditions (i) and (ii) standing alone, i.e. the exact test v0.0.11 replaced, asserted as *overriding* the rule that replaced it. A setting-type therapy tied to sequenced samples therefore had an explicit precedence claim on one side and an explicit suppression order on the other, with nothing to arbitrate. Example 5 is now decided under (iii) (it suppresses, and carries the flip case that would make it report), and the precedence note names all three conditions. `incidental_clinical_therapy` is the second-largest suppression reason at **86 of 392 papers**, so **determinations are expected to move here** — that is the point, not a side effect, and the direction is `yes` -> `no` on papers whose therapy is a setting. **The incidental rule's threshold is stated once.** The rule says any one of its three signals is enough and that none of them settles a named, tied therapy at all; the precedence note said "the three signals are met", conjunctive. They part company on every cohort that trips some but not all three — the majority case, and the one example 5 is built on. **The NOT list's blanket escape clause is gone.** The heading qualified the whole list with "unless the paper makes the item the manipulated variable", which is the defect v0.0.5 shipped and v0.0.6 named — in a pipeline-benchmarking paper the protocol *is* the manipulated variable, so the clause promoted exactly the cases the list exists to exclude. It was fixed for `sample_handling_protocol` only, and that rule announced itself as "the one place" the qualifier does not reach while three others were equally flat and said nothing. The heading now draws the line the rules actually use — an item is promoted when the paper applies it as a **biological** variable, never when it is the paper's **technical** variable — and **an eight-row table owns the exclusion reasons' scopes**, stating per rule whether promotion can reach it. That table also removes the overlap where `routine_processing` and `sample_handling_protocol` both claimed a media-brand comparison, a storage-duration series and a dissociation-enzyme benchmark: both fired on the corpus (63 papers against 27), so the tally the eight-value scheme exists to produce was splitting one population in two. Bucket labels move; determinations cannot, since a suppressed candidate never reaches Stage A. **Step 1 no longer tells the model to stop.** "Do not proceed to perturbation matching" on a no-assay paper contradicted two required fields defined over Step 2 regardless of assay, so such a paper had to either disobey the instruction or omit a required field. Dormant here — 0 of 392 papers have `has_single_cell_assay` other than "yes", the corpus being pre-filtered — and not dormant for the next pack. **A failed extraction's 0.0 is named as a sentinel** rather than left contradicting a rubric that would score it near 1.0, and **the partial-text confidence ceiling now applies only to negatives and unclears**, matching Stage B's deliberate asymmetry: missing text can hide a pairing but cannot invent one. Measured cost of the old wording — all three positives on partial text sat at confidence **exactly 0.38**, the ceiling rather than the evidence, which routed every one of them into triage tier 3 by rubric rather than by any judgment about the paper. Also: the model-facing text no longer names a `schema`/`prompt` version pair that 0.0.13 abolished; Step 2 no longer claims "(same criteria as before)" after five revisions of those criteria; the cell-line organism bullet no longer tells the model to infer a species the bullet above it forbids inferring; and the batch spec now describes the record the harness actually writes — it specified a `{"run": ..., "result": ...}` envelope with `run_id`, `error_code`, `input_tokens` and `assembled_text_sha256`, none of which appears in any record or anywhere in the code, while claiming a model-free "failed" record is written for an unfetched paper, which is the determination-shaped row for an unassessed paper that the rule it cites exists to forbid. **What holds this.** `tests/test_spec_self_consistency.py` (12 guards, one per finding, every parser failing loudly when it matches nothing) plus two in `test_prompt_pack_agree.py` binding the new table to the closed `rule` set — because owning the scopes means restating the eight names, and a value stated in N places and changed in fewer is what cost this project two runs of one paper at v0.0.7 and a 386/6 record split at v0.0.12. **Tests 165 -> 183.** The 392 stored records re-validate to a byte-identical summary CSV and a byte-identical 5,836-line review screen, at 140 issues and 2,471 of 2,471 quotes verified — because nothing outside the model's own reading changed. **Not yet done, and it is the part that matters: no paper has been re-scored under 0.0.14.** The determination effect of the clinical-therapy fix is predicted, not measured.

- **0.0.13: one version number, and it is no longer written in this file.** Replaces the `prompt_version` + `schema_version` pair with a single `task_version`, declared once in `task/task.yaml` and spliced into this file by `pe.prepare` as `{{TASK_VERSION}}` — the same mechanism as `{{PAPER_ID}}`. **No criterion changed and no determination logic changed:** Stage A, Stage B, the truth table, every Step 2 rule and the closed `rule` set are untouched, so `pe.validate`'s mirror and `tests/test_determination_v005.py` remain valid unchanged. The two numbers were separate only because somebody had to judge, per revision, whether the record shape had moved — and the cost of that judgment is measured. At v0.0.12 the answer was yes and three of the four declaration sites were updated; the model split on the contradiction, **386 of 392 corpus records followed the schema example and emitted 0.0.7 while 6 followed the instruction line and emitted 0.0.6**, and the validator was calibrated to the minority, filing a spurious issue on 386 correct records. The judgment is now replaced by two facts: `task_version`, which the harness grades a record against, and `pack_sha256`, a hash over every rule-bearing file that answers "were these two records produced under the same rules" by comparison rather than by opinion. A version bump says the author thought something changed; the hash says whether anything did. Records written before 0.0.13 carry `schema_version` and no `task_version`; `pe.validate` reads them as their run's `prompt_version` and says so, so the 392 already scored stay comparable without a re-run.

- **v0.0.12: the record says WHOSE sample was perturbed. `schema_version` 0.0.6 -> 0.0.7, two new fields, and NO determination-logic change.** Stage A, Stage B and the truth table are untouched, so `pe.validate`'s mirror and `tests/test_determination_v005.py` remain valid unchanged. Curator rulings of 2026-08-31 (`CURATOR-RULINGS.md` 4 and 5). `perturbation_present` asked whether a perturbed sample was profiled by a qualifying assay; it never asked **whose** sample. So a paper could be `yes` on the strength of an animal model while the human data — the material that actually reaches the curated deposit — was purely observational. Measured on the 50-paper v0.0.11 run: **5 of 15 positives rest on an animal-only pairing, ~10% of a random sample**, the largest single source of false positives found in any run. Two were ruled `no`: `10.1038/s41586-022-05060-x` (`yes` from surgical LAD ligation in mice; the human snRNA-seq/snATAC-seq/Visium cohort is naturally occurring infarction) and `10.1126/science.aay3224` (`yes` from a Rag1 knockout **mouse**; the paper is a human thymus atlas). Both had been independently flagged in review as the least trustworthy calls in the set without the reviewer identifying the common cause — one gap, not two judgments. New: `paired_organism` on each perturbation, `organism` on each sample group. Triage gains tier **7** in the previously-unused slot, so **tiers 1-6 do not renumber**.
  **The field is recorded and deliberately NOT acted on**, per the curator: the corpus is "human primarily, but... we SHOULD NOT limit to human only. The paper could be mice, or zebrafish or killifish, or some other specie", and the instruction was to add the column "but not call on it — leaving it to the human interpreter." Two reasons that is the right place for the line rather than mere caution. (i) **A non-human dataset can be a valid curation target**, and hard-coding human into the determination would drop it silently — the worst direction for a task that is deliberately recall-biased. (ii) **The paper usually cannot say which species was deposited.** `s41586-022-05060-x` contains both human and mouse single-cell data and nothing in the text says which reached CellxGene; that is knowledge about the deposit, not the publication. Recording is answerable, filtering is not. The value is an OPEN string, unlike `rule`, because a closed set would have to enumerate every model organism in advance and killifish is exactly the case that breaks such a list.
  **Guards, written from the two previous field additions rather than discovered again.** Both `suppressed_candidates` (v0.0.10) and the v0.0.9 boundary rules moved determinations nobody intended, so: precedence is stated FIRST and says recording an organism must never shorten `perturbations`, never change a `single_cell_paired` value, and never change `perturbation_present` — a mouse perturbation paired to mouse scRNA-seq is still `"yes"` on both counts; `null` is declared legitimate, common, and **not to be guessed** from the journal, the authors, the cell-line name or the base rate; the per-perturbation scope is stated explicitly, because collapsing a human-plus-mouse paper to one organism is the specific failure the field exists to prevent; and the xenograft case is named (the organism of the PROFILED cells, so human cells in a mouse host are `"human"`). `pe.validate` additionally raises an issue if a species-only difference ever coincides with a determination change, and `tests/test_organism.py` asserts over every Stage A input combination that it cannot.

- **v0.0.11: Two boundary rules whose written test pointed away from the curator's line, plus the precedence gap between them.** Curator rulings of 2026-08-28, taken after the 30-paper v0.0.10 evaluation (`EVAL-30-v0.0.10.md`). **No schema change and no determination-logic change** — Stage A, Stage B and the truth table are untouched, `schema_version` stays `0.0.6`, and `pe.validate`'s mirror plus `tests/test_determination_v005.py` remain valid unchanged. Nothing in `pe/` encodes these criteria, so the change is confined to this file. Two rules and one bug:
  1. **Directed differentiation is a target cell type, not a perturbation — the uniformity precondition is dropped.** v0.0.9 rule 4 excluded a derivation cocktail only when "given uniformly to every sample". On `10.1016/j.stem.2022.11.013` it was not uniform: LinPOS organoids sat in self-renewing medium while alveolar organoids got 7 days of AT2 differentiation medium (+ dexamethasone, cAMP, IBMX, DAPT; − EGF, Noggin, FGF10, FGF7; CHIR99021 raised from 3 µM), and **both arms were sequenced**. The precondition therefore failed and the rule should not have fired — yet the extraction reached the curator's answer anyway, by asserting in `why` that both arms "received the same standard differentiation cocktail", which the Methods contradict. Right answer, false premise. The curator's ruling is that the answer is right for a different reason: "different target cell type, not perturbation... I don't really care what has been applied to get to the final diff cell type." The test is now **what the manipulation is FOR** — a different target cell IDENTITY is the model; a named factor varied while identity is held fixed is a perturbation. The v0.0.6 carve-out is untouched, because it was always about a contrast in a *factor*, never in cell identity.
  2. **An applied clinical therapy is a perturbation only when the paper treats it as the VARIABLE, not the SETTING.** v0.0.10's guard 5 tested structure — "where two sequenced timepoints straddle the therapy... report it as a chemical perturbation, do not suppress it" — which cannot separate the two cases it now has to. `10.7554/elife.104978.2` says relapse involves a "chemotherapy-driven lineage switch": the drug changed the cells, so it stays a perturbation and that paper stays `yes`. `10.1038/s41467-025-65049-8` reports a ZBTB16+ blast population already present at diagnosis (0.67% at day 0, 97.6% at day 28) and delivers a **day-0** biomarker for refractoriness across a responder/non-responder cohort of 58 children: the therapy is the sieve, not the subject, and the curator ruled it not a perturbation. Both papers have two sequenced timepoints straddling chemotherapy, so only an attribution test separates them.
  3. **The precedence gap those two left.** Guard 5 ordered "report it"; the incidental-therapy rule offered three signals "any one of which is enough" to suppress. `s41467-025-65049-8` satisfied both and nothing said which won, so the model was arbitrating an unstated contradiction — the exact shape v0.0.7 fixed for temperature and the reason two runs of one paper can disagree. Guard 5 now settles only whether a condition was *unintended* and hands the perturbation question to the clinical-therapy rules; the incidental rule defers to the governing question. All four places that state these rules — the unintended-condition bullet, the derivation bullet, the report rule, and the toggle table — are changed together, since leaving one behind is what caused the v0.0.7 precedence bug.
  **Known cost, accepted deliberately.** Both edits WIDEN exclusions, so the expected direction of travel is toward `no` — the lossy direction for a task that is deliberately recall-biased. **That is a tendency, not a guarantee: the precedence rewrite can also un-suppress**, and a candidate moving out of `suppressed_candidates` into `perturbations` with an unclear pairing drives the paper to `unclear` under A5. Change 2 also replaces a structural test with an intent test, which is less reproducible than what it replaces. Both keep a toggle. Measured blast radius is zero across the 30-paper set (all 30 determinations already match the curator, and the other three `derivation_formulation` papers were uniform anyway) and zero on the 6-paper regression set by construction, since `elife.104978.2` is held at `yes` by design. **Unmeasured everywhere else.** Per the v0.0.10 lesson, the acceptance test is determination-only against the preserved v0.0.10 baseline, run twice, and must include papers that must NOT move: `s41467-026-69587-7` (MC903, dupilumab) and `celrep.2019.03.099` (App knock-in).
- **v0.0.10: The NOT list stops being silent — suppressed candidates become a first-class, countable field.** `schema_version` 0.0.5 -> **0.0.6**, one new required field: `suppressed_candidates`. **No determination-logic change** — Stage A, Stage B and the truth table are untouched; a suppressed candidate is by construction not a perturbation, so `pe.validate`'s mirror and `tests/test_determination_v005.py` remain valid unchanged. What v0.0.9 fixed, it also hid. v0.0.9 moved four categories from "report as a low-confidence candidate" to "do not report at all", each instructed to "note it in `ambiguities`" — a single free-text string. Two consequences, both measured on 10.1038/s44318-024-00328-6. (i) **Not enforceable.** A regression run under final v0.0.9 returned the correct determination (`no`, 8-9 perturbations, all `single_cell_paired: "no"`) and never mentioned the SFTPC-GFP reporter in `ambiguities` at all, so the record cannot distinguish "considered the transgene and excluded it under the reporter rule" from "never noticed it" — very different levels of trust. (ii) **Not countable.** "How many papers in this corpus were held back from `yes` by the reporter rule?" is exactly the question that justified v0.0.9, and it had to be answered by hand-reading 9 papers. This is worse than an ordinary logging gap because of Stage A rule **A5**: one perturbation with `single_cell_paired = "unclear"` drives the whole paper to `unclear`, so under v0.0.8 a suppressed-today candidate was load-bearing. Suppression is a determination-affecting decision, and it was the one decision the record did not capture. v0.0.10 adds one entry per candidate deliberately not listed, with a **closed** eight-value `rule` set so it can be tallied, and `would_have_paired` so a curator can see which suppressions sit one toggle away from flipping the paper. The four v0.0.9 categories are joined by the four older rules that have always been silent — observational disease state, sample-handling protocol, readout reagent, routine processing — so the field means "everything the NOT list swallowed", not "the v0.0.9 additions". `ambiguities` stays, narrowed to genuine narrative: contradictions resolved, unresolved sample groups, degraded-text notes. Triage priorities renumbered: the new priority 2 is "not `yes`, but a suppressed candidate would have paired `yes`", and the old 2-5 shift to 3-6 (step 10).
  **The field turned out to be an attractor, and five guards exist because of it.** Measured on the six-paper regression set: the first v0.0.10 run moved 2 of 6 determinations `yes` -> `no` with no criterion edited — `10.1038/s41586-024-07571-1` (gluten-free diet, n=2 treated vs n=3 untreated, both sequenced) and `10.7554/elife.104978.2` (chemotherapy at diagnosis vs relapse), both filed as `incidental_clinical_therapy`, both with their `perturbations` array emptied outright, and in both every suppressed candidate marked `would_have_paired: "yes"`. The same records also enumerated a ROCK inhibitor in a wash buffer and a HypoThermosol hold, which this field's own threshold forbids. Eight named buckets plus a required field made suppression the salient action: making a path structured makes it more travelled. The guards, all in the reporting layer — (1) precedence, stated before anything else, that the field never shortens `perturbations`, with the gluten-free case named inline; (2) "zero entries is a normal, correct and common result"; (3) named negative examples for the ambient-Methods threshold, because the generic instruction was ignored twice in one paper; (4) `would_have_paired` held to Step 3's evidence standard rather than used as an emphasis marker; and (5) precedence inside the unintended-condition rule, since guards 1-4 restored `s41586` but left `elife` suppressed under `unintended_condition` instead — an applied therapy with an inferred effect is still applied. `pe.validate` additionally raises an issue when >=2 entries are all `would_have_paired: "yes"`, since prompt text alone demonstrably was not enough. Lesson for the next field: a single run cannot tell an attractor from variance, so the acceptance test is determination-only, against a preserved baseline, run twice, and it must include a paper that ought NOT to populate the new field.

- **v0.0.9: Four boundary rules that were silently doing work the curator never agreed to.** Measured on the 50-paper v0.0.8 run, where 8 of the 9 low-confidence positives came from exactly two of them. No schema change and **no determination-logic change** — Stage A, Stage B and the truth table are untouched, so `pe.validate`'s mirror and `tests/test_determination_v005.py` remain valid. `schema_version` stays `0.0.5`. What changed, all in Step 2 (what counts as a perturbation):
  1. **Clinical therapy now needs a per-sample tie.** v0.0.8 said therapy-then-sample is a perturbation with no qualifier about whether the study treated it as a variable. On 10.1016/j.cell.2019.06.029 the entire basis for a `yes` was one subordinate clause — "18 UC patients under different treatment regimens" — which is a statement of cohort heterogeneity, not of an applied condition; no drug is named, no patient-to-treatment mapping is in the text, and the study's own axis is healthy/non-inflamed/inflamed, an observational disease contrast this prompt already excludes. Two runs of the same paper under the same v0.0.8 prompt disagreed (`no` on 22 Aug, `yes` on 28 Aug), which is the same arbitration instability v0.0.7 fixed for the temperature rule. v0.0.9 requires the text to tie treatment to the profiled material.
  2. **Reporters, selection markers and epitope tags are now flatly NOT perturbations**, named in the NOT list rather than left as low-confidence candidates. v0.0.8 made a labelling-only edit a candidate, which is enough on its own to drive a paper to `unclear` under A5. On 10.1038/s44318-024-00328-6 that is exactly what happened: the SFTPC-GFP reporter — a promoter reporter, i.e. a readout of SFTPC expression rather than a manipulation of it — was reported with an unresolved pairing even though the scRNA-seq Methods never say whether the four sequenced lines carried any construct. Three of the nine low-confidence positives in the 50-paper run came from reporter or driver-reporter edits. The boundary is preserved in both directions: a locus-disrupting knock-in, a driver that excises a floxed functional allele, and functional cargo carried alongside a tag all remain perturbations.
  3. **Unintended conditions are not perturbations.** Same paper: the rebuttal letter admits some scRNA-seq lines "likely became somewhat hypoxic" from over-long culture. It is the only condition ever attached to the sequenced material, so it pairs trivially — but it was not applied, was inferred after the fact, has no arm, no dose and no duration.
  4. **A model's own derivation formulation is not a perturbation.** The v0.0.6 carve-out promoting "a named bioactive component within the medium" was written for add/withhold/substitute contrasts. Applied to a directed-differentiation cocktail given uniformly to every sample, it promotes the definition of the model system itself.
- **v0.0.8: Assay taxonomy re-grounded in the CELLxGENE assay vocabulary, and spatial assays now qualify. Curator scope ruling.** The qualifying list was previously hand-written and named ~20 platforms; it is now organised by platform family and covers the assay values actually present in the CELLxGENE corpus as supplied by the curator, adding ~25 that were missing entirely (BD Rhapsody, Parse Evercode, ScaleBio, SPLiT-seq, PIP-seq, GEXSCOPE, HIVE CLX, microwell-seq, TruDrop, Asteria, Quartz-seq, SCRB-seq, SORT-seq, STRT-seq, Fluidigm C1/SMARTer, DroNc-seq, 10x Flex variants) and, importantly, the single-cell epigenome assays the old list omitted altogether: snmC-seq/snmC-seq2/3, mCT-seq and snm3C-seq. The list is explicitly labelled indicative rather than a closed allowlist, since new platforms appear faster than the prompt is revised.**Spatial assays now qualify unconditionally.** Visium, Slide-seq/Slide-seqV2/Slide-tags/Slide-TCR-seq, Xenium, MERFISH, CosMx, seqFISH and STARmap all qualify, unconditionally — the previous "only if single-cell segmented / deconvolved" proviso is removed, and the explicit exclusion of spot-based spatial is deleted. Single-cell TCR/BCR (V(D)J) immune profiling and the protein-multimodal assays (CITE-seq, REAP-seq, ASAP-seq, TEA-seq) are named explicitly so they are not inferred. Rationale is deliberately recall-biased: if a perturbed sample was examined by any of these, curation wants the paper. This reverses a v0.0.3-era exclusion, so it is a widening, not a fix — expect papers to move `no`/`unclear` -> `yes` and none to move the other way. Measured on the 342 papers scored under v0.0.5-v0.0.7: **4 papers have a perturbation whose own readout assay is newly-qualifying and should flip** (`immuni.2022.09.002` Slide-TCR-seq; `isci.2022.104097` Slide-seqV2; `s41588-023-01435-6` Slide-seq V2 with a CRISPR Tbx6 knockout; `s41698-023-00488-4` Visium with neoadjuvant chemotherapy). That 4 is a FLOOR, not an estimate: those existing extractions were produced while these assays were disqualified, so the model had no reason to trace a pairing to them, and a re-run may find more. All three places that stated the old rule — the qualifying list, the NOT list, and the toggle table — are changed together; leaving one behind is what caused the v0.0.7 precedence bug. **What still does NOT qualify:** bulk RNA/ATAC/ChIP, microarray, qPCR, Western, ELISA, IHC/IF, flow cytometry, CyTOF, and bulk immune-repertoire (TCR/BCR) sequencing — only the SINGLE-CELL form of V(D)J profiling counts. No schema change; `schema_version` stays `0.0.5`.
- **v0.0.7: Resolve the precedence conflict v0.0.6 created between the temperature rule and the protocol rule.** v0.0.6 declared "varying cold-storage duration or preservation temperature" a technical variable, but left standing an older, earlier-listed rule reading "Heat shock, cold shock, hypoxia chamber applied as a condition = perturbation". Tissue held at 4 °C for 12/24/72 h satisfies both, and nothing said which wins. Observed in practice on `10.1186_s13059-019-1906-x`: two runs of the same paper under the same prompt version disagreed — one returned `no` (protocol rule) and one returned `yes` at confidence 0.5 (temperature rule), the second explicitly citing "the temperature-applied-as-a-condition rule" and the recall bias. The model was not ignoring v0.0.6; it was being asked to arbitrate a contradiction, and did so inconsistently. v0.0.7 states the precedence in both places and gives the disambiguating question: **what is the temperature FOR** — eliciting a biological response, or keeping the sample viable until it can be processed. Cold shock applied to induce a stress response is still a perturbation; a 4 °C hold before dissociation is not. **No schema change and no determination-logic change**; `schema_version` stays `0.0.5`.
- **v0.0.6: A sample-handling or culture PROTOCOL used as the studied variable is a technical variable, not a biological perturbation.** Curator ruling after reviewing the mechanism groups in the first 342 classified papers. v0.0.5's "NOT perturbations by themselves" list already named cryopreservation reagents and dissociation enzymes, but it qualified the whole list with "unless the paper makes the item the manipulated variable" — and in a pipeline-benchmarking paper the protocol *is* the manipulated variable, so the escape clause promoted exactly the cases the list meant to exclude. v0.0.6 closes that loop: when a paper benchmarks its own assay pipeline (comparing commercial media formulations against each other, varying preservation temperature or cold-storage duration, comparing dissociation enzyme cocktails, testing freeze-thaw, comparing passage number), the biological system is the instrument rather than the subject, and the manipulation is not reported in `perturbations`. **The line is the formulation *as such* versus a named bioactive component *within* it** — adding, withholding or dose-varying a specific factor stays a perturbation even when delivered via the medium. **No schema change and no change to the determination logic**: Stage A, Stage B, all seven consistency checks and every field are untouched, so v0.0.5 outputs remain valid and `pe.validate`'s logic is unaffected. The effect is purely on what enters the `perturbations` array. Measured on the 342 papers classified under v0.0.5: **10 perturbation instances are excluded and exactly 2 papers move `yes` -> `no`** (`10.1165_rcmb.2023-0356ma`, a culture-media comparison; `10.1186_s13059-019-1906-x`, cold-storage duration and dissociation enzymes). Re-run only those papers, or the corpus, to pick it up; `pe.compare` will show the delta.
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
| `task_version` | `{{TASK_VERSION}}` — ONE number for this pack, replacing the old `prompt_version` + `schema_version` pair. Declared in `task/task.yaml` and spliced in by `pe.prepare`, so no literal appears in this file and it cannot go stale. Bump it for any change to what a record should contain or how it is judged. |
| `pack_sha256` | computed by the harness over every rule-bearing file. `task_version` says the author thought something changed; this says whether anything did. |
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

If `processing_status` = "failed", return immediately with `perturbation_present` = "unclear", `paper_confidence` = 0.0, empty `perturbations` and `samples`, and a description of what you received in `ambiguities`. The 0.0 here is a **sentinel meaning "nothing was assessed"**, and it is exempt from the confidence rubric below — do not re-derive it from that rubric, which asks how likely a curator would agree with you and would therefore score an access-denied page near 1.0. Do NOT report "no": absence of retrievable text is not evidence of absence of perturbation.

## Step 1: Identify the single-cell/nucleus sequencing assay(s) used
Qualifying assays. The platform list below is the assay vocabulary actually present
in the CELLxGENE corpus (supplied by the curator), plus the multimodal and immune-
repertoire assays curation also wants. Treat it as indicative, not a closed
allowlist: **a close variant, a newer kit version, or a differently-branded
equivalent of anything here also qualifies.** New platforms appear constantly and
the list will always lag.

- **Droplet / microwell scRNA-seq:** 10x Chromium 3' (v1/v2/v3) and 5' (v1/v2);
  10x Flex Apex / GEM-X Flex v1 / Next GEM Flex v1 / Gene Expression Flex; Drop-seq; DroNc-seq;
  inDrop; Seq-Well and Seq-Well S3; BD Rhapsody (Whole Transcriptome Analysis and
  Targeted mRNA); GEXSCOPE; HIVE CLX; microwell-seq; TruDrop; Asteria scRNA-seq;
  particle-templated instant partition sequencing (PIP-seq). CELLxGENE also
  records generic values — "10x 3'/5' transcription profiling", "10x transcription
  profiling" — which qualify without a stated version
- **Plate-based / low-throughput scRNA-seq:** Smart-seq, Smart-seq2, Smart-seq3,
  Smart-seq v4; Fluidigm C1 with SMARTer library prep; CEL-seq, CEL-seq2; MARS-seq;
  Quartz-seq; SCRB-seq; SORT-seq; STRT-seq and modified STRT-seq
- **Combinatorial-indexing / split-pool:** sci-RNA-seq (incl. sci-RNA-seq3);
  SPLiT-seq; Parse Evercode Whole Transcriptome v2; ScaleBio single-cell RNA sequencing
- **Single-nucleus forms** of any of the above (snRNA-seq), including 10x snRNA-seq
- **Chromatin / epigenome:** scATAC-seq, snATAC-seq, 10x scATAC-seq, sci-ATAC-seq;
  single-cell/nucleus DNA methylation (snmC-seq, snmC-seq2/3, mCT-seq); single-nucleus
  chromatin conformation (snm3C-seq)
- **Multiome (joint modalities):** 10x Multiome (RNA+ATAC), SNARE-seq, SHARE-seq,
  ISSAAC-seq
- **Protein-multimodal:** CITE-seq, REAP-seq, ASAP-seq, TEA-seq
- **Immune repertoire:** single-cell TCR/BCR (V(D)J) profiling, e.g. 10x Chromium
  Single Cell Immune Profiling, whether run alone or alongside gene expression.
  **Bulk** repertoire sequencing does NOT qualify.
- **Spatial transcriptomics, in ALL its forms, whether or not the paper segments or
  deconvolves to single cells:**
  - imaging / subcellular resolution: MERFISH, seqFISH, Xenium, CosMx, STARmap
  - bead-based: Slide-seq, Slide-seqV2, Slide-tags, Slide-TCR-seq
  - spot-based: 10x Visium Spatial Gene Expression (including standard 55-micron),
    GeoMx
  A spot that pools several cells still qualifies. This is deliberate and
  recall-biased: if the perturbed material was examined by a spatial assay,
  curation wants the paper.
- **Perturbation screens with a single-cell readout:** Perturb-seq, CROP-seq,
  CRISP-seq, Mosaic-seq, sci-Plex. The perturbation and the single-cell assay are
  the same experiment by design, so the pairing is "yes" by construction.
- **Other:** Patch-seq (single-cell electrophysiology + RNA-seq); single-cell DNA-seq
  / single-cell whole-genome or exome sequencing

Explicitly NOT single-cell/nucleus sequencing (do not count these as qualifying, even if described near perturbation language):
- Bulk RNA-seq, bulk ATAC-seq, bulk ChIP-seq, CUT&RUN/CUT&Tag (bulk), bulk WGS/WES
- Bulk immune-repertoire sequencing (bulk TCR-seq / BCR-seq on pooled cells) — only the single-cell V(D)J form qualifies
- Microarray
- qPCR / RT-qPCR, digital PCR
- Western blot, ELISA, immunostaining/IHC/IF used as a protein readout
- Flow cytometry / FACS used as a readout (single-cell resolution but not a sequencing assay)
- Mass cytometry (CyTOF) (single-cell resolution but not a sequencing assay)
- Any assay performed on pooled/bulk lysate, even if the input cells were sorted into a defined population first (sorting a population, then pooling for extraction, is still bulk)

**Wording trap:** the phrase "single-cell suspension" almost always refers to a tissue/sample DISSOCIATION step (preparing cells for FACS sorting, loading onto a bulk assay, etc.), not to a single-cell sequencing assay. Do not treat "dissociated into a single-cell suspension" or "single-cell suspension for FACS" as evidence of scRNA-seq/snRNA-seq. Only count it if the text goes on to describe a qualifying single-cell/nucleus sequencing method being applied to that suspension.

If no qualifying single-cell/nucleus sequencing assay appears anywhere in the paper, set `has_single_cell_assay` to "no". **Do still complete Step 2.** `perturbation_present_any_assay` is defined over Step 2 REGARDLESS of assay, and `suppressed_candidates` is a required field, so a paper you stop here on cannot answer its own schema, which is what an earlier version of this instruction asked for. Step 3 then costs you nothing rather than being skipped: with no qualifying assay, every perturbation's `single_cell_paired` is "no" — a value each one must still carry, per CC-4 — and rule A2 decides the paper. If `processing_status` is "partial" or `text_completeness` is not "full", an assay may simply be missing from the text you were given, and the determination logic will cap the paper at "unclear" rather than "no".

## Step 2: Identify perturbations
Any deliberate manipulation applied to the samples/subjects, including:
- Chemical treatment (drugs, compounds, small molecules, agonists, antagonists, inhibitors, toxins, morphogens)
- Biologic treatment (cytokines, growth factors, ligands, recombinant proteins, functional antibodies)
- Activation or stimulation (e.g., TCR/BCR activation, LPS stimulation, receptor ligation)
- Genetic modification: knockout, knockdown, deletion, point mutation, overexpression, shRNA, RNAi/siRNA, CRISPR knockout, CRISPR activation (CRISPRa), CRISPR interference (CRISPRi), base/prime editing
- Physical / environmental conditions applied as a variable: temperature (heat shock, cold shock), pressure, hypoxia/anoxia, oxidative stress, irradiation, mechanical force, starvation
- Dietary intervention (special diet, fasting, supplementation)

### The core distinction you must make
Reagents used for ROUTINE SAMPLE PROCESSING or as a READOUT are NOT perturbations, even though they appear in Methods. A perturbation is a manipulation applied as an experimental variable to the biological system being studied. The same molecule can be a buffer in one paper and the studied perturbation in another. Judge the ROLE, not the mere presence of a word.

#### NOT perturbations by themselves
An item on this list becomes a perturbation only when the paper applies it as a **BIOLOGICAL** variable — to elicit a response in the system under study. It does **not** become one when it is the paper's **TECHNICAL** variable, the case where the pipeline itself is what is being characterised. That replaces the blanket "unless the paper makes the item the manipulated variable" this heading used to carry: in a pipeline-benchmarking paper the protocol *is* the manipulated variable, so the blanket clause promoted exactly the cases this list exists to exclude. The table under **Recording an exclusion** states, for each of the eight exclusion reasons, whether promotion can reach it at all — do not infer it from this heading.
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
- **Fluorescent and luminescent reporter transgenes** (GFP, EGFP, YFP, mCherry, RFP/TagRFP, tdTomato, luciferase) carried for tracking, sorting, lineage labelling, or to read out a promoter's activity. A promoter-reporter such as `SFTPC-GFP` is a READOUT of expression, not a manipulation of it.
- **Antibiotic-resistance and other selection markers** carried by a construct (puromycin/puroR, neomycin/G418/neoR, hygromycin/hygroR, blasticidin, zeocin), and the selection drug applied to maintain the line
- **Epitope and purification tags** used only for detection or pulldown (HA, FLAG, Myc, V5, His, GST) when the tagged protein is otherwise wild type

**Recording an exclusion from this list.** When one of these items was a genuine candidate — the paper puts it in a perturbing role and you decided it was not one — record it in `suppressed_candidates` rather than leaving the call invisible. The `rule` value comes from the eight below, and **this table is the only place their scopes are defined**: where a rule is described again later in this prompt, that description elaborates its entry here and never extends it to a case another rule owns.

| `rule` | what it covers | can a BIOLOGICAL-variable role promote it? |
|---|---|---|
| `routine_processing` | lysis and extraction reagents, wash buffers, fixation and permeabilization, blocking reagents, standard media and sera, sterility antibiotics, standard incubation and centrifugation, cryopreservation and dissociation, library and sequencing chemistry — in a paper that is **not** benchmarking them | **Yes.** Applied to elicit a response it is simply a perturbation and not an exclusion at all: DMSO as a differentiation agent, an antibiotic given as a drug, 4 °C applied as cold shock, a named medium component dose-varied against a comparator |
| `readout_reagent` | detection and staining antibodies and dyes used purely as a readout, and delivery reagents carrying no functional cargo. A reporter construct itself belongs to `reporter_or_marker`, which takes precedence | **Yes**, by role: an antibody used to block, neutralize, deplete or activate is a perturbation. A paper *benchmarking* its own readout is `sample_handling_protocol`, not a promotion |
| `sample_handling_protocol` | the paper's studied variable IS a handling or culture protocol — media brand against media brand, cold-storage duration or preservation temperature, dissociation enzyme cocktails, freeze-thaw, passage number, ECM substrate as a handling choice | **No.** This rule *is* the technical-variable case, so it owns every benchmarking paper of this shape. A named bioactive component *within* a formulation still counts — see that rule's first sub-bullet |
| `reporter_or_marker` | fluorescent and luminescent reporter transgenes, antibiotic-resistance and other selection markers, epitope and purification tags | **No** — not by the paper's interest in it. What promotes is what the construct DOES to the cell: a locus-disrupting knock-in, or functional cargo alongside the tag |
| `derivation_formulation` | a model system's defining derivation or maintenance formulation, including a directed-differentiation cocktail | **No** — not by the paper's interest in it (curator ruling, 2026-08-28). What promotes is identity versus factor: a named factor varied while cell identity is held fixed |
| `observational_disease_state` | a naturally occurring disease state or donor genotype, with no bench manipulation | **No** — nothing was applied, so there is nothing to promote |
| `unintended_condition` | a condition the paper describes as unintended, accidental, or inferred after the fact | **No** — a condition the investigators made their variable is by definition not unintended |
| `incidental_clinical_therapy` | a therapy applied to patients that the paper treats as its SETTING rather than its VARIABLE, and treatment recorded only as incidental cohort metadata | **Yes**, and this is the one rule whose promotion test is the paper's own treatment of the item: THE GOVERNING QUESTION below |

The threshold is a call you actually had to make: do not enumerate every reagent the Methods happen to name. **These are NOT suppressed candidates in an ordinary paper** — a ROCK inhibitor (Y-27632) in a dissociation or wash buffer, a preservation solution or a 4 °C hold before processing (HypoThermosol, cold ischaemic time), DMSO used only for freezing, a dissociation enzyme, pen/strep, a library-prep kit. In an ordinary paper they are simply not candidates, and listing them buries the one suppression that mattered.

### Rules for tricky cases
- Antibody: readout/detection (staining) = NOT a perturbation. Functional use (blocking, neutralizing, depleting, activating, agonist) = perturbation.
- Temperature / oxygen: standard culture incubation = NOT. Heat shock, cold shock, hypoxia chamber applied as a condition **in order to elicit a biological response** = perturbation. **Temperature or hold-time applied as a PRESERVATION or TRANSPORT parameter is NOT** — see the sample-handling/protocol rule below, which takes precedence over this line.
- Genetic modification: a FUNCTIONAL edit (KO/KD/overexpression/CRISPRa/CRISPRi/shRNA/RNAi/functional point mutation) = perturbation. A pure LABELING/reporter edit — a fluorescent or luminescent reporter, a selection marker, or an epitope tag, introduced only so cells can be found, sorted, selected or measured — is **NOT a perturbation**. Do not list it in `perturbations`; record it in `suppressed_candidates` with `rule: reporter_or_marker` so the call stays auditable and countable. This holds whether the reporter is a knock-in, a lentiviral transgene, or a transgenic allele, and whether or not the text says the profiled samples carried it: silence about a transgene is not evidence of one, and asserting a candidate that the Methods never place in the sequenced material has repeatedly pushed whole papers to "unclear" under A5 on nothing the text actually says. [TOGGLE: if the curator wants all genetic modifications counted, treat reporter edits as full perturbations.]
  - **Where the line falls.** The question is what the construct DOES to the cell, not how it was delivered. Still perturbations: a reporter knock-in that disrupts or replaces the endogenous locus (a knock-in null / gene-trap); a driver line whose recombinase excises a floxed functional allele (Cre x flox-KO), as distinct from the same driver crossed only to a Cre-dependent fluorophore for lineage tracing; a construct carrying functional cargo alongside the tag (an HA-tagged pathogenic point mutant is a perturbation because of the mutation, not the tag). A Cre-driver plus a Cre-dependent fluorescent reporter, with no floxed functional allele in the cross, is labelling.
- Cell/animal model where the engineering is the studied point (e.g., an oncogene-transformed line, a transgenic disease model) = perturbation. A generic unmodified line (HeLa, HEK293) with no manipulation = NOT.
- Naturally occurring disease state or genotype in patient/donor samples with NO experimental manipulation (tumor vs adjacent normal, a donor carrying a variant) = NOT an experimental perturbation; record it in `suppressed_candidates` with `rule: observational_disease_state`. [TOGGLE: include if the curator wants observational disease contrasts flagged.]
- Selection antibiotics (puromycin, G418) maintaining a stable line = the selection is NOT the perturbation; the introduced construct may be.
- **Sample-handling or culture protocol as the studied variable = a TECHNICAL variable, NOT a biological perturbation.** This is the TECHNICAL-variable case the heading above names, so promotion cannot reach it and the table above marks it **No** for exactly that reason. When the paper is benchmarking its own pipeline — comparing named commercial media against each other (e.g. PneumaCult-ALI vs BEGM-ALI vs Clancy), varying cold-storage duration or preservation temperature, comparing dissociation enzyme cocktails, testing freeze-thaw, comparing passage number, or comparing ECM substrate as a handling choice — the biological system is the instrument being characterised, not the subject being perturbed. Do NOT list these in `perturbations`; record each in `suppressed_candidates` with `rule: sample_handling_protocol` so the decision stays auditable and countable. [TOGGLE: report them as perturbations if protocol-benchmarking studies are in scope for curation.]
  - **The line is the formulation *as such* versus a named bioactive component *within* it.** Adding, withholding, substituting or dose-varying a specific factor IS a perturbation even when it is delivered through the medium. All of these remain perturbations: "EVTM1 +NRG1 vs EVTM2 -NRG1", "varying EGF concentration in the culture medium", "WNT3A-conditioned medium, withdrawal vs inclusion", "recombinant WNT2B substituted for WNT3A", "retinoic acid 1 uM". Only the protocol-versus-protocol comparison is excluded: "PneumaCult-ALI vs BEGM-ALI" is not a perturbation.
  - **Precedence.** This rule OVERRIDES the temperature/oxygen line above whenever the temperature or duration is a preservation, storage or transport parameter rather than an applied biological stress. The disambiguating question is what the temperature is FOR: eliciting a biological response, or keeping the sample viable until it can be processed. "Cold shock at 4 °C for 30 min to induce a cold-stress response" is a perturbation. "Tissue held at 4 °C in preservation solution for 12, 24 or 72 h before dissociation" is a preservation protocol and is not, even though both are 4 °C and both are deliberate. The same applies to hypoxia: an anoxic chamber applied as an experimental condition is a perturbation, while cold ischemic time accrued during organ transport is not.
  - A protocol variable does not become biological just because it has a biological readout. A paper that measures dissociation-induced stress genes (FOS, JUN) to show that its dissociation method perturbs the transcriptome is still characterising the method.
- Transfection/transduction: delivering functional cargo (shRNA, ORF for overexpression, sgRNA) = perturbation. Delivering only a reporter or empty/control vector = NOT.
- **A condition the paper describes as unintended, accidental, or inferred after the fact is NOT a perturbation**, even when it demonstrably affected the profiled samples and therefore pairs trivially to the assay. The test is whether the investigators APPLIED it as a condition. Hedged, retrospective language is the tell: "we now understand they likely became", "may have been", "appears to have inadvertently". Such a condition has no arm to compare against, and typically no dose, duration or level on record. Do not list it in `perturbations`; record it in `suppressed_candidates` with `rule: unintended_condition` — it is real information about sample quality even though it is not an experimental variable, and it typically pairs trivially, so `would_have_paired` will often be "yes".
  - **Precedence against the clinical-therapy rule: what must be hedged is the condition's own EXISTENCE or APPLICATION, not its effect.** "We now understand the cultures likely became somewhat hypoxic" hedges whether the condition happened at all — that is an unintended condition and stays suppressed. A patient who received chemotherapy between two sequenced timepoints was *definitely* treated, and treated deliberately, so the therapy is not "unintended" and **this rule does not reach it**. **An inferred CAUSE does not make an applied condition unintended.** Whether that therapy is a perturbation is then decided by the clinical-therapy rules below — **not here**. v0.0.10 had this bullet end by forcing the report ("where two sequenced timepoints straddle the therapy... report it as a chemical perturbation, do not suppress it"), which collided with the incidental rule's "any one signal is enough" and left the case unarbitrated; that is the v0.0.7 precedence-bug shape, and it is removed. The disambiguating question for THIS rule is only whether you could say who applied the condition and when — if you can, it was applied, however hedged the paper is about what it did. [TOGGLE: count inadvertent conditions if the curator wants any non-baseline state of the profiled cells flagged.]
- **The defining derivation or maintenance formulation of a model system is NOT a perturbation**, even when it is a directed-differentiation cocktail of named bioactive factors, even when the paper says it drives a fate change, and — new in v0.0.11 — **even when an undifferentiated arm was sequenced alongside it**. v0.0.9 conditioned this rule on the formulation being "given uniformly to every sample", and that precondition inverted the call on `10.1016/j.stem.2022.11.013`, where LinPOS organoids in self-renewing medium and alveolar organoids in AT2 differentiation medium were both sequenced; the curator's ruling (2026-08-28) is that this is "a different target cell type, not perturbation... I don't really care what has been applied to get to the final diff cell type." **The line is what the manipulation is FOR: producing a different target cell IDENTITY is the model; varying a named factor while the identity is held fixed is a perturbation.** "fdAT2 organoids maintained in AT2 differentiation medium (dexamethasone, cAMP, IBMX, DAPT, FGF7, CHIR99021, A83-01, Y-27632)" is the model, and so is "LinPOS tip organoids differentiated for 7 days into alveolar organoids", because the endpoint is a new cell type. "The same alveolar organoids with FGF7 withdrawn versus control" is a perturbation, because the identity stays put and one named factor moves. The v0.0.6 carve-out is therefore intact and unchanged — it was always about a contrast in a factor, never about a contrast in cell identity. Do not list the formulation in `perturbations`; record it in `suppressed_candidates` with `rule: derivation_formulation`, because a reader who counted the cocktail would have found a perturbation here and the record must show that you did not. [TOGGLE: count directed differentiation as a perturbation if the curator wants any applied cocktail on profiled material flagged.]
- **Peer-review correspondence is a secondary source.** Referee reports, author rebuttal letters and editorial checklists (often shipped as a "Review Process File") describe the study but are not its Methods. Evidence found only there may inform `ambiguities` and lower confidence; it must not be the sole basis for asserting a perturbation or a pairing. Quote it with its own `source_id` when you use it.
- Vehicle-only / untreated CONTROL samples: mark those samples as perturbed=false, but their presence indicates the experiment contains a perturbation. Report the perturbation for the treated arm.
- **A drug administered to patients as therapy, then sampled = a chemical perturbation (report it), even in a clinical study — but only when ALL THREE of the following hold.** (i) The sampled material is what went into the single-cell/nucleus assay (see Step 3). (ii) The text ties the treatment to the profiled material well enough to name it: either the treated cohort is described as uniformly receiving a named therapy, or specific treatments are recorded for specific sequenced samples, or treatment is used as a grouping/analysis variable for the single-cell data. (iii) **The paper treats the therapy as its VARIABLE rather than its SETTING** — the governing question two bullets below, added in v0.0.11 because (i) and (ii) alone returned the wrong answer on `10.1038/s41467-025-65049-8`. A treatment recorded ONLY as incidental cohort metadata does not qualify — see the next bullet.
- **Incidental treatment heterogeneity in a patient cohort is NOT a perturbation.** A clause noting that subjects were "on various medications", "under different treatment regimens", "receiving standard of care", or "variably treated" is a statement about how heterogeneous the cohort is, not about a condition applied as part of the study. Treat it the way you treat a naturally occurring disease state: do not list it in `perturbations`, record it in `suppressed_candidates` with `rule: incidental_clinical_therapy`, and say plainly in `why` whether the per-sample treatment detail exists in the text you were given or lives in a table you cannot see. Three signals that it is incidental rather than applied, any one of which is enough: no agent is named for the profiled cohort; no sample-to-treatment mapping appears in the text; the paper's own comparison axis is something else (disease vs healthy, inflamed vs uninflamed, responder vs non-responder scored from EXTERNAL data). **When a named therapy IS tied to specific sequenced samples, none of these three signals settles the case by itself — the governing question in the next bullet does.** [TOGGLE: count incidental clinical therapy as a perturbation if the curator wants any treated donor material flagged regardless of study design.]
- **THE GOVERNING QUESTION for an applied clinical therapy: is it the study's VARIABLE, or its SETTING?** (v0.0.11, curator ruling 2026-08-28.) Two sequenced timepoints straddling a therapy is NOT sufficient on its own; that structural test is what v0.0.10 used, and it returned the wrong answer on `10.1038/s41467-025-65049-8`. Ask instead what the paper does with the difference between the timepoints. **Attributed to the treatment = VARIABLE = report it as a perturbation.** `10.7554/elife.104978.2` states that "at relapse the inferred-prenatal origin patient undergoes a chemotherapy-driven lineage switch to a lymphoid phenotype" — the paper's own claim is that the drug changed the cells, so the drug is what is being characterised. **Used to reveal a difference that was already there = SETTING = suppress it.** In `10.1038/s41467-025-65049-8` the ZBTB16+ blast population is present at diagnosis (0.67% of blasts at day 0) and dominant after induction (97.6% at day 28), the cohort axis is responder vs non-responder across 58 children, and the payload is a **day-0** biomarker for refractoriness — the therapy is the sieve that exposes an intrinsic property, not the subject of study. Record that case in `suppressed_candidates` with `rule: incidental_clinical_therapy` and say in `why` which way the paper attributes the difference. This is the same move v0.0.7 made for temperature ("what is the temperature FOR") and that the bullet above makes for differentiation ("what is the cocktail FOR"). Note the cost, so it is chosen and not stumbled into: this is an INTENT test replacing a structural one, and intent is less reproducible — if two runs of one paper disagree here, that is this rule, and it is the failure mode v0.0.7 and v0.0.9 were both written for. [TOGGLE: count any therapy applied to profiled material as a perturbation regardless of the study's axis.]

## Step 3: Pair perturbations to the single-cell/nucleus assay
For EACH perturbation identified in Step 2, determine whether the sample(s) it was applied to are the same sample(s) that went into a qualifying single-cell/nucleus assay from Step 1. Classify each perturbation's `single_cell_paired` as:
- **"yes"** — the text explicitly links the perturbed sample/group to a qualifying single-cell/nucleus assay (e.g., "PBMCs were stimulated with LPS for 4h and then processed for 10x scRNA-seq"; "tumors from Brca1-deleted mice underwent snRNA-seq"). A Perturb-seq/CROP-seq-type screen is always "yes" by construction.
- **"no"** — the text indicates the perturbed sample was assayed by something other than a qualifying single-cell/nucleus method (bulk RNA-seq, qPCR, Western, flow, ELISA, functional/behavioral assay, histology), OR the single-cell/nucleus assay in the paper is explicitly performed on a different, unperturbed sample set.
- **"unclear"** — a qualifying single-cell/nucleus assay exists in the paper and a perturbation exists in the paper, but the text does not make clear whether they were applied to the same sample/group (e.g., separate figures/sections that never state whether the scRNA-seq cohort included treated samples).

Do this per perturbation, and per sample group in the "samples" array (add `assay` and `is_single_cell_assay` there too — see schema).

### Organism of the paired material

**Record the organism. Never call on it.** `paired_organism` on each perturbation
and `organism` on each sample group are descriptive fields for a human
interpreter. They are stated FIRST because the precedence matters more than the
field: recording an organism **must not** remove a perturbation from
`perturbations`, must not change any `single_cell_paired` value, and must not
change `perturbation_present`. A mouse perturbation paired to mouse scRNA-seq is
still `single_cell_paired: "yes"` and still makes the paper `perturbation_present:
"yes"`. If filling these fields shortened your `perturbations` array or flipped a
pairing, you have made an error, not a finer distinction. The curation scope is
applied downstream, by a person, not here — the corpus is human-primarily but
**not** human-only, and a mouse, zebrafish or killifish dataset can be a
legitimate curation target.

- **Use the paper's own word**, lowercased: `"human"`, `"mouse"`, `"rat"`,
  `"zebrafish"`, `"killifish"`, `"macaque"`, `"pig"`. This is an OPEN value, not a
  closed set — new model organisms appear faster than this prompt is revised, so
  do not force an unusual species into a nearby bucket.
- **`null` is a legitimate and common answer, and must not be guessed.** If the
  text does not state the organism for that specific material, the value is
  `null`. Do NOT infer it from the journal, the authors, the cell-line name, or
  the fact that most papers are human. An unstated organism is unstated. Do not
  write `"human"` because a study is clinical unless the text says so for the
  material in question.
- **One value per perturbation, not per paper.** A paper with human patient
  scRNA-seq and a mouse model has TWO pairings with two different
  `paired_organism` values, and both must be reported. Collapsing them to whichever
  you noticed first is the specific failure this field exists to prevent.
- **Where the paper states a line's species, that is the organism of the LINE**,
  not of the lab: a human cell line is `"human"`, a murine line is `"mouse"`.
  Where it names only a line and never its species, the bullet above governs and
  the value is `null` — recognising `K562` as human is exactly the inference that
  bullet forbids. This line settles WHICH organism when two are in play, not
  whether to supply one the text withheld. A human xenograft in a mouse host is
  the organism of the PROFILED cells — human cells implanted into a mouse are
  `"human"`; the mouse stroma sequenced alongside is `"mouse"`. Say which in
  `reasoning` when the text makes this ambiguous.
- No quote is required for this field. It is a descriptive attribute rather than a
  claim about whether a perturbation exists, so it does not carry its own
  `evidence_quote`. Reflect genuine uncertainty by using `null`, not by guessing. Different perturbations in the same paper can have different pairings (e.g., a genetic knockout validated by scRNA-seq = paired; a separate pharmacologic rescue experiment in the same paper validated only by qPCR = not paired).

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
5. "Tumor and adjacent normal tissue were profiled by scRNA-seq. Patients had received neoadjuvant chemotherapy prior to resection. We compared the malignant and non-malignant compartments to define the tumor microenvironment." → (i) and (ii) hold: the sequenced tumor tissue is itself the post-treatment sample, and the cohort is described as uniformly pre-treated with a named modality. **(iii) fails.** The paper's comparison axis is tumor versus adjacent normal and it attributes nothing to the chemotherapy, so the therapy is this study's SETTING — it is the state the tissue was in, not the thing being characterised. Not listed in `perturbations`; one `suppressed_candidates` entry, `rule: incidental_clinical_therapy`, `would_have_paired: "yes"`, and `why` recording that the paper attributes the difference to the compartment rather than to the treatment. Paper → **"no"** on this evidence alone. **Change one fact and it flips:** had the same paper profiled paired biopsies before and after the chemotherapy and called the shift chemotherapy-driven, (iii) would hold, the chemotherapy would be a perturbation with **single_cell_paired = yes**, and the paper would be **"yes"**. The two versions differ in nothing but attribution, which is exactly what the governing question tests.
6. "We generated 366,650 single-cell transcriptomes from biopsies of 18 UC patients under different treatment regimens and 12 healthy individuals. Clinical metadata are in Table S1." → no agent is named, no sample-to-treatment mapping is in the text, and the analysis axis is healthy vs non-inflamed vs inflamed → incidental treatment heterogeneity, **not listed in `perturbations`**; one `suppressed_candidates` entry with `rule: incidental_clinical_therapy`, `would_have_paired: "yes"` (the profiled cohort IS the treated cohort), and `why` noting that per-patient treatment lives in a table not present in the supplied text. (Same answer as 5, reached one criterion earlier: there a named modality IS tied to the cohort, so (i) and (ii) hold and (iii) is what excludes it; here (ii) already fails.)
7. "Four organoid lines were harvested and profiled by 10x scRNA-seq to characterize the model. Elsewhere the paper generates SFTPC-GFP reporter lines and performs CRISPRi knockdowns read out by qPCR and flow." → the Methods never state whether the four sequenced lines carried the reporter or the CRISPRi machinery → the reporter is **not a perturbation** (silence, not presence); the CRISPRi arms are perturbations with **single_cell_paired = no**; paper → **"no"**. The reporter still gets a `suppressed_candidates` entry — `rule: reporter_or_marker`, `evidence_quote: null` because the exclusion rests on the absence of a statement, `why` saying the Methods never place the construct in the sequenced material, and `would_have_paired: "unclear"`. "Not a perturbation" and "not in the record" are different claims, and only the first is correct here.

Every example above assumes `processing_status` = "ok" and `text_completeness` = "full" — no count is given here on purpose, because the last one went stale. On degraded text, examples 2, 5 and 7 would be capped at "unclear" by Stage B rather than resolving to "no".

## Resolution
- Give a paper-level determination.
- If the paper describes distinct sample groups / conditions / arms, enumerate them in "samples" using the labels the paper uses, and mark each perturbed true/false/unclear, linking to the relevant perturbation(s), and record the assay used for that group. If groups cannot be resolved, leave "samples" empty and report at paper level only.

## Confidence rubric
`paper_confidence` and each perturbation's `confidence` answer ONE question: how likely is it that a careful human curator, reading this same text, would assign the value you assigned? This definition holds for all three values of `perturbation_present`, so a well-evidenced "no" scores high and a coin-flip "yes" scores low. It is NOT a probability that the paper is perturbed.

- **0.80-1.0 (high).** For "yes": explicit statement that a treatment/modification was applied as a condition, with a clear agent and target, AND an explicit statement that the SAME sample/group was profiled by a qualifying single-cell/nucleus assay (or the perturbation is itself a single-cell screen, e.g. Perturb-seq). For "no": complete text in which either no qualifying assay appears, or every perturbed group is explicitly assigned to a non-qualifying readout, with quotes for both halves.
- **0.40-0.79 (medium).** Perturbation-like language and a qualifying assay both exist, but the pairing between the specific perturbed group and the single-cell/nucleus assay is inferred rather than explicitly stated, OR the perturbation's role is itself ambiguous (could be processing/readout). For "no": the negative rests on absence of a statement rather than on an explicit contrary statement.
- **0.20-0.39 (low).** Weak or indirect signal on either the perturbation or the pairing, included only because recall is prioritized. A **"no" or "unclear"** determination made on `processing_status` = "partial" text belongs here or lower.
- **The partial-text ceiling does not apply to a "yes"**, and the asymmetry is the same one Stage B is built on: missing text can hide the sentence that would have paired a perturbation, but it cannot invent one. So a positive drawn from the text you *do* have is scored on that evidence by the two bands above. A ceiling over positives too would route every one of them into the low-confidence review queue by rubric rather than by any judgment about the paper.
- **For "unclear" determinations**, confidence expresses how confident you are that the pairing is genuinely unresolvable from this text, not how likely a hidden "yes" is. A high-confidence "unclear" means the text demonstrably never states the pairing; a low-confidence "unclear" means you may simply have failed to find where it does.
- **Exempt: a failed extraction.** `processing_status` = "failed" carries `paper_confidence` = 0.0 from Step 0. That is a sentinel meaning "nothing was assessed", not a score from this rubric — which, read literally, would put a failed extraction near 1.0, since any curator would agree that an access-denied page settles nothing.

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
Return ONLY a single JSON object, no prose, no markdown fences, matching the schema below. Echo `task_version` as "{{TASK_VERSION}}".

PAPER_ID: {{PAPER_ID}}

SOURCE_IDS: {{SOURCE_IDS}}

PAPER_TEXT:
{{PAPER_TEXT}}
```

---

## Output schema

```json
{
  "task_version": "{{TASK_VERSION}}",
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
      "paired_organism": "organism of the material this pairing refers to, as the paper names it, or null if the text does not say",
      "assay_evidence": {"source_id": "main", "quote": "verbatim span tying this perturbation's sample to the assay"},
      "confidence": 0.0,
      "reasoning": "one sentence: why this is a perturbation and not routine processing/readout, AND why it is/isn't paired with a single-cell/nucleus assay"
    }
  ],
  "samples": [
    {
      "label": "group/condition label as named in the paper",
      "organism": "organism of this sample group as the paper names it (e.g. \"human\", \"mouse\", \"zebrafish\", \"killifish\"), or null if the text does not say",
      "perturbed": "true | false | \"unclear\"",
      "perturbation_refs": [0],
      "assay": "assay used for this sample group, else empty string",
      "is_single_cell_assay": "yes | no | unclear"
    }
  ],
  "suppressed_candidates": [
    {
      "candidate": "what you recognised, named as the paper names it (e.g. 'lentiviral SFTPC-promoter-GFP + EF1a-TagRFP reporter')",
      "rule": "reporter_or_marker | incidental_clinical_therapy | unintended_condition | derivation_formulation | observational_disease_state | sample_handling_protocol | readout_reagent | routine_processing",
      "why": "one sentence: which NOT-list rule excluded it, and the fact in the text that put it there",
      "evidence_quote": {"source_id": "main", "quote": "verbatim span"},
      "would_have_paired": "yes | no | unclear"
    }
  ],
  "ambiguities": "free text: contradictions resolved, unresolved sample groups, unresolved assay-perturbation pairing, degraded-text notes, or empty string. Boundary calls that EXCLUDED a candidate belong in `suppressed_candidates`, not here."
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
- `suppressed_candidates` is the audit trail for the NOT list, and it is REQUIRED: use `[]`, never `null`, when nothing was suppressed. One entry per item you recognised as a possible perturbation and deliberately did not list. It exists so a curator can tell "considered and excluded under rule X" apart from "never noticed", and so the corpus can be asked how many papers each rule held back — neither of which a free-text `ambiguities` string can answer.
- **`suppressed_candidates` never takes anything away from `perturbations`.** Decide first whether the item IS a perturbation, on Step 2's report rules. It becomes a suppressed candidate only if it satisfies a NOT rule **and no report rule**. Report rules win: a named therapy tied to specific sequenced samples **that the paper treats as its variable** (the clinical-therapy rule's condition (iii), not (i) and (ii) alone — a therapy tied to sequenced samples that the paper treats as its SETTING is suppressed, and that is the case worked example 5 turns on), a named bioactive factor added or withheld against a comparator, a functional edit, a named dietary intervention with a comparator arm — all of these are perturbations, and the existence of a tidy `rule` value for something adjacent is not a reason to reclassify them. **Filling in this field must not shorten your `perturbations` array.** If it did, you have made an error, not a finer distinction. Concretely: "untreated patients with coeliac disease (n = 3) and treated patients on a gluten-free diet (n = 2)", both sequenced, is a **dietary perturbation with a per-sample tie** — it is not incidental cohort metadata, because the agent is named, the counts are per-arm, and an untreated comparator exists. Incidental means what that rule's own test says it means — do not read a threshold off this line, which used to say "the three signals are met" while the rule itself says any one of them is enough and that none of them settles a named, tied therapy at all. It does not mean merely that a treatment is mentioned.
- **Zero suppressed candidates is a normal, correct and common result.** This is not a quota, a checklist, or a section to fill. An empty array is the right answer for most papers, and a long list is a warning sign about the reader rather than the paper.
- **The threshold for an entry is deliberation, not presence.** Record what you actually had to rule out: a construct, therapy, condition, formulation or reagent that a careful reader could have taken for this paper's perturbation. Do NOT enumerate the ambient Methods — the PBS washes, the library-prep kit, the trypsin. Those become entries only when the paper puts them in a perturbing role and you decided they were not one (a study comparing dissociation enzymes, where the enzyme IS the studied variable, is a real call and belongs here).
- `rule` is a **closed set** of exactly the eight values listed. An open string cannot be tallied, which is the whole point of the field. If a candidate seems to need a ninth value, choose the closest and explain in `why`.
- `would_have_paired` is the pairing this candidate WOULD have received under Step 3 had it been listed — the same yes/no/unclear judgment, held to the **same evidence standard**, not a marker for how notable the candidate feels. If the text does not tie the candidate to the material that went into the single-cell assay, the answer is "unclear" or "no". It is the highest-value column in the record: a suppressed candidate that would have paired "yes" sits one toggle away from flipping the paper to "yes", so those rows triage near the top — step 10 priority 2, which admits only the four rules whose boundary is still under review, because `observational_disease_state` pairs "yes" on any disease-vs-healthy contrast and a tier holding most papers is not a queue. Which is exactly why it must be earned — a record whose every suppressed candidate is "yes" has stopped discriminating, and the column is worthless the moment it becomes automatic.
- `evidence_quote` is verified verbatim like every other quote. An unverifiable quote is dropped and **the entry keeps its place** — the suppression still happened, and a quoteless entry is more honest than no entry. Use `null` when the exclusion rests on the ABSENCE of a statement (the Methods never say the sequenced lines carried the construct): there is nothing to quote, and `why` should say so.
- **A suppressed candidate is not a perturbation.** It never enters `perturbations`, never receives a `perturbation_refs` index, and cannot affect Stage A, Stage B or `perturbation_present_any_assay`. If recording one changes a determination, that is a bug in the harness, not a judgment call.
- `sources_seen` should echo the ids in the `<<<SOURCE>>>` markers. A mismatch against the manifest means the assembly step dropped a file.
- Run metadata (`model_id`, `pack_sha256`, timestamps, token counts, source checksums) is added by the harness, not by the model. `task_version` is the one version value, and the model echoes it because the record must be readable on its own. See the JSONL record in the batch spec.

**Added in v0.0.12:** `paired_organism` on each perturbation and `organism` on each sample group, both OPEN strings and both nullable. Nothing else changed, and no determination field moved — the fields are recorded and deliberately not acted on, so a consumer written against the previous record shape reads one of these correctly by ignoring the new keys.

**Added in v0.0.10:** `suppressed_candidates`, a required field (empty array when nothing was suppressed). Nothing else changed, and no determination field moved — again, a consumer that ignores the new key still reads the record correctly.

**Breaking changes from v0.0.4:** `evidence_quotes` is now an array of objects rather than an array of strings; `assay_evidence_quote` (string) is replaced by `assay_evidence` (object or null); `sources_seen`, `processing_status`, `text_completeness`, `unresolved_reason`, `consistency_flags` and a version field are new and required — the version field was `schema_version` until 0.0.13 replaced it with `task_version`.

---

## Toggle decisions to set before the full run

| Case | Default in prompt | Alternative |
|---|---|---|
| Reporter / labeling-only genetic edits — fluorescent reporters (GFP/RFP/luciferase), antibiotic-resistance and selection markers, epitope tags | **Not a perturbation**; recorded in `suppressed_candidates`, `rule: reporter_or_marker` (v0.0.10). Functional cargo alongside the tag, a locus-disrupting knock-in, or a driver excising a floxed allele still count (v0.0.9) | Count as full perturbation |
| Clinical therapy recorded only as incidental cohort metadata ("patients under different treatment regimens") | Not a perturbation; recorded in `suppressed_candidates`, `rule: incidental_clinical_therapy` (v0.0.10) | Count any treated donor material as perturbed |
| Unintended / retrospectively inferred condition affecting the profiled samples (e.g., cultures that "likely became hypoxic") | Not a perturbation; recorded in `suppressed_candidates`, `rule: unintended_condition` (v0.0.10) | Count any non-baseline state of the profiled cells |
| A model's defining derivation / directed-differentiation formulation — **whether or not an undifferentiated arm was also sequenced** | Not a perturbation; producing a different target cell IDENTITY is the model. Only a named factor varied while identity is held fixed counts (v0.0.11) | Count directed differentiation as a perturbation |
| Clinical therapy the paper uses to REVEAL a pre-existing difference rather than to CAUSE one (e.g. a day-0 biomarker for treatment refractoriness) | Not a perturbation; the therapy is the study's setting, not its variable. Suppressed under `rule: incidental_clinical_therapy` (v0.0.11) | Count any therapy applied to profiled material as a perturbation |
| Evidence found only in peer-review correspondence (referee reports, rebuttal letters) | Secondary source: may inform `ambiguities` and confidence, cannot alone establish a perturbation or pairing (v0.0.9) | Treat as equivalent to Methods |
| Naturally occurring disease state / donor genotype, no bench manipulation | Not a perturbation; recorded in `suppressed_candidates`, `rule: observational_disease_state` (v0.0.10) | Flag as perturbation of interest |
| Selection antibiotics maintaining a stable line | Not the perturbation; the construct **may** be, and is one only if it carries functional cargo — a marker-only construct is `reporter_or_marker` (v0.0.9) | - |
| Sample-handling / culture protocol as the studied variable (media brand comparison, storage duration, dissociation enzyme, freeze-thaw, passage number) | Technical variable; not reported in `perturbations`, recorded in `suppressed_candidates`, `rule: sample_handling_protocol` (v0.0.10) | Report as a perturbation if protocol-benchmarking studies are in scope |
| Perturbation validated only by bulk RNA-seq/qPCR/Western while a separate cohort is used for single-cell/nucleus sequencing | `single_cell_paired = no`; `perturbation_present = no` for that perturbation | Loosen to allow paper-level co-occurrence (not recommended, this is the behavior fixed in v0.0.3) |
| Spatial transcriptomics of any resolution (Visium, Slide-seq, Xenium, MERFISH, CosMx) | **Qualifies** as a single-cell assay, segmentation not required (v0.0.8) | Require single-cell segmentation/deconvolution, as in v0.0.3-v0.0.7 |
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
- **A paper that was never retrieved must never be silently absent from the output**, because absence is indistinguishable from a negative in any downstream count. The harness honours that by keeping it *visible as unscored* rather than by synthesizing a record: it stays in the manifest, `pe.pending` names it and why, and it appears in the summary with an open-string `status` and `triage_priority` 0. No model-free `processing_status = "failed"` record is written — a determination-shaped row for a paper nobody assessed is the thing this rule is against, not the way to satisfy it.
- `extractor = "ocr"` should set `processing_status` no better than "partial" by default.

### 2. Text assembly
Concatenate sources with `<<<SOURCE id=... type=...>>>` markers as described in **How to use**. Persist the exact assembled string (or its sha256 plus the ordered source list) so quote verification and any later re-verification compare against the same bytes.

### 3. Truncation policy
If the assembled text exceeds the context budget, drop content in this fixed order and record the rung reached in the run's `truncation` block. **Do not set `text_completeness` here:** that field is the model's call in Step 0, on the text it was actually given, and having two owners would make it unreadable. A harness-applied truncation is a fact the model cannot see, so it is recorded beside the record rather than written into it (`pe.prepare.sources_within_budget`).
1. Reference list (should already be stripped).
2. Author contributions, funding, competing interests, data availability.
3. Discussion.
4. Introduction / background.
5. Supplementary sources beyond the first, longest one.

Never drop Methods, Results, figure legends, or the abstract: pairing evidence concentrates there. If the budget cannot be met without cutting Methods, mark the paper for a section-level second pass rather than truncating blindly.

### 4. Model call
Temperature 0, one call per paper, response format constrained to JSON where the API supports it. Record `model_id`, `task_version`, `pack_sha256`, request timestamp, and input/output token counts.

### 5. Parse
On JSON parse failure, retry once with a repair instruction ("return only the JSON object"). A second failure leaves the paper **unscored rather than scored as failed**: no record is written, `pe.pending` reports it as unparseable with the reason, and re-running step 2 picks up only those. There is no `error_code` field — step 9 says what is actually recorded.

### 6. Quote verification and recomputation
For each quote, normalize both sides (see **How to use**) and check it against the source named by its `source_id`.

- Verified: keep.
- Verifies against a different source: keep the text, correct the `source_id`, flag `EV-WRONG-SOURCE`.
- Unverified anywhere: drop the quote, flag `EV-UNVERIFIED`.
- A perturbation left with zero verified `evidence_quotes`: drop the perturbation object entirely and flag `EV-PERT-DROPPED`.
- A `single_cell_paired` of "yes" or "no" whose `assay_evidence` failed verification: downgrade that pairing to "unclear" and flag `EV-PAIRING-DOWNGRADED`.
- A `suppressed_candidates[].evidence_quote` that fails verification: drop the QUOTE, keep the ENTRY, and flag `EV-SUPPRESSED-UNVERIFIED`. The entry is a record that a decision was made; the decision is not undone by a bad quote, and deleting the entry would restore exactly the silence this field exists to remove. A suppressed candidate is never dropped and never promoted, so nothing here feeds the recomputation below.

**Then recompute `perturbation_present` in the harness** by re-running Stage A and Stage B over the pruned object. Do not trust the model's returned value after pruning; a determination resting on a hallucinated quote must not survive the removal of that quote. Store both values (`perturbation_present_model`, `perturbation_present_final`) and count disagreements: a rising disagreement rate is the earliest signal of evidence fabrication.

### 7. Idempotency
Key each result on `(paper_id, task_version, model_id, sorted source sha256 list)`. A rerun over an unchanged corpus is a no-op unless forced, matching manuscript-harvest's behavior, so a killed run can be resumed safely.

### 8. Retries
Transport and rate-limit errors: retry, then leave the paper pending with its error text rather than writing a record. Never write a failed call as a determination of "no" — and never let it vanish either: a paper that was never scored must stay visible as pending, because absence is indistinguishable from a negative in any downstream count.

### 9. Output record
One JSON file per paper: the model object **flat**, post-pruning and with `perturbation_present` recomputed, carrying the harness's own findings in a `validation` block beside it.

```json
{
  "task_version": "{{TASK_VERSION}}",
  "paper_id": "10.1038_s41586-024-00000-0",
  "...": "every other field the model returned, pruned",
  "perturbation_present_model": "yes",
  "perturbation_present_final": "no",
  "validation": {
    "model_id": "...",
    "pack_sha256": "<sha256 of every rule-bearing file>",
    "stage_a": "no",
    "stage_b_capped": false,
    "determination_changed_by_harness": true,
    "quotes_checked": 7, "quotes_failed": 1, "quotes_wrong_source": 0,
    "evidence_flags": ["EV-UNVERIFIED"],
    "consistency_flags": [],
    "issues": ["..."],
    "threshold": 0.85,
    "sources_verified_against": ["main", "supp1"]
  },
  "needs_review": true
}
```

**Written as it actually is, because the shape stated here was not the shape produced.** This section used to specify a `{"run": {...}, "result": {...}}` envelope with `run_id`, `assembled_text_sha256`, `input_tokens` and `error_code` — none of which any record carries, and none of which appears anywhere in the harness. A spec of the record shape is what the next pack author builds against, so an aspirational one is worse than none. What the harness does instead: the record is flat, the run's own facts live under `validation`, and a paper that failed or was never scored is not a record at all — `pe.pending` reports it as pending with a reason, and the summary row carries an open-string `status` (`pending`, `unreadable: ...`, the manifest's error text) with `triage_priority` 0. If the envelope above is wanted, it has to be built; do not read it here as a description of what exists.

### 10. Triage table
Flatten to one row per paper for curator review, sorted by this priority:

1. `perturbation_present = "unclear"` and `unresolved_reason = "pairing_not_stated"` (most likely to hide a real match).
2. `perturbation_present` is not `"yes"`, and some `suppressed_candidates[].would_have_paired = "yes"` **for a rule whose boundary is under review** — `reporter_or_marker`, `incidental_clinical_therapy`, `unintended_condition`, `derivation_formulation` (**new in v0.0.10**). The other four rules are long-settled toggles and are deliberately excluded: `observational_disease_state` pairs "yes" on any tumour-vs-normal or disease-vs-healthy contrast, so including it put 5 of the 6 regression papers in this tier, and a tier holding most papers is not a queue. Both the restricted and unrestricted counts are reported (`suppressed_would_pair_yes_under_review` and `suppressed_would_pair_yes`), so the share of the suppression load coming from settled toggles stays visible. One toggle flips these papers: the candidate is named, its pairing is already judged, and the only thing standing between the paper and a `"yes"` is a NOT-list rule. Priority 1 stays above it because that bucket is an open question a reader must resolve; this bucket is a settled call a curator must ratify — narrower, but far more actionable.
3. `perturbation_present = "yes"` with `paper_confidence < 0.6`.
4. `perturbation_present = "unclear"` and `unresolved_reason = "degraded_text"` (route to re-fetch, not to reading).
5. `perturbation_present = "no"` with `perturbation_present_any_assay = "yes"` (the v0.0.3 filter doing its job; sample it, do not read all of it).
6. Any row with a non-empty `consistency_flags` or `evidence_flags` — **or** an `unclear` whose `unresolved_reason` is not one of the five reasons a tier above already accounts for (including "none", null and an off-enum string). Such a paper has no stated reason to be unclear and used to fall past every tier into 9, the bottom of the queue; widening this tier rather than inserting one is why nothing renumbered.
7. `perturbation_present = "yes"` and the `single_cell_paired = "yes"` perturbations name an organism, but **none of them names the organism curation is scoped to** — in practice, **`yes` carried entirely by a non-human model** (**new in v0.0.12**). A paper whose every `paired_organism` is `null` is deliberately NOT here: an organism nobody stated must not read as a non-human one, so that case stays out of the tier and is counted separately (`paired_organism_human` is empty, not `false`). Two of five such papers in the 50-paper v0.0.11 run were ruled `no` by the curator on exactly this basis (`CURATOR-RULINGS.md` 4 and 5): a mouse perturbation paired to mouse scRNA-seq while the human cohort was observational. It sits at 7 rather than near the top for a reason of KIND, not convenience: tiers 1-6 flag uncertainty or defect, whereas this tier flags a determination that is probably **correct under these rules** and may still be out of curation scope. It is a scope filter, and the scope belongs to a person. **Placing it in the previously-unused slot 7 also means tiers 1-6 do NOT renumber** — the v0.0.10 renumber is a documented trap and is not repeated.

**The ladder renumbered at v0.0.10.** Old 2-5 became 3-6 to make room for the suppression tier. **v0.0.12 did NOT renumber**: its new tier took the unused slot 7. Do not compare a priority column across versions without checking which prompt version produced it. The ladder now lives in ONE place, `task/report.yaml: tiers`, read by both the predicate (`task/report.py: triage_priority`) and the queue summary — it used to be written twice inside `pe/summarize.py`, forty lines apart. This list and that table must be changed together, and priority 0 remains reserved for rows that failed or are still pending.

Columns: `paper_id`, `doi`, `perturbation_present`, `perturbation_present_any_assay`, `has_single_cell_assay`, `paper_confidence`, `unresolved_reason`, `n_perturbations`, `processing_status`, `text_completeness`, `consistency_flags`, `evidence_flags`, `perturbation_agents` (`|`-joined, and never truncated — a curator reads that column to judge each perturbation, and a cut mid-sentence cannot be judged), `single_cell_assay_types`, and — new in v0.0.10 — `n_suppressed`, `suppressed_rules` (`|`-joined set of the `rule` values used), `suppressed_would_pair_yes` (boolean) and `suppressed_would_pair_yes_under_review` (boolean, restricted to the four rules tier 2 admits — both are emitted so the share of the suppression load coming from settled toggles stays visible); and — new in v0.0.12 — `paired_organisms` (`|`-joined distinct `paired_organism` values over the `single_cell_paired = "yes"` perturbations), `paired_organism_human` (`true`/`false`/empty when unknown) and `n_paired_yes_human`.

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
| `any_assay = yes` and `perturbation_present` is `no` **or** `unclear` | Size of the assay-pairing filter's effect. Both, not just `no`: a perturbation whose pairing went unresolved was filtered by the same requirement, and this is what `validation.assay_filtered` and Screen A count. Triage tier 5 is deliberately narrower — it samples only the confident negatives |
| Mixed no/unclear pairing papers | Frequency of the case v0.0.4 left undefined |
| **Papers by suppression rule** (v0.0.10) | What each NOT-list rule is actually costing. This is the counter that makes a boundary change arguable from data instead of from hand-reading — the measurement v0.0.9 needed and did not have |
| **Papers with a suppressed candidate that would have paired `yes`** (v0.0.10), counted both unrestricted and restricted to the rules under review | Every one of these is a paper the rules alone kept out of `yes`. The RESTRICTED count is the review queue and is what triage tier 2 holds; the unrestricted one is larger and is not a queue — `observational_disease_state` pairs `yes` on any disease-vs-healthy contrast, so an unrestricted tier put 5 of 6 regression papers in it. Reporting both is what makes the settled-toggle share visible |

---

## Validation loop

1. **Re-score an acceptance set against a PRESERVED baseline of the previous version, and run it TWICE.** Do not assume the delta is zero, and do not read one run's delta as an effect: this prompt disagrees with itself on a few percent of papers across two runs of byte-identical input, so a single-paper movement against a single-run baseline is *unattributable* rather than negative. `pe.compare --baseline <dir> --baseline2 <dir>` computes that noise floor and labels movements inside it `WITHIN-NOISE`. Every paper that moves beyond it must be accounted for by a named change class (`task/change.yaml`); an `UNEXPLAINED` movement is a bug in this version, not a refinement. Include at least one paper the change must NOT touch, and check `CURATOR-RULINGS.md` for papers that already constrain the criterion you edited. `ACCEPTANCE-v0.0.12.md` is the worked example.
2. For every paper where `perturbation_present` and `perturbation_present_any_assay` disagree, spot-check the full text to confirm the assay-pairing call. This remains the highest-value QA pass.
3. Hand-label a small stratified set specifically on the pairing dimension (clearly paired, clearly not paired, ambiguous pairing) if the automated pairing calls look unreliable.
4. Validate the multi-source path separately: pick papers whose perturbation detail lives only in supplementary methods and confirm the quotes carry the right `source_id` and that main-text-only runs miss them. This is the one behavior that cannot be checked on a main-text-only sample.
5. Check the counters in step 11 before extending to the full corpus. Two failure modes to watch: an unclear bucket dominated by `degraded_text` means the fetch pipeline is the bottleneck, not the prompt; a non-trivial `perturbation_present_model != final` rate means quotes are being fabricated and the confidence bands are not trustworthy.
6. Once pairing precision/recall look acceptable, extend to the full corpus and route by the triage priority in step 10.
