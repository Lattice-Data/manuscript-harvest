"""Acquisition stage: DOI -> article PDF + supplementary files on disk.

Open-access routes are tried first (Europe PMC, PMC OA, bioRxiv) because they
need no credentials and no page scraping. The authenticated-browser route
through the Stanford library proxy is the last resort.

Every fetch writes a manifest recording which tier produced each byte, so a
result can be traced back to its source the same way `audit/runs.jsonl` traces
an extraction back to a model and a prompt.
"""

__version__ = "0.1.0"
