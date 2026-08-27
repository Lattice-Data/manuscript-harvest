"""Command line for the panel.

    manuscript-ui                       # then open the URL it prints
    manuscript-ui --port 9000
    manuscript-ui --config /etc/harvest.yaml --corpus-dir /data/corpus

Prints one line and stays in the foreground. It opens no browser: the URL carries
a one-run secret, and handing it to whichever browser the desktop happens to
consider default is a decision for the person reading the line, not for this
process.

The panel resolves `config.yaml` and the corpus directory exactly as
`manuscript-fetch` does, by calling that CLI's own loader -- so a panel started
from the wrong directory reports the same fallback-to-defaults note on stderr that
a fetch run would, and shows the resolved paths in its header. That warning is the
one in `config.py`, and it is worth reading: this is a long-running process, and
being wrong about where the corpus is for an hour is worse than being wrong about
it for one command.
"""

import argparse
import sys
from pathlib import Path

from ..extract.cli import load_config as load_extract_config
from ..fetch.cli import load_config as load_fetch_config
from . import server


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="manuscript-ui",
        description="A local control panel for the fetch and extract stages.",
    )
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--port", type=int, default=8787,
                        help="loopback port (default 8787; 0 picks a free one)")
    parser.add_argument("--corpus-dir", default=None,
                        help="override the corpus directory from the config file. "
                             "Passed on to every command the panel runs")
    parser.add_argument("--root", default=None,
                        help="directory the panel runs commands in, and looks in for "
                             "DOI lists (default: the current directory)")
    return parser


def resolve_corpus_dir(config_path, corpus_dir_override) -> Path:
    """Where the corpus is, answered the way the extract stage answers it.

    `extract.cli.load_config` is the one that already reconciles the two stages --
    `extract.corpus_dir` falls back to `fetch.corpus_dir` so that moving a corpus
    takes one edit rather than two that can drift. Reusing it means the panel's
    header cannot disagree with what `manuscript-extract all` will read.
    """
    if corpus_dir_override:
        return Path(corpus_dir_override).expanduser()
    extract_cfg = load_extract_config(config_path).get("extract") or {}
    return Path(extract_cfg.get("corpus_dir") or "corpus").expanduser()


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.root).expanduser() if args.root else Path.cwd()
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 2

    # The fetch config, for the header's chips: tiers, keys, the proxy session.
    config = load_fetch_config(args.config)
    corpus_dir = resolve_corpus_dir(args.config, args.corpus_dir)

    panel = server.Panel(
        root=root,
        config_path=args.config,
        config=config,
        corpus_dir=corpus_dir,
        corpus_dir_override=args.corpus_dir,
        port=args.port,
    )
    try:
        httpd = server.serve(panel)
    except OSError as e:
        print(f"error: cannot listen on 127.0.0.1:{args.port}: {e}\n"
              "       Something else may be using that port -- try --port 0 for any "
              "free one.", file=sys.stderr)
        return 2

    # Flushed explicitly. This one line is the entire point of the process, and
    # stdout is block-buffered whenever it is not a terminal -- so `manuscript-ui |
    # tee panel.log`, or any redirection, would otherwise hold the URL back until
    # the buffer filled or the panel exited.
    print(f"manuscript-harvest panel\n"
          f"  corpus  {corpus_dir}\n"
          f"  config  {Path(args.config).expanduser()}\n"
          f"  runs in {root}\n\n"
          f"Open this, and keep it to yourself -- the token in it is this run's key:\n\n"
          f"  http://127.0.0.1:{panel.port}/?t={panel.token}\n\n"
          f"Ctrl-C here shuts the panel down. It does not stop a job: jobs run in "
          f"their own\nprocess group, and the Stop button on the page is what "
          f"signals them.", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting the panel down.", file=sys.stderr)
        if panel.runner.busy():
            # Said rather than done. Killing a running fetch because the panel is
            # closing would leave downloaded bytes with no manifest entry, and
            # nobody asked for that: the job runs in its own process group
            # precisely so that this Ctrl-C does not reach it.
            current = panel.runner.current
            pid = current.proc.pid if current.proc else None
            print(f"note: {current.label} is still running and keeps going.\n"
                  + (f"      It is its own process group, so to stop it from here:\n"
                     f"          kill -INT -{pid}\n" if pid else "")
                  + "      Or start the panel again -- but a new panel does not adopt "
                    "an old job,\n      so it will not show you this one's progress.",
                  file=sys.stderr)
    finally:
        httpd.server_close()
        left = server.cleanup(panel)
        if left:
            print(f"note: left {left} in place; the running job is writing there.",
                  file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
