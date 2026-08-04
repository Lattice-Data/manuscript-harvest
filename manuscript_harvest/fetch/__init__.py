"""Acquisition stage: DOI -> article PDF + supplementary files on disk.

Open-access routes are tried first (Europe PMC, PMC supplements, PMC OA, bioRxiv)
because they need no credentials and no browser. Two of them do scrape a rendered
page -- `pmc_supplements` regexes PMC's HTML for `/bin/` paths and bioRxiv regexes
its supplement page -- which is why those return `fetched_unverified` rather than
`fetched`: see `fetcher`. The authenticated-browser route through the Stanford
library proxy is the last resort.

Every fetch writes a manifest recording which tier produced each byte, so any
later claim about a paper can be traced back to the file it came from and the
route that retrieved it.
"""

__version__ = "0.1.0"
