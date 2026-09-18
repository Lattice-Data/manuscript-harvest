# The PDF glyph-decoding fault: what it was, and what moved

2026-09-17/18, PR #73, branch `fix-pdf-glyph-decoding`.

## The fault

84 of 392 corpus papers carried 13,482 raw control characters in
`corpus/<id>/extracted/blocks.jsonl`. They were not corruption. MuPDF's fallback
for a character code it cannot map is to emit the code itself, and under
`/Encoding /Identity-H` that code is a glyph id, so the extracted string *is* the
glyph-id sequence. `get_text` prints `UV\x14\x16\x16\x16\x1a\x1cB*` where the page
draws `rs133379_G`.

**The brief for this work said the existing ToUnicode machinery in
`extract/pdf.py` was failing to reach these fonts. It was not.** It declines them
correctly, because for about 95% of the damaged characters there is no Unicode
evidence anywhere in the file. Measured over the affected pages, by what the font
says each unnamed glyph is:

| what the font says the glyph is | count | share |
|---|---|---|
| `glyph00404` -- TrueType with `cmap` and `post` stripped | 13,619 | 50.9% |
| `C0` / `g1` -- opaque private names | 6,079 | 22.7% |
| nothing at all -- Type3 bitmap glyphs, or no embedded program | 2,731 | 10.2% |
| ambiguous -- two same-named fonts on one page | 2,341 | 8.7% |
| a real AGL name, which is what the pre-existing repair can use | 1,134 | 4.2% |
| `cid00042` -- CID-keyed CFF | 770 | 2.9% |

Two further corrections to the brief, both of which cost time before they were
found:

* **The `+31` shift is one font's coincidence.** It is
  `BSHNBY+MinionPro-Regular` in `10.1038/s41586-020-2496-1`, a CID-keyed CFF
  whose charset happens to be in the Adobe standard order. The other 83 files
  decode under no single offset, and `10.1038/s41586-021-04345-x` -- the worst by
  character count -- is a Type3 bitmap font with glyph names `/0 /1 /2`, no
  `/ToUnicode` and no font program, which no shift can touch.
* **`get_texttrace` over-reports.** It marks a glyph U+FFFD when the font
  *program* does not name it and takes no account of a `/ToUnicode` CMap that
  names it anyway. Page 2 of `10.1038/s41588-025-02161-x` is a table of contents
  whose 1,013 dot leaders come back U+FFFD from the trace and `.` from
  `get_text`. Any measurement built on the trace alone counts those 1,013 dots as
  damage; the real figure there is 7 characters.

## A second fault, worse, that nothing was counting

Page 17 of `10.1016/j.immuni.2022.09.002` reads `5 microlitres` and `1x Q5 Ultra
II PCR Master Mix`. The extractor wrote `5 mL` and `13 Q5` -- no control
character, no replacement character, nothing for a downstream check to catch.
`mL` for the microlitre is a thousandfold error in a Methods section.

Elsevier's `AdvPS3F4C13` is a `/Type1` with `/Encoding /WinAnsiEncoding`, no
`/ToUnicode`, and glyphs *named* `S`, `a`, `b`, `m` that *draw* Sigma, alpha,
beta and mu -- Adobe Symbol positions. MuPDF resolves the names through the glyph
list to the Latin letters, which is all the file lets it do. Rendering the glyphs
settles what they are.

Measured over an 8-paper Elsevier sample: ~530 such characters, ~65 per paper,
dominated by m→mu, b→beta, g→gamma, d→delta.

## What was fixed, and on what evidence

Two orderings, both from the specifications rather than fitted to this corpus:
the standard Macintosh ordering that TrueType `post` format 1 refers to, and the
predefined charset a CFF means by a standard-strings index. Stripping `cmap` does
not renumber glyphs, so a font that was never reordered still has its glyph ids
where the original put them.

`_order_agreement` decides whether a font may be read that way, from the
document's own evidence: the ordering assigns a character to each glyph id, the
`/W` array says how wide each glyph is, and a reference face this process never
touched says how wide that character should be.

| file | macintosh | cff |
|---|---|---|
| `science.abf3041` Helvetica | **100%** | 56% |
| `j.cell.2022.01.012` ArialMT (5 fonts) | **100%** | 49-63% |
| `j.cell.2024.08.019` Helvetica | **100%** | 55% |
| `science.abl4290` (4 fonts) | **82-100%** | 19-61% |
| `s41586-020-2496-1` MinionPro | 48% | **98%** |
| `science.aat1699` (3 Calibri subsets) | 7-29% | 26-38% |

The last row is the refusal, and the reason a whole-string shift cannot be the
fix: those subsets are numbered from 1 in order of first use, so no published
table applies to them and the file stays `garbled_text_encoding`.

For the Symbol faces, nothing in the file distinguishes one from a Latin face --
both carry `/Flags 32`, both declare `/WinAnsiEncoding` -- so the evidence is the
widths again, and two independent conditions are required. `_latin_or_symbol`
fits one free scale factor, because a condensed or bold design is a Latin design
times a constant and `ArialNarrow` compared width-for-width against Helvetica
reads as a symbol font. That says whether a face is Latin; it does *not* say
which non-Latin encoding a non-Latin face uses, so the character is identified
per code as well. `AdvPS4721B4` draws a Sigma at the code where Adobe Symbol has
the minus sign, which is what that second condition is for.

## What moved

Full corpus re-extraction of all 392 papers (2h53m; editing `extract/*.py`
changes `source_fingerprint()`, so no cache entry survives).

* **51 papers** changed `blocks.jsonl`; 341 came out byte-identical.
* Control characters **13,482 → 9,252**. **No paper got worse.**
* `science.abf3041` 2,562 → **0**. `s41586-020-2496-1` 642 → **0**.
  `j.cell.2022.01.012` 792 → 190.
* 10,400 glyphs are now marked undecodable across 39 papers and 1,069 blocks,
  in `locator_ref.undecodable_glyphs`.
* **8 papers carry damage the control-character scan never saw**, because their
  glyph ids all land on printable codepoints: `s41467-020-19737-2` alone has
  2,691 such glyphs and zero control characters.

Previously garbled passages now read as English: `rs133379_G-SMDT1
rs7977940_G-CLEC2D`; `No sample size calculation was performed. The number of
mice was chosen to include a minim...`; `PC1 (41.21%)`; `5 microlitres was taken
for a subsequent PCR`.

The bulk of the residue is two files that cannot be decoded from what they
contain: `science.aat1699` (3,577 glyphs, 34 marked blocks) and
`s41586-021-04345-x` (1,687 glyphs, 209 marked blocks).

## Known limitations, measured rather than assumed

* **50 papers keep 1,911 control characters that the block marking does not
  flag.** All are Elsevier `Adv*` opaque-name fonts. For a simple font the
  coverage test can only ask whether the font has a usable CMap at all, and
  those have one that covers other codes -- so their unresolved `C0`/`C14`
  glyphs go unmarked. Closing this needs a per-code test for simple fonts, which
  needs the character code, which the trace does not carry.
* **`10.1016/j.immuni.2022.09.002` still says `5 mL`.** Its symbol face declares
  only four codes, of which one is separable by width, and a median over four
  discards that outlier and calls the font Latin. Relaxing the fit to catch it
  was tried and reverted: it made `ArialMT` map `W` to Omega and turned `GWAS`
  into `GΩAS`, and `AdvOT9bd21c25.I` map real Latin `D` and `R` to Delta and
  Rho. A false positive here corrupts running text, so the conservative side is
  the right one.
* **Mojibake is out of scope.** The ~79 `garbled_run` papers with `ÃÂ¢`-style
  double-decoded UTF-8 are valid Unicode, a different fault with a different
  fix, and folding them in would have mixed two measurements.

## The re-score, 2026-09-18

Only **17** of the 51 papers needed one, and that is the part worth carrying
forward. `blocks.jsonl` changing is not the same as the text the model reads
changing: the other 34 changed only by gaining `locator_ref.undecodable_glyphs`,
a field the assembled prompt text does not include, so their
`assembled_text_sha256` is byte-identical and their stored record already
corresponded exactly to what the model saw.

Re-scoring those 34 would have cost about $63 and made the corpus **worse**. The
prompt returns different determinations on roughly 3 papers in 50 across runs of
byte-identical input, so it would have injected churn into records that were
already right. **Filter on `assembled_text_sha256` from the run manifest, never
on the blocks.jsonl hash.**

The 17 ran at `task_version 0.0.25` into `work-glyphfix-17`:

* 17/17 parsed, 0 pending, 0 unparseable, **111/111 quotes verified**, 0
  misattributed, and `model != final` after pruning was 0/17 — no fabricated
  evidence.
* No consistency codes and no evidence flags on any of the 17.
* **16 of 17 determinations unchanged.** `pe.compare` accounted for the one that
  moved by a known mechanism and reported nothing unexplained.
* The mover is `10.1038/s41586-020-2496-1`, `unclear` -> `no`, class
  **STAGE-B-RELEASED**. Its two `garbled_run` defect claims no longer verify now
  that the text decodes to English, so the degraded-text cap released. That is
  the cap flagged as probably spurious when this work was scoped, and it
  resolved on its own rather than being argued away.
* Triage P4 (`unclear` + `degraded_text`) is empty across the 17, where the
  baseline had that paper sitting in it.

## Corpus state

Verified by re-deriving the assembled text for all 392 papers and comparing each
against the manifest of whichever run produced its record:

    papers in corpus                392
    record matches current text     392
    record stale                      0
    no record at all                  0
    task_version across the corpus  {'0.0.25': 392}

So `corpus/` is internally consistent again: one task version, and every record
matches the text it was produced from.

Two records carry a null `validation.model_id`
(`10.1038/s41467-017-02001-5`, `10.1182/bloodadvances.2023011445`). Both predate
this work — neither is among the 17 — and both are otherwise complete at 0.0.25.

Pre-fix state remains backed up at
`~/.manuscript-harvest/extraction-backup-pre-glyph-fix/extracted-2026-09-17.tar`
(423 MB: all 392 `blocks.jsonl` and all 392 `perturbations.json`). `corpus/` is
gitignored, so that copy is the only route back.

## Still open

Nothing in this thread. The two limitations above -- the 1,911 unmarked control
characters in 50 Elsevier papers, and `5 mL` in
`10.1016/j.immuni.2022.09.002` -- are unfixed and documented, not forgotten.
