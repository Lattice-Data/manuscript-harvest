"""Source registry, in tier order.

The open-access tiers come first because they need no credentials, no browser,
and no page scraping. `proxy_browser` is last and is imported lazily -- Playwright
is an optional dependency, and a corpus of open-access papers can be built
without it ever being installed.
"""

from typing import List

from .base import Source
from .biorxiv import BiorxivSource
from .europepmc import EuropePmcSource
from .pmc_oa import PmcOaSource
from .pmc_supplements import PmcSupplementsSource

OA_TIERS = ["europepmc", "pmc_supplements", "pmc_oa", "biorxiv"]
DEFAULT_TIERS = OA_TIERS + ["proxy_browser"]

_EAGER = {
    "europepmc": EuropePmcSource,
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
