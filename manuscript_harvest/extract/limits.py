"""Every cap the extractor applies, in one place.

Caps exist because the corpus contains a 16,596 x 88 spreadsheet, a 72 MB zip,
and a 487 MB gzip. Nothing here silently truncates: whatever a cap drops is
recorded in the extraction manifest and in the affected table card, so a thin
result reads as "capped" rather than "there was nothing there".
"""

from dataclasses import asdict, dataclass


@dataclass
class Limits:
    # -- tables
    max_scan_rows: int = 5000
    """Rows read per sheet/table when profiling. Beyond this the card says so."""
    max_columns: int = 300
    max_header_scan_rows: int = 20
    """How far down to look for the header row. Real worst case seen: row 4."""
    max_unique_values: int = 25
    """A column with at most this many distinct values gets all of them listed.
    That listing is the point of the whole card: a column whose two values are
    {M, F} answers "sex" outright."""
    max_unique_numeric_values: int = 12
    """Numeric columns get a lower bar. Enumerating `0 | 6 | 24` says "these are
    the timepoints"; enumerating 22 patient ages says nothing the range does not,
    at ten times the length."""
    max_value_chars: int = 60
    """Longer cell values are not enumerated as a value set; they get examples."""
    max_sample_rows: int = 3
    """Example rows printed under the column profile. Three is enough to show the
    shape of a value -- an accession, a date format, a free-text note -- without the
    card turning into a copy of the data it is describing."""
    max_rendered_columns: int = 60
    max_card_chars: int = 4000
    """Budget for one rendered card. A card is meant to fit in a prompt beside
    thirty others, so a single 88-column sheet must not be able to crowd them out;
    whatever this drops is counted in the card's notes rather than trimmed away
    quietly."""

    # -- files
    max_sheets: int = 30
    max_tables_per_file: int = 60
    max_tables_per_sheet: int = 20
    """One sheet can hold many blank-row-separated panels: `Figure 6` of
    10.1126/sciimmunol.aba4163's data file holds ten. Beyond this the sheet's
    later tables are counted in `tables_skipped` rather than dropped quietly."""
    max_blocks_per_file: int = 20000
    max_file_mb: int = 200
    min_paragraph_chars: int = 2
    max_paragraph_chars: int = 20000
    """No real paragraph is this long. A `.txt` data dump with no blank lines is
    one 23 MB "paragraph" otherwise -- observed on
    10.1126/science.aax6234's supplement TableS8.txt."""
    min_html_block_chars: int = 80
    """Landing pages are mostly navigation; short fragments are chrome."""
    min_landing_chars: int = 1000
    """A saved page with no citation metadata and less text than this is an
    interstitial, not an article. Nine Elsevier landing pages in this corpus hold
    129 characters: the browser's own user-agent string."""

    # -- archives
    max_archive_members: int = 25
    """Files read out of one zip. A supplementary archive holding more than this is
    a dataset rather than a set of tables, and the overflow is recorded."""
    max_member_mb: int = 50
    """Per file inside a zip, separate from `max_file_mb` for the archive itself:
    one 500 MB member should not be read because its container was small."""
    max_archive_depth: int = 2
    """Three of this corpus's zips contain only more zips, so one level of
    nesting has to be followed or those supplements read as empty."""

    # -- main text
    min_main_text_chars: int = 2000
    """Below this a JATS extraction is treated as too thin and the PDF is used
    instead. Some deposited XML carries only front matter."""
    # -- review
    max_review_cards_per_article: int = 25
    """Low-confidence table cards queued for a human per article. Beyond this the
    overflow is counted in `review.queue_truncated` rather than dropped quietly:
    "there were 25 to check" and "there were 25 of 60" are different facts."""

    min_pdf_text_chars: int = 200
    """Matches manuscript_harvest.fetch.validate: less than this means scanned images."""
    ocr_dpi: int = 300
    """What a scanned page is rendered at before tesseract reads it.

    Tesseract's own documentation asks for about 300 dpi for 10-point text, and
    PyMuPDF's `get_textpage_ocr` default of 72 is the screen resolution rather than a
    reading one. The *accuracy* of 300 here is not measured: the binary is not
    installed on the machine this pass was written on, so this is the conventional
    figure and not a tuned one.

    The cost of it is measured. Rendering all 245 scanned pages in this corpus at
    300 dpi takes 19.7 s -- 80 ms a page -- and the largest single pixmap is 54 MB,
    which is a full-page figure panel and is transient. So the render half of the
    pass is not what a change here would be trading against; tesseract's own time is.

    Here rather than a module constant so that tuning it is a config edit which
    lands in every extraction's `limits` block and moves the extraction key, which
    is what `extraction_key` exists for."""
    max_ocr_pages: int = 25
    """Pages in one file this stage will OCR. Beyond it the file keeps
    `no_text_scanned_pdf` and the reason says so.

    Measured: the 70 scanned supplements in this corpus are 245 pages between them,
    median 3, longest 11. This is twice that worst case, so nothing here is near it,
    and what it stops is the shape that is not a supplementary table -- a scanned
    88-page peer-review bundle, which at 300 dpi would cost more than the article it
    belongs to."""
    max_unnamed_glyph_fraction: float = 0.25
    """How much of a PDF's text may be glyphs the document never says the meaning
    of before the file stops being `ok`. See `pdf._repair_glyph_encoding` for what
    produces them.

    Set between the two populations rather than at a round number. Measured over
    the 972 PDFs in this corpus after the repair: 767 sit at exactly zero, the
    highest readable file is 0.1805 -- 10.1016/j.cell.2024.08.019's mmc8, whose
    figure axis labels are damaged while its captions are not -- and the one file
    that genuinely cannot be read sits at 1.0. Nothing lands between 0.19 and
    0.99, so the bar is inside a gap five times its own width rather than on a
    slope."""
    running_header_min_pages: int = 3
    """A short line repeated in a page margin on this many pages is a running
    head, not content."""
    max_bounded_section_chars: int = 6000
    """How far a heading that names a *statement* -- abstract, conclusions, data
    availability -- may carry before `SectionTracker` abandons it.

    Chosen against measurement rather than taste. The longest legitimate run seen
    over the ground-truth papers is 4,653 characters (a Cell Press abstract plus
    its highlights and eTOC blurb, 10.1016/j.xgen.2026.101304); the shortest
    pathological one is 6,294. This sits between them. It decided a third of one
    article's labels while being a module constant with no config key -- living
    here is also what gets it recorded in every extraction's `limits` block, so a
    label can be read back against the bound that produced it."""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, values) -> "Limits":
        """Build from a config mapping, ignoring keys that are not caps."""
        known = {f for f in cls().to_dict()}
        return cls(**{k: v for k, v in (values or {}).items() if k in known})
