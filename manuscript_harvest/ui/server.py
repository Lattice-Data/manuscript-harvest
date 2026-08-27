"""The local HTTP surface: loopback, one token, no framework.

Stdlib `ThreadingHTTPServer` and the hand-written page in `page.py`, for the reason
`extract/reviewsheet.py` gives about its own HTML: this repository has no web
framework and does not need one to serve one document and five JSON endpoints.

**Three guards, and each answers a specific way a local panel that spawns
subprocesses can be driven by something that is not the person who started it.**
This process runs a browser, holds a library session and can delete a corpus, so
the fact that it listens only on loopback is where the reasoning starts, not where
it ends -- a browser on this machine will happily carry a request from a page on
the internet to 127.0.0.1.

`Host` must name loopback and this port. This is the DNS-rebinding guard: a page
can point its own hostname at 127.0.0.1 and then talk to this server as
*same-origin*, which defeats the browser's cross-origin read protection and would
otherwise let it read the token straight out of the page. It cannot forge `Host`.

`Origin`, when the browser sends one, must be this server. A form on another page
POSTing here arrives with a foreign origin.

The token is a per-run secret, required on every `/api/` call in a custom header.
Custom headers cannot be set cross-origin without a CORS preflight, which this
server never answers, so the header requirement alone stops the simple form-POST
case; the secret covers whatever gets past it.

Deliberately absent: any way to bind an address other than loopback. There is no
flag for it, because there is no version of this that should be reachable from a
network.
"""

import json
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .. import __version__
from . import jobs, page, state

# A pasted DOI list is the largest thing a client legitimately sends. 2000 DOIs is
# about 60 KB; 4 MB is far above that and bounded, so a request cannot make the
# panel read an arbitrary amount into memory.
MAX_BODY_BYTES = 4 * 1024 * 1024

# What a pasted list is written to inside the panel's own temporary directory.
PASTED_DOIS_NAME = "pasted.dois"


class Panel:
    """Everything a request needs: where things are, what is running, and the token."""

    def __init__(self, root, config_path, config, corpus_dir, *,
                 corpus_dir_override=None, port=0, token=None):
        self.root = Path(root)
        self.config_path = Path(config_path)
        self.config = config
        self.corpus_dir = Path(corpus_dir)
        # Only forwarded to the CLIs when the panel was started with an explicit
        # override, so that otherwise the config file remains the single answer to
        # where the corpus is rather than the panel restating it per command.
        self.corpus_dir_override = corpus_dir_override
        self.port = port
        self.token = token or secrets.token_urlsafe(32)
        self.snapshots = state.SnapshotCache(self.corpus_dir)
        self.runner = jobs.JobRunner(on_finish=self._job_finished)

    def _job_finished(self, job) -> None:
        # A run that just added twenty papers must not leave a header showing the
        # count from before it.
        self.snapshots.invalidate()

    # -- payloads -----------------------------------------------------------

    def state_payload(self) -> dict:
        history = self.runner.history()
        last_check = next((j for j in history if j["kind"] == "check"), None)
        return {
            "version": __version__,
            "port": self.port,
            "root": str(self.root),
            "corpus": self.snapshots.get(),
            "health": state.health(self.config_path, self.config, self.corpus_dir),
            "doi_files": state.doi_list_files(self.root),
            "busy": self.runner.busy(),
            "history": history,
            "last_check": last_check,
        }

    def resolve_dois(self, body: dict) -> tuple:
        """Turn a request's DOI source into (text, path_or_None, label).

        A request says what the DOIs *are*, or names one of the files the panel
        itself found in its own directory. It never gets to hand over a path: a
        pasted list is written into the panel's temporary directory, and a named
        file is matched against `doi_list_files` by name, so nothing here opens a
        path a client composed.
        """
        source = body.get("source")
        if source == "file":
            name = str(body.get("name") or "")
            for candidate in state.doi_list_files(self.root):
                if candidate["name"] == name:
                    path = Path(candidate["path"])
                    return path.read_text(encoding="utf-8", errors="replace"), path, name
            raise ValueError(f"{name!r} is not one of the DOI lists in {self.root}")
        if source == "text":
            text = body.get("text")
            if not isinstance(text, str) or not text.strip():
                raise ValueError("no DOIs given")
            if len(text) > MAX_BODY_BYTES:
                raise ValueError("that list is too long")
            return text, None, "pasted DOIs"
        raise ValueError("say source: file or text")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = f"manuscript-harvest-ui/{__version__}"
    panel: Panel = None

    # -- plumbing -----------------------------------------------------------

    def log_message(self, fmt, *args):
        """Quiet. The one line this process prints is the URL to open."""

    def handle_one_request(self):
        """As inherited, but a dropped connection is not an incident.

        `protocol_version = "HTTP/1.1"` means connections are kept alive, so every
        browser that navigates away, reloads, or closes the tab leaves a socket
        that resets on the next read. Unhandled, each one prints a traceback
        through `socketserver.handle_error` -- into the same terminal whose only
        useful line is the URL. The page polls twice a second; this would bury it.
        """
        try:
            super().handle_one_request()
        except (ConnectionResetError, BrokenPipeError):
            self.close_connection = True

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        # No referrer, so nothing this page links to ever learns the panel's URL --
        # which until the script strips it carries the token.
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'unsafe-inline'; "
            "script-src 'unsafe-inline'; connect-src 'self'; "
            "form-action 'none'; base-uri 'none'",
        )
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, status: int = 200) -> None:
        self._send(status, json.dumps(obj).encode("utf-8"), "application/json")

    def _error(self, status: int, message: str) -> None:
        self._json({"error": message}, status=status)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY_BYTES:
            raise ValueError("request too large")
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ValueError("body is not JSON")
        if not isinstance(parsed, dict):
            raise ValueError("body is not a JSON object")
        return parsed

    # -- guards -------------------------------------------------------------

    def _allowed_hosts(self) -> set:
        port = self.panel.port
        return {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}

    def _guard(self, path: str, query: dict):
        """None when the request may proceed, else the refusal to send."""
        host = (self.headers.get("Host") or "").strip()
        if host not in self._allowed_hosts():
            # See the module docstring: this is the DNS-rebinding guard, and it is
            # the reason a foreign page cannot read the token out of the document.
            return 403, "this panel answers only to 127.0.0.1 by its own port"
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).netloc not in self._allowed_hosts():
            return 403, "cross-origin request refused"
        if path.startswith("/api/"):
            supplied = self.headers.get("X-Harvest-Token") or ""
        else:
            supplied = (query.get("t") or [""])[0]
        # Compared as bytes: `compare_digest` raises TypeError on a str holding
        # anything outside ASCII, and a header is whatever the caller put in it.
        if not secrets.compare_digest(supplied.encode("utf-8", "replace"),
                                      self.panel.token.encode("utf-8")):
            return 403, "wrong or missing token: open the URL the panel printed"
        return None

    # -- routes -------------------------------------------------------------

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        refusal = self._guard(parsed.path, query)
        if refusal:
            return self._error(*refusal)

        if parsed.path == "/":
            body = page.render(self.panel.token).encode("utf-8")
            return self._send(200, body, "text/html; charset=utf-8")
        if parsed.path == "/api/state":
            return self._json(self.panel.state_payload())
        if parsed.path == "/api/job":
            try:
                cursor = int((query.get("cursor") or ["0"])[0])
            except ValueError:
                cursor = 0
            return self._json(self.panel.runner.snapshot(max(0, cursor)))
        return self._error(404, "no such path")

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        refusal = self._guard(parsed.path, parse_qs(parsed.query))
        if refusal:
            return self._error(*refusal)
        try:
            body = self._body()
        except ValueError as e:
            return self._error(400, str(e))

        if parsed.path == "/api/preflight":
            return self._preflight(body)
        if parsed.path == "/api/run":
            return self._run(body)
        if parsed.path == "/api/stop":
            stopped = self.panel.runner.stop(force=bool(body.get("force")))
            return self._json({"stopped": stopped})
        return self._error(404, "no such path")

    def _preflight(self, body: dict):
        try:
            text, _path, _label = self.panel.resolve_dois(body)
        except (ValueError, OSError) as e:
            return self._error(400, str(e))
        return self._json(state.preflight(self.panel.corpus_dir, text))

    def _run(self, body: dict):
        panel = self.panel
        kind = str(body.get("kind") or "")
        if kind not in jobs.COMMANDS:
            return self._error(400, f"unknown command {kind!r}")
        options = body.get("options")
        if options is not None and not isinstance(options, dict):
            return self._error(400, "options must be an object")
        options = options or {}
        applying = bool(options.get("apply"))

        # The typed confirmation is checked here as well as on the page. A button
        # that is disabled in the DOM is a courtesy; this is the check.
        if kind in jobs.DESTRUCTIVE and applying:
            if str(body.get("confirm") or "").strip() != "delete":
                return self._error(400, "type delete to confirm this")

        target = None
        label = kind
        if kind == "fetch":
            try:
                text, path, label_source = panel.resolve_dois(body)
            except (ValueError, OSError) as e:
                return self._error(400, str(e))
            if path is None:
                path = panel.runner.scratch_file(PASTED_DOIS_NAME, text)
            target = path
            counted = state.preflight(panel.corpus_dir, text)["counts"]
            label = f"fetch {counted['total']} DOIs from {label_source}"
        elif kind == "extract-one":
            target = str(body.get("target") or "").strip()
            if not target:
                return self._error(400, "name a DOI or slug to extract")
            # Nothing here can inject a command -- argv is a list and no shell is
            # involved -- but a target beginning with a dash is read by argparse as
            # a flag, which turns a typo into "article is required" a second after
            # the page said the job started. Refused here, where it can be said.
            if len(target) > 300 or "\n" in target or target.startswith("-"):
                return self._error(400, "that does not look like a DOI or slug")
            label = f"extract {target}"
        elif kind == "extract-all":
            label = "extract all"
        elif kind == "prune" and not applying:
            label = "prune (preview)"
        elif kind in jobs.DESTRUCTIVE:
            label = f"{kind}{'' if applying else ' (preview)'}"

        progress_path = None
        if jobs.COMMANDS[kind].get("progress"):
            progress_path = panel.runner.progress_dir() / f"progress-{kind}.jsonl"

        try:
            argv = jobs.build_argv(
                kind,
                config_path=panel.config_path,
                corpus_dir=panel.corpus_dir_override,
                options=options,
                progress_path=progress_path,
                target=target,
            )
        except ValueError as e:
            return self._error(400, str(e))

        try:
            job = panel.runner.start(
                kind, argv, panel.root, label=label,
                progress_path=progress_path,
                destructive=kind in jobs.DESTRUCTIVE,
            )
        except jobs.Busy as e:
            # 409, not 400: the request was fine, the panel is busy. One job at a
            # time is a rule about publisher request rates and about two runs
            # racing on one `extracted/` directory -- see `jobs`.
            return self._error(409, str(e))
        return self._json({"job": job.summary(0)})


def serve(panel: Panel) -> ThreadingHTTPServer:
    """Bind and return the server. Loopback only, and no flag says otherwise."""
    handler = type("BoundHandler", (Handler,), {"panel": panel})
    httpd = ThreadingHTTPServer(("127.0.0.1", panel.port), handler)
    # Ask for port 0 and the real one is only known now; the guards and the printed
    # URL both need it.
    panel.port = httpd.server_address[1]
    return httpd


def cleanup(panel: Panel):
    """Remove the panel's temporary directory, and say so if a job still needs it."""
    return panel.runner.cleanup()
