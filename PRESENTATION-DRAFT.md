# Presentation draft — manuscript-harvest and perturbation detection

**Status:** content draft, revision 3. No design, no colours, no slide layout yet.
**Audience:** colleagues who know single-cell biology but know nothing about this
project, nothing about perturbation detection as a curation task, and nothing about
Claude skills.
**Jargon rule:** no acronyms except assay names (scRNA-seq, snRNA-seq, snATAC-seq).
**Time:** 30 minutes.

**Shape — 16 slides.** Suggested pacing:

| Section | Slides | Minutes |
|---|---|---|
| Opening | 1–2 | 3 |
| Getting the text | 3–6 | 6 |
| The question and the rules | 7–8 | 4 |
| **Your logic, up for feedback** | **9** | **7** ← the point of the talk |
| What stops the machine being wrong | 10–12 | 5 |
| **It is not really about perturbations** | **13–14** | **3** |
| The queue, and the ask | 15–16 | 3 |

That totals 31 minutes with no discussion, which means you will overrun — and you
*want* to, on slide 9. **If the room engages there, drop slides 5 and 12 on the fly.**
Both are self-contained and nothing later depends on them.

> **Note on the perturbation half:** you originally asked for 4–5 slides and this is
> now 10 (slides 7–16). Every addition was one you asked for: the confidence caveat,
> the review ask, and the two generality slides. The fetch/extract half is still 4.

---

## Slide 1 — Title

**A machine that reads papers so curators do not have to read all of them**

Turning a published paper into text a computer can reason about — and then asking it
one hard biological question.

*Speaker note:* Two halves. Say up front that the second half is where you want
argument, not applause.

---

## Slide 2 — Why this exists

**Key message:** The bottleneck is not analysis. It is deciding which papers are
worth a curator's time.

- We need to know, for a large set of published papers, whether the cells that went
  into the sequencer had something done to them — a drug, a knockout, low oxygen, a
  change of diet.
- That answer decides whether a paper enters curation at all.
- A person can answer it in five to twenty minutes per paper. For hundreds of papers
  that is weeks.
- The goal is **not** a machine that guesses. It is a machine that reads everything,
  answers where the answer is solid, and hands back a short, ordered list of the
  papers where a human is genuinely needed.

*Speaker note:* Frames the talk as triage, not automation.

---

# Part one — getting the text (slides 3–6)

---

## Slide 3 — Stage one: from a paper's identifier to every file that belongs to it

**Key message:** Getting the paper is harder than it sounds, and almost every failure
disguises itself as success.

Input: the paper's permanent identifier (its DOI — the string journals assign, like
`10.1038/s41586-021-03852-1`). Output: a folder holding the article and every
supplementary file, plus a record of where each byte came from.

- Seven sources are tried in a fixed order — public archives first, publishers'
  automated download services next, and only last a real browser logged in through
  the library.
- Six of the seven need no login and open no browser, so the whole thing can run
  unattended overnight.
- Supplementary files are the point. The answer is very often in supplementary
  table 4, not in the abstract.

**Every fetch ends in exactly one of fifteen recorded outcomes.** Not "worked" and
"did not work" — because we found that the interesting failures all *look* like
success:

| | Outcome | What it means |
|---|---|---|
| **We have it** | `ok` | The article is on disk and readable |
| | `scanned_pdf_suspected` | Saved, but the pages are images with no text layer |
| **It is not the paper** | `not_research_article` | A real publication — but a correction, retraction or editorial notice |
| | `identity_unverified` | A document arrived and does not appear to be this paper |
| | `publisher_stub_page` | A plausible-looking shell served to automation instead of the article |
| **We are not allowed** | `paywalled` | Behind a subscription |
| | `not_in_oa_subset` | Not in the open-access collection |
| | `proxy_not_configured` | No library route is set up |
| | `session_expired` | The library login has died |
| **Blocked, but public** | `javascript_challenge` | Not a refusal — the file is public behind a bot-check page |
| **Nothing there, or it broke** | `link_resolver_error` | A resolver answered "no such article here" |
| | `too_large` | Declared size over the limit; refused before transferring |
| | `not_a_pdf` | What arrived is not the document type claimed |
| | `download_failed` | The transfer itself failed |
| | `not_found` | No route produced anything |

**Only the first two mean the article is on disk.**

Two of these exist because of specific incidents in this very collection, both of
which were originally recorded as complete success:

- One DOI returned a one-page **Author Correction** for a *different* paper — and
  because the correction carries its own title and identifier, every identity check
  passed on it.
- Another returned a **71-page 10x Genomics Visium user guide** from a third-party
  server, picked up as the first document-shaped link on the page. The first line of
  extracted text was `10xGenomics.com`.

*Speaker note:* Tell those two stories. They are what justify the whole fifteen-value
table, and they get a laugh. Do not read the table aloud — put it up, name the five
groups, and land on "only the first two mean we have it."

---

## Slide 4 — Stage two: from files to numbered pieces of text

**Key message:** Not one big blob of text. Small numbered pieces, each of which knows
exactly where it came from.

Each piece is one paragraph, one heading, one figure caption, or one summary of a
supplementary table. Each carries which file, where in that file, which section of
the paper, and how long it is.

Two things a single merged text file cannot do:

- **Say where a sentence came from.** Confirming a quote is word-for-word is not
  enough — a merged blob cannot tell you *which* of thirty supplementary files it
  came from. A numbered piece points at "sheet 'Patient metadata' of supplementary
  file 7." This matters enormously in part two.
- **Let you choose what to read.** Organism and sequencing kit live in Methods,
  sample counts in Results, and the Introduction is mostly other people's work.

Where the publisher provides a structured version of the article, that is used in
preference to the PDF — sections are declared rather than guessed, and tables are
real tables. Otherwise the PDF. Otherwise the saved web page. Only ever one of the
three, so no paragraph is counted twice.

*Speaker note:* The "which of thirty files" line is the setup for the quote-checking
slide. Plant it here deliberately.

---

## Slide 5 — What a supplementary spreadsheet becomes

**Key message:** A supplementary table cannot be pasted into a question. It has to be
summarised in a way that keeps what makes it useful.

One sheet in this collection is **16,596 rows by 88 columns**. Almost none of it
helps. What answers a curation question is the **column**, not the row.

So each sheet becomes a card:

```
TABLE: Supplementary Table 1
File: supplementary/01_..._MOESM1_ESM.xlsx (sheet 'Supplementary Table 1')
Caption: Detailed demographic, clinical, and disease treatment data for all patients.
Shape: 29 data rows x 35 columns; header on row 4
Columns (35):
   1. patient_code  [text, 29 distinct]   e.g. SMM5, SMM6, SMM2
   2. diagnosis     [text,  5 distinct]   = AL | MGUS | MGUS-MGRS | MM | SMM
   3. Sex           [text,  2 distinct]   = F | M
   4. Age           [number, 22 distinct] (range 40-84, median 65)
   ... 31 further columns not shown
```

- A column whose only two values are `F` and `M` answers *sex* outright.
- A column whose values are `0`, `6`, `24` is telling you the timepoints.
- Numeric columns get a lower listing threshold, because 22 patient ages say nothing
  a range does not, at ten times the length.

**The card does not copy the data.** It records exactly where the numbers live —
file, sheet, checksum, which row the header is on, first and last data row — so code
that wants real values re-opens the original at that precise offset, and says so if
the file has changed since.

Header detection is the fiddly part and worth one sentence: one real workbook puts a
title on row 1, a caption on row 2, a blank on row 3, and the actual column headers
on **row 4**. The detector only claims high confidence when the row underneath has a
different type profile — numbers sitting under text headings.

*Speaker note:* This is the slide that convinces people supplementary tables were
taken seriously rather than skipped. Worth the 90 seconds.

---

## Slide 6 — The principle both stages are built on

**Key message:** Never report an absence you cannot account for.

> An answer of "no perturbations found" is only meaningful if the record can prove
> the text was actually there to search.

- Every file gets one of thirteen outcomes — text extracted, a figure with no text,
  audio or video, a data file that is not prose, a scan read by character
  recognition, a scan that was not, corrupt, too large, no reader available, and so
  on.
- A paper is **complete** only when the main text is usable and every file that
  should have produced text did. Figures and videos carry no blame — a paper whose
  only supplements are images is still complete.
- Separately the record carries caveats: *supplementary files were expected and did
  not arrive*, *the main text is shorter than an article should be*, *the supplement
  list could not be confirmed complete*.

This is the hinge into part two. It is what lets the second half automatically refuse
to say "no" when the text was incomplete.

---

# Part two — the biological question (slides 7–16)

---

## Slide 7 — The question, and why the obvious approach fails

**Key message:** One question per paper, three answers, and one rule that makes it
genuinely hard.

> Were the samples that **actually went into the single-cell sequencer**
> experimentally perturbed — a drug, a cytokine, a knockout, low oxygen, a diet?

**yes** (curate it) · **no** (skip it) · **unclear** (a human must read it)

**Why keyword searching does not work.** Search finds "drug", search finds
"scRNA-seq", search says yes.

|  | Paper A | Paper B |
|---|---|---|
| Contains a perturbation? | yes | yes |
| Contains scRNA-seq? | yes | yes |
| **Same sample?** | **yes** | **no** |
| Correct answer | **yes** | **no** |

Papers routinely treat cells and read them out by bulk RNA sequencing, quantitative
PCR, Western blot or flow cytometry — while the single-cell dataset comes from
separate, untreated material.

**Measured across all 392 papers, current scoring round:**

- **174** papers contain a perturbation *somewhere*
- **101** have it on the *sequenced* sample — the answer we want
- **73** fail on this rule alone (67 become "no", 6 become "unclear")

> **42% of would-be positives turn on this one distinction.**

*Speaker note:* The most important slide in the deck. If they remember one thing, it
is that "perturbation in the paper" and "perturbation in the sequenced sample" are
different questions, and the gap is enormous.

---

## Slide 8 — The rules: one question, then two kinds of manipulation

**Key message:** Almost every hard case reduces to a single question about the thing
that was applied.

> **Is the applied thing what the paper is trying to LEARN ABOUT — or is it how the
> paper OBTAINED the material it then studies?**

Learning about it → **a perturbation. Report it.**
Obtaining the material with it → **the model, or the setting. Record it, do not count
it.**

When that question does not settle it, the *kind* of manipulation does:

| | **Exposure** | **Construction** |
|---|---|---|
| What it is | Something external the material was subjected to and **reacts to** | A modification that **builds** the material or the state you wanted |
| Examples | Diet, temperature, low oxygen, irradiation, a drug, a surgical injury, **an infection** | A transplant that becomes the tissue studied, a genotype the animal carries, a differentiation recipe, timed mating |
| Verdict | **Perturbation** | **The model** |

**The line between them:** does the applied thing *become* the material, or does the
material *react to* it? Transplanted tumour organoids become the tumour that gets
sequenced — construction. An inhaled virus is reacted to by the animal's own cells,
which are what gets sequenced — exposure. That is why a pathogen and a tumour graft,
both living things put into a mouse, land on opposite sides.

**An exposure needs a comparison to count.** An exposure given to every arm alike is
a constant of the protocol, not a variable. In one paper both the treated and the
control animals received the same colitis-inducing agent — so that agent is how every
animal was brought to a damaged colon, and the real contrast is carried entirely by
the genetics.

*Speaker note:* Do not read the table. Put up the governing question, let it sit, then
use pathogen-versus-graft as the illustration. That is the example that makes it
click.

---

## Slide 9 — ⚠ THIS IS WHERE I NEED YOUR FEEDBACK

**Key message, said out loud:**

> **These rules are my judgment calls, and they are the single biggest determinant of
> whether this thing performs well or badly. I am not presenting them as settled. I
> want you to tell me where I am wrong.**

Every rule below was forced by a specific paper. **Fourteen rulings are on file and
every one of them is mine** — each with its reasoning and its date. That is exactly
the problem: one person's judgment, applied to hundreds of papers at once. **A rule I
get wrong here is wrong everywhere at the same time.**

| The case | Intuition says | Current rule |
|---|---|---|
| Cells driven down a differentiation recipe; starting *and* finished cells both sequenced | perturbed | **No** — a different target cell type, not a perturbation of one |
| A western diet fed to mice to induce fatty liver disease, in a paper about liver macrophages | just how they made the model | **Yes** — the mice react to the diet |
| Tumour organoids transplanted into a mouse, surrounding tissue then sequenced | perturbed | **No** — the graft became the tissue under study |
| A mouse born carrying a disease genotype | perturbed | **No** — nothing was applied; it was born that way |
| A knockout strain, in a paper whose stated gap is *"we don't know what this gene does to this cell state"* | just a model | **Yes** — the paper is characterising the lesion |
| Children given chemotherapy between two sequenced timepoints | perturbed | **It depends — and this is the sharpest one** |

**The chemotherapy pair.** Two papers, near-identical structure, opposite answers:

- One states *"at relapse the patient undergoes a chemotherapy-driven lineage
  switch"* — the paper's own claim is that the drug changed the cells. The drug is
  the **variable**. Report it.
- The other finds a population at 0.67% before treatment and 97.6% after, and
  delivers a **before-treatment** biomarker for who will not respond. The therapy is
  the sieve that exposes something already there. It is the **setting**. Do not
  report it.

**Three traps the earlier rules fell into, and that I had to overturn:**

1. **Being the axis you group by is not being the subject.** Ask what the paper
   *concludes*, never what it *groups by*.
2. **The shape of the comparison decides nothing.** "Diseased versus healthy"
   describes both a perturbation paper and a model paper. Two earlier versions of
   these rules keyed on exactly such shapes, and both were refuted by the first two
   papers I read under them.
3. **Two manipulations in one paper get answered separately.** One can be the model
   while the other is a perturbation.

**Three questions I would most like answered today:**

- Is **"the mice react to it"** the right line between an exposure and a
  construction, or is it too clever?
- Is asking **what the paper concludes** — rather than what it measures — asking too
  much of an automated reader?
- The disease-model rule is currently the single biggest source of "no" in the
  collection. **Is it too aggressive?**

*Speaker note:* Slow down here. Seven minutes. Do not defend the rules — put them up
and stop talking. If the room pushes back, the slide worked. Naming yourself as the sole
author of all fourteen rulings is what makes the ask on slide 16 land: you are not
asking for approval, you are asking for a second opinion that does not currently
exist.

---

## Slide 10 — The machine does not get the last word

**Key message:** Three mechanisms stop the language model's answer from being the
answer — and the order they run in matters.

**The order, because it is the opposite of what people assume:**

```
   1. the model reads the paper and proposes an answer, with a
      word-for-word quote behind every single claim
                    │
   2. CODE CHECKS EVERY QUOTE against the exact file it claims to come from
      · found                        → keep
      · found in a DIFFERENT file    → keep the text, correct the attribution, flag it
      · not found anywhere           → DROP the quote
      · a perturbation left with no surviving quote → DROP it entirely
                    │
   3. THE VERDICT IS THEN RECOMPUTED from only what survived
```

**The quotes are checked mechanically, and the rules are applied *after* that, to
what is left.** The model's own verdict is not trusted once its evidence has been
pruned — a determination resting on an invented quote must not outlive the removal of
that quote.

Both values are kept — what the model said, and what the rules computed from
surviving evidence — so the gap between them is a **direct measurement of invented
evidence**. On the most recent full run: **2,337 of 2,337 quotes verified, none
unfindable, none attributed to the wrong file.**

**The verdict is computed by code, not written by the model.** The model reports
observations — is there a qualifying assay, is there a perturbation, was the perturbed
sample the one that was sequenced. A fixed rule table turns those into the answer.
That is why the rules can be tested exhaustively: every possible combination of
inputs, thousands of them, in a fraction of a second. You cannot do that to an
opinion.

**Incomplete text can never produce a confident "no."** If the text was truncated or
the Methods never arrived, a "no" is automatically raised to "unclear" and routed back
for re-fetching. A "yes" is *not* capped, deliberately: missing text can hide the
sentence that would have proved a pairing, but it cannot invent one.

**And the model must record what it decided *not* to count**, and why, from a fixed
list of reasons. Otherwise a record showing no perturbation cannot distinguish *"I
considered the fluorescent tag and excluded it under the labelling rule"* from *"I
never noticed it."*

*Speaker note:* Say "recomputed" twice. If anyone asks whether the model could just
lie convincingly — yes, and that is exactly what the model-versus-computed gap
measures.

---

## Slide 11 — ⚠ What the confidence number means, and what it does NOT

**Key message:** There is a confidence score on every answer. It is the most
misreadable number in the whole output, and I want to be blunt about it.

> **It is NOT the probability that the paper is perturbed.**

It answers exactly one question:

> *How likely is it that a careful human curator, reading this same text, would
> assign the value I assigned?*

That definition has consequences people find counter-intuitive:

- A **well-evidenced "no" scores HIGH.** A high number next to "no" does not mean
  "probably not perturbed" — it means "a curator would also say no."
- A **coin-flip "yes" scores LOW.** The number is about agreement, not about the
  biology.
- On an **"unclear"**, it means *how sure I am that this genuinely cannot be resolved
  from this text* — not how likely a hidden "yes" is.
- **Zero is not a score.** It is a marker meaning "nothing was assessed" — a failed
  extraction. Read literally as a score it would be nearly 1.0, since any curator
  would agree that an access-denied page settles nothing.

**And now the part that should stop anyone from filtering on it.** Re-running the
*unchanged* system on *byte-identical text* moves this number a long way:

| Paper | Confidence, run 1 → run 2 |
|---|---|
| one | 0.35 → 0.90 |
| two | 0.45 → 0.85 |
| three | 0.35 → 0.88 |
| four | 0.45 → 0.90 |
| five | 0.30 → 0.88 |

Same words in, very different number out. We verified the input was identical — same
checksum on the assembled text, same instruction files, nothing dropped for length in
either pass.

> **So: use it to sort a reading queue. Never use it as a threshold, and never quote
> it as a probability.**

*Speaker note:* You asked for this to be stressed — this is a whole slide rather than
a bullet for that reason. The five-row table is the argument. Everything above it is
definitional; the table is empirical.

---

## Slide 12 — How we know it works, and the number that changed how we measure

**Key message:** Four layers of checking — and one uncomfortable finding that
reframes all of them.

**The uncomfortable finding first.** Run the *unchanged* system twice on the *same
papers* and it disagrees with itself on **3 of 50 — 94% stable, not 100%.**

```
   New version, run 1:  4 papers moved      "it moved 4 papers!"
   New version, run 2:  1 paper moved       ...same change. Four-fold swing.
                        ─────────────────
   3 of the 4 were inside the noise.
```

A single run can no longer be used to argue that a rule change did anything. This is
enforced by the tooling, not by good intentions: hand it one run and it refuses to
draw the conclusion.

**The four layers:**

1. **The rule table is proved** — every possible input combination enumerated and
   tested, thousands of them, in under a tenth of a second.
2. **Every quote is re-found** — continuous, and needs no ground truth to run.
3. **A human rules, and the ruling is filed** — fourteen so far, all mine, each with
   reasoning and date, in a ledger kept separate from the rules. The ledger is
   separate on purpose: rules do not preserve the judgments they came from.
4. **Every rule change must beat the noise floor** — two runs, against a two-run
   baseline.

**A thirty-paper blind evaluation**, randomly drawn with no overlap with any set the
rules were developed on:

- 28 of 30 answers unchanged from the previous version
- 2 of 30 changed — both the new version *correcting* the old one
- 30 of 30 correct after I ruled on the two disputes
- 38 of 38 exclusions correct

*Speaker note:* Lead with the noise floor. It is the thing that most distinguishes
this from a demo.

---

## Slide 13 — Only the top layer is about perturbations

**Key message:** Perturbation detection is one question plugged into a machine that
does not know what a perturbation is.

The whole thing is three layers, and the task-specific content is confined to the
top one:

```
 ┌──────────────────────────────────────────────────────────────┐
 │  JUDGMENT     what counts · how to decide · what to read     │  ← SWAP
 │               first · what counts as a change                │
 │               (the rules from slide 9 live here, and         │
 │                nowhere else)                                 │
 ├──────────────────────────────────────────────────────────────┤
 │  CHECKING     assemble the sources · ask the model once ·    │  ← KEEP
 │               verify every quote · drop what fails ·         │
 │               recompute the verdict · tabulate · compare     │
 │               runs                                           │
 ├──────────────────────────────────────────────────────────────┤
 │  TEXT         identifier → article + attachments → labelled  │  ← KEEP
 │               pieces of text with provenance                 │
 │               (slides 3–6. No model involved at all)          │
 └──────────────────────────────────────────────────────────────┘
```

**This is enforced, not merely intended.** The checking layer is about 1,700 lines
that name this task **nowhere in the code** — a test reads every module word by word
and fails the build if a task word appears in any name, string or key.

It was not born that way. The task content started scattered through four files —
one was 80% perturbation-specific, another 71%, another 60%. Pulling it out into a
separate rulebook changed **nothing**: all 392 records re-scored with zero
differences, and three of the output files came out byte-for-byte identical. That
null result is the only real evidence that a rearrangement of that size was in fact
a rearrangement.

*Speaker note:* The point of the byte-identical result is that it is the *only*
honest way to prove a refactor did not quietly change behaviour. Say it that way.

---

## Slide 14 — And it has already been swapped

**Key message:** A second, completely different question runs on the same 392 papers
through byte-identical machinery.

> **"Which tissue did the sequenced material come from, and does the paper state it
> explicitly?"**

```
 paper: "Nasal brushings and bronchial biopsies were collected …
         scRNA-seq was performed on the bronchial biopsies.
         Reference lung atlas data were downloaded for annotation."

 out:   tissue profiled     : bronchial mucosa        paired: yes
                              quote: "scRNA-seq was performed on
                                      the bronchial biopsies"
        considered, excluded: nasal epithelium → collected, not profiled
                              lung atlas      → someone else's data, reused
        read-first priority : 1  (a second tissue was collected and its
                                   pairing was never stated)
```

**What it cost:** about 1,000 lines of new rules, against roughly 1,700 lines of
checking machinery that was **not touched at all**.

**What carried over untouched** — and this is the part that generalises:

- every quote verified against the file it claims
- the pairing requirement: *the attribute must attach to the thing actually
  measured, not merely co-occur in the paper*
- the ledger of what was considered and excluded, and why
- incomplete text can never produce a confident negative
- the read-first queue, and the noise-floor discipline

**Honest caveat:** the first attempt did not run at all. Five separate assumptions
about "the question" had leaked into the machinery — the worst of which turned a
rulebook that could not even load into *"nothing to do, every paper already has a
result"* and reported success. None of the existing tests caught any of the five.
The seam is real **because** it was swapped and the swap broke; a seam nobody has
crossed is a claim, not a fact.

**So what else could this answer?** Any question of the form *"does attribute X
attach to the material that was actually measured?"* — tissue, organism, donor
count, disease state, sequencing platform, sample preparation. Co-occurrence in the
paper is the default failure mode of every literature-mining question, and that is
the exact failure this machine is built around.

*Speaker note:* This is the slide that makes the work matter beyond one curation
task. Land the last paragraph slowly. If someone in the room has their own question,
that is the best possible outcome of this talk — get their question written down
before they leave.

---

## Slide 15 — The output is a reading queue, not a verdict list

**Key message:** 392 papers, sorted so the ones most likely to be wrong are at the
top.

| Tier | Papers | What it is |
|---|---:|---|
| 1 | 4 | The pairing was never stated — likeliest to hide a real match. **Read first** |
| 2 | 106 | One exclusion rule away from flipping — *ratify the rule, not the paper* |
| 3 | 7 | A "yes" the machine itself doubts |
| 4 | 11 | Text was incomplete → send back for re-fetching, do **not** read |
| 5 | 35 | Perturbation exists but not on the sequenced sample → spot-check |
| 7 | 43 | Probably right, possibly out of scope (the pairing is animal-only) |
| 9 | 186 | Nothing to look at |

Where the whole collection currently stands: **101 yes · 19 unclear · 272 no.**

One finding worth a sentence, because it is about the biology rather than the
software: two of my own reversals turned out to have a cause nobody had named — the
paper was scored yes on the strength of a *mouse* experiment while its human data was
purely observational. The record now says **whose** sample was perturbed. Of the 101 "yes"
papers: **55 human, 46 non-human only.** Recorded, deliberately not acted on — a
non-human dataset can be a perfectly legitimate curation target.

*Speaker note:* Tier 2 is the one to point at. 106 papers hang on rule decisions, not
on paper-by-paper reading — which is exactly why slide 9 matters and why the ask on
the next slide is shaped the way it is.

---

## Slide 16 — What I am asking for

**Key message:** A pool of 24 papers that need a human ruling. I do not yet know how
many of you can help — tell me, and I will divide them up.

> **24 papers · 10–20 minutes each · every one of them changes a rule, not a row**

**Why these 24.** Right now every rule on slide 9 rests on my judgment alone. These
are the papers where a second opinion changes the *rulebook*:

- **13** of them changed their answer under the newest rules, and **nobody but me has
  ever looked at them.**
- **8** of those 13 turn on the single rule that produces more "no" answers than any
  other in the collection.
- The rest are where the machine is least sure of itself.

**What I need back, per paper — three lines, not an essay:**

1. **yes / no / unclear** — your call
2. **one sentence of why**, in your own words
3. **which rule it turns on**, if you can name it

**How this will work:** tell me if you can take a batch. I will split the pool by how
many volunteers there are and send each of you a short list, grouped so that everyone
is answering one question rather than picking through unrelated papers. No deadline —
this is worth more done properly than done quickly.

*Speaker note:* Do not put 24 identifiers on the slide. Put up the "why these 24"
block, make the ask, and hand out the appendix. Close on: *"a ruling from you is
worth more than another version from me."*

---

# Appendix — the review pool *(not a slide; hand out or email)*

24 papers from the current 392-paper scoring round, grouped by **which rule a ruling
would settle**. Groups are the natural unit to hand to one person — they are not
pre-assigned.

**For each paper, please record:** your call (yes / no / unclear), one sentence of
reasoning, and the rule you think it turns on. No deadline.

**A word on the confidence numbers below.** They do **not** mean "probability the
paper is perturbed" — see slide 11. A high number next to "no" means *I expect a
curator to agree with the no*. Those are the interesting ones to challenge.

---

### Group A — is the disease-model rule right? *(8 papers)*

**The rule under test:** a manipulation whose purpose is to give the samples the
disease state the paper studies is **the model, not a perturbation**.

**Your question for each paper:** was the manipulation how they *obtained* the
diseased material, or is it what the paper is *studying*?

**Why this group matters most:** this rule produces more "no" answers than any other
in the collection, and it has never been checked at this scale.

| DOI | Machine says | Its confidence |
|---|---|---|
| `10.1016/j.ccell.2025.12.003` | no | 0.70 |
| `10.1016/j.cell.2021.11.031` | no | 0.60 |
| `10.1016/j.immuni.2022.09.002` | no | 0.55 |
| `10.1016/j.isci.2022.104097` | no | 0.80 |
| `10.1038/s41590-023-01584-0` | no | 0.60 |
| `10.1038/s42003-021-02562-8` | no | 0.72 |
| `10.1126/science.aay3224` | no | 0.88 |
| `10.3389/fimmu.2023.1211505` | no | 0.85 |

*Splits cleanly into two batches of four if two people take it.*

### Group B — are the other exclusion rules right? *(5 papers)*

**The rules under test:** a clinical therapy as the study's *setting* rather than its
variable; a differentiation or derivation recipe as the model; a naturally occurring
disease state as observational rather than applied.

| DOI | Machine says | Its confidence |
|---|---|---|
| `10.1016/j.healun.2026.02.1666` | no | 0.60 |
| `10.1016/j.molmet.2023.101746` | no | 0.72 |
| `10.1182/bloodadvances.2023011445` | no | 0.85 |
| `10.1186/s12943-025-02430-7` | no | 0.50 |
| `10.64898/2025.12.18.695268` | no | 0.93 |

### Group C — the pairing was never stated *(4 papers)*

**Not a rule under test.** These are the four papers most likely to be hiding a real
match: a perturbation and a qualifying assay both exist, and the text never says
whether they met.

**Your question:** can you find the link in the paper that the machine could not?

| DOI | Machine says | Its confidence |
|---|---|---|
| `10.1016/j.coi.2022.102188` | unclear | 0.55 |
| `10.1016/j.immuni.2020.03.019` | unclear | 0.45 |
| `10.1038/s41586-021-03852-1` | unclear | 0.55 |
| `10.1164/rccm.202207-1384oc` | unclear | 0.45 |

### Group D — the machine's least-confident "yes" calls *(7 papers)*

**Not a rule under test.** Every one of these is a "yes" the machine itself scored
below 0.6.

**Your question:** is the call right — and if it is, is the hesitation justified?

| DOI | Machine says | Its confidence |
|---|---|---|
| `10.1016/j.cell.2021.07.023` | yes | 0.40 |
| `10.1038/s41467-021-25125-1` | yes | 0.50 |
| `10.1038/s41467-024-55440-2` | yes | 0.45 |
| `10.1038/s41467-025-59997-4` | yes | 0.45 |
| `10.1038/s41586-024-08172-8` | yes | 0.55 |
| `10.1038/s44161-025-00612-6` | yes | 0.55 |
| `10.1101/2024.10.27.620502` | yes | 0.40 |

---

**Coverage if you get N volunteers:**

| Volunteers | Suggested split |
|---|---|
| 2 | Group A to one, Groups B+C+D to the other (8 / 16) |
| 3 | A / B+C / D (8 / 9 / 7) |
| 4 | A split in two / B+C / D (4 / 4 / 9 / 7) |
| 5 | A split in two / B / C / D (4 / 4 / 5 / 4 / 7) |

If only one person can help, **give them Group A.** It is the rule with the most
leverage over the collection.

---

# Notes for you, not for the deck

## What changed in revision 3

| Your instruction | What I did |
|---|---|
| Rulings so far are mine (Idan) | Attributed by name on slides 9, 12 and 15 — and slide 9 now uses it as the *argument*: fourteen rulings, one person, and that is the problem |
| Identifiers are fine to show | All 24 shown in full in the appendix; still kept off the slide itself, for legibility rather than privacy |
| Unknown number of helpers — need a pool | Slide 16 and the appendix are now a **pool of 24 in four groups**, with a split table for 2–5 volunteers and a fallback if only one person helps |
| A couple of slides on generality | New slides 13 and 14: the three-layer split, and the second question already running through byte-identical machinery |
| No deadline | Removed everywhere; slide 16 says so explicitly |

## What changed in revision 2 *(kept for reference)*

The 15 fetch outcomes as a full table; slide 5 promoted to core; slide 9 rewritten as
the centrepiece; the confidence caveat given its own slide; the quote-checking order
corrected; all figures re-derived from the current scoring round.

## The correction from revision 2, restated

You asked whether the quotes are checked *after* the rules are applied. **It is the
other way round:** the model proposes an answer with a quote behind every claim →
code mechanically checks every quote against the file it names → failing quotes are
dropped, and any perturbation left with no surviving quote is dropped whole → **then
the rules are re-applied to what survived.** Slide 10 shows this as a three-step
diagram, and slide 14 lists it as one of the things that carried over unchanged to
the second question.

## Numbers — all from one round

Every figure comes from the current 392-paper scoring round: 174 papers with a
perturbation somewhere, 101 on the sequenced sample, 73 failing on the pairing rule
alone (42%), 2,337 quotes verified with none failing, and 101 yes / 19 unclear /
272 no.

## Still open

1. **Do you want a slide naming what this could answer next?** Slide 14 ends with a
   list — tissue, organism, donor count, disease state, platform — but if there is a
   *specific* second question you actually intend to build, that deserves to be named
   rather than left as a category.
2. **Slide 12 or slides 13–14, if you have to cut?** My view: keep the generality
   slides. The noise-floor point on 12 is the more rigorous one, but 13–14 is what
   makes people want to use this.
3. **Should the appendix say who to send rulings back to, and how?** Right now it
   says what to record but not where to put it.
