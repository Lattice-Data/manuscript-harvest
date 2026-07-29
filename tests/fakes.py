"""Stand-ins for the network, the browser, and PDFs.

Every fixture here mirrors a response actually observed against the live service,
so a test failing means behaviour changed rather than that the fixture was
invented. The docstrings name the DOI each shape came from.

The browser fakes matter most: the `proxy_browser` tier is the largest and most
fragile module in the package and had no offline coverage at all, so every bug in
it so far was found by running real DOIs.
"""

import io
import json
import tarfile
import zipfile
from typing import Dict, List, Optional

import fitz

from curation.fetch.http import Response

DOI = "10.1038/s41586-021-03852-1"
PMCID = "PMC8426186"


# -- PDFs --------------------------------------------------------------------

def make_pdf(pages: int = 3, text: str = "Methods. TP53 knockout via CRISPR-Cas9. " * 20) -> bytes:
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page().insert_text((72, 72), text, fontsize=9)
    data = doc.tobytes()
    doc.close()
    return data


def make_scanned_pdf(pages: int = 2) -> bytes:
    """Parses as a PDF but yields almost no text, like a scanned article."""
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page()
    data = doc.tobytes()
    doc.close()
    return data


def make_paywall_pdf() -> bytes:
    """The short stub some publishers serve instead of the article."""
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "Purchase this article to view the full text.",
                               fontsize=12)
    data = doc.tobytes()
    doc.close()
    return data


# -- HTML shapes -------------------------------------------------------------

PAYWALL_HTML = (
    b"<html><body><h1>Access Denied</h1>"
    b"<p>Please sign in to read the full article, or purchase this article.</p></body></html>"
)

# OCLC EZproxy's response for a host with no stanza.
EZPROXY_HTML = (
    b"<html><body><h1>Oops!</h1><p>It looks like you have attempted to view a page "
    b"that has not been configured for access.</p></body></html>"
)

# NCBI's proof-of-work interstitial, verified on a PMC /bin/ URL (1817 bytes live).
POW_HTML = (
    b"<html><head><title>Preparing to download ...</title></head>"
    b"<body><script src='/assets/pow-o51sQKbL.js'></script></body></html>"
)

SSO_HTML = b"<html><body><h1>Stanford Login</h1><p>Enter your SUNet ID</p></body></html>"

# NCBI's reCAPTCHA gate, served to headless Chrome while plain HTTP got the page.
RECAPTCHA_HTML = b"<html><head><title>Checking your browser - reCAPTCHA</title></head></html>"


# -- API payloads ------------------------------------------------------------

def europepmc_search_json(**overrides) -> bytes:
    """Europe PMC `resultType=core`, shaped from the live response for `DOI`."""
    record = {
        "id": "34497389", "source": "MED", "pmid": "34497389", "pmcid": PMCID,
        "doi": DOI, "title": "A test article", "pubYear": 2021,
        "journalInfo": {"journal": {"title": "Nature"}},
        "isOpenAccess": "Y", "inEPMC": "Y", "inPMC": "Y",
        "hasPDF": "Y", "hasSuppl": "Y", "license": "cc by",
        "fullTextUrlList": {"fullTextUrl": [
            {"availability": "Open access", "availabilityCode": "OA",
             "documentStyle": "pdf", "site": "Europe_PMC",
             "url": "https://example.org/article.pdf"},
        ]},
    }
    record.update(overrides)
    return json.dumps({"hitCount": 1, "resultList": {"result": [record]}}).encode()


EUROPEPMC_EMPTY = json.dumps({"hitCount": 0, "resultList": {"result": []}}).encode()

OA_XML_OK = (
    b'<OA><records returned-count="1"><record id="' + PMCID.encode() + b'" license="CC BY">'
    b'<link format="tgz" href="ftp://ftp.ncbi.nlm.nih.gov/pub/pmc/oa_package/34/e8/'
    + PMCID.encode() + b'.tar.gz" /></record></records></OA>'
)

# What oa.fcgi returns for an article in PMC but outside the OA subset.
OA_XML_ERROR = b'<OA><error code="idIsNotOpenAccess">identifier is not Open Access</error></OA>'


def crossref_json(**overrides) -> bytes:
    message = {
        "publisher": "Test Publisher", "title": ["A test article"],
        "container-title": ["Nature"], "issued": {"date-parts": [[2021, 9, 8]]},
        "resource": {"primary": {"URL": "https://publisher.example/article"}},
    }
    message.update(overrides)
    return json.dumps({"message": message}).encode()


def biorxiv_details_json(version: str = "2", server: str = "biorxiv") -> bytes:
    return json.dumps({
        "messages": [{"status": "ok"}],
        "collection": [{"doi": "10.1101/2024.01.23.576878", "version": version,
                        "server": server, "jatsxml": "https://example.org/x.xml"}],
    }).encode()


# -- archives ----------------------------------------------------------------

def make_zip(members) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in members:
            archive.writestr(name, content)
    return buffer.getvalue()


def make_tgz(members) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, content in members:
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


# -- fake HTTP ---------------------------------------------------------------

class FakeHttp:
    """Routes URLs to canned responses by substring, recording every call.

    Route values are `(status, body, content_type)`. Unmatched URLs return 404,
    which keeps a test honest: forgetting a route surfaces as a miss rather than
    silently passing.
    """

    def __init__(self, routes: Optional[Dict] = None):
        self.routes = dict(routes or {})
        self.calls: List[str] = []
        self.params: List[dict] = []

    def get(self, url, params=None, accept=None, allow_redirects=True) -> Response:
        self.calls.append(url)
        self.params.append(dict(params or {}))
        for fragment, response in self.routes.items():
            if fragment in url:
                status, content, content_type = response
                return Response(url=url, status=status, content=content,
                                content_type=content_type)
        return Response(url=url, status=404, content=b"", content_type="")

    def resolve_redirect(self, url):
        return url

    def called_matching(self, fragment: str) -> int:
        return sum(1 for url in self.calls if fragment in url)


# -- fake browser ------------------------------------------------------------

class FakeResponse:
    def __init__(self, status=200, body=b"", headers=None):
        self.status = status
        self._body = body
        self.headers = headers or {}

    def body(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class FakeRequest:
    """Stands in for Playwright's APIRequestContext."""

    def __init__(self, responses: Optional[Dict] = None):
        self.responses = dict(responses or {})
        self.gets: List[str] = []
        self.heads: List[str] = []

    def _match(self, url):
        for fragment, response in self.responses.items():
            if fragment in url:
                return response
        return FakeResponse(404, b"")

    def get(self, url, headers=None):
        self.gets.append(url)
        response = self._match(url)
        return response() if callable(response) else response

    def head(self, url, headers=None):
        """HEAD is only used for a Content-Length pre-flight.

        A callable route is deliberately NOT invoked here: callables model
        stateful GET sequences (first call challenged, second call real), and
        consuming one from the pre-flight would silently shift the sequence.
        """
        self.heads.append(url)
        response = self._match(url)
        if callable(response):
            return FakeResponse(200, b"", {})
        return FakeResponse(response.status, b"", response.headers)


class FakePage:
    """The slice of Playwright's Page that adapters and the browser tier use."""

    def __init__(self, url="https://www.nature.com/articles/x", metas=None, links=None,
                 title="An article", content=b"<html></html>", goto_error=None):
        self.url = url
        self.metas = metas or {}
        self.links = links or []
        self._title = title
        self._content = content
        self.goto_error = goto_error
        self.closed = False
        self.visited: List[str] = []

    # -- navigation
    def goto(self, url, wait_until=None, timeout=None):
        self.visited.append(url)
        if self.goto_error:
            raise self.goto_error
        self.url = url

    def wait_for_load_state(self, state=None, timeout=None):
        return None

    def title(self):
        return self._title

    def content(self):
        if isinstance(self._content, Exception):
            raise self._content
        return self._content.decode() if isinstance(self._content, bytes) else self._content

    def close(self):
        self.closed = True

    # -- queries
    def get_attribute(self, selector, attribute, timeout=None):
        for name, value in self.metas.items():
            if f'name="{name}"' in selector:
                return value
        raise RuntimeError(f"no element for {selector}")

    def eval_on_selector_all(self, selector, script):
        return list(self.links)


class FakeContext:
    """Stands in for a Playwright BrowserContext."""

    def __init__(self, pages=None, request=None):
        self._queued = list(pages or [])
        self.pages: List[FakePage] = []
        self.request = request or FakeRequest()
        self.cleared_domains: List[str] = []
        self.added_cookies: List[dict] = []

    def new_page(self):
        page = self._queued.pop(0) if self._queued else FakePage()
        self.pages.append(page)
        return page

    def clear_cookies(self, domain=None):
        self.cleared_domains.append(domain)

    def add_cookies(self, cookies):
        self.added_cookies.extend(cookies)

    def storage_state(self, path=None):
        state = {"cookies": [{"name": "a", "value": "b", "domain": "x", "path": "/"}],
                 "origins": []}
        if path:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(state, handle)
        return state

    def set_default_timeout(self, timeout):
        return None

    def close(self):
        return None


def fetch_config(corpus_dir, tiers, **overrides) -> dict:
    config = {"corpus_dir": str(corpus_dir), "tiers": list(tiers),
              "min_interval_seconds": 0, "max_files": 50, "max_file_mb": 200}
    config.update(overrides)
    return {"fetch": config}
