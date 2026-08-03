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
#
# `/suppl/` and `/suppl_file/` are AAAS's abbreviation, and they are matched as
# whole path segments rather than as a bare `suppl` substring: `suppl` is also a
# prefix of `supplier`, `supply` and `supplant`, so the loose form would start
# claiming ordinary hrefs as supplementary material. Measured on
# 10.1126/science.adt8307, whose three supplements are all served from
# `/doi/suppl/<doi>/suppl_file/<name>`: without this the page's supplement
# section is invisible and `supplementary_status` comes back
# `unknown_none_found` for an article that has three of them -- exactly the
# silent false negative the taxonomy exists to expose.
#
# `mmc<n>` is Elsevier's, and it is the filename that carries the meaning -- the
# link text does not. Measured on 10.1016/j.xgen.2026.101304, whose twelve
# supplements are all listed on the page: eleven read "Table S1. Primer
# sequences, related to ..." or "Document S1. Figures S1-S18" and contain no
# word matched above, so only `mmc12` was found, and only by accident -- its
# text happens to say "Article plus supplemental information". One of twelve
# retrieved, reported as `fetched`. Anchored on a separator because a bare
# `mmc\d` could collide with a PII.
SUPPLEMENT_HINT = re.compile(
    r"(supplement|supporting[\s_-]*information|additional[\s_-]*file|"
    r"media-?\d|MOESM|_ESM|appendix|extended[\s_-]*data|"
    r"/suppl(?:_file)?/|(?:^|[/_-]|%2f)mmc\d)",
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

    def looks_blocked(self, page) -> bool:
        """True when the page is a stub served to automation, not the article.

        Distinct from a paywall: the content is licensed and reachable, just not to
        this browser. Publishers that do this return HTTP 200 with a plausible
        shell, so without an explicit check it looks like an article that simply
        has no PDF and no supplements.
        """
        return False


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


def is_supplement_url(url: str) -> bool:
    """True when the URL *path itself* says the target is supplementary.

    Different question from `looks_like_supplement`, so it reads different
    evidence: the link text is ignored and no file extension is required,
    because this answers "could this be the article?" rather than "is this a
    supplementary file worth downloading?". Link text is dropped deliberately --
    a "Download PDF" button next to a supplement says nothing about the target.

    Used to keep a supplement out of the article-PDF slot. Both errors cost
    something, but not equally: a false negative writes the wrong document to
    `fulltext.pdf` and reports `ok`, which is an unaccountable success, while a
    false positive loses the fallback and reports `not_found`, which is visible
    in the taxonomy. The fallback is also only reached when `citation_pdf_url`
    is absent, so the blast radius of the strict answer is small.
    """
    return bool(SUPPLEMENT_HINT.search(url_without_fragment(url or "")))


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


def supplements_from_links(page, predicate) -> Tuple[List[dict], bool]:
    """The `find_supplements` skeleton every adapter shares: collect, filter, dedupe.

    `predicate(link, url)` gets the raw anchor dict *and* the fragment-stripped URL,
    which is why it takes two: the five adapters do not agree on which to match.
    Nature tests the stripped form -- see `NatureAdapter._is_supplement` for the
    `#MOESM<n>` case that decides it -- while Wiley, Elsevier and PMC test the raw
    href. Handing a predicate only one of the two would have changed one of them
    silently, and the difference is not visible from any single adapter.

    The `(links, parsed)` pair is the reason to share this at all. Returning `[]`
    with `parsed=True` for a page that did not render is how a publisher redesign
    becomes a silently incomplete corpus, and until now every new adapter had to
    remember that by hand.
    """
    links = collect_links(page)
    if not links:
        # No anchors at all means the page did not render for us.
        return [], False
    found = [
        {"url": url_without_fragment(link["url"]), "label": link["text"] or None}
        for link in links
        if predicate(link, url_without_fragment(link["url"]))
    ]
    return dedupe_by_target(found), True


def dedupe_by_target(links: List[dict]) -> List[dict]:
    """Collapse links that point at the same file ignoring their fragments."""
    seen, out = set(), []
    for link in links:
        target = url_without_fragment(link.get("url", ""))
        if target and target not in seen:
            seen.add(target)
            out.append({**link, "url": target})
    return out
