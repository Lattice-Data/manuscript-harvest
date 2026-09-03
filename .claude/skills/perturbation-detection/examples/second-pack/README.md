# A second task pack, archived

This is the pack that proved the three-layer split works. It answers a different
question — *"which tissue or organ did the sequenced material come from, and does
the paper state it explicitly?"* — through a **byte-identical `pe/`**.

**It is an archive, not a runnable skill.** Read the next section before relying
on anything in here.

## What this is not

- **Not in CI.** The `skills` job runs `pytest` and `ruff` against
  `<skill>/tests` and `<skill>/pe`. Nothing under `examples/` is collected,
  linted or executed by anything.
- **Not maintained.** It is a snapshot. When the pack interface changes, this
  will silently stop matching it, and no test will say so.
- **Not wired up.** There is no `pe/` here and no `tests/`. Running it means
  copying it beside a harness, as described below.

That was a deliberate choice rather than an oversight: keeping it alive as a
second skill would mean maintaining a throwaway question forever, and the
guards that matter are already in
[`tests/test_second_pack.py`](../../tests/test_second_pack.py), which uses a
synthetic minimal pack it builds itself. What is archived here is the *worked
example* — the thing to read when writing question #3.

## Provenance

| | |
|---|---|
| verified working at | `v0.1.0` (`58f7b50`) |
| ran on | 10 papers from this repo's corpus |
| result | 10/10 validated, **116 of 116 quotes verified** |
| harness edits it needed, after the five fixes below | **zero** |
| `pack_sha256` | `f4827c258db8…` |

`results/` holds the 10-row summary CSV and one full validated record, as
evidence and as a shape example. The raw model output and the assembled prompts
(1 MB) are not archived.

## What it cost to ask a different question

| | lines |
|---|---|
| `prompt.md` — the spec | 155 |
| `task/*.yaml` — identity + the four tables | 279 |
| `task/*.py` — the four rule modules | 588 |
| **written in total** | **1,022** |
| `pe/` reused, untouched | 1,697 |

## What it found

The first attempt did not run at all. Five leaks, none of which any existing
test caught — including `test_seam.py`, written for exactly this purpose, which
checks for task *vocabulary* while every leak was structural:

1. `pe/validate.py` read `secondary_arrays[0]["path"]` unconditionally, so a
   pack with no considered-and-rejected array died at **import**.
2. `pe/run_headless.sh` computed its queue in a command substitution, so leak
   1's traceback left the queue empty — and an empty queue reads as "nothing
   pending". A pack that could not be imported reported **"nothing to do …
   every paper already has a result" and exited 0.**
3. `pe/compare.py` printed prose naming `SUPP-EVIDENCE`, a change class only the
   perturbation pack declares.
4. `pe/compare.py` hardcoded `"WITHIN-NOISE"`, so a pack omitting that key had
   papers counted into a class that was never printed.
5. `test_seam.py`'s statement of the interface was hand-written and missing five
   names the harness genuinely imports.

All five are fixed and guarded in
[`tests/test_second_pack.py`](../../tests/test_second_pack.py).

## Why it is shaped the way it is

Deliberately *not* a relabelling of perturbation detection. It differs in every
way the interface allows, because a pack that merely renamed the fields would
have tested nothing:

- a different primary field (`tissue_stated`);
- a different item array (`tissues`) with a different pairing field
  (`is_sequenced`);
- an enum with no analogue in the other pack — `stated_where`
  (`methods` / `results_or_figure` / `abstract_or_title` / `inferred`), which is
  the whole question rather than a detail;
- **no secondary array at all**, because `is_sequenced: "no"` on the item
  already records "considered and excluded" — this is what found leak 1;
- **no `ref_arrays`**, since nothing points into the item array;
- a different decide table: a `yes` needs a tissue that is both paired *and*
  stated explicitly, so an inference alone resolves to `unclear`;
- different tiers, columns, counters, screens and change classes;
- a different cross-run match rule. The perturbation pack's stop list drops
  `cell`, `sample` and `tissue` as uninformative; here that list would delete
  the answer. Same algorithm, different corpus, different uninformative words.

What it shares is only the mechanism: one call per paper, quotes verified against
the source they claim, prune, recompute, tabulate, diff.

## Running it, if you want to

There is no harness here. Copy one beside it:

```bash
cp -R .claude/skills/perturbation-detection /tmp/tissue-stated
cd /tmp/tissue-stated
rm -rf task prompt.md tests corpus
cp -R <this directory>/prompt.md <this directory>/task .

TISSUE_RUN_ROOT=/tmp/tissue-run python -m pe.prepare \
    --set papers-10.txt --corpus <repo>/corpus
TISSUE_RUN_ROOT=/tmp/tissue-run ./pe/run_headless.sh /tmp/tissue-run/work 4
python -m pe.validate  --work /tmp/tissue-run/work
python -m pe.summarize --work /tmp/tissue-run/work
python -m pe.audit     --work /tmp/tissue-run/work
```

`task.yaml: outputs` gives it its own run root (`TISSUE_RUN_ROOT`,
`tissue-stated/`), so it cannot read the perturbation run's papers as pending.

**Expect this to need adjusting.** It is pinned to the interface as of `v0.1.0`;
`tests/test_seam.py` computes the current interface from the harness's own
imports, so run that first and reconcile.

## One thing worth stealing

Screen A found `lung` in a paper that had named it only as the source of a
*downloaded reference dataset* used for annotation — not as material this study
sequenced. That is exactly the trap the spec warns about, caught by the generic
quote-verification machinery working on a field it knows nothing about. The
harness also flagged a real `EV-WRONG-SOURCE` misattribution on another paper.

Neither of those is a perturbation concept. That is the point.
