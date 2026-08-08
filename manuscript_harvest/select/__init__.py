"""Selection stage: blocks of text -> the evidence one question needs.

The two stages before this one end at `blocks.jsonl`: every paragraph, caption and
table card an article yielded, in document order, each carrying the file and
location it came from. What was missing was any way to *ask* it something. A
question -- which GEO accessions did this paper deposit, how many donors, what
assays -- wants a few dozen of an article's several hundred blocks, and
`manuscript-extract show` is a human pager with no machine-readable output and a
`--limit` that defaults to 20.

This stage is that missing surface, and it stops short of answering. Four jobs,
all of them deterministic and none of them needing a model:

1. **readiness** -- can a *negative* answer from this article be believed at all?
   An article whose main text is a saved landing page has no Methods to have
   missed an accession in. This is the guard that makes "none found" mean
   something.
2. **query** -- filter and rank blocks, and pack them to a character budget while
   recording what the budget dropped.
3. **candidates** -- find the things a regex finds better than a model does.
   Accession numbers are the clearest case: the finder gets essentially every one,
   and gets the *role* of every one wrong, which is exactly the split worth making
   in code.
4. **verify** -- check a quote against the block it claims to come from, so an
   answer citing text that is not there is caught rather than shipped.

Deliberately absent, and staying absent: no model client, no prompts, no schemas,
no scoring rubric. Everything here is testable without a network and without a
model, which is the line this package draws. What a question *asks*, and with
what, belongs to whatever consumes these packs.
"""

__version__ = "0.1.0"
