"""Landing pages -> blocks. The last resort, and it is honest about being one.

Ten articles in this corpus (all Elsevier or Science) have no PDF and no XML --
only the `landing.html` the browser tier saved. What that page contains is the
publisher's abstract plus a great deal of navigation, so this module does two
things and claims nothing more:

- reads the `citation_*` / `dc.*` / `og:*` meta tags, which is where the title,
  journal, date, and often the whole abstract actually live, in a clean form;
- keeps text runs long enough to be prose and drops the rest, because a
  three-word run is a menu item.

The extractor marks an article whose main text came from here as
`landing_page_only`. That flag exists so that "no perturbations found" in such an
article reads as "we never had the article" rather than as a finding.
"""

import re
from html.parser import HTMLParser
from typing import Dict, List, Tuple

from ..fetch.validate import classify_denial
from .blocks import METADATA, PARAGRAPH, Block
from .limits import Limits

OK = "ok"
NO_TEXT = "no_text"

_SKIP_ELEMENTS = {"script", "style", "noscript", "svg", "template", "head",
                  "nav", "footer", "form", "select", "button", "iframe"}
_BLOCK_ELEMENTS = {"p", "div", "section", "article", "li", "td", "th", "tr", "h1",
                   "h2", "h3", "h4", "h5", "h6", "blockquote", "dd", "dt", "figcaption",
                   "br", "table", "ul", "ol", "main", "span"}
_WANTED_META_PREFIXES = ("citation_", "dc.", "og:", "prism.", "twitter:description")


class _Reader(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.metas: Dict[str, List[str]] = {}
        self.chunks: List[str] = []
        self._buffer: List[str] = []
        self._skip_depth = 0

    def _flush(self) -> None:
        text = re.sub(r"\s+", " ", "".join(self._buffer).replace("\xa0", " ")).strip()
        self._buffer = []
        if text:
            self.chunks.append(text)

    def handle_starttag(self, tag, attrs):
        if tag == "meta":
            attributes = dict(attrs)
            name = (attributes.get("name") or attributes.get("property") or "").strip()
            content = (attributes.get("content") or "").strip()
            if name and content and name.lower().startswith(_WANTED_META_PREFIXES):
                self.metas.setdefault(name, []).append(content)
            return
        if tag in _SKIP_ELEMENTS:
            self._skip_depth += 1
            self._flush()
            return
        if tag in _BLOCK_ELEMENTS:
            self._flush()

    def handle_endtag(self, tag):
        if tag in _SKIP_ELEMENTS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag in _BLOCK_ELEMENTS:
            self._flush()

    def handle_data(self, data):
        if self._skip_depth == 0:
            self._buffer.append(data)

    def close(self):
        super().close()
        self._flush()


def blocks_from_html(
    data: bytes, source_file: str, limits: Limits
) -> Tuple[List[Block], str, dict]:
    """Parse one saved HTML page. Returns `(blocks, status, meta)`."""
    reader = _Reader()
    try:
        reader.feed(data.decode("utf-8", errors="replace"))
        reader.close()
    except Exception as e:  # html.parser is lenient, but never trust a saved page
        return [], NO_TEXT, {"reason": f"{type(e).__name__}: {e}"}

    blocks: List[Block] = []
    meta: dict = {}

    if reader.metas:
        lines = []
        for name in sorted(reader.metas):
            values = reader.metas[name]
            # Author lists arrive as one meta tag per author.
            lines.append(f"{name}: " + "; ".join(dict.fromkeys(values)))
        meta["meta_tags"] = {k: v for k, v in reader.metas.items()}
        blocks.append(Block(kind=METADATA, text="\n".join(lines), source_file=source_file,
                            origin="html", locator="meta"))

    seen = set()
    kept = 0
    for chunk in reader.chunks:
        if len(chunk) < limits.min_html_block_chars:
            continue
        key = chunk[:200]
        if key in seen:
            continue
        seen.add(key)
        if len(blocks) >= limits.max_blocks_per_file:
            meta["blocks_capped"] = True
            break
        kept += 1
        blocks.append(Block(kind=PARAGRAPH, text=chunk, source_file=source_file,
                            origin="html", locator=f"text {kept}"))

    meta["text_runs"] = kept
    if not blocks:
        return [], NO_TEXT, meta

    # A page with no citation metadata and almost no prose is an interstitial.
    # Returning it as `ok` would be the failure mode `harvest/fetch/validate.py`
    # exists to prevent: an empty result that reads like a clean run.
    chars = sum(len(b.text) for b in blocks)
    if not reader.metas and chars < limits.min_landing_chars:
        denial = classify_denial("", data)
        reason = (f"only {chars} characters of text and no citation metadata: "
                  f"this is an interstitial, not an article page")
        if denial:
            reason += f" (looks like a {denial} page)"
        meta["reason"] = reason
        return [], NO_TEXT, meta

    return blocks, OK, meta
