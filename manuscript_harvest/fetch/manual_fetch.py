"""Compare a fetch against manually downloaded ground truth.

`manual_fetch/manual_fetch.yaml` records what a human found on the publisher's site for a handful
of DOIs: the article PDF, every supplement, and the hashes of both. The bytes stay
out of git -- they are the publisher's, exactly as with `corpus/` -- so the claims
are checked in and reviewable while the files are pointed at by
`MANUSCRIPT_HARVEST_MANUAL_DIR`.

The comparison is deliberately asymmetric, because the two kinds of artifact are
not equally stable:

    article PDF   compared on page count and identity, never on bytes. Publishers
                  stamp per-download watermarks and embed creation timestamps and
                  document IDs, so two correct fetches of one paper differ. Byte
                  equality here would fail constantly while meaning nothing. Page
                  count is asserted only when both copies are the same rendition:
                  see `rendition_of`.

    supplements   compared on content hash, because these are static assets served
                  identically to everyone. Matched as a *set*: browsers rename
                  downloads, and `store.supplement_filename` prefixes retrieval
                  order, so filenames never line up.

Archives are normalised in both directions. Science ships
`science.adt8307_tables_s1_to_s28.zip`; a tier that unpacks it and a human who
saved it whole both have the same 28 tables, so every hash reachable through an
archive counts alongside the archive's own.

The check that justifies the whole exercise is `supplementary_status`. No
synthetic fixture can catch a *silent* false negative -- an article that really
has supplements, that fetch comes away from with none, reported as `none_listed`
rather than `expected_but_missing`. Only ground truth knows the difference.
"""

import argparse
import hashlib
import os
import re
import sys
import tarfile
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

from . import store
from .identifiers import doi_slug, normalize_doi

MANUAL_DIR_ENV = "MANUSCRIPT_HARVEST_MANUAL_DIR"
DEFAULT_MANUAL_DIR = "manual_fetch"
SPEC_NAME = "manual_fetch.yaml"

# Statuses that mean the PDF is on disk and usable. Kept in step with
# `store.PDF_USABLE`, which is the definition; duplicated rather than imported so
# that this verification harness reads on its own, without the fetch stage's
# internals. If that pair ever changes, it changes in `store` first.
PDF_SUCCESS = {"ok", "scanned_pdf_suspected"}

# Elsevier names every supplementary component mmc<N>, and the article PDF is
# never one of them. Without this rule the Cell Genomics mmc12.pdf -- a 59-page
# extended version of the article that opens with the word "Article" -- reads like
# main text.
_ELSEVIER_SUPPLEMENT = re.compile(r"^mmc\d+$", re.IGNORECASE)

# Elsevier names the article PDF after the PII rather than the DOI, so the
# stem-equals-DOI rule cannot find it: 10.1016/j.xgen.2026.101304 arrives as
# 1-s2.0-S2666979X26001667-main.pdf. Matching the house style beats making every
# Elsevier paper carry a hand-written override.
_ELSEVIER_ARTICLE = re.compile(r"^1-s2\.0-\S+-main$", re.IGNORECASE)

# Cell Press, downloading from cell.com rather than ScienceDirect, names it
# `PII<PII>.pdf`: 10.1016/j.cell.2021.04.038 arrives as PIIS0092867421005730.pdf
# and 10.1016/j.ccell.2021.03.007 as PIIS1535610821001653.pdf. Both are the
# correct typeset article (35 and 23 pages), and neither the DOI rule nor the
# `1-s2.0-` form matches them.
#
# Left unmatched this gap is *silent*, which is why it is worth a rule of its own:
# a folder with no recognised article PDF gets `main_pdf: null`, and `compare`
# then collapses pdf_present, pdf_pages and pdf_identity into one unasserted note
# -- the article PDF simply stops being checked. Anchored on `PIIS` and the
# characters a PII can contain (cell.com uses both S0092867421005730 and the
# punctuated S0092-8674(21)00573-0) so it cannot claim an ordinary filename.
_CELLPRESS_ARTICLE = re.compile(r"^PIIS[0-9X()\-]{10,}$", re.IGNORECASE)

# A folder downloaded from PMC rather than from the publisher: the author
# manuscript is `nihms-<id>.pdf` and its supplements are
# `NIHMS<id>-supplement-<name>`. 10.1016/j.cell.2025.05.027 arrives as
# nihms-2117886.pdf, which matches neither the DOI rule nor either Elsevier form --
# the third naming convention to defeat them, and silent in the same way, because a
# folder with no recognised article PDF stops having its PDF checked at all. The
# hyphen after `nihms` is load-bearing: it is what the supplements do not have.
_NIHMS_ARTICLE = re.compile(r"^nihms-\d+$", re.IGNORECASE)

# Only *container* archives are expanded -- a bundle a tier might legitimately
# unpack into separate supplements. An .xlsx is a zip too, as is every Office and
# OpenDocument format, so trusting `is_zipfile` alone recorded spreadsheet
# internals as supplement members: 25 of them for one Nature workbook. That is not
# merely noise. Parts like `[Content_Types].xml` are byte-identical across
# unrelated workbooks, so member-wise matching would report a manual file as found on
# the strength of boilerplate shared with a different one. An allowlist and not a
# denylist, because new zip-based document formats keep arriving.
_ARCHIVE_SUFFIXES = {".zip", ".tar", ".tgz", ".gz", ".bz2", ".xz"}

# How many leading pages to read when confirming a PDF is the right paper. The
# DOI appears in the header or the footer of the first page or two; reading the
# whole document to find it would mean parsing 88-page peer review files.
_IDENTITY_PAGES = 2

# Which rendition of the article a copy is. A page count only means something
# between two copies of the *same* rendition, and there are three cases:
#
#   published          the publisher's typeset article
#   author manuscript  the PMC/NIHMS deposit. Different pagination for the same
#                      paper: 49 pages for 10.1016/j.cell.2025.05.027 against a
#                      typeset article a third shorter, and 19 against 7 for
#                      10.1126/science.aat5031
#   anything else      a rendition a fetch cannot return, so never comparable.
#                      Cell Genomics ships an extended article as mmc12.pdf, 59
#                      pages against a 37-page typeset version, and both are the
#                      same paper
PUBLISHED = "published"
AUTHOR_MANUSCRIPT = "author manuscript"

#: Renditions a fetch can actually come back with, so a page count is comparable
#: when the manual copy and the fetched file are the same one.
COMPARABLE_VERSIONS = {PUBLISHED, AUTHOR_MANUSCRIPT}

# A PMC deposit says what it is on its first page, so neither copy has to be
# described by hand: `bootstrap` reads the rendition off the manual file and
# `compare` reads it off the fetched one. `author manuscript` alone would be too
# loose -- a paper *about* manuscripts could say it -- so each marker is a phrase
# only the PMC/Europe PMC cover sheet uses.
_AUTHOR_MANUSCRIPT_MARKERS = (
    "published in final edited form as",
    "hhs public access",
    "europe pmc funders author manuscripts",
)


def rendition_of(head_text: str) -> str:
    """Which rendition this PDF's opening pages say it is.

    `PUBLISHED` is the answer when nothing says otherwise, because the publisher's
    typeset article is the one that carries no cover sheet announcing itself.
    """
    lowered = head_text.lower()
    if any(marker in lowered for marker in _AUTHOR_MANUSCRIPT_MARKERS):
        return AUTHOR_MANUSCRIPT
    return PUBLISHED


# -- reading files -----------------------------------------------------------

def sha256_file(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def archive_members(path) -> List[Tuple[str, str]]:
    """(name, sha256) for every file inside an archive; empty for anything else.

    Never raises. A supplement that merely looks like an archive -- a `.gz` that
    is really a compressed FASTA, a truncated download -- has to degrade to "no
    members" rather than break the comparison it exists to inform.
    """
    path = Path(path)
    members: List[Tuple[str, str]] = []
    if path.suffix.lower() not in _ARCHIVE_SUFFIXES:
        return members
    try:
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as archive:
                for info in archive.infolist():
                    if info.is_dir():
                        continue
                    with archive.open(info) as handle:
                        members.append((info.filename, hashlib.sha256(handle.read()).hexdigest()))
            return members
        if tarfile.is_tarfile(path):
            with tarfile.open(path) as archive:
                for info in archive.getmembers():
                    if not info.isfile():
                        continue
                    handle = archive.extractfile(info)
                    if handle is None:
                        continue
                    members.append((info.name, hashlib.sha256(handle.read()).hexdigest()))
            return members
    except (OSError, zipfile.BadZipFile, tarfile.TarError, EOFError, ValueError):
        return []
    return members


def pdf_pages(path) -> Optional[int]:
    """Page count, or None when the file will not open as a PDF."""
    try:
        import fitz
    except ImportError:  # pragma: no cover - pymupdf is a hard dependency
        return None
    try:
        with fitz.open(path) as document:
            return document.page_count
    except Exception:
        return None


def _page_text(path, indices) -> str:
    try:
        import fitz
    except ImportError:  # pragma: no cover - pymupdf is a hard dependency
        return ""
    try:
        with fitz.open(path) as document:
            # Deduplicated, because head and tail overlap in a short document and
            # reading a page twice would double its text for no gain.
            wanted = sorted(set(indices(document.page_count)))
            chunks = [document[i].get_text() or "" for i in wanted]
    except Exception:
        return ""
    return " ".join(" ".join(chunks).split())


def pdf_head_text(path, pages: int = _IDENTITY_PAGES) -> str:
    return _page_text(path, lambda count: range(min(pages, count)))


def pdf_tail_text(path, pages: int = _IDENTITY_PAGES) -> str:
    """The closing pages, which is where AAAS prints the DOI.

    Reading only the front misses Science and Science Immunology entirely. Measured
    on the hand-fetched copies: the DOI is on pages 6-7 of 7 for
    10.1126/science.aat5031 and 15-16 of 16 for 10.1126/sciimmunol.aba4163, and
    page 1 carries only the running citation ("Sci. Immunol. 5, eaba4163"), which
    normalises apart from the DOI. So `pdf_identity` was reporting "wrong paper, or
    a stub" for the hand-fetched files that define correctness.

    Both ends are needed rather than the other one: PMC's author-manuscript
    rendition puts the DOI on page 1 and nowhere near the end.
    """
    return _page_text(path, lambda count: range(max(0, count - pages), count))


def fingerprint(path) -> dict:
    """Everything the comparison can use about one file on disk."""
    path = Path(path)
    entry = {
        "file": path.name,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }
    if path.suffix.lower() == ".pdf":
        pages = pdf_pages(path)
        if pages is not None:
            entry["pages"] = pages
    members = archive_members(path)
    if members:
        entry["members"] = [{"name": name, "sha256": digest} for name, digest in members]
    return entry


def hash_universe(paths) -> Dict[str, str]:
    """sha256 -> label, for these files and everything inside their archives.

    Both directions of the archive question then reduce to a set lookup: a tier
    that unpacked a zip contributes the members, a human who saved it whole
    contributes the members too, and either one matches the other.
    """
    universe: Dict[str, str] = {}
    for path in paths:
        path = Path(path)
        if not path.is_file():
            continue
        universe.setdefault(sha256_file(path), path.name)
        for name, digest in archive_members(path):
            universe.setdefault(digest, f"{path.name}!{name}")
    return universe


# -- classifying a folder of manual downloads --------------------------------

def doi_tail(doi: str) -> str:
    """The distinctive half of a DOI, normalised for filename comparison."""
    tail = normalize_doi(doi).split("/", 1)[-1]
    return re.sub(r"[^a-z0-9]", "", tail.lower())


def _stem_key(path: Path) -> str:
    return re.sub(r"[^a-z0-9]", "", path.stem.lower())


def classify(directory, doi: str, main_hint: Optional[str] = None) -> Tuple[Optional[Path], List[Path]]:
    """Split a folder of manual downloads into the article PDF and its supplements.

    The article is the file whose stem *equals* the DOI's tail. Equality and not
    containment: `science.adt8307_sm.pdf` contains `science.adt8307` and is the
    supplementary materials, not the article.

    `main_hint` overrides the rule by filename, because the rule cannot always be
    right. Cell Genomics publishes the article as `mmc12.pdf` -- an Elsevier
    supplementary component by naming convention, the main text in fact -- and no
    heuristic reading filenames can know that. The spec is hand-reviewed, so the
    override lives in the spec rather than in a cleverer regex.

    A None main PDF is a finding, not an error: a folder with no article PDF should
    say so rather than have one of its supplements quietly promoted.
    """
    files = sorted(p for p in Path(directory).iterdir() if p.is_file() and not p.name.startswith("."))

    if main_hint:
        main = next((p for p in files if p.name == main_hint), None)
        if main is None:
            raise FileNotFoundError(f"{main_hint} not in {directory}")
        return main, [p for p in files if p != main]

    tail = doi_tail(doi)
    main = None
    for path in files:
        if _ELSEVIER_SUPPLEMENT.match(path.stem):
            continue
        if (_ELSEVIER_ARTICLE.match(path.stem) or _CELLPRESS_ARTICLE.match(path.stem)
                or _NIHMS_ARTICLE.match(path.stem) or _stem_key(path) == tail):
            main = path
            break

    return main, [p for p in files if p != main]


# -- the spec ----------------------------------------------------------------

def manual_root(explicit=None) -> Path:
    """Where the publisher bytes live.

    Deliberately absent from the checked-in spec: it is a per-machine path, and
    baking one developer's home directory into a reviewed file would be wrong.
    """
    if explicit:
        return Path(explicit).expanduser()
    return Path(os.environ.get(MANUAL_DIR_ENV) or DEFAULT_MANUAL_DIR).expanduser()


def spec_path() -> Path:
    """The spec travels with the repo, not with the bytes."""
    return Path(DEFAULT_MANUAL_DIR) / SPEC_NAME


def load_spec(path=None) -> dict:
    target = Path(path) if path else spec_path()
    if not target.exists():
        return {"articles": []}
    loaded = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    loaded.setdefault("articles", [])
    return loaded


def build_article(doi: str, directory, source_dir: str, main_hint: Optional[str] = None,
                  main_version: Optional[str] = None) -> dict:
    """Fingerprint one folder of downloads into a spec entry.

    `main_version` defaults to whatever the file says it is, rather than to
    `PUBLISHED`. A folder downloaded from PMC holds the author manuscript, and a
    spec that called it the published version would quietly stop comparing page
    counts -- 49pp against 49pp went unasserted for 10.1016/j.cell.2025.05.027 for
    exactly that reason. Pass a version explicitly for a rendition no file
    announces, such as Cell Genomics' extended article.

    Written by `bootstrap` and then hand-reviewed -- the point of checking the spec
    in is that a human vouches for it, so the generated `expect` block is a
    proposal rather than an answer.
    """
    main, supplements = classify(directory, doi, main_hint)
    entry = {
        "doi": normalize_doi(doi),
        "slug": doi_slug(doi),
        "source_dir": source_dir,
        "main_pdf": fingerprint(main) if main else None,
        "supplements": [fingerprint(p) for p in supplements],
    }
    if main is not None:
        entry["main_pdf"]["version"] = main_version or rendition_of(pdf_head_text(main))
    else:
        entry["note"] = (
            "no file matched the DOI, so the publisher's article PDF is absent from "
            "this folder; pdf checks are reported but not asserted"
        )
    return entry


# -- comparison --------------------------------------------------------------

def _check(name: str, ok: Optional[bool], detail: str) -> dict:
    """ok=True passed, False failed, None means reported but not asserted."""
    return {"check": name, "ok": ok, "detail": detail}


def fetched_supplement_paths(directory) -> List[Path]:
    directory = Path(directory)
    found = []
    for subdir in (store.SUPPLEMENT_DIR, store.MEDIA_DIR):
        target = directory / subdir
        if target.is_dir():
            found.extend(sorted(p for p in target.rglob("*") if p.is_file()))
    return found


def compare(article: dict, record: dict, directory, root=None) -> List[dict]:
    """Check one fetched article against its entry in the spec.

    Returns a list of checks rather than raising, so a caller can print all of
    them at once. A fetch that missed one supplement out of twelve and a fetch
    that got nothing are both failures, and telling them apart is the point.
    """
    root = manual_root(root)
    directory = Path(directory)
    checks: List[dict] = []

    source = root / article["source_dir"]
    if not source.is_dir():
        return [_check("manual_files_present", None, f"{source} not on this machine")]

    # -- the PDF -------------------------------------------------------------
    fulltext = record.get("fulltext") or {}
    pdf_status = fulltext.get("status", "missing")
    got_pdf = pdf_status in PDF_SUCCESS
    manual_pdf = article.get("main_pdf")

    if manual_pdf is None:
        # Nothing to compare against, but staying silent would hide the more
        # interesting half: fetch may well have found the PDF the human missed.
        checks.append(_check(
            "pdf_present", None,
            f"no manually fetched article PDF to compare; fetch reported {pdf_status}"
            + (" (fetch found one the manual copy lacks)" if got_pdf else ""),
        ))
    else:
        checks.append(_check("pdf_present", got_pdf, f"fetch reported {pdf_status}"))
        fetched_pdf = directory / (fulltext.get("path") or store.FULLTEXT_PDF)
        if got_pdf and fetched_pdf.is_file():
            head = pdf_head_text(fetched_pdf)
            fetched_version = rendition_of(head)
            want = manual_pdf.get("pages")
            have = pdf_pages(fetched_pdf)
            version = manual_pdf.get("version", PUBLISHED)
            if want is not None and have is not None:
                # Compared rendition to rendition. Two author manuscripts are as
                # comparable as two typeset articles; it is the mismatch that makes
                # a page count meaningless.
                comparable = version in COMPARABLE_VERSIONS and version == fetched_version
                if version not in COMPARABLE_VERSIONS:
                    why = f" -- not asserted, the manual copy is the {version} version"
                elif not comparable:
                    why = (f" -- not asserted, the manual copy is the {version} and fetch "
                           f"returned the {fetched_version}")
                else:
                    why = f" ({version})"
                checks.append(_check(
                    "pdf_pages", (want == have) if comparable else None,
                    f"manual {want}pp, fetched {have}pp" + why,
                ))
            # Both ends of the document: the DOI is on page 1 of a PMC rendition and
            # in the closing citation block of an AAAS reprint. See `pdf_tail_text`.
            tail = doi_tail(article["doi"])
            text = re.sub(r"[^a-z0-9]", "",
                          (head + " " + pdf_tail_text(fetched_pdf)).lower())
            checks.append(_check(
                "pdf_identity", tail in text,
                "DOI found in the opening or closing pages" if tail in text
                else "DOI absent from the opening and closing pages -- wrong paper, or a stub",
            ))

    # -- the supplement question --------------------------------------------
    expected_status = (article.get("expect") or {}).get("supplementary_status")
    actual_status = record.get("supplementary_status")
    if expected_status:
        checks.append(_check(
            "supplementary_status", expected_status == actual_status,
            f"expected {expected_status}, got {actual_status}",
        ))

    spec_supplements = article.get("supplements") or []
    universe = hash_universe(fetched_supplement_paths(directory))

    missing = [entry for entry in spec_supplements if not _found(entry, universe)]
    checks.append(_check(
        "supplements_matched", not missing,
        f"{len(spec_supplements) - len(missing)}/{len(spec_supplements)} manual files accounted for"
        + (f"; missing {', '.join(e['file'] for e in missing)}" if missing else ""),
    ))

    # Extra files are reported, never failed. Fetch legitimately collects things a
    # human skipped -- reporting summaries, peer review files, the landing page's
    # own media -- and calling that a regression would punish the better result.
    spec_digest_set = _spec_digests(spec_supplements)
    extra = sorted({label for digest, label in universe.items() if digest not in spec_digest_set})
    if extra:
        checks.append(_check(
            "supplements_extra", None,
            f"{len(extra)} fetched file(s) not fetched by hand: {', '.join(extra[:6])}"
            + (" ..." if len(extra) > 6 else ""),
        ))

    return checks


def _found(entry: dict, universe: Dict[str, str]) -> bool:
    """Is this manually fetched supplement present in what fetch got, archives either way?"""
    if entry.get("sha256") in universe:
        return True
    members = entry.get("members") or []
    return bool(members) and all(m["sha256"] in universe for m in members)


def _spec_digests(entries: List[dict]) -> set:
    digests = set()
    for entry in entries:
        if entry.get("sha256"):
            digests.add(entry["sha256"])
        for member in entry.get("members") or []:
            digests.add(member["sha256"])
    return digests


def failures(checks: List[dict]) -> List[dict]:
    return [c for c in checks if c["ok"] is False]


def format_checks(label: str, checks: List[dict]) -> str:
    marks = {True: "pass", False: "FAIL", None: "note"}
    lines = [label]
    for check in checks:
        lines.append(f"  [{marks[check['ok']]}] {check['check']}: {check['detail']}")
    return "\n".join(lines)


# -- command line ------------------------------------------------------------

def _bootstrap(args) -> int:
    """Fingerprint a folder of manual downloads into a draft spec."""
    root = manual_root(args.root)
    if not root.is_dir():
        print(f"no such directory: {root}", file=sys.stderr)
        return 2

    # normalize_doi raises on a typo, and a traceback is the wrong answer for a
    # mistyped argument -- the same reason fetch_publication returns a failed
    # manifest instead of propagating.
    pairs = []
    for item in args.doi:
        doi, _, source_dir = item.partition("=")
        if not source_dir:
            print(f"expected DOI=SUBDIR, got {item!r}", file=sys.stderr)
            return 2
        try:
            pairs.append((normalize_doi(doi), source_dir))
        except ValueError as error:
            print(str(error), file=sys.stderr)
            return 2

    # DOI=FILE[@VERSION], so a hand-known article PDF survives regenerating the spec.
    hints = {}
    for item in args.main or []:
        doi, _, rest = item.partition("=")
        filename, _, version = rest.partition("@")
        if not filename:
            print(f"expected DOI=FILE[@VERSION], got {item!r}", file=sys.stderr)
            return 2
        try:
            hints[normalize_doi(doi)] = (filename, version or None)
        except ValueError as error:
            print(str(error), file=sys.stderr)
            return 2

    articles = []
    for doi, source_dir in pairs:
        directory = root / source_dir
        if not directory.is_dir():
            print(f"no such directory: {directory}", file=sys.stderr)
            return 2
        # No version means "read it off the file"; see build_article.
        filename, version = hints.get(doi, (None, None))
        try:
            entry = build_article(doi, directory, source_dir,
                                  main_hint=filename, main_version=version)
        except FileNotFoundError as error:
            print(str(error), file=sys.stderr)
            return 2
        # A draft, as the whole spec is: bootstrap cannot know which tier will
        # reach the paper, and that is what decides between `fetched` and
        # `fetched_unverified`. `fetched_unverified` is the better guess here --
        # papers worth fetching by hand are the ones the open-access tiers miss,
        # so they arrive via a page scrape, and plain `fetched` is earned only by
        # unpacking Europe PMC's ZIP or the PMC OA tarball. Confirm it by eye.
        entry["expect"] = {
            "supplementary_status": ("fetched_unverified" if entry["supplements"]
                                     else "none_listed"),
        }
        articles.append(entry)
        main = entry["main_pdf"]
        print(f"{doi}: main={main['file'] + ' (' + main['version'] + ')' if main else 'NONE'} "
              f"supplements={len(entry['supplements'])}", file=sys.stderr)

    document = {"articles": articles}
    target = Path(args.out)

    # bootstrap builds the spec from its arguments alone -- it does not merge -- and
    # `--out` defaults to the checked-in spec. So a run naming one paper silently
    # discarded the other six, and the only warning was in the README.
    #
    # Compared as DOI *sets* rather than counts: seven articles replaced by seven
    # different ones loses exactly as much as seven replaced by one, and a count
    # check would wave that through. Naming the losses matters more than the number,
    # because the fix is usually to paste them back onto the command line.
    kept = {entry["doi"] for entry in articles}
    existing, readable = _spec_dois(target)
    if not readable:
        if not args.replace:
            print(f"\nrefusing to write {target}: it already exists and could not be read, so "
                  f"there is no way to tell what overwriting it would lose.\n"
                  f"Pass --replace to overwrite it anyway, or --out to write elsewhere.",
                  file=sys.stderr)
            return 2
    else:
        dropped = sorted(existing - kept)
        if dropped and not args.replace:
            print(f"\nrefusing to write {target}: it holds {len(existing)} article(s), and this "
                  f"run would drop {len(dropped)} of them:", file=sys.stderr)
            for doi in dropped:
                print(f"    {doi}", file=sys.stderr)
            print("\nbootstrap writes the spec from its arguments; it does not merge. Either add "
                  "these to\nthe command line to keep them, or pass --replace to accept the loss, "
                  "or draft into a\nscratch file with --out.", file=sys.stderr)
            return 2

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(document, sort_keys=False, width=100), encoding="utf-8")
    if readable:
        dropped = sorted(existing - kept)
        if dropped:
            # --replace was given, so this is what the user asked for. Still said out
            # loud: the whole point of the guard is that the loss is never silent.
            print(f"dropped {len(dropped)} article(s) at --replace: {', '.join(dropped)}",
                  file=sys.stderr)
    print(f"wrote {target}", file=sys.stderr)
    return 0


def _spec_dois(target: Path) -> Tuple[set, bool]:
    """(DOIs in an existing spec, whether it could be read).

    A missing file reads as empty and readable -- there is nothing to lose. A file
    that exists but will not parse is the dangerous case: it may hold anything, so
    it is reported as unreadable rather than as empty.
    """
    if not target.exists():
        return set(), True
    try:
        loaded = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError, UnicodeDecodeError):
        return set(), False
    if not isinstance(loaded, dict):
        return set(), False
    return {entry.get("doi") for entry in (loaded.get("articles") or [])
            if isinstance(entry, dict) and entry.get("doi")}, True


def _verify(args) -> int:
    """Fetch each DOI in the spec into a scratch corpus and compare. Hits the network."""
    from .cli import load_config
    from .fetcher import fetch_publication

    spec = load_spec(args.spec)
    articles = spec.get("articles") or []
    if not articles:
        print("no articles in the spec", file=sys.stderr)
        return 2

    config = load_config(args.config)
    config.setdefault("fetch", {})["corpus_dir"] = args.corpus_dir
    if args.tiers:
        config["fetch"]["tiers"] = args.tiers.split(",")

    bad = 0
    for article in articles:
        record = fetch_publication(article["doi"], config, force=not args.cached)
        directory = record.get("_directory") or store.article_dir(args.corpus_dir, article["doi"])
        checks = compare(article, record, directory, root=args.root)
        print(format_checks(f"\n{article['doi']}  ({article['source_dir']})", checks))
        bad += len(failures(checks))

    print(f"\n{bad} failed check(s)")
    return 1 if bad else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m manuscript_harvest.fetch.manual_fetch",
        description="Fingerprint papers fetched by hand, and check the fetcher against them",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    boot = subparsers.add_parser("bootstrap", help="write a draft spec from manual downloads")
    boot.add_argument("doi", nargs="+", metavar="DOI=SUBDIR",
                      help="e.g. 10.1126/science.adt8307=Science")
    boot.add_argument("--main", action="append", metavar="DOI=FILE[@VERSION]",
                      help="name the article PDF when the filename rule cannot; "
                           "VERSION is 'published' unless said otherwise")
    boot.add_argument("--root", default=None,
                      help=f"folder holding the downloads (default ${MANUAL_DIR_ENV} or ./{DEFAULT_MANUAL_DIR})")
    boot.add_argument("--out", default=str(Path(DEFAULT_MANUAL_DIR) / SPEC_NAME))
    boot.add_argument("--replace", action="store_true",
                      help="accept dropping articles the existing --out spec holds. Without "
                           "it, a run that would lose any is refused and names them")
    boot.set_defaults(func=_bootstrap)

    check = subparsers.add_parser("verify", help="fetch each DOI in the spec and compare (network)")
    check.add_argument("--spec", default=None)
    check.add_argument("--config", default="config.yaml")
    check.add_argument("--root", default=None)
    check.add_argument("--corpus-dir", default="manual-fetch-run",
                      help="scratch corpus, kept away from the real one")
    check.add_argument("--tiers", default=None)
    # Was `--force` with `action="store_true", default=True`, which made the flag
    # inert: `args.force` was True whether or not it was passed. Forcing is the
    # right default -- comparing the fetcher against ground truth through a cached
    # corpus validates stale bytes rather than current behaviour -- so the working
    # flag is the inverse, for iterating on `compare` without re-downloading.
    check.add_argument("--cached", action="store_true",
                      help="reuse an already-fetched scratch corpus instead of "
                           "re-fetching; for working on the comparison itself")
    check.set_defaults(func=_verify)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
