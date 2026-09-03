# Finding perturbed single-cell papers

**How a language model is used to read 392 papers, how we check that it read them
right, and what of this transfers to any other question you want to ask a corpus.**

Plain language. Diagrams over paragraphs. Every number in here was measured, not estimated.

---

## 1 · The question

One question, one answer per paper.

```
   a published paper                             yes      ← curate it
  ┌────────────────────┐                       /
  │ article + all its  │ ──────────────▶  no  ─── ← skip it
  │ supplementary files│                       \
  └────────────────────┘                         unclear  ← a human must read it
```

> Were the samples that **actually went into the single-cell sequencer**
> experimentally perturbed — a drug, a cytokine, a knockout, hypoxia, a diet?

---

## 2 · Why this is hard: the one rule

A keyword search finds "drug" and finds "scRNA-seq" and says yes. That is wrong
about a third of the time.

```
        PAPER A                                 PAPER B
  ┌──────────────────────┐              ┌──────────────────────────┐
  │  drug ▶ cells ▶ 🧬   │  scRNA-seq   │  drug ▶ cells ▶ 📊       │  qPCR
  └──────────────────────┘              │  untreated cells ▶ 🧬    │  scRNA-seq
                                        └──────────────────────────┘

   perturbation?   ✔                       perturbation?   ✔
   single-cell?    ✔                       single-cell?    ✔
   SAME SAMPLE?    ✔                       SAME SAMPLE?    ✘

        → YES                                   → NO
```

Papers routinely treat cells for a bulk-RNA / qPCR / Western readout, while the
single-cell dataset comes from *separate, untreated* samples.

**Measured on all 392 papers:**

```
  186  papers contain a perturbation somewhere
  115  have it on the sequenced sample            ← the answer we want
   71  fail only this rule  (66 → no, 5 → unclear)

  38% of would-be positives turn on this single rule.
```

---

## 3 · The whole machine

The model touches **exactly one box**. Everything else is ordinary code that can
be re-run for free.

```
 DOI list
    │
    ▼
 FETCH      try 7 sources in order → article + every attachment
    │       (records what it did NOT get, and why)
    ▼
 EXTRACT    → the paper as labelled pieces: each paragraph, heading, caption,
    │          tagged with which file it came from
    ▼
 ① PREPARE  build ONE self-contained file per paper:
    │         the rules  +  the blank answer form  +  the paper itself
    ▼
 ╔═══════════════════════════════════════════════════════════════╗
 ║  ② READ      ◀── the only step that uses a model              ║
 ║              one private conversation per paper               ║
 ║              in:  that one file                               ║
 ║              out: one filled-in form + a quote per finding    ║
 ╚═══════════════════════════════════════════════════════════════╝
    │
    ▼
 ③ CHECK     re-find every quote in the exact bytes we sent
    │        then RE-DERIVE the verdict in code — the model's answer is input,
    ▼        not output
 ④ SORT      one row per paper, most-likely-to-be-wrong first
    │
    ├──▶  a spreadsheet (392 rows, read top-down)
    ├──▶  six review screens ("what would we have gotten wrong?")
    └──▶  written back beside each paper, as its permanent record
                    │
                    ▼
              CURATOR reads → writes a ruling → rules updated → re-run   ⟲
```

**No API key.** Step ② shells out to `claude -p` — the same logged-in Claude Code
session a person uses — one call per paper, model pinned to `claude-opus-5` so
results stay attributable across machines and months.

---

## 4 · What the model is asked

Not "is this paper perturbed?". A 588-line specification — about 53,000
characters of instruction in front of every paper — that walks the model through
four steps.

```
 STEP 0   Is this text usable at all?
          ok / partial / failed  ·  full / truncated / methods missing

 STEP 1   Which single-cell or single-nucleus SEQUENCING assays are here?
          ⚠ trap: "cells were dissociated into a single-cell suspension"
            is a dissociation step, not an assay

 STEP 2   Which manipulations are PERTURBATIONS?
          ✔ drugs · cytokines · stimulation · knockout/knockdown · CRISPR
            hypoxia · irradiation · diet
          ✘ buffers · fixatives · stains · culture media · dissociation enzymes
            freezing DMSO · library kits · GFP reporters · selection markers
          ⚠ the same molecule is a buffer in one paper and the subject of
            another → judge the ROLE, not the presence of the word

 STEP 3   For each perturbation: was THAT sample the one that got sequenced?
          yes / no / unclear   — each with its own verbatim quote
```

**One design choice worth stealing.** The confidence number does *not* mean "how
likely is this paper perturbed". It means:

> *How likely is it that a careful human curator, reading this same text, would
> write down what I just wrote down?*

So a well-evidenced **no** scores high, and a coin-flip **yes** scores low. It is
a measure of agreement, not of enthusiasm — which is the thing you can actually
check later.

---

## 5 · What comes back

A filled form. Every claim carries a quote, and every quote carries the name of
the file it came from.

```
 perturbation_present :  yes
 assays               :  ["sci-Plex", "sci-RNA-seq2"]

 perturbations[0]
   agent      : dexamethasone, nutlin-3a, BMS-345541, vorinostat
   target     : glucocorticoid receptor / p53–Mdm2 / NF-κB / HDACs
   detail     : 24 h, seven doses in triplicate, 84 combinations + vehicle
   paired     : yes          ← the treated cells ARE the sequenced cells
   organism   : human
   quote      : "We exposed A549 … to one of four compounds …"   [from: main]
   quote      : "Cells were then exposed to …"                   [from: supp1]
```

That "[from: main]" tag is the load-bearing part of the whole design. Keep reading.

---

## 6 · The model does not get the last word

Plain quote-checking asks *"is this sentence real?"*. Because every quote is
tagged with its source file, we ask the harder question: **"is it in the file you
said it was in?"**

```
 model says: YES
   └─ perturbation "IL-2"                            paired: yes
        quote → "IL-2-treated cells were profiled     claims: supp1
                 by scRNA-seq"

 harness:   look in supp1 … not there
            look in main  … not there
            ✘ quote deleted
            ✘ perturbation now has no evidence → dropped entirely
            ▶ re-run the checklist on what SURVIVED   →   UNCLEAR

 recorded:  model said YES  ·  evidence supported UNCLEAR  ·  changed by harness
```

Two different failures, two different fixes:

```
  found in the WRONG file  →  a filing error.    Fix the label, keep the finding.
  found NOWHERE            →  a fabrication.     Delete it, recompute the verdict.
```

Both answers are kept side by side — *what the model said* and *what the evidence
supported*. The gap between them is a standing fabrication meter that needs no
ground truth at all.

**Measured:**

```
  this full-corpus run      2,471 quotes checked · 0 unfindable · 0 misattributed
  every run ever recorded   4,597 quotes checked · 0 unfindable · 4 misattributed
  perturbations dropped for lack of evidence:  0
  verdicts the harness had to overrule:        1 of 392
```

That is a *result*, not a formality — and the check is the only reason we get to
state it. Quotes are matched allowing for characters mangled by PDF extraction
(a degree sign arriving as a control character should not count as a lie).

---

## 7 · The verdict is computed, not generated

The model reports **findings**. A fixed 7-line checklist, written in ordinary
Python, turns findings into the answer. Same inputs → same answer, every time.

```
 A0  text was unusable?                             → unclear
 A1  no perturbations found at all?                 → no  (or unclear)
 A2  no qualifying single-cell assay in the paper?  → no
 A3  assay present but unconfirmable?               → unclear
 A4  ANY perturbation paired "yes"?                 → YES     ← one is enough
 A5  none "yes", but some "unclear"?                → unclear ← recall first
 A6  every perturbation paired "no"?                → no
```

Then one asymmetric safety rule:

```
             text was incomplete
                     │
        ┌────────────┴────────────┐
   verdict "no"              verdict "yes"
        │                         │
        ▼                         ▼
    → UNCLEAR                 unchanged
   (send to re-fetch)

   Missing text can HIDE the sentence that would have made this a yes.
   It can never INVENT one.
```

9 of 392 papers were caught by that cap and routed back to fetching rather than
to a reader.

---

## 8 · Write down what you decided *not* to count

Before this existed, a "no" was indistinguishable from "never noticed". So the
form has a required field: everything the model recognised as a possible
perturbation and deliberately excluded — with the reason, from a **closed list of
eight**, plus a verbatim quote and one counterfactual.

```
 suppressed_candidates[0]
   candidate         : SFTPC-GFP reporter line
   rule              : reporter_or_marker
   why               : a readout of expression, not a manipulation of it;
                       the Methods never place it in the sequenced material
   quote             : null    ← legitimate: the exclusion rests on SILENCE,
                                 and silence cannot be quoted
   would_have_paired : unclear ← "would this have counted, if the rule differed?"
```

Closed list, because the point is to be able to **count**:

```
  observational_disease_state   262  ← tumour vs adjacent normal
  incidental_clinical_therapy    93
  reporter_or_marker             90
  derivation_formulation         83
  sample_handling_protocol       73
  readout_reagent                61
  routine_processing             28
  unintended_condition           12
  ─────────────────────────────────
  702 exclusions across 351 papers
```

"Why did this say no?" went from hand-reading nine papers to a spreadsheet query.
And it surfaced something nobody knew: **83 papers (21%) sit one rule change away
from flipping.**

Structural guarantee, not a convention: an exclusion can never move a verdict.
The checklist in §7 physically cannot see this field, and a test proves it over
every possible input.

---

## 9 · The output is a reading queue, not a verdict list

392 rows, sorted so that the papers most likely to be wrong are at the top. Tiers
are never renumbered once published, or the column stops being comparable across
versions.

```
 P1    4  the pairing was never stated — likeliest to hide a real match  READ FIRST
 P2   83  one exclusion rule away from flipping   ← ratify the RULE, not the paper
 P3   15  a "yes" the model itself doubts
 P4    8  text was incomplete → send back to fetching, do NOT read
 P5   36  perturbation exists but not on the sequenced sample → spot-check
 P7   46  probably right, possibly out of scope (animal-only pairing)
 P9  200  nothing to look at
      ───
      392 papers · ~150 worth a human minute · in a defined order
```

Alongside it, six **screens** — deliberately not verdicts, but questions:
pairing flips, possibly-missed assays, possibly-missed perturbations, the
re-fetch queue, supplementary-only evidence, and every exclusion the rules
swallowed.

---

## 10 · How do we know it works

Four rings, cheapest and hardest first.

```
 ┌──────────────────────────────────────────────────────────────────────┐
 │ ① THE RULEBOOK IS PROVED                                            │
 │    74 automated tests · 0.07 seconds · every input combination       │
 │    enumerated (4,320 in the totality test alone).                    │
 │    Not "spot-checked" — exhaustive. This is only possible because    │
 │    the verdict is code, not a model.                                 │
 ├──────────────────────────────────────────────────────────────────────┤
 │ ② EVERY QUOTE IS RE-FOUND                                           │
 │    4,597 checked, 0 unfindable. Continuous, no ground truth needed.  │
 ├──────────────────────────────────────────────────────────────────────┤
 │ ③ A HUMAN CURATOR RULES, AND THE RULING IS FILED                    │
 │    5 papers read end to end. The curator REVERSED 2 of them.         │
 │    Each ruling recorded with its reasoning and date, in a ledger      │
 │    kept separate from the rules.                                     │
 ├──────────────────────────────────────────────────────────────────────┤
 │ ④ EVERY RULE CHANGE MUST BEAT THE NOISE FLOOR   ← see §11            │
 │    Run the new version twice. Compare against a TWO-run baseline.    │
 └──────────────────────────────────────────────────────────────────────┘
```

**The 30-paper blind evaluation** (randomly drawn, zero overlap with any set the
rules were developed on):

```
  28 / 30  answers unchanged from the previous version
   2 / 30  changed — and both changes were the new version CORRECTING the old one
  30 / 30  correct after the curator ruled on the two disputes
  38 / 38  exclusions correct
```

---

## 11 · The most important idea here: the noise floor

Run the **unchanged** system twice on the **same words**. It disagrees with
itself.

```
  same prompt · same papers · run twice   →  disagrees on 3 of 50  (94% stable)
```

That number changes what "an improvement" means.

```
   NEW VERSION, RUN 1  :  4 papers moved     "it moved 4 papers!"
   NEW VERSION, RUN 2  :  1 paper moved      ...same change. 4× swing.
                          ────────────────
   3 of the 4 were inside the noise floor.

   REPORTED AS: 1 paper moved beyond noise.  The other 3: WITHIN NOISE.
```

Enforced in code, not in good intentions:

```
  ✘  one-run baseline    →  the tool SAYS SO: "a single-paper movement cannot
                            be told from run-to-run variance"  (not "no effect"
                            — "cannot tell", which is a different claim)
  ✘  two runs of DIFFERENT versions handed in as a replicate pair
                         →  hard refusal, exit 2, writes nothing.
                            That misuse launders a real effect into "nothing
                            moved" — the exact inversion the check exists for.
                            Its author made this mistake on first use.
```

### The companion finding: a new field changes old answers

Adding the "what did you exclude" field of §8 — **with no criterion edited** —
flipped 2 of 6 papers from yes to no and emptied their findings entirely.

> Making a path structured makes it more travelled.

Four guards fixed it, and they generalize to any new field:

```
  1. State precedence FIRST — the new field never shortens the answer.
  2. Say plainly that empty is normal and common.
  3. NAME the negative examples. ("Don't list ambient reagents" was ignored
     twice in one paper. The concrete list held.)
  4. Hold every sub-judgment to its parent's evidence standard.
  + Add a test proving the new field cannot reach the verdict at all.
```

---

## 12 · A finding about the biology, not the software

Two of the curator's reversals had one cause nobody had named: the paper was
scored **yes** on the strength of a *mouse* experiment, while its human data was
purely observational.

```
  a mouse knockout           → sequenced   ✔ perturbed
  a human tissue atlas       → sequenced   ✘ observational
  ────────────────────────────────────────────────────────
  old answer: YES (from the mouse)
  what reaches the curated deposit: the human data
```

Both had been flagged in review as the least trustworthy calls in the set —
*without anyone spotting the shared reason.* One gap, not two judgments.

So the record now says **whose** sample was perturbed. At corpus scale:

```
  of 115 "yes" papers:   62 human   ·   52 non-human only   ·   1 not stated
```

Two deliberate choices:

- **Recorded, not acted on.** A non-human dataset can be a legitimate curation
  target, and the paper usually cannot tell you which species was actually
  deposited. *Recording is answerable; filtering is not.*
- **Three states, not two.** human / not-human / **not stated** — so an unknown
  never reads as a confident "not human".

---

## 13 · One worked example, start to finish

> **Fake paper: "A single-cell atlas of human lung organoids"**
>
> Methods:
> 1. "Organoid lines were dissociated with collagenase into a **single-cell suspension** and cryopreserved in DMSO."
> 2. "Four **untreated** organoid lines were profiled by **10x scRNA-seq**."
> 3. "Organoids were treated with **10 ng/mL TNF for 24 h**; response measured by **qPCR and Western**."
> 4. "**SFTPC-GFP** reporter lines were generated to sort alveolar cells."
> 5. "Reference 14: Smith et al., TNF signalling in lung…"
>
> Supplement `mmc1.pdf`: a full copy of the Methods, plus one new sentence —
> "Dose–response: 1, 10, 100 ng/mL TNF were tested."

```
 ① PREPARE
    · line 5 (references) dropped — never evidence
    · the reagent catalogue table dropped — it lists every reagent in the lab,
      and this task turns on a reagent's ROLE, not its presence
    · the supplement is ~90% a word-for-word copy → duplicate paragraphs deleted;
      the surviving dose–response sentence keeps it alive as `supp1`
    → one file, two labelled sources: main, supp1

 ② READ — the four traps
    · "single-cell suspension" (line 1)  = a dissociation step, NOT an assay
    · collagenase, DMSO                  = sample handling, NOT treatments
    · SFTPC-GFP (line 4)                 = a readout OF expression, not a
                                           manipulation of it — and the Methods
                                           never say the sequenced lines carried
                                           it. Asserting it would invent a fact
                                           out of silence.
    · TNF (line 3)                       = a REAL perturbation …
                                           … read out by qPCR and Western.
                                           The sequenced lines are the UNTREATED
                                           ones (line 2).

 ③ THE RECORD
    perturbation_present            : no
    perturbation_present_any_assay  : yes   ← so the rule's cost stays countable
    perturbations[0] TNF            : paired = no   (quote from main)
    suppressed_candidates           : SFTPC-GFP → reporter_or_marker, quote null
                                      collagenase/DMSO → routine_processing
    triage_priority                 : 5     (spot-check tier)

 ④ THE TWIST — suppose the model had answered YES, quoting
    "TNF-treated organoids were profiled by scRNA-seq"  [claims: supp1]
      · look in supp1 … absent      · look in main … absent
      · quote deleted → perturbation dropped → checklist re-run → UNCLEAR
      · both answers filed: "model said yes / evidence said unclear"

    A keyword search calls this paper YES. This pipeline calls it NO,
    and can show you the sentence it used to decide.
```

---

## 14 · Doing this for a different question

Three layers. Only the top one is about perturbations.

```
 ┌───────────────────────────────────────────────────────────────┐
 │  JUDGMENT           the spec + four lookup tables             │  SWAP
 │                     "what counts, how to decide, what to      │
 │                      read first, what counts as a change"     │
 ├───────────────────────────────────────────────────────────────┤
 │  PLUMBING           assemble sources · splice the prompt ·    │  KEEP
 │                     one call per paper · verify every quote · │
 │                     prune · recompute · tabulate · diff       │
 │                     (1,697 lines that name this task NOWHERE  │
 │                      in code — a test holds that line)        │
 ├───────────────────────────────────────────────────────────────┤
 │  TEXT               DOI → article + attachments → labelled    │  KEEP
 │                     pieces of text with provenance            │
 │                     (no model client, no task content at all) │
 └───────────────────────────────────────────────────────────────┘
```

**As of pack 0.0.13 this is the tree, not an aspiration.** The judgment is
`task/`; the harness is `pe/`. It was not always: `pe/` was 1,038 task lines to
1,185 generic ones, interleaved inside four files — `audit.py` 80% task,
`summarize.py` 71%, `validate.py` 60%. Only `paper_text.py` and `prepare.py` were
already clean, and they were the evidence the seam existed to be found.

| the four tables | what it holds | was |
|---|---|---|
| `task/record.yaml` | what counts | 9 closed sets across two modules |
| `task/decide.yaml` | how to decide | the cap rule and CC-1..CC-7 in `validate.py` |
| `task/report.yaml` | what to read first | the ladder written twice, 44 columns written twice, the keyword banks |
| `task/change.yaml` | what counts as a change | 12 class labels in `compare.py` |

Moving them changed nothing: all 392 records re-validated with **0 differing**
beyond the pack hash, and the summary CSV, the 5,836-line review screen and the
version diff came out byte-identical.

**And it has since been swapped.** A second pack — "which tissue did the
sequenced material come from, and does the paper state it explicitly?" — runs on
this corpus through a byte-identical `pe/`. It cost 155 lines of spec, 279 of
tables and 588 of rule modules, against 1,697 lines of harness it did not touch.
Getting there took five fixes, because the first attempt did not run at all: the
harness assumed every pack has a considered-and-rejected array, printed prose
naming a change class only this pack declares, and — worst — turned a pack that
could not be imported into "nothing to do … every paper already has a result",
exit 0. None of the tests written to prevent exactly that caught any of them. `tests/test_seam.py` tokenises every module
in `pe/` and fails on a task word in any identifier, string or key — comments and
docstrings exempt, because half the value here is the record of which DOI taught
which rule, and forcing that history out of the harness would trade the thing
worth keeping for a tidier grep.

One thing deliberately did **not** become a table. This document's own diagram
says "four lookup tables", and Stage A — the ordered rules that turn evidence
into a verdict — is published in the spec as a 10-row truth table, so it looked
like a fifth. It is a function instead, in `task/rules.py`. A table needs a
predicate vocabulary; that vocabulary is a guess until a second question has a
decide step to draw it from; and Stage A is precisely the piece expected to be
shaped differently per question. Inventing an expression language for one
instance buys a generality nobody can check. Lists and messages are data, rules
are four functions, and the reasoning is written into `decide.yaml` so the next
person does not re-derive it.

Swap the top layer and the same machine answers a different question:

```
 QUESTION: "what tissue did the single-cell assay actually sample?"

 paper: "Nasal brushings and bronchial biopsies were collected …
         scRNA-seq was performed on the bronchial biopsies.
         Reference lung atlas data (GSE1234) were downloaded for annotation."

 out:  tissues_profiled      : bronchial mucosa   paired: yes
                               quote: "scRNA-seq was performed on the
                                       bronchial biopsies"      [main]
       suppressed_candidates : nasal epithelium → collected, not profiled
                               lung atlas       → reused public dataset
       triage_priority       : 1   (a second tissue was collected and its
                                    pairing was never stated)

 carried over untouched: source labelling · quote verification against the
 named file · the pairing requirement · the considered-and-excluded ledger

 what genuinely breaks: the answer is now a SET, not one value — so
 "did run 1 equal run 2?" stops being a string comparison, and the noise
 floor needs set similarity. That is the real rewrite.
```

### The transferable recipe

```
 1  ONE FILE IS THE SPEC.  Criteria, vocabulary, answer form and decision
    logic in one versioned document. The runbook only says how to run it.

 2  THE SPEC ALSO OWNS THE CODE'S CONTRACT.  Put the assembly rules, the
    verification rules and the triage order INTO the spec, so the domain
    expert can read what the code must do — and the two cannot drift apart.

 3  EXACTLY ONE STAGE MAY CALL A MODEL.  Everything before and after is
    deterministic and tested, so an answer can be RE-DERIVED rather than
    re-generated. Re-running the cheap 80% costs nothing.

 4  STATE THE ATTACHMENT RULE IN ONE SENTENCE, FIRST.  The attribute must
    attach to the thing actually measured, not merely co-occur in the paper.
    Co-occurrence is the default failure mode of every literature-mining
    question. Give it its own field and its own quote.

 5  MIRROR THE DECISION LOGIC IN CODE.  The model reports evidence; the
    harness computes the answer. Record every disagreement.

 6  VERIFY EVERY QUOTE AGAINST THE SOURCE IT CLAIMS.  Not "is this string
    somewhere in the corpus" — was it in the file the model named. Then
    prune, then recompute. Keep both answers: their divergence rate is a
    free, standing measurement of fabricated evidence.

 7  MISSING INPUT IS ASYMMETRIC.  Incomplete text caps a negative at
    "unclear" and routes it to re-fetch. It never touches a positive.
    A negative you cannot account for is worthless.

 8  STRUCTURE THE NEGATIVE SPACE.  Make "considered and excluded" a required
    field with a CLOSED reason vocabulary and a "would it have qualified?"
    flag. Excluding something silently is the same defect as not noticing it.

 9  TREAT EVERY NEW FIELD AS AN ATTRACTOR.  It will move answers with no
    criterion edited. Guard it, and test that it cannot reach the verdict.

 10 SHIP A WORK QUEUE, NOT A DUMP.  Sort by "most likely to hide a real
    answer". Give "text incomplete — do not read" its own tier. Never
    renumber a published tier.

 11 ACCEPTANCE IS NOISE-FLOOR-AWARE OR IT IS NOTHING.  Run it twice. Keep a
    two-run baseline. Report only movement outside the floor.

 12 KEEP A RULINGS LEDGER, SEPARATE FROM THE RULES.  Rules do not preserve
    the judgments they came from. Twice the written rule pointed the OPPOSITE
    way from the curator while the pipeline reached the right answer anyway —
    which is not a property to rely on.
```

Rules 1–7 and 10–12 are question-independent. Only rule 4's specific attachment
("the perturbed sample was itself sequenced") and four lookup tables are
perturbation content — and none of the ~13,000 lines that turn a DOI into text.

---

## 15 · What this does *not* show

Stated up front, because it costs nothing and the rest of the deck is worth more
for it.

```
 ✘  No independent gold-standard labelled set exists.
    A curator read 5 papers end to end and reversed 2 of them; the other 25
    of the blind evaluation were reviewed by the developer.
    Corpus-scale accuracy is UNMEASURED.

 ✘  "0 fabricated quotes in 4,597" is a real result, but it is not proof the
    reading is correct — only that the sentences it cited are genuine and in
    the file it named.

 ✘  The system disagrees with itself on ~1 paper in 17. That is measured and
    reported, not fixed.

 ✘  One open question from the last change is explicitly unresolved: the
    "species not stated" answer appears in only 0.3% of values, where the
    spec says it should be common. Either these papers nearly all state
    their species, or the model is inferring one from silence. This test
    cannot tell which.
```

---

### Where the pieces live

| | |
|---|---|
| the spec — all criteria and decision logic | [prompt.md](.claude/skills/perturbation-detection/prompt.md) |
| how to run it | [SKILL.md](.claude/skills/perturbation-detection/SKILL.md) |
| **the four lookup tables — swap these** | [task/record.yaml](.claude/skills/perturbation-detection/task/record.yaml), [decide.yaml](.claude/skills/perturbation-detection/task/decide.yaml), [report.yaml](.claude/skills/perturbation-detection/task/report.yaml), [change.yaml](.claude/skills/perturbation-detection/task/change.yaml) |
| the predicates a table cannot express | [task/rules.py](.claude/skills/perturbation-detection/task/rules.py), [report.py](.claude/skills/perturbation-detection/task/report.py), [screens.py](.claude/skills/perturbation-detection/task/screens.py), [change.py](.claude/skills/perturbation-detection/task/change.py) |
| the pack's identity, version and spec contract | [task/task.yaml](.claude/skills/perturbation-detection/task/task.yaml) |
| reading a pack, and hashing it — plumbing, not judgment | [pe/pack.py](.claude/skills/perturbation-detection/pe/pack.py) |
| assemble the paper into one prompt | [prepare.py](.claude/skills/perturbation-detection/pe/prepare.py), [paper_text.py](.claude/skills/perturbation-detection/pe/paper_text.py) |
| the one model step | [run_headless.sh](.claude/skills/perturbation-detection/pe/run_headless.sh) |
| verify quotes, prune, recompute the verdict | [validate.py](.claude/skills/perturbation-detection/pe/validate.py) |
| the reading queue and the six screens | [summarize.py](.claude/skills/perturbation-detection/pe/summarize.py), [audit.py](.claude/skills/perturbation-detection/pe/audit.py) |
| version diffs and the noise floor | [compare.py](.claude/skills/perturbation-detection/pe/compare.py) |
| what a run directory is, and the refusal to report on nothing | [runstate.py](.claude/skills/perturbation-detection/pe/runstate.py) |
| the seam, and the test that holds it | [tests/test_seam.py](.claude/skills/perturbation-detection/tests/test_seam.py) |
| the human rulings ledger | [CURATOR-RULINGS.md](.claude/skills/perturbation-detection/CURATOR-RULINGS.md) |
| what each version was allowed to claim | [ACCEPTANCE-v0.0.12.md](.claude/skills/perturbation-detection/ACCEPTANCE-v0.0.12.md), [EVAL-30-v0.0.10.md](.claude/skills/perturbation-detection/EVAL-30-v0.0.10.md) |
