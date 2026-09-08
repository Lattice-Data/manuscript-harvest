# Corpus re-score under v0.0.21

Run 2026-09-07/08 on all 392 papers of `papers-all.txt`, plus a second run of the
22 papers whose determination moved. First corpus-scale score since v0.0.12, and
the first that covers curator rulings 5 through 14.

This is not an acceptance test. It is the corpus measurement the six acceptance
records explicitly did not license: the largest of them covered 92 papers and
predated two of the four criteria changes.

## The corpus

| `perturbation_present` | v0.0.12 | v0.0.21 |
|---|---:|---:|
| yes | 115 | **101** |
| unclear | 13 | **19** |
| no | 264 | **272** |

370 of 392 unchanged. 22 moved, 5.6% -- which is inside the noise floor measured
on `papers-50b`, so the movement could not be interpreted from one run. Hence the
mover re-run.

**Every change is accounted for by a known mechanism. `UNEXPLAINED` is 0.** This
was predicted to be large, on the reasoning that `task/change.yaml`'s classes were
calibrated for single-version steps while this diff spans nine versions and four
criteria changes. The prediction was wrong: `SUPPRESSED` and `PERT-SET-CHANGED`
key on what changed in the RECORD, not on which version changed it, so they absorb
criteria movement of any size.

| class | n |
|---|---:|
| `PERT-SET-CHANGED` | 16 |
| `SUPPRESSED` | 12 |
| `ANY-ASSAY-CHANGED` | 8 |
| `STAGE-B` | 7 |
| `STAGE-B-RELEASED` | 2 |
| `SUPP-EVIDENCE` | 1 |
| `UNEXPLAINED` | **0** |

Health: 392/392 validated, 0 unparseable, 2337/2337 quotes verified with 0 failed
and 0 misattributed, 0 CC codes, 0 EV flags, 0 perturbations dropped for
unverifiable evidence. One paper differs between model and final after pruning
(`10.1126_sciimmunol.adz8650`).

## The 22 movers, re-run: 15 real, 7 noise

**All seven unstable papers return to their v0.0.12 value in run 2.** Not one of
the seven is a movement at all; each is a paper the model reads differently on
consecutive passes over byte-identical input.

| | n | what it is |
|---|---:|---|
| criteria movement, stable | **13** | full text, `unresolved_reason: none` |
| text-quality movement, stable | **2** | Stage B cap entered on degraded text |
| apparent movement that was noise | **7** | r2 reverts to the baseline value |

The 13 criteria movements, eleven `yes -> no` and two `unclear -> no`:

`j.ccell.2025.12.003` (r7) · `j.cell.2021.11.031` (r8) · `j.healun.2026.02.1666` ·
`j.immuni.2022.09.002` · `j.isci.2022.104097` (r13) · `j.molmet.2023.101746` ·
`s41590-023-01584-0` · `s42003-021-02562-8` · `science.aay3224` (r5) ·
`bloodadvances.2023011445` · `s12943-025-02430-7` · `fimmu.2023.1211505` (r12) ·
`10.64898_2025.12.18.695268`

`disease_model_establishment` -- the rule added at v0.0.15 for the curator's AKPS
engraftment ruling -- fires in 8 of the 13. It had never been measured at corpus
scale before; it now accounts for more of the movement than any other rule.

## Stage B's cap is triggered by a self-report that is not reproducible

This is the finding worth acting on, and the mover re-run is what exposed it.

Five of the seven unstable papers are unstable because the model's own
**text-quality assessment** flipped, not because its judgment about perturbation
changed:

| paper | `text_completeness` r1 -> r2 | `paper_confidence` r1 -> r2 |
|---|---|---|
| `s41591-021-01586-1` | truncated -> full | 0.35 -> 0.90 |
| `2020.03.04.976407` | truncated -> full | 0.45 -> 0.85 |
| `2023.03.06.531398` | truncated -> full | 0.35 -> 0.88 |
| `2024.10.18.618987` | truncated -> full | 0.45 -> 0.90 |
| `science.abo1984` | unknown -> full | 0.30 -> 0.88 |

The input was verified identical: `assembled_text_sha256` matches, the prompt
files are byte-identical, and `truncation.rung` is 0 in both runs for all five --
the harness dropped nothing for budget either time.

So Stage B is not misfiring on its own terms. Its ENTRY CONDITION is a model
self-report, and that self-report is not stable on the same bytes. Every
consequence follows from it: the `unclear`, the 0.39 ceiling, the P4 routing to
re-fetch. A paper lands in the re-fetch queue or does not depending on which pass
you happened to run.

Whether the underlying text is genuinely incomplete is a separate question and
this run does not settle it -- rung 0 means the HARNESS truncated nothing, not
that the publisher's extraction was whole. The instability is in the
classification, not necessarily in the classification's subject.

The remaining two unstable papers are ordinary judgment wobble at a boundary:
`scitranslmed.abh2624` (`no` then `yes`, full text, confidence 0.70 then 0.40) and
`rccm.202207-1384oc` (`unclear` then `no`, `pairing_not_stated`).

## Curator rulings, checked against the corpus: 8 of 9 land

Rulings 5, 7, 8, 9, 10, 12, 13 and 14 all produce the determination the curator
gave. Seven of the eight were `yes` under v0.0.12, so the corpus now agrees with
the curator where it previously did not.

**Ruling 6 is satisfied at the judgment layer and reported as `unclear` anyway.**
`science.aat1699` suppressed the neoadjuvant treatment under
`incidental_clinical_therapy` and wrote its own conclusion:

> *"no perturbation was reported, and perturbation_present_any_assay = "no", so
> Stage A gives "no" via A1"*

Stage B then capped that negative, because the paper's text is broken:

> *"jumps from "Young et al. Page 4" to "Young et al. Page 8" ... several of which
> are rendered as long runs of garbled control characters ... No
> methods/experimental-procedures prose is present in either source"*

That is the asymmetric cap working: it refuses to certify a negative whose Methods
it could not read. The criteria agree with the curator; the EXTRACTION is what
fails, three pages missing and control-character damage in the supplementary. It
belongs to the extract layer, not to `prompt.md`.

## Triage queue

| tier | n | |
|---|---:|---|
| P1 | 4 | `unclear` + `pairing_not_stated` -- read first |
| P2 | 106 | held back by a rule still IN REVIEW that would have paired `yes` |
| P3 | 7 | `yes` with confidence < 0.6 |
| P4 | 11 | `unclear` + `degraded_text` -- route to re-fetch |
| P5 | 35 | `no` but `any_assay=yes` |
| P6 | 0 | consistency or evidence flags |
| P7 | 43 | `yes` carried only by a non-human model -- scope call |
| P9 | 186 | everything else |

P7 counts 43 where the underlying population is 46, because the ladder stops at
the first matching tier. That is the known undercount, unchanged.

Organisms of `yes`-paired perturbations: human 55, mouse 46, rhesus macaque 2,
zebrafish 2, *Plasmodium falciparum* 1.

## What this licenses, and what it does not

**Licensed.** 385 of 392 papers have a determination that two runs agree on, or
that survived a version change unaltered. The 13 criteria movements are stable and
reproduce the curator's rulings. The corpus can be curated on.

**Not licensed.**

- **Seven papers have no determination**, in the sense that matters: their value
  depends on which run you read. Five of them because of the Stage B self-report,
  two at a judgment boundary.
- **The suppression tallies are texture, not measurement.** The field was measured
  at ~21% unstable run-to-run earlier in this session, and this run does not
  revisit that. `disease_model_establishment` firing in 8 of 13 movers is a real
  observation about the DETERMINATIONS, which are stable; the 54-paper rule tally
  is not the same kind of number.
- **The 370 unchanged papers were run once under v0.0.21.** They agreed with a
  different prompt version, which is evidence they are not borderline, but it is
  not a stability test.

## Operational note: a guard keyed on too narrow a signature

The run was interrupted at 249/392 by a usage limit and resumed cleanly via
`pe.pending`, with no corruption -- all 143 interrupted papers produced no raw
file at all rather than a partial one.

But `run_headless.sh`'s `.auth-failed` sentinel never fired, and this is exactly
the scenario it was written for: its own comment records that "a 22-paper run x2
burned ~60 minutes to produce 44 copies of the same 73-byte auth error". The
sentinel keys on authentication failure. What happened was

    You've hit your session limit · resets 11:50pm (America/Los_Angeles)

which 145 logs carry identically, each having paid a full `claude -p` spawn to
receive it. Right failure shape, wrong signature -- the same too-narrow-key family
as several bugs already fixed in this repo.

## Protocol note

Eighth note. **The mover re-run changed the conclusion, and it was cheap.** One
run said 22 papers moved; two runs said 15 did, and that 5 of the other 7 were a
degraded-text self-report flipping on identical bytes. Re-running only the movers
cost 22 paper-runs against 392 for a second full pass -- 6% of the cost for the
finding that mattered most in this record.

The general rule this suggests: after a corpus score, the movers are the only
papers worth a second run, and they are always a small fraction. Nothing is
learned by re-running the papers that did not move.
