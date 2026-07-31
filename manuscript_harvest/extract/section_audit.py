"""Measure the PDF section labeller against JATS, which declares its sections.

`sections.SectionTracker` guesses which part of a paper a block of PDF text sits
in, from headings that happen to survive the layout. JATS does not guess: a
publisher's XML says `<sec sec-type="methods">` outright. So for any article the
fetch stage saved *both* renditions of -- and it saves both, choosing only which
one becomes the article's main text -- the XML is a reference answer for the PDF's
candidate answer, and the heuristic can be scored instead of argued about.

That is the whole point of this module. The PDF labeller was measured wrong on two
of seven ground-truth papers (996 of 1,184 blocks of 10.1126/science.adt8307 under
`conclusions`), and the bound that now limits the damage was chosen against a
handful of hand-read runs because nothing could say whether a change helped.
This can.

    python -m manuscript_harvest.extract.section_audit --corpus-dir corpus

Two things it deliberately does not do:

- **It does not score PDF text that JATS has no answer for.** Reference lists are
  the clearest case: `jats.walk_section` drops them as low-value, while the PDF
  path extracts them, so every reference paragraph is unalignable by
  construction. Counting those as errors would measure the difference between two
  extractors rather than the accuracy of one labeller. They are reported as
  `unaligned`.
- **It does not treat JATS as ground truth about the paper**, only as ground truth
  about the labels. The two renditions genuinely differ -- an author manuscript is
  not the typeset article -- so alignment is by content, never by position.
"""

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..fetch import store
from . import jats as jats_mod
from . import pdf as pdf_mod
from .blocks import PARAGRAPH
from .limits import Limits

#: Words per shingle. Alignment is by shared word sequence rather than by
#: similarity score, because the two renditions are the same sentences with
#: different line breaks, hyphenation and figure furniture around them. Eight is
#: long enough that a shingle belongs to one passage -- a shared four-word phrase
#: like "in the aging human" is not evidence of anything -- and short enough that
#: a two-sentence paragraph still yields plenty.
SHINGLE_WORDS = 8

#: A paragraph shorter than one shingle cannot be aligned this way. PDF layout
#: produces a lot of these (axis labels, panel letters), and guessing at them
#: would put noise into a measurement whose job is to be trusted.
_TOO_SHORT = "too_short"


def words(text: str) -> List[str]:
    """Lowercased alphanumeric words. Punctuation and hyphenation are dropped, so
    "perturba-tion" and "perturbation" shingle the same way."""
    out, current = [], []
    for char in text.lower():
        if char.isalnum():
            current.append(char)
        elif current:
            out.append("".join(current))
            current = []
    if current:
        out.append("".join(current))
    return out


def shingles(text: str, size: int = SHINGLE_WORDS) -> List[Tuple[str, ...]]:
    tokens = words(text)
    if len(tokens) < size:
        return []
    return [tuple(tokens[i:i + size]) for i in range(len(tokens) - size + 1)]


def reference_index(blocks, size: int = SHINGLE_WORDS) -> Dict[Tuple[str, ...], str]:
    """shingle -> section, over the JATS paragraphs that carry a section.

    A shingle seen under two different sections is dropped rather than resolved.
    It is boilerplate ("data are available in the supplementary information"), and
    a coin flip in the reference answer is worse than a smaller reference answer.
    """
    seen: Dict[Tuple[str, ...], Optional[str]] = {}
    for block in blocks:
        if block.kind != PARAGRAPH or not block.section:
            continue
        for shingle in shingles(block.text, size):
            if shingle in seen and seen[shingle] != block.section:
                seen[shingle] = None
            else:
                seen.setdefault(shingle, block.section)
    return {k: v for k, v in seen.items() if v is not None}


def reference_for(text: str, index, size: int = SHINGLE_WORDS) -> Optional[str]:
    """The section JATS puts this text in, or None when it cannot be aligned.

    Majority vote over the shingles that matched, because a PDF layout block
    occasionally spans a section boundary -- the last line of Methods and the first
    of Results in one box.
    """
    votes = Counter(index[s] for s in shingles(text, size) if s in index)
    return votes.most_common(1)[0][0] if votes else None


def audit(jats_blocks, pdf_blocks, size: int = SHINGLE_WORDS) -> dict:
    """Score the PDF labels against the JATS ones. Returns a report dict."""
    index = reference_index(jats_blocks, size)
    per_section: Dict[str, Counter] = defaultdict(Counter)
    confusions: Counter = Counter()
    aligned = correct = unaligned = too_short = 0
    unaligned_chars = 0

    for block in pdf_blocks:
        if block.kind != PARAGRAPH:
            continue
        if not shingles(block.text, size):
            too_short += 1
            continue
        reference = reference_for(block.text, index, size)
        if reference is None:
            unaligned += 1
            unaligned_chars += len(block.text)
            continue
        aligned += 1
        candidate = block.section
        if candidate == reference:
            correct += 1
            per_section[reference]["tp"] += 1
        else:
            per_section[reference]["fn"] += 1
            if candidate:
                per_section[candidate]["fp"] += 1
            confusions[(reference, candidate or "(none)")] += 1

    sections = {}
    for name, counts in sorted(per_section.items()):
        tp, fp, fn = counts["tp"], counts["fp"], counts["fn"]
        sections[name] = {
            "support": tp + fn,
            "labelled": tp + fp,
            "precision": round(tp / (tp + fp), 3) if tp + fp else None,
            "recall": round(tp / (tp + fn), 3) if tp + fn else None,
        }
    return {
        "reference_shingles": len(index),
        "aligned": aligned,
        "correct": correct,
        "accuracy": round(correct / aligned, 3) if aligned else None,
        "unaligned": unaligned,
        "unaligned_chars": unaligned_chars,
        "too_short_to_align": too_short,
        "sections": sections,
        "confusions": [
            {"jats_says": ref, "pdf_says": cand, "blocks": n}
            for (ref, cand), n in confusions.most_common()
        ],
    }


def audit_article(directory, limits: Optional[Limits] = None) -> Optional[dict]:
    """Audit one article directory, or None when it has no XML/PDF pair."""
    directory = Path(directory)
    xml_path = directory / store.FULLTEXT_XML
    pdf_path = directory / store.FULLTEXT_PDF
    if not (xml_path.is_file() and pdf_path.is_file()):
        return None
    limits = limits or Limits()

    jats_blocks, jats_status, _ = jats_mod.blocks_from_jats(
        xml_path.read_bytes(), str(xml_path), limits)
    pdf_blocks, pdf_status, _ = pdf_mod.blocks_from_pdf(
        pdf_path.read_bytes(), str(pdf_path), limits)
    if jats_status != "ok" or pdf_status != "ok":
        return {"slug": directory.name, "jats_status": jats_status,
                "pdf_status": pdf_status, "skipped": "one rendition did not parse"}

    report = audit(jats_blocks, pdf_blocks)
    report["slug"] = directory.name
    report["jats_paragraphs"] = sum(1 for b in jats_blocks if b.kind == PARAGRAPH)
    report["pdf_paragraphs"] = sum(1 for b in pdf_blocks if b.kind == PARAGRAPH)
    return report


def format_report(report: dict) -> str:
    if report.get("skipped"):
        return (f"\n{report['slug']}: skipped -- {report['skipped']} "
                f"(jats={report['jats_status']}, pdf={report['pdf_status']})")
    lines = [
        f"\n{report['slug']}",
        f"  paragraphs: {report['pdf_paragraphs']} in the PDF, "
        f"{report['jats_paragraphs']} in the XML",
        f"  aligned {report['aligned']} of them; {report['unaligned']} had no counterpart "
        f"in the XML ({report['unaligned_chars']} chars, mostly references), "
        f"{report['too_short_to_align']} too short to align",
        f"  agreement on aligned paragraphs: {report['correct']}/{report['aligned']}"
        + (f" = {report['accuracy']:.1%}" if report["accuracy"] is not None else ""),
    ]
    if report["sections"]:
        lines.append(f"    {'section':20s} {'support':>7s} {'labelled':>8s} "
                     f"{'precision':>9s} {'recall':>6s}")
        for name, s in report["sections"].items():
            precision = "-" if s["precision"] is None else f"{s['precision']:.2f}"
            recall = "-" if s["recall"] is None else f"{s['recall']:.2f}"
            lines.append(f"    {name:20s} {s['support']:7d} {s['labelled']:8d} "
                         f"{precision:>9s} {recall:>6s}")
    for row in report["confusions"][:8]:
        lines.append(f"    XML says {row['jats_says']:18s} PDF says "
                     f"{row['pdf_says']:18s} {row['blocks']:5d} paragraph(s)")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m manuscript_harvest.extract.section_audit",
        description="Score the PDF section labeller against JATS, which declares its sections",
    )
    parser.add_argument("--corpus-dir", default="corpus")
    parser.add_argument("--article", default=None,
                        help="one slug or directory; default is every article with both renditions")
    parser.add_argument("--json", default=None, help="write the reports here as JSON")
    args = parser.parse_args(argv)

    root = Path(args.corpus_dir).expanduser()
    if args.article:
        candidates = [Path(args.article) if Path(args.article).is_dir() else root / args.article]
    else:
        candidates = sorted(p for p in root.glob("*") if p.is_dir())

    reports = []
    for directory in candidates:
        report = audit_article(directory)
        if report is None:
            continue
        reports.append(report)
        print(format_report(report))

    if not reports:
        print(f"no article in {root} has both {store.FULLTEXT_XML} and "
              f"{store.FULLTEXT_PDF}; fetch an open-access paper to get a pair",
              file=sys.stderr)
        return 2

    scored = [r for r in reports if not r.get("skipped")]
    if scored:
        aligned = sum(r["aligned"] for r in scored)
        correct = sum(r["correct"] for r in scored)
        print(f"\n{len(scored)} article(s): {correct}/{aligned} aligned paragraphs agree"
              + (f" = {correct / aligned:.1%}" if aligned else ""))
    if args.json:
        Path(args.json).write_text(json.dumps(reports, indent=2), encoding="utf-8")
        print(f"wrote {args.json}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
