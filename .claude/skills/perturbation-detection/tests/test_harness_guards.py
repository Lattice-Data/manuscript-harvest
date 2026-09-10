"""Guards for five harness defects, plus the negative control for the 0-flag result.

The strongest claim this skill makes is that over 392 papers **2,471 of 2,471
evidence quotes verified, with 0 unverifiable, 0 misattributed, 0 perturbations
dropped and 0 EV/CC flags raised.** A result that clean has two readings — the
model is honest, or the checker cannot fail — and until now nothing separated
them at corpus scale. `test_the_verifier_can_actually_fail` is that separation:
it corrupts a quote in real records from a real run and asserts the flags fire.
It skips when no run directory is present, the same way
`tests/test_extract_corpus.py` skips for want of a local corpus.

The rest are regressions for defects found reviewing this layer against a
taggable release. Each one was reproduced before it was fixed.

Run: python -m pytest tests/test_harness_guards.py -q
"""
from __future__ import annotations

import copy
import json
import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pe.paper_text import build_sources, split_assembled  # noqa: E402
from pe.runroot import work_default  # noqa: E402
from pe.validate import model_of, validate_result  # noqa: E402
from task.rules import TEXT_COMPLETENESS, stage_b  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------
# Stage B must fail CLOSED
# --------------------------------------------------------------------------

@pytest.mark.parametrize("completeness", [None, "", "Full", "truncated ", "partial",
                                          "unknown_value", 0])
def test_stage_b_caps_a_no_on_any_value_that_is_not_full(completeness):
    """prompt.md: cap when `text_completeness` is "anything other than 'full'".

    The guard here used to be `text_completeness in TEXT_COMPLETENESS and
    != "full"`, so every value above skipped the cap and kept the "no" — while
    an honest "unknown" was capped. The safety mechanism failed OPEN on exactly
    the input it should distrust most, and a typo switched it off.
    """
    assert stage_b("no", "ok", completeness) == ("unclear", True)


def test_stage_b_still_does_not_cap_a_full_text_negative():
    assert stage_b("no", "ok", "full") == ("no", False)


@pytest.mark.parametrize("verdict", ["yes", "unclear"])
def test_stage_b_never_caps_a_positive(verdict):
    """The asymmetry is the point: missing text can hide the sentence that would
    have paired a perturbation, but it cannot invent one."""
    assert stage_b(verdict, "partial", "truncated") == (verdict, False)


def test_every_legal_completeness_value_is_still_covered():
    """A tightened guard must not have loosened the enum it replaced."""
    for value in TEXT_COMPLETENESS:
        expected = ("no", False) if value == "full" else ("unclear", True)
        assert stage_b("no", "ok", value) == expected


# --------------------------------------------------------------------------
# --no-supplementary: a documented toggle that had never worked
# --------------------------------------------------------------------------

_BLOCKS = [
    {"kind": "metadata", "section": None, "source_file": "fulltext.pdf",
     "text": "Title: A paper\nDOI: 10.1/x"},
    {"kind": "paragraph", "section": "methods", "source_file": "fulltext.pdf",
     "text": "Cells were treated with LPS and profiled by scRNA-seq. " * 20},
    {"kind": "paragraph", "section": "methods",
     "source_file": "supplementary/mmc1.pdf",
     "text": "Supplementary methods: LPS at 100 ng/mL for 4 h. " * 20},
]


def test_main_text_only_assembly_reports_its_char_count():
    """`pe.prepare --no-supplementary` died on `KeyError: 'chars'`.

    `build_sources` returned early on this path, before the block that set
    `chars` and `supp_chars`, so the toggle prompt.md documents ("Supplementary
    sources -> Main text only") crashed every time it was used.
    """
    sources, stats = build_sources(_BLOCKS, include_supplementary=False)
    assert [s["source_id"] for s in sources] == ["main"]
    assert stats["chars"] == stats["main_chars"] == sources[0]["char_count"]
    assert stats["supp_chars"] == 0


def test_both_assembly_paths_agree_about_the_main_text():
    with_supp, s_with = build_sources(_BLOCKS, include_supplementary=True)
    _, s_without = build_sources(_BLOCKS, include_supplementary=False)
    assert s_with["main_chars"] == s_without["main_chars"]
    assert s_with["chars"] > s_without["chars"]
    assert [s["source_id"] for s in with_supp] == ["main", "supp1"]


def test_stats_keys_do_not_depend_on_the_path_taken():
    """The defect was a key present on one path and absent on the other, which
    no caller can defend against. Asserted as a property, not per key."""
    _, a = build_sources(_BLOCKS, include_supplementary=True)
    _, b = build_sources(_BLOCKS, include_supplementary=False)
    assert set(a) == set(b)


# --------------------------------------------------------------------------
# config.yaml is read, not documentation. Every key must have a reader.
# --------------------------------------------------------------------------

def test_no_config_key_is_read_by_nothing():
    """Five keys were dead: `fuzzy_match.enabled`, `.normalize_unicode`,
    `.normalize_punctuation`, `confidence_thresholds` and `flag_all_for_review`.

    config.yaml's own header says it "is READ by pe.prepare and pe.validate — it
    is not documentation", and it already carried a comment explaining that the
    removed `output_dir:` key "was read by nothing, so setting it looked like it
    worked and did not". The same failure, five more times. This is the guard
    that comment was asking for.
    """
    yaml = pytest.importorskip("yaml")
    config = yaml.safe_load((ROOT / "config.yaml").read_text()) or {}
    source = "\n".join(p.read_text() for p in sorted((ROOT / "pe").glob("*.py")))

    def keys(node, prefix=""):
        for key, value in node.items():
            yield key
            if isinstance(value, dict):
                yield from keys(value, f"{prefix}{key}.")

    unread = [k for k in keys(config)
              if f'"{k}"' not in source and f"'{k}'" not in source]
    assert not unread, (
        f"config.yaml declares {unread} but no module in pe/ reads them. A key "
        f"nobody reads looks like it works and does not — either wire it up or "
        f"delete it.")


# --------------------------------------------------------------------------
# needs_section_pass routes to the right queue
# --------------------------------------------------------------------------

def _minimal(**over):
    record = {
        "task_version": "0.0.13", "sources_seen": ["main"],
        "processing_status": "ok", "text_completeness": "full",
        "has_single_cell_assay": "yes", "perturbation_present": "no",
        "perturbation_present_any_assay": "no", "unresolved_reason": "none",
        "consistency_flags": [], "perturbations": [], "suppressed_candidates": [],
        "samples": [], "paper_confidence": 0.9,
    }
    record.update(over)
    return record


def test_over_budget_paper_says_re_fetching_will_not_help():
    """Both papers that hit `needs_section_pass` on the 392-paper run were capped
    at "unclear"/degraded_text like any truncated paper and sorted to triage P4 —
    "route to re-fetch, not to reading". That is the wrong queue: the text arrived
    complete and simply does not fit the budget with Methods preserved. pe.prepare
    wrote the flag into the manifest and nothing read it.
    """
    out = validate_result(_minimal(), {"main": "x"}, 0.85,
                          needs_section_pass=True, truncated_by_harness=True)
    assert out["needs_section_pass"] is True
    issue = next(i for i in out["validation"]["issues"] if "needs_section_pass" in i)
    assert "re-fetching will not change it" in issue
    assert "section-level second pass" in issue


def test_an_ordinary_truncation_does_not_claim_to_need_a_section_pass():
    out = validate_result(_minimal(), {"main": "x"}, 0.85,
                          truncated_by_harness=True)
    assert "needs_section_pass" not in out
    assert not [i for i in out["validation"]["issues"] if "needs_section_pass" in i]


# --------------------------------------------------------------------------
# model_id: the pin now buys attribution
# --------------------------------------------------------------------------

def test_model_id_is_recorded_when_the_runner_wrote_it(tmp_path):
    (tmp_path / "meta").mkdir()
    (tmp_path / "meta" / "10.1_x.model").write_text("claude-opus-5\n")
    assert model_of(tmp_path, "10.1_x") == "claude-opus-5"
    out = validate_result(_minimal(), {"main": "x"}, 0.85, model_id="claude-opus-5")
    assert out["validation"]["model_id"] == "claude-opus-5"


def test_an_unrecorded_model_is_none_not_a_guess(tmp_path):
    """Every run before the sidecar existed has no model recorded, and None is
    the honest answer. Defaulting to the current pin would backdate a claim."""
    assert model_of(tmp_path, "10.1_x") is None
    assert validate_result(_minimal(), {"main": "x"}, 0.85)["validation"]["model_id"] is None


# --------------------------------------------------------------------------
# The negative control for 2,471/2,471
# --------------------------------------------------------------------------

def _real_run() -> Path | None:
    """A run directory with validated records and the prompts they were scored on.

    Honours PERTURBATION_RUN_ROOT through `work_default()`, so this follows the
    same location every other module uses.
    """
    for candidate in (Path(os.environ["PE_TEST_WORK"]) if os.environ.get("PE_TEST_WORK")
                      else None, work_default()):
        if candidate and (candidate / "validated").is_dir() and (candidate / "prompts").is_dir():
            return candidate
    return None


def test_the_verifier_can_actually_fail():
    """0 failed quotes over 392 papers: honest model, or a checker that cannot fail?

    Takes real records that verified cleanly, corrupts the middle of each
    evidence quote against the real assembled text, and re-runs the real
    validator. Every one must now fail to verify, drop its perturbation, raise
    EV-UNVERIFIED and EV-PERT-DROPPED, and recompute the determination away from
    "yes". A unit test with synthetic text proves the mechanism exists; this
    proves it engages on the actual corpus, at the actual 0.85 threshold, against
    the actual multi-source assembly — which is where a too-permissive fuzzy
    match would hide.
    """
    work = _real_run()
    if work is None:
        pytest.skip(f"no run directory at {work_default()} (set PE_TEST_WORK)")

    from pe.validate import paper_text_from_prompt

    checked = 0
    for path in sorted((work / "validated").glob("*.json"))[:200]:
        record = json.loads(path.read_text())
        prompt_file = work / "prompts" / f"{path.stem}.txt"
        if not prompt_file.is_file():
            continue
        perts = record.get("perturbations") or []
        if record.get("perturbation_present") != "yes" or not perts:
            continue
        quotes = [q for p in perts for q in (p.get("evidence_quotes") or [])
                  if isinstance(q, dict) and len(str(q.get("quote") or "")) > 60]
        if not quotes:
            continue

        sources = split_assembled(paper_text_from_prompt(prompt_file))
        clean = validate_result(copy.deepcopy(record), sources, 0.85)
        if clean["validation"]["quotes_failed"]:
            continue                      # not a clean record; nothing to falsify

        # Corrupt every quote the record rests on, in the middle, where a
        # prefix/suffix match cannot rescue it.
        poisoned = copy.deepcopy(record)
        for pert in poisoned["perturbations"]:
            for quote in pert.get("evidence_quotes") or []:
                text = str(quote.get("quote") or "")
                if len(text) > 60:
                    half = len(text) // 2
                    quote["quote"] = (text[:half] + " ZZQX fabricated interpolation "
                                      "that appears in no source ZZQX " + text[half:])
            if isinstance(pert.get("assay_evidence"), dict):
                pert["assay_evidence"]["quote"] = "ZZQX wholly invented pairing claim ZZQX"

        out = validate_result(poisoned, sources, 0.85)
        validation = out["validation"]
        assert validation["quotes_failed"] > 0, f"{path.stem}: fabricated quote verified"
        assert "EV-UNVERIFIED" in validation["evidence_flags"], path.stem
        assert "EV-PERT-DROPPED" in validation["evidence_flags"], path.stem
        assert validation["perturbations_kept"] == 0, path.stem
        assert out["perturbation_present"] != "yes", (
            f"{path.stem}: determination survived the removal of all its evidence")
        assert validation["determination_changed_by_harness"] is True, path.stem
        checked += 1
        if checked == 5:
            break

    if not checked:
        pytest.skip("no clean 'yes' record with a long quote in this run")
    assert checked >= 1


def test_prepare_refuses_an_empty_paper_set(tmp_path):
    """Found by hitting it: `cat` of two paths that did not exist.

    `pe.prepare` printed "0/0 prepared" and exited 0 on an empty `--set`, so a
    mistyped or half-written set file read as a successful stage 1. Stage 2 then
    found nothing pending and also exited 0, and the whole pipeline reported
    success over zero papers -- the vacuous-pass shape that three of the seven
    release blockers were fixed for, surviving in the one module nobody had
    handed an empty file.
    """
    import subprocess
    empty = tmp_path / "none.txt"
    empty.write_text("\n  \n")          # blank lines only: parses to zero papers
    proc = subprocess.run(
        [sys.executable, "-m", "pe.prepare", "--set", str(empty),
         "--work", str(tmp_path / "work"), "--corpus", str(tmp_path / "corpus")],
        cwd=str(ROOT), capture_output=True, text=True)
    assert proc.returncode != 0, (
        f"prepare exited {proc.returncode} on an empty set; a zero exit here is "
        f"indistinguishable from a successful run.\nstdout: {proc.stdout[-400:]}")
    assert "names no papers" in proc.stderr or "names no papers" in proc.stdout, (
        f"prepare failed on an empty set but did not say why.\n{proc.stderr[-400:]}")


# --------------------------------------------------------------------------
# The abort sentinel covers BOTH ways a session goes unusable
# --------------------------------------------------------------------------

#: The real line, from work-corpus-v0021-r1's logs. 145 papers received it.
USAGE_LIMIT_LINE = ("You've hit your session limit · resets 11:50pm "
                    "(America/Los_Angeles)")
AUTH_FAILURE_LINE = "OAuth session expired. Please run /login."

RUNNER = ROOT / "pe" / "run_headless.sh"


#: The predicate name the runner uses, and the variable holding its vocabulary.
_PREDICATE_VARS = {"is_auth_failure": "AUTH_RE", "is_usage_limit": "LIMIT_RE"}


def _shell_predicate(name: str) -> str:
    """The regex one of the runner's predicates actually greps with.

    Reads it from the script rather than restating it, so this guard tests what
    runs; a copy here would pass while the script rotted. The pattern moved out
    of the function body and into a shared variable when the same vocabulary
    gained a third reader (`limit_message`), so this follows it there -- and
    asserts the predicate really does use the variable, which is the part that
    could silently drift back.
    """
    body = RUNNER.read_text()
    variable = _PREDICATE_VARS[name]
    used = re.search(rf"^{re.escape(name)}\(\) \{{\n\s*grep -qiE \"\${variable}\"",
                     body, re.MULTILINE)
    assert used, f"{name}() no longer greps with \"${variable}\" in {RUNNER.name}"
    match = re.search(rf"^{variable}='([^']+)'$", body, re.MULTILINE)
    assert match, f"{variable} is not a single-quoted assignment in {RUNNER.name}"
    return match.group(1).replace("\\\\", "\\")


def _matches(pattern: str, text: str) -> bool:
    import subprocess
    return subprocess.run(["grep", "-qiE", pattern], input=text, text=True).returncode == 0


def test_the_usage_limit_signature_trips_the_sentinel():
    """The defect: `.auth-failed` fired on auth errors only.

    The v0.0.21 corpus run hit a usage limit at 249 of 392 papers. The sentinel
    stayed clear, so the remaining 145 each paid a `claude -p` spawn to receive
    one identical line -- the exact waste the sentinel's own comment cites as its
    reason for existing ("44 copies of the same 73-byte auth error"). Right
    failure shape, one of two signatures.
    """
    assert _matches(_shell_predicate("is_usage_limit"), USAGE_LIMIT_LINE), (
        f"is_usage_limit does not match the real limit line:\n  {USAGE_LIMIT_LINE}\n"
        f"Every remaining paper in a limited run spawns to receive it.")


#: Strings the CLI binary actually carries, checked against `strings` on
#: version 2.1.251: it maps 401 -> authentication_error, 403 -> permission_error
#: (rendered "Authentication failed"), 429 -> rate_limit_error. The original
#: patterns matched six of these eleven not at all -- `rate_limit_error` among
#: them, because the pattern said "rate limit" with a space.
_REAL_AUTH_STRINGS = [
    "Authentication failed", "authentication_error", "permission_error",
    "OAuth token has expired", "Invalid bearer token", "OAuth session expired",
]
_REAL_LIMIT_STRINGS = [
    "rate_limit_error", "429 Too Many Requests", "Credit balance is too low",
    USAGE_LIMIT_LINE,
]
#: Must trip NEITHER. A transient upstream error is not a dead session, and
#: aborting a 392-paper queue on one overloaded response costs more than the
#: retry it replaces. The success strings guard the other direction: a false
#: positive on the digest line would abort every run.
_NOT_A_SESSION_DEATH = [
    "overloaded_error", "invalid_request_error", "DONE",
    "result=DONE turns=5 cost=$1.01 api=145686ms in=10 out=13100",
]


@pytest.mark.parametrize("text", _REAL_AUTH_STRINGS)
def test_every_auth_signature_the_cli_emits_is_matched(text):
    assert _matches(_shell_predicate("is_auth_failure"), text), (
        f"is_auth_failure misses {text!r}, so a dead session would spawn every "
        f"remaining paper to rediscover it.")


@pytest.mark.parametrize("text", _REAL_LIMIT_STRINGS)
def test_every_limit_signature_the_cli_emits_is_matched(text):
    assert _matches(_shell_predicate("is_usage_limit"), text), (
        f"is_usage_limit misses {text!r}. This is the 145-spawn defect: right "
        f"failure shape, wrong signature.")


@pytest.mark.parametrize("text", _NOT_A_SESSION_DEATH)
def test_a_transient_error_or_a_success_does_not_abort_the_queue(text):
    auth = _shell_predicate("is_auth_failure")
    limit = _shell_predicate("is_usage_limit")
    assert not _matches(auth, text) and not _matches(limit, text), (
        f"{text!r} trips a sentinel. Aborting a 392-paper run on a transient "
        f"error, or on a successful paper's own log line, costs more than it saves.")


def test_the_two_failure_kinds_stay_distinguishable():
    """Both abort the queue; the REMEDIES differ and so must the predicates.

    A usage limit told "re-authenticate" sends the caller to /login, which
    cannot move a limit. An auth failure told "wait for the reset" waits for a
    reset that will never come.
    """
    auth, limit = _shell_predicate("is_auth_failure"), _shell_predicate("is_usage_limit")
    assert _matches(auth, AUTH_FAILURE_LINE)
    assert not _matches(auth, USAGE_LIMIT_LINE), (
        "is_auth_failure matches the usage-limit line, so a limited run is told "
        "to re-authenticate.")
    assert not _matches(limit, AUTH_FAILURE_LINE), (
        "is_usage_limit matches the auth line, so a dead session is told to wait "
        "for a reset.")


def test_the_sentinel_is_written_read_and_cleared_under_one_name():
    """A rename that missed one site would leave the guard permanently armed or
    permanently clear. Three sites: the early abort, the two writers, the reset.
    """
    body = RUNNER.read_text()
    # Matched with its path prefix, which every FUNCTIONAL reference has. The
    # comment explaining the rename names the old file too, and a guard that
    # forbids its own rationale gets the rationale deleted instead.
    stale = re.findall(r"\$\{?[Ww][Oo][Rr][Kk]\}?/\.auth-failed", body)
    assert not stale, (
        f"{stale} -- a functional reference to the old sentinel survives. It is "
        f"`.session-dead` now because auth is only one of the two failures it "
        f"covers, and a half-renamed sentinel is never both written and read.")
    assert body.count(".session-dead") >= 4, (
        "expected the sentinel at the abort check, both writers and the "
        "per-run reset")
    assert 'rm -f "$WORK/.session-dead"' in body, (
        "a previous run's verdict must be cleared, or one limited run arms the "
        "sentinel forever and every later run aborts on paper 1.")


# --------------------------------------------------------------------------
# There is no implicit corpus default
# --------------------------------------------------------------------------

def test_no_module_falls_back_to_a_literal_corpus_path():
    """The defect this is the regression for.

    `pe.prepare` and `pe.validate` both fell back to `"./corpus"`, which
    resolves against the CWD -- and every documented invocation runs from the
    skill directory, where a stale 382-paper copy sits beside the 392-paper tree
    at the repo root. Forgetting `--corpus` therefore scored a different, smaller
    corpus: 10 papers of `papers-all.txt` absent, both trees gitignored, nothing
    to diff. The fallback could only ever fire by mistake.
    """
    import ast

    offenders = []
    for path in sorted((ROOT / "pe").glob("*.py")):
        tree = ast.parse(path.read_text())
        # Docstrings are Constants too, and the modules explain the removed
        # default in prose. Comments never reach the AST, so only docstrings
        # need excluding -- which is why this walks the tree instead of
        # grepping: a guard that fires on its own explanation gets deleted.
        docstrings = {
            node.body[0].value for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef))
            and node.body and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        }
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and node.value == "./corpus"
                    and node not in docstrings):
                offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, (
        f"{offenders} -- a corpus default that resolves against the CWD picks "
        f"the stale copy whenever the flag is forgotten. Use "
        f"pe.runstate.resolve_corpus, which refuses instead.")


def test_prepare_refuses_when_no_corpus_is_named(tmp_path):
    import subprocess
    papers = tmp_path / "one.txt"
    papers.write_text("10.1000_x\n")
    empty_config = tmp_path / "config.yaml"
    empty_config.write_text("include_supplementary: true\n")
    proc = subprocess.run(
        [sys.executable, "-m", "pe.prepare", "--set", str(papers),
         "--work", str(tmp_path / "work"), "--config", str(empty_config)],
        cwd=str(ROOT), capture_output=True, text=True)
    assert proc.returncode != 0, (
        f"prepare exited {proc.returncode} with no corpus named; it used to fall "
        f"back to ./corpus.\nstdout: {proc.stdout[-400:]}")
    assert "no corpus directory" in proc.stderr, (
        f"prepare refused but did not say why.\n{proc.stderr[-400:]}")


def test_prepare_refuses_a_corpus_path_that_does_not_exist(tmp_path):
    """Otherwise every paper SKIPs and the run reports "0/N prepared", exit 0 --
    the vacuous-pass shape the empty-set refusal above was added for.
    """
    import subprocess
    papers = tmp_path / "one.txt"
    papers.write_text("10.1000_x\n")
    proc = subprocess.run(
        [sys.executable, "-m", "pe.prepare", "--set", str(papers),
         "--work", str(tmp_path / "work"),
         "--corpus", str(tmp_path / "typo-not-a-corpus")],
        cwd=str(ROOT), capture_output=True, text=True)
    assert proc.returncode != 0, (
        f"prepare exited {proc.returncode} on a nonexistent corpus path.\n"
        f"stdout: {proc.stdout[-400:]}")
    assert "does not exist" in proc.stderr, proc.stderr[-400:]


# --------------------------------------------------------------------------
# Shell quoting, which `bash -n` on this machine cannot check for us.
# --------------------------------------------------------------------------

def test_no_shell_escaping_artifact_leaked_into_the_runner():
    """The `\'"\'"\'` idiom is for embedding a quote in a single-quoted string
    typed at a shell. Written into a FILE by a generator it is just five stray
    characters, and it shipped twice: once in a grep pattern, which silently
    returned nothing, and once as a heredoc delimiter, which made the whole
    script unparseable.

    It got through because `bash -n` cannot see either. Both sites live inside
    `$( )`, and bash 3.2 -- what macOS ships and what this repo is developed on
    -- parses command substitutions lazily. CI runs bash 5, which parses them
    eagerly and failed with "unexpected EOF". A grep is version-independent and
    costs nothing.
    """
    offenders = [
        (n, line.strip())
        for n, line in enumerate(RUNNER.read_text().splitlines(), 1)
        if "\'\"\'\"\'" in line
    ]
    assert not offenders, (
        "shell-escaping artifact in " + RUNNER.name + ":\n"
        + "\n".join(f"  line {n}: {text}" for n, text in offenders)
        + "\nA generator wrote the quote-escaping idiom into the file verbatim.")


def test_every_heredoc_delimiter_is_a_bare_word():
    """The failure above, stated as the property rather than the symptom.

    A delimiter carrying quote characters does not match its own closing line,
    so the heredoc swallows the rest of the file.
    """
    import re
    bad = [(n, line.strip())
           for n, line in enumerate(RUNNER.read_text().splitlines(), 1)
           # `(?<!<)` and `(?!<)` keep the prompt's own `<<<SOURCE>>>` markers
           # out of it: those are the paper-assembly delimiters, not heredocs.
           if (m := re.search(r"(?<!<)<<-?(?!<)\s*(\S+)", line))
           and not re.fullmatch(r"'?[A-Za-z_][A-Za-z0-9_]*'?", m.group(1))]
    assert not bad, (
        "heredoc delimiter is not a bare word (optionally single-quoted):\n"
        + "\n".join(f"  line {n}: {text}" for n, text in bad))
