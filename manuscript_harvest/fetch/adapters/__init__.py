"""Adapter registry, selected by the hostname of the rendered page."""

from urllib.parse import urlparse

from .base import Adapter
from .generic import GenericAdapter
from .publishers import ElsevierAdapter, NatureAdapter, PmcAdapter, WileyAdapter

# Specific adapters first; the generic one always matches and comes last.
ADAPTERS = [
    NatureAdapter(),
    WileyAdapter(),
    ElsevierAdapter(),
    PmcAdapter(),
]
FALLBACK = GenericAdapter()

# EZproxy rewrites hostnames by replacing dots with hyphens and appending its own
# domain: www.nature.com becomes www-nature-com.stanford.idm.oclc.org. Without
# undoing that, every proxied page falls through to the generic adapter -- which is
# exactly what happened on the first authenticated fetch.
_PROXY_SUFFIXES = (".idm.oclc.org", ".ezproxy.stanford.edu")


def candidate_hosts(url: str):
    """The page hostname, plus its un-rewritten form if it came via the proxy."""
    host = (urlparse(url).hostname or "").lower()
    hosts = [host]
    for suffix in _PROXY_SUFFIXES:
        if host.endswith(suffix):
            label = host[: -len(suffix)].split(".")[0]
            # Hyphens stood in for dots. Real hyphens in a hostname are
            # unrecoverable here, but this is only used for substring matching,
            # so an approximate reversal is enough to pick the right adapter.
            hosts.append(label.replace("-", "."))
            break
    return hosts


def adapter_for(url: str) -> Adapter:
    hosts = candidate_hosts(url)
    for adapter in ADAPTERS:
        if any(adapter.matches(host) for host in hosts):
            return adapter
    return FALLBACK


def adapter_names() -> list:
    return [a.name for a in ADAPTERS] + [FALLBACK.name]
