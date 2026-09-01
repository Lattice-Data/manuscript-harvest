# Proposal: record the organism of the paired material, and do not call on it

Status: **proposal only.** Nothing in `prompt.md` or `pe/` changes in this branch.
Raised by the curator 2026-08-31 after ruling two papers `no`; see
`CURATOR-RULINGS.md` entries 4 and 5.

## The gap

`perturbation_present` today answers: *was a perturbed sample profiled by a
qualifying single-cell assay?* It does not ask **whose** sample. A paper can
therefore be `yes` on the strength of a mouse model while the human data — the
material that actually reaches the curated deposit — is purely observational.

Two papers in the 50-paper v0.0.11 run were ruled `no` for exactly this:

- **`10.1038/s41586-022-05060-x`** — `yes` came from surgical LAD coronary artery
  ligation in mice with sham controls, paired to mouse scRNA-seq. The human
  snRNA-seq / snATAC-seq / Visium cohort is naturally occurring myocardial
  infarction, suppressed under `observational_disease_state`. Curator: the mouse
  work is "more as comparisons or parallels to human data", and "the CellxGene
  contains the human, and not mouse data."
- **`10.1126/science.aay3224`** — `yes` came from a Rag1 knockout **mouse**. The
  paper is a human thymus atlas. Curator: "no perturbation of human T-cells."

Both were independently flagged in review as the least trustworthy calls in the
set, without the reviewer identifying why. They are one gap, not two judgments.

## Size

Keyword scan over the paired perturbations of all 15 `yes` papers in
`work-50b-v0011` (heuristic, not authoritative — which is itself the argument for
a real field):

| paired perturbation is | papers |
|---|---|
| animal only | **5** |
| human | 6 |
| both / unclear | 4 |

A third of the positives rest on an animal model; **~10% of a random 50-paper
sample.** Two are the ruled papers. The other three are the same shape and would
plausibly follow:

- `10.1016/j.isci.2022.104097` — BTBR ob/ob and UMOD-KI mouse genotypes
- `10.1038/s41588-023-01435-6` — CRISPR-Cas9 Tbx6 knockout (mouse)
- `10.1096/fj.202002747r` — ovariectomy + 17β-estradiol

This is the largest single source of false positives measured in any run so far.

## Design

**Record the organism. Do not filter on it.** Curator, 2026-08-31: the corpus is
"human primarily, but... we SHOULD NOT limit to human only. The paper could be
mice, or zebrafish or killifish, or some other specie." And: add the column "but
not call on it — leaving it to the human interpreter."

### 1. Schema — additive, `schema_version` 0.0.6 -> 0.0.7

On each `samples[]` entry:

```json
{
  "label": "group/condition label as named in the paper",
  "organism": "NCBI taxon name as the paper states it, or null if unstated",
  "perturbed": "true | false | \"unclear\"",
  "perturbation_refs": [0]
}
```

On each `perturbations[]` entry, alongside `single_cell_paired`:

```json
{
  "single_cell_paired": "yes | no | unclear",
  "paired_organism": "organism of the material this pairing refers to, or null",
  "assay_evidence": {"source_id": "...", "quote": "..."}
}
```

Open, not closed, unlike `rule`. A closed set would have to enumerate every
model organism in advance, and killifish is exactly the case that breaks such a
list. Normalise downstream, not in the prompt. `null` is a legitimate value and
must not be guessed: an unstated organism is unstated.

### 2. Determination logic — UNCHANGED

Stage A, Stage B and the truth table are untouched. `perturbation_present` stays
species-agnostic. Two reasons this matters more than convenience:

- **A non-human dataset can be a valid curation target.** Hard-coding "human"
  into the determination silently drops a mouse-only perturbation study, and this
  task is deliberately recall-biased, so that is the worst available failure
  direction.
- **The paper usually cannot say which species was deposited.**
  `s41586-022-05060-x` contains both human and mouse single-cell data and nothing
  in the text says which reached CellxGene. That is knowledge about the deposit,
  not the publication. Extraction can faithfully report "this pairing is mouse,
  that one is human"; it cannot know which is curated. Recording is answerable,
  filtering is not.

It also avoids the first Stage A change since v0.0.5, which
`tests/test_determination_v005.py` exists to prevent.

### 3. Downstream — where the question actually gets asked

New `pe.summarize` columns:

- `paired_organisms` — pipe-joined distinct organisms across `single_cell_paired
  = "yes"` pairings; empty when no pairing is `yes`
- `paired_organism_human` — `true` / `false` / `""` (unknown), derived
- `n_paired_yes_human` — count, so a mixed paper is legible at a glance

New corpus counter: papers by `paired_organisms`, and the one the curator asked
for by hand — **papers that are `yes` with no human pairing**.

New triage tier, inserted rather than renumbering (the v0.0.10 renumber is
already a documented trap): **`yes` with no human paired organism.** These are
the papers a human interpreter must look at, and the tier is a queue, not a
verdict — consistent with the standing rule that a report names the stage, not
the answer.

### 4. Test plan

`tests/test_determination_v005.py` must pass **unchanged** — that is the
assertion that this change is additive. Add `tests/test_organism.py` covering:
`null` accepted and never inferred; an open string accepted; a species-only
change never moves `perturbation_present` across every Stage A input combination
(the shape `test_suppressed_candidates.py` uses for schema 0.0.6).

### 5. Measurement before merge

Same protocol as v0.0.11, since a new required field has twice acted as an
attractor:

- determination-only diff against the preserved v0.0.11 baseline
  (`work-50b-v0011`), **run twice**;
- the acceptance set must include the 5 animal-only papers, the 6 human ones, and
  the 4 mixed — the mixed ones are the real test, since they must stay `yes` and
  report two organisms rather than collapsing to one;
- **and a paper that must not populate the field at all** — a purely in-vitro
  human cell-line study where organism is stated once or not at all.

Expected: zero determination changes. Any movement is the attractor, not the
field.

## Cost

- `schema_version` bump; the 381 existing `corpus/*/extracted/perturbations.json`
  files predate the field and will not have it.
- Populating it requires a re-run. It does **not** require a criteria change,
  which makes this a materially safer edit than the last two.
- One more required field on a schema that has grown twice recently, each time
  with a measured pull toward over-population.
