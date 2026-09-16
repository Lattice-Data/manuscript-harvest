# Stage B's entry condition — measurement and proposed design

**Status: Part 1 BUILT and MEASURED as v0.0.24 — the gate failed on its blocking
criterion, and the measurement changed both remaining parts.** Results in
`ACCEPTANCE-v0.0.24.md`; the four findings that bear on this document:

1. **Part 1 worked on what it was aimed at.** The three previously-known
   flippers all stopped flipping (3/3 agree), none of the six
   harness-false-positive anchors over-fired (6/6, all `full`), and 10 of 15 caps
   released. The dangling-heading diagnosis of §2 was correct.
2. **Part 2 is now indicated by measurement, not argument.** Self-report
   agreement is 20/24, with all four flips in the degraded-text population — a
   rate no previous acceptance set could measure. Three of the four are
   adjudicable by a quote (a verbatim mid-sentence break, dangling
   cross-references to absent methods, located garbled runs) and the fourth
   claims `truncated` with no locus at all.
3. **One correction to Part 2 as specified below:** the requirement that
   `ends_mid_sentence` evidence sit in the source's TAIL must go.
   `s41586-023-06981-x` broke off mid-article with content after it, and the tail
   rule would have rejected a correct claim.
4. **Part 3's premise is refuted.** §3 calls `partial` + `full` internally
   contradictory. Both instances in the runs justify it precisely —
   *"supp2 arrived as a large run of mojibake... the main article, including a
   complete Methods section, supp1 and supp3 are complete"* — so that pair is
   the only way this schema can say **one source is garbage, the article is
   whole**, and `aat1699`'s flip is caused by having no per-source way to say it.
   Collapsing the fields would delete a distinction the model uses correctly.
   What Part 3 should become: **per-source text quality, and a home for
   `garbled_run`**, which currently has none in the `text_completeness` enum.

**Also found, and not yet fixed:** Step 0 defines `"full"` as nothing missing
beyond what `ASSEMBLY:` reports, which makes `"full"` the literally correct answer
on a budget-truncated paper — while `pe.validate` overrides that same answer.
Both rung-3 papers did exactly this in both runs. One spec, two answers, harness
winning silently; a one-line carve-out in Step 0 fixes it and needs approval like
any other Step 0 edit.

---

**Original status: approved 2026-09-16. Part 1 is BUILT as v0.0.24; Parts 2 and 3
are held until Part 1 is measured.** Written 2026-09-15 against the stored
`work-corpus-v0022-revalidate` records and the corpus's own extraction sidecars.
Every number below is measured from files on disk; nothing here cost a model call.

**Decisions, and what they changed in this document.**

- **Build Part 1 first, then re-measure.** One prompt edit plus a `pe.prepare`
  splice, no schema change and no new required field — so no attractor surface. If
  the two-run self-report flip rate goes to 0 of 24, Parts 2 and 3 may be
  unnecessary, and that is measurable for $88 before committing to them. Shipped
  as task version **0.0.24**; protocol and predictions in `ACCEPTANCE-v0.0.24.md`;
  set `papers-accept-stageb.txt`, scorer `score-acceptance-stageb.py`.
- **Acceptance: 24 papers, two runs, ~$88**, with the gate's blocking criterion
  being agreement of the self-report between runs rather than any determination.
  Trimmed from the 30 of §5: the four degraded-text positives, the review and
  `j.ccell.2023.08.015` came out because the property they test — the cap never
  reaching a non-negative — is a code invariant `task.rules.stage_b` and
  `tests/test_harness_guards.py` already hold over every Stage A value. The cost
  is that criterion 3 is exercised by one paper, and the scorer prints NOT
  EXERCISED rather than PASS if that one does not reach a non-negative Stage A.
- **Order: this pass, then the full corpus run.** Not caution — a single corpus
  run cannot measure run-to-run agreement, so $721 would buy an updated corpus
  and leave this version's own question unanswered; and if the flip rate has not
  moved, Part 2 is the next version and the corpus would need scoring twice.
- **The v0.0.23 blocker below is resolved:** it merged as #66 and this work is
  rebased onto it. **One consequence that outlives it:** there is no v0.0.23
  *corpus* run, so §5's movement comparison against the v0.0.22 baseline cannot
  attribute a `no` → `yes` to this version rather than to v0.0.23's infection rule.
  The scorer states that rather than absorbing it, and takes `--baseline` for a
  v0.0.23 run over the same 24 papers (one more run, +$44) if that attribution is
  wanted.

## 0. First, a correction and a blocker

**The capped population is 15, not 14.** `unresolved_reason = degraded_text` on 15 of
392 records in the v0.0.22 baseline (19 `unclear` total = 15 `degraded_text` + 4
`pairing_not_stated`). `ACCEPTANCE-v0.0.23.md` says 14. The list is §5 group A; it
matters because the new acceptance set is drawn from that pool.

**This worktree is at task 0.0.22.** v0.0.23 lives on `curator-batch-v0023` — 4
commits, pushed, unmerged — and it **touches Stage B** (adds the `not_applicable`
passthrough) and **already uses CC-8** for the article-type gate. Building on `main`
would collide and would make any acceptance run confound two versions.

## 1. What the harness can and cannot see

### Harness-applied truncation is almost never the reason a paper is capped

| fact | fires on |
|---|---|
| `truncation.rung > 0` | **2** of 392 (both >1M chars, both `needs_section_pass`) |
| `text_completeness_source = "harness"` (the existing override) | **1** of 392 |
| capped papers with `rung = 0` — the harness withheld nothing | **13** of 15 |

### Every harness-measurable degradation proxy has unusable precision

Tested against the 15 capped papers. `extraction.json` turns out to carry a rich
`main_text.section_labelling` block (`method`, `coverage`, `body_sections_found`,
`body_sections_missing`, `confidence`, `why`) plus a declared/fetched/read supplement
ledger, so this is the best case for a harness-side key, not a strawman.

| candidate predicate | fires | covers | precision |
|---|---|---|---|
| fetcher `fetch_status = partial` | 3 | **0** of 15 | 0.00 |
| `main_text.status` / `thin` / `usable` | 392 / 0 / 0 | — | constant, unusable |
| `section_labelling.confidence ∈ (none, low)` | 50 | 7 | 0.14 |
| `body_sections_missing` non-empty | 84 | 7 | 0.08 |
| `methods` not in `body_sections_found` | 29 | 3 | 0.10 |
| `coverage < 0.50` | 11 | **0** | 0.00 |
| supplements declared > `text_read` | 72 | 4 | 0.06 |
| **union of all of the above** | **146** (37% of the corpus) | 11 | 0.08 |

**So option 1 as posed is not available.** Re-keying the cap on harness facts either
deletes it (2 papers) or triples the `unclear` bucket (146) while still missing 4 of
the 15. The reason is that these fields measure **whether the extractor could label
the structure**, not **whether content is absent**: `science.abc3172` has coverage
0.99 with "no methods label anywhere", and `science.abo7257` has coverage **0.00**
while the model correctly reported `full`.

## 2. The mechanism, and it is harness-side after all

The pipeline removes, before the model sees anything: reference lists, back matter,
acknowledgments, funding, competing interests, data availability, **and every table**
— plus Discussion and Introduction at rung ≥ 1. **The instruction prompt never says
so.** Step 0 asks "is this text complete?" over a text the pipeline itself cut, with
no way to tell its own cut from the publisher's.

**On the paper that actually moved**, `10.1182/bloodadvances.2023011445`:

| harness fact | value |
|---|---|
| origin / `section_labelling.confidence` | jats / `declared` |
| coverage | 0.98 |
| `body_sections_missing` | `[]` — all five body sections found |
| supplement | 1 declared, fetched, read |
| `truncation.rung` | 0 |

Every harness signal says complete. And the text it was handed ends:

> … The remaining authors declare no competing financial interests.
> **Footnotes / Contributor Information / … / Associated Data / Supplementary Materials**

— a dangling heading with nothing under it, because the exclusion list removed what
was under it. Corpus-wide: **154 of 392** main texts end on a dangling heading block,
**192 of 392** end without terminal punctuation. `10.1038/s41591-021-01586-1` ends
mid-sentence on a medRxiv licence footer (`"…It is made"`) that running-header removal
cut in half.

A model asked whether that text "ends abruptly" is answering a coin-flip question.
**That is the ~10%.**

## 3. The second defect: two near-redundant fields with an OR between them

Step 0 defines `processing_status = "partial"` by four conditions — garbled runs, ends
mid-sentence, abstract-only, no Methods-like content. **Three of the four are the
definitions of `text_completeness`'s own `truncated` / `methods_missing`.** The two
fields ask nearly the same question and Stage B ORs them, so the model can route
either way — which is exactly what `j.healun.2026.02.1666` did (`partial`/`full` in
r1, `ok`/`truncated` in r2: "two different routes to the same cap").

`partial` + `full` occurred **0 of 392** times in the baseline, and once in the
v0.0.23 runs. A consistency check alone would police a state that barely persists
while leaving the redundancy that produces it.

## 4. Proposal — three parts, in order of value per unit of risk

### Part 1 — Tell the model what the pipeline removed, and define `full` against it

`pe.prepare` already knows all of it. Splice a harness-generated block into the prompt
beside `SOURCE_IDS`:

```
<<<ASSEMBLY>>>
sources supplied: main (37,924 chars), supp1 (26,667 chars)
removed by the pipeline on purpose: reference lists, acknowledgments, funding,
  competing interests, data availability, back matter, and ALL tables
budget truncation applied: none
```

Step 0 gains the missing decision procedure: **`full` means nothing is missing beyond
what `<<<ASSEMBLY>>>` says was removed.** A dangling trailing heading, a cut licence
footer, an absent reference list, a missing table, a supplement that was never
supplied — the pipeline's own doing, and **not** `truncated`.

No ownership change: prompt.md's rule that `text_completeness` is the model's call
stands. The input is deterministic and byte-identical between runs by construction.

### Part 2 — A degraded claim must carry a locatable observation

New sub-object, required only when the claim is degraded:
`text_defect: {source_id, kind, quote}`, `kind ∈ {ends_mid_sentence,
explicit_cut_marker, garbled_run, no_methods_content}`.

- The harness verifies `quote` against the cited source with the machinery it already
  has (`verify_quote_sourced`, threshold 0.85); for the two cut kinds it additionally
  requires the quote to sit in that source's tail.
- `no_methods_content` is an absence and cannot be quoted, so the harness
  **falsifies** it instead: reject the claim if a methods-labelled block was supplied.
  (`science.aat1699` and `2021.09.16.460628` both have `methods` in
  `body_sections_missing`, so both survive — the check only kills false claims.)
- A claim that does not survive is normalized to `full` with
  `text_completeness_source = "harness"` and an `issue` filed. **This is the exact
  mirror of the one-way override already at `pe/validate.py:264`**, which today only
  ratchets `full → truncated`.

This is option 3 in the form the data allows: not "cap only when the harness also saw
truncation" (2 papers), but "cap only when the model's observation survives the
harness's check."

**Attractor guards** — `structured-field-as-attractor` says a new required field moved
2 of 6 determinations with no criterion edited, so all four of its guards apply:

1. Precedence stated first: decide `full` or not on the text; the field justifies a
   decision and never creates one.
2. "`full` with no `text_defect` is the normal, correct and common answer" — **372 of
   392 papers**, and the number goes in the prompt.
3. Negatives named concretely, not generically: the dangling heading, the stripped
   reference list, the missing table, the absent supplement, a dropped Discussion when
   `<<<ASSEMBLY>>>` says it went for budget, and v0.0.23's own carve-out that a review
   with no Methods is not a defective extraction.
4. Same evidence standard as the parent array — the same quote verification
   `perturbations[]` already gets.

### Part 3 — Collapse the OR, then CC-9

- Step 0 rewritten so the two fields ask different questions: `processing_status` =
  *can this be assessed at all* (`ok`/`partial`/`failed`); `text_completeness` = *what
  specifically is missing*. `partial`'s enumerated causes stop restating the
  completeness values.
- Stage B then reads **one** gate, not an OR. `partial` stays on the record for triage
  and keeps `failed` for A0.
- **CC-9** (not CC-8 — v0.0.23 took that number): `processing_status = "partial"` with
  `text_completeness = "full"` and no `text_defect`. Mechanical, because Part 2's
  object makes it mechanical. Answering the question as posed: yes, it should be a
  check — but it is worth having only after Part 3 makes the state genuinely
  contradictory instead of merely odd.

### Where the code goes — the seam is unchanged

`pe/validate.py` already threads `truncated_by_harness` and `needs_section_pass` in
from the manifest and `task/rules.stage_b` already decides. Part 2 adds one more
harness fact to the same channel and one more field name to `task/record.yaml`. No
task vocabulary enters `pe/`; `tests/test_seam.py` stays green. `decide.yaml: inputs`
and `change.yaml: inputs_from` must move together, which `task/change.py` asserts at
import.

## 5. Acceptance set — `papers-accept-stageb.txt`, 30 papers, two runs

`papers-accept-v0023.txt` cannot test this: it holds one degraded-text paper, and that
one (`science.aat1699`) is capped, so its Stage A answer is masked.

**A — the 15 capped papers.** The population at risk of moving `unclear → no`:
`s41586-020-2157-4`, `s41586-022-04817-8`, `s41586-023-06981-x`, `s41591-021-01586-1`,
`s41598-022-17832-6`, `2020.03.04.976407`, `2021.09.16.460628`, `2023.03.06.531398`,
`2024.09.05.611379`, `2024.10.18.618987`, `science.aat1699`, `science.abo1984`,
`sciimmunol.adz8650`, `atvbaha.122.317953`, `genes15030298`.

**B — the 5 degraded-self-report, uncapped papers** (4 `yes`, 1 `unclear` via A5):
`j.mucimm.2026.03.012`, `j.isci.2021.102151`, `s41586-022-04518-2`,
`s41586-024-07476-z`, `j.coi.2022.102188`. Proves the asymmetry survives — a positive
must never cap. The last is also v0.0.23's review (`not_applicable` + `methods_missing`),
the one paper that must not cap for a reason v0.0.23 just wrote.

**C — 6 false-positive anchors, all `stage_a = no` and all self-reported `full`.**
Papers whose *harness* facts look degraded. Without these the set cannot detect a gate
that over-fires:

| paper | why it is the anchor |
|---|---|
| `science.abo7257` | `coverage 0.00`, no body section label anywhere |
| `fj.202300601rrr` | `coverage 0.23` |
| `science.aat5031` | `coverage 1.00` **and** no methods label — the pure labelling miss |
| `s41467-020-19737-2` | 210 control chars per 10k — the `garbled_run` surface |
| `science.abf3041` | 165 per 10k **and** no methods label |
| `s41586-021-04345-x` | 65 per 10k, `coverage 0.75`, methods+results unlabelled |

**D — the 3 known self-report flippers**, the primary stability probes:
`bloodadvances.2023011445`, `j.healun.2026.02.1666`, `s41586-021-03852-1`.

**E — 1 positive with degraded-looking harness facts:** `j.ccell.2023.08.015`
(`coverage 0.41`, determination `yes`).

**New primary criterion:** the **gate itself** must agree across the two runs on all
30 papers. Baseline is 3 of 30 flipping. v0.0.23's criteria 1–4 apply unchanged
alongside it.

**Cost:** 30 × 2 at the measured $1.84/paper ≈ **$110**.

## 6. Measured and rejected

- **Re-key the cap onto section-labelling / supplement-ledger facts** — 146 of 392
  fire, precision 0.08. §1.
- **Cap only when harness truncation agrees** (the tie-break as posed) — 2 of 392.
  Deletes the mechanism rather than stabilising it.
- **Make Stage B advisory** — flag + triage tier, determination untouched, the
  `confidence-is-display-not-router` shape. This is the *most* stable answer available
  and it is a live option, not a strawman: `pe.validate` already sets
  `needs_review = True` on every record unconditionally, so the `unclear` is not the
  only thing standing between a degraded `no` and a curator. Not recommended because
  it gives up the cap's purpose rather than repairing its trigger — but see §7.

## 7. What this costs, stated plainly

Up to 15 papers move `unclear → no`, taking the corpus `unclear` count from 19 toward
~6. The two `needs_section_pass` papers keep their cap by harness fact whatever the
model says. Determination churn is the accepted cost under
`spec-coherence-outweighs-corpus`; what is being removed is a cap fired by an
unauditable adjective over text the pipeline itself cut.

**One fact to weigh before spending two runs defending the cap:** curator ruling 6 on
`science.aat1699` is **`no`**. Stage B reports `unclear` for it. On the single paper in
this corpus whose text is genuinely broken — three pages missing, 323 control
characters per 10k — the cap's output is not the curator's answer.
