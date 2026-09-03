# v0.0.12 acceptance test

Run 2026-09-01, twice, on the 50 papers of `papers-50b.txt`, against the
preserved v0.0.11 run of the same 50. Field under test: `organism` /
`paired_organism` (`schema_version` 0.0.7). Protocol from
`PROPOSAL-organism.md`.

100/100 papers ran clean: 0 FAIL, 0 ABORT, 0 unparseable, both runs validated
50/50.

## Result: PASS

The field populates, does not decide anything, and did not behave as an
attractor. One claim below is **unverified** and is called out as such.

### Determinations

| | moved vs v0.0.11 |
|---|---|
| r1 | 4 / 50 |
| **r2** | **1 / 50** |
| r1 vs r2 | 3 disagree — 94% reproducible |

Movement is not zero, so each case was classified:

| paper | what moved | organism implicated |
|---|---|---|
| `j.jcmgh.2025.101665` | r1 judged its own text `truncated` -> Stage B cap | **no** |
| `scitranslmed.abh2624` | r1 dropped a perturbation; r2 matches baseline exactly | **no** |
| `science.aay3224` | r1 pairing `yes` -> `unclear`; r2 matches baseline | **no** |
| `j.immuni.2020.03.019` | both runs moved a pairing `no` -> `unclear` | **no** — `paired_organisms` empty |

Three are single-run variance or the `text_completeness` wobble already recorded
in the v0.0.11 acceptance test (`j.cell.2019.08.008`, same shape: `full` and
`truncated` on byte-identical input). The fourth moved in **both** runs so it is
not variance, but no organism was recorded on it, so the field is not the
mechanism.

**No perturbation was removed from `perturbations` and no `paired_organism` value
drove a determination** — that is the failure `suppressed_candidates` had on its
first run, and it did not recur.

### The field works

- **Population: 61/62 `yes` pairings (98%)**; `samples[].organism` 675/676.
- The single gap (`scitranslmed.abh2624` r2, IFN-β/γ) left `paired_organism_human`
  **unknown**, so the paper was not flagged. The tri-state design absorbed it in
  the safe direction, which is the whole reason it is tri-state.
- **Open vocabulary is genuinely exercised:** human 303, mouse 94/88, pig 2,
  chimpanzee 1, macaque 1, marmoset 1. Nothing forced into a nearby bucket — a
  closed set would have been wrong.
- **Mixed-species held.** `s41588-025-02158-6` reported `human|mouse` in both runs,
  stayed `yes`, and was correctly not flagged. The other three papers a keyword
  scan had called "mixed" are genuinely human-only; the field is the accurate
  instrument and the scan was the crude one.
- **The xenograft guard resolved a real case correctly, twice.**
  `2025.05.16.654622` transplants human ES-derived retinal ganglion cells into
  mouse, and both runs answered `mouse`. That is right: the paper states "we
  profiled the transcriptome of the **host** myeloid (CX3CR1GFP) cell population",
  FACS-sorted from mouse retina. The human cells are the stimulus, not the
  sequenced material.
- Curator rulings 4 and 5 now surface mechanically as triage tier 7.

## Unverified: the `null` rate

`null` appears **2 times in ~700 values (0.3%)**. `prompt.md` says `null` is
"legitimate and common". 0.3% is not common.

Two readings, and this test cannot separate them: either these 50 papers nearly
all state their species — plausible, most do — or the model is inferring an
organism where the text is silent, which guard 2 explicitly forbids. Falsifying
it means reading a paper to confirm it never states a species, which is not
cheap.

**This is the one claim in v0.0.12 that rests on nothing.** Everything else here
is measured. A curator spot-check of one or two papers whose species should be
unstated would settle it.

## Two-run baseline, and a bug found by using it

The v0.0.11 baseline had **one** run, so for three of the four movements above
attribution was *impossible*, not negative. That gap is now closed for the next
change: `baseline-v0012-50b/` preserves both runs plus `noise-floor.json`.

**v0.0.12 noise floor: the prompt disagrees with itself on 3/50 papers** —
`j.jcmgh.2025.101665`, `science.aay3224`, `scitranslmed.abh2624`. A future change
that moves only those papers has not been shown to change anything.

`pe.compare` gained `--baseline2`. Correct use, demonstrated on this data:

```
noise floor: the baseline disagrees with itself on 3/50 paper(s)
changed BEYOND the noise floor: 0   (within noise: ...)
  -> every apparent change is run-to-run variance. Nothing is shown to have moved.
```

**The flag's first use was wrong, which is why it now has a guard.** A v0.0.12
directory was passed as `--baseline2` against a v0.0.11 `--baseline`; the tool
computed a *version diff* and reported it as variance. That inversion launders a
real effect into "nothing moved" — the precise opposite of the flag's purpose.
`pe.compare` now compares `prompt_version` across the two baseline runs and exits
2 on a mismatch, and when no second run is supplied it says so instead of staying
quiet:

```
noise floor: NOT AVAILABLE -- the baseline has only one run, so a single-paper
movement cannot be told from run-to-run variance.
```

`noise_floor()` is a module-level pure function so this is unit-tested rather
than reachable only through the CLI.

## Tests

**74 pass.** The 60 pre-existing are unchanged — including
`tests/test_determination_v005.py`, which is the assertion that the determination
contract still has not moved since v0.0.5. New: 11 in `tests/test_organism.py`
(the property test covers 2,000+ Stage A input combinations proving organism
alone moves nothing; two encode curator rulings 4 and 5 directly) plus 3 for the
noise floor, including the version-mismatch refusal.

Re-validating all 50 v0.0.11 records with the new harness changed **zero**
determinations and read their absent organism as *unstated* rather than
non-human.

## What this licenses

Licensed: the field populates on real papers, uses an open vocabulary, handles
mixed-species and xenograft cases, and does not move determinations.

Not licensed: the `null` rate, above. And this is 50 papers — the corpus is 392,
and 342 of them have never been scored under v0.0.12.

## The protocol this was run against

Carried over from `PROPOSAL-organism.md`, which shipped as v0.0.12 and is deleted
rather than left reading as an open proposal. The protocol is worth keeping
because a new required field has now three times acted as an attractor, and this
is the shape of test that catches it:

- a determination-only diff against a **preserved** baseline of the previous
  version, **run twice** -- a single run cannot tell an attractor from ordinary
  variance;
- an acceptance set covering every value the field can take, including the mixed
  cases, which are the real test: they must report both values rather than
  collapsing to one;
- **and at least one paper that must NOT populate the field at all.** A field
  that fills in on everything has stopped discriminating, and that pattern
  travelled with `suppressed_candidates` pulling real perturbations across the
  line.

Expected result: zero determination changes. Any movement is the attractor, not
the field.

**What this cost, recorded because the next proposal will want it:** the field
bump meant the 392 existing per-paper records predated it and would not carry it,
so populating it needed a full re-run. It did not need a criteria change, which
is what made it a materially safer edit than v0.0.9 and v0.0.10.
