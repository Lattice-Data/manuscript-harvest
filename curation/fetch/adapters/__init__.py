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


def adapter_for(url: str) -> Adapter:
    host = (urlparse(url).hostname or "").lower()
    for adapter in ADAPTERS:
        if adapter.matches(host):
            return adapter
    return FALLBACK


def adapter_names() -> list:
    return [a.name for a in ADAPTERS] + [FALLBACK.name]
