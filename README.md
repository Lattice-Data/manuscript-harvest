# manuscript-harvest

[![tests](https://github.com/Lattice-Data/manuscript-harvest/actions/workflows/tests.yml/badge.svg)](https://github.com/Lattice-Data/manuscript-harvest/actions/workflows/tests.yml)
[![coverage](https://coveralls.io/repos/github/Lattice-Data/manuscript-harvest/badge.svg?branch=main)](https://coveralls.io/github/Lattice-Data/manuscript-harvest?branch=main)

Fetch a published paper from its DOI — the article and every supplementary file —
and turn it into blocks of text with provenance: paragraphs, headings, captions,
and structured summaries of supplementary tables, each carrying the file and
location it came from.

Three stages, and the `manuscript_harvest` package still holds no model client: what
to ask of the extracted text, and with what, remains a separate decision. One answer
to that question now ships *alongside* the package rather than inside it — a
perturbation-detection skill under `.claude/skills/`, which does carry a prompt and
does call a model. See [Skills](#skills) for why that is a different kind of artifact
and not a hole in the boundary.

    DOI ──▶ fetch (open-access APIs, then library proxy) ──▶ PDF + supplements
                                    │
                                    ▼
             extract ──▶ extracted/blocks.jsonl + extraction.json
                                    │
                                    ▼
             select ──▶ evidence packs, candidates, verified quotes, scores

`blocks.jsonl` is also where anything model-shaped attaches, from outside the
package:

             extracted/blocks.jsonl ──▶ .claude/skills/ ──▶ per-paper answers

## Design

Two principles account for most of the code.

**Push everything deterministic into code.** Identifier resolution, tier
selection, PDF validation, header detection in spreadsheets — all plain code with
tests, so a result can be re-derived rather than re-generated.

**Never report emptiness you cannot account for.** Every bug found so far looked
like a *plausible success*: a paywall page served as `application/pdf`, a publisher
claiming supplements exist while none arrive, a workbook with three sheets that a
library reads as having none, a bot-check page holding 129 characters of user-agent
string. So both stages carry an explicit status taxonomy and record what they did
not get, with the reason. A downstream answer of "no perturbations found" is only
meaningful if the record can show the text was actually there.

**Why the rules are in the code, not here.** Each non-obvious rule is a comment at
the function that enforces it, naming the DOI and the measurement behind it. This
file covers how to run the two stages and the vocabularies a caller needs; for *why*
a rule exists, read the module. Counts in those comments are measurements over a
63-paper development corpus of single-cell and genomics papers, which is not in this
repository — the bytes are the publishers' — but which
`tests/test_extract_corpus.py` re-checks whenever a corpus is present.

## Install

Needs **Python 3.10 or newer** (CI runs 3.10 through 3.13).

    python -m venv .venv && source .venv/bin/activate
    pip install -e .

    manuscript-fetch get 10.1038/s41467-023-40505-5   # download
    manuscript-extract all                            # extract
    manuscript-select readiness                       # ask something of it

Without installing, all three entry points are reachable as
`python -m manuscript_harvest.fetch.cli`,
`python -m manuscript_harvest.extract.cli` and
`python -m manuscript_harvest.select.cli`; `pip install -r requirements.txt` is
enough for that.

Optional extras, as requirements files or as pip extras — they install the same
things, so use whichever fits how you installed the package:

    pip install -r requirements-browser.txt   # or: pip install -e '.[browser]'
    pip install -r requirements-dev.txt       # or: pip install -e '.[dev]'
    pip install 'xlrd>=2.0'                   # or: pip install -e '.[xls]'

`browser` is the library-proxy tier (Playwright), `dev` is pytest, and `xls` is for
legacy binary `.xls` supplements.

Playwright needs a browser too (`python -m playwright install chromium`), unless
Google Chrome is installed — leave `fetch.browser.channel: "chrome"` and real Chrome
is used instead, which clears bot checks bundled Chromium does not. None of this is
needed for the open-access tiers, and `--oa-only` guarantees no browser ever opens.

## Configuration

Both commands read `config.yaml` **from the current directory**, and `--config` is
a top-level flag, so it goes *before* the subcommand:

    manuscript-fetch --config /etc/harvest.yaml get 10.1038/...

Anything the file does not set falls back to built-in defaults, which enable the
proxy tier and write to `./corpus/`. **Run from a different directory than the one
holding your `config.yaml` and you get those defaults silently** — worth knowing
after `pip install -e .`, when the commands work from anywhere. `--corpus-dir` on
any subcommand is the direct way to say where the corpus is.

Every key is commented in `config.yaml`. Three are worth calling out here.

`fetch.contact_email` — **set this to your own address** before running against a
publisher. Crossref, NCBI and Europe PMC all ask callers to identify themselves,
and it is what keeps a polite client out of the rate-limited pool.

If your institution is not Stanford, the shipped proxy prefix is an example. Two
keys need changing:

    fetch:
      proxy:
        prefix: "https://YOUR-INSTITUTION.idm.oclc.org/login?url="
      browser:
        check_url: "https://a-journal-you-subscribe-to.example/some/article"

`check_url` **must be an article your library licenses and the public cannot
read.** Pointed at an open-access one, `check` passes with no session at all and
tells you nothing. To turn the proxy off entirely, set `fetch.proxy.enabled: false`
or pass `--no-proxy`.

### Before you run it against a publisher

The last-resort tier drives a real browser through an authenticated institutional
proxy to reach articles your library subscribes to. That is ordinary licensed
access, but it is *your* licence: check your institution's terms and your
publishers' agreements, keep request rates polite — the default is one request per
host every 3 seconds — and do not use this to bulk-download a catalogue.
Everything the open-access tiers do needs no credentials and no browser.

`login` writes `~/.manuscript-harvest/storage_state.json`. **That file is a live
credential** — anyone holding it has your library session until it expires, with no
password and no second factor. It is the only secret this package creates.

## Fetch: DOI → PDF + supplementary files

    manuscript-fetch get 10.1038/s41586-021-03852-1
    manuscript-fetch batch dois.txt --report fetched.jsonl

If any papers are paywalled, log in once first — the browser tier is the only one
that needs it, and the last one tried:

    manuscript-fetch login     # headed browser, sign in by hand
    manuscript-fetch check     # confirm the saved session still works
    manuscript-fetch batch dois.txt

**`--headed` is not a way to log in.** It shows the browser during a fetch, for
debugging, and never waits for a human: on a dead session it opens on the login
page, reports `session_expired`, and closes a second later. `login` is the command
that waits — Cardinal Key is a WebAuthn credential and Duo cannot be scripted, so
the login itself is not automatable and this does not pretend otherwise. EZproxy
sessions are short-lived; `check` tells you when one has died, and downloads and
validates a PDF to do it. On a base install the tier is skipped rather than fatal.

Writes `corpus/<doi_slug>/` containing `fulltext.pdf`, `supplementary/`, a
`fulltext.nxml` when JATS XML comes free, and a `manifest.json` recording where
every byte came from. The browser tier also leaves `landing.html`, the page it
scraped, for debugging an adapter. Re-running is a no-op unless you pass `--force`.

`dois.txt` for `batch` is one DOI per line; `#` starts a comment, blank lines are
skipped, and a line that is not a DOI is reported and skipped rather than aborting
the run. Repeats are collapsed after normalization, so `10.1038/X` and
`https://doi.org/10.1038/x` count once, and the run says which it collapsed.

`get` prints the article directory on **stdout** and everything else on stderr, so
`DIR=$(manuscript-fetch get 10.1038/...)` gives you the path.

Sources are tried in order. The first four need no credentials and no browser;
`--oa-only` guarantees nothing ever opens one:

| Tier | What it gives |
|---|---|
| `europepmc` | PDF via `fullTextUrlList`, JATS XML, supplements ZIP — the ZIP is not universal |
| `pmc_supplements` | PMC lists the files; the publisher's open-access host serves the bytes |
| `pmc_oa` | `oa.fcgi`: mainly an "is this in the OA subset" signal (see Known limitations) |
| `biorxiv` | 10.1101 and 10.64898 (openRxiv) preprints: PDF, JATS, supplements |
| `proxy_browser` | institutional proxy + real browser; needs a one-time `login` |

Two of the open-access tiers reach their file list by regexing a rendered page
rather than by reading an enumeration, which is exactly why they report
`fetched_unverified` rather than `fetched` below.

**Flags.** `get` and `batch` share `--oa-only`, `--no-proxy`, `--headed`,
`--force`, `--no-supplements`, `--tiers` (comma-separated order), `--corpus-dir`,
and `--json`/`--report` for the manifest. `usage` takes `--by-size` and `--limit`;
`prune` takes `--max-gb` and `--dry-run`; `login` and `check` take `--url`.

### What the statuses mean

The point of this vocabulary is that "no supplements" and "we failed to get the
supplements" must never look alike.

`fulltext.status`, fourteen values: `ok` · `scanned_pdf_suspected` (saved, but has no
extractable text — needs OCR) · `not_research_article` (the DOI resolves to a
correction, retraction or editorial notice — a real publication, but not the paper) ·
`identity_unverified` (a document arrived and does not appear to be this paper; the
bytes are kept, because they are the evidence) · `paywalled` · `not_in_oa_subset` ·
`proxy_not_configured` · `session_expired` · `javascript_challenge` (not a refusal:
the file is public behind a proof-of-work page, so route through the browser tier) ·
`publisher_stub_page` (a plausible 200-OK shell served to automation instead of the
article) · `link_resolver_error` (a resolver answered "no such article here", so
this is not a page we failed to parse) · `not_a_pdf` · `download_failed` ·
`not_found`

Only the first two mean the article is on disk. The two new ones exist because a
document can be perfectly valid and still not be the paper that was asked for, and
in this corpus that happened twice with no complaint from anything:

| DOI | recorded | what was actually stored |
|---|---|---|
| `10.1038/s41586-024-08560-0` | `complete` | a one-page Nature *Author Correction* for `10.1038/s41586-024-08150-0` — its `article-type` is `correction`, and both PDF and JATS carry the *notice's* own DOI and title, so an identity check passes on them |
| `10.1126/science.adf1226` | `complete` | a 71-page **10x Genomics Visium user guide** (CG000239) from a third-party CDN, picked up as the first non-supplement `.pdf` anchor on a page with no `citation_pdf_url`. First extracted block: `10xGenomics.com` |

Three signals answer "is this a notice rather than an article", ordered strongest
first, because none of them is always available: Europe PMC's `pubTypeList`, which
needs no download at all; the JATS `article-type` on the root element; and the
indexed title's prefix (`Author Correction: …`), which insists on the colon so that
*Retraction of the primary cilium during mitosis* stays a research article. When a
notice is identified, the manifest names the DOI to fetch instead — from Europe PMC's
`commentCorrectionList` where it exists, rather than by guessing at the first other
DOI in the text.

"Is this the right paper" is answered by the DOI, with the title as a fallback. That
order is measured, not assumed: the requested DOI appears in the first three pages of
632 of the 633 full-text files in this corpus, the one exception being the vendor
manual above — while the title alone, scored against 1,121 deliberately mismatched
paper/title pairs, clears its threshold for 59 of them. So a missing DOI degrades the
status and a title match rescues it, which keeps the real articles whose publishers
omit the DOI. A document with no extractable text is `scanned_pdf_suspected` and is
never called wrong: "cannot tell" is not "mismatch".

`supplementary_status`, nine values:

| Status | Meaning |
|---|---|
| `none_listed` | the publisher says there are none (`hasSuppl: N`) |
| `fetched` | an archive that *is* the deposit was unpacked whole — they exist and we have them |
| `fetched_unverified` | every file we identified arrived, but nothing bounds the set |
| `partial_failure` | some arrived; at least one failed |
| `expected_but_missing` | `hasSuppl: Y` and we came away with nothing — **the bug case** |
| `none_retrieved` | a tier tried and every file it went after was lost |
| `page_not_parsed` | a page loaded but no file list could be read from it |
| `unknown_none_found` | nobody said whether any exist, and none were found |
| `not_requested` | `--no-supplements` |

`none_retrieved` and `unknown_none_found` both come back with an empty
`supplementary/`, and separating them is the point: the first means a tier looked
and lost everything, the second that no tier ever tried.

**Why `fetched` splits in two.** The two are separated by what *bounded* the set —
a ZIP or tarball member list is not a guess, while a regex over page anchors cannot
know what it failed to match. Both count as settled, so an article still finishes
`complete` and is never re-fetched; an unbounded set is not a failed one. The
measured case that forced the split is in `fetch/fetcher.py`.

A refusal is never written to disk as `fulltext.pdf`: acceptance requires PDF magic
bytes (not the `Content-Type` header, which lies), a successful parse, a body that
does not read like a purchase page, and — since the two cases above — evidence that
the document is the article that was requested. A PDF that fails only the last test
is still written, under `identity_unverified`: it may be the only copy any tier
produced, and it is what a reader needs to check the verdict against. It is simply
never counted as having the article, so it can never finish `complete`, and the
extraction stage will not offer it as main text.

### Re-checking a corpus fetched earlier

The checks above run inside the tier loop, which fixes the next fetch and nothing
already on disk. `revalidate` re-asks the question of what is already there, using
the same functions and no network at all:

    manuscript-fetch revalidate            # report only
    manuscript-fetch revalidate --apply    # write the verdicts into the manifests

It never downloads and never deletes; the worst it can do is move an article from
`complete` to `failed`, which is the direction that makes a corpus more honest. Run
over the 392 articles here it corrected exactly the two in the table above and left
the other 390 untouched.

### Disk usage

Articles average **~40 MB**, so a few hundred papers is tens of gigabytes.

    manuscript-fetch usage --by-size     # what is taking the space
    manuscript-fetch prune --dry-run     # what a sweep would remove
    manuscript-fetch prune --max-gb 20

Set `fetch.max_corpus_gb` and the budget is enforced automatically after every
fetch, evicting oldest first. **Eviction keeps the manifest** — only the bytes go,
the record of what existed stays, and the article is marked `evicted` rather than
incomplete so the next batch does not re-download what the budget just freed. The
newest article is never evicted. Re-fetch with `--force`.

### Exit codes

Scripting either stage means reading the taxonomy off the exit code:

| Command | 0 | 1 | 2 |
|---|---|---|---|
| `fetch get`, `extract one` | `complete` | `partial` | anything else, or a bad DOI |
| `fetch batch`, `extract all` | every article `complete` | at least one was not | no usable input |
| `fetch check` | session works | it does not | — |
| `extract review` | nothing queued | questions are queued | — |
| `extract table` | a card was printed | re-read failed | ambiguous match |
| `select readiness` | every article can carry a negative | at least one cannot | no articles |
| `select verify` | every quote verified | at least one did not | no article, or nothing to verify against |
| `select eval` | scored | below `--fail-under` | no labels, or nothing to score |

Other subcommands use 2 for "nothing to do" — `extract status` and `show` when
nothing is extracted, `fetch prune` with no budget set. So
`manuscript-fetch get ... ; echo $?` returning 1 means the paper is on disk but
something is missing; check `manifest.json` for which artifact and why.

## Extract: corpus files → blocks

    manuscript-extract one 10.1038/s41467-023-40505-5
    manuscript-extract all                       # every article; skips unchanged
    manuscript-extract status                    # coverage across the corpus
    manuscript-extract show <doi> --section methods
    manuscript-extract show <doi> --kind table --full
    manuscript-extract table <doi> --file mmc7.xlsx --all

Offline: it reads only what the fetch stage already wrote. Output lands beside the
article, so an eviction removes it along with the rest of the payload:

    corpus/<doi_slug>/extracted/
        blocks.jsonl      every block, in document order, with provenance
        article.md        the same content rendered for a human to read
        extraction.json   what each file yielded, and what it did not

**Flags.** All subcommands take `--corpus-dir`. `one`/`all` take `--force` and
`--max-scan-rows`; `all` takes `--limit`. `status` takes `--quiet` and
`--needs-review`. `show` takes `--kind`, `--section`, `--role`, `--file`, `--full`
and `--limit` — **note `--limit` defaults to 20**, so a section with more blocks
than that is silently truncated unless you raise it. `table` takes `--file`,
`--locator`, `--rows` and `--all`; without `--all` an ambiguous match exits 2
rather than guessing which card you meant.

### Blocks, not one big text file

A block is one paragraph, heading, figure caption, table card, or a `metadata`
block for article-level fields:

```json
{"index": 2, "block_id": "dc33924763df7a6a", "kind": "paragraph",
 "role": "main_text", "origin": "jats", "source_file": "fulltext.nxml",
 "locator": "front/article-meta/abstract[1]/sec[1]/p[1]",
 "section": "introduction", "section_path": ["Background"], "label": null,
 "chars": 708, "text_sha256": "e736a036…", "text": "Malignant pleural effusions…"}
```

Two things a single concatenated blob cannot do. **Provenance:** confirming a quote
is verbatim is not enough on its own, because a blob cannot say *which* of thirty
supplementary files it came from, where a block can be pointed at
`sheet '<name>' of supplementary/<nn>_<file>.xlsx`. **Selection:** organism and
library kit live in Methods, sample counts in Results, and Introduction is mostly
other people's work, so blocks can be filtered by `section`, `kind` and `role`
before anything reaches a model.

`block_id` is content plus provenance plus an occurrence ordinal, and it survives a
parser change — `section` is deliberately excluded from it, so a human's confirmed
fact about donor age is not invalidated by a relabel. `blocks.jsonl` is written
with sorted keys and no timestamps, so extracting the same bytes twice produces a
byte-identical file. That makes an extraction safe to hash and lets a parser change
be reviewed as a diff.

`role` is a three-value set: `main_text`, `supplement`, and `non_evidence` for a
file a human marked as not article evidence — a peer-review file, a reporting
summary, a description-of-files stub. Its text is kept and readable.

### What makes `all` re-extract

`extraction.json` carries an `extraction_key` hashed over the manifest sha, the
extractor version, a fingerprint of this package's own parser source, the effective
`limits`, the PyMuPDF/openpyxl/Python versions, and the sha of the article's review
file. `all` reuses a cached extraction only while that key matches, so a parser edit
or a `config.yaml` cap change invalidates the corpus by itself. The pieces stay in
the record separately, so a human can see which moved. See
`extractor.extraction_key` — and note that the version number alone was tried first
and did not work.

### JATS XML first, PDF second, landing page last

Where Europe PMC's `fulltext.nxml` sits next to the PDF it is strictly better:
sections are declared rather than guessed, tables are real tables, and
`<supplementary-material>` labels turn an opaque `..._MOESM3_ESM.xlsx` into
"Supplementary Table 3" by joining on the manifest's `original_name`.

Only one source is used per article — extracting both would double every paragraph
and leave a model to guess which copy to quote. XML yielding less than
`min_main_text_chars` is treated as front matter and the PDF is used instead.
References and citation markers are dropped: left in, `<xref ref-type="bibr">` turns
"as shown previously" into "as shown previously12,13".

A block's `label` is the *publisher's* name for the file, never the fetch
transport's — a manifest label used by two entries of one article is rejected and
recorded — and `label_source` says where it came from (`jats`, `jats_caption`,
`manifest`, `review`, `none`).

### Table cards: what a spreadsheet becomes

A supplementary table is not prose, and one sheet in the development corpus is
16,596 rows × 88 columns — it cannot go into a prompt and almost none of it would
help. What answers the curation questions is the **column**, not the row, so each
table becomes a card:

```
TABLE: Supplementary Table 1
File: supplementary/01_41591_2018_269_MOESM1_ESM.xlsx (sheet 'Supplementary Table 1')
Caption: Detailed demographic, clinical, and disease treatment data for all patients.
Shape: 29 data row(s) x 35 column(s); header on row 4
Columns (35):
    1. patient_code [text, 29 distinct] e.g. SMM5, SMM6, SMM2
    2. diagnosis [text, 5 distinct] = AL | MGUS | MGUS-MGRS | MM | SMM
    3. Sex [text, 2 distinct] = F | M
    4. Age [number, 22 distinct] (range 40-84, median 65)
  ... 31 further column(s) not shown
Sample rows:
  1) patient_code=SMM5 | diagnosis=SMM | Sex=M | Age=61
```

A column whose two distinct values are `{F, M}` answers *sex* outright; one of
`{0, 6, 24}` says "these are the timepoints". Numeric columns get a lower
enumeration bar, because 22 patient ages say nothing a range does not at ten times
the length.

**The card does not copy the data.** It records `data_ref` — file, sheet, sha256,
header row, first and last data row — so code wanting real values re-reads the
original at the exact offset the card was built from. `manuscript-extract table` is
that code, which is what makes `data_ref` a contract rather than a comment: it
re-opens the source, prints the rows, and says so if the file changed.

Header detection is the crux, and the reason is in `tables.py`: one real workbook
puts a title on row 1, a caption on row 2, a blank on row 3, and the header on
**row 4**. Confidence is `high` only when the row below has a different type profile
— numbers under text headers — and `confirmed` when a human said so.

### Every file gets a status

`extraction.json` accounts for every file in the article. This is as much the point
of the stage as the blocks are: a thin extraction has to be legible.

| status | meaning |
| --- | --- |
| `ok` | text or table cards were produced |
| `image_no_text` | a figure image; no extractable text (a vision pass would be needed) |
| `media_no_text` | audio or video |
| `data_file_skipped` | binary or columnar data (`.h5ad`, `.bam`, …), not prose |
| `no_text_scanned_pdf` | parses as a PDF but has almost no text: needs OCR |
| `no_text` | readable and genuinely empty, or everything inside was capped out |
| `unsupported_format` | no parser (`.doc`, `.rtf`, `.pptx`, non-zip archives) |
| `too_large` | over `max_file_mb`; recorded, not read |
| `missing` | in the manifest but not on disk |
| `unreadable` | corrupt, or a parser named its own failure |
| `garbled_text_encoding` | draws correctly and cannot be read: its fonts never say what their glyphs mean |
| `parser_error` | a parser raised; the file is named and the run continues |

The article is `complete` when the main text is usable and every file that should
have yielded text did; `partial` when something is missing; `failed` when there is
nothing to ask a question of. Images and media carry no blame — an article whose
only supplements are figures is still `complete`.

### Caveats

Beside the status, `extraction.json` carries a `caveats` list from a closed
vocabulary — things true about an extraction without being a per-file failure:

| caveat | blocks `complete`? | meaning |
| --- | --- | --- |
| `supplements_expected_but_missing` | yes | the fetch stage says files were listed and not retrieved |
| `main_text_thin` | yes | shorter than `min_main_text_chars`: front matter, not an article |
| `landing_page_only` | yes | the main text is a saved publisher landing page |
| `supplement_set_unverified` | no | supplements were fetched; no tier could confirm the set is complete |
| `manifest_entry_without_a_path` | no | a supplementary entry has no file on disk to read |

`supplement_set_unverified` deliberately does not block: it is the common outcome
for any page-scraping route, so it is a caveat, not a defect. Before these existed
the extractor never read the fetch stage's own verdict, so an article whose manifest
said `expected_but_missing` extracted as `complete` with an empty supplement list.

### When a PDF's fonts do not say what their glyphs mean

`garbled_text_encoding` is the one status here that is not about missing text. The
file that earned it, 10.1126/science.adf5357's Supplementary Materials, holds that
paper's only copy of its Materials and Methods, renders perfectly in any viewer, and
extracted as this:

    TheVe VWXdLeV ZeUe LQWeQded WR be Whe fLUVW e[SORUaWLRQV Rf ceOOXOaU...

124,178 characters of it, reported `ok`. Its text is drawn with subsetted fonts
under `/Encoding /Identity-H`, where a character code in the content stream is a
*glyph id* rather than a character, and the `/ToUnicode` CMap that would turn those
back into characters covers glyph 8 to glyph 75 and stops. MuPDF's fallback for a
code it cannot map is to emit the code, and in this font's glyph order a codepoint
sits 29 above its glyph id, so `i` (glyph 76) surfaces as `L`.

Two things follow, and the order matters.

**It is repaired from the font, not from the string.** The embedded font program
carries its own character map — the table a viewer uses to draw the page — and it
names every glyph the CMap left out. `pdf._repair_glyph_encoding` reads it and fills
the gaps before any page is laid out. Shifting the output string by 29 instead would
be fitted to one font's glyph order and would also be unable to tell a real `T` from
a `q`: both reach the text as `T`, and only the glyph id separates them. The repair
adds entries for codes the publisher's CMap leaves out and none that it covers, and
declines outright on any font where the two maps disagree — the evidence that a code
really is a glyph id there. Measured over the 972 PDFs in this corpus: 183 have a
font with a gap in its CMap, and exactly one of them comes out with different text —
the Science supplement. The other 182 gaps are for glyphs their documents never draw.

**Where it cannot be repaired, it is reported.** 10.1038/s41588-024-01702-0's
reporting summary has 6,869 glyphs and no character behind any of them: CID-keyed
CFF subsets, identity ordering, no ToUnicode, no character map, glyph names of the
form `cid00042`. Nothing in that file says what its glyphs mean, so reading it would
be a guess. It gets the status, its blocks are dropped rather than written out as
prose, and the article goes `partial`.

The rule asks the *document*, through the count of glyphs MuPDF could not name, and
not the prose. "This text has no English function words in it" flags 26 files in
this corpus of which one is broken — a supplementary figure PDF is mostly gene
symbols — and would say nothing at all about a paper written in another language.
Damage below `max_unnamed_glyph_fraction` is counted in `glyphs_unnamed` rather than
dropped, which is the same rule `glyphs_unmapped` already follows: the worst of it
is a figure's axis labels in a file whose captions are fine.

### Failures this stage is built to avoid

Each of these looked exactly like "there was nothing there" until something
checked: a strict-conformance workbook openpyxl reads as having zero sheets, an
unsized worksheet, a caption nested inside `<media>`, a heading glued to its
paragraph, a 23 MB "paragraph" that was really a TSV, files saved with no extension,
and a bot-check page holding 129 characters of user-agent string. One did not look
like that at all: a supplementary PDF that produced 124,178 characters of fluent
nonsense and called it a clean run. Each is pinned by a test and commented at the
code that enforces it; the corpus files that taught them are named in
`tests/test_extract_corpus.py`.

### The human review layer

Some things this stage cannot decide: whether a spreadsheet's first row is a header
or a first data row, whether the body of the article is actually here, whether a
`.pptx` nobody can parse holds the donor table. Those are cheap for a person and
impossible for the parser, so they are asked:

    manuscript-extract review <doi>                    # writes review-<slug>.html
    manuscript-extract review <doi> --apply answers.json

The sheet is one self-contained HTML page — stdlib `html.escape` and f-strings, no
CDN, no server — with the card text verbatim, a `file://` link to open the source
beside it, closed-set radios, and a Download button producing the JSON `--apply`
reads. A terminal gives a curator no way to open the spreadsheet next to the
question; CSV turns a multi-line card into one unreadable cell.

Questions are ordered by value per minute: **"is the article here" first**, because
it is one yes/no and every other answer for that article depends on it; then table
headers, which are the bulk of the work (bounded, ~15 seconds each, and a wrong one
silently corrupts every metadata answer drawn from that sheet); then unparseable
files, then supplement labels, then section spans, then sign-off last. Figure
images are never queued — most non-`ok` supplements are figures and nobody can
judge a `.jpg` by name.

Answers live in `reviews/<doi_slug>.json` at the repo root, checked in, appended
never rewritten. That location is forced: `store.evict_article` deletes everything
but `manifest.json`, and `corpus/` is gitignored, so a review kept beside the
article would die with a budget eviction and could never be committed.

An applied answer changes the next extraction — `header_confidence` becomes
`confirmed`, a cleared file stops blocking `complete` while staying listed beside
the human who cleared it, an unlabelled main-text span takes the section a curator
gave it (marked `section_source: review`, since one value for a whole span is
coarser than a heading the parser found), and the review file's sha is part of the
extraction key so the first correction is not discarded by the next
`manuscript-extract all`. A re-fetch drops the answer (`stale_bytes`: it was about
bytes that are gone); a parser change keeps it but re-asks the question
(`stale_shape`: the claim was about the bytes, not the parser).

`manuscript-extract status --needs-review` lists only what is queued or stale.

### Caps

Every cap lives in `manuscript_harvest/extract/limits.py`, each with a comment
saying why it exists, and any can be overridden under `extract.limits` in
`config.yaml`. Nothing a cap drops is silent: it is recorded in `extraction.json`
and in the affected table card's notes, so a thin result reads as "capped" rather
than "empty". Because `limits` is part of the extraction key, changing one
re-extracts rather than reusing a result made under the old value.

## Select: blocks → the evidence one question needs

    manuscript-select readiness                  # can a "not found" be believed?
    manuscript-select candidates <doi>           # what a regex finds, with no role
    manuscript-select pack <doi> --sections methods,data_availability
    manuscript-select sheet --out labels.html    # a page to hand-label
    manuscript-select label --apply labels.json --truth truth/accessions
    manuscript-select verify answers.json --article <doi>
    manuscript-select eval answers/ --truth truth/accessions --baseline

Offline like the extract stage. Four jobs, and the reason they are here rather than
downstream is that every one of them can be tested without a model.

**`readiness` — what emptiness is allowed to mean.** Ask "which datasets did this
paper deposit?" of 10.1016/j.cell.2019.08.008 and the honest answer is *unknown*: its
`fulltext.status` is `download_failed`, its main text is the publisher's saved landing
page, and its extraction carries `landing_page_only`. There is no Methods section to
have missed anything in. Five states — `ready`, `ready_with_caveats`,
`text_unavailable`, `not_extracted`, `not_fetched` — of which the first two mean a
negative answer is worth recording, and 27 of the 37 development-corpus directories
reach them. `ready_with_caveats` carries a `gaps` list, so the answer states its own
bound instead of implying there wasn't one.

**`pack` — filter, rank, budget.** The subtle part is that **a section preference is
never a filter**. Corpus-wide 599 of 4,009 main-text paragraphs carry `section: null`,
because an unrecognised heading leaves the field unset rather than guessing — and not
only on the broken articles: 39 of the 86 in 10.1126/science.abo0510, which is `ready`
with no caveats. So `prefer` ranks in three tiers — the sections asked for, then
`null`, then everything else — and an unlabelled block outranks a known-other one,
because a null section is missing information about a block rather than information
that the block is elsewhere. Only the character budget drops anything, and it records
`dropped_ids` rather than a count, because a pack that skipped a higher-ranked block
to fit a lower one is exactly the silent reordering that makes a wrong negative look
considered.

**`candidates` — the half a regex does better.** Over the 27 believable articles the
finder gets 69 distinct study-level accessions in 21 of them, and says nothing about
what any of them are. That gap is the point:

| DOI | found | actually deposited | naive precision |
|---|---|---|---|
| 10.1002/ctm2.1356 | 5 | 1 (GSE208532) | 0.20 |
| 10.1016/j.isci.2023.106877 | 10 | 0 — every one reanalysed | 0.00 |

Deciding `own` from `reused` needs the sentence around the identifier, so nothing here
does it; a candidate carries `role: None` and the mentions that let something else
decide. Sample-level ids (`GSM`, `SRR`, `ERS`, `SAMEA`) are found, marked, and kept out
of what gets adjudicated: on 10.1126/science.aat5031 they live in a table card whose
column enumeration stops at `max_unique_values`, so whatever the finder returns is a
sample of a cap rather than the paper's deposit.

**`verify` — a quote against the block it cites, not the article.** Confirming a quote
is a substring of a concatenated article cannot tell whether the sentence came from
Methods or from the peer-review PDF bundled as a supplement. Four levels are tried and
**which one matched is recorded**: `exact`, `normalized` (NFKC, folded dashes and
quotes, collapsed whitespace), `loose` (alphanumerics only), and `fuzzy` above 0.92
coverage. A quote that is real but real *elsewhere* returns `wrong_block` and names
where. `fuzzy` is refused below 40 comparable characters, because "abcd" against
"abXcd" scores coverage 1.00 on what is not a quotation.

### Measuring it: `truth/` and `eval`

`eval` scores an answer against hand labels, per article and micro-averaged.
`--baseline` scores "every study accession found is a deposit" — what a pipeline does
when nobody adjudicates the role — so the number a change must beat is recomputed from
the same labels rather than remembered.

**`complete: true` in a label is what makes recall a number.** A label listing one
deposit bounds precision immediately, but says nothing about recall unless the labeller
asserts they looked for others and found none. So recall is computed only over complete
labels, the rest are reported as `partial`, and the headline says how many were
excluded — the same rule as `none_listed` against `unknown_none_found`, applied to the
gold standard. A label also carries `finder_missed`, so the labelling pass doubles as a
test of the pattern list.

The labels themselves are **not in this repository**: what counts as an "own deposit"
is defined by the question that produced it, so they travel with that question and
reach this code through `--truth`. Same arrangement as `manual_fetch.yaml`, whose spec
is checked in while the bytes are not. Note that a truth label and a `reviews/` answer
have opposite lifetimes and must not be filed together: a review answer is about bytes
and parser shape, so a re-fetch expires it, while "GSE208532 is this paper's deposit"
survives a re-fetch, a parser rewrite and a PyMuPDF upgrade.

`sheet` writes one self-contained HTML page for the whole corpus — 69 candidates across
21 articles plus 6 with none to confirm, one sitting — with the sentence each accession
appeared in quoted underneath and closed-set radios. `label --apply` splits the download
into per-article files and **refuses a half-filled one** unless `--partial`: a blank role
scores as a deliberate `reused` call, quietly rewarding a model for the labeller's
unfinished work.

## Skills

`.claude/skills/` holds packaged answers to the question the three stages
deliberately do not answer: *what to ask of the extracted text.* They are Claude
Code skills — a directory with a `SKILL.md` and whatever files it needs — and they
load automatically for anyone working in this repo.

### Why they are here and not in the package

They fail the `manuscript_harvest` admission test: a prompt cannot be tested without
a model. So they are kept as a sibling of the package, not a part of it. That
separation is mechanical, not just stated:

- nothing under `manuscript_harvest/` imports them,
- `pytest.ini`'s `testpaths = tests` means their tests are not collected by this
  repo's suite, and `ruff` is scoped to `manuscript_harvest` and `tests`,
- each skill vendors its own copy of what it needs and takes the corpus path as an
  argument, so it runs against any directory of extracted papers, not just this one.

The trade-off is real and worth naming: a skill's own tests do not run in this
repo's CI, so a skill can rot while the badge stays green.

### `perturbation-detection`

Classifies extracted papers as **perturbed / not perturbed / unclear**, for
single-cell biocuration. The rule that makes it non-trivial: a paper counts only if
a perturbed sample was *itself* profiled by a single-cell or single-nucleus
sequencing assay. A perturbation somewhere in the paper plus a qualifying assay
somewhere in the paper is not enough — papers routinely perturb cells for a bulk
RNA-seq, qPCR, Western or flow readout while the single-cell dataset comes from
separate untreated material.

    cd .claude/skills/perturbation-detection
    python -m pe.prepare  --set papers.txt --work work --corpus ../../../corpus
    ./pe/run_headless.sh work 4
    python -m pe.validate --work work --write-corpus --corpus ../../../corpus
    python -m pe.summarize --work work
    ./pe/watch.sh work            # in another shell: progress, then a notification

Only the second step needs a model, and it needs no API key: it runs `claude -p`
against the logged-in Claude Code session. `pe/pending.py` makes a run resumable,
which matters because session limits rather than papers are the binding constraint
at scale — a paper counts as done only if its result parses, carries every required
field, and its `sources_seen` matches the manifest, so a partial write is re-run
instead of silently accepted.

The design choice worth copying into any similar skill: **the harness does not trust
the model's own answer.** Every quote is verified against the specific source it
claims, unlocatable quotes are dropped, a perturbation left with no verified quote is
dropped whole, and only then is the paper-level call recomputed. Both values are
kept, so the gap between them measures fabricated evidence directly. Over 77 papers
it has been 0, with 544/544 quotes verified and no misattributions.

This mirrors the `## Design` principle above: emptiness you cannot account for is
worthless, so a paper whose text is truncated or missing its Methods can never be
reported as "not perturbed" — it is capped at "unclear" and routed to re-fetch.
Positives are not capped, because missing text can conceal evidence but cannot
manufacture it.

Read `.claude/skills/perturbation-detection/SKILL.md` to run it, and `prompt.md` in
the same directory for the criteria — that file, not this one, is the source of
truth for what counts as a perturbation.

## Tests

    pip install -r requirements-dev.txt
    python -m pytest tests -q            # everything offline: no network, no browser
    python -m pytest tests -q -k budget  # just the matching tests
    python -m pytest tests --cov=manuscript_harvest --cov-report=term-missing

Two CI jobs: the suite across Python 3.10–3.13 with coverage gated at 90%, and
`ruff check --select F` — pyflakes correctness only, deliberately not a formatting
gate, because this repo's style is hand-maintained and reformatting would bury the
comments explaining why the odd branches exist.

The coverage figure CI reports is always the no-corpus one:
`tests/test_extract_corpus.py` skips itself without a local `corpus/`, so running
with one present reads about a point higher than the badge.

| File | Covers |
|---|---|
| `tests/fakes.py` | fixtures shaped from real files and API responses; fake HTTP, page, browser context |
| `tests/test_units.py` | DOIs, validation, store + size budget, adapters, HTTP politeness, config |
| `tests/test_pipeline.py` | tier orchestration and the full fetch status taxonomy |
| `tests/test_browser_tier.py` | the browser tier offline — proxy rewriting, settling, challenges, caps |
| `tests/test_open_access_tiers.py` | the four open-access tiers end to end: which status each outcome earns |
| `tests/test_fetch_cli.py` | the fetch CLI: missing-login warning, proxy breaker, exit codes, `usage`/`prune`/`check` |
| `tests/test_extract_units.py` | sections, table cards, and each parser: JATS, PDF, xlsx, docx, HTML, zip |
| `tests/test_extract_article.py` | source choice, per-file statuses, the extraction record, the CLI |
| `tests/test_extract_corpus.py` | the real files that taught the extractor its rules — skipped without `corpus/` |
| `tests/test_review.py` | the review layer: what is asked, in what order, and when an answer expires |
| `tests/test_section_audit.py` | the section audit: alignment, scoring, and what must *not* count as an error |
| `tests/test_manual_fetch_units.py` | the comparison rules: publisher filename conventions, archives, versions |
| `tests/test_manual_fetch_live.py` | fetches those same DOIs for real and compares — off unless asked for twice |
| `tests/test_select_units.py` | readiness states, the section rule, the accession finder, the quote verifier, scoring |
| `tests/test_select_cli.py` | the select CLI: exit codes, and the warnings that stop a bad answer being kept |

They lean on failure cases rather than happy paths, because every bug found so far
was a *plausible-looking success*. Where a test pins a rule a live batch disproved,
its docstring names the DOI, so a failure explains itself.

### Checking the fetcher against papers fetched by hand

`corpus/` tests what the extractor does with files already fetched. It cannot tell
you whether the *fetch* was right — whether a paper with twelve supplements came
back with twelve. That needs ground truth: a human saving everything by hand.

    MANUSCRIPT_HARVEST_MANUAL_DIR=~/manual-fetch-papers \
    MANUSCRIPT_HARVEST_MANUAL_NETWORK=1 \
    python -m pytest tests/test_manual_fetch_live.py -v

    # or directly, printing a per-check table per paper:
    python -m manuscript_harvest.fetch.manual_fetch verify

`manual_fetch/manual_fetch.yaml` is checked in and describes eight papers; the bytes
are not, for the same reason `corpus/` is ignored. `verify` fetches into
`manual-fetch-run/`, a scratch corpus kept away from your real one, and re-fetches
every time — comparing against a cached corpus would check the last run's bytes
rather than today's code (`--cached` opts out while you work on the rules).

`bootstrap` adds a paper, and **writes the whole spec from its arguments rather than
merging**, so every existing paper must be listed too. Omitting one is not silent: a
run that would drop an article the spec already holds is refused and names it,
compared as DOIs rather than as a count, so swapping one paper for another is caught
too. `--replace` accepts the loss deliberately; `--out` drafts to a scratch file.

The comparison is deliberately asymmetric, and `manual_fetch.py` explains each rule
at the code: the article PDF is compared on page count and identity but never on
bytes (publishers stamp per-download watermarks), page counts only between the same
rendition, supplements as a set of content hashes with archives normalised both
ways. The check that justifies the exercise is `supplementary_status` — no synthetic
fixture can catch a paper that really has supplements, that fetch comes away from
with none, reported as `none_listed`. It cannot run in CI, so it is a diagnostic,
and what it finds belongs in `tests/fakes.py` afterwards.

### How accurate is the PDF section labeller?

JATS declares its sections, so an article saved in both renditions scores the
heuristic for free. `section_audit.py` aligns the two by shared eight-word shingle
and compares labels paragraph by paragraph:

    python -m manuscript_harvest.extract.section_audit --corpus-dir corpus
    python -m manuscript_harvest.extract.section_audit --fail-under 0.85

`--fail-under` makes it a gate, and `tests/expected_section_scores.json` holds a
per-slug baseline `tests/test_extract_corpus.py` asserts against, so a regression in
one article cannot hide behind an improvement in another. A real improvement
rewrites that file in the same commit and the diff shows the gain. **The baseline is
tied to the PyMuPDF version it was measured on** — see the note beside it, which is
why `requirements.txt` floors pymupdf rather than accepting anything recent.

Articles with no XML/PDF pair are named before the headline rather than dropped from
it, so the percentage never reads as corpus coverage. Treat it as a baseline to
improve against, not a published figure: the samples are small. The audit paid for
itself twice on its first run, producing the low-value-heading rule and the
`STAR★METHODS` glyph fix, both commented at `sections.py`.

## Known limitations

Deliberate non-goals first — scope commitments, not gaps:

- **No OCR** (text-based PDFs only), and **no vision pass** (figure images are
  recorded as `image_no_text` and not read).
- **No table structure recovered from PDFs.** `page.find_tables()` exists, but a
  table found in a PDF has no stable `data_ref` to re-read, and the card contract is
  built on one.
- **PDF reading order is left in insertion order.** Sorting is the obvious fix and
  measurably worse on every article tested; pinned by
  `test_pdf_reading_order_is_not_improved_by_sorting` rather than argued about.
- **An unrecognised heading leaves `section` as `null`** rather than guessing. A
  wrong section is worse than none — which is why `select`'s section preference ranks
  instead of filtering.
- **No model client, no prompts, no schemas — in the package.** `select` packs
  evidence, finds candidates and verifies quotes; it never asks anything. The
  boundary moved from `blocks.jsonl` to "code that can be tested without a model".
  Prompts now exist in the repo, under `.claude/skills/`, and are outside that
  line by construction: nothing in `manuscript_harvest` imports them and the test
  suite does not collect them. See [Skills](#skills).
- **One aspect implemented, not a general extractor.** `candidates` knows accession
  syntax. Donor counts, ages, assays and perturbation each need their own finder, and
  only the accession one has been measured.
- **A reviewer's answer is scoped to bytes, not to the parser.** There is
  deliberately no way to record one that outlives a change to the file it was about.
  A truth label is the opposite and is stored elsewhere for that reason.

Gaps and dead ends, each with the detail at the code that handles it:

- No parser for `.doc`, `.rtf`, `.pptx` or non-zip archives; legacy `.xls` needs the
  optional `xlrd`.
- **ScienceDirect blocks programmatic access** — no supplement links and no PDF href
  even to a real browser — so a stubbed Elsevier page is retried at cell.com, where a
  human downloads these files from. cell.com carries Cell Press but not all of
  Elsevier, so for other Elsevier journals the retry leaves the proxy and the failure
  names the host it was redirected to rather than suggesting `--headed`
  (`proxy_browser.py`, `adapters/publishers.py`).
- **A few PMC supplement sets stay refused**: listed, `hasSuppl: Y`, every route
  answering the proof-of-work page or 403. Reported as `expected_but_missing`. The
  browser tier's PMC path needs a warm profile or `--headed`; on a cold profile
  headless gets a reCAPTCHA and says so rather than reporting an empty list.
- **The PMC OA tarball route 404s** over HTTPS and FTP — that tree now lists only
  `deprecated/`. Off by default (`fetch.try_oa_package`); the tier still runs for its
  OA-subset signal and the unpack path is kept and tested in case it returns.
- **NCBI's FAIR-SMART supplementary API is documented but dead**, erroring on every
  input including its own example IDs (`pmc_supplements.py`).
- Publisher supplement URL construction is implemented only for Springer/Nature;
  others fall back to the browser tier.
- Files over `fetch.max_file_mb` are recorded, not fetched. Independently, the
  browser transport cannot return anything near ~512 MB because Playwright marshals
  bodies as strings — raising the cap will not help, and the failure says to fetch
  by hand.
- `hasSuppl` is trusted for indexed journal articles but **not** for preprints or
  articles Europe PMC does not hold, both of which report `N` over files that exist
  (`fetcher.suppl_flag_is_authoritative`).
- A versioned DOI falls back to its unversioned form when the versioned one is not
  indexed (eLife reviewed preprints); only 1–2 trailing digits count, so article
  numbers are never truncated.
- **Two tables side by side in one sheet become one card.** The split is row-wise
  only; an all-blank separator column is profiled as data (`tables.split_blocks`).

## Prior art

[`pygetpapers`](https://joss.theoj.org/papers/10.21105/joss.04451) (Garg et al.,
JOSS 2022, `10.21105/joss.04451`) covers the same Europe PMC endpoints. It is
query-oriented where this is DOI-oriented, so the endpoints are called directly
rather than taking the dependency.

## License

MIT — see [LICENSE](LICENSE).

## Not in this repository

Deliberately, **in the `manuscript_harvest` package: no model client, no prompts, no
schemas, no rubrics, and no labels.** Since the skill landed this needs saying
precisely rather than as a slogan — prompts, a schema and a confidence rubric now do
exist in the repo, under `.claude/skills/`, and the paragraph below is about where
they are *not*.

The line is *code that can be tested without a model.* Retrieval, candidate finding,
quote verification and the eval runner all clear it, so they are the `select` stage,
tested offline like everything else. What does not clear it stays out: the question
text, the output schema, the confidence rubric, and the truth labels — whose meaning
depends on the question that produced them, and which reach `eval` through `--truth`.

This is narrower than the boundary this file first drew at `blocks.jsonl`. That
version had `select`'s four jobs downstream too, which meant every consumer
re-implemented the section-ranking rule slightly differently and none of them had
tests. Forcing a *choice of model* here would still make the harvesting code harder to
reuse; shipping a tested regex for GEO accession syntax does not.

Also absent, and gitignored: `corpus/`, the fetched papers, because the bytes are
the publishers' and every article can be re-fetched from its DOI; and the
hand-fetched papers `manual_fetch/manual_fetch.yaml` describes, for the same
reason. The spec that makes claims about them is checked in, because that is the
part worth reviewing.
