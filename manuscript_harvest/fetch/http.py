"""Polite HTTP client shared by every non-browser source.

Kept as a thin wrapper over `requests` on purpose: the exact request stays
visible and auditable rather than buried in a client library's abstractions.

Two behaviours the sources rely on:

- A minimum interval between requests to the *same host*, so a batch of DOIs
  never bursts against Europe PMC or NCBI. One interval covers every host, with
  named exceptions -- see `_wait_for_host`.
- A self-identifying User-Agent with a contact address. This is the documented
  convention for Crossref, NCBI and Europe PMC, and it is what keeps a polite
  client out of the rate-limited pool. (The browser tier deliberately does NOT
  do this -- see `sources/proxy_browser.py`.)
"""

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
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
        min_interval_overrides: Optional[Dict[str, float]] = None,
    ):
        if max_retries < 0:
            # `get` relies on its retry loop running at least once: the final
            # iteration either returns or raises, which is why it needs no
            # fallthrough. A negative count makes `range()` empty and would send
            # the caller a `None` where a `Response` is declared.
            raise ValueError(f"max_retries must be >= 0, got {max_retries}")
        self.contact_email = contact_email
        self.min_interval = float(min_interval_seconds)
        self.min_interval_overrides = {
            host.lower(): float(seconds)
            for host, seconds in (min_interval_overrides or {}).items()
        }
        self.timeout = timeout_seconds
        self.max_retries = max_retries
        self.ncbi_api_key = ncbi_api_key
        self.max_bytes = max_bytes
        self._last_request: Dict[str, float] = {}
        self._session = requests.Session()

        ua = f"manuscript-harvest/{__version__}"
        if contact_email:
            ua += f" (+mailto:{contact_email})"
        self._session.headers["User-Agent"] = ua

    # -- politeness ---------------------------------------------------------

    def _wait_for_host(self, url: str) -> None:
        """Sleep until this host may be asked again.

        One interval for everything, with per-host exceptions from
        `fetch.min_interval_overrides`. The default is a courtesy NCBI's E-utilities
        documents and asks for; some hosts ask for nothing and are built for volume,
        and a single number cannot say both. `pmc_s3` is the case that forced the
        distinction: it fetches one object per request, so a 14-supplement article
        spends ~45 s asleep and one at the 50-file cap ~150 s, against an AWS bulk
        object store that publishes no such request.

        Matched on the exact netloc, not a suffix. A suffix rule for
        `s3.amazonaws.com` would quietly cover every bucket on it, including hosts
        this tool has never measured, which is the opposite of what an override is
        for. Anything unlisted keeps the default, so an empty mapping -- the default
        -- is byte-for-byte the old behaviour.
        """
        host = urlparse(url).netloc
        interval = self.min_interval_overrides.get(host.lower(), self.min_interval)
        previous = self._last_request.get(host)
        if previous is not None:
            remaining = interval - (time.monotonic() - previous)
            if remaining > 0:
                time.sleep(remaining)
        self._last_request[host] = time.monotonic()

    def _ncbi_params(self, url: str, params: Optional[dict]) -> dict:
        """NCBI asks callers to identify themselves via tool= and email=."""
        params = dict(params or {})
        if "ncbi.nlm.nih.gov" in urlparse(url).netloc:
            params.setdefault("tool", "manuscript-harvest")
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
        headers: Optional[Dict[str, str]] = None,
    ) -> Response:
        """GET with per-host throttling and retry on transient status codes.

        Returns a Response for any completed request, including 4xx -- callers
        distinguish "no supplements" (404) from "we failed" and need the status
        rather than an exception. Only transport failures raise.

        `headers` exists for one reason: `elsevier_tdm` authenticates with an
        `X-ELS-APIKey` header. Elsevier also accepts the key as an `apiKey` query
        parameter, which would have matched `_ncbi_params` above exactly and needed
        no new argument -- and that is the version not taken. Every tier records the
        URL it asked for (`SourceResult.note(..., url=...)`), and those attempts are
        written into `corpus/*/manifest.json`, so a key in the query string would be
        copied onto disk once per Elsevier article and could only be removed by
        rewriting every manifest. A header is not recorded anywhere, and the
        download URLs Elsevier hands back carry no credential of their own.
        """
        params = self._ncbi_params(url, params)
        request_headers = dict(headers or {})
        if accept:
            # `accept=` predates this argument and every existing caller uses it, so
            # it keeps winning: a caller passing both means the explicit `accept`.
            request_headers["Accept"] = accept
        headers = request_headers
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

    def download_to(
        self,
        url: str,
        target,
        headers: Optional[Dict[str, str]] = None,
        cookies: Optional[Dict[str, str]] = None,
        max_bytes: Optional[int] = None,
        chunk: int = 1 << 20,
    ) -> dict:
        """Stream a response body to `target`, hashing as it goes.

        `get` exists for bodies a caller wants in memory and is the right shape for
        almost everything here -- the median supplement is under a megabyte. This is
        for the tail that cannot be a `bytes` at all: `proxy_browser` reaches
        Playwright's wall at ~384 MB of file, because its Node driver marshals a
        body as a **base64 string** and `0x1fffffe8 / (4/3)` is where that lands.
        Two live supplements sit past it -- a 423 MB `.xlsx` and a 488 MB `.gz` --
        and neither can be returned by any in-memory path, whatever the cap says.

        Returns what `store.save_file` returns (`path`, `bytes`, `sha256`) so the
        two are interchangeable to `_write_group`, plus `content_type`. The digest
        is computed from the same chunks that are written rather than by re-reading
        the file, so it describes the bytes that actually landed.

        `cookies` is how the proof-of-work state gets here. The challenge NCBI sets
        is per-session and lives in the browser context that cleared it, so a
        request from this client without them draws the 1.8 KB challenge page
        instead of the file -- `proxy_browser` passes `context.cookies(url)`.

        **Partial files are removed.** A stream that dies halfway leaves a
        plausible-looking file of the right name and the wrong length, and
        `manifest_is_complete` asks only whether a named file is present -- so the
        article would read as settled around a truncated supplement. On any failure
        the target is unlinked and the exception propagates.
        """
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        cap = self.max_bytes if max_bytes is None else max_bytes
        digest = hashlib.sha256()
        written = 0

        self._wait_for_host(url)
        try:
            with self._session.get(
                url,
                headers=dict(headers or {}),
                cookies=cookies or None,
                timeout=self.timeout,
                stream=True,
            ) as resp:
                if resp.status_code >= 400:
                    raise HttpError(f"GET {url} returned HTTP {resp.status_code}")
                content_type = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                with open(target, "wb") as handle:
                    for block in resp.iter_content(chunk_size=chunk):
                        if not block:
                            continue
                        written += len(block)
                        if cap is not None and written > cap:
                            raise HttpError(
                                f"GET {url} exceeded the {cap}-byte cap while streaming")
                        digest.update(block)
                        handle.write(block)
        except Exception:
            target.unlink(missing_ok=True)
            raise

        if not written:
            target.unlink(missing_ok=True)
            raise HttpError(f"GET {url} returned an empty body")

        return {
            "path": str(target),
            "bytes": written,
            "sha256": digest.hexdigest(),
            "content_type": content_type,
        }
