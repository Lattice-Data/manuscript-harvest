"""Source registry, in tier order.

The open-access tiers come first because they need no browser and no human. Not
because none of them scrapes: two regex a rendered page, and `OA_TIERS` below is the
only statement of their order, so read it rather than a number in a docstring.
`proxy_browser` is last and is imported lazily -- Playwright is an optional
dependency, and a corpus of open-access papers can be built without it ever being
installed.

**"No credentials" is not the line `OA_TIERS` draws, and `elsevier_tdm` is why the
distinction is now written down.** Two of these tiers already send an API key when
`fetch.ncbi_api_key` is set (`_ncbi_params` adds it for every `ncbi.nlm.nih.gov`
request, which `pmc_supplements` and `pmc_oa` make), so an optional key was never
what this list excluded. What `--oa-only` promises is its own help text -- "never
open a browser" -- and what the browser costs that an API does not is a headed
Stanford SSO login, a human, and `manuscript-fetch login`. `elsevier_tdm` needs a
key and no browser, so it belongs here; every tier in this list still *works* with
no credentials at all, because `elsevier_tdm.applies` returns False without a key.

`pmc_s3` and `elsevier_tdm` sit between `europepmc` and `pmc_supplements`, the two
placements in this list that are an argument rather than an accident. Ahead of them,
Europe PMC answers a whole article in one request for a bounded ZIP, so it stays
cheapest even though S3 is the more reliable route -- an article there costs one
request per object. Behind them, `pmc_supplements` is the tier that walks into PMC's
proof-of-work wall, so anything that can settle the supplements without that wall
has to be tried first; otherwise the common case spends a page load per file to
earn a 403 and then asks for a browser it did not need. `elsevier_tdm` is on the
near side of that wall for the same reason and costs one listing request plus one
per file, and it is the *only* route to the supplements of a Cell Press or
ScienceDirect article: Cloudflare serves the browser tier a challenge there, so
without it those files can only be fetched by hand.
"""

from typing import List

from .base import Source
from .biorxiv import BiorxivSource
from .elsevier_tdm import ElsevierTdmSource
from .europepmc import EuropePmcSource
from .pmc_oa import PmcOaSource
from .pmc_s3 import PmcS3Source
from .pmc_supplements import PmcSupplementsSource

OA_TIERS = ["europepmc", "pmc_s3", "elsevier_tdm", "pmc_supplements", "pmc_oa",
            "biorxiv"]
DEFAULT_TIERS = OA_TIERS + ["proxy_browser"]

_EAGER = {
    "europepmc": EuropePmcSource,
    "pmc_s3": PmcS3Source,
    "elsevier_tdm": ElsevierTdmSource,
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
