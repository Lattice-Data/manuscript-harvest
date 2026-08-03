# Cleanup progress

A running tally for the `cleanup` branch, so an interrupted session can be resumed
without re-deriving what was already done. **One commit per item**, and this file is
updated in the same commit — so `git log --oneline main..HEAD` and the boxes below
always agree.

Delete this file in the last commit of the cleanup.

Baseline that must hold at every commit:

    python -m pytest tests -q                          # 805 passed, 10 skipped
    ruff check --select F manuscript_harvest tests     # clean

The full audited plan this comes from is not checked in; it lives in the session
scratchpad. Each item below carries enough detail to be executed without it.

## Scope of this pass

Group (A) safe deletions, group (D) requirements fixes, and two items lifted out of
group (B) because they are the same class of defect as (D): the pymupdf floor (B11)
and the coverage gate (B10). Groups (C), (E), (F), (G) and the rest of (B) are
audited but untouched — see the bottom of this file.

## (A) Safe deletions — 20 of 20 done (COMPLETE)

- [x] A1 `extract/review.py:422-423` — duplicate `partially_reviewed` branch; the next
      two lines return the identical tuple for every input that reaches it.
- [x] A2 `extract/__init__.py:33-34` — two re-export lines nothing imports; their
      `# noqa: F401` suppresses a true positive.
- [x] A3 `extract/jats.py:78-79` — `_normalize_ws`, zero references repo-wide.
- [x] A4 `extract/review.py:318-319` — `Overrides.__bool__`; every consumer is an
      identity test, no `if overrides` anywhere.
- [x] A5 `extract/section_audit.py:63` — unreferenced `_TOO_SHORT`; keep its comment by
      moving it above the `too_short` counter (~line 123).
- [x] A6 `extract/tables.py:308-309, 312-313` — both `detect_header` early guards; the
      sole caller `build_card` already returns on the identical conditions.
- [x] A7 `fetch/store.py:299` — unreachable trailing `return f"{value:.1f}TB"`; the
      loop's `or unit == "TB"` guarantees the fifth iteration returns.
- [x] A8 `fetch/http.py:158-159` — unreachable trailing `raise`; also add a
      `max_retries < 0` guard in `Http.__init__` so the function cannot fall off the end.
- [x] A9 `fetch/sources/proxy_browser.py:1292` — `disposition = ""`; both arms rebind
      before the first read.
- [x] A10 `fetch/manual_fetch.py:359, 388` — `**extra` on `build_article`; today a typo
      lands verbatim in the reviewed spec instead of raising.
- [x] A11 `fetch/identifiers.py:113, 211` — write-only `epmc_id`, collected and never
      reaching a manifest. **Resolved the other way than the plan's default: persisted
      it in `to_dict` rather than deleting it.** `epmc_source` was already recorded and
      is half a composite key; for preprints the `PPR` number is the only handle Europe
      PMC answers to. This is the one item in group (A) that adds a line instead of
      removing one, and the one to revert first if a manifest-shape change is unwanted.
- [x] A12 `fetch/validate.py:175` — never-passed `min_bytes` param; replace with a
      `_MIN_DOWNLOAD_BYTES` constant beside its siblings.
- [x] A13 `fetch/fetcher.py:217, 227` — never-passed `tiers` param; the config dict is
      the single channel.
- [x] A14 `extract/blocks.py:124, 148` — never-passed `offset` param on `number_blocks`.
      Keep the docstring verbatim (it records the id-collision measurement).
- [x] A15 `extract/pdf.py:200` — never-passed `origin` param; inline `origin="pdf"` at
      the four construction sites.
- [x] A16 `extract/sections.py:322-326` — never-passed first positional on
      `SectionTracker.__init__`. Keep the class docstring (three measured DOIs).
- [x] A17 `fetch/adapters/publishers.py:83, 190` — unreachable long host fragments;
      `Adapter.matches` is a substring test. Trim to the fragment that decides.
- [x] A18 `fetch/sources/proxy_browser.py:879-880` — unreachable `not_elsevier` guard;
      the only call site is already inside an Elsevier-only `looks_blocked` branch.
      Do NOT also delete line 789 (see B7).
- [x] A19 `pytest.ini:17` — dead `is not supported and will be removed` filter;
      `spreadsheet.py` silences openpyxl at the call site so it never reaches pytest.
- [x] A20 `extract/sections.py:266-273` + `tests/test_extract_units.py:282` — unused
      public alias `MAX_BOUNDED_SECTION_CHARS` and its tautological assertion. Do NOT
      rewrite the docstring at 271-272 as if it were false.

## (D) Requirements and packaging — 8 of 8 done (COMPLETE)

- [x] D1 `requirements.txt:3`, `pyproject.toml:25` — the declared `pyyaml` floor could
      not be installed on py3.12 or py3.13. **Raised to `>=6.0.2`, not the `>=6.0.1` the
      plan proposed:** measured wheel availability is cp312 from 6.0.1 and cp313 from
      6.0.2, so 6.0.1 fails the same way on the 3.13 leg. Note the severity is "the
      floor is not honourable", not "CI is broken" — pip resolves to 6.0.3 either way.
- [x] D2 `.github/workflows/tests.yml` — CI never runs `pip install .`, so the
      requirements/pyproject agreement and both console scripts are unguarded.
- [x] D3 `pyproject.toml:2, 11, 17` — `license = { text = "MIT" }` is a deprecated TOML
      table that already warns on build. Use the SPDX string, drop the license
      classifier, require `setuptools>=77`.
- [x] D4 `config.yaml` — two live browser keys documented nowhere:
      `fetch.max_challenge_failures` (default 3) and
      `fetch.browser.challenge_wait_seconds` (default 8).
- [x] D5 `requirements.txt:2, 4` — annotate the two floors that are NOT load-bearing
      (`requests>=2.31` passes at 2.25.1, `openpyxl>=3.1` at 3.0.10). No version change.
- [x] D6 — coverage mode was nowhere declared. Declared `branch = false` in pyproject
      (statement mode, which is what the gate and the badge already mean; total
      unchanged at 93% with a local corpus). **The plan overstated this one:** the
      `DataError` needs `--cov-append`, which neither CI nor the README passes — a plain
      run erases the data file first, so it was never reproducible as described.
- [x] D7 `README.md:64` — `pip install xlrd` should be `pip install 'xlrd>=2.0'` to match
      the extra it claims to equal.
- [x] D8 `requirements.txt:6-13` — the corpus counts are stated as fact; they are
      measurements over the 63-paper development corpus and the `.xls` file they cite is
      not in the shipped corpus.

## (B) — 6 of 11 done

- [x] B11 `requirements.txt:1`, `pyproject.toml:22` — pymupdf floor raised `>=1.24` to
      `>=1.28`, the version the checked-in section-score baseline was measured on, plus a
      note beside `EXPECTED_SCORES` recording the sweep. 1.24.0 also had no cp313 wheel,
      so the old floor was unsatisfiable on one CI leg. **Nuance the plan missed:** older
      releases mostly shrink the *alignable sample* (98->70, 125->79 paragraphs) rather
      than label worse (88.8%->82.9%, 92.0%->91.1%), so this gate can fail on a PyMuPDF
      change without the labeller regressing. Recorded in the note.
- [x] B1 `extract/review.py:348` — `Overrides.section_for` had no caller, so a curator's
      `section_span` answer was discarded. **Wired up** (the alternative was deleting the
      question). Applied in `extractor._apply_reviewed_section`, not in `pdf.py`: the answer
      is article-level, keyed on the main text's path. Three rules, each pinned by a test
      and each verified by mutation: only `section is None` blocks are filled; `section_for`
      is called once (it counts into `applied()`); every filled block carries
      `section_source: "review"`, a new optional `Block` field. Verified on
      10.1126_science.aat5031: `overrides_applied` 13 -> 14, 52 blocks labelled `results`,
      35 parser labels untouched, `blocks.jsonl` still byte-identical across re-extraction,
      and no other article in the corpus affected.
- [x] B2 `extract/cli.py:379-383` — the `--apply` headline counted overrides over the
      whole stored file while the breakdown counted the incoming batch. Added
      `Overrides.applied_kinds()`, counted where each answer is consumed, surfaced as
      `review.overrides_applied_kinds`; the breakdown now sums to the headline and the
      batch size is reported separately. Verified on the 14-answer article.
- [x] B3 `fetch/sources/proxy_browser.py` — the two download paths had drifted: only
      `_download_one` told the user a ~512 MB file must be fetched by hand. Shared
      `_refuse_oversize` and `_transport_failure` (they record; each caller shapes its own
      3- or 4-tuple return), which fixes the drift as a side effect. Three tests added,
      including the two page-route cases that had no coverage; the drift was
      re-introduced to confirm the new test catches it.
- [x] B4 `fetch/http.py:61`, `fetch/fetcher.py:202` — `Http(max_bytes=)` was a cap
      production could not set. **Wired, not deleted** (it is the only ceiling on a
      plain-HTTP body): `build_http` now reads `fetch.max_response_mb`, documented
      commented-out in config.yaml. Unset stays unbounded, matching prior behaviour.
- [x] B10 `.github/workflows/tests.yml:44-55` — `--cov-fail-under` raised 70 to 90, and
      the comment claiming the floor was "set just under the current number" replaced with
      the measured pair: **91.8% without a corpus (what CI reports and the badge shows)
      and 92.9% with one.** Both verified against the 90 floor. At 70 a regression had to
      delete a fifth of the exercised code before the gate fired.

## Remaining — audited, not started

- **(B) the other 8 items needing care.** Includes the real defects:
  a security claim asserted where it is not enforced (`europepmc.py:245`); `Http(max_bytes=)` is a
  cap production cannot set — wire it, do not delete; two `proxy_browser` download paths
  drifted apart; review-queue order docs contradict code.
- **(C) 8 merges** (highest value: the table-header override translation exists three
  times and only the `spreadsheet.py` copy is tested) **and 8 explicit leave-alones**.
- **(E) 20 stale comments/docstrings**, including `extract/cli.py:426` advertising
  `--role` as two values when three exist, and `pdf_loader` being a ghost name in three
  places.
- **(F) 7 README passages carrying knowledge found nowhere else** — must be preserved
  before the README shrinks.
- **(G) `EXTRACT_HARDENING_PLAN.md` deletion.** Safe: nothing references it and ~35.5 of
  its 36 items are implemented. One item to carry forward — 3.3 bullet 3, the
  side-by-side-table split on an interior all-blank column.
- **The README rewrite** (981 lines to ~330-380) and its ~18 factual corrections.
