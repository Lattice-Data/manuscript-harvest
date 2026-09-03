"""Reading a task pack: what it declares, and whether it declares enough.

**This is plumbing, and it used to live in the judgment layer.** It was
`task/__init__.py` -- 232 lines of loader, hashing and shape-validation sitting
in the directory whose whole job is to hold the answer to one question. A second
pack found it the hard way: it had to copy the file verbatim, so every pack would
carry an identical copy of machinery none of them owns.

So `task/` now has no `__init__.py` at all. It is a namespace package, like `pe/`
already was, and it contains nothing but the spec, the four tables and the four
rule modules. Nothing in it is generic.

The dependency runs pack -> harness, which is the right direction and worth
stating because it looks backwards at first glance. The harness must never
import a task's vocabulary; a task importing the harness's loader is the ordinary
plugin shape, and `tests/test_seam.py` enforces exactly that asymmetry.

**What is NOT here, deliberately.** `pack_sha256` covers the rules -- the spec and
`task/*` -- and not this file, for the same reason it does not cover
`pe/validate.py`: a change to how tables are READ is a change to the harness, and
the harness is not what one run differs from another by. Moving this file out of
the pack therefore changes every pack's hash exactly once, which is honest -- the
set of rule-bearing files really did change.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - yaml is a package requirement
    yaml = None

#: The skill root: the directory holding prompt.md, pe/ and task/. Unchanged by
#: the move -- this file went from `task/__init__.py` to `pe/pack.py`, and
#: `parent.parent` is the same directory from either.
ROOT = Path(__file__).resolve().parent.parent

#: Files whose contents define the answer, and therefore the pack hash.
#:
#: `config.yaml` is deliberately NOT here. It carries `corpus_dir`, a path
#: specific to whoever is running, so hashing it would make two identical runs
#: on two machines look like different rules -- the exact confusion the hash
#: exists to remove.
#:
#: Neither is this module, which is why `task/*.py` reads as "the rule modules"
#: rather than "everything in task/": the loader moved out of the pack precisely
#: because it is not a rule.
PACK_GLOBS = ("prompt.md", "task/*.yaml", "task/*.py")


class PackError(Exception):
    """The pack is missing, unreadable, or does not declare what it must."""


def pack_files(root: Path | None = None) -> list[Path]:
    """Every rule-bearing file, in a stable order.

    Sorted by path relative to the root rather than absolute, so the hash does
    not depend on where the skill is checked out.
    """
    base = root or ROOT
    found: set[Path] = set()
    for pattern in PACK_GLOBS:
        found.update(p for p in base.glob(pattern)
                     if p.is_file() and "__pycache__" not in p.parts)
    return sorted(found, key=lambda p: str(p.relative_to(base)))


def pack_sha256(root: Path | None = None) -> str:
    """A hash over the rules themselves.

    Paths are hashed alongside contents, so moving a rule between files changes
    the hash even when the bytes are conserved -- a file split is a change to
    the rules a reader has to find.
    """
    base = root or ROOT
    digest = hashlib.sha256()
    for path in pack_files(base):
        digest.update(str(path.relative_to(base)).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


class TaskPack:
    """Loaded `task.yaml`, plus the hash of everything it points at."""

    def __init__(self, config: dict, root: Path) -> None:
        self.root = root
        self._config = config
        for field in ("name", "version", "spec"):
            if not config.get(field):
                raise PackError(f"task.yaml declares no {field!r}")
        self.name = str(config["name"])
        self.version = str(config["version"])
        spec = config["spec"]
        self.spec_path = root / str(spec.get("path") or "prompt.md")
        self.anchors = dict(spec.get("anchors") or {})
        self.placeholders = dict(spec.get("placeholders") or {})
        self.read_back_marker = str(spec.get("read_back_marker") or "\nPAPER_TEXT:")
        missing = [k for k in ("instruction", "schema_start", "schema_end")
                   if not self.anchors.get(k)]
        if missing:
            raise PackError(f"task.yaml spec.anchors is missing {missing}")
        missing = [k for k in ("paper_id", "paper_text", "source_ids", "task_version")
                   if not self.placeholders.get(k)]
        if missing:
            raise PackError(f"task.yaml spec.placeholders is missing {missing}")

    @property
    def question(self) -> str:
        return str(self._config.get("question") or "").strip()

    def sha256(self) -> str:
        return pack_sha256(self.root)

    def stamp(self) -> dict:
        """What every manifest entry and every record records about the rules.

        Both values, because they answer different questions. `task_version` is
        what the harness grades against and is comparable across runs;
        `pack_sha256` says whether the rules were byte-identical, which a version
        number only asserts.
        """
        return {"task": self.name, "task_version": self.version,
                "pack_sha256": self.sha256()}

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<TaskPack {self.name} {self.version} {self.sha256()[:12]}>"


#: The four lookup tables, by the name they are referred to throughout.
TABLE_FILES = {
    "record": "record.yaml",   # what counts
    "decide": "decide.yaml",   # how to decide
    "report": "report.yaml",   # what to read first
    "change": "change.yaml",   # what counts as a change
}

_TABLES: dict[str, dict] | None = None


def _reject_yaml_booleans(path: Path, node, trail: str = "") -> None:
    """Refuse a boolean where a VALUE belongs -- that is, inside a list.

    YAML 1.1 reads `yes` and `no` as True and False, so a label set written
    `[yes, no, unclear]` loads as `[True, False, 'unclear']`. Nothing crashes:
    every tri-state comparison in the harness just silently stops matching,
    because a determination gets compared against `True` and never equals the
    string the model emitted. The answers quietly change. Found by writing
    exactly that bug into record.yaml.

    Scoped to list ELEMENTS rather than every value, because a scalar flag like
    `drop_when_no_verified_quote: true` is a real boolean and rejecting it would
    make the guard something a pack author has to work around. A list in these
    tables is always a set of values -- labels, enum members, field names,
    stopwords, regexes -- and none of those is ever a boolean.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            _reject_yaml_booleans(path, value, f"{trail}.{key}" if trail else str(key))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            where = f"{trail}[{i}]"
            if isinstance(value, bool):
                raise PackError(
                    f"{path.name}: {where} is the YAML boolean {value!r}, but a list "
                    f"in this table is a set of VALUES. Quote it -- \"yes\" / "
                    f"\"no\" -- because YAML 1.1 reads the bare words as booleans, "
                    f"and a label that arrives as True never matches the string a "
                    f"record carries.")
            _reject_yaml_booleans(path, value, where)


def tables(root: Path | None = None, *, reload: bool = False) -> dict[str, dict]:
    """The four tables, read once and cached.

    Cached because `rules.py` reads them at import to define its constants, and
    because every module in `pe/` would otherwise re-parse four files per paper.
    `reload=True` exists for the tests that write a pack into a tmp_path.

    A missing table is an error, not an empty default. A pack that half-loads is
    a run applying rules nobody can name, and the one thing this pipeline may not
    do is proceed while unable to say what it is applying.
    """
    global _TABLES
    if _TABLES is not None and not reload and root in (None, ROOT):
        return _TABLES
    base = root or ROOT
    if yaml is None:
        raise PackError("pyyaml is required to read the task pack")
    loaded: dict[str, dict] = {}
    for name, filename in TABLE_FILES.items():
        path = base / "task" / filename
        if not path.is_file():
            raise PackError(f"the pack has no {filename} (table {name!r}) at {path}")
        try:
            data = yaml.safe_load(path.read_text()) or {}
        except yaml.YAMLError as exc:
            raise PackError(f"{path} is not readable YAML: {exc}") from exc
        if not isinstance(data, dict):
            raise PackError(f"{path} is not a mapping")
        _reject_yaml_booleans(path, data)
        loaded[name] = data
    if root in (None, ROOT):
        _TABLES = loaded
    return loaded


def load(root: Path | None = None) -> TaskPack:
    """Read `task/task.yaml`. Raises PackError rather than returning a default.

    No fallback on purpose. A pack that cannot be read is not a pack running
    with defaults -- it is a run whose rules are unknown, and the one thing this
    pipeline may not do is proceed while unable to say what it is applying.
    """
    base = root or ROOT
    path = base / "task" / "task.yaml"
    if yaml is None:
        raise PackError("pyyaml is required to read the task pack")
    if not path.is_file():
        raise PackError(f"no task pack at {path}")
    try:
        config = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise PackError(f"{path} is not readable YAML: {exc}") from exc
    if not isinstance(config, dict):
        raise PackError(f"{path} is not a mapping")
    return TaskPack(config, base)
