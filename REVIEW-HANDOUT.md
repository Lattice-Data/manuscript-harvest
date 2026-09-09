# Perturbation detection — papers that need a human ruling

Thank you for helping. **24 papers, in four groups.** Each group asks one question,
so take a whole group rather than a scattering — the point is to settle a *rule*, not
to correct individual rows.

**The question, for every paper:**

> Were the samples that **actually went into the single-cell or single-nucleus
> sequencer** experimentally perturbed — a drug, a cytokine, a knockout, low oxygen,
> a diet?

**yes** · **no** · **unclear**

The rule that makes this hard: a perturbation somewhere in the paper plus a
single-cell assay somewhere in the paper is **not** enough. Papers routinely treat
cells for a bulk RNA, quantitative PCR, Western or flow readout while the
single-cell dataset comes from separate, untreated material.

**What to send back — three lines per paper, not an essay:**

1. your call — yes / no / unclear
2. one sentence of why, in your own words
3. which rule you think it turns on, if you can name it

**No deadline.**

---

## About the "machine says" and "confidence" columns

The confidence number is **not** the probability that the paper is perturbed. It
answers only: *would a careful curator, reading this same text, agree with me?* So a
well-evidenced **no** scores **high**. A high number next to a call you disagree with
is the most interesting kind of disagreement — please flag those especially.

Also worth knowing: that number is not stable. Re-running the unchanged system on
byte-identical text has moved it from 0.35 to 0.90. Treat it as a rough sort order,
nothing more.

---

## Group A — is the disease-model rule right? (8 papers)

**The rule under test:** a manipulation whose purpose is to give the samples the
disease state the paper studies is **the model, not a perturbation**.

**Your question for each paper:** was the manipulation how they *obtained* the
diseased material — or is it what the paper is *studying*?

**Why this group matters most:** this rule produces more "no" answers than any other
in the collection, and it has never been checked at this scale. Splits cleanly into
two batches of four if two people take it.

| DOI | Machine says | Its confidence |
|---|---|---|
| 10.1016/j.ccell.2025.12.003 | no | 0.70 |
| 10.1016/j.cell.2021.11.031 | no | 0.60 |
| 10.1016/j.immuni.2022.09.002 | no | 0.55 |
| 10.1016/j.isci.2022.104097 | no | 0.80 |
| 10.1038/s41590-023-01584-0 | no | 0.60 |
| 10.1038/s42003-021-02562-8 | no | 0.72 |
| 10.1126/science.aay3224 | no | 0.88 |
| 10.3389/fimmu.2023.1211505 | no | 0.85 |

## Group B — are the other exclusion rules right? (5 papers)

**The rules under test:** a clinical therapy treated as the study's *setting* rather
than its variable; a differentiation or derivation recipe treated as the model; a
naturally occurring disease state treated as observational rather than applied.

| DOI | Machine says | Its confidence |
|---|---|---|
| 10.1016/j.healun.2026.02.1666 | no | 0.60 |
| 10.1016/j.molmet.2023.101746 | no | 0.72 |
| 10.1182/bloodadvances.2023011445 | no | 0.85 |
| 10.1186/s12943-025-02430-7 | no | 0.50 |
| 10.64898/2025.12.18.695268 | no | 0.93 |

## Group C — the pairing was never stated (4 papers)

**Not a rule under test.** These are the four papers most likely to be hiding a real
match: a perturbation and a qualifying assay both exist, and the text never says
whether they met.

**Your question:** can you find the link in the paper that the machine could not?

| DOI | Machine says | Its confidence |
|---|---|---|
| 10.1016/j.coi.2022.102188 | unclear | 0.55 |
| 10.1016/j.immuni.2020.03.019 | unclear | 0.45 |
| 10.1038/s41586-021-03852-1 | unclear | 0.55 |
| 10.1164/rccm.202207-1384oc | unclear | 0.45 |

## Group D — the machine's least-confident "yes" calls (7 papers)

**Not a rule under test.** Every one of these is a "yes" the machine itself scored
below 0.6.

**Your question:** is the call right — and if it is, is the hesitation justified?

| DOI | Machine says | Its confidence |
|---|---|---|
| 10.1016/j.cell.2021.07.023 | yes | 0.40 |
| 10.1038/s41467-021-25125-1 | yes | 0.50 |
| 10.1038/s41467-024-55440-2 | yes | 0.45 |
| 10.1038/s41467-025-59997-4 | yes | 0.45 |
| 10.1038/s41586-024-08172-8 | yes | 0.55 |
| 10.1038/s44161-025-00612-6 | yes | 0.55 |
| 10.1101/2024.10.27.620502 | yes | 0.40 |

---

## Suggested split by number of volunteers

| Volunteers | Split |
|---|---|
| 1 | Group A — the rule with the most leverage |
| 2 | A · B+C+D |
| 3 | A · B+C · D |
| 4 | A split in two · B+C · D |
| 5 | A split in two · B · C · D |
