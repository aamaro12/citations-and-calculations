"""Check a delivered chat message against the checked answer it was written from."""

import json
from pathlib import Path
import re

from .audit import verify
from .chat import render_chat
from .numbers import tokens
from .store import EvidenceError, canonical, digest

LINK = re.compile(r"(?<!\\)\[((?:\\.|[^\]\\\n])*)\]\((?:<([^>\n]+)>|([^)\s]+))\)")
AUDIT = re.compile(r"(.*)/chat/(a_[a-f0-9]{64})/[^/]+")


def unescape(text):
    return re.sub(r"\\([^\w\s])", r"\1", text)


def resolve(target, base):
    target = re.sub(r"^file://", "", target)
    target = re.sub(r"(?::\d+){1,2}$|#L\d+(?:C\d+)?$", "", target)
    path = Path(target)
    return (path if path.is_absolute() else Path(base) / path).resolve()


def links(text, base):
    return [(unescape(m[1]), resolve(m[2] or m[3], base)) for m in LINK.finditer(text)]


def numbers(text):
    """Every number a reader sees: link labels count, link targets do not."""
    return tokens(unescape(LINK.sub(lambda m: " " + m[1] + " ", text)))


def comparable(result):
    return canonical({k: v for k, v in result.items() if k != "audited_at"})


def check_answer(workspace, bundle, message, base="."):
    """Compare a final message with a fresh audit; prose may change, checked figures and links may not."""
    delivered = links(message, base)
    audits = {m.groups() for _, path in delivered if (m := AUDIT.fullmatch(str(path)))}
    if len(audits) != 1:
        return {
            "status": "changed",
            "problems": [f"The message must link to the pages of 1 audit; it links to {len(audits)}"],
        }
    out, name = audits.pop()
    try:
        stored = json.loads((Path(out) / "chat" / name / "audit.json").read_bytes())
    except (OSError, ValueError) as e:
        raise EvidenceError("The linked audit is missing or unreadable") from e
    if "a_" + digest(canonical(stored)) != name:
        raise EvidenceError("Saved audit content does not match its directory")
    fresh = verify(workspace, json.loads(Path(bundle).read_bytes()))
    if comparable(fresh) != comparable(stored):
        problem = "The linked audit no longer matches a fresh audit of the bundle; run answer again and use its text"
        return {"status": "changed", "problems": [problem]}
    # The fresh result keeps the bundle's order, so the pages render exactly as they were saved.
    payload = render_chat(out, {**fresh, "audited_at": stored["audited_at"]})
    problems = []
    targets = {}
    for label, path in delivered:
        targets.setdefault(path, []).append(label)
    for label, path in links(payload["answer_markdown"], "/"):
        if re.fullmatch(r"(?:fact|calc)-.+\.md", path.name):
            found = targets.get(path, [])
            if label not in found:
                problems.append(
                    "Figure " + label + " must keep its link to " + path.name
                    + (" (found " + ", ".join(found) + ")" if found else "")
                )  # fmt: skip
        elif path not in targets:
            problems.append(("Missing source link: " if path.name.startswith("source-") else "Missing link: ") + label)
    allowed = {value for _, value in numbers(payload["answer_markdown"])}
    problems += ["Unchecked number: " + raw for raw, value in numbers(message) if value not in allowed]
    if payload["status"] == "blocked" and "draft: checks failed" not in message.casefold():
        problems.append("A blocked answer must keep its warning: Draft: checks failed")
    problems = list(dict.fromkeys(problems))
    return {"status": "changed" if problems else "passed", "problems": problems}
