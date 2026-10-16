"""Use the same verifier through a console command or a bundled Codex script."""

import argparse
import json
from pathlib import Path
import sys

from .acquire import ingest, capture
from .audit import audit_file
from .store import Store
from . import __version__


def parser():
    p = argparse.ArgumentParser(
        prog="citecalc",
        description="Save sources, check the quotes in an answer against them and recompute its figures.",
    )
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)
    c = sub.add_parser("ingest", help="Save a copy of a file and index its passages")
    c.add_argument("--workspace", required=True)
    c.add_argument("--file", required=True)
    c.add_argument("--format")
    c.add_argument("--title")
    c.add_argument("--url")
    c.add_argument("--published-at")
    c.add_argument("--completeness", choices=["complete", "partial", "unknown"], default="complete")
    c = sub.add_parser("fetch", help="Save a public web page or document")
    c.add_argument("--workspace", required=True)
    c.add_argument("--url", required=True)
    c.add_argument("--title")
    c = sub.add_parser("capture", help="Save a connected tool's response, with its query and whether it was complete")
    c.add_argument("--workspace", required=True)
    c.add_argument("--file", required=True)
    c = sub.add_parser("read", help="List a saved source's matching passages with their exact locations")
    c.add_argument("--workspace", required=True)
    c.add_argument("--source", required=True)
    c.add_argument("--find", default="")
    c.add_argument("--limit", type=int, default=30)
    for command, help_text in (
        ("audit", "Recheck every quote and recompute every figure; print the result as JSON"),
        ("answer", "Run the same audit and print only the chat answer"),
    ):
        c = sub.add_parser(command, help=help_text)
        c.add_argument("--workspace", required=True)
        c.add_argument("--bundle", required=True)
        c.add_argument("--output", required=True)
    c = sub.add_parser("demo", help="Run the whole workflow offline on the synthetic example")
    c.add_argument("--output", default="runs/demo")
    return p


def main(argv=None):
    p = parser()
    a = p.parse_args(argv)
    try:
        if a.command == "ingest":
            result = ingest(a.workspace, a.file, a.title, a.url, a.format, a.completeness, a.published_at)
        elif a.command == "fetch":
            from .web import fetch

            result = fetch(a.workspace, a.url, a.title)
        elif a.command == "capture":
            result = capture(a.workspace, a.file)
        elif a.command == "read":
            result = Store(a.workspace).search(a.source, a.find, a.limit)
        elif a.command in ("audit", "answer"):
            result = audit_file(a.workspace, a.bundle, a.output)
        else:
            from .demo import run

            result = run(Path(a.output))
        if a.command == "answer":
            print(result["chat"]["answer_markdown"], end="")
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if result.get("status") == "blocked" else 0
    except (ValueError, OSError, UnicodeError, RecursionError) as e:
        print(json.dumps({"status": "error", "message": str(e)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
