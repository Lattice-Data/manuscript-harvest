"""Guards for the five leaks a second task pack actually found.

Stage 4 of the plan was: write a second pack for a different question, run it on
ten papers, and see whether the harness needs editing. The answer was **yes, in
five places** -- and none of the existing tests caught any of them, including
`test_seam.py`, which was written for exactly this purpose. That is the useful
part: the seam guard checked for task VOCABULARY and the leaks were all
structural.

The second pack answered "which tissue did the sequenced material come from, and
does the paper state it explicitly?" over ten real papers. It shares nothing with
perturbation detection except the mechanism.

  LEAK 1  `pe/validate.py` read `secondary_arrays[0]["path"]` unconditionally, so
          a pack declaring no considered-and-rejected array died at IMPORT with
          `IndexError: list index out of range`. A secondary array is one task's
          answer to keeping its exclusions visible; indexing [0] made it a
          requirement of the shape.
  LEAK 2  `pe/run_headless.sh` computed its queue in a command substitution, so
          leak 1's traceback left `$DOIS` empty -- and an empty queue reads as
          "nothing pending". A broken pack printed "nothing to do ... every paper
          already has a result" and exited 0. The vacuous-pass shape again, in
          the one script `pe/runstate.py` does not cover.
  LEAK 3  `pe/compare.py` printed three lines of prose naming `SUPP-EVIDENCE`, a
          change class only the perturbation pack declares, so the second pack's
          report told its reader to look for a class absent from its own table.
  LEAK 4  `pe/compare.py` hardcoded the class name `"WITHIN-NOISE"`. The harness
          assigns that class itself, so it owns the concept -- but the label is
          rendered from the pack's table, so a pack omitting the key had papers
          counted into a class that was never printed.
  LEAK 5  `test_seam.py`'s statement of the interface was a hand-written list,
          and it was missing FIVE names `pe/` genuinely imports: `CC_TEXT`,
          `PRIMARY_FIELD_GLOSS`, `FOOTER`, and the two added above. A
          hand-maintained list of what the interface IS, is precisely the
          duplication this split was about. It is derived from the harness's own
          imports now, in `test_seam.py`, so it cannot drift again.

Leaks 3 and 4 are one shape, and `test_no_pack_class_is_hardcoded_in_the_harness`
is the generalisation: a class name or check code from ANY pack's tables must not
appear as a string literal in `pe/`.

The pack itself is archived at `examples/second-pack/`, with its 10-paper result
and its leak list. The guards below deliberately do NOT use it: they build a
synthetic minimal pack instead, so this file keeps working when that snapshot
inevitably falls behind the interface.

Run: python -m pytest tests/test_second_pack.py -q
"""
from __future__ import annotations

import io
import json
import re
import subprocess
import sys
import tokenize
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pe.pack import tables  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
yaml = pytest.importorskip("yaml")


# --------------------------------------------------------------------------
# LEAKS 3 + 4, generalised: the harness may not name a pack's classes
# --------------------------------------------------------------------------

def _pack_class_vocabulary() -> set[str]:
    """Every change-class name and check code this pack declares."""
    loaded = tables(ROOT)
    names = set(map(str, loaded["change"].get("classes") or {}))
    names |= set(map(str, loaded["decide"].get("checks") or {}))
    names |= {str(loaded["change"].get("unexplained_class") or "")}
    names |= {str(loaded["change"].get("noise_class") or "")}
    for array in loaded["record"].get("secondary_arrays") or []:
        names.add(str(array.get("unverified_flag") or ""))
    return {n for n in names if n}


def test_no_pack_class_is_hardcoded_in_the_harness():
    """A class the harness names is a class every pack must declare.

    `test_seam.py` misses this: `SUPP-EVIDENCE` and `WITHIN-NOISE` contain none
    of its task words, so both sailed through while being one pack's vocabulary
    embedded in the harness. The failure was silent in both cases -- one printed
    prose about a class that did not exist, the other counted papers into a class
    that was never rendered.
    """
    vocabulary = _pack_class_vocabulary()
    assert vocabulary, "the pack declares no classes or codes -- parser broken"
    offenders: dict[str, set[str]] = {}
    for path in sorted((ROOT / "pe").glob("*.py")):
        for token in tokenize.generate_tokens(io.StringIO(path.read_text()).readline):
            if token.type != tokenize.STRING:
                continue
            if token.string.lstrip("rbuRBUf").startswith(('"""', "'''")):
                continue          # prose about history is allowed
            for name in vocabulary:
                if re.search(r"\b" + re.escape(name) + r"\b", token.string):
                    offenders.setdefault(f"{path.name}:{token.start[0]}", set()).add(name)
    assert not offenders, (
        f"the harness names pack classes in code: "
        f"{ {k: sorted(v) for k, v in offenders.items()} }. Read the name from the "
        f"pack's table instead -- a literal here is one pack's vocabulary that "
        f"every other pack must then happen to share.")


def test_the_pack_declares_the_two_classes_the_harness_assigns():
    """The harness assigns `unexplained_class` and `noise_class` itself, so both
    have to be named by the pack or their labels cannot be rendered."""
    change = tables(ROOT)["change"]
    for key in ("unexplained_class", "noise_class"):
        name = change.get(key)
        assert name, f"change.yaml declares no {key}"
        assert name in change["classes"], (
            f"change.yaml's {key} is {name!r} but `classes` has no such entry, so "
            f"papers in that class would be counted and never printed")


def test_the_diff_preamble_is_the_packs_not_the_harnesss():
    """Which caveat a reader needs depends on the question."""
    from task.change import DIFF_PREAMBLE
    assert DIFF_PREAMBLE and all(isinstance(line, str) for line in DIFF_PREAMBLE)


def test_no_key_in_the_harness_contract_is_read_by_nothing():
    """A key nobody reads looks like it works and does not.

    `spec.read_back_marker` was exactly that: `task.yaml` declared it, `pack.py`
    parsed it into `TaskPack.read_back_marker`, and **nothing read it** --
    `paper_text_from_prompt` hardcoded the string. A pack declaring any other
    marker was silently ignored, and the recovered "paper text" would have been
    the whole prompt file including the instructions, so every quote would verify
    against the spec as readily as against the paper. That is the pack-side twin
    of `test_no_config_key_is_read_by_nothing`, which exists because five
    `config.yaml` keys had rotted the same way.

    **Scoped to `task.yaml`**, which is the contract between the pack and the
    HARNESS -- the relationship where a mismatch is silent and expensive, because
    the two sides ship separately and neither can see the other's literals.

    The four tables are deliberately out of scope, and not because they are
    clean. Two sets of keys in them are read by nothing today:

      report.yaml   `blurb`, `empty`, `grep` -- this pack's `screens.py` emits
                    its blurbs and empty-notes as literals, because the screen
                    bodies were moved verbatim rather than parameterised.
      record.yaml   `item_array.quotes_field`, `name_field`,
                    `secondary_quote_field`, `secondary_downgrades`,
                    `drop_when_no_verified_quote`, and the `ref_arrays` /
                    `secondary_arrays` shape keys -- `rules.py` hardcodes
                    `"evidence_quotes"`, `"assay_evidence"` and `"agent"`.

    Those are documentation pretending to be configuration, and worth fixing --
    but they are a pack talking to its OWN rule modules, both of which ship
    together, so an author who edits one and not the other breaks their own pack
    and finds out at once. Recorded here rather than suppressed in an allow-list,
    so the finding survives whether or not anyone acts on it.
    """
    source = "\n".join(
        p.read_text() for p in
        sorted((ROOT / "pe").glob("*.py")) + sorted((ROOT / "task").glob("*.py")))
    contract = yaml.safe_load((ROOT / "task" / "task.yaml").read_text()) or {}

    #: Read via the parent mapping rather than by name, or written for a human.
    UNREAD = {
        "spec", "anchors", "placeholders", "outputs",   # read as whole mappings
        "question",                                      # prose for a pack author
    }
    dead = sorted(k for k in _all_keys(contract)
                  if k not in UNREAD
                  and f'"{k}"' not in source and f"'{k}'" not in source)
    assert not dead, (
        f"task.yaml declares {dead} and nothing in pe/ or task/ reads them. Wire "
        f"the key up, delete it, or add it to UNREAD with a reason. "
        f"`spec.read_back_marker` sat dead for a whole version, and a pack that "
        f"changed it was silently ignored.")


def _all_keys(node, out=None) -> set[str]:
    """Every mapping key, at any depth."""
    out = set() if out is None else out
    if isinstance(node, dict):
        for key, value in node.items():
            out.add(str(key))
            _all_keys(value, out)
    elif isinstance(node, list):
        for value in node:
            _all_keys(value, out)
    return out


# --------------------------------------------------------------------------
# LEAK 1: a pack with no secondary array
# --------------------------------------------------------------------------

_MINIMAL_SPEC = """# Minimal spec
Version: {{TASK_VERSION}}

## Instruction prompt

```
Answer the question.
Echo `task_version` as "{{TASK_VERSION}}".

PAPER_ID: {{PAPER_ID}}
SOURCE_IDS: {{SOURCE_IDS}}

PAPER_TEXT:
{{PAPER_TEXT}}
```

## Output schema

```json
{
  "task_version": "{{TASK_VERSION}}",
  "answer": "yes | no | unclear"
}
```

## Toggle decisions
none
"""


def _copy_harness(base: Path) -> None:
    """Copy `pe/` verbatim -- files only, so `__pycache__` is not read as one."""
    target = base / "pe"
    target.mkdir(parents=True, exist_ok=True)
    for module in sorted((ROOT / "pe").iterdir()):
        if module.is_file():
            (target / module.name).write_bytes(module.read_bytes())


def _minimal_pack(base: Path) -> None:
    """A pack with NO secondary array and NO ref arrays -- the shape that broke."""
    (base / "task").mkdir(parents=True)
    (base / "prompt.md").write_text(_MINIMAL_SPEC)
    (base / "task" / "task.yaml").write_text(yaml.safe_dump({
        "name": "minimal", "version": "0.0.1",
        "outputs": {"run_root_subdir": "minimal", "env_var": "MINIMAL_RUN_ROOT",
                    "per_paper_file": "answer.json", "summary_csv": "s.csv",
                    "review_txt": "r.txt", "diff_txt": "d.txt"},
        "spec": {"path": "prompt.md",
                 "anchors": {"instruction": "## Instruction prompt",
                             "schema_start": "## Output schema",
                             "schema_end": "## Toggle decisions"},
                 "placeholders": {"paper_id": "{{PAPER_ID}}",
                                  "paper_text": "{{PAPER_TEXT}}",
                                  "source_ids": "{{SOURCE_IDS}}",
                                  "task_version": "{{TASK_VERSION}}"},
                 "read_back_marker": "\nPAPER_TEXT:"},
    }, sort_keys=False))
    (base / "task" / "record.yaml").write_text(yaml.safe_dump({
        "labels": ["yes", "no", "unclear"],
        "primary_field": "answer", "model_field": "answer_model",
        "run_states": {"processing_status": ["ok", "partial", "failed"],
                       "text_completeness": ["full", "truncated", "unknown"]},
        "unresolved_reasons": ["none"],
        "required_fields": ["task_version", "answer"],
        "legacy_field_names": {},
        "field_checks": [{"path": "answer", "in": "labels",
                          "message": "{path}={value!r} off-schema"}],
        "item_array": {"path": "items", "label_field": "paired",
                       "name_field": "name", "quotes_field": "quotes",
                       "secondary_quote_field": "pairing",
                       "secondary_downgrades": "paired",
                       "drop_when_no_verified_quote": True,
                       "enums": {"paired": "labels"}, "open_fields": {}},
        "ref_arrays": [],
        "secondary_arrays": [],          # <-- LEAK 1
        "normalisers": {}, "downgrade_confidence": 0.2,
    }, sort_keys=False))
    (base / "task" / "decide.yaml").write_text(yaml.safe_dump({
        "inputs": {"processing_status": "processing_status",
                   "paired": "items[].paired"},
        "cap": {"when_status": "partial", "when_completeness_not": "full",
                "from": "no", "to": "unclear", "reason": "none"},
        "checks": {}, "harness_raised_checks": [],
        "reason_rules": {"none_value": "none", "required_when": "unclear",
                         "cap_reason": "none"},
    }, sort_keys=False))
    (base / "task" / "report.yaml").write_text(yaml.safe_dump({
        "tiers": [{"n": 9, "summary": "all", "label": "everything"}],
        "low_confidence_yes": 0.6, "triaged_unresolved_reasons": [],
        "columns": ["triage_priority", "doi", "status", "answer"],
        "column_limits": {},
        "screens": [{"id": "A", "summary": "all", "title": "all",
                     "empty": "none"}],
        "footer": ["-"], "traps": {}, "signals": {},
    }, sort_keys=False))
    (base / "task" / "change.yaml").write_text(yaml.safe_dump({
        "order": ["yes", "unclear", "no"],
        "classes": {"UNEXPLAINED": "outside every class",
                    "WITHIN-NOISE": "self-disagreement"},
        "unexplained_class": "UNEXPLAINED", "noise_class": "WITHIN-NOISE",
        "match": {"min_shared_words": 2, "standalone_word_length": 8,
                  "stopwords": ["the"]},
    }, sort_keys=False))
    # No `task/__init__.py`: the loader is `pe/pack.py`, which arrives with the
    # harness. Copying it per pack is what this move removed.
    # The four rule modules, minimal.
    (base / "task" / "rules.py").write_text('''
from pe.pack import tables
_REC = tables()["record"]
_PATH = _REC["item_array"]["path"]


def stage_a(record):
    return record.get("answer")


def stage_b(a, status, completeness):
    return a, False


def decide(record):
    return record.get("answer"), record.get("answer"), False


def checks(record):
    return []


def extra_field_issues(record):
    return []


def validate_items(record, verify, issues, flags):
    return {"kept": [], "dropped": [], "index_map": {},
            "checked": 0, "failed": 0, "wrong_source": 0}


def validate_secondary(record, verify, issues, flags):
    return [], 0, 0, 0


def metrics(record, ctx):
    return {}


def progress_line(doi, record):
    return f"  {doi} {record.get('answer')}"


CC_TEXT = {}
''')
    (base / "task" / "report.py").write_text('''
from pe.pack import tables
COLUMNS = list(tables()["report"]["columns"])
TIERS = [(9, "everything")]


def tier_labels():
    return TIERS


def triage_priority(record):
    return 9


def row_for(doi, record, entry):
    return {"triage_priority": 9, "doi": doi, "status": "ok",
            "answer": record.get("answer", "")}


def counters(rows, results):
    return ["", f"{len(rows)} row(s)"]
''')
    (base / "task" / "screens.py").write_text('''
from pe.pack import tables
SCREENS = {s["id"]: s for s in tables()["report"]["screens"]}


FOOTER = list(tables()["report"]["footer"])


def render(loaded, text_for):
    return [], {"A": 0}
''')
    (base / "task" / "change.py").write_text('''
from pe.pack import tables
_T = tables()
ORDER = list(_T["change"]["order"])
CLASS_LABELS = dict(_T["change"]["classes"])
UNEXPLAINED = _T["change"]["unexplained_class"]
NOISE_CLASS = _T["change"]["noise_class"]
PRIMARY_FIELD = _T["record"]["primary_field"]
PRIMARY_FIELD_GLOSS = f"`{PRIMARY_FIELD}`"
DIFF_PREAMBLE = ["minimal"]


def determination_inputs(record):
    return {"processing_status": record.get("processing_status"),
            "paired": sorted(str(i.get("paired"))
                             for i in (record.get("items") or []))}


def classify(new, old=None):
    return [UNEXPLAINED]


def render_paper(doi, old, new, entry, classes):
    return [doi]


def render_unchanged(doi, new, entry):
    return doi
''')


def test_a_pack_with_no_secondary_array_imports(tmp_path):
    """LEAK 1, reproduced. This is an import-time failure, so it is checked in a
    subprocess with the minimal pack on the path -- `pe.validate` reads its
    tables at import and cannot be re-imported against a different pack
    in-process."""
    base = tmp_path / "skill"
    base.mkdir()
    _minimal_pack(base)
    _copy_harness(base)

    probe = subprocess.run(
        [sys.executable, "-c",
         "import pe.validate as v, pe.summarize, pe.audit, pe.compare, pe.pending; "
         "print(v.SECONDARY_PATH)"],
        cwd=base, capture_output=True, text=True)
    assert probe.returncode == 0, (
        f"a pack with no secondary array cannot even be imported:\n{probe.stderr}")
    assert probe.stdout.strip() == "None"


def test_a_pack_with_no_secondary_array_validates_a_record(tmp_path):
    """And the write-back is skipped rather than crashing on a None key."""
    base = tmp_path / "skill"
    base.mkdir()
    _minimal_pack(base)
    _copy_harness(base)

    script = (
        "import json\n"
        "from pe.validate import validate_result\n"
        "out = validate_result({'task_version': '0.0.1', 'answer': 'yes',\n"
        "                       'processing_status': 'ok', 'items': []},\n"
        "                      {'main': 'text'}, 0.85, version='0.0.1')\n"
        "print(json.dumps({'answer': out['answer'],\n"
        "                  'keys': sorted(k for k in out if 'suppress' in k)}))\n"
    )
    probe = subprocess.run([sys.executable, "-c", script], cwd=base,
                           capture_output=True, text=True)
    assert probe.returncode == 0, probe.stderr
    result = json.loads(probe.stdout)
    assert result["answer"] == "yes"
    assert result["keys"] == [], "a pack with no secondary array grew one"


# --------------------------------------------------------------------------
# LEAK 2: a failure computing the queue is not an empty queue
# --------------------------------------------------------------------------

def test_run_headless_refuses_when_the_pending_list_cannot_be_computed(tmp_path):
    """LEAK 2, reproduced. A traceback in the queue heredoc used to leave `$DOIS`
    empty, and `[ -z "$DOIS" ]` reads that as "every paper already has a result".
    So a pack that cannot be imported reported a completed run and exited 0."""
    base = tmp_path / "skill"
    _copy_harness(base)
    (base / "pe" / "run_headless.sh").chmod(0o755)
    # No task/ at all: the import cannot succeed.
    work = base / "work"
    (work / "raw").mkdir(parents=True)
    (work / "prompts").mkdir(parents=True)
    (work / "manifest.json").write_text(json.dumps(
        [{"doi": "x", "source_ids": ["main"]}]))

    probe = subprocess.run(["bash", "pe/run_headless.sh", str(work), "1"],
                           cwd=base, capture_output=True, text=True,
                           env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
                                "PERTURBATION_SKIP_PREFLIGHT": "1"})
    assert probe.returncode != 0, (
        "a run whose pending list could not be computed exited 0 -- "
        "indistinguishable from a finished run")
    assert "NOT an empty queue" in probe.stderr + probe.stdout
