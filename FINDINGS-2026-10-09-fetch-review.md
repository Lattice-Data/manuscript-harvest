# Fetch-stage review, 2026-10-09

Scope: everything under `manuscript_harvest/fetch/` (11,700 lines across 27 files) plus
`text_bearing.py`, read in full; the 392 manifests in `corpus/` aggregated; four live
probes (three anonymous S3 listings, one OpenAlex lookup); one leak reproduced locally;
one browser launch timed with a throwaway profile. No fetch was run against a
publisher and no Elsevier request was made.

One caveat on the corpus numbers: most manifests predate the `pmc_s3` and
`elsevier_tdm` tiers (tried on 22 and 9 articles respectively), so the per-tier
delivery counts understate what a fresh run would get from them.

## 0. The question to settle first: figures

The brief for this review says the goal is "the full text of the manuscript and all
figures and all supplementary material". The code does the opposite with figures, on
purpose:

- `fetch.text_bearing_only: true` (the default, in `config.yaml` and in
  `cli.DEFAULT_FETCH_CONFIG`) refuses every image before download.
- `drop-media` removed 2,428 stored image/audio/video entries from the corpus.
- `media/` exists in 0 of 392 article directories.
- `elsevier_tdm` filters attachments on `ref` starting `mmc`, which drops every
  figure rendition (`gr1`, `figs1`, `IMAGE-HIGH-RES`) by design.
- The adapters look for supplement anchors, never for figure images, and nothing
  resolves the `<graphic>` hrefs in a JATS file.

Figure *captions* do arrive, inside the JATS and the PDF text. The images do not. So
either the stated goal is "text, including captions" and the docs should say so, or
figures are wanted and the cheapest complete route is the S3 deposit (every PMC OA
article's figures are listed there with sizes) plus Elsevier's `IMAGE-HIGH-RES`
attachments, filed under a `figures/` role rather than `media/`. Flipping the config
key alone would not do it: Europe PMC's ZIP members all land in `supplementary/`
(no media split there) and the Elsevier `ref` filter would still drop them.

This decides the shape of several items below, so it is listed before them.

## 1. Findings, ranked by value

### F1. JATS is never asked for once a PDF has arrived (79 articles, 20% of PMC ones)

`europepmc.fetch` and `pmc_s3.fetch` both fetch the article XML only inside
`if need_pdf:`. On a fresh run Europe PMC tries first, so when its `fullTextXML`
answers 404 and its PDF route succeeds, `need_pdf` is False for every later tier and
no tier ever asks the bucket for `<prefix>.xml`.

| PMC articles in corpus | 330 |
|---|---|
| without any JATS on disk | 79 |
| of which Europe PMC said `not_available` / `http_error` | 73 / 6 |
| of which `pmc_s3` was ever tried | 8 |
| probed on the public bucket (anonymous listing) | 3, all 3 list `<prefix>.xml` |

The 79 split 27 Springer Nature, 23 Elsevier, 17 AAAS, 12 other. For all of them
extraction falls back to PDF text, which is where the glyph damage lives
(`FINDINGS-2026-09-18-pdf-glyph-decoding.md`).

Fix (medium, ~40 lines plus tests): make XML a third need. Add `need_xml` to
`Source.fetch` with a default of True so existing callers and tests keep working;
have `europepmc`, `pmc_s3` and `biorxiv` honour it; in the tier loop, when PDF and
supplements are both settled, *skip* tiers that cannot supply XML (a `provides_xml`
class attribute) rather than break, so the browser is never launched for an XML.
`pmc_s3` keeps its withhold rule for an incomplete listing.

### F2. `pmc_oa` delivers nothing

| articles that tried it | 121 |
|---|---|
| PDFs it produced | 0 (5 attempts, all `download_failed`) |
| `oa_lookup` answers | 54 `ok`, 54 `not_in_oa_subset`, 13 `http_error` |

Its one remaining output, the `not_in_oa_subset` routing word, is also produced by
`pmc_s3` (a complete listing with no keys). The tarball route it was built for is off
by default because the FTP tree is retired. Cost today: one `www.ncbi.nlm.nih.gov`
request plus a 3 s host wait per PMC article, 373 lines, and about 38 test references.

Recommendation: drop it from `OA_TIERS` and `config.yaml` now; move
`supplement_or_media` (which `pmc_s3` imports from it) into `pmc_s3.py` or a small
`deposit.py`; delete the module once nobody has reached for `--tiers pmc_oa`.

### F3. `pmc_supplements` spends 34 minutes per corpus pass on a route that has never worked

| `/bin/` requests that came back as the proof-of-work page | 680, across 96 articles |
|---|---|
| `/bin/` requests that returned a file | 0 |
| files it did deliver, all via Springer's static host | 843, across 77 articles |

Each `/bin/` request costs a 3 s wait on `pmc.ncbi.nlm.nih.gov`. The challenge is per
session, so after the first one the rest are foregone. The tier exists for two real
things: the Springer route, and the `javascript_challenge` diagnosis that sends the
article to the browser. One probe per article is enough for the diagnosis.

Fix (small, ~15 lines): after the first `javascript_challenge` from `pmc_bin`, record
the remaining files as challenged without requesting them. `suppl_status`,
`problems` and the browser advice come out identical.

### F4. The 3 s per-host interval misreads NCBI's policy

`config.yaml` says "The 3.0 s default is a courtesy NCBI's E-utilities documents and
asks for." NCBI's documented limit is 3 requests per *second* without a key and 10
with one ([NCBI E-utilities](https://www.ncbi.nlm.nih.gov/books/NBK25497/),
[NLM support](https://support.nlm.nih.gov/kbArticle/?pn=KA-05317)). Europe PMC
publishes no rate limit; its forum says enforcement is per IP
([Europe PMC developers](https://europepmc.org/developers)). The 429s this corpus
actually recorded came from bioRxiv (11 of 13), the one host with no override.

A fresh article costs roughly 4 to 5 sequential `ebi.ac.uk` requests, 2 to
`www.ncbi.nlm.nih.gov` and 1 or more to `pmc.ncbi.nlm.nih.gov`, so about 15 s of each
article's wall-clock is sleep. Over 392 articles that is on the order of 1.5 hours,
before the 34 minutes in F3.

Fix (config only): 0.4 s for the two NCBI hosts (0.15 s with `ncbi_api_key`), 1 s for
`www.ebi.ac.uk`, and *raise* `www.biorxiv.org` and `api.biorxiv.org` to 5 s. Correct
the comment.

### F5. A browser is launched per article, not per batch

`fetch_publication` calls `build_sources` for every DOI, so each paywalled article
gets a fresh `ProxyBrowserSource`, a fresh Playwright context, a cookie restore and a
`save_state`, and `_challenge_cleared` starts False again. Measured with a scratch
profile: 0.5 to 0.9 s to launch, 1.2 to 1.7 s launch plus page plus close; the real
profile with its 84 KB cookie snapshot will be slower. 153 articles reached the tier.
`cmd_batch` already shares one `Http` across the run for exactly this reason.

Fix (medium): let `fetch_publication` accept prebuilt sources; have the browser tier
open its context lazily and close it in a `close()` the batch calls at the end and
when the circuit breaker drops the tier. Recycle the context every N articles to
bound memory.

### F6. Streamed downloads leak, and two docstrings describe a stage that does not exist

`_download_streamed` stages each large body in `tempfile.mkdtemp()` with no `dir`, so
under the *system* temp dir; its docstring and `store.save_staged_file`'s both said the
stage is a sibling in the article directory, chosen so the move is atomic. Reproduced
locally: after `save_staged_file` moved the file, the `mh-stream-*` directory stayed
behind. Two further paths in `fetch_publication` dropped a staged file without
deleting it (the text-bearing filter and the duplicate check), and the duplicate
check hashed `item.content`, which is `b""` for every streamed file, so two large
supplements sharing a name had the same key.

Fixed on the branch (see section 3). Still open: whether to stage beside the article
so the move is always a rename. Trade-off: a crashed fetch would then leave a 400 MB
file inside the corpus, counted against the budget and invisible to `drop-orphans`,
which walks only `supplementary/` and `media/`.

### F7. Elsevier full text may be one API call away from Stanford's network

41 Elsevier PDFs came through the proxy browser (a route the code itself documents as
stubbed and retried at cell.com), and 23 Elsevier articles are among the 79 without
JATS. The `elsevier_tdm` module records `/content/article/...` as 403 "Requestor
configuration settings insufficient" and concludes full text needs a separate
entitlement. Elsevier's own documentation says the default article view returns full
text when the client IP maps to a subscribing institution and errors otherwise
([API error messages](https://dev.elsevier.com/tecdoc_article_access.html),
[authentication](https://dev.elsevier.com/tecdoc_api_authentication.html)); the 403
is what off-campus looks like. Nothing in the repo or the memory notes says where the
probe was run from.

Proposed probe (one request, from campus or VPN, not run here because the Elsevier
service agreement reading is still pending per the memory notes):

    GET https://api.elsevier.com/content/article/doi/10.1016/j.cell.2024.02.020?httpAccept=text/xml
    X-ELS-APIKey: <key>

If it returns the article, 41 browser fetches and 23 missing full texts become one
API call each. Caveat: Elsevier's XML is not JATS; `extract/jats.py` would not read it
without a small adapter, though the response embeds plain text.

### F8. No open-access locator beyond Europe PMC's `fullTextUrlList`

22 articles flagged open access by Europe PMC still needed the browser for their PDF:
11 Cell Press, 9 Nature, 2 others, all with PMCIDs (most predate `pmc_s3`, which
would likely serve them now). OpenAlex answers
`GET api.openalex.org/works/doi:<doi>` with `best_oa_location.pdf_url` and no key;
verified for 10.1038/s41586-020-2922-4, which returns the nature.com PDF URL.
Unpaywall's own page says an endpoint was retired on 2026-09-18 while OpenAlex's help
centre says the DOI lookup stays
([Unpaywall API](https://unpaywall.org/products/api),
[OpenAlex help](https://help.openalex.org/access/unpaywall/)); OpenAlex is the safer
dependency. Crossref's `link[]` field (TDM URLs) is in the response
`_query_crossref` already fetches and is not read.

Fix (medium, ~120 lines plus tests): an `openalex` tier between `biorxiv` and
`proxy_browser` that feeds candidate PDF URLs through the existing `_fetch_pdf_url`
validation and identity checks, plus reading Crossref `link[]` into the same
candidate list.

### F9. Smaller smells, not changed on the branch

- Three near-copies of `_refuse_oversize` (`pmc_s3`, `elsevier_tdm`, `proxy_browser`),
  each with a docstring saying the wording matches the others deliberately. A base
  method would make that true by construction.
- `pmc_s3._fetch_payload` and `elsevier_tdm._fetch_supplements` share about 60 lines
  of role split, cap arithmetic and the same five-way status ladder.
- `_download_one` and `_download_streamed` return 5-tuples, `_download_via_page` a
  3-tuple; 13 tests unpack them. A small `Downloaded` dataclass would end the arity
  bookkeeping that F6's stale docstring was about.
- `_authenticated_yet`'s `context is None` branch is unreachable from both production
  callers; a test pins it as the honest answer for a hypothetical caller. Your call.
- `Source.max_file_bytes` / `max_files` default to 200 MB / 50 while
  `DEFAULT_FETCH_CONFIG` says 500 / 300, and the three oversize messages print 200 as
  their default. `DEFAULT_FETCH_CONFIG.min_interval_overrides` lacks the
  `api.elsevier.com` entry `config.yaml` has, although its comment says kept in step.
- `Http.get` retries transient statuses; `Http.download_to` does not.
- Europe PMC's supplementary ZIP is held whole in memory and unpacked from a
  `BytesIO`; with `max_response_mb` unset nothing bounds it. Streaming it to a temp
  file and opening with `ZipFile(path)` would cap peak memory at one member.
- `orphans._ARCHIVE_SUFFIXES` and `manual_fetch._ARCHIVE_SUFFIXES` are the same list.
- Ruff beyond the `F` gate: one `B023` (lambda over a loop variable in
  `orphans._member_hashes`), two `PLW2901`, two `RET503`, twelve unused `doi`
  arguments on adapter methods that are part of the interface. Cosmetic.
- `fetch_publication` re-resolves identifiers and re-runs every tier for any article
  not `complete`, on every batch. By design, and moot now that all 392 are complete,
  but a `--skip-if-tried-within` option would matter for the next corpus.
- Checked and fine: `enforce_budget` walks the whole corpus after every fetch, but
  the walk is 0.28 s for 6,266 files.

### F10. Prose

The fetch package is 50% code, 26% docstring, 15% comment, 10% blank (10,960 lines).
The docstrings carry measurements, and measurements go stale: this review found the
tier list in `fetch/__init__.py` two tiers behind, the `store.py` layout missing
`media/`, a return arity, a "~512 MB" that the same file elsewhere corrects to 384 MB,
and the staging-location claim in F6, all fixed on the branch. The pattern suggests
keeping measurements in dated FINDINGS files like this one and docstrings to the
contract, so a number can age without making the code lie.

## 2. How I would shape the task, against what exists

What exists and should stay: the status taxonomy, the manifest with per-byte
provenance, the identity and not-a-research-article checks, the settled/unsettled
distinction that stops re-fetch loops. That is the strongest part of the stage.

What I would do differently:

1. **Resolve the whole batch first.** Europe PMC's search takes `OR`-joined DOI
   queries and NCBI's ID converter takes 200 IDs per request, so identifiers for 392
   articles are a dozen requests, not 1,200 with 3 s between them.
2. **Separate locating from downloading.** Today each tier both finds and fetches,
   which is why the cap, the oversize refusal, the text-bearing filter and the status
   ladder exist three times. A planner that emits per-article candidates (JATS, PDF,
   supplement list, each with source and declared size) and one downloader that
   applies policy once would collapse those copies.
3. **Treat JATS, PDF and supplements as three needs** (F1).
4. **One browser per batch** (F5), and politeness intervals from the published limits
   (F4).
5. **An OA locator** (F8) and the institutional API routes (F7, and Wiley's TDM token
   for its 4 browser-fetched PDFs) ahead of the browser.
6. **Decide figures** (section 0) and give them a role of their own.

## 3. Applied on branch `fetch-review-cleanups` (uncommitted)

Behaviour-preserving except for the two leak fixes. 40 edits across 14 files; the
package diff is +173/-185 lines, most of the insertions being the docstrings of the
three new shared helpers. `ruff --select F` is
clean; the fetch-related test files pass (781 tests, up from the 710 baseline because
`test_pipeline.py` is now included and two tests were added).

- Removed `text_bearing.text_can_be_extracted` (used only by two tests, now
  asserting `skip_reason` directly).
- `ProxyBrowserSource.applies` returned a condition that could never be false
  (`ids.doi` is never empty); it now returns True and says why.
- `proxy_browser._oversize_mb` reuses `_content_length` instead of repeating the HEAD
  request; its stale "~512 MB" sentence is gone.
- Seven copies of `try: page.close() except Exception: pass` became one
  `_close_quietly(page)`.
- `store.sha256_file` replaces three chunked-hash loops (`orphans.sha256_path`,
  `manual_fetch.sha256_file`, and the inline one in `save_staged_file`).
- `store.prune_empty_group_dirs` replaces the two identical `_prune_empty_dirs`
  (`drop_media`, `orphans`); neither command imports the other.
- `sources.base.as_int` replaces `pmc_s3._as_int` and `elsevier_tdm._as_int`.
- `manual_fetch.PDF_SUCCESS`, a copy of `store.PDF_USABLE` kept "so the harness reads
  on its own" in a module that imports `store` four other ways, now reads
  `store.PDF_USABLE`.
- `fetcher._PDF_SUCCESS` alias removed in favour of `store.PDF_USABLE`.
- `europepmc`: `not a == b` became `a != b`.
- F6 leak fixes: `save_staged_file` removes the emptied staging directory;
  `fetcher._discard_staged` deletes a streamed body the filter or the duplicate check
  declines; `fetcher._digest_of` hashes a staged file from disk. Two tests added in
  `tests/test_units.py`.
- Docstrings corrected: `fetch/__init__.py` tier list, `store.py` layout (`media/`),
  `_refuse_oversize` arity, the stage location in two places.

**Second pass, same day, after a go-ahead on F1 and F4:**

- XML is a third need. `Source.fetch` takes `need_xml` (default None, the old
  contract: XML wanted exactly when the PDF is); `europepmc`, `pmc_s3` and
  `biorxiv` honour it and declare `provides_xml = True`; the tier loop skips, rather
  than stops at, tiers that cannot supply XML once the PDF and supplements are
  settled, so the browser is never opened for one. Nine tests added across the
  tier and pipeline suites.
- `manuscript-fetch fill-xml`, the XML-only pass for the articles already on disk
  (`fetch/fill_xml.py`, thirteen tests): it asks only the XML-capable tiers, only
  for the XML, and writes only `fulltext.nxml` and its manifest entry. Run with
  `--apply` over the corpus on 2026-10-09:

  | articles checked | 392 |
  |---|---|
  | without a JATS before the run | 123: the 79 PMC articles above, 35 with no PMCID, 9 preprints |
  | filled | 80: 66 from `pmc_s3`, 8 from `europepmc` (its earlier `http_error`s answered this time), 6 from `biorxiv` |
  | flagged by the identity or notice check | 0 |
  | still without one | 43: 35 have no PMCID and are not preprints, so no tier can serve them; 5 PMC articles list no JATS at Europe PMC or in the bucket; 3 preprints have none at bioRxiv, one of them a Research Square posting |

  Every filled manifest gained a `fulltext_xml` entry and nothing else; the PDF
  tier and supplement status distributions are unchanged and all 392 still read
  `complete`. The next `manuscript-extract all` re-extracts those 80 from JATS.

  One side effect worth knowing. The supplement ledger (`supplements.py`) can now
  measure 79 more articles, and 531 of their 583 newly visible declarations come
  back `not_reconciled`: the JATS is PMC's author-manuscript deposit, whose hrefs
  carry NIHMS names, while the files on disk came from the publisher under its own
  names. Same supplements, two naming schemes, and the ledger refuses to pair them
  by guesswork. `evidence_lost` did not move, so nothing is newly missing; the
  corpus-pinned totals in `tests/test_supplements.py` were updated with that
  explanation. Pairing the two schemes (by file hash against the S3 listing, which
  names both) would be the next step if those declarations matter.
- Intervals: default 1.0 s; NCBI hosts 0.4 s; Europe PMC 1.0 s; S3 and Elsevier
  0.2 s; both bioRxiv hosts 5 s. `config.yaml` and `DEFAULT_FETCH_CONFIG` now
  agree, and a test pins them in step with the NCBI floor of a third of a second.

## 4. Proposed next steps, in order

| # | change | size | needs |
|---|---|---|---|
| 1 | decide figures (section 0) | decision | you |
| 2 | F1 `need_xml` and the `fill-xml` pass (done, run over the corpus) | done | re-extract the 80 filled articles with `manuscript-extract all` |
| 3 | F4 intervals and biorxiv override (done) | config | nothing |
| 4 | F3 fail-fast in `pmc_supplements` | small | go-ahead |
| 5 | F2 drop `pmc_oa` from the defaults | small | go-ahead |
| 6 | F7 one Elsevier probe from campus/VPN | one request | you, from the network |
| 7 | F8 `openalex` tier plus Crossref links | medium | go-ahead |
| 8 | F5 one browser per batch | medium | go-ahead |
| 9 | F9 shared oversize/cap/status helpers, `Downloaded` dataclass | medium | go-ahead |
