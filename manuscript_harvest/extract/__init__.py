"""Extraction stage: corpus bytes -> blocks of text with provenance."""

import hashlib
from pathlib import Path

__version__ = "0.2.0"


def source_fingerprint() -> str:
    """A hash of this package's own parser source, for the extraction cache.

    `__version__` has been bumped exactly once, by a rename, while `sections.py`
    changed materially twice. Extracting 10.1126/science.aat5031 before and after
    those changes gives 21 blocks a different `section` -- at the same manifest
    sha and the same `"0.1.0"`, so `--force` was the only thing that had ever
    picked up a parser fix. A version number nobody remembers to bump is not a
    cache key; the source is.

    Returns `""` when the glob finds nothing, which is a source-less install
    (a zipimport or a frozen bundle). The key then rests on `__version__` alone,
    which is the old behaviour rather than a new failure.
    """
    files = sorted(Path(__file__).parent.glob("*.py"))
    if not files:
        return ""
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]
