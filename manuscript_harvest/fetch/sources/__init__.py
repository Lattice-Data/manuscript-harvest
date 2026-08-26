"""Source registry, in tier order.

The open-access tiers come first because they need no credentials and no browser.
Not because none of them scrapes: two regex a rendered page, and `OA_TIERS` below is
the only statement of their order, so read it rather than a number in a docstring.
`proxy_browser` is last and is imported lazily -- Playwright is an optional
dependency, and a corpus of open-access papers can be built without it ever being
installed.

`pmc_s3` sits between `europepmc` and `pmc_supplements`, which is the one placement
in this list that is an argument rather than an accident. Ahead of it, Europe PMC
answers a whole article in one request for a bounded ZIP, so it stays cheapest even
though S3 is the more reliable route -- an article there costs one request per
object. Behind it, `pmc_supplements` is the tier that walks into PMC's
proof-of-work wall, so anything that can settle the supplements without that wall
has to be tried first; otherwise the common case spends a page load per file to
earn a 403 and then asks for a browser it did not need.
"""

from typing import List

from .base import Source
from .biorxiv import BiorxivSource
from .europepmc import EuropePmcSource
from .pmc_oa import PmcOaSource
from .pmc_s3 import PmcS3Source
from .pmc_supplements import PmcSupplementsSource

OA_TIERS = ["europepmc", "pmc_s3", "pmc_supplements", "pmc_oa", "biorxiv"]
DEFAULT_TIERS = OA_TIERS + ["proxy_browser"]

_EAGER = {
    "europepmc": EuropePmcSource,
    "pmc_s3": PmcS3Source,
    "pmc_supplements": PmcSupplementsSource,
    "pmc_oa": PmcOaSource,
    "biorxiv": BiorxivSource,
}


def _load(name: str):
    if name in _EAGER:
        return _EAGER[name]
    if name == "proxy_browser":
        # Deferred: importing this pulls in Playwright.
        from .proxy_browser import ProxyBrowserSource

        return ProxyBrowserSource
    raise ValueError(f"unknown fetch tier: {name!r} (known: {', '.join(DEFAULT_TIERS)})")


def build_sources(names, http, config: dict) -> List[Source]:
    """Instantiate the named tiers, in the order given."""
    return [_load(name)(http, config) for name in names]
