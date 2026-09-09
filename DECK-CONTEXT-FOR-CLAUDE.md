# Deck context brief — paste this into Claude in PowerPoint before asking for edits

You are helping edit `perturbation-detection-talk.pptx`. You can see the shapes but
not the reasoning behind them. This brief supplies the missing context: what the deck
is for, the design rules it already follows, what every slide is doing, and which
numbers are real. **Read it before changing anything.**

---

## 1. What this deck is

| | |
|---|---|
| **Speaker** | Idan Gabdank, Stanford Medicine, Dept. of Biomedical Data Science |
| **Audience** | Colleagues who know single-cell biology. They know **nothing** about this project, nothing about perturbation detection as a curation task, and nothing about AI agents or "skills" |
| **Length** | 30 minutes |
| **Goal** | Not to impress. To get colleagues to **give feedback on the rules** on slide 9 and **volunteer to rule on 24 papers** (slide 16) |
| **Template** | Official Stanford SOM 16:9. Every slide uses the `header only` layout, which already carries the Stanford Medicine corner mark, footer and slide number |

**The one-sentence summary of the work:** software takes a published paper's
identifier, downloads the article and every supplementary file, turns it into
labelled pieces of text, and then a language model — heavily fenced in by
deterministic checking code — answers one question: *were the samples that actually
went into the single-cell sequencer experimentally perturbed?*

---

## 2. Hard constraints — do not break these

1. **No jargon, no acronyms.** The only permitted acronyms are assay names:
   scRNA-seq, snRNA-seq, snATAC-seq. Everything else must be spelled out in plain
   English. Specifically **do not reintroduce**: DOI (say "the paper's permanent
   identifier"), API, JSON, LLM, prompt, schema, pipeline, corpus, JATS, XML, OCR,
   CI, provenance.
2. **Minimal text. Visuals, boxes, arrows.** No bulleted lists anywhere in this deck
   — that is deliberate, not an oversight. If asked to add content, add a *shape*,
   not a bullet. Current average is ~95 words per slide including titles; treat that
   as a ceiling.
3. **Never use a colour outside the palette in §3.** No blues, no greens.
4. **Do not add a second Stanford wordmark** to a content slide. The layout already
   has one at bottom-left.
5. **Keep the speaker notes in sync.** Every slide has notes. If you change what a
   slide claims, change its notes too.

---

## 3. The design system already in use

**Colours — these are the Stanford Medicine theme values. Use only these.**

| Name | Hex | Used for |
|---|---|---|
| Cardinal | `8C1515` | The single most important thing on the slide. Emphasis blocks, the "yes" state, banner statements |
| Clay | `5F574F` | Secondary emphasis, the "other side" of a contrast |
| Warm grey | `7F7776` | Tertiary blocks, arrows |
| Tan | `DAD7CB` | Warning / caveat blocks, the "unclear" state |
| Light grey | `E9E8E6` | Neutral cards, the "no" state |
| Off-white | `F8F7F5` | Detail cards sitting inside a slide |
| White / Black | `FFFFFF` / `000000` | Text on dark / light fills |

**Rule of thumb:** Cardinal is a spotlight. If more than about a fifth of a slide is
Cardinal, it has stopped being a spotlight.

**Type — Calibri throughout.**

| Element | Size | Weight |
|---|---|---|
| Slide title | 30pt (cover 38pt) | bold, Cardinal |
| Banner statement inside a red block | 16–19pt | bold, white |
| Card heading | 13–15pt | bold |
| Card body | 11–12.5pt | regular |
| Big stat number | 26–42pt | bold |
| Caption / footnote | 10.5–11pt | regular, grey |

**Geometry — the working area on every slide.**

- Slide is 13.333in × 7.5in.
- Left margin `0.51`, right edge `12.82`, so full content width is **12.31in**.
- Title occupies roughly `0.45`–`1.56`. **Body content starts at `1.72` and must end
  by `6.78`** or it collides with the footer.
- Standard 4-across grid: width `2.92`, pitch `3.10`.
- Standard 2-across grid: width `6.01`, pitch `6.30`.
- Cards are rounded rectangles, no outline, no shadow.

---

## 4. Slide-by-slide: what each one is doing

Read the "job" column before editing. Several slides look like they are making a
point they are not.

| # | Title | Its job | Load-bearing? |
|---|---|---|---|
| 1 | Reading 392 papers… | Cover | — |
| 2 | The bottleneck is not analysis | Frame the work as **triage, not automation** | Cuttable |
| 3 | Every fetch ends in one of fifteen outcomes *(tagged STAGE 1)* | Show that **failure disguises itself as success**. The 15 outcomes are the evidence, the two anecdotes are the payload | Keep |
| 4 | Numbered pieces that know where they came from *(tagged STAGE 2)* | Plant the phrase *"which of thirty files"* — it is the setup for slide 10 | Keep |
| 5 | A supplementary table becomes a card *(tagged STAGE 2, IN DETAIL)* | Show supplementary tables were taken seriously, not skipped | Cuttable under time pressure |
| 6 | The principle both stages are built on | The hinge between the two halves of the talk | Keep |
| 7 | Why searching for keywords does not work | **The most important slide.** Same-sample rule, and the 42% that turns on it | Never cut |
| 8 | One question settles almost every hard case | The governing question, then exposure vs construction | Keep |
| 9 | This is where I need your feedback | **The point of the talk.** Budgeted 7 of the 30 minutes | Never cut |
| 10 | The model does not get the last word | Three-step checking flow. The banner correcting the *order* is the payload | Keep |
| 11 | What the confidence number does NOT mean | Stop anyone treating the score as a probability | Keep |
| 12 | Run it twice and it disagrees with itself | The noise floor. Least flattering number in the project, stated first on purpose | Cuttable if slide 9 overruns |
| 13 | Only the top layer is about perturbations | Generality, part 1: the three layers | Keep |
| 14 | It has already been swapped | Generality, part 2: a second question already runs through it | Keep |
| 15 | The output is a reading queue | Sets up the ask. Tier 2 (106 papers) is the one to point at | Keep |
| 16 | What I am asking for | The ask: 24 papers, four groups, volunteers unknown | Never cut |

**If the speaker needs to save time, cut in this order: 2, then 5, then 12.**

---

## 5. Every number in the deck, and where it comes from

**All figures are from one measurement — the most recent full scoring round over 392
papers. Do not mix in older numbers, and do not round or "tidy" them.**

| Number | Means | Slide |
|---|---|---|
| 392 | papers in the collection | 1, 13 |
| 174 | contain a perturbation *somewhere* | 7 |
| 101 | have it on the *sequenced* sample — the answer wanted | 7, 15 |
| 73 | fail on the same-sample rule alone | 7 |
| 42% | of would-be positives turn on that one rule | 7 |
| 101 / 19 / 272 | final split: yes / unclear / no | 15 |
| 55 / 46 | of the 101 "yes" papers: human / non-human only | 15 |
| 2,337 of 2,337 | quotes verified, 0 unfindable, 0 attributed to the wrong file | 10 |
| 3 of 50 | papers where the unchanged system disagrees with itself on re-run (94% stable) | 12 |
| 4 → 1 | papers that "moved" in run 1 vs run 2 of the same change — a four-fold swing | 12 |
| 28/30, 2/30, 30/30, 38/38 | thirty-paper blind evaluation results | 12 |
| 14 | curator rulings on file, **all made by Idan** | 9, 12 |
| ~1,700 | lines of checking machinery that name the task nowhere | 13, 14 |
| 15 | possible outcomes of a fetch | 3 |
| 13 | possible outcomes per file at the text-extraction stage | 6 |
| 16,596 × 88 | the largest single supplementary sheet | 5 |
| 0.35→0.90, 0.45→0.85, 0.35→0.88, 0.45→0.90, 0.30→0.88 | confidence scores on **byte-identical input**, run 1 → run 2 | 11 |
| 24 / 8 / 5 / 4 / 7 | review pool total, then groups A / B / C / D | 16 |
| 4, 106, 7, 11, 35, 43, 186 | reading-queue tier sizes, in tier order | 15 |

**Three claims that must not be softened, because they are the argument:**

1. *The quotes are checked first; the rules are applied afterwards, to what survived.*
   (Slide 10. The reverse order would be meaningless — a verdict resting on an
   invented quote would outlive the removal of that quote.)
2. *The confidence score is not a probability that the paper is perturbed.* It is
   only "would a careful curator reading this same text agree with me?" — so a
   well-evidenced "no" scores **high**. (Slide 11.)
3. *All fourteen rulings are Idan's own.* That is not a credential, it is the
   problem being raised. (Slide 9.)

---

## 6. The biology rules, in case you are asked to reword them

The governing question, which decides almost every case:

> Is the applied thing what the paper is trying to **learn about** — or is it how the
> paper **obtained** the material it then studies?

Learn about it → a **perturbation**, report it. Obtained the material with it → the
**model** or the **setting**, record but do not count.

When that does not settle it, the kind of manipulation does:

- **Exposure** — something external the material *reacts to* (diet, temperature, low
  oxygen, irradiation, a drug, an injury, an infection) → **perturbation**.
- **Construction** — something that *becomes* the material (a transplant that becomes
  the tissue, a genotype the animal carries, a differentiation recipe, timed mating)
  → **the model**.
- The dividing test: does the applied thing *become* the material, or does the
  material *react to* it? A pathogen and a tumour graft are both living things put
  into a mouse and they land on opposite sides.
- An exposure given to every arm alike is a constant of the protocol, not a variable.

---

## 7. Editing traps specific to this file

These bit during the build. They will bite again.

1. **Do not resize or move the title placeholder** unless you also set its `left`
   explicitly. Setting only width/height writes an explicit position whose origin
   defaults to zero, and the title slides off the left edge.
2. **If a title contains a line break, style every paragraph**, not just the first.
   The template master has a stray Times New Roman fallback, and an unstyled second
   line silently renders as a serif.
3. **Do not switch a slide to a `divider` or `cover` layout.** Those layouts paint a
   large Cardinal shape behind the title area; dark red title text on it is
   unreadable. Every slide here uses `header only` for that reason.
4. **Do not let any shape extend below y = 6.78in** — it collides with the footer and
   the Stanford corner mark.
5. **Avoid tan or light text on clay or grey fills.** Contrast fails. Light grey
   (`E9E8E6`) works on both.
6. **An empty placeholder renders as "Click to edit".** If you delete a shape's text,
   delete the shape.
7. **Slides 3, 4 and 5 carry a small Cardinal "STAGE n" chip** at the right of the
   title row (x 9.72, y 0.62, 3.10 x 0.42in). Do not delete them: slide 6's title
   says "both stages", and these chips are what make that phrase resolve.

---

## 8. Things the deck deliberately leaves out

Do not "helpfully" add these back:

- The third stage of the software, which packs evidence for a question.
- The browser/library-login route for paywalled papers.
- Any mention of which AI model is used, or how the automation is orchestrated. The
  audience does not need it and it invites the wrong conversation.
- Deadlines for the reviewers. There are none, on purpose.
- The 24 paper identifiers themselves. They live in a separate handout — putting 24
  identifiers on a slide is unreadable.

---

## 9. Useful things to ask for

Phrase requests in terms of the design system above and you will get consistent
results. Examples that work well:

- *"Slide 7 is too crowded — move the four statistic blocks onto their own slide,
  keeping the 4-across grid at width 2.92 and pitch 3.10."*
- *"Add a slide after 14 naming a specific next question we intend to answer. Use the
  same layout as slide 13: three stacked full-width bars."*
- *"Make slide 12 simpler — drop the four checking layers and keep only the noise
  floor statistic and the four-fold swing."*
- *"Re-do slide 3 as five columns of equal height, so the last column does not run
  longer than the others."*

Ask for a **shape change**, not "more detail". More detail on a slide in this deck is
almost always the wrong answer — the detail belongs in the speaker notes, which are
already written.
