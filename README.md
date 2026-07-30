# manuscript-harvest

[![tests](https://github.com/Lattice-Data/manuscript-harvest/actions/workflows/tests.yml/badge.svg)](https://github.com/Lattice-Data/manuscript-harvest/actions/workflows/tests.yml)
[![coverage](https://coveralls.io/repos/github/Lattice-Data/manuscript-harvest/badge.svg?branch=main)](https://coveralls.io/github/Lattice-Data/manuscript-harvest?branch=main)

Fetch a published paper from its DOI — the article and every supplementary file —
and turn it into blocks of text with provenance: paragraphs, headings, captions,
and structured summaries of supplementary tables, each carrying the file and
location it came from.

It is two stages and nothing else. There is no model in this repository: what to
ask of the extracted text, and with what, is a separate decision.

    DOI ──▶ acquisition (open-access APIs, then library proxy) ──▶ PDF + supplements
                                        │
                                        ▼
             extraction ──▶ extracted/blocks.jsonl + extraction.json

## Design

Two principles account for most of the code:

**Push everything deterministic into code.** Identifier resolution, tier
selection, PDF validation, header detection in spreadsheets — all of it is plain
code with tests, so a result can be re-derived rather than re-generated.

**Never report emptiness you cannot account for.** Every bug found so far looked
like a *plausible success*: a paywall page served as `application/pdf`, a
publisher claiming supplements exist while none arrive, a workbook with three
sheets that a library reads as having none, a bot-check page holding 129
characters of user-agent string. So both stages carry an explicit status taxonomy
and record what they did not get, with the reason. A downstream answer of "no
perturbations found" is only meaningful if the record can show the text was
actually there.

**About the numbers below.** Counts like "40 of the 63 articles" or "323 figure
images" are measurements over the 63-paper development corpus this code was built
against — single-cell and genomics papers across Nature, Cell Press, Wiley,
Science, eLife, PMC and bioRxiv. That corpus is not in this repository (the bytes
are the publishers'), but it is why each rule exists, and
`tests/test_extract_corpus.py` re-checks the specific files that motivated the
non-obvious ones whenever a corpus is present.

## Install

    python -m venv .venv && source .venv/bin/activate
    pip install -e .

    manuscript-fetch get 10.1038/s41467-023-40505-5   # download
    manuscript-extract all                            # extract

Without installing, the same two entry points are reachable as
`python -m manuscript_harvest.fetch.cli` and
`python -m manuscript_harvest.extract.cli`; `pip install -r requirements.txt` is
enough for that.

Optional extras:

    pip install -r requirements-browser.txt   # the library-proxy tier (Playwright)
    pip install -r requirements-dev.txt       # pytest, to run the suite
    pip install xlrd                          # legacy binary .xls supplements

### Before you run it against a publisher

Set `fetch.contact_email` in `config.yaml` to your own address. Crossref, NCBI and
Europe PMC all ask callers to identify themselves, and it is what keeps a polite
client out of the rate-limited pool. It ships unset, in which case requests go out
without one.

The last-resort tier drives a real browser through an authenticated institutional
proxy (the default example is Stanford's) to reach articles your library
subscribes to. That is ordinary licensed access, but it is *your* licence: check
your institution's terms and your publishers' agreements, keep request rates
polite — the default is one request per host every 3 seconds — and do not use this
to bulk-download a publisher's catalogue. Everything the open-access tiers do
needs no credentials and no browser, and `--oa-only` guarantees no browser ever
opens.

## Acquiring papers: DOI → PDF + supplementary files

    manuscript-fetch get 10.1038/s41586-021-03852-1
    manuscript-fetch batch dois.txt --report fetched.jsonl

Writes `corpus/<doi_slug>/` containing `fulltext.pdf`, `supplementary/`, a
`fulltext.nxml` when the JATS XML comes free, and a `manifest.json` recording
where every byte came from. Re-running is a no-op unless you pass `--force`.

Sources are tried in order, and the first four need no credentials and no
browser. `--oa-only` guarantees nothing ever opens one:

| Tier | What it gives | Status |
|---|---|---|
| `europepmc` | PDF via `fullTextUrlList`, JATS XML, supplements ZIP | works; the ZIP is not universal |
| `pmc_supplements` | PMC lists the files, the publisher's OA host serves them | works |
| `pmc_oa` | `oa.fcgi`: mainly an "is this in the OA subset" signal | the tarball tree is being retired (below) |
| `biorxiv` | 10.1101 preprints: PDF, JATS, supplements | works |
| `proxy_browser` | Stanford library proxy + real browser | needs a one-time login |

Two findings from testing against the live services are worth knowing, because
they are why the tiers are arranged this way:

- **`hasSuppl: Y` does not mean Europe PMC has the files.** For
  `10.1038/s41586-021-03852-1` the search API reports supplements exist, and the
  `supplementaryFiles` endpoint returns 404. The files are real; they are just
  somewhere else. This is what `pmc_supplements` is for.
- **PMC's `/bin/` downloads sit behind a proof-of-work page** ("Preparing to
  download ...") that no plain HTTP client can clear. But the publisher's own
  open-access host serves the same files directly, so the file *list* comes from
  PMC and the *bytes* come from the publisher. When no publisher pattern is
  known, the browser tier clears the challenge instead.
- **The proof-of-work challenge is per-session, not per-file.** Navigating to one
  `/bin/` URL runs the script and sets cookies; every subsequent file then fetches
  normally. Waiting for a browser download event does *not* work — PMC
  author-manuscript supplements are often `.mp4`, which Chrome renders inline, so
  no download ever fires.
- **A headless browser is treated with more suspicion than plain HTTP.** NCBI
  serves headless Chrome a reCAPTCHA ("Checking your browser") while `requests`
  gets the same page fine. A visible browser passes, so that case reports the
  problem and tells you to re-run with `--headed`.

### Paywalled articles

    manuscript-fetch login     # headed browser, one time
    manuscript-fetch check     # is the saved session still good?

Cardinal Key is a WebAuthn credential and Duo cannot be scripted, so **the login
itself is not automatable and this does not pretend otherwise**. `login` opens a
real browser and you sign in by hand. Needs `requirements-browser.txt`.

It does not ask you to press a key: the browser takes keyboard focus during
login, so a terminal prompt just swallows an Enter that never arrives. Instead it
polls until the article is actually reachable, and also stops if you simply close
the browser window.

Two things learned from getting this wrong:

- **A persistent Chrome profile is not enough.** EZproxy issues *session*
  cookies, and Chrome discards those on restart — measured: three
  `.idm.oclc.org` cookies present right after login, gone by the next launch. So
  `login` also snapshots cookies to `~/.manuscript-harvest/storage_state.json`
  (Playwright's `storage_state` keeps session cookies) and later runs re-inject
  them. **That file is a live credential — treat it like one.**
- **`check` downloads the PDF and validates it.** Finding a `citation_pdf_url` is
  not evidence of access; publishers emit it on paywalled articles too, because
  Google Scholar requires it. The probe URL is also deliberately a *paywalled*
  article — pointed at an open-access one, `check` passes with no session at all
  and tells you nothing.

EZproxy sessions are short-lived, so expect to re-run `login` periodically;
`check` tells you when. A dead session reports `session_expired`, not a silent
failure.

### What the statuses mean

The point of this vocabulary is that "no supplements" and "we failed to get the
supplements" must never look alike — the same trap as an extraction that returns
an empty list and gets logged as a clean success.

`fulltext.status`: `ok` · `scanned_pdf_suspected` (saved, but has no extractable
text — needs OCR) · `paywalled` · `not_in_oa_subset` · `proxy_not_configured` ·
`session_expired` · `link_resolver_error` (a resolver answered "no such article
here", so this is not a page we failed to parse) · `not_a_pdf` ·
`download_failed` · `not_found`

`supplementary_status`:

| Status | Meaning |
|---|---|
| `none_listed` | the publisher says there are none (`hasSuppl: N`) |
| `fetched` | they exist and we have them |
| `partial_failure` | some arrived; at least one failed |
| `expected_but_missing` | `hasSuppl: Y` and we came away with nothing — **the bug case** |
| `page_not_parsed` | a page loaded but no file list could be read from it |
| `unknown_none_found` | nobody said whether any exist, and none were found |

A refusal is never written to disk as `fulltext.pdf`: acceptance requires PDF
magic bytes (not the `Content-Type` header, which lies), a successful parse, and
a body that does not read like a purchase page.

Prior art: [`pygetpapers`](https://joss.theoj.org/papers/10.21105/joss.04451)
(Garg et al., JOSS 2022, `10.21105/joss.04451`) covers the same Europe PMC
endpoints. It is query-oriented where this is DOI-oriented, so the endpoints are
called directly rather than taking the dependency.

### Disk usage

Articles average **~40 MB**, so a few hundred papers is tens of gigabytes.

    manuscript-fetch usage --by-size     # what is taking the space
    manuscript-fetch prune --dry-run     # what a prune would remove
    manuscript-fetch prune --max-gb 20   # actually evict

Set `fetch.max_corpus_gb` and the budget is enforced automatically after every
fetch, evicting **oldest first**. Three things worth knowing:

- **Eviction keeps the manifest.** Only the bytes go; the record of what existed —
  filenames, sizes, sha256, which tier supplied it — stays, and the article is
  marked `evicted`. A corpus that forgets what it deleted is worse than one that
  never had it. Re-fetch with `--force`.
- **An evicted article is not re-fetched** by a later run. Treating eviction as
  "incomplete" would make the next batch re-download exactly what the budget just
  freed and thrash against the cap forever.
- **The newest article is never evicted** — it is the one you just asked for.

There is no useful "drop the media" saving: measured across 63 papers, PDFs are
45% and spreadsheets/CSV another 25%, with video and images only 8%. The bulk is
the content you actually want, so staying inside a budget means giving up whole
articles rather than trimming fat.

## Extraction: corpus files → blocks

    manuscript-extract one 10.1038/s41467-023-40505-5
    manuscript-extract all                # every article; skips unchanged ones
    manuscript-extract status             # coverage across the corpus
    manuscript-extract show <doi> --section methods
    manuscript-extract show <doi> --kind table --full

Offline: it reads only what the acquisition stage already wrote. Output lands
beside the article, so an eviction removes it along with the rest of the payload:

    corpus/<doi_slug>/extracted/
        blocks.jsonl      every block, in document order, with provenance
        article.md        the same content rendered for a human to read
        extraction.json   what each file yielded, and what it did not

### Blocks, not one big text file

A block is one paragraph, heading, figure caption, or table card:

```json
{"index": 148, "kind": "paragraph", "role": "main_text", "origin": "jats",
 "source_file": "fulltext.nxml", "locator": "body/sec[4]/p[2]",
 "section": "methods", "label": null, "chars": 412,
 "text_sha256": "9f2c…", "text": "Nuclei were isolated from frozen heart…"}
```

Two things a single concatenated blob cannot do:

- **Provenance.** Checking that a quote is a verbatim substring of what a model
  was given is not enough on its own: with a blob it cannot say *which* of
  thirty supplementary files the quote came from. A block can be pointed at:
  `sheet 'Table S6' of supplementary/03_mmc7.xlsx`.
- **Selection.** Different questions want different slices — organism and library
  kit live in Methods, sample counts in Results, and Introduction is mostly other
  people's work. Blocks can be filtered by `section`, `kind` and `role` before
  anything is sent to a model.

`blocks.jsonl` is written with sorted keys and no timestamps, so extracting the
same bytes twice produces a byte-identical file. That makes an extraction safe to
hash and lets a parser change be reviewed as a diff.

### JATS XML first, PDF second, landing page last

40 of the 63 articles here carry Europe PMC's `fulltext.nxml` next to the PDF, and
it is strictly better: sections are declared rather than guessed from a line that
happens to sit alone, tables are real tables, and `<supplementary-material>`
labels turn an opaque `41467_2023_40505_MOESM3_ESM.xlsx` into "Supplementary
Table 3" by joining on the manifest's `original_name`. 271 such labels were
recovered across 36 files.

Only one source is used per article — extracting both the XML and the PDF would
double every paragraph and leave a model to guess which copy to quote. XML that
yields less than `min_main_text_chars` is treated as front-matter-only and the PDF
is used instead. Measured over this corpus: 39 articles from JATS, 14 from PDF,
10 landing-page-only.

References are dropped. A model asked for perturbations will otherwise take one
from a reference title. Citation markers are dropped too: left in,
`<xref ref-type="bibr">` turns "as shown previously" into "as shown
previously12,13", which is noise in a quote and worse in an evidence check.

### Table cards: what a spreadsheet becomes

172 of the supplementary files here are `.xlsx`, and a supplementary table is not
prose. One sheet in this corpus is 16,596 rows × 88 columns; it cannot go into a
prompt and almost none of it would help if it did. What answers the curation
questions is the **column**, not the row, so each table becomes a card:

```
TABLE: Supplementary Table 1
File: supplementary/01_41591_2018_269_MOESM1_ESM.xlsx (sheet 'Supplementary Table 1')
Caption: Detailed demographic, clinical, and disease treatment data for all patients.
Shape: 29 data row(s) x 35 column(s); header on row 4
Columns (35):
    1. patient_code [text, 29 distinct] e.g. SMM5, SMM6, SMM2
    2. diagnosis [text, 5 distinct] = AL | MGUS | MGUS-MGRS | MM | SMM
    4. Sex [text, 2 distinct] = F | M
    5. Age [number, 22 distinct] (range 40-84, median 65)
Sample rows:
  1) patient_code=SMM5 | diagnosis=SMM | Sex=M | Age=61
```

A column whose two distinct values are `{F, M}` answers *sex* outright; a column
of `{0, 6, 24}` says "these are the timepoints". Numeric columns get a lower
enumeration bar than text ones, because 22 patient ages say nothing a range does
not, at ten times the length.

**The card does not copy the data.** It records `data_ref` — file, sheet, header
row — so code that wants the real values re-reads the original file at the exact
offset the card was built from. Duplicating a 2.4 GB corpus to paraphrase it would
be the wrong trade.

Header detection is the crux. `41591_2018_269_MOESM1_ESM.xlsx` puts a title on
row 1, a caption on row 2, a blank on row 3, and the real header on **row 4**;
anything that assumes row 1 reads the caption as column names and the column names
as data. The rule: skip blanks, treat a row with a single populated cell as a
title or caption line, then take the first row that is wide relative to the table
and reads like labels. Confidence is reported as `high` only when the row below
has a different type profile (numbers under text headers) — the evidence that
separates a header from a first data row of gene names.

### Every file gets a status

`extraction.json` accounts for every file in the article. This is the point of the
stage as much as the blocks are: a thin extraction has to be legible.

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

The article is `complete` when the main text is usable and every file that should
have yielded text did; `partial` when something is missing; `failed` when there is
nothing to ask a question of. Images and media carry no blame — an article whose
only supplements are figures is still `complete`.

Across the 63 articles: 42 complete, 12 partial, 9 failed (all nine are the
Elsevier landing-page-only articles), producing 23,477 blocks and 1,188 table
cards. Supplementary files: 302 `ok`, 323 `image_no_text`, 10
`no_text_scanned_pdf`, 4 `unsupported_format`.

### Failures this stage is built to avoid

Each of these looked exactly like "there was nothing there" until something
checked. They are pinned by tests, and the corpus files that taught them are named
in `tests/test_extract_corpus.py`.

- **A strict-conformance workbook.** `mmc7.xlsx` uses ISO-29500 *strict* namespaces.
  openpyxl reads those as having zero worksheets — no exception, no warning — so a
  file holding `sampleID, Age, Sex, CoVID-19 severity` reported as empty. The
  namespaces are rewritten in memory and the workbook re-opened.
- **A worksheet with no declared dimensions.** openpyxl raises
  `ValueError: Worksheet is unsized` from `calculate_dimension()`, so this stage
  never calls it.
- **A caption nested one level down.** Springer puts a supplement's `xlink:href`
  and caption inside `<media>`, not on `<supplementary-material>`; reading only
  direct children found labels for none of the 40 XML files.
- **A heading glued to its paragraph.** Nature's PDFs emit
  `"Methods Data collection Nuclei isolation from adult heart tissue…"` as one
  layout block, so whole-line heading matching found no sections at all in them.
  Splitting on a leading section name lifted Methods detection to 47 of 53 PDFs.
  The guard against splitting an ordinary sentence is that a heading is followed by
  the start of a new one — `"Results of the assay"` does not split.
- **A 23 MB "paragraph."** `TableS8.txt` is a TSV whose first line is a caption.
  Requiring every line to agree on the delimiter count sent it down the prose path
  as a single block; the count is now judged by its mode.
- **Thirteen files called `NN_url`.** Saved by the browser tier with no extension.
  Magic bytes decide what they are, with `Content-Type` only as a fallback — the
  same order, and for the same reason, as `manuscript_harvest/fetch/validate.py`.
- **A bot-check page reported as an article.** Nine Elsevier `landing.html` files
  hold 129 characters: the browser's own user-agent string. A page with no citation
  metadata and less than `min_landing_chars` of text is named an interstitial, and
  `classify_denial` is reused to say which kind when it can tell.

### Caps

Every cap lives in `manuscript_harvest/extract/limits.py`, each with a comment saying why it
exists, and any of them can be overridden under `extract.limits` in `config.yaml`.
Nothing a cap drops is silent: it is recorded in `extraction.json` and in the
affected table card's notes, so a thin result reads as "capped" rather than
"empty".

## Tests

    pip install -r requirements-dev.txt
    python -m pytest tests -q            # everything offline: no network, no browser
    python -m pytest tests -q -k budget  # just the matching tests
    python -m pytest tests --cov=manuscript_harvest --cov-report=term-missing

CI gates coverage at 70%, a little under where it stands, so the check fires on a
real regression rather than on noise. The figure it reports is always the
no-corpus one — `tests/test_extract_corpus.py` skips itself without a local
`corpus/`, so running with one present reads higher than the badge.

The tests live in `tests/` and run under pytest:

| File | Covers |
|---|---|
| `tests/fakes.py` | fixtures shaped from real files and API responses; fake HTTP, page, browser context |
| `tests/test_units.py` | DOIs, validation, store + size budget, adapters, HTTP politeness, config |
| `tests/test_pipeline.py` | tier orchestration and the full acquisition status taxonomy |
| `tests/test_browser_tier.py` | the browser tier offline — proxy rewriting, settling, challenges, caps |
| `tests/test_extract_units.py` | sections, table cards, and each parser: JATS, PDF, xlsx, docx, HTML, zip |
| `tests/test_extract_article.py` | source choice, per-file statuses, the extraction record, the CLI |
| `tests/test_extract_corpus.py` | the real files that taught the extractor its rules — skipped without `corpus/` |
| `tests/test_manual_fetch_units.py` | the comparison rules: publisher filename conventions, archives, article versions |
| `tests/test_manual_fetch_live.py` | fetches those same DOIs for real and compares — off unless asked for twice |

They lean on failure cases rather than happy paths, because every bug found so far
was a *plausible-looking success*: a paywall page served as `application/pdf`,
`hasSuppl=Y` with nothing retrieved, a `max_files` cap applied silently, 26 copies
of one article page saved as supplements, a workbook with three sheets read as
having none. Where a test pins a rule that a live batch disproved, its docstring
names the DOI — so a failure explains itself instead of sending you back to the
publisher.

The browser tier is the reason for `tests/fakes.py`. It is the largest and most
fragile module here, and it previously had no offline coverage at all, so every
regression in it surfaced only after a real run against a real publisher.

`tests/test_extract_corpus.py` is the one file that touches real bytes. `corpus/`
is gitignored, so it skips in a clean checkout and runs on a machine that has
fetched the papers; the synthetic equivalents of every shape it checks are pinned
in `tests/test_extract_units.py`.

### Checking the fetcher against papers fetched by hand

`corpus/` tests what the extractor does with files that were already fetched. It
cannot tell you whether the *fetch* was right — whether a paper with twelve
supplements came back with twelve. That needs ground truth: a human opening the
publisher's page and saving everything by hand.

    MANUSCRIPT_HARVEST_MANUAL_DIR=~/manual-fetch-papers \
    MANUSCRIPT_HARVEST_MANUAL_NETWORK=1 \
    python -m pytest tests/test_manual_fetch_live.py -v

`manual_fetch/manual_fetch.yaml` is checked in; the bytes it describes are not, for the same reason
`corpus/` is ignored. Point `MANUSCRIPT_HARVEST_MANUAL_DIR` at wherever they live —
there is no need to copy them into the repo. To add papers, drop a folder per DOI
and regenerate:

    python -m manuscript_harvest.fetch.manual_fetch bootstrap 10.1126/science.adt8307=Science

The spec it writes is a draft for a human to confirm, not an answer. Two things it
gets right that are worth knowing about, because both were found on the first three
papers:

- **The article PDF is compared on page count and identity, never on bytes.**
  Publishers stamp per-download watermarks and embed timestamps, so two correct
  fetches of one paper differ. And only the *published* rendition is held to a page
  count: Cell Genomics ships an extended version as `mmc12.pdf` running 59 pages
  against a 37-page typeset article, and both are the same paper.
- **Supplements are compared as a set of content hashes, with archives normalised
  both ways.** Filenames never line up — browsers rename downloads and
  `store.supplement_filename` prefixes retrieval order. Science ships 28 tables as
  one zip, so a tier that unpacks it and a human who saved it whole have to count as
  the same thing.

The check that justifies the exercise is `supplementary_status`. No synthetic
fixture can catch a *silent* false negative — a paper that really has supplements,
that fetch comes away from with none, reported as `none_listed` rather than
`expected_but_missing`. Only ground truth knows the difference.

This cannot run in CI: no network, no browser, no proxy credentials. So it is a
diagnostic, and what it finds belongs in `tests/fakes.py` afterwards, where it will
run on every commit.

## Known limitations

- Text-based PDFs only. Scanned/image PDFs need an OCR pre-step (not included);
  the fetcher flags them as `scanned_pdf_suspected` and the extractor as
  `no_text_scanned_pdf`, rather than letting them look like a paper the model
  simply found nothing in.
- **Table structure is not recovered from PDFs.** A supplementary PDF that is
  really a table yields its cell text as paragraphs — searchable, but not a card.
  JATS XML and spreadsheets are where the 1,188 table cards come from. 10 of the
  72 supplementary PDFs here are image-only scans on top of that.
- **Figure images are not read at all.** 323 supplementary files in this corpus are
  `.jpg`/`.gif`/`.tif` figure panels, recorded as `image_no_text`. Reading them
  needs a vision pass, which is deliberately not part of this stage.
- `.doc`, `.rtf`, `.pptx` and non-zip archives (`.tar.gz`) have no parser: 4 files
  here, each reported as `unsupported_format`. Legacy `.xls` works only if the
  optional `xlrd` is installed (1 file).
- An unrecognised heading leaves `section` as `null` rather than guessing. "Single-cell
  profiling of pancreatic islets" is as likely to be a Methods subsection as a
  Results one, and a wrong section is worse than none — it makes a filter silently
  drop the text it was looking for.
- The PMC OA tarball route (`oa.fcgi` → `oa_package/*.tar.gz`) is advertised by
  NCBI but currently 404s over both HTTPS and FTP; that FTP tree now lists only
  `deprecated/`. It is off by default (`fetch.try_oa_package`) and the tier runs
  for its OA-subset signal. The unpack path is kept and tested in case it returns.
- NCBI's FAIR-SMART supplementary-materials API
  (`bionlp/RESTful/supplmat.cgi`) is documented but returns an error for every
  input, including NCBI's own example IDs, so it is not used.
- The browser tier's PMC path needs a *warm* profile or `--headed`. Once a headed
  session has cleared NCBI's bot check, later headless runs reuse those cookies
  and succeed; on a cold profile, headless gets a reCAPTCHA and the run says so
  rather than reporting an empty supplement list.
- ScienceDirect's article page yielded no supplement links in the rendered DOM.
  For the paper tested that is correct — its supplementary files exist only as PMC
  deposits, not on ScienceDirect — so the Elsevier supplement selector has never
  been confirmed against an article that really has them there.
- Supplementary files larger than `fetch.max_file_mb` are recorded but not
  fetched (one Science supplement is a 487.8 MB gzip). Independently of the cap,
  the browser transport cannot return anything near ~512 MB, because Playwright's
  Node driver marshals bodies as strings. Raising the cap will not get you past
  that; fetch such files by hand.
- `hasSuppl` is trusted for indexed journal articles but **not** for preprints,
  which are always checked at source. Europe PMC reports `hasSuppl: N` for
  10.1101/2025.07.21.666016, which has `media-1.pdf` and `media-2.zip` (72 MB
  together) -- trusting the flag silently dropped both.
- A versioned DOI falls back to its unversioned form when the versioned one is not
  indexed (eLife reviewed preprints). Only 1-2 trailing digits count, so article
  numbers like `10.1016/j.cell.2021.01.053` are never truncated.
- **ScienceDirect blocks programmatic PDF retrieval.** The article page exposes no
  PDF link at all (no `citation_pdf_url`, no href), so the URL is constructed from
  the PII -- but `/pdfft` answers 403 even unproxied with a real browser
  User-Agent, and a page-navigation retry is refused too. Affects
  `10.1016/j.stem.2023.12.013` and `10.1016/j.cell.2019.08.008`. Cell Press papers
  that are in the PMC OA subset are unaffected; these two are not.
- **A few PMC supplement sets stay refused.** For `10.1084/jem.20232192` the four
  supplementary tables are listed and `hasSuppl: Y`, but every route returns the
  proof-of-work page or 403. Reported as `expected_but_missing`, which is accurate.
  Clearing NCBI cookies to force a fresh challenge was tried and rejected: it fixed
  nothing and destroyed the warm state that lets a headless browser through at all
  (a paper that fetched 4/4 supplements regressed to a reCAPTCHA).
- Publisher supplement URL construction is only implemented for Springer/Nature
  (`static-content.springer.com`). Other publishers fall back to the browser tier.
- Section detection is heuristic: a canonical heading alone on its line, or one
  glued to the front of a paragraph. It found Methods in 47 of 53 PDFs here and
  is exact for JATS, which declares its sections. Where it misses, `section` is
  `null` and the block is still extracted.

## License

MIT — see [LICENSE](LICENSE).

## Not in this repository

Deliberately: no model client, no prompts, no schemas, no scoring. This repo's job
ends at `blocks.jsonl`. Consuming those blocks — retrieval, an LLM skill, a gold
standard to measure against — belongs downstream, and forcing a choice of model
here would make the harvesting code harder to reuse than it needs to be.
