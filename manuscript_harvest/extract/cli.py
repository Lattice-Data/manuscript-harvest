"""Command line for the extraction stage.

    manuscript-extract one 10.1038/s41467-023-40505-5
    manuscript-extract all --limit 10
    manuscript-extract status
    manuscript-extract show 10.1038/s41467-023-40505-5 --section methods
    manuscript-extract show <doi> --kind table --full
    manuscript-extract table <doi> --file mmc7.xlsx --locator "Table S6"
    manuscript-extract review <doi>                   # writes review-<slug>.html
    manuscript-extract review <doi> --apply answers.json

Everything here is offline: it reads what the fetch stage already put in the
corpus. `all` is safe to re-run -- an article is skipped when nothing in its cache
key has moved. That key is `extractor.extraction_key`, and naming only the version
here would name the one component already proven insufficient: see
`extract/__init__.py`, where a version number nobody remembers to bump is rejected
as a cache key in favour of a hash of the parser source.
"""

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

import yaml

from ..config import merge_config
from ..fetch import store
from ..fetch.identifiers import doi_slug, normalize_doi
from . import extractor, review, reviewsheet, spreadsheet
from .blocks import BLOCKS_NAME, read_blocks
from .limits import Limits

DEFAULT_EXTRACT_CONFIG = {
    "corpus_dir": "corpus",
    "write_markdown": True,
    "limits": Limits().to_dict(),
}


def load_config(path) -> dict:
    raw = {}
    config_path = Path(path)
    if config_path.exists():
        raw = yaml.safe_load(config_path.read_text()) or {}
    config = dict(raw)
    config["extract"] = merge_config(DEFAULT_EXTRACT_CONFIG, raw.get("extract") or {})
    # One corpus, two stages. Unless `extract` names its own directory, follow
    # wherever the fetch stage was told to write, so moving the corpus needs one
    # edit rather than two that can drift apart.
    if "corpus_dir" not in (raw.get("extract") or {}):
        fetch_corpus = (raw.get("fetch") or {}).get("corpus_dir")
        if fetch_corpus:
            config["extract"]["corpus_dir"] = fetch_corpus
    return config


def _settings(args) -> tuple:
    config = load_config(args.config)
    section = config["extract"]
    corpus_dir = getattr(args, "corpus_dir", None) or section["corpus_dir"]
    limits = Limits.from_dict(section.get("limits"))
    if getattr(args, "max_scan_rows", None):
        limits.max_scan_rows = args.max_scan_rows
    return Path(corpus_dir).expanduser(), limits, bool(section.get("write_markdown", True))


def _resolve(corpus_dir: Path, wanted: str) -> Path:
    """Accept a DOI, a slug, or a path to an article directory."""
    candidate = Path(wanted)
    if candidate.is_dir() and (candidate / store.MANIFEST_NAME).exists():
        return candidate
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


def _exit_code(extraction: dict) -> int:
    return {"complete": 0, "partial": 1}.get(extraction.get("status"), 2)


def _labelling(extraction: dict) -> dict:
    return ((extraction.get("main_text") or {}).get("section_labelling")) or {}


def _section_warning(extraction: dict) -> list:
    """The one line worth printing when the main text is barely labelled.

    An article can be `complete` with no methods or results label anywhere in its
    body -- 10.1126/science.aat5031 is -- because `totals.sections` counts the
    supplements too. Nothing said that was abnormal.
    """
    report = _labelling(extraction)
    if report.get("confidence") in {"low", "none"}:
        return [f"section labelling is {report['confidence']}: {report['why']}"]
    return []


def cmd_one(args) -> int:
    corpus_dir, limits, markdown = _settings(args)
    directory = _resolve(corpus_dir, args.article)
    extraction = extractor.extract_article(directory, limits=limits, force=args.force,
                                           write_markdown=markdown,
                                           config=load_config(args.config))
    print(f"{extraction.get('slug')}  {extractor.summarize(extraction)}", file=sys.stderr)
    if extraction.get("cached"):
        print("    (cached; use --force to re-extract)", file=sys.stderr)
    for note in [(extraction.get("main_text") or {}).get("note")]:
        if note:
            print(f"    ! {note}", file=sys.stderr)
    for line in _section_warning(extraction):
        print(f"    ! {line}", file=sys.stderr)
    for path in extraction.get("unextracted_text_files") or []:
        print(f"    ! no text from {path}", file=sys.stderr)
    if args.json:
        Path(args.json).write_text(json.dumps(extraction, indent=2, ensure_ascii=False))
    print(directory / extractor.EXTRACT_DIR)
    return _exit_code(extraction)


def cmd_all(args) -> int:
    config = load_config(args.config)
    corpus_dir, limits, markdown = _settings(args)
    directories = _article_dirs(corpus_dir)
    if not directories:
        print(f"{corpus_dir}: no articles with a manifest", file=sys.stderr)
        return 2
    if args.limit:
        directories = directories[: args.limit]

    by_status: dict = {}
    for directory in directories:
        try:
            extraction = extractor.extract_article(directory, limits=limits,
                                                   force=args.force,
                                                   write_markdown=markdown,
                                                   config=config)
        except Exception as e:
            # One article must not take the rest of the corpus with it. The
            # extractor guards each file; this guards everything above them.
            print(f"{directory.name:38s} crashed: {type(e).__name__}: {e}", file=sys.stderr)
            by_status["crashed"] = by_status.get("crashed", 0) + 1
            continue
        status = extraction.get("status", "?")
        by_status[status] = by_status.get(status, 0) + 1
        marker = " (cached)" if extraction.get("cached") else ""
        print(f"{directory.name:38s} {extractor.summarize(extraction)}{marker}",
              file=sys.stderr)
        for line in _section_warning(extraction):
            print(f"{'':38s}   ! {line}", file=sys.stderr)

    print("\n" + "  ".join(f"{k}={v}" for k, v in sorted(by_status.items())), file=sys.stderr)
    return 0 if by_status.get("complete") == len(directories) else 1


def cmd_status(args) -> int:
    """What has been extracted, and where the text is still missing."""
    corpus_dir, _, _ = _settings(args)
    directories = _article_dirs(corpus_dir)
    if not directories:
        print(f"{corpus_dir}: no articles with a manifest", file=sys.stderr)
        return 2

    totals = {"articles": 0, "extracted": 0, "blocks": 0, "tables": 0, "chars": 0}
    file_statuses: dict = {}
    sources: dict = {}
    labelling: dict = {}
    reviews = {"signed": 0, "queued": 0, "answered": 0, "stale": 0}
    for directory in directories:
        totals["articles"] += 1
        extraction = extractor.read_extraction(directory)
        if extraction is None:
            print(f"{directory.name:38s} not extracted", file=sys.stderr)
            continue
        totals["extracted"] += 1
        article_totals = extraction.get("totals") or {}
        totals["blocks"] += article_totals.get("blocks", 0)
        totals["tables"] += article_totals.get("tables", 0)
        totals["chars"] += article_totals.get("chars", 0)
        source = (extraction.get("main_text") or {}).get("source")
        sources[source] = sources.get(source, 0) + 1
        for status, count in (extraction.get("supplementary_by_status") or {}).items():
            file_statuses[status] = file_statuses.get(status, 0) + count
        sect = _labelling(extraction).get("confidence", "?")
        labelling[sect] = labelling.get(sect, 0) + 1
        reviewed = extraction.get("review") or {}
        reviews["signed"] += 1 if reviewed.get("sign_off") else 0
        reviews["queued"] += reviewed.get("queued") or 0
        reviews["answered"] += reviewed.get("answered") or 0
        reviews["stale"] += len(reviewed.get("stale") or [])
        if getattr(args, "needs_review", False) \
                and reviewed.get("state") not in {"queued", "stale"}:
            continue
        if not args.quiet:
            print(f"{directory.name:38s} {extractor.summarize(extraction)} sect={sect}",
                  file=sys.stderr)

    print(f"\n{totals['extracted']}/{totals['articles']} articles extracted: "
          f"{totals['blocks']} blocks, {totals['tables']} tables, "
          f"{totals['chars']} characters", file=sys.stderr)
    print("main text source: " + "  ".join(f"{k}={v}" for k, v in sorted(
        sources.items(), key=lambda kv: str(kv[0]))), file=sys.stderr)
    print("supplementary files: " + "  ".join(f"{k}={v}" for k, v in sorted(
        file_statuses.items())), file=sys.stderr)
    print("main-text section labelling: " + "  ".join(f"{k}={v}" for k, v in sorted(
        labelling.items())), file=sys.stderr)
    print(f"review: {reviews['signed']}/{totals['extracted']} signed off; "
          f"{reviews['queued']} items queued, {reviews['answered']} answered, "
          f"{reviews['stale']} stale", file=sys.stderr)
    return 0


def cmd_show(args) -> int:
    """Print blocks, filtered. The quickest way to see what a question would see."""
    corpus_dir, _, _ = _settings(args)
    directory = _resolve(corpus_dir, args.article)
    path = directory / extractor.EXTRACT_DIR / BLOCKS_NAME
    if not path.exists():
        print(f"not extracted yet: {path}", file=sys.stderr)
        return 2

    shown = 0
    for block in read_blocks(path):
        if args.kind and block["kind"] != args.kind:
            continue
        if args.section and block.get("section") != args.section:
            continue
        if args.role and block.get("role") != args.role:
            continue
        if args.file and args.file not in block.get("source_file", ""):
            continue
        shown += 1
        if args.limit and shown > args.limit:
            break
        header = (f"[{block['index']}] {block['kind']} "
                  f"{block.get('section') or '-'} "
                  f"{block['source_file']} {block.get('locator') or ''}")
        print(header)
        text = block["text"]
        if not args.full and len(text) > 600:
            text = text[:600] + f" ... (+{len(text) - 600} chars)"
        print(text)
        print()
    if shown == 0:
        print("no blocks matched", file=sys.stderr)
    return 0


def cmd_table(args) -> int:
    """Re-read and print the real rows a table card describes.

    This is the command that makes `data_ref` a contract: the card says which
    file, which sheet and which rows it was built from, and this re-opens the
    source at that offset so a curator can check the card against the bytes.
    """
    corpus_dir, limits, _ = _settings(args)
    directory = _resolve(corpus_dir, args.article)
    path = directory / extractor.EXTRACT_DIR / BLOCKS_NAME
    if not path.exists():
        print(f"not extracted yet: {path}", file=sys.stderr)
        return 2

    matches = [b for b in read_blocks(path)
               if b.get("table")
               and (not args.file or args.file in b.get("source_file", ""))
               and (not args.locator or args.locator in (b.get("locator") or ""))]
    if not matches:
        print("no table card matched; try `show <article> --kind table`", file=sys.stderr)
        return 2
    if len(matches) > 1 and not args.all:
        for block in matches[:20]:
            print(f"  {block['source_file']}  {block.get('locator')}", file=sys.stderr)
        print(f"{len(matches)} cards matched; narrow with --file/--locator or pass --all",
              file=sys.stderr)
        return 2

    failures = 0
    for block in matches:
        card = block["table"]
        data_ref = card.get("data_ref") or {}
        source = directory / (data_ref.get("file") or block["source_file"])
        print(f"\n{block['source_file']}  {block.get('locator')}")
        if not source.exists():
            print("  source file is not on disk", file=sys.stderr)
            failures += 1
            continue
        data = source.read_bytes()
        recorded = data_ref.get("sha256")
        if recorded and hashlib.sha256(data).hexdigest() != recorded:
            print("  ! the source file has changed since this card was built",
                  file=sys.stderr)
        header, rows = spreadsheet.read_rows(
            data, data_ref, Path(source).suffix.lower(), limit=args.rows)
        if not rows:
            print("  this card records no re-readable row range", file=sys.stderr)
            failures += 1
            continue
        if header:
            print(f"  header (row {data_ref.get('header_row')}): "
                  + " | ".join(header))
        for number, cells in rows:
            print(f"  {number:>7}: " + " | ".join(cells))
        remaining = (data_ref.get("last_data_row") or 0) - (rows[-1][0] if rows else 0)
        if remaining > 0:
            print(f"  ... {remaining} further row(s); pass --rows to see more")
    return 1 if failures else 0


def cmd_review(args) -> int:
    """Write the review sheet for an article, or apply the answers that came back.

    Exit 0 when there is nothing queued and 1 when there is: a review sheet with
    questions on it is a job that has not been done yet, and a CI run should be
    able to say so.
    """
    config = load_config(args.config)
    corpus_dir, limits, markdown = _settings(args)
    directory = _resolve(corpus_dir, args.article)
    extraction = extractor.read_extraction(directory)
    if extraction is None:
        print(f"not extracted yet: {directory}", file=sys.stderr)
        return 2
    manifest = store.read_manifest(directory)
    slug = extraction.get("slug") or directory.name
    stored_path = review.review_path(slug, config)
    blocks_file = directory / extractor.EXTRACT_DIR / BLOCKS_NAME
    queue = review.queue_for(extraction, blocks_file, limits, manifest)

    if args.apply:
        return _apply_review(args, config, directory, extraction, manifest,
                             stored_path, limits, markdown)

    _, stale = review.state_of(review.read_review(stored_path), extraction,
                               manifest, queue)
    out_dir = Path(args.out or ".")
    out_dir.mkdir(parents=True, exist_ok=True)
    sheet = out_dir / f"review-{slug}.html"
    sheet.write_text(
        reviewsheet.render(extraction, queue, review.read_review(stored_path),
                           article_dir=directory, stale=stale),
        encoding="utf-8")
    print(sheet)
    queued = len([i for i in queue if i["kind"] != review.SIGN_OFF])
    print(f"{queued} question(s) queued for {slug}", file=sys.stderr)
    return 1 if queued else 0


def _apply_review(args, config, directory, extraction, manifest, stored_path,
                  limits, markdown) -> int:
    """Merge answers into `reviews/<slug>.json` and re-extract with them applied."""
    incoming = json.loads(Path(args.apply).read_text(encoding="utf-8"))
    stored = review.read_review(stored_path) or review.empty_review(extraction)
    # Append-only: the file is an audit log, and the last non-stale answer for a
    # key wins. Rewriting would lose the record of what was believed before.
    stored["answers"] = (stored.get("answers") or []) + (incoming.get("answers") or [])
    if incoming.get("sign_off"):
        if stored.get("sign_off"):
            stored["previous_sign_off"] = stored["sign_off"]
        stored["sign_off"] = incoming["sign_off"]
        stored["signed_manifest_sha256"] = incoming.get("signed_manifest_sha256")
    stored_path.parent.mkdir(parents=True, exist_ok=True)
    stored_path.write_text(json.dumps(stored, indent=2, ensure_ascii=False) + "\n",
                           encoding="utf-8")

    after = extractor.extract_article(directory, limits=limits, force=True,
                                      write_markdown=markdown, config=config)
    # Both numbers come from the extraction that just ran, so the breakdown sums to
    # the headline. Built from the incoming batch instead, it measured a different
    # set: this file is append-only, so a second apply against an article with
    # fourteen stored answers printed "14 override(s) applied: 1 table header".
    review_record = after.get("review") or {}
    applied = review_record.get("overrides_applied", 0)
    kinds = Counter(review_record.get("overrides_applied_kinds") or {})
    detail = ", ".join(f"{n} {kind.replace('_', ' ')}" for kind, n in
                       sorted(kinds.items())) or "none"
    submitted = sum(1 for a in incoming.get("answers") or [] if a.get("override"))
    print(f"{applied} override(s) applied: {detail} "
          f"({submitted} submitted in this batch)", file=sys.stderr)
    print(f"{stored_path}")
    return _exit_code(after)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="manuscript-extract",
        description="Extract text and table cards from fetched articles.",
    )
    parser.add_argument("--config", default="config.yaml")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(sub):
        sub.add_argument("--corpus-dir", default=None)

    one = subparsers.add_parser("one", help="extract a single article")
    one.add_argument("article", help="DOI, slug, or path to the article directory")
    one.add_argument("--force", action="store_true", help="re-extract even if unchanged")
    one.add_argument("--json", default=None, help="also write the extraction record here")
    one.add_argument("--max-scan-rows", type=int, default=None)
    add_common(one)
    one.set_defaults(func=cmd_one)

    every = subparsers.add_parser("all", help="extract every article in the corpus")
    every.add_argument("--force", action="store_true")
    every.add_argument("--limit", type=int, default=None)
    every.add_argument("--max-scan-rows", type=int, default=None)
    add_common(every)
    every.set_defaults(func=cmd_all)

    status = subparsers.add_parser("status", help="report extraction coverage")
    status.add_argument("--quiet", action="store_true", help="totals only")
    status.add_argument("--needs-review", action="store_true",
                        help="list only articles whose review is queued or stale")
    add_common(status)
    status.set_defaults(func=cmd_status)

    show = subparsers.add_parser("show", help="print extracted blocks")
    show.add_argument("article")
    show.add_argument("--kind", default=None, help="heading, paragraph, caption, table, metadata")
    show.add_argument("--section", default=None, help="methods, results, abstract, ...")
    show.add_argument("--role", default=None, help="main_text or supplement")
    show.add_argument("--file", default=None, help="only blocks whose source path contains this")
    show.add_argument("--limit", type=int, default=20)
    show.add_argument("--full", action="store_true", help="do not truncate block text")
    add_common(show)
    show.set_defaults(func=cmd_show)

    table = subparsers.add_parser(
        "table", help="reprint the real rows a table card was built from")
    table.add_argument("article")
    table.add_argument("--file", default=None,
                       help="only cards whose source path contains this")
    table.add_argument("--locator", default=None,
                       help="only cards whose locator contains this, e.g. \"sheet 'S1'\"")
    table.add_argument("--rows", type=int, default=20)
    table.add_argument("--all", action="store_true",
                       help="print every matching card instead of requiring one")
    add_common(table)
    table.set_defaults(func=cmd_table)

    reviewing = subparsers.add_parser(
        "review", help="write a review sheet, or apply the answers that came back")
    reviewing.add_argument("article")
    reviewing.add_argument("--out", default=None, help="where to write the HTML sheet")
    reviewing.add_argument("--apply", default=None,
                           help="a downloaded answers JSON to merge and re-extract with")
    add_common(reviewing)
    reviewing.set_defaults(func=cmd_review)

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
