# Cleanup progress

A running tally for the `cleanup` branch, so an interrupted session can be resumed
without re-deriving what was already done. **One commit per item**, and this file is
updated in the same commit — so `git log --oneline main..HEAD` and the boxes below
always agree.

Delete this file in the last commit of the cleanup.

Baseline that must hold at every commit:

    python -m pytest tests -q                          # 796 passed, 10 skipped
    ruff check --select F manuscript_harvest tests     # clean

The full audited plan this comes from is not checked in; it lives in the session
scratchpad. Each item below carries enough detail to be executed without it.

## Scope of this pass

Group (A) safe deletions and group (D) requirements fixes only. Groups (B), (C), (E),
(F) and (G) are audited but deliberately untouched — see "Not started" at the bottom.

## (A) Safe deletions — 6 of 20 done

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
- [ ] A7 `fetch/store.py:299` — unreachable trailing `return f"{value:.1f}TB"`; the
      loop's `or unit == "TB"` guarantees the fifth iteration returns.
- [ ] A8 `fetch/http.py:158-159` — unreachable trailing `raise`; also add a
      `max_retries < 0` guard in `Http.__init__` so the function cannot fall off the end.
- [ ] A9 `fetch/sources/proxy_browser.py:1292` — `disposition = ""`; both arms rebind
      before the first read.
- [ ] A10 `fetch/manual_fetch.py:359, 388` — `**extra` on `build_article`; today a typo
      lands verbatim in the reviewed spec instead of raising.
- [ ] A11 `fetch/identifiers.py:113, 211` — write-only `epmc_id`, collected and never
      reaching a manifest. Deleting it (alternative: add it to `to_dict`).
- [ ] A12 `fetch/validate.py:175` — never-passed `min_bytes` param; replace with a
      `_MIN_DOWNLOAD_BYTES` constant beside its siblings.
- [ ] A13 `fetch/fetcher.py:217, 227` — never-passed `tiers` param; the config dict is
      the single channel.
- [ ] A14 `extract/blocks.py:124, 148` — never-passed `offset` param on `number_blocks`.
      Keep the docstring verbatim (it records the id-collision measurement).
- [ ] A15 `extract/pdf.py:200` — never-passed `origin` param; inline `origin="pdf"` at
      the four construction sites.
- [ ] A16 `extract/sections.py:322-326` — never-passed first positional on
      `SectionTracker.__init__`. Keep the class docstring (three measured DOIs).
- [ ] A17 `fetch/adapters/publishers.py:83, 190` — unreachable long host fragments;
      `Adapter.matches` is a substring test. Trim to the fragment that decides.
- [ ] A18 `fetch/sources/proxy_browser.py:879-880` — unreachable `not_elsevier` guard;
      the only call site is already inside an Elsevier-only `looks_blocked` branch.
      Do NOT also delete line 789 (see B7).
- [ ] A19 `pytest.ini:17` — dead `is not supported and will be removed` filter;
      `spreadsheet.py` silences openpyxl at the call site so it never reaches pytest.
- [ ] A20 `extract/sections.py:266-273` + `tests/test_extract_units.py:282` — unused
      public alias `MAX_BOUNDED_SECTION_CHARS` and its tautological assertion. Do NOT
      rewrite the docstring at 271-272 as if it were false.

## (D) Requirements and packaging — 0 of 8 done

- [ ] D1 `requirements.txt:3`, `pyproject.toml:25` — **`pyyaml>=6.0` is not installable
      on py3.12 or py3.13**, both CI matrix legs. Raise to `>=6.0.1`.
- [ ] D2 `.github/workflows/tests.yml` — CI never runs `pip install .`, so the
      requirements/pyproject agreement and both console scripts are unguarded.
- [ ] D3 `pyproject.toml:2, 11, 17` — `license = { text = "MIT" }` is a deprecated TOML
      table that already warns on build. Use the SPDX string, drop the license
      classifier, require `setuptools>=77`.
- [ ] D4 `config.yaml` — two live browser keys documented nowhere:
      `fetch.max_challenge_failures` (default 3) and
      `fetch.browser.challenge_wait_seconds` (default 8).
- [ ] D5 `requirements.txt:2, 4` — annotate the two floors that are NOT load-bearing
      (`requests>=2.31` passes at 2.25.1, `openpyxl>=3.1` at 3.0.10). No version change.
- [ ] D6 — no coverage config anywhere, so a stale branch-mode `.coverage` makes the
      documented local command die after the tests pass. Declare branch mode.
- [ ] D7 `README.md:64` — `pip install xlrd` should be `pip install 'xlrd>=2.0'` to match
      the extra it claims to equal.
- [ ] D8 `requirements.txt:6-13` — the corpus counts are stated as fact; they are
      measurements over the 63-paper development corpus and the `.xls` file they cite is
      not in the shipped corpus.

## Not started — audited, deliberately out of this pass

- **(B) 11 items needing care.** Includes the real defects: `Overrides.section_for` is
  dead so a curator's `section_span` answer is discarded (`review.py:351`); a security
  claim asserted where it is not enforced (`europepmc.py:245`); `Http(max_bytes=)` is a
  cap production cannot set — wire it, do not delete; two `proxy_browser` download paths
  drifted apart; review-queue order docs contradict code. Also **B11: the `pymupdf>=1.24`
  floor is four minor releases below what the recorded section-score baseline needs, and
  `pymupdf==1.24.0` has no py3.13 wheel** — same class of defect as D1, worth doing next.
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
