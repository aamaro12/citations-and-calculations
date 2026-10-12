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
        prog="citecalc", description="Snapshot sources, anchor citations and recompute numerical answers."
    )
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)
    c = sub.add_parser("ingest", help="Snapshot an uploaded document or a saved web response")
    c.add_argument("--workspace", required=True)
    c.add_argument("--file", required=True)
    c.add_argument("--format")
    c.add_argument("--title")
    c.add_argument("--url")
    c.add_argument("--published-at")
    c.add_argument("--completeness", choices=["complete", "partial", "unknown"], default="complete")
    c = sub.add_parser("fetch", help="Snapshot a public web page or document; redirects and size are bounded")
    c.add_argument("--workspace", required=True)
    c.add_argument("--url", required=True)
    c.add_argument("--title")
    c = sub.add_parser("capture", help="Preserve a connected tool response envelope")
    c.add_argument("--workspace", required=True)
    c.add_argument("--file", required=True)
    c = sub.add_parser("read", help="Read/search a snapshot and list exact citation locators")
    c.add_argument("--workspace", required=True)
    c.add_argument("--source", required=True)
    c.add_argument("--find", default="")
    c.add_argument("--limit", type=int, default=30)
    for command, help_text in (
        ("audit", "Recheck evidence and arithmetic; return a chat answer, citation map and review paths"),
        ("answer", "Re-audit and print the checked answer with clickable links for Codex chat"),
    ):
        c = sub.add_parser(command, help=help_text)
        c.add_argument("--workspace", required=True)
        c.add_argument("--bundle", required=True)
        c.add_argument("--output", required=True)
    c = sub.add_parser(
        "demo", help="A complete offline example with documents, a web capture and a connected-tool capture"
    )
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
