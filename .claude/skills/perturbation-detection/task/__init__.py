"""The task pack: what question is being asked, and under exactly which rules.

`pe/` is the harness and holds no version literal. This package holds the
identity, and Stage 3 of the refactor moves the four judgment tables in beside
it. The split the whole thing is aimed at:

    JUDGMENT   task/  -- the spec + the tables. Swap this.
    PLUMBING   pe/    -- assemble, splice, one call per paper, verify every
                        quote, prune, recompute, tabulate, diff. Keep this.
    TEXT       manuscript_harvest -- DOI to labelled text with provenance. Keep.

Two things live here that used to be spread across five files.

**One version.** `prompt_version` and `schema_version` were separate because
somebody had to decide, per revision, whether the record shape had moved. At
v0.0.12 that decision was made and then applied to three of four declaration
sites; the model split on the contradiction, 386 records said 0.0.7 and 6 said
0.0.6, and `pe.validate` compared against a literal calibrated to the minority.
There is now one number, declared in `task.yaml`, and prompt.md carries
`{{TASK_VERSION}}` rather than a literal at every site that declares it -- so a
stale version is not a bug to be caught but a state the file cannot be in.

**One hash.** `pack_sha256` covers every rule-bearing file, so "were these two
records produced under the same rules" is a comparison rather than an opinion.
That is the question the two-number scheme was really trying to answer, and a
hand-maintained semver answers it badly: a version bump says the author thought
something changed, while the hash says whether anything did.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - yaml is a package requirement
    yaml = None

#: The skill root: the directory holding prompt.md, pe/ and task/.
ROOT = Path(__file__).resolve().parent.parent

#: Files whose contents define the answer, and therefore the pack hash.
#:
#: `config.yaml` is deliberately NOT here. It carries `corpus_dir`, a path
#: specific to whoever is running, so hashing it would make two identical runs
#: on two machines look like different rules -- the exact confusion the hash
#: exists to remove. The text policy it also holds does belong in the hash and
#: moves into this package at Stage 3, at which point it is covered.
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
