"""A local control panel for the two stages: buttons, a DOI-list picker, progress.

Not a fourth stage. Everything here is a front end over the three command lines,
and the boundary is deliberate and narrow:

- It **spawns** `manuscript-fetch` and `manuscript-extract` as subprocesses. It
  never imports `fetcher` or `extractor` to do their work in-process, so there is
  no second implementation of a fetch that could drift from the tested one.
- It **reads** `manifest.json` and `extraction.json` for every number it shows, via
  `store.read_manifest` and `extractor.read_extraction` -- the readers those
  modules define -- rather than parsing the log lines a run prints.
- It **writes** nothing into the corpus. The only files it creates are its own
  temporary heartbeat files and any DOI list pasted into the page.

`jobs.py` has the argument for the subprocess boundary, `state.py` for reading the
records instead of the log, and `server.py` for the three guards that keep a page
on the internet from driving a process that holds a library session.
"""
