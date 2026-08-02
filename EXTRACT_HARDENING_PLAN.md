# Hardening the extraction stage

A work plan, ordered. Every item below was found by reading the code and then measured against
the local corpus (`corpus/`, 6 articles, 4 of them extracted); a second pass tried to refute each
one before it was written down. Line numbers are as of `adf5746`.

**Every number in this document was measured on the machine this plan was written on, against
those 6 articles.** The README describes a larger corpus that is not on this machine; none of its
figures are quoted here. Where a count would only be meaningful at that scale, the plan says what
was seen locally and leaves the extrapolation to whoever has the full corpus.

## How to work through this

- **Do Stage 0 first.** Until the extraction cache invalidates on a parser change, none of the
  later work is observable in the corpus — you will fix a parser, re-run, and see the old output.
- **One item, one commit.** The commit message is a sentence about behaviour, in the style already
  in the log: *"Stop a heading claiming a section it cannot account for"*, not *"fix sections.py"*.
- **Commit and push at the end of every stage**, on a branch, with the full test suite green:

  ```bash
  python -m pytest tests -q && git push -u origin HEAD
  ```

  Work is done in sessions that can end without warning; an unpushed stage is a stage that has to
  be redone. Push even when the stage is only partly finished — a branch with three of six commits
  on it is progress, a lost working tree is not.
- **Write the test first, and make it fail for the stated reason.** Every item names its test.
- **Measure on the corpus before and after.** Most items quote a number from a real file. If your
  before-number does not reproduce, stop and say so — do not implement against a claim you could
  not confirm.
- **Nothing a cap or a rule drops may be silent.** If your change discards text, the count and the
  reason go into `extraction.json` (via `FileResult.meta` *and* the allow-list at
  `extractor.py:206-213` — a key not in that tuple never reaches the record) and into the affected
  card's `notes`.
- **Statuses are a closed set.** Adding one means adding it to `extractor.py:50-59` and to
  `tests/test_extract_corpus.py::test_every_status_is_in_the_taxonomy`.
- **`blocks.jsonl` must stay byte-identical across re-extraction of unchanged bytes**
  (`tests/test_extract_article.py:345`). Never put a live timestamp, a set iteration, or a
  `datetime.now()` into a block or a card note.
- **Module docstrings say why, naming the file that taught the rule.** Each item below gives you
  the DOI and the string to cite.
- After each stage, run `python -m pytest tests -q` and
  `python -m manuscript_harvest.extract.cli all --force`, and diff the resulting `blocks.jsonl`.
  The diff is the review.

Stages 0–2 are prerequisites: until the cache invalidates on a parser change, none of the later
work is visible in the corpus.

---

## Stage 0 — Make the pipeline safe to iterate on

### 0.1 One bad supplement must not take the whole run with it

`extract_bytes` (`extractor.py:273-358`) has no exception guard and neither does `extract_article`;
`cli.cmd_all` calls it in a bare loop. Measured: an article with one supplement containing
4,000-deep nested `<sec>` raised `RecursionError` straight out of `extract_article`, leaving
neither `extraction.json` nor `blocks.jsonl` on disk. `jats.blocks_from_jats` catches only
`ET.ParseError` (`jats.py:331`) while its walker is recursive; `pdf.blocks_from_pdf` is
`try:`/`finally:` with no `except` around the page loop; `tables.build_card` is unguarded at all
five of its call sites.

- Add `PARSER_ERROR = "parser_error"` to the status constants in `extractor.py`. Put it in neither
  `_PRODUCTIVE` nor `_BENIGN`, so it counts as a text-bearing failure and blocks `complete`.
- Wrap the dispatch body of `extract_bytes` (lines 312-358) in
  `except Exception as e: return result(PARSER_ERROR, note=f"{type(e).__name__}: {e}")`.
  `RecursionError` subclasses `Exception`, so this catches it.
- Tighten the two parsers so the generic guard is a backstop, not the first line of defence:
  `jats.blocks_from_jats` catches `(ET.ParseError, ValueError, RecursionError)`;
  `pdf.blocks_from_pdf` gains `except Exception` beside its `finally`.
- In `cli.cmd_all`, wrap the call, print `f"{name:38s} crashed: {type(e).__name__}: {e}"`, count a
  `crashed` bucket, and keep going.

Tests: `test_a_supplement_that_raises_becomes_parser_error_not_a_crash`,
`test_cmd_all_survives_one_crashing_article`, plus the taxonomy test.

### 0.2 A parser change must invalidate a cached extraction

`extractor.py:512-524` accepts a cached record on `source_manifest_sha256` + `extractor_version` +
"blocks.jsonl exists". `__version__` is `"0.1.0"` in `extract/__init__.py` and has been bumped
exactly once, by the rename commit — while `sections.py` changed materially in `6a54ff7` (+123
lines) and `dc7197b` (+86). Measured: extracting `10.1126_science.aat5031` at `6a54ff7^` versus
HEAD gives 21 blocks with a different `section` (abstract 47→26, null 38→59) at the same manifest
sha and the same `"0.1.0"` — so `--force` is the only thing that has ever picked up a parser fix.
`limits` is recorded in the record but is also not part of the key, so editing `max_scan_rows` in
`config.yaml` reuses a stale extraction.

- Bump `__version__` to `"0.2.0"`.
- Add `source_fingerprint() -> str` to `extract/__init__.py`: sha256 over
  `sorted(Path(__file__).parent.glob("*.py"))`, updating with each file's `name.encode()` then
  `read_bytes()`, first 16 hex chars; `""` when the glob is empty (a source-less install).
- Add `_parser_versions()` to `extractor.py` returning
  `{"pymupdf": fitz.__version__, "openpyxl": openpyxl.__version__, "python": "%d.%d" % sys.version_info[:2]}`
  — major.minor for Python only, so a patch bump does not invalidate a whole corpus.
- Add `extraction_key(manifest_sha, limits)` = sha256 of `json.dumps({...}, sort_keys=True)` over
  manifest sha, `__version__`, `source_fingerprint()`, `limits.to_dict()`, `_parser_versions()`.
  Write it as `"extraction_key"`; keep `source_manifest_sha256`, `extractor_version` and `limits`
  as the human-readable breakdown. The cache branch compares the key.

Tests: `test_a_changed_limit_invalidates_the_cache`,
`test_a_changed_parser_source_invalidates_the_cache` (monkeypatch `source_fingerprint`).

### 0.3 The cache must check the file it is trusting

`extractor.py:522` tests only that `blocks.jsonl` exists. Measured: emptying a real 475 KB
`blocks.jsonl` and re-running gives `cached: True, status: complete, totals.blocks: 532` over zero
lines on disk. `read_blocks` compounds it — a malformed line is skipped silently
(`blocks.py:113-115`).

- `blocks.write_blocks` returns `{"path", "sha256", "lines"}`; record `blocks_sha256` and
  `blocks_lines` beside `blocks_path`.
- The cache branch recomputes the sha of `blocks.jsonl` and requires a match. A record with no
  `blocks_sha256` (everything written before this change) counts as a mismatch and re-extracts once.
- On mismatch, append to a `problems` list in the record:
  `"blocks.jsonl did not match the hash in extraction.json; re-extracted"`.

Test: `test_a_truncated_blocks_file_is_re_extracted_not_trusted`.

---

## Stage 1 — Stop wrong characters reaching the model

These are the items where a model is being handed a string no human wrote. They are all small.

### 1.1 Soft hyphens, zero-width characters and symbol-font glyphs

Measured in `corpus/10.1126_sciimmunol.aba4163/extracted/blocks.jsonl`: 79 damaged codepoints —
21× U+00AD, 41× U+F067, 5× U+200B, plus U+F061/F062/F06B/F073. Block 6 reads
`interleukin-<U+00AD> 17A (IL-17A), IL-17F, and interferon- (IFN-)`. The paper says
`interleukin-17A` and `interferon-γ (IFN-γ)`. U+F067 is Adobe Symbol gamma
(`page.get_text("dict")` reports the font as `SymbolGreek`); it stands for γ in `IFN-γ`, `RORγt`
and `CD8` across blocks 6, 13, 18, 66, 137, 138, 235-245, 301. `10.1038_s41467-023-40505-5` block
451 reads `1 i n` from font `CMSY10`, which is `1 ≤ i ≤ n`.

Root cause: `_HYPHEN_BREAK` (`pdf.py:37`) is `r"(\w)[-‐‑]\s*\n\s*(\w)"`, and U+00AD is category Cf
— neither `\w` nor `\s` — so the pattern cannot fire and `re.sub(r"\s+", " ")` leaves the invisible
character inside the word. `grep -rniE 'xad|u00ad|200b|feff|unicodedata|NFKC'` over
`manuscript_harvest/` returns nothing: this is unhandled everywhere.

In `pdf.py`, in `_clean_block`, **in this order**:

1. `_SOFT_BREAK = re.compile(r"­[ \t]*\n?[ \t]*")` applied **before** `_HYPHEN_BREAK` — this
   turns `interleukin-­\n17A` into `interleukin-17A`, not `interleukin17A`. Both observed
   shapes must pass: `scRNA-­ seq` and `pheno­ type`.
2. `text.translate({0x00ad: None, 0x200b: None, 0xfeff: None, 0x200c: None, 0x200d: None})`.
3. A module-level `_SYMBOL_PUA: Dict[int, str]` of ~40 entries over the Adobe Symbol encoding
   (0xF061 α, 0xF062 β, 0xF067 γ, 0xF06B κ, 0xF073 σ, 0xF0A3 ≤, …). Apply it **only** to codepoints
   whose span font matches `re.compile(r"symbol|cmsy|cmmi|mathematicalpi|advp", re.I)` — build the
   allowed set per page from `page.get_text("dict")` spans, so a PUA codepoint from an unrelated
   subsetted font is never turned into a Greek letter.
4. Record it: `meta["glyphs_mapped"] = {"": "γ", ...}` and
   `meta["glyphs_unmapped"] = {"\uf0XX": 4}`, both added to the allow-list at `extractor.py:206`.

Tests: `test_a_soft_hyphen_does_not_survive_inside_a_word` (asserts `scRNA-seq` and `phenotype`
appear and no U+00AD remains), `test_a_symbol_font_glyph_becomes_the_greek_letter_it_stands_for`,
`test_a_private_use_codepoint_from_an_ordinary_font_is_left_alone`, and a corpus test asserting no
block of `10.1126_sciimmunol.aba4163` contains U+00AD, U+200B or an unmapped U+F0xx.

### 1.2 De-hyphenation deletes a real hyphen in identifiers

`_HYPHEN_BREAK` rejoins across a line break unconditionally, so a genuinely hyphenated token
broken at the hyphen loses it. Measured at ~3% of hyphenated tokens, and the casualties are
identifiers (gene symbols, cell-type names, accession-like strings) — exactly the tokens a
curation answer is made of.

Guard the substitution: keep the hyphen when the character before it is a digit or an uppercase
letter, or when the fragment after the break starts with a digit or an uppercase letter —
`scRNA-seq` and `IL-17A` keep theirs, `perturba-tion` does not. Record
`meta["hyphens_kept"]` and `meta["hyphens_joined"]` so the ratio is inspectable.

Test: `test_a_hyphen_inside_an_identifier_survives_the_line_break`, parametrised over
`IL-\n17A`, `scRNA-\nseq`, `CD4-\npositive`, `perturba-\ntion`, `well-\nknown`.

### 1.3 A JATS table cell holding several paragraphs is fused into one value

`jats._inline_text` (`jats.py:76-93`) ends with `"".join(parts)` — no boundary between block-level
children — and `_table_rows` calls it per `<td>`. In
`corpus/10.1038_s41467-023-40505-5/fulltext.nxml`, 24 of 144 `td`/`th` hold more than one block
child. The card in `blocks.jsonl` reads, byte for byte:

```
4. SNP PIP [mixed, 14 distinct] = 0.12 | 0.1770.1390.1300.119 | 0.3450.1720.117 | 0.5280.113 | 0.7980.15 | ...
5. Link Method [text, 11 distinct] = ABC | Distance | DistanceDistance | ... | Nearby OCRNearby OCR
3. Supporting SNPs ... rs1906615rs7689774
```

The dtype has silently flipped from `number` to `mixed`, so the column gets no min/max/median.

Use a sentinel, not a plain separator — the obvious version leaves `'; 0.798; ; 0.15;'`:

```python
_BLOCK_LEVEL = frozenset({"p", "list-item", "disp-quote", "sec", "title", "def", "term", "tr"})
_SEP = "\x1f"   # no publisher XML carries this
```

`_inline_text(element, block_sep: str = " ")`; in `walk`, when a child's tag is in `_BLOCK_LEVEL`,
`parts.append(_SEP); walk(child, False); parts.append(_SEP)`. Then:

```python
text = "".join(parts).replace("\xa0", " ")
text = re.sub(r"\s*\x1f+\s*", _SEP, text)
text = text.strip(_SEP)
return re.sub(r"\s+", " ", text.replace(_SEP, block_sep)).strip()
```

`_table_rows` calls `_inline_text(cell, block_sep="; ")`, so the cell becomes `0.798; 0.15`.
Docstring cites 10.1038/s41467-023-40505-5 Table 1 and the string `0.7980.15`.

Tests: the four cases above, plus a check that a prose `<p>` containing `<italic>` is unchanged
from today, plus a corpus assertion that no card value in that article matches `^\d+\.\d+\d\.\d`.

### 1.4 Dropping a citation marker leaves its punctuation behind

`_inline_text` returns early for `xref[ref-type=bibr]` but still appends `node.tail`, so the
separators between grouped citations survive. Over the JATS blocks of
`10.1016_j.cell.2021.01.053`: 35 literal `()`, 12 more `(` followed by a separator, 16
whitespace-before-comma. One block reads verbatim
`...key mechanisms for severe symptoms (; ; ; ; , ). While recent studies have offe...`. In
`10.1038_s41467-023-40505-5`: `into LD blocks using LDetect,.` and 5 doubled commas.

Worse: in the **key resources table** of 10.1016/j.cell.2021.01.053 — the one table this pipeline
exists to read — 10 of 29 SOURCE cells are destroyed. `(Korsunsky et al., 2019)` → `()`,
`Bray et al., 2016; Melsted et al., 2019` → `;`, `Wolf et al., 2018` → ``. The card reads
`2. SOURCE [text, 11 distinct, 11 empty] = () | 10x Genomics | ; | Aglient | BD Biosciences | ...`.

Two changes in `jats.py`:

(a) After the whitespace collapse in `_inline_text`, in order:
`re.sub(r"(?<=\s)\(\s*[,;&–—-]*\s*\)", "", t)` — **the lookbehind is what keeps `susie_rss()` and
`HarmonyMatrix()` intact while removing `report ()`** — then `re.sub(r"\s+([,;.)])", r"\1", t)`,
then `re.sub(r"([,;])(\s*[,;])+", r"\1", t)`, then one final whitespace collapse.

(b) `_inline_text` gains `keep_citations: bool = False` which skips the bibr early return, and
`_table_rows` passes `keep_citations=True`. Docstring: in prose a citation is noise, in a cell it
is the value.

Tests: `tests/test_extract_units.py:665` must stay green; new units for
`report (<xref ref-type="bibr">1</xref>).` → `report.`, `symptoms (<xref/>; <xref/>).` →
`symptoms.`, `we ran the susie_rss() function` unchanged, and a `<td>` with a bibr xref rendering
`(Korsunsky et al., 2019)`. Corpus test: no card value in that article is `()` or `;`.

### 1.5 `blocks.jsonl` already contains a line that is not valid JSON

`tables._as_number` is `float(text.rstrip("% "))` guarded only by `except ValueError` — and
`float("inf")` succeeds. Line 520 of `corpus/10.1038_s41467-023-40505-5/extracted/blocks.jsonl`
(sheet `Supplementary Data 3`, column `neg. log10-pval`) contains `"max": Infinity`. Python's
`json.loads` with default settings accepts it; `serde_json`, Go's `encoding/json`, PostgreSQL
`jsonb` and DuckDB all reject the line.

- `import math`; `_as_number` returns `value if math.isfinite(value) else None`.
- `profile_column` counts the non-finite values and appends
  `f"{n} non-finite value(s) (Inf/NaN) were not counted in the range"` to the card's notes.
- `write_blocks` passes `allow_nan=False`, so any future path that produces one raises at write
  time instead of writing an invalid artifact.

Test: `test_an_infinite_cell_does_not_make_an_invalid_json_line` — build the card, write it, and
`json.loads` every line with `parse_constant=lambda c: pytest.fail(c)`.

---

## Stage 2 — Stop the running-head rule deleting content

`_running_lines` (`pdf.py:47-55`) drops any string of ≤100 characters seen on ≥3 pages, with no
regard for where on the page it sits. Measured drops: 424 of 1,160 blocks in
`10.1126_sciimmunol.aba4163`, 108 of 457 in the Cell PDF, **854 of 2,472 (34.5%)** in the 89-page
Science supplement, 3 in MOESM2.

Those 3 are one string: `Reviewer #2 (Remarks to the Author):`.
`corpus/10.1038_s41467-023-40505-5/extracted/blocks.jsonl` contains Reviewer #1 (index 222), #3
(244), #1 (265), #3 (368), #4 (421) and **no Reviewer #2 at all** — their remarks now read as a
continuation of reviewer 1's. In the Science supplement the rule also deletes the UMAP legend
(`CD4 T cell`, `CD8 T cell`, `B cell`, `NK cell`, `MNP-a`, `MNP-b`); in aba4163 it deletes
`S. aureus`, `Day 0`, `Day 60`, `Control`, `Crescents (%)` — treatment and timepoint labels.

And it is invisible: `meta["running_lines_dropped"]` is set at `pdf.py:95/102` but is **not** in
the allow-list at `extractor.py:206-213`, so it never reaches `extraction.json`, while the
docstring at `pdf.py:15` claims "is dropped and the count recorded". The only reader anywhere is
one test.

**Two commits, in this order.**

### 2.1 Say which lines the running-head rule deleted

`_running_lines` returns `Dict[str, int]` (text → pages) instead of a set. After the block loop:

```python
meta["running_lines_dropped"] = <total blocks dropped, as today>
meta["running_lines"] = [{"text": t, "pages": n}
                         for t, n in sorted(furniture.items(), key=lambda kv: -kv[1])][:20]
```

Add both keys to the tuple at `extractor.py:206-213`.

Tests: extend `test_running_headers_are_dropped` to assert the first entry, and add a test that
`extract_article` puts `running_lines` into `extraction.json`.

### 2.2 Stop the rule deleting a line that is not in a margin

Collect `(text, in_margin)` per page instead of `text`, with
`in_margin = (y1 < 0.12*h) or (y0 > 0.88*h) or (x1 < 0.08*w) or (x0 > 0.92*w)` computed from the
raw rect and `page.rect`. Count **only margin appearances** toward the threshold: a string is
furniture when it appeared in a margin on at least `running_header_min_pages` pages.

**Do not** require "in a margin on every page" — that was tried and it fails on real furniture.
With margin-counting, `Krebs et al., Sci. Immunol...` (y0/h = 0.03 on every page),
`SCIENCE IMMUNOLOGY | RESEARCH ARTICLE`, the rotated right-margin
`Downloaded from https://www.science.org...` (x0/w = 0.95), `ll`, `ll Resource` and
`(legend on next page)` all stay dropped, while `Reviewer #2 (Remarks to the Author):`
(y0/h = 0.28, 0.53, 0.12) and the 80 figure-legend labels in the Science supplement are kept.

Test: `test_a_repeated_line_in_the_body_is_not_mistaken_for_a_running_head`, plus a corpus test
asserting `Reviewer #2 (Remarks to the Author):` is present in that article's blocks.

---

## Stage 3 — Table cards

The card is the highest-value artifact this stage produces and the one a curator will actually
read. Four defects, ordered by how wrong the answer is.

### 3.1 A truncated scan must not render its value set as complete

`cards_from_csv` passes `n_rows_total=None` unconditionally
(`spreadsheet.py:216`). `corpus/10.1126_science.aat5031/supplementary/02_aat5031_data_s1.csv` is
40,269 lines; its card reads `Shape: 4998 data row(s) x 7 column(s)` with the truncation note and
**no total**, so it is indistinguishable on that line from the 60-row file beside it.

The real damage is one level down. The card renders
`celltype [text, 12 distinct] = B cell | CD4 T cell | ...` using the `=` form, which per
`tables.py:236-241` and the module docstring means *the complete value set* and is the entire
point of the card. The file holds **33** cell types; 21 are missing, including Podocyte, Proximal
tubule, Glomerular endothelium and Thick ascending limb. A model asked which cell types were
profiled in this kidney atlas gets 12 immune types and misses every epithelial and endothelial
one, presented as exhaustive.

- `cards_from_csv`: keep consuming the `csv.reader` past the cap and count non-blank rows exactly
  (an exact count, not a newline count, so quoted embedded newlines do not inflate it); pass it as
  `n_rows_total`. The whole file is already decoded at `spreadsheet.py:206`, so this costs no I/O
  and does not violate the "never read a whole sheet to size it" rule, which is about xlsx.
- `profile_column` takes `complete: bool` (pass `not truncated` from `build_card`). When it is
  False, emit `examples` and **never** the `=` form. A value set drawn from a partial scan must
  not be rendered as the full set.
- Truncation note becomes
  `f"scan stopped at {limits.max_scan_rows} rows of {n_rows_total}; the value sets below are examples from those rows only"`.

Tests: a 60-row CSV keeps `= a | b | c`; the same CSV under `Limits(max_scan_rows=10)` renders
`e.g.` and never `=`, and its Shape line carries the total 60.

### 3.2 A sheet of stacked panels becomes one card that pools six experiments

`corpus/10.1126_sciimmunol.aba4163/supplementary/01_aba4163_data_file_s1.xlsx` sheet `Figure 6`
holds ten blank-row-separated panels. `detect_header` finds the first panel's header on row 2 and
`build_card` treats rows 3-104 — every later panel's title, units and header row — as data. The
card reads:

```
1. Figure 6C [text, 23 distinct, 63 empty] = % Crescents | [% of CD3+] | ... | Figure 6D | Figure 6E | ...
2. Control [mixed, 24 distinct, 50 empty] = 0 | 0.061 | ... | cGN | cGN in deleter | Control
7. column_7 [number, 14 distinct] e.g. NTN + aIL-17A, 0.26, ...
```

with `header_confidence: high` and no note. Across the local corpus: 56 sheets scanned, 12 with
≥2 blank-separated row groups, 10 with an interior blank column — 18 distinct sheets, 32%.

- New `tables.split_blocks(rows, limits) -> List[Tuple[int, int]]`: cut on any run of ≥1 fully
  blank row, return `(start, end_exclusive)` in original row coordinates.
- **The guard matters.** Do *not* require every part to be ≥2 rows — that disables the split on
  `STable 4.4` (parts `[1, 13, 13, 13]`, a title row above three stacked tables) and `Figure S7`
  (`[9, 1, 9, 10]`). Instead: merge any 1-row part into the part that follows it (a lone row above
  a table is a panel title, which `detect_header` already consumes as a caption line), drop a
  trailing 1-row part, then split only if ≥2 parts remain. Verified against every xlsx sheet in
  the local corpus: fires on exactly the 16 multi-group sheets, and correctly collapses
  `STable 4.1` `[1,13]`, `STable 4.2` `[1,3086]` and `STable 4.5` `[1,3086]` to a single card.
- In `_cards_from_xlsx` and `cards_from_xls`, build one card per part with
  `locator=f"sheet {title!r} rows {start+1}-{end}"`,
  `data_ref={..., "row_start": start+1, "row_end": end}` and a note naming the split.
- New `limits.max_tables_per_sheet = 20`; overflow into `meta["tables_skipped"]`. The new cards
  push `01_aba4163_data_file_s1.xlsx` past `max_tables_per_file = 60`, so that cap must record its
  overflow in meta too.

Test: 10 cards from the `Figure 6` fixture, each with its row-range locator, and the pooled
`Figure 6C` column name absent everywhere.

### 3.3 A header whose names repeat is rejected, and the header strings become data

`_looks_like_labels` (`tables.py:138`) requires `distinct >= int(0.7 * len(present))`. On
`corpus/10.1016_j.cell.2021.01.053/supplementary/08_mmc5.xlsx` sheet `TV+vs.V-`, the real header
is two identical eight-column tables side by side: `present 16, distinct 8, threshold 11` → False.
The card falls back to "no header row identified; columns are positional" and then prints
`7. column_7 [text, 3 distinct] = cluster | virus+ | virus-` — a header string offered as one of
three complete values, in the authoritative `=` form. Same failure on the two-row merged header in
`49_..._MOESM4_ESM.xlsx` sheet `Supplementary Data 5`:
`column_5 [number, 6 distinct] = 0 | 0.01 | 0.02 | 0.03 | Endothelial OCRs | Sum.PIPs`. 9 of 68
corpus cards are headerless; 6 are one of these two shapes.

- **Periodic repeat.** In `_looks_like_labels`, before applying the 0.7 rule, test whether the
  present cells are the same block repeated k times for k in 2..4
  (`present == present[:len(present)//k] * k`). If so, accept, and have `build_card` note
  `f"the header repeats {k} times across the row; this sheet holds {k} tables side by side"`.
- **Two-row headers.** New `_compose_header(rows, index, width)`: if the row above the accepted
  header is label-like but sparser, forward-fill its cells rightwards across the `None`s — that is
  exactly how openpyxl in read-only mode renders a merged cell, and `ReadOnlyWorksheet` has no
  `merged_cells` attribute, so forward-fill is the only reconstruction available — and join as
  `"Cardiomyocyte OCRs / Sum.PIPs"`. Set `header_row` to the sub-header index, record
  `header_rows: [3, 4]` on the card, note `"header spans 2 rows"`.
- Prefer routing the side-by-side case to 3.2's splitter on the interior all-blank column where
  one exists (10 sheets here).

### 3.4 A column that contains its own header name should never be `high` confidence

Cheap safety net, independent of whether 3.2 and 3.3 land. In `build_card`, after `columns` is
computed: if any `column["name"].lower()` appears in that column's own
`values`/`examples`, force `confidence = "low"` and note
`"column '<name>' contains its own header name as a value; the sheet probably holds more than one table"`.

Measured on the local corpus: fires on exactly 7 column-instances across 5 cards (Figure 4 ×2,
Figure 6, Figure 7, Figure S6 ×2, Figure S9), **all of them currently `high`**, with zero false
positives. Use exact match, not substring — a substring variant fires on 50 columns including
legitimate ones.

### 3.5 `data_ref` does not contain what its docstring promises

The module docstring says a card "records `data_ref` — file, sheet, header row — so code that
wants the real values re-reads the original file at the exact offset". Nothing in the repo does
that, and `data_ref` is not sufficient to: it has no scan window and no file hash, so the rows a
card describes cannot be re-read reproducibly.

- Extend `data_ref` to `{"file", "sheet", "header_row", "first_data_row", "last_data_row", "sha256"}`
  (the sha of the source file, which the manifest already has).
- Add `manuscript-extract table <doi> --file <path> --locator <loc> [--rows N]` to `cli.py`: read
  `data_ref` off the card in `blocks.jsonl`, re-open the source at that offset and print the real
  rows. This is the command a curator uses to check a card, and it is the thing that makes
  `data_ref` a contract rather than a comment.

Test: `test_the_table_command_reprints_the_rows_the_card_describes`.

### 3.6 The renderer drops sample rows without saying so

`tables.render` breaks out of the sample-row loop when the budget runs out and prints nothing
about it, in the one module whose docstring promises the opposite. Append
`f"  ... {n} further sample row(s) not shown"` on the same rule as the column line already uses.

---

## Stage 4 — Section labelling

### 4.1 An abandoned section is reopened by the next heading of the same name

`SectionTracker.heading()` (`sections.py:300-306`) sets `self.current = name` and
`self._carried = 0` unconditionally and never consults `self.abandoned`. On
`10.1126_science.aat5031`, `abstract` is abandoned at block 33 — and then block 70, the heading
`One Sentence Summary` (an ABSTRACT alias), reopens it, so blocks 70-85 come back labelled
`abstract`: **16 blocks, 6,272 characters, including 4 figure legends totalling 2,844 characters**
beginning `Fig. 1. Mapping the spatial and temporal architecture of the mature and developing
human kidney`. Meanwhile `extraction.json` carries the note "the blocks after it are left
unlabelled", which is false for 16 of them, and so does README:~800.

The existing test only reopens with a *different* section, so this path is uncovered.

- `__init__` gains `self.reopens_refused: List[str] = []`.
- First line of `heading(name)`:
  `if name in self.abandoned: self.current = None; <append to reopens_refused, dedup>; return None`.
- `reason()` gains a third clause naming it.
- **Do not touch `pdf.py:108-119` or `docxfile.py:120-129`** — both already assign the return value
  straight into `Block(section=...)`, so `None` becomes an unlabelled heading. Editing them is a
  no-op at best.
- **Do** add `meta["reopens_refused"]` next to the existing `meta["sections_abandoned"]` in
  `pdf.py:133-134` and `docxfile.py:158-159`, and add the key to the allow-list at
  `extractor.py:206-213` — otherwise it never reaches the record.

Test: `test_an_abandoned_section_is_not_reopened_by_its_own_heading`.

### 4.2 The alias list misses the actual top-level headings of Cell Press and Science

Verified `None` today: `Experimental Model and Subject Details`,
`Quantification and Statistical Analysis`, `Supplemental Information`,
`Supplemental Experimental Procedures`, `References and Notes`, `Data Availability Statement`,
`Availability of data and materials`, `Methods Summary`.

And Cell Press prefixes its STAR Methods headings with a bullet glyph the `^…$` anchor rejects:
`corpus/10.1016_j.cell.2021.01.053/fulltext.pdf` page 18 emits `d KEY RESOURCES TABLE`,
`d EXPERIMENTAL MODEL AND SUBJECT DETAILS`, `d METHOD DETAILS`,
`d QUANTIFICATION AND STATISTICAL ANALYSIS` — 9 such blocks in that file.

The strongest single case: `References and Notes` on page 83 of
`supplementary/01_aat5031-stewart-sm.pdf` goes unrecognised, leaving **71 blocks and 19,265
characters of other people's reference titles labelled `methods`** in that article's
`blocks.jsonl`.

- In `_compiled()`, widen the optional prefix group from
  `(?:(?:\d+(?:\.\d+)*|[IVXLC]+)\s*[.)]?\s*)?` to
  `(?:(?:\d+(?:\.\d+)*|[IVXLC]+|[d●▪•⁃])\s*[.)]?\s*)?`. The bare `d` is safe only because the body
  must still match in full.
- Extend `_ALIASES`, each addition commented with the real file it came from. METHODS gains
  `|experimental\s+model(?:\s+and\s+subject\s+details?)?|quantification\s+and\s+statistical\s+analysis|supplement(?:al|ary)\s+experimental\s+procedures?|methods?\s+summary`;
  SUPPLEMENTARY changes `supplementary\s+` to `supplement(?:al|ary)\s+`; DATA_AVAILABILITY gains an
  optional `(?:\s+statement)?` tail and `|availability\s+of\s+(?:data|code)(?:\s+and\s+materials?)?`;
  REFERENCES gains `references?\s+and\s+notes` **before** the bare `references?` in the body string
  — `normalize()` backtracks past ordering because of the `$` anchor, but `_leading_patterns()` has
  no anchor, so trailing order leaves `AND NOTES` glued to the first citation.
- **Do not add `Main`.** Nature uses it for the body as a whole; mapping it to any canonical name
  is the guess the module docstring refuses.

Test: parametrise `normalize` over every real heading above, and include the negative controls —
`normalize("Main") is None`, and the widened `[d]` prefix must not promote a Cell Press bulleted
highlight line to a heading.

### 4.3 A glued `REFERENCES AND NOTES` is not split

`split_leading_heading`'s lookahead demands a capital letter, and a reference list starts with a
digit. Widen the lookahead to `(?=[A-Z0-9])`. Combined with 4.2's ordering fix, the Science
supplement parses as heading `REFERENCES AND NOTES` + rest `1. K. W. Wucherpfennig...` instead of
19,265 characters of bibliography under `methods`.

### 4.4 An article can be `complete` with no `methods` or `results` label anywhere in its body

`corpus/10.1126_science.aat5031/extracted/extraction.json` says `status: complete`,
`main_text.source: pdf`, `main_text.sections: [data_availability, abstract, supplementary,
back_matter, references]`. Of its 87 main-text blocks, 36 are `section: null` and only 63% of
main-text characters are labelled. Blocks 34-52 — the entire Results and Discussion — are all
null. `totals.sections` nonetheless lists `methods`, because all 1,607 `methods` blocks come from
a *supplementary* PDF. A downstream filter for `section == methods` over this paper's main text
returns zero blocks and nothing says that is abnormal.

Add `_section_labelling(main_result, main_info)` to `extractor.py`, merged into
`extraction["main_text"]["section_labelling"]`:

```json
{"method": "declared" | "heuristic",
 "labelled_blocks": 51, "total_blocks": 87,
 "labelled_chars": 27546, "total_chars": 43740, "coverage": 0.63,
 "body_sections_found": ["abstract"],
 "body_sections_missing": ["methods", "results"],
 "confidence": "declared" | "ok" | "low" | "none",
 "why": "no results or methods label anywhere in the main text (63% of characters labelled)"}
```

`declared` when the origin is jats; else `none` when nothing from
`{introduction, results, methods, discussion}` was found, `low` when coverage < 0.75 or anything
in `{results, methods}` is missing, `ok` otherwise.

**Do not add an article status** — the taxonomy is closed. Instead `cmd_one`/`cmd_all` print
`! section labelling is <confidence>: <why>` beside the existing note, and `cmd_status` gains a
`sect=` column.

### 4.5 The one number that decided a third of an article's labels is not configurable

`MAX_BOUNDED_SECTION_CHARS = 6000` is a module constant with no config key, which README states
outright. Move it into `Limits` as `max_bounded_section_chars`, keep the docstring's measurement
(longest legitimate run 4,653; shortest pathological 6,294), have `SectionTracker` default to it,
and thread `limits` in from `pdf.py` and `docxfile.py`. Record the effective value in the card's
existing `limits` block, which already ships in the record.

### 4.6 `section` is single-valued although JATS has the tree

`walk_section` knows the full heading path and throws it away. Add
`section_path: Optional[List[str]] = None` to `Block`, emitted from `to_dict` only when set, and
populate it in `jats.py` from the walker's title stack. Leave `pdf.py` alone — there the tree is a
guess, and a guessed path is exactly what this module refuses to produce.

---

## Stage 5 — Make the record tell the truth

### 5.1 A 185-character main text is `complete`

`main_usable = main_result.status == OK and main_result.chars > 0` (`extractor.py:558`) — any
non-zero length passes. Measured on a synthetic article with only a thin JATS body and no PDF:
`status: complete`, `main_text.chars: 185`, and
`note: "JATS XML yielded only 185 characters (under 2000); fell back to the PDF"` — a fallback to
a PDF that does not exist, because `_main_text` sets that note at line 467 *before* checking
whether a PDF is there.

- At the no-PDF exit, replace the note with one that says there was nothing to fall back to.
- Before every `return` in `_main_text`, set `info["thin"] = result.chars < limits.min_main_text_chars`
  for whichever result won, regardless of source.
- Require `not main_info.get("thin")` for `complete`. Do not add a new limit —
  `min_main_text_chars` is already the number and already carries its why.

For reference the four complete local articles carry 89,151 / 88,262 / 43,740 / 94,014
main-text characters, so this gate flips none of them.

### 5.2 The fetch stage's own verdict on the supplements is never read

`extract_article` copies `record["status"]` into `fetch_status` but never reads
`record["supplementary_status"]`, which is where `fetcher._supplement_status` records
`expected_but_missing`, `partial_failure`, `none_retrieved`, `fetched_unverified`, … Measured: an
article whose manifest says `supplementary_status: expected_but_missing` and
`problems: ["...listed supplementary material; no tier retrieved it"]` extracts as
`status: complete, supplementary: [], suppl[-]`.

Declare a closed caveat vocabulary beside the status constants:

```python
SUPPLEMENTS_MISSING = "supplements_expected_but_missing"
SUPPLEMENTS_UNVERIFIED = "supplement_set_unverified"
MAIN_TEXT_THIN = "main_text_thin"
LANDING_PAGE_ONLY = "landing_page_only"
MANIFEST_ENTRY_WITHOUT_PATH = "manifest_entry_without_a_path"
CAVEATS = {...}
#: fetch verdicts that mean files were lost, not merely uncounted.
_FETCH_SUPPLEMENTS_LOST = {"expected_but_missing", "partial_failure", "none_retrieved"}
```

`none_retrieved` belongs in the blocking set: README defines it as "a tier tried and every file it
went after was lost". Copy `supplementary_status` into the record as
`fetch_supplementary_status`, build `caveats`, require `SUPPLEMENTS_MISSING not in caveats` for
`complete`, and append the caveats to the `summarize` line. Deliberately **not** blocking on
`SUPPLEMENTS_UNVERIFIED` — it is common (2 of 6 locally) and is a caveat, not a defect.

### 5.3 The fetch transport's name is stamped on blocks as the publisher's label

`Block.label`'s docstring says "The publisher's name for this item, e.g. 'Supplementary Table 3'".
Measured: `label: "Download"` on **1,630 of 1,717** blocks in `10.1126_science.aat5031`, and
`label: "Europe PMC supplementary archive"` on **344 of 532** in `10.1038_s41467-023-40505-5`.
Both come straight from the fetch manifest's `entry["label"]`.

Meanwhile the thing that *is* the publisher's name is thrown away: `extract_bytes` accepts
`caption`, passes it only to `FileResult`, and no parser ever receives it — so
`"Table S7. Cytokine analysis, related to Figure 6"` reaches `extraction.json` and none of that
file's 3 blocks. 12 of 25 `ok` supplements carry a caption in the record; 7 of those reach zero
blocks. And `_table_blocks` sets `label=card.title`, i.e. the sheet name, so a JATS label would
lose to `cytokine_analysis` anyway.

- Add `caption: Optional[str] = None` to `Block`, emitted from `to_dict` only when set (sorted
  keys keep byte-stability). Set it in `extractor.result()` alongside the label loop.
- `tables.render` emits the block caption as its `Caption:` line when `card.caption` is None.
- `_table_blocks` prefers the JATS-joined label over `card.title` when one was supplied.
- **Reject the transport label by measurement, not a denylist.** A manifest `label` shared by two
  or more `supplementary[]` entries in the same article is not a per-file name: drop it and record
  `extraction["supplement_label_rejected"]` once. Verified this catches every case here and
  nothing else — `Europe PMC supplementary archive` covers 39/39 and 49/49 entries, `Download`
  covers 11/11 and 2/2, while a genuine Cell Press per-file caption
  (`Table S1. Primer sequences…`) is unique.
- Record `label_source` per supplement as a closed set
  `"jats" | "jats_caption" | "manifest" | "review" | "none"`, and **add it to the allow-list at
  `extractor.py:206-213`** or it will not reach the record. Where JATS gave a caption but no
  label, synthesise the label from the caption's leading identifier
  (`Table S7. Cytokine analysis…` → `Table S7`, first 40 chars before the first period) and mark
  it `jats_caption`.

Tests: `test_no_supplement_label_is_shared_by_two_files` over the corpus;
`test_a_shared_manifest_label_is_rejected_and_a_unique_one_survives`.

### 5.4 A JATS locator is XPath-shaped and points at the wrong element

`walk_children` builds `f"{path}/{name}[{index}]"` where `index` counts *every* child, not children
of that tag — which is not what `[n]` means. Of 168 body/back locators in
`10.1038_s41467-023-40505-5`, only 76 resolve under real XPath semantics and 153 point at a
different element. `_front_metadata` hard-codes `"front/abstract"` for every `<abstract>`.

Fix the string; do not add a correct one beside a wrong one. Replace the running `index` with a
`collections.Counter` keyed on tag name; enumerate the abstracts as `front/abstract[{n}]`.

Separately, `pdf.py` receives PyMuPDF's `(x0, y0, x1, y1, text, block_no, block_type)` and keeps
only two of them, so a PDF block is locatable only to a page. Add
`locator_ref: Optional[dict] = None` to `Block`, emitted only when set, and populate
`{"page": n, "bbox": [round(v, 1) for v in raw[0:4]], "block_no": raw[5]}` — round to 1 decimal so
the JSON stays byte-stable. This is what lets a review sheet point a human at a rectangle.

Tests: a body of `<sec><title>T</title><p>a</p><fig/><p>b</p></sec>` gives the second `<p>` the
locator `body/sec[1]/p[2]`, not `p[4]`; a corpus test that every jats-origin locator resolves.

### 5.5 The JATS parser is the only one that caps silently

`_Walker.add` (`jats.py:168`) stops at `max_blocks_per_file` with no flag, unlike `pdf.py` and
`docxfile.py` which both set `blocks_capped`; `add_table` returns silently past
`max_tables_per_file`; and the deliberate `ref-list` drop leaves no trace, so `meta["sections"]`
disagrees with the sections actually present. Set `blocks_capped`, `tables_capped` and
`reference_list_dropped: True`, and add them to the allow-list.

Also: a `<p>` wrapping a `table-wrap` emits the table twice with the same locator
(`walk_children` handles the nested float, then the generic branch handles it again). Track
visited elements by `id()` within a walk.

---

## Stage 6 — Stable identity and a measured baseline

### 6.1 A block needs an identity that survives a parser change

`index` is positional: insert one block and every downstream reference moves. This is the
prerequisite for the review layer — a human confirmation recorded against index 148 must not
silently become a statement about a different paragraph.

The naive content key is not enough: `(source_file, locator, text_sha256)` collides **223 times in
1,717 blocks** in `10.1126_science.aat5031` (`p.79 / 'Developing nephron'` occurs 22 times), 93 of
882 in aba4163, 11 of 532 in the Nature paper. An occurrence ordinal is required.

- Add `block_id: str = ""` to `Block`, emitted in `to_dict`.
- Compute it inside `number_blocks` — the one place indices are assigned, so no caller can forget.
  For each block: `key = "\x00".join([role, origin, source_file, locator, kind, sha256(text)])`,
  keep a `Dict[str, int]` of how many blocks already used that key, and set
  `block_id = sha256(f"{key}\x00{ordinal}".encode()).hexdigest()[:16]`.
- **`section` is deliberately excluded**, and the docstring must say why: section is the
  most-revised heuristic in this package, and a confirmed fact about donor age must survive a
  relabel. Measured: including it would change 21 of 1,717 ids and 2 of 882.

Measured for this design across the real `6a54ff7^ → HEAD` parser change: 1,717/1,717 and 882/882
ids unchanged, 0 collisions across all 3,312 local blocks, 1.4 ms to compute 1,717 ids, +17
characters per line (~50 KB on a 700 KB file).

Test: `test_a_block_id_survives_a_section_relabel`, `test_block_ids_are_unique_over_the_corpus`.

### 6.2 The section audit must be a gate, not a thing someone remembers to run

`section_audit.py` already scores the PDF labeller against JATS. Make it a regression metric:

- `--fail-under FLOAT` and a checked-in `tests/expected_section_scores.json` keyed by slug, holding
  the aligned/correct counts and per-section precision. A test in `tests/test_extract_corpus.py`
  runs the audit over whatever corpus is present and asserts no slug regressed; a legitimate
  improvement updates the file in the same commit, so the diff shows the gain.
- `section_audit.py:236-239` does `if report is None: continue`, so an article with no XML vanishes
  from the report. Collect the skipped slugs and print
  `N article(s) had no XML/PDF pair and were not scored: <slugs>` before the headline, and add
  them to `--json` as `{"skipped": [...]}`. The 91% figure must never read as corpus coverage.
- Record the *measured* fact that reading order is already correct: insertion order gives 10
  backward steps in 98 aligned paragraphs, and `sort=True` makes it **worse** (10.6% → 11.5% and
  2.5% → 3.9% inverted pairs). Pin it with `test_pdf_reading_order_is_not_improved_by_sorting` so
  the next person does not "fix" it.

---

## Stage 7 — The human review layer

This does not exist today: `grep -rn "review\|override\|confirm" manuscript_harvest/` returns
nothing outside `section_audit.py`'s prose. Build it in this order; each piece is useful alone.

**What to ask a human, ranked by value per minute** — build in this order:

1. **Table header rows.** Bounded (16 low-confidence cards across 4 articles), ~15 seconds each,
   and a wrong header silently corrupts every metadata answer drawn from that sheet.
2. **Is the article body actually here.** One yes/no per article. If it is wrong, every answer for
   that article is wrong.
3. **Article sign-off.** The container, not a competitor — it is what makes the layer honest.
4. **Files a human thinks do carry content.** A checkbox, and a rare one: every non-`ok`
   supplement in the local corpus is a figure image, which is never queued, so this fires zero
   times here. It matters at scale, not on this sample.
5. **Supplement label joins.** Fifth, because most of the win is the code fix in 5.3.
6. **Section labels.** Last and narrowly scoped: `section_audit.py` already scores this
   automatically wherever a JATS reference exists, so only ask where it cannot.

### 7.1 `review_signals` in `extraction.json`

The strongest triage signal — `header_confidence == "low"`, 16 of 68 cards — lives only inside
`blocks.jsonl`, so no queue can be computed from the record today. After `number_blocks`
(`extractor.py:543`), add a counting pass and a top-level key:

```json
"review_signals": {
  "tables_total": 68, "tables_header_low": 16, "tables_headerless": 9,
  "tables_truncated": 3, "tables_columns_dropped": 0,
  "main_text_blocks": 87, "main_text_unlabelled": 36,
  "supplements_sniffed": ["supplementary/07_url"],
  "jats_reference_available": false
}
```

### 7.2 `manuscript_harvest/extract/review.py` — the queue

A pure function `queue_for(extraction, blocks_path) -> List[dict]` and a closed item-kind
taxonomy `MAIN_TEXT_PRESENT, TABLE_HEADER, FILE_HAS_CONTENT, SUPPLEMENT_LABEL, SECTION_SPAN,
SIGN_OFF`. Rules, in order:

1. **MAIN_TEXT_PRESENT** — one item when any of: `landing_page_only`; `source is None`;
   `status != "ok"`; `status == "ok" and chars < 4 * limits.min_main_text_chars`; or
   `origin == "pdf" and not {"methods","results"} & set(main_text.sections)`.
2. **TABLE_HEADER** — one per table block with `header_confidence == "low"`, carrying the rendered
   card text verbatim, capped at a new `Limits.max_review_cards_per_article = 25`; the overflow
   count goes into `review.queue_truncated` (a cap is never silent).
3. **FILE_HAS_CONTENT** — one per supplement with
   `status in {no_text, no_text_scanned_pdf, unsupported_format, too_large, unreadable, missing, parser_error}`,
   plus every path in `supplements_sniffed`. Explicitly **not** `image_no_text` / `media_no_text` /
   `data_file_skipped` — 76 of the 101 supplements in the local corpus are figure images, so
   queuing them would be three quarters of the work, and nobody can judge a `.jpg` from its name.
4. **SUPPLEMENT_LABEL** — one per *article* when any `ok` supplement has `label_source != "jats"`.
5. **SECTION_SPAN** — one per article only when `origin == "pdf"` **and** no `fulltext.nxml` on
   disk **and** (`sections_abandoned` non-empty or unlabelled fraction > 0.25). Verified: fires on
   aat5031, not on aba4163.
6. **SIGN_OFF** — always, always last.

Measured against the six local articles: 2, 11, 2, 5, ~6, 5 ≈ 33 items, so about six per article,
and two thirds of them are table headers at ~15 seconds each. Re-measure the per-article rate on a
larger corpus before promising anyone a total.

### 7.3 Where the answer lives

Three structural obstacles have to be designed around, and they decide the location:
`store.evict_article` deletes everything but `manifest.json`, so a review beside the article dies
with a budget eviction; `corpus/` is gitignored, so curator labour would be uncommittable; and the
extraction cache would ignore it.

**Write `reviews/<doi_slug>.json` at the repo root**, alongside the existing `manual_fetch/`
precedent — checked in, no `.gitignore` edit, no `store.py` edit, and it outlives eviction by
construction. `REVIEW_DIR = "reviews"` lives in `review.py` (the corpus layout is unchanged);
resolve as `Path(config["extract"].get("review_dir", "reviews")) / f"{slug}.json"`.

```json
{"review_format": 1, "slug": "...", "doi": "...",
 "answers": [
   {"kind": "table_header",
    "key": {"source_file": "supplementary/06_mmc3.xlsx", "locator": "sheet 'Readme'"},
    "source_sha256": "<manifest sha of that path>",
    "card_fingerprint": "<sha256 of '|'.join(header) + '#' + header_row + '#' + n_columns>",
    "verdict": "corrected", "override": {"header_row": null},
    "note": "row 1 is a legend line, not column names",
    "by": "gabdank@stanford.edu", "at": "2026-08-01T10:14:00Z"}],
 "sign_off": {"verdict": "fit", "by": "...", "at": "...", "note": ""},
 "signed_manifest_sha256": "..."}
```

Keys by kind: TABLE_HEADER → `{source_file, locator}` (verified unique among table blocks);
FILE_HAS_CONTENT / SUPPLEMENT_LABEL / MAIN_TEXT_PRESENT → `{path}`; SECTION_SPAN →
`{source_file, locator, text_sha256, ordinal}` — **not** `{source_file, heading_sha256}`, which
collides 33 times in the real corpus.

Staleness, computed by `review.state_of(review, extraction, manifest) -> (state, stale_items)`:

- `stale_bytes` — `answer.source_sha256` differs from the manifest sha for that path. The override
  is **not** applied; the item is re-queued with the previous answer shown as context.
- `stale_shape` — bytes match but `card_fingerprint` differs (a parser change moved the header).
  The override **is** applied — the human's claim is about the bytes, not the parser — but the item
  is re-queued and listed under `extraction.json`'s `review.stale`.
- Sign-off goes stale when `signed_manifest_sha256 != source_manifest_sha256`; the article drops to
  `queued` and the old sign-off is kept as `previous_sign_off`.

Answers are appended, never rewritten — the file is an audit log; the last non-stale answer for a
key wins. Article state is a closed set:
`unreviewed | queued | partially_reviewed | reviewed | stale`.

Test: `test_every_review_key_is_unique_over_the_corpus`.

### 7.4 Corrections must feed back

Add to `review.py`:

```python
class Overrides:
    @classmethod
    def load(cls, slug, manifest) -> "Overrides"      # empty when no review file
    def header_for(self, source_file, locator) -> Optional[dict]
    def label_for(self, path) -> Optional[dict]
    def content_expected(self, path) -> Optional[bool]
    def section_for(self, source_file, locator, text_sha256, ordinal) -> Optional[str]
    def main_text_source(self) -> Optional[str]
    def applied(self) -> int
```

Thread one optional keyword `overrides: Optional[Overrides] = None` through
`extract_article` → `_main_text` / `extract_path` → `extract_bytes` → the parsers → `build_card`.
**`build_card` has five call sites in four modules**, not two: `spreadsheet.py:128`, `:175`,
`:216`, `jats.py:256` and `docxfile.py:141`; also thread it into
`extractor._plain_text_blocks`, because a `.txt` supplement routes to `cards_from_csv` and
produces cards the queue will list. Omitting docx and txt shows a curator a card they cannot
answer.

Application points:

- `_main_text`: `forced = overrides.main_text_source()`; when set, use that rendition and set
  `info["source_forced_by_review"] = True`.
- `extractor.py:534`: the review label wins over the JATS label, which wins over the manifest.
- `build_card` gains `forced_header_row`, `forced_headerless`, `review_note`. When either force
  flag is set, skip `detect_header`, set `header_confidence = CONFIRMED`, append `review_note`.
- `tables.py` gains `CONFIRMED = "confirmed"` and
  `HEADER_CONFIDENCE = frozenset({"low", "high", "confirmed"})`, mirroring how `extractor.py:50-59`
  declares its statuses. Pin it with `test_header_confidence_is_a_closed_set`; note in the
  docstring that any consumer treating `!= "high"` as suspect now sees a third value.
- **Byte-stability trap:** `review_note` is an f-string over `answer["at"]` and `answer["by"]`
  *read out of the review file*. It must never call `datetime.now()`.

For "this file does have content": add the path to `text_bearing_failures` even when the status is
benign, record it under `unreachable_content` with the note and attribution, and **do not change
the per-file status** — the taxonomy stays closed and a `.pptx` stays `unsupported_format`. For
the opposite answer ("this does not matter"), **do not drop the path**: leave it in
`unextracted_text_files`, add it to `cleared_by_review`, and compute the status from
`blocking = [p for p in text_bearing_failures if p not in cleared_by_review]`. Nothing disappears;
a reader sees the file listed and, one key away, the human who cleared it.

### 7.5 The interaction surface: a single-file HTML review sheet

Build `manuscript_harvest/extract/reviewsheet.py`, stdlib only (`html.escape` plus f-strings; no
Jinja, no CDN, no framework), exposing `render(extraction, queue, existing) -> str`.

Why HTML and not a terminal walk or a CSV round-trip: `cmd_show --full` already renders a card to
a terminal, so the terminal option is cheaper than it looks — but half of this corpus's 1,327
table-card lines exceed 100 characters and the longest is 742, and the curator has no way to open
`supplementary/06_mmc3.xlsx` in Excel next to the question. CSV puts a multi-line monospaced card
inside one cell, which Excel renders as an unreadable single line, and makes every correction
free text. The HTML sheet gives side-by-side source access via a `file://` link and closed-set
radios instead of free text.

One `<section>` per queue item: the item kind, the key as `<code>` and as a `file://` link, the
card or block text verbatim in a `<pre>`, radios `confirm / correct / cannot tell`, a kind-specific
input (TABLE_HEADER: a 1-based row number or the word `none`; FILE_HAS_CONTENT: yes/no;
SUPPLEMENT_LABEL: label + caption + a **"this file is not article evidence"** checkbox), and a
note `<textarea>`. At the bottom: a "who are you" field, the sign-off radio
(`fit / fit with notes / unfit`), a read-only `<textarea>` an inline script keeps in sync as JSON,
and a `Download review-<slug>.json` button built with `Blob` + `<a download>`. No network, no
server, opens by double-click, e-mailable.

The evidence checkbox writes `{"override": {"evidence": false}}` and sets
`block.role = "non_evidence"` for that file's blocks — for peer-review files, reporting summaries
and description-of-files stubs. That makes `blocks.py:38-39` a three-value role set, so pin it in
`tests/test_extract_corpus.py` and say so in the README beside the status table, because
`cmd_show --role` and every downstream filter change meaning.

### 7.6 CLI

- `manuscript-extract review <article> [--out DIR]` — writes `review-<slug>.html`, prints its
  path, exit 0 when nothing is queued and 1 when items are.
- `manuscript-extract review <article> --apply answers.json` — merges into
  `reviews/<slug>.json` (append-only), re-extracts with `force=True`, prints what changed:
  `3 override(s) applied: 2 header row(s), 1 file reopened`.

### 7.7 A reviewed extraction must be visibly different from an unreviewed one

- Add the review file's sha to the extraction key from 0.2, so the first correction is not
  silently discarded by the next `manuscript-extract all`.
- Always-present block in the record:
  `"review": {"state": "unreviewed", "queued": 11, "answered": 0, "stale": [], "overrides_applied": 0, "sign_off": null, "queue_truncated": 0}`.
- `summarize` gains one trailing token: `rev=unreviewed` / `rev=queued(11)` / `rev=reviewed` /
  `rev=stale(2)`.
- `cmd_status` prints one more line —
  `review: 4/6 signed off; 118 items queued, 12 answered, 3 stale` — and gains `--needs-review`
  to list only articles in `queued` or `stale`.

---

## Deliberately not doing

Record these in the README's "Known limitations" so the next reader does not re-litigate them:

- **OCR and vision.** Figure images stay `image_no_text` (76 of the 101 supplements in the local
  corpus) and a scanned PDF stays `no_text_scanned_pdf`. Naming them is the contribution.
- **Table structure from PDFs.** `page.find_tables()` exists, but a PDF table has no stable
  `data_ref` to re-read and the card contract is built on one. Revisit only after Stage 3.
- **Sorting PDF reading order.** Measured worse, twice. Pinned by a test in 6.2.
- **Guessing an unrecognised heading's section.** `null` is the correct answer; a wrong section
  makes a filter drop the text it was looking for while reporting success.
- **A model-facing "evidence pack" artifact.** README's "Not in this repository" draws the
  boundary at `blocks.jsonl`, and it is the right boundary — an index over the blocks (7.1's
  `review_signals`, plus `section_labelling` from 4.4) gives a caller what it needs to select
  without this repo taking a position on prompts or budgets.

---

## Suggested ordering

| Stage | Why here | Rough size |
| --- | --- | --- |
| 0 | Nothing later is observable until the cache invalidates | 3 commits, S/M |
| 1 | Wrong characters are reaching a model today | 5 commits, S/M |
| 2 | Deleted content is reaching a model today | 2 commits, S then M |
| 3 | The highest-value artifact, and where the answers are wrong | 6 commits, incl. one L |
| 4 | The filter every downstream question depends on | 6 commits, S/M |
| 5 | The record must stop over-claiming before anyone reviews it | 5 commits, S/M |
| 6 | Stable ids gate Stage 7; the audit gate protects Stage 4 | 2 commits, M |
| 7 | The review layer, once there is something stable to review | 7 commits, the largest |

Stage 0 comes first and is not optional. Stages 1-5 are independent of each other and can be done
in any order within a stage. Stage 7 depends on 6.1 (block ids), 7.1 (`review_signals`) and 0.2
(the cache key).

Push at the end of every stage — and at the end of every session, finished or not. The stage
boundaries above are the natural review points, but a half-finished stage on a pushed branch is
worth more than a finished one that only exists locally.
