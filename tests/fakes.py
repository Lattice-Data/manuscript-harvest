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
import re
import tarfile
import zipfile
from typing import Dict, List, Optional

import fitz

from manuscript_harvest.fetch.http import Response

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


def make_pdf_pages(pages) -> bytes:
    """A PDF with exact control over each page's text.

    `pages` is a list of lists of strings; each string becomes its own layout
    block, which is the unit `manuscript_harvest/extract/pdf.py` reads.

    Text that does not fit raises rather than being silently dropped: PyMuPDF's
    `insert_textbox` returns a negative number and inserts nothing in that case,
    which would hand the test an empty PDF and make it look like a parser bug.
    """
    doc = fitz.open()
    for blocks in pages:
        page = doc.new_page()
        top, bottom = 50.0, 790.0
        height = (bottom - top) / max(1, len(blocks))
        for index, text in enumerate(blocks):
            box = fitz.Rect(45, top + index * height, 550, top + (index + 1) * height)
            overflow = page.insert_textbox(box, text, fontsize=7)
            if overflow < 0:
                raise ValueError(
                    f"fixture text does not fit in its box ({len(text)} chars, "
                    f"{overflow:.0f} short); use fewer blocks or less text")
    data = doc.tobytes()
    doc.close()
    return data


# -- spreadsheets ------------------------------------------------------------

def make_xlsx(sheets) -> bytes:
    """`sheets` is `{sheet_name: [row, ...]}`; rows are lists of cell values."""
    import openpyxl

    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    for name, rows in sheets.items():
        sheet = workbook.create_sheet(title=name)
        for row in rows:
            sheet.append(list(row))
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


#: Transitional -> strict, the inverse of `manuscript_harvest/extract/ooxml._NAMESPACES`.
_STRICT_SWAPS = [
    (b"http://schemas.openxmlformats.org/spreadsheetml/2006/main",
     b"http://purl.oclc.org/ooxml/spreadsheetml/main"),
    (b"http://schemas.openxmlformats.org/officeDocument/2006/relationships",
     b"http://purl.oclc.org/ooxml/officeDocument/relationships"),
]


def make_strict_xlsx(sheets) -> bytes:
    """A strict ISO-29500 workbook, the shape openpyxl reads as having no sheets.

    Built by rewriting a normal workbook's namespaces, which is exactly how
    Excel's "Strict Open XML Spreadsheet" option differs. Observed live on
    10.1016/j.cell.2021.01.053's `mmc7.xlsx`, which held three worksheets and
    reported as empty.
    """
    source = zipfile.ZipFile(io.BytesIO(make_xlsx(sheets)))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as target:
        for info in source.infolist():
            content = source.read(info)
            if info.filename.lower().endswith((".xml", ".rels")):
                for transitional, strict in _STRICT_SWAPS:
                    content = content.replace(transitional, strict)
            target.writestr(info.filename, content)
    source.close()
    return buffer.getvalue()


def make_dimensionless_xlsx(sheets) -> bytes:
    """A workbook whose sheets declare no dimensions.

    openpyxl raises `ValueError: Worksheet is unsized` from
    `calculate_dimension()` for these. Observed on
    10.1038/s44161-025-00612-6's `MOESM5_ESM.xlsx`.
    """
    source = zipfile.ZipFile(io.BytesIO(make_xlsx(sheets)))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as target:
        for info in source.infolist():
            content = source.read(info)
            if info.filename.startswith("xl/worksheets/"):
                content = re.sub(rb"<dimension[^>]*/>", b"", content)
            target.writestr(info.filename, content)
    source.close()
    return buffer.getvalue()


# -- Word documents ----------------------------------------------------------

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _docx_paragraph(text: str, style=None) -> str:
    properties = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f"<w:p>{properties}<w:r><w:t>{text}</w:t></w:r></w:p>"


def _docx_table(rows) -> str:
    body = ""
    for row in rows:
        cells = "".join(
            f"<w:tc>{_docx_paragraph(str(cell))}</w:tc>" for cell in row)
        body += f"<w:tr>{cells}</w:tr>"
    return f"<w:tbl>{body}</w:tbl>"


def make_docx(parts) -> bytes:
    """`parts` is a list of `("paragraph", text[, style])`, `("table", rows)`,
    or `("raw", xml)` for exercising field codes and tracked deletions."""
    body = ""
    for part in parts:
        kind = part[0]
        if kind == "paragraph":
            body += _docx_paragraph(part[1], part[2] if len(part) > 2 else None)
        elif kind == "table":
            body += _docx_table(part[1])
        elif kind == "raw":
            body += part[1]
    document = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<w:document xmlns:w="{_W}"><w:body>{body}</w:body></w:document>')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml",
                         '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org'
                         '/package/2006/content-types"><Default Extension="xml" '
                         'ContentType="application/xml"/></Types>')
        archive.writestr("_rels/.rels",
                         '<?xml version="1.0"?><Relationships xmlns="http://schemas.'
                         'openxmlformats.org/package/2006/relationships"/>')
        archive.writestr("word/document.xml", document)
    return buffer.getvalue()


# -- JATS --------------------------------------------------------------------

def jats_article(body: str = "", front_extra: str = "", back: str = "",
                 doctype: bool = True) -> bytes:
    """A JATS article shaped like Europe PMC's `fulltext.nxml`.

    The DOCTYPE is included by default because it is present in every real file
    and it is what makes named entities undefined for the stdlib parser.
    """
    prologue = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE article PUBLIC "-//NLM//DTD JATS (Z39.96) Journal Publishing '
        'DTD v1.2 20190208//EN" "JATS-journalpublishing1.dtd">\n' if doctype else
        '<?xml version="1.0" encoding="UTF-8"?>\n')
    return (prologue + f"""<article xmlns:xlink="http://www.w3.org/1999/xlink"
 article-type="research-article">
<front><journal-meta><journal-title>Nature Communications</journal-title></journal-meta>
<article-meta>
<article-id pub-id-type="doi">{DOI}</article-id>
<title-group><article-title>A test article about islets</article-title></title-group>
<pub-date><year>2023</year></pub-date>
<kwd-group><kwd>single-cell</kwd><kwd>pancreas</kwd></kwd-group>
<abstract><p>We profiled human islets.</p></abstract>
{front_extra}
</article-meta></front>
<body>{body}</body>
<back>{back}</back>
</article>""").encode("utf-8")


#: The shape Springer uses: href and caption live on a nested <media>, not on
#: <supplementary-material> itself. Reading only direct children found labels
#: for none of the 40 XML files in the corpus.
SPRINGER_SUPPLEMENT = """<sec sec-type="supplementary-material"><sec>
<title>Supplementary information</title><p>
<supplementary-material content-type="local-data" id="MOESM1">
<media xlink:href="41467_2023_40505_MOESM3_ESM.xlsx"><caption><p>Supplementary Table 3</p>
</caption></media></supplementary-material>
</p></sec></sec>"""


LANDING_INTERSTITIAL = (
    b"<html><body><div>User Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    b"AppleWebKit/537.36 HeadlessChrome/150.0.0.0 Safari/537.36</div></body></html>"
)


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

# Duo's universal prompt, where an expired proxy session actually lands. Served
# from `api-<id>.duosecurity.com/prompt/...`, and note what is *not* on it: no
# SUNet ID, no "Stanford Login", no "two-step authentication". Matching only
# Stanford's own wording left this classified as no denial at all.
DUO_PROMPT_HTML = (
    b"<html><head><title>Duo Security</title></head><body>"
    b"<h1>Select an option to log in</h1>"
    b"<button>Duo Push</button><button>Send to Mobile Phone</button>"
    b"<footer>Secured by Duo</footer></body></html>"
)
DUO_PROMPT_URL = "https://api-1b2c3d4e.duosecurity.com/prompt/?sid=frameless-xyz"

# ClinicalKey's answer for an article it does not carry: an XML error document,
# HTTP 200, 2562 bytes live. Rendered through Chrome's XML viewer, which is why
# the markup appears twice -- once as the hidden source and once escaped in the
# pretty-print tree. Observed on 10.1016/j.xgen.2026.101304, where the proxy
# routed an Elsevier DOI to a clinical-content platform that has no Cell
# Genomics. The Chromium viewer's ~2 KB of stylesheet is dropped here; nothing
# in it is load-bearing.
RESOURCE_NOT_FOUND_XML = (
    b'<html xmlns="http://www.w3.org/1999/xhtml"><head></head><body>'
    b'<div id="webkit-xml-viewer-source-xml"><ServiceErrorResponse xmlns="">'
    b"<status>RESOURCE_NOT_FOUND</status><message>Could not find EID for link "
    b"resolver with params: field: pii; value: S2666979X26001667</message>"
    b"</ServiceErrorResponse></div>"
    b'<div class="header"><span>This XML file does not appear to have any style '
    b"information associated with it. The document tree is shown below.</span></div>"
    b'<div class="pretty-print"><div class="line">'
    b'<span class="html-tag">&lt;ServiceErrorResponse&gt;</span></div>'
    b'<div class="line"><span class="html-tag">&lt;status&gt;</span>'
    b"<span>RESOURCE_NOT_FOUND</span></div></div></body></html>"
)

# NCBI's reCAPTCHA gate, served to headless Chrome while plain HTTP got the page.
RECAPTCHA_HTML = b"<html><head><title>Checking your browser - reCAPTCHA</title></head></html>"


# -- rendered anchor sets ----------------------------------------------------

#: The AAAS article page for 10.1126/science.adt8307, reduced to the anchors that
#: matter, in the order `collect_links` really saw them (absolute, as `e.href`
#: yields them, and proxied, because that is the only way the page is reachable).
#:
#: Two bugs live in this ordering. The page carries no `citation_pdf_url` at all,
#: and every supplement anchor precedes both article-PDF anchors -- so the naive
#: "first link ending in .pdf" fallback stored the 29-page Supplementary
#: Materials PDF as `fulltext.pdf` and never fetched the 19-page article. The
#: three real supplements were invisible at the same time, because `suppl_file`
#: is not the word `supplement`.
_SCIENCE_HOST = "https://www-science-org.stanford.idm.oclc.org"
SCIENCE_ARTICLE_LINKS = [
    {"url": f"{_SCIENCE_HOST}/doi/10.1126/science.adt8307#supplementary-materials",
     "text": "Supplementary Materials"},
    {"url": f"{_SCIENCE_HOST}/doi/suppl/10.1126/science.adt8307/suppl_file/"
            "science.adt8307_sm.pdf", "text": "Download"},
    {"url": f"{_SCIENCE_HOST}/doi/suppl/10.1126/science.adt8307/suppl_file/"
            "science.adt8307_tables_s1_to_s28.zip", "text": "Download"},
    {"url": f"{_SCIENCE_HOST}/doi/suppl/10.1126/science.adt8307/suppl_file/"
            "science.adt8307_mdar_reproducibility_checklist.pdf", "text": "Download"},
    {"url": f"{_SCIENCE_HOST}/doi/pdf/10.1126/science.adt8307?download=true",
     "text": "Download PDF"},
    {"url": f"{_SCIENCE_HOST}/doi/pdf/10.1126/science.adt8307", "text": "Download PDF"},
]


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


def make_article(directory, fulltext=None, xml=None, supplements=(), landing=None,
                 doi=DOI) -> "object":
    """Write a corpus article directory the extraction stage can be pointed at.

    `supplements` is a list of `(filename, bytes)` or
    `(filename, bytes, original_name)`; the manifest records them the way
    `manuscript_harvest/fetch/store.py` does, retrieval-order prefix included.
    """
    from pathlib import Path

    from manuscript_harvest.fetch import store
    from manuscript_harvest.fetch.identifiers import doi_slug

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    record = {
        "doi": doi, "doi_raw": doi, "slug": doi_slug(doi),
        "fetched_at": "2026-07-01T00:00:00+00:00",
        "identifiers": {"doi": doi}, "status": "complete",
        "fulltext": {"status": "not_found", "path": None},
        "fulltext_xml": None, "supplementary": [],
    }
    if fulltext is not None:
        (directory / store.FULLTEXT_PDF).write_bytes(fulltext)
        record["fulltext"] = {"path": store.FULLTEXT_PDF, "status": "ok",
                             "bytes": len(fulltext)}
    if xml is not None:
        (directory / store.FULLTEXT_XML).write_bytes(xml)
        record["fulltext_xml"] = {"path": store.FULLTEXT_XML, "bytes": len(xml)}
    if landing is not None:
        (directory / store.LANDING_HTML).write_bytes(landing)
    for index, entry in enumerate(supplements, start=1):
        name, content = entry[0], entry[1]
        original = entry[2] if len(entry) > 2 else name
        stored = f"{store.SUPPLEMENT_DIR}/{store.supplement_filename(index, name)}"
        target = directory / stored
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        record["supplementary"].append({
            "path": stored, "bytes": len(content), "index": index,
            "original_name": original, "content_type": "",
        })
    store.write_manifest(directory, record)
    return directory


def fetch_config(corpus_dir, tiers, **overrides) -> dict:
    config = {"corpus_dir": str(corpus_dir), "tiers": list(tiers),
              "min_interval_seconds": 0, "max_files": 50, "max_file_mb": 200}
    config.update(overrides)
    return {"fetch": config}
