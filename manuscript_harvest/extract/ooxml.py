"""Making ISO-29500 "strict" Office files readable.

Excel's *Strict Open XML Spreadsheet* save option produces a file that is a
valid `.xlsx` in every way except that it uses the strict namespace URIs
(`http://purl.oclc.org/ooxml/...`) instead of the transitional ones
(`http://schemas.openxmlformats.org/...`). openpyxl only knows the transitional
namespace, so it reads a strict workbook as having **zero worksheets** -- no
exception, no warning, just an empty book.

That is the worst possible failure shape for this pipeline: `04_mmc7.xlsx` in
this corpus holds three worksheet parts and reported `no_text`, which is
indistinguishable from a genuinely empty supplement.

The fix is to rewrite the namespace URIs in every XML part and hand openpyxl the
patched bytes. Nothing is written to disk and the original file is untouched;
only the in-memory copy handed to the parser is rewritten.
"""

import io
import zipfile
from typing import Optional

STRICT_MARKER = b"purl.oclc.org/ooxml"

#: Strict -> transitional, longest first so no prefix rewrites another's stem.
_NAMESPACES = [
    (b"http://purl.oclc.org/ooxml/officeDocument/relationships",
     b"http://schemas.openxmlformats.org/officeDocument/2006/relationships"),
    (b"http://purl.oclc.org/ooxml/officeDocument/extendedProperties",
     b"http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"),
    (b"http://purl.oclc.org/ooxml/officeDocument/customProperties",
     b"http://schemas.openxmlformats.org/officeDocument/2006/custom-properties"),
    (b"http://purl.oclc.org/ooxml/officeDocument/docPropsVTypes",
     b"http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"),
    (b"http://purl.oclc.org/ooxml/officeDocument/math",
     b"http://schemas.openxmlformats.org/officeDocument/2006/math"),
    (b"http://purl.oclc.org/ooxml/spreadsheetml/main",
     b"http://schemas.openxmlformats.org/spreadsheetml/2006/main"),
    (b"http://purl.oclc.org/ooxml/wordprocessingml/main",
     b"http://schemas.openxmlformats.org/wordprocessingml/2006/main"),
    (b"http://purl.oclc.org/ooxml/presentationml/main",
     b"http://schemas.openxmlformats.org/presentationml/2006/main"),
    (b"http://purl.oclc.org/ooxml/drawingml/spreadsheetDrawing",
     b"http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"),
    (b"http://purl.oclc.org/ooxml/drawingml/wordprocessingDrawing",
     b"http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"),
    (b"http://purl.oclc.org/ooxml/drawingml/chart",
     b"http://schemas.openxmlformats.org/drawingml/2006/chart"),
    (b"http://purl.oclc.org/ooxml/drawingml/main",
     b"http://schemas.openxmlformats.org/drawingml/2006/main"),
]

_XML_SUFFIXES = (".xml", ".rels")


def relax_strict(data: bytes) -> Optional[bytes]:
    """Return the package with transitional namespaces, or None if not applicable.

    None means either the bytes are not a readable zip or no strict namespace was
    present, in which case the caller should keep whatever error it already had.
    """
    try:
        source = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, OSError):
        return None

    changed = False
    buffer = io.BytesIO()
    try:
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as target:
            for info in source.infolist():
                if info.is_dir():
                    continue
                try:
                    content = source.read(info)
                except (zipfile.BadZipFile, OSError, RuntimeError):
                    return None
                if info.filename.lower().endswith(_XML_SUFFIXES) and STRICT_MARKER in content:
                    for strict, transitional in _NAMESPACES:
                        if strict in content:
                            content = content.replace(strict, transitional)
                            changed = True
                target.writestr(info.filename, content)
    finally:
        source.close()

    return buffer.getvalue() if changed else None
