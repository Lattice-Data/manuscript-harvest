"""Can text be extracted from a file with this name?

One question, one answer, in one place. The fetch stage asks it before spending a
request on a supplement (`fetch/sources/*`, then `fetch/fetcher.py` as the
guarantee), the pruner asks it again of files already on disk
(`fetch/drop_media.py`), and the extraction stage dispatches on the same two sets
below. Nothing restates the lists: two copies of this policy would mean a file the
fetcher refused and a file the extractor called readable, which is the drift
`pmc_oa.supplement_or_media` was lifted out to prevent one layer down.

It lives at the top of the package, beside `config.py`, and imports nothing from
either subpackage. That is not tidiness: `extract/extractor.py` imports
`fetch.store`, so a predicate under `fetch/` that wanted the extractor's extension
sets -- or an extractor that wanted the fetcher's -- would close a cycle. Here
both sides import downward and neither imports the other.

**Measured over this corpus: 393 articles, 26.90 GB of payload** (manifests and
`extracted/` excluded):

    text-bearing  18.82 GB  70.0%   3464 files
    archive        5.11 GB  19.0%    118 files   (.zip alone 5.05 GB)
    audio/video    2.15 GB   8.0%     59 files   (.mp4 1.97 GB, .mov 0.18 GB)
    image          0.75 GB   2.8%   2373 files   (.tif 0.39 GB, .jpg 0.31 GB)

**And over the manifests: 5116 stored supplementary entries, 2428 of them (47%)
image, audio or video** -- files no text can be extracted from. 138 articles hold
at least one, and inside those articles 71% of the supplement slots are non-text.

So the saving this policy pursues is *not mainly disk*: 2.90 GB of 26.90 GB, and
`fetch/store.py`'s size-budget comment has said since it was written that there is
no useful drop-the-media saving to be had against a budget. That measurement still
holds. What halves is the number of things: requests spent, manifest entries
promising a file, and extraction records whose only content is the word
`image_no_text`. 2373 image files against 0.75 GB is the whole shape of it -- the
count is the cost, not the bytes.

**The skip sets are the decision; the keep set is documentation.** `skip_reason`
consults `IMAGE_EXTENSIONS` and `AUDIO_VIDEO_EXTENSIONS` and keeps everything
else, including every extension it has never seen. The opposite arrangement --
keep only what `KEPT_EXTENSIONS` names -- reads more cautious and is worse: 13
supplements in this corpus were saved by the browser tier as `NN_url` with no
extension at all, several of them real PDFs and spreadsheets, and a whitelist
would refuse those, plus every format a publisher adopts after today. Unknown
means unknown, which is the same judgement `supplement_or_media` makes when it
sends an unrecognised extension to `supplementary/`: the cost of fetching a file
nothing reads is clutter, and the cost of refusing a supplementary table is a
curator not finding it. `KEPT_EXTENSIONS` is enumerated anyway because it is the
list the user actually stated, and `tests/test_text_bearing.py` walks it to pin
that every one of those extensions still gets fetched -- so it is a constraint
rather than a comment.

**Archives are kept**, although no extension in them is text. `.zip` alone is
5.05 GB of the 5.11 GB archive total, and those zips are mostly supplementary
tables; `extract/extractor.py` already unpacks them and reads the members, so a
zip is a text-bearing file with a lid on. `.gz`/`.tar`/`.tgz` come along because
the same publishers use them for the same content, even where the extractor
currently reports `unsupported_format` for them -- refusing to fetch a file is a
much more expensive mistake than not yet having a parser for it.

**Scanned PDFs stay too**, and cannot be helped from here: 68 supplements in this
corpus extract as `no_text_scanned_pdf`, which is only knowable after the bytes
arrive and a parser has looked. Not this module's problem, and not solvable by any
predicate over a filename.

Two neighbouring lists answer *different* questions and are deliberately not
merged into this one:

- `extract/extractor.py`'s `TEXT_BEARING_EXTENSIONS` says which zip *members* the
  dispatcher has a parser for. It is narrower on purpose -- no `.json`, `.doc`,
  `.rtf` or `.pptx`, all of which are worth having on disk. If either list gains
  an extension, review both.
- `fetch/sources/pmc_oa.py`'s `_IMAGE_EXTENSIONS` decides whether a *deposited*
  file is one of the article's own figures (`media/`) or a supplement. It is a
  subset of `IMAGE_EXTENSIONS` here, and pointing it at this set would move
  `.svg`, `.webp`, `.ps` and `.ai` files from `supplementary/` to `media/` for a
  run with this policy switched off -- a behaviour change in the branch that is
  supposed to reproduce today exactly. Left alone.
"""

import re
from typing import Optional

#: The `fetch` config key that switches this policy off. Read through
#: `policy_is_on` rather than by name, so the default lives in one place: a run
#: that finds no `config.yaml` at all must filter exactly like one that finds it.
CONFIG_KEY = "text_bearing_only"

#: Reasons `skip_reason` returns. They correspond one-for-one to the extraction
#: stage's `image_no_text` and `media_no_text` per-file statuses, because they are
#: driven by the same two sets -- which is the whole argument for refusing these
#: files at fetch time. A file skipped here is a file the extractor would have
#: recorded as benign and empty, so no block, no table and no character is lost.
SKIP_IMAGE = "image"
SKIP_AUDIO_VIDEO = "audio_video"

#: Figure images. `.svg` is XML on the wire and still belongs here: the extractor
#: has no parser for it and records `image_no_text`, so fetching one buys a file
#: whose only trace is that word.
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".tif", ".tiff", ".bmp", ".eps",
                    ".ps", ".svg", ".webp", ".ai"}

#: Audio and video. Named for what they are rather than "media", which means the
#: article's own figures on the fetch side (`store.MEDIA_DIR`) and audio/video on
#: the extraction side -- one word, two meanings, and this module is where they
#: meet.
AUDIO_VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".mpg", ".mpeg",
                          ".m4v", ".mp3", ".wav", ".flv"}

#: What the policy is *for*, stated as the user stated it. Not consulted by
#: `skip_reason` -- see the module docstring on why the skip sets are the decision
#: -- but pinned by a test, so an extension cannot quietly leave the set.
KEPT_EXTENSIONS = {
    # prose and tables
    ".pdf", ".txt", ".md", ".csv", ".tsv", ".xlsx", ".xls", ".xlsm",
    ".docx", ".doc", ".rtf", ".pptx",
    # structured text
    ".xml", ".nxml", ".json", ".html",
    # archives: a lid over the tables above, which the extractor opens
    ".zip", ".gz", ".tar", ".tgz",
}


def extension(name: str) -> str:
    """The lowercased extension of `name`, dot included, or `""` if it has none.

    Takes a URL as readily as a filename, because at fetch time the only name a
    tier has is often an anchor href or an S3 key: the query string and fragment
    are cut first and only the last path segment is read. Without the cut,
    `.../media-1.mp4?download=true` has the extension `.mp4?download=true` and
    every skip silently misses.
    """
    candidate = re.split(r"[?#]", name or "")[0]
    candidate = candidate.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return candidate[candidate.rfind("."):] if "." in candidate else ""


def skip_reason(name: str) -> Optional[str]:
    """Why no text can come out of a file called `name`, or None if some can.

    None is the answer for every name this cannot classify, including one with no
    extension at all. See the module docstring: the skip sets are closed and the
    keep side is open.
    """
    suffix = extension(name)
    if suffix in IMAGE_EXTENSIONS:
        return SKIP_IMAGE
    if suffix in AUDIO_VIDEO_EXTENSIONS:
        return SKIP_AUDIO_VIDEO
    return None


def text_can_be_extracted(name: str) -> bool:
    """The predicate, for readers who want it as a question rather than a reason."""
    return skip_reason(name) is None


def policy_is_on(fetch_cfg: Optional[dict]) -> bool:
    """Is the fetch stage restricted to files text can come out of?

    Default True, which is the user's stated intent, and the default has to live
    here rather than at each call site: `fetch/cli.py` fills the key into
    `DEFAULT_FETCH_CONFIG` for a run that *found* a config file, and a run started
    from a subdirectory or from an install finds none at all
    (`warn_if_config_missing` prints and carries on). Both must filter.
    """
    return bool((fetch_cfg or {}).get(CONFIG_KEY, True))
