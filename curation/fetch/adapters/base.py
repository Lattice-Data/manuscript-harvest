"""Adapter contract for the browser tier.

An adapter's job is to look at a rendered article page and say where the PDF and
the supplementary files are. Adapters are only reached when every open-access
route has failed, so this is the smallest and most publisher-specific layer --
and the one that rots when a publisher redesigns.

Two rules keep that rot visible:

- `find_supplements` returns `(links, parsed)`. `parsed=False` means "I could not
  locate the supplement section at all", which the fetcher reports as
  `page_not_parsed`. An empty list with `parsed=True` is a real "this page lists
  none". Collapsing those two into `[]` is how a publisher redesign turns into a
  silently incomplete corpus.
- Adapters never raise for a missing element; they return nothing and let the
  caller record it.
"""

import re
from typing import List, Optional, Tuple

# Anchor text or href fragments that indicate supplementary material.
SUPPLEMENT_HINT = re.compile(
    r"(supplement|supporting[\s_-]*information|additional[\s_-]*file|"
    r"media-?\d|MOESM|_ESM|appendix|extended[\s_-]*data)",
    re.IGNORECASE,
)

# Extensions worth downloading when an href looks like a file.
FILE_EXTENSION = re.compile(
    r"\.(pdf|docx?|xlsx?|csv|tsv|txt|zip|gz|tar|pptx?|mp4|avi|mov|"
    r"png|jpe?g|tif{1,2}|fasta|fa|vcf|bed|gtf|json|xml)(\?|$)",
    re.IGNORECASE,
)


class Adapter:
    """Base adapter. `hosts` are matched as substrings of the page hostname."""

    name = "base"
    hosts: Tuple[str, ...] = ()

    def matches(self, host: str) -> bool:
        return any(fragment in host for fragment in self.hosts)

    def find_pdf_url(self, page, doi: str) -> Optional[str]:
        raise NotImplementedError

    def find_supplements(self, page, doi: str) -> Tuple[List[dict], bool]:
        raise NotImplementedError


def meta_content(page, name: str) -> Optional[str]:
    """Read a <meta name=...> value, or None."""
    try:
        return page.get_attribute(f'meta[name="{name}"]', "content", timeout=2000)
    except Exception:
        return None


def collect_links(page, selector: str = "a[href]") -> List[dict]:
    """All anchors on the page as {url, text}, absolute URLs, deduplicated."""
    try:
        raw = page.eval_on_selector_all(
            selector,
            "els => els.map(e => ({url: e.href, text: (e.textContent || '').trim()}))",
        )
    except Exception:
        return []
    seen, out = set(), []
    for entry in raw or []:
        url = entry.get("url")
        if url and url not in seen:
            seen.add(url)
            out.append({"url": url, "text": entry.get("text") or ""})
    return out


def url_without_fragment(url: str) -> str:
    return (url or "").split("#", 1)[0]


def is_file_url(url: str) -> bool:
    """True when the URL path (not its fragment) names a downloadable file.

    The fragment must be ignored. A Nature article page carries anchors like
    `.../s41586-021-03852-1#MOESM4`, one per supplementary object. Matching those
    downloads the article's own HTML once per anchor -- 26 copies of the same page
    saved under extension-less names, which is exactly what happened before this
    check existed.
    """
    return bool(FILE_EXTENSION.search(url_without_fragment(url)))


def looks_like_supplement(link: dict) -> bool:
    """True when an anchor points at an actual supplementary *file*."""
    url = url_without_fragment(link.get("url", ""))
    if not url:
        return False
    # The hint has to be in the URL path or the link text -- never only in a
    # fragment, which marks a section of the current page rather than a file.
    haystack = f"{url} {link.get('text', '')}"
    if not SUPPLEMENT_HINT.search(haystack):
        return False
    return is_file_url(url)


def dedupe_by_target(links: List[dict]) -> List[dict]:
    """Collapse links that point at the same file ignoring their fragments."""
    seen, out = set(), []
    for link in links:
        target = url_without_fragment(link.get("url", ""))
        if target and target not in seen:
            seen.add(target)
            out.append({**link, "url": target})
    return out
