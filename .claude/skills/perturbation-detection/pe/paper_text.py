"""Assemble a paper into labelled sources, and verify quotes against them.

Two jobs, and they are the two halves of one contract: this module decides what
text the model is shown, so it is also what a quote has to be found in. Splitting
them would put the writer of the `<<<SOURCE>>>` markers in one file and the
reader in another, and those are the two things that must never disagree.

No LLM calls, and no task vocabulary -- this and `pe/prepare.py` were the two
files in the skill that never named perturbations even before the 0.0.13 split,
which is what made them the evidence a seam existed to be found.

Two things left here and went to the modules that already owned them:
`prompt_version` -> `pe.pack.spec_version_line`, since reading the spec is the
pack loader's job; and `entry_paths` -> `pe.runstate`, since what a run directory
looks like is that module's whole subject. Neither had anything to do with
assembling text. `reconstruct_text` went earlier still, superseded by
`build_sources` when v0.0.5 made supplementary files first-class sources.
"""

from __future__ import annotations

import json
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

# Sections never fed to the model. 'back_matter' holds acknowledgments, author
# contributions and declaration-of-interests in JATS-derived articles;
# config.yaml's flat names ('acknowledgments', 'funding', ...) never matched it.
EXCLUDE_SECTIONS = (
    "references",
    "supplementary",
    "acknowledgments",
    "competing_interests",
    "funding",
    "back_matter",
    "data_availability",
)

# 'metadata' carries "Title: ... / Journal: ... / DOI: ...", which prompt.md
# v0.0.2 requires up front. 'table' is dropped: in Cell Press STAR Methods the
# KEY RESOURCES TABLE lists every antibody and compound in the lab, which is a
# false-positive magnet for a task about the *role* a reagent plays.
INCLUDE_KINDS = ("metadata", "heading", "paragraph", "caption")


# PDF extraction in this corpus emits control characters where symbols belonged:
# U+0001 for a minus sign ("Lin-"), U+0004 for a degree sign, etc. Present in
# 9 of the 40 validation papers.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def normalize_text(text: str) -> str:
    """Normalize for quote matching, per prompt.md's post-processing rules."""
    text = _CONTROL_CHARS.sub("", text)
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("–", "-").replace("—", "-")
    text = text.replace("“", '"').replace("”", '"')
    text = text.replace("‘", "'").replace("’", "'")
    text = text.replace(" ", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def read_blocks_jsonl(article_dir: Path) -> list[dict]:
    """Read an article's blocks. Canonical path is <article>/extracted/blocks.jsonl."""
    for candidate in (article_dir / "extracted" / "blocks.jsonl", article_dir / "blocks.jsonl"):
        if candidate.exists():
            return [json.loads(line) for line in candidate.open() if line.strip()]
    return []


def _anchors(quote_norm: str, limit: int = 6) -> list[tuple[str, int]]:
    """Distinctive substrings of the quote, with their offsets, for candidate seeking."""
    words = [(m.group(0), m.start()) for m in re.finditer(r"\w{5,}", quote_norm)]
    words.sort(key=lambda wp: len(wp[0]), reverse=True)
    return words[:limit]


def fuzzy_match_quote(quote: str, full_text: str, threshold: float = 0.85) -> tuple[bool, float]:
    """Locate a quote in the paper text. Returns (found, best_ratio).

    Normalized substring match first; otherwise anchor-seeded fuzzy windows.
    A naive sliding window over a 150k-char paper would run SequenceMatcher
    150k times per quote, so candidate positions come from word anchors.
    """
    quote_norm = normalize_text(quote).casefold()
    text_norm = normalize_text(full_text).casefold()

    if not quote_norm:
        return False, 0.0
    if quote_norm in text_norm:
        return True, 1.0

    qlen = len(quote_norm)
    candidates: set[int] = set()
    for anchor, offset in _anchors(quote_norm):
        start = 0
        while len(candidates) < 600:
            hit = text_norm.find(anchor, start)
            if hit < 0:
                break
            candidates.add(max(0, hit - offset))
            start = hit + 1

    best = 0.0
    for pos in candidates:
        # Widen slightly: dropped superscripts make the source span longer
        # than the quote (e.g. "1 x 10cells" vs "1 x 10 6 cells").
        window = text_norm[pos : pos + qlen + 12]
        matcher = SequenceMatcher(None, quote_norm, window)
        if matcher.real_quick_ratio() < threshold or matcher.quick_ratio() < threshold:
            continue
        best = max(best, matcher.ratio())
        if best >= threshold:
            return True, best

    return False, best


# ---------------------------------------------------------------------------
# v0.0.5: multi-source assembly
#
# prompt.md v0.0.5 makes supplementary files first-class evidence, each carrying
# its own `source_id` so a quote can be verified against the source it claims.
# That replaces v0.0.4's flat main-text-only string, and it brings three traps
# that the assembly below exists to handle:
#
#   1. Cell Press ships an "accepted manuscript" mmc PDF that is 83-97% a COPY
#      of the article (8 of the 40 validation papers). Feeding it in doubles the
#      token cost for no information and, worse, gives the model two legitimate
#      sources for the same sentence -- which makes prompt.md's EV-WRONG-SOURCE
#      attribution check meaningless. Duplicate blocks are dropped.
#   2. Some supplementary files are administrative forms (MDAR reproducibility
#      checklists, eLife transparent-reporting forms), never evidence.
#   3. Supplementary PDFs carry their own reference lists and
#      competing-interest statements, which prompt.md says to ignore.
# ---------------------------------------------------------------------------

# Sections excluded from supplementary sources. Narrower than EXCLUDE_SECTIONS:
# a supplementary file is usually ALL methods, and 'supplementary' as a section
# label inside a supplementary file is not a reason to drop it.
SUPP_EXCLUDE_SECTIONS = (
    "references",
    "back_matter",
    "data_availability",
    "competing_interests",
    "funding",
    "acknowledgments",
)

# Administrative attachments, never scientific evidence.
_ADMIN_FILE = re.compile(
    r"mdar|reproducibility[_-]?checklist|transrepform|reporting[_-]?summary|readme",
    re.IGNORECASE,
)

# Below this, a "unique" supplementary file is extraction noise, not a source.
MIN_SUPP_CHARS = 250

# Blocks shorter than this are not compared for duplication -- a 20-character
# heading legitimately recurs, and dropping it would fragment the text.
_DEDUP_MIN_CHARS = 40


def is_supplementary(block: dict) -> bool:
    """The decisive supplementary test: source_file, not section.

    `section` is often None for PDF-derived articles, which is how whole
    supplementary PDFs used to leak into the main text: the original filter
    tested a `source` field that does not exist on these blocks, so it never
    fired, and 40% of the assembled text turned out to be supplement.
    """
    source_file = block.get("source_file") or ""
    return source_file.startswith("supplementary/") or "supplement" in source_file.lower()


def _block_texts(blocks, exclude_sections, include_kinds) -> list[str]:
    out = []
    for block in blocks:
        if not block or block.get("kind") not in include_kinds:
            continue
        section = (block.get("section") or "").lower()
        if section and any(excluded in section for excluded in exclude_sections):
            continue
        text = (block.get("text") or "").strip()
        if text:
            out.append(text)
    return out


def build_sources(blocks: list[dict], exclude_sections=EXCLUDE_SECTIONS,
                  include_kinds=INCLUDE_KINDS, include_supplementary: bool = True,
                  supp_exclude_sections=SUPP_EXCLUDE_SECTIONS) -> tuple[list[dict], dict]:
    """Split an article's blocks into prompt.md v0.0.5 sources.

    Returns (sources, stats). `sources[0]` is always the main text with
    source_id "main"; supplementary files follow as supp1..suppN in the
    publisher's own file order, which is what the manifest records.
    """
    main_blocks = [b for b in blocks if b and not is_supplementary(b)]
    supp_blocks = [b for b in blocks if b and is_supplementary(b)]

    main_text = "\n\n".join(_block_texts(main_blocks, exclude_sections, include_kinds))

    # `chars` and `supp_chars` are seeded here rather than only on the way out.
    # The main-text-only path returned before the block that set them, so
    # `pe.prepare --no-supplementary` -- a toggle prompt.md documents -- died on
    # `KeyError: 'chars'` and had never once worked. Every key this dict will ever
    # carry now exists before the first return.
    stats = {
        "main_chars": len(main_text),
        "chars": len(main_text),
        "supp_chars": 0,
        "supp_files_seen": 0,
        "supp_files_kept": 0,
        "supp_dropped_admin": [],
        "supp_dropped_duplicate": [],
        "supp_dropped_tiny": [],
        "supp_duplicate_chars": 0,
    }

    sources = [{
        "source_id": "main",
        "source_type": "main_text",
        "source_file": "main",
        "text": main_text,
        "char_count": len(main_text),
        "references_stripped": True,
    }]

    if not include_supplementary:
        return sources, stats

    # Normalized main-text blocks, for duplicate detection.
    main_norm = set()
    for text in _block_texts(main_blocks, exclude_sections, include_kinds):
        norm = normalize_text(text)
        if len(norm) >= _DEDUP_MIN_CHARS:
            main_norm.add(norm)

    by_file: dict[str, list[dict]] = {}
    for block in supp_blocks:
        by_file.setdefault(block.get("source_file") or "supplementary/unknown", []).append(block)

    stats["supp_files_seen"] = len(by_file)
    index = 0
    for source_file in sorted(by_file):
        if _ADMIN_FILE.search(source_file):
            stats["supp_dropped_admin"].append(source_file)
            continue

        kept, dup_chars = [], 0
        for text in _block_texts(by_file[source_file], supp_exclude_sections, include_kinds):
            norm = normalize_text(text)
            if len(norm) >= _DEDUP_MIN_CHARS and norm in main_norm:
                dup_chars += len(text)
                continue
            kept.append(text)

        stats["supp_duplicate_chars"] += dup_chars
        text = "\n\n".join(kept)
        original = sum(len(t) for t in _block_texts(
            by_file[source_file], supp_exclude_sections, include_kinds))

        if len(text) < MIN_SUPP_CHARS:
            # Either genuinely empty, or a near-complete duplicate of the article.
            bucket = ("supp_dropped_duplicate" if dup_chars > len(text)
                      else "supp_dropped_tiny")
            stats[bucket].append(f"{source_file} ({original:,}->{len(text):,} chars)")
            continue

        index += 1
        sources.append({
            "source_id": f"supp{index}",
            "source_type": "supplementary",
            "source_file": source_file,
            "text": text,
            "char_count": len(text),
            "references_stripped": True,
        })

    stats["supp_files_kept"] = index
    stats["supp_chars"] = sum(s["char_count"] for s in sources if s["source_id"] != "main")
    stats["chars"] = sum(s["char_count"] for s in sources)
    return sources, stats


def source_marker(source: dict) -> str:
    return f"<<<SOURCE id={source['source_id']} type={source['source_type']}>>>"


def assemble_paper_text(sources: list[dict]) -> str:
    """Concatenate sources with the explicit markers prompt.md v0.0.5 requires."""
    return "\n".join(f"{source_marker(s)}\n{s['text']}" for s in sources)


_SOURCE_MARKER_RE = re.compile(r"^<<<SOURCE id=(\S+) type=(\S+)>>>$", re.MULTILINE)


def split_assembled(paper_text: str) -> dict[str, str]:
    """Recover per-source text from an assembled string.

    Validation must compare a quote against the exact bytes the model was shown,
    so the split is done by re-parsing the markers rather than by re-reading
    blocks.jsonl -- which could have changed since the prompt was built.
    """
    matches = list(_SOURCE_MARKER_RE.finditer(paper_text))
    if not matches:
        return {"main": paper_text}
    out = {}
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(paper_text)
        out[match.group(1)] = paper_text[match.end():end].strip("\n")
    return out


def verify_quote_sourced(quote: str, claimed_source: str, sources_text: dict[str, str],
                         threshold: float = 0.85) -> dict:
    """Verify a quote against the source it claims, per prompt.md step 6.

    Returns {status, source_id, ratio}, where status is one of:
      "verified"      -- found in the claimed source
      "wrong_source"  -- found in a different source (EV-WRONG-SOURCE); the
                         returned source_id is the corrected one
      "unknown_source"-- claimed a source_id that was never supplied (CC-7)
      "unverified"    -- not found anywhere (EV-UNVERIFIED)
    """
    text = str(quote or "")
    if not text.strip():
        return {"status": "unverified", "source_id": claimed_source, "ratio": 0.0}

    known = claimed_source in sources_text
    if known:
        found, ratio = fuzzy_match_quote(text, sources_text[claimed_source], threshold)
        if found:
            return {"status": "verified", "source_id": claimed_source, "ratio": round(ratio, 4)}
    else:
        ratio = 0.0

    best_ratio, best_id = ratio, None
    for source_id, source_text in sources_text.items():
        if source_id == claimed_source:
            continue
        found, other_ratio = fuzzy_match_quote(text, source_text, threshold)
        if found:
            return {
                "status": "unknown_source" if not known else "wrong_source",
                "source_id": source_id,
                "ratio": round(other_ratio, 4),
                "claimed": claimed_source,
            }
        if other_ratio > best_ratio:
            best_ratio, best_id = other_ratio, source_id

    return {"status": "unverified", "source_id": claimed_source,
            "ratio": round(best_ratio, 4), "closest": best_id}
