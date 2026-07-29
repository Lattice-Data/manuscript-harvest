"""Polite HTTP client shared by every non-browser source.

Kept as a thin wrapper over `requests` on purpose: the exact request stays
visible and auditable rather than buried in a client library's abstractions.

Two behaviours the sources rely on:

- A minimum interval between requests to the *same host*, so a batch of DOIs
  never bursts against Europe PMC or NCBI.
- A self-identifying User-Agent with a contact address. This is the documented
  convention for Crossref, NCBI and Europe PMC, and it is what keeps a polite
  client out of the rate-limited pool. (The browser tier deliberately does NOT
  do this -- see `sources/proxy_browser.py`.)
"""

import json
import time
from dataclasses import dataclass, field
from typing import Dict, Optional
from urllib.parse import urlparse

import requests

from . import __version__

_RETRY_STATUS = {429, 500, 502, 503, 504}


class HttpError(RuntimeError):
    pass


@dataclass
class Response:
    url: str            # final URL after redirects
    status: int
    content: bytes
    content_type: str = ""
    headers: Dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", "replace")

    def json(self):
        return json.loads(self.text)


class Http:
    def __init__(
        self,
        contact_email: Optional[str] = None,
        min_interval_seconds: float = 3.0,
        timeout_seconds: int = 60,
        max_retries: int = 2,
        ncbi_api_key: Optional[str] = None,
        max_bytes: Optional[int] = None,
    ):
        self.contact_email = contact_email
        self.min_interval = float(min_interval_seconds)
        self.timeout = timeout_seconds
        self.max_retries = max_retries
        self.ncbi_api_key = ncbi_api_key
        self.max_bytes = max_bytes
        self._last_request: Dict[str, float] = {}
        self._session = requests.Session()

        ua = f"paper-harvest/{__version__}"
        if contact_email:
            ua += f" (+mailto:{contact_email})"
        self._session.headers["User-Agent"] = ua

    # -- politeness ---------------------------------------------------------

    def _wait_for_host(self, url: str) -> None:
        host = urlparse(url).netloc
        previous = self._last_request.get(host)
        if previous is not None:
            remaining = self.min_interval - (time.monotonic() - previous)
            if remaining > 0:
                time.sleep(remaining)
        self._last_request[host] = time.monotonic()

    def _ncbi_params(self, url: str, params: Optional[dict]) -> dict:
        """NCBI asks callers to identify themselves via tool= and email=."""
        params = dict(params or {})
        if "ncbi.nlm.nih.gov" in urlparse(url).netloc:
            params.setdefault("tool", "paper-harvest")
            if self.contact_email:
                params.setdefault("email", self.contact_email)
            if self.ncbi_api_key:
                params.setdefault("api_key", self.ncbi_api_key)
        return params

    # -- requests -----------------------------------------------------------

    def get(
        self,
        url: str,
        params: Optional[dict] = None,
        accept: Optional[str] = None,
        allow_redirects: bool = True,
    ) -> Response:
        """GET with per-host throttling and retry on transient status codes.

        Returns a Response for any completed request, including 4xx -- callers
        distinguish "no supplements" (404) from "we failed" and need the status
        rather than an exception. Only transport failures raise.
        """
        params = self._ncbi_params(url, params)
        headers = {"Accept": accept} if accept else {}
        last_error = None

        for attempt in range(self.max_retries + 1):
            self._wait_for_host(url)
            try:
                resp = self._session.get(
                    url,
                    params=params or None,
                    headers=headers,
                    timeout=self.timeout,
                    allow_redirects=allow_redirects,
                )
            except requests.RequestException as e:
                last_error = f"{type(e).__name__}: {e}"
                if attempt == self.max_retries:
                    raise HttpError(f"GET {url} failed: {last_error}") from e
                time.sleep(2 ** attempt)
                continue

            if resp.status_code in _RETRY_STATUS and attempt < self.max_retries:
                # Honour Retry-After when the server sends one.
                delay = resp.headers.get("Retry-After")
                try:
                    delay = float(delay) if delay is not None else 2 ** attempt
                except ValueError:
                    delay = 2 ** attempt
                time.sleep(min(delay, 30))
                continue

            content = resp.content
            if self.max_bytes is not None and len(content) > self.max_bytes:
                raise HttpError(
                    f"GET {url} returned {len(content)} bytes, over the "
                    f"{self.max_bytes}-byte cap"
                )
            return Response(
                url=resp.url,
                status=resp.status_code,
                content=content,
                content_type=(resp.headers.get("Content-Type") or "").split(";")[0].strip().lower(),
                headers=dict(resp.headers),
            )

        raise HttpError(f"GET {url} exhausted retries: {last_error}")

    def resolve_redirect(self, url: str) -> str:
        """Follow redirects and return the final URL, without keeping the body.

        Used to turn a DOI into a publisher landing URL before the proxy prefix
        is applied. Falls back to the input URL if resolution fails, so a
        transient doi.org problem does not abort the whole fetch.
        """
        try:
            self._wait_for_host(url)
            resp = self._session.get(url, timeout=self.timeout, allow_redirects=True, stream=True)
            final = resp.url
            resp.close()
            return final
        except requests.RequestException:
            return url
