"""Command line for the selection stage.

    manuscript-select readiness                       # can a "not found" be believed?
    manuscript-select candidates <doi> --json out.json
    manuscript-select pack <doi> --sections methods,data_availability
    manuscript-select sheet --out accession-labels.html
    manuscript-select label --apply accession-labels.json --truth truth/accessions
    manuscript-select verify answers.json --article <doi>
    manuscript-select eval answers/ --truth truth/accessions --baseline

Offline throughout, like the extraction stage: everything here reads what is already
in the corpus. Nothing calls a model, and `eval` is the only subcommand that reads
anything a model wrote.
"""

import argparse
import json
import sys
from pathlib import Path

from ..extract.cli import load_config
from ..fetch import store
from ..fetch.identifiers import doi_slug, normalize_doi
from . import candidates, query, readiness, sheet, truth, verify


def _corpus_dir(args) -> Path:
    config = load_config(args.config)
    chosen = getattr(args, "corpus_dir", None) or config["extract"]["corpus_dir"]
    return Path(chosen).expanduser()


def _resolve(corpus_dir: Path, wanted: str) -> Path:
    """A DOI, a slug, or a path to an article directory. Same rule as `extract`."""
    given = Path(wanted)
    if given.is_dir() and (given / store.MANIFEST_NAME).exists():
        return given
    slug = wanted
    try:
        slug = doi_slug(normalize_doi(wanted))
    except ValueError:
        pass
    directory = corpus_dir / slug
    if not directory.exists():
        raise ValueError(f"no article directory for {wanted!r} (looked in {directory})")
    return directory


def _article_dirs(corpus_dir: Path):
    if not corpus_dir.exists():
        return []
    return sorted(p for p in corpus_dir.iterdir()
                  if p.is_dir() and (p / store.MANIFEST_NAME).exists())


def _sections(value) -> list:
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def _find_in(directory: Path) -> list:
    """The accession candidates of one article, references excluded.

    Excluding `references` is the one filter this aspect always wants: an accession in
    a reference list belongs to the paper being cited. It is applied here rather than
    inside `candidates.find` so the finder stays a finder.
    """
    blocks = query.select(query.load(directory), exclude_sections=["references"])
    return candidates.find(blocks)


def cmd_readiness(args) -> int:
    """Whether a negative answer from each article would mean anything."""
    corpus_dir = _corpus_dir(args)
    directories = ([_resolve(corpus_dir, args.article)] if args.article
                   else _article_dirs(corpus_dir))
    if not directories:
        print(f"{corpus_dir}: no articles with a manifest", file=sys.stderr)
        return 2

    counts: dict = {}
    rows = []
    for directory in directories:
        verdict = readiness.assess(directory)
        counts[verdict["state"]] = counts.get(verdict["state"], 0) + 1
        rows.append((directory.name, verdict))
        if args.quiet and readiness.trustworthy(verdict):
            continue
        gaps = ",".join(verdict.get("gaps") or []) or "-"
        print(f"{directory.name:38s} {verdict['state']:20s} {gaps}", file=sys.stderr)
        for line in verdict.get("why") or []:
            print(f"{'':38s}   ! {line}", file=sys.stderr)

    print("\n" + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())),
          file=sys.stderr)
    trusted = sum(v for k, v in counts.items() if k in readiness.TRUSTWORTHY)
    print(f"{trusted}/{len(directories)} article(s) can carry a negative answer",
          file=sys.stderr)
    if args.json:
        Path(args.json).write_text(json.dumps(
            {slug: verdict for slug, verdict in rows}, indent=2, sort_keys=True) + "\n")
    return 0 if trusted == len(directories) else 1


def cmd_candidates(args) -> int:
    """What the deterministic finder produces, before anything judges it."""
    corpus_dir = _corpus_dir(args)
    directories = ([_resolve(corpus_dir, args.article)] if args.article
                   else _article_dirs(corpus_dir))
    if not directories:
        print(f"{corpus_dir}: no articles with a manifest", file=sys.stderr)
        return 2

    out = {}
    totals = {"study": 0, "sample": 0, "articles": 0, "skipped": 0}
    for directory in directories:
        verdict = readiness.assess(directory)
        if not readiness.trustworthy(verdict) and not args.include_unreadable:
            totals["skipped"] += 1
            print(f"{directory.name:38s} skipped: {verdict['state']}", file=sys.stderr)
            continue
        found = _find_in(directory)
        split = candidates.by_level(found)
        totals["articles"] += 1
        totals["study"] += len(split[candidates.STUDY])
        totals["sample"] += len(split[candidates.SAMPLE])
        out[directory.name] = {
            "doi": verdict.get("doi"), "readiness": verdict["state"],
            "gaps": verdict.get("gaps") or [],
            "study": [c.to_dict() for c in split[candidates.STUDY]],
            "sample": [c.to_dict() for c in split[candidates.SAMPLE]],
        }
        listed = ", ".join(f"{c.accession}[{c.repository}]"
                           for c in split[candidates.STUDY]) or "-"
        print(f"{directory.name:38s} {len(split[candidates.STUDY]):2d} study  "
              f"{len(split[candidates.SAMPLE]):3d} sample  {listed}", file=sys.stderr)

    print(f"\n{totals['study']} study-level and {totals['sample']} sample-level "
          f"candidate(s) across {totals['articles']} article(s); "
          f"{totals['skipped']} skipped as unreadable", file=sys.stderr)
    print("no role is assigned here: `own` vs `reused` is a judgement, and "
          "`naive_own` is what assuming it costs", file=sys.stderr)
    if args.json:
        Path(args.json).write_text(json.dumps(out, indent=2, ensure_ascii=False,
                                              sort_keys=True) + "\n")
    return 0 if totals["articles"] else 2


def cmd_pack(args) -> int:
    """The blocks a question would see, ranked, budgeted, and told what was dropped."""
    corpus_dir = _corpus_dir(args)
    directory = _resolve(corpus_dir, args.article)
    verdict = readiness.assess(directory)
    blocks = query.load(directory)
    if not blocks:
        print(f"no blocks: {verdict['state']}", file=sys.stderr)
        for line in verdict.get("why") or []:
            print(f"  ! {line}", file=sys.stderr)
        return 2

    chosen = query.select(blocks, kinds=_sections(args.kinds) or None,
                          roles=_sections(args.roles) or None,
                          exclude_sections=_sections(args.exclude) or None)
    ranked = query.prefer(chosen, _sections(args.sections) or None)
    packed = query.pack(ranked, budget=args.budget)

    record = {"slug": directory.name, "doi": verdict.get("doi"),
              "readiness": verdict["state"], "gaps": verdict.get("gaps") or [],
              **packed.to_dict()}
    if args.json:
        Path(args.json).write_text(query.dump(record) + "\n")
    else:
        print(query.dump(record))
    print(f"{len(packed.blocks)}/{packed.considered} block(s), {packed.chars} chars"
          + (f"; {packed.dropped} dropped ({packed.dropped_chars} chars) over budget"
             if packed.truncated else ""), file=sys.stderr)
    if not readiness.trustworthy(verdict):
        print(f"! readiness is {verdict['state']}: a 'not found' from this pack "
              f"means nothing", file=sys.stderr)
    return 0


def cmd_sheet(args) -> int:
    """Write the hand-labelling sheet for the whole corpus."""
    corpus_dir = _corpus_dir(args)
    entries = []
    for directory in _article_dirs(corpus_dir):
        verdict = readiness.assess(directory)
        entries.append({"slug": directory.name, "doi": verdict.get("doi"),
                        "verdict": verdict,
                        "candidates": _find_in(directory)
                        if readiness.trustworthy(verdict) else []})
    worth = sheet.articles_worth_labelling(entries)
    if not worth:
        print(f"{corpus_dir}: no article has believable text to label", file=sys.stderr)
        return 2
    if args.limit:
        worth = worth[: args.limit]

    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(sheet.render(worth), encoding="utf-8")
    study = sum(len(candidates.by_level(e["candidates"])[candidates.STUDY])
                for e in worth)
    print(target)
    print(f"{study} candidate(s) across {len(worth)} article(s); "
          f"{len(entries) - len(worth)} article(s) left out as unreadable",
          file=sys.stderr)
    return 0


def cmd_label(args) -> int:
    """Split a downloaded sheet into one truth file per article.

    Refuses to write a label with an unlabelled candidate in it unless `--partial` is
    passed. A half-filled label scores as though the blanks were deliberate `reused`
    calls, which quietly rewards a model for the labeller's unfinished work -- exactly
    the kind of plausible success this repository is arranged against.
    """
    payload = json.loads(Path(args.apply).read_text(encoding="utf-8"))
    out_dir = Path(args.truth)
    out_dir.mkdir(parents=True, exist_ok=True)

    written, skipped, complete = 0, [], 0
    for article in payload.get("articles") or []:
        rows = article.get("accessions") or []
        blanks = [row["accession"] for row in rows if not row.get("role")]
        if blanks and not args.partial:
            skipped.append((article.get("slug"), len(blanks)))
            continue
        record = {
            "slug": article.get("slug"), "doi": article.get("doi"),
            "aspect": "accessions",
            "labeled_by": article.get("labeled_by") or payload.get("labeled_by") or "",
            "complete": bool(article.get("complete")),
            "accessions": [row for row in rows if row.get("role")],
        }
        if article.get("missing"):
            # A finder miss is a pattern bug, and recording it in the label is what
            # turns the labelling pass into a test of `candidates._PATTERNS`.
            record["finder_missed"] = list(article["missing"])
        (out_dir / f"{record['slug']}.json").write_text(truth.dump(record),
                                                        encoding="utf-8")
        written += 1
        complete += 1 if record["complete"] else 0

    for slug, blanks in skipped:
        print(f"{slug}: {blanks} candidate(s) unlabelled -- skipped "
              f"(pass --partial to write it anyway)", file=sys.stderr)
    print(f"{written} label(s) written to {out_dir}; {complete} marked complete "
          f"(only those count toward recall)", file=sys.stderr)
    missed = sum(len(a.get("missing") or []) for a in payload.get("articles") or [])
    if missed:
        print(f"! {missed} accession(s) reported as missed by the finder -- "
              f"a pattern bug worth fixing before measuring anything", file=sys.stderr)
    return 0 if written else 2


def cmd_verify(args) -> int:
    """Check that every quote in an answer is really in the block it cites."""
    corpus_dir = _corpus_dir(args)
    answer = json.loads(Path(args.answers).read_text(encoding="utf-8"))
    article = args.article or answer.get("slug") or answer.get("doi")
    if not article:
        print("no article: pass --article, or give the answer a slug", file=sys.stderr)
        return 2
    directory = _resolve(corpus_dir, article)
    blocks_by_id = query.by_id(query.load(directory))
    if not blocks_by_id:
        print(f"nothing to verify against: {readiness.assess(directory)['state']}",
              file=sys.stderr)
        return 2

    claims = answer.get("accessions") or answer.get("claims") or []
    result = verify.verify_claims(claims, blocks_by_id, search_all=not args.no_search)
    for claim in result["claims"]:
        if claim["verified"]:
            continue
        name = claim.get("accession") or claim.get("id") or "?"
        for checked in claim["evidence_checked"]:
            if checked["verified"]:
                continue
            extra = (f" (found in {checked['found_in']})" if checked.get("found_in")
                     else "")
            print(f"  {name}: {checked['verdict']}{extra}", file=sys.stderr)
    print(f"{result['verified']}/{len(result['claims'])} claim(s) verified; "
          + "  ".join(f"{k}={v}" for k, v in sorted(result["counts"].items())),
          file=sys.stderr)
    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=2, ensure_ascii=False,
                                              sort_keys=True) + "\n")
    return 1 if result["unverified"] else 0


def cmd_eval(args) -> int:
    """Score answers against the labels, and against the no-judgement baseline."""
    labels = truth.read_dir(args.truth)
    if not labels:
        print(f"{args.truth}: no truth files", file=sys.stderr)
        return 2

    if args.baseline:
        corpus_dir = _corpus_dir(args)
        predictions = {}
        for slug in labels:
            directory = corpus_dir / slug
            if directory.exists():
                predictions[slug] = candidates.naive_own(_find_in(directory))
        source = "baseline (every study accession found is a deposit)"
    else:
        answers = Path(args.answers) if args.answers else None
        if not answers or not answers.exists():
            print("pass a directory of answer files, or --baseline", file=sys.stderr)
            return 2
        predictions = {}
        for path in sorted(answers.glob("*.json")):
            record = json.loads(path.read_text(encoding="utf-8"))
            predictions[record.get("slug") or path.stem] = truth.own_set(record)
        source = str(answers)

    result = truth.score(predictions, labels)
    print(f"scoring {source}", file=sys.stderr)
    for row in result["per_article"]:
        mark = "" if row["complete"] else "  (partial: recall not counted)"
        print(f"  {row['slug']:38s} p={_rate(row['precision'])} "
              f"r={_rate(row['recall'])}  "
              f"hit={len(row['hits'])} fp={len(row['false_positives'])} "
              f"miss={len(row['missed'])}{mark}", file=sys.stderr)
    print(f"\n{result['articles']} article(s), {result['articles_partial']} partial "
          f"(excluded from recall)", file=sys.stderr)
    print(f"precision {_rate(result['precision'])}  "
          f"recall {_rate(result['recall'])}  "
          f"({result['hits']} hit / {result['predicted']} predicted / "
          f"{result['truth']} true)", file=sys.stderr)
    confusions = {k: v for k, v in result["confusions"].items() if v}
    if confusions:
        print("confusions: " + "  ".join(f"{k}={v}" for k, v in sorted(
            confusions.items())), file=sys.stderr)
    for slug in result["labels_without_prediction"]:
        print(f"! no prediction for {slug}", file=sys.stderr)
    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=2, ensure_ascii=False,
                                              sort_keys=True) + "\n")
    if args.fail_under is not None and (result["precision"] or 0) < args.fail_under:
        print(f"precision below --fail-under {args.fail_under}", file=sys.stderr)
        return 1
    return 0


def _rate(value) -> str:
    return "  -  " if value is None else f"{value:.3f}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="manuscript-select",
        description="Select evidence from extracted blocks, and measure what was "
                    "made of it.")
    parser.add_argument("--config", default="config.yaml")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(sub):
        sub.add_argument("--corpus-dir", default=None)

    ready = subparsers.add_parser(
        "readiness", help="whether a 'not found' from each article would mean anything")
    ready.add_argument("article", nargs="?", default=None)
    ready.add_argument("--quiet", action="store_true", help="list only the problems")
    ready.add_argument("--json", default=None)
    add_common(ready)
    ready.set_defaults(func=cmd_readiness)

    cands = subparsers.add_parser(
        "candidates", help="accession candidates, with no role assigned")
    cands.add_argument("article", nargs="?", default=None)
    cands.add_argument("--include-unreadable", action="store_true",
                       help="do not skip articles whose text cannot be believed")
    cands.add_argument("--json", default=None)
    add_common(cands)
    cands.set_defaults(func=cmd_candidates)

    packing = subparsers.add_parser("pack", help="the blocks a question would see")
    packing.add_argument("article")
    packing.add_argument("--sections", default=None,
                         help="comma-separated sections to rank first, never a filter")
    packing.add_argument("--exclude", default="references",
                         help="comma-separated sections to drop outright")
    packing.add_argument("--kinds", default=None,
                         help="comma-separated: paragraph, table, heading, ...")
    packing.add_argument("--roles", default=None,
                         help="comma-separated: main_text, supplement, non_evidence")
    packing.add_argument("--budget", type=int, default=query.DEFAULT_BUDGET_CHARS)
    packing.add_argument("--json", default=None)
    add_common(packing)
    packing.set_defaults(func=cmd_pack)

    sheeting = subparsers.add_parser("sheet", help="write the hand-labelling sheet")
    sheeting.add_argument("--out", default="accession-labels.html")
    sheeting.add_argument("--limit", type=int, default=None)
    add_common(sheeting)
    sheeting.set_defaults(func=cmd_sheet)

    labelling = subparsers.add_parser(
        "label", help="split a downloaded sheet into per-article truth files")
    labelling.add_argument("--apply", required=True)
    labelling.add_argument("--truth", default="truth/accessions")
    labelling.add_argument("--partial", action="store_true",
                           help="write labels that still have unlabelled candidates")
    labelling.set_defaults(func=cmd_label)

    verifying = subparsers.add_parser(
        "verify", help="check an answer's quotes against the blocks they cite")
    verifying.add_argument("answers")
    verifying.add_argument("--article", default=None)
    verifying.add_argument("--no-search", action="store_true",
                           help="do not look for a quote in other blocks")
    verifying.add_argument("--json", default=None)
    add_common(verifying)
    verifying.set_defaults(func=cmd_verify)

    evaluating = subparsers.add_parser("eval", help="score answers against the labels")
    evaluating.add_argument("answers", nargs="?", default=None)
    evaluating.add_argument("--truth", default="truth/accessions")
    evaluating.add_argument("--baseline", action="store_true",
                            help="score the no-judgement finder instead of an answer")
    evaluating.add_argument("--fail-under", type=float, default=None)
    evaluating.add_argument("--json", default=None)
    add_common(evaluating)
    evaluating.set_defaults(func=cmd_eval)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
