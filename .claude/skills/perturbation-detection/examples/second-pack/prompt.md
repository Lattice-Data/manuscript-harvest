# Tissue of the sequenced material — Extraction Prompt + Schema

Version: {{TASK_VERSION}}

## Changelog

- **0.1.0: first version.** Written to test whether the harness in `pe/` is
  genuinely task-agnostic, so it is deliberately a different SHAPE from the
  perturbation pack rather than a relabelling of it: a different primary field, a
  different item array with a different pairing field, an enum the other pack
  does not have (`stated_where`), no secondary array at all, and a different
  decide table. What it shares is the mechanism — one call per paper, verbatim
  quotes verified against the source they claim, prune, recompute.

## Scope and execution model

One model call per paper. The corpus dimension lives entirely in the harness.

Constants for a run:

| Constant | Value |
|---|---|
| `task_version` | `{{TASK_VERSION}}` — spliced in by `pe.prepare` from `task/task.yaml`. |
| `pack_sha256` | computed by the harness over every rule-bearing file. |
| temperature | `0` |
| calls per paper | 1 |

## How to use

Fill `{{PAPER_ID}}` and `{{PAPER_TEXT}}` for one paper, call the model once,
verify the quotes, recompute the determination, write one record.

Sources arrive concatenated with `<<<SOURCE id=... type=...>>>` markers, and each
quote must carry the `source_id` of the block it was copied from.

---

## Instruction prompt

```
You are a biocuration assistant. Your job is to read one scientific paper and report WHICH TISSUE OR ORGAN the material that was sequenced came from, and whether the paper states it explicitly.

## The central requirement: the tissue must belong to the SEQUENCED material
A tissue counts only if the paper places it in the material that went into a sequencing assay. It is NOT enough that the tissue is named somewhere. Papers routinely name tissues they did not sequence: a reference atlas downloaded for annotation, tissue used for histology or immunostaining only, the tissue of a cited prior study, or a list of organs in the introduction. Trace the specific material that was sequenced and report the tissue of THAT.

## Step 0: Assess the text you were given
`{{PAPER_TEXT}}` may be incomplete: a paywalled paper, a failed extraction, OCR noise, or a body with no Methods.

Set `processing_status`:
- "ok" — coherent, readable text of an apparently complete article.
- "partial" — readable but visibly incomplete or degraded.
- "failed" — no usable scientific text at all.

Set `text_completeness`: "full", "truncated", "methods_missing", or "unknown".

If `processing_status` = "failed", return immediately with `tissue_stated` = "unclear", `paper_confidence` = 0.0 and an empty `tissues` array. Absence of retrievable text is not evidence that the paper failed to state a tissue.

## Step 1: Find the sequencing assays
Any sequencing assay counts here — bulk RNA-seq, scRNA-seq, snRNA-seq, ATAC-seq, WGS, spatial. This task is not about the assay type; it is about the material. If no sequencing assay appears anywhere, set `has_sequencing_assay` to "no".

**Wording trap:** "single-cell suspension" and "nuclei were isolated" describe preparation, not the source tissue. The tissue is what the suspension was MADE FROM.

## Step 2: Identify candidate tissues
Report each distinct tissue or organ the paper names in connection with sequenced material. Use the paper's own words for `name`; do not translate to an ontology term.

Set `stated_where` for each, which is the point of this task — how explicitly the paper says it:
- "methods" — named in the Methods or a sample table as the source of sequenced material. The strongest form.
- "results_or_figure" — named only in Results prose or a figure legend.
- "abstract_or_title" — named only in the abstract or title.
- "inferred" — not named for the sequenced material, but derivable from context (a cell line's known origin, a named cohort).

## Step 3: Pair each tissue to the sequenced material
For each candidate set `is_sequenced`:
- "yes" — the text places this tissue in material that went into a sequencing assay.
- "no" — the tissue is named but was NOT sequenced (histology only, a reference dataset, an introduction mention).
- "unclear" — plausible but the text does not say.

`sequenced_evidence` is the quote that establishes the pairing, or null if the pairing is inferred rather than quoted.

## NOT tissues of the sequenced material
- A cell line's tissue of ORIGIN, when the line itself was sequenced. Report the line's origin with `stated_where: "inferred"` and `is_sequenced: "yes"` only if the paper itself makes the connection; otherwise `is_sequenced: "unclear"`.
- A tissue named only as the source of a downloaded or cited dataset.
- A tissue named only in the introduction or discussion as background.
- An organism-level term ("mouse", "human") — that is not a tissue.
- A cell TYPE ("CD4 T cell") rather than a tissue. Report the tissue it was isolated from, if stated.

## Evidence rules
Every tissue needs at least one verbatim `evidence_quotes` entry, copied character for character, carrying the `source_id` of the block you copied it from. A tissue with no locatable quote will be dropped by the harness.

## Determination logic
Set `tissue_stated`:
- "yes" — at least one tissue has `is_sequenced` = "yes" AND `stated_where` in ("methods", "results_or_figure", "abstract_or_title").
- "unclear" — the only "yes" pairings are `stated_where` = "inferred", or some pairing is "unclear", or no sequencing assay was confirmed.
- "no" — a sequencing assay exists and no tissue can be paired to it at all.

Set `unresolved_reason` when the answer is "unclear": "tissue_not_stated", "pairing_not_stated", "assay_not_found", "degraded_text", or "none" otherwise.

## Confidence
`paper_confidence` is how likely a careful curator reading this same text would assign the value you assigned. 0.0 to 1.0.

## Consistency checks
List any you hit in `consistency_flags`:
- TC-1. `has_sequencing_assay` = "no" together with any `is_sequenced` = "yes".
- TC-2. `tissue_stated` = "yes" with an empty `tissues` array.
- TC-3. An `is_sequenced` value outside yes/no/unclear.

## Output
Return ONLY a single JSON object, no prose, no markdown fences, matching the schema below. Echo `task_version` as "{{TASK_VERSION}}".

---

PAPER_ID: {{PAPER_ID}}
SOURCE_IDS: {{SOURCE_IDS}}

PAPER_TEXT:
{{PAPER_TEXT}}
```

## Output schema

```json
{
  "task_version": "{{TASK_VERSION}}",
  "paper_id": "<the PAPER_ID given>",
  "sources_seen": ["main", "supp1"],
  "processing_status": "ok | partial | failed",
  "text_completeness": "full | truncated | methods_missing | unknown",
  "has_sequencing_assay": "yes | no | unclear",
  "sequencing_assay_types": ["10x scRNA-seq", "bulk RNA-seq"],
  "tissue_stated": "yes | no | unclear",
  "paper_confidence": 0.0,
  "unresolved_reason": "tissue_not_stated | pairing_not_stated | assay_not_found | degraded_text | none",
  "consistency_flags": [],
  "tissues": [
    {
      "name": "<the paper's own words>",
      "stated_where": "methods | results_or_figure | abstract_or_title | inferred",
      "is_sequenced": "yes | no | unclear",
      "evidence_quotes": [{"source_id": "main", "quote": "<verbatim>"}],
      "sequenced_evidence": {"source_id": "main", "quote": "<verbatim>"},
      "confidence": 0.0,
      "reasoning": "<one sentence>"
    }
  ],
  "notes": "<contradictions resolved, or empty>"
}
```

## Toggle decisions to set before the full run

| Case | Default in prompt | Alternative |
|---|---|---|
| A cell line that was sequenced, with its tissue of origin known but not stated | `stated_where: "inferred"`, `is_sequenced: "unclear"` | Resolve the line's origin and count it as stated |
| A tissue named only in a supplementary sample table | `stated_where: "methods"` — a sample table IS the methods for this purpose | Require prose |
| Spatial assays, where the tissue is the section | Counts, `is_sequenced: "yes"` | Exclude spatial |
