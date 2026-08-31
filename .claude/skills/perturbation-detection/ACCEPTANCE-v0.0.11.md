# v0.0.11 acceptance test

Run 2026-08-31, twice, on 22 papers, against a preserved v0.0.10 baseline.
Determination-only is the pass/fail criterion; everything else below is
observation. Draft under test: branch `prompt-v0.0.11`.

## Why these 22

Selected on a rule, not by feel:

- **all 9 papers that were `yes` under v0.0.10** — both edits widen exclusions, so
  a `yes` is what a regression would destroy;
- **every paper the two changed rules actually fired on** — 4
  `derivation_formulation`, 5 `incidental_clinical_therapy`;
- **the standing 6-paper regression set**, which carries both controls.

Set is `papers-accept-v0011.txt`. Baseline is `baseline-v0010-accept/`, merged from
`work-30-eval` (the 30-paper eval) and `work-6-v0010-g5`, with all 22 verified
`prompt_version: 0.0.10`. `-g5` is the correct v0.0.10 baseline for the 6: it is
the run in which guard 5 had landed and both controls read `yes`.

| paper | v10 first run | +guards 1-4 | +guard 5 (baseline) |
|---|---|---|---|
| `s41586-024-07571-1` | no | **yes** | **yes** |
| `elife.104978.2` | no | no | **yes** |

## Result: PASS

44/44 papers ran clean, 0 FAIL, 0 unparseable. Quotes 235/235 and 231/231, zero
failures and zero misattributions in both runs.

| diff | moved |
|---|---|
| r1 vs v0.0.10 | 1 / 22 |
| r2 vs v0.0.10 | **0 / 22** |
| r1 vs r2 | 1 / 22 — **95%** |

**All 9 `yes` papers held in both runs. Both curator-ruled papers stayed `no`.**

### The three controls, all PASS in both runs

- **`elife.104978.2` -> `yes`.** The test change 2 exists to survive. It is
  structurally identical to `s41467-025-65049-8` — two sequenced timepoints
  straddling chemotherapy — and had to land on the opposite side on attribution
  alone ("chemotherapy-driven lineage switch"). It did, twice.
- **`s41586-024-07571-1` -> `yes`.** The gluten-free-diet paper the v0.0.10
  attractor broke; the canary for new rule text making suppression salient again.
- **`s41467-026-70751-2` -> `yes`.** Anti-PD-1, a clinical therapy that genuinely
  IS the study's variable. Untouched by change 2, as intended.

### The one determination difference is not v0.0.11

`10.1016/j.cell.2019.08.008`: v0.0.10 `no`, r1 `unclear`, r2 `no`.

It is **Stage B**, not the criteria. `unresolved_reason = degraded_text`, and r1's
own `ambiguities` reads *"Stage A resolves to 'no' under A1: the perturbations
array is empty and perturbation_present_any_assay is 'no'."* r1 reached the same
criteria answer as r2 and as v0.0.10, then capped itself because it judged its own
input `truncated`.

The two runs saw byte-identical prompts (same md5), the paper is 112,061 chars
against a 400,000 budget with no harness truncation, and `text_completeness` reads
`full / truncated / full` across three runs of the same bytes. **Criteria stability
is therefore 22/22 in both runs**; the wobble is in a v0.0.5 self-assessment field
this draft does not touch.

## Observations that are not pass/fail

**Suppression volume is not comparable to the 30-paper eval.** 53 candidates (r1)
and 49 (r2) over 22 papers, mean 2.4 / 2.2, against 1.27 on the 30. That is
expected and not a signal: this set was *selected* for papers the two rules fired
on, so it is enriched by construction.

**No over-firing of the forbidden kind, despite added prompt text.** Re-ran the
guard-3 scan because v0.0.10's lesson is that adding a structured path makes it
more travelled. No wash-buffer ROCK inhibitor, no HypoThermosol or 4 °C hold, no
pen/strep, no bare library kit. Every Y-27632 hit is a named component *inside* a
differentiation cocktail that is itself the candidate. The in-silico hits
(CellOracle SOX4 knockout, SCENIC+ knockout simulation) are defensible: a
computational "knockout" is exactly the knockout-shaped trap the field is for. One
borderline entry, r1 only: "viral RT oligo spiked into the 10x master mix", which
is close to ambient Methods.

**Rule-label stability is much lower than determination stability: 11/22 papers
have an identical rule multiset across the two runs.** This is the first two-run
measurement of that property at any prompt version, so it **cannot be attributed
to v0.0.11** — no clean v0.0.10 double-run exists to compare against (the three
work-6-v0010 dirs each ran a different prompt). Most differences are one marginal
candidate crossing the deliberation threshold in one run and not the other
(±1 entry), rather than the same candidate landing in a different bucket; two
cases (`2025.09.26.678707`, `elife.104978.2`) are genuine relabels. It matters
because the closed `rule` set exists so the corpus becomes *countable* — the
per-rule counters are noisier than the determinations they sit beside, and a
corpus-scale tally should be read with that in mind.

**`model != final` on `sciimmunol.adz8650` (1/22) is pre-existing, and is not
fabrication.** Identical at v0.0.10 (`model=no`, `final=unclear`) with
`quotes_failed=0`, so it is Stage B's cap, not quote pruning. `pe.summarize`
labels this counter "fabrication rate", which is misleading whenever Stage B is
the cause.

## Correction to the draft's stated cost

The v0.0.11 changelog and commit message both claim the edits "can only move
papers toward `no`". That is wrong. The precedence rewrite can also *un-suppress*
— a candidate that moves out of `suppressed_candidates` and into `perturbations`
with an unclear pairing drives the paper to `unclear` under A5. It did not happen
in either run, but the safety argument as written was incomplete in the direction
that matters for recall.

## What this does and does not license

Licensed: the two rules behave as rewritten on the population they touch, the
intent test in change 2 held at 22/22 across two independent samples, and the case
most likely to break it did not.

Not licensed: anything about the ~370 corpus papers neither ruling has been near.
This set was chosen *because* it is exposed to these rules; it measures the blast
radius where the blast was aimed, not everywhere.
