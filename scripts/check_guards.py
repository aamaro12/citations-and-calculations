"""Remove selected material safeguards in isolated copies and require the regressions to notice.

This is targeted mutation coverage, not an exhaustive proof of all verifier behaviour.
"""

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
BASE = "src/citations_calculations/"
RULES = [
    (
        "source metadata binding",
        "store.py",
        '            if "s_" + digest(canonical(meta)) != sid:',
        "            if False:",
    ),
    (
        "snapshot hashes",
        "store.py",
        '        if digest(raw) != meta["raw_sha256"] or digest(extracted) != meta["extracted_sha256"]:',
        "        if False:",
    ),
    (
        "exact excerpt",
        "store.py",
        "        if not any(normal(excerpt) in normal(text) for text in readings(found[0])):",
        "        if False:",
    ),
    (
        "structured labels",
        "audit.py",
        "            if any(v != fact[field] for v in observed):",
        "            if False:",
    ),
    (
        "source scale",
        "audit.py",
        '        if decimal(fields["scale"]) != decimal(fact.get("scale", "1")):',
        "        if False:",
    ),
    (
        "workbook period",
        "audit.py",
        '        if not re.search(r"(?<!\\d)" + year + r"(?!\\d)", fact["period"]):',
        "        if False:",
    ),
    (
        "exact printed number and sign",
        "audit.py",
        '                    if expected not in [n for _, n in tokens(e["excerpt"])]:',
        "                    if False:",
    ),
    (
        "no cached spreadsheet input",
        "audit.py",
        '                    if e["unit"].get("context", {}).get("cached_formula"):',
        "                    if False:",
    ),
    ("support review verdict", "audit.py", '    if r["verdict"] != "supports":', "    if False:"),
    (
        "existing answer figure",
        "audit.py",
        '            if c.get("reported_result") is not None and decimal(c["reported_result"]) != decimal(result["rounded"]):',
        "            if False:",
    ),
    ("unbound answer number", "audit.py", r'            if re.search(r"\d", unbound):', "            if False:"),
    (
        "hidden numerical assumption",
        "numbers.py",
        "            if isinstance(node.value, bool) or node.value not in (0, 1, 100):",
        "            if False:",
    ),
    ("output dimensions", "numbers.py", "    if q.dims != unit(unit_name):", "    if False:"),
    ("unused calculation input", "numbers.py", "        if used != set(variables):", "        if False:"),
    ("pagination caveat", "acquire.py", "    if incomplete(envelope):", "    if False:"),
    (
        "chat literal escaping",
        "chat.py",
        '    text = re.sub(r"([\\\\`*_{}\\[\\]<>#|~&$])", r"\\\\\\1", text)',
        "    return text",
    ),
    ("blocked chat claim label", "chat.py", '        if c["status"] == "blocked":', "        if False:"),
    (
        "chat export source integrity",
        "chat.py",
        '        if digest(raw) != meta["raw_sha256"] or digest(canonical(units)) != meta["extracted_sha256"]:',
        "        if False:",
    ),
    ("chat note immutability", "chat.py", "            if path.read_bytes() != body:", "            if False:"),
    (
        "chat audit target binding",
        "chat.py",
        '    snapshot = inside(out, "chat/a_" + audit_sha)',
        '    snapshot = inside(out, "chat/latest")',
    ),
    ("nonempty review rationale", "audit.py", '        or not r["rationale"].strip()', "        or False"),
    ("review object shape", "audit.py", "        not isinstance(r, dict)", "        False"),
    (
        "failed claim evidence isolation",
        "audit.py",
        '                    "evidence": [],',
        '                    "evidence": c.get("evidence", []),',
    ),
    (
        "restyle source attribution",
        "scripts/refresh_notes.py",
        '                "title": e["source"]["title"],',
        '                "title": c["title"],',
    ),
    ("restyle citation ID uniqueness", "scripts/refresh_notes.py", ' or c["id"] in seen:', ":"),
    ("spelled-out numbers", "audit.py", "            if spelled:", "            if False:"),
    (
        "whole cited number",
        "audit.py",
        '                    if not any(expected in [n for _, n in tokens(text)] for text in readings(e["unit"])):',
        "                    if False:",
    ),
    (
        "scrambled PDF pages",
        "extract.py",
        "            if not text.strip() or scrambled(text):",
        "            if not text.strip():",
    ),
    (
        "unread pages make a source partial",
        "acquire.py",
        '    if (extracted.get("unread_pages") or scope) and completeness == "complete":',
        "    if False:",
    ),
    (
        "workbook cell limit",
        "extract.py",
        "                if len(units) + len(found) >= MAX_UNITS:",
        "                if False:",
    ),
    (
        "format that hides a sign",
        "extract.py",
        '    if not is_date_format(code) and (not re.search(r"\\d", text) or (value < 0 and not re.search(r"[-−(]", text))):',
        "    if False:",
    ),
    (
        "unbalanced bold stays literal",
        "chat.py",
        '        text = text.replace(BOLD, "**" if text.count(BOLD) % 2 == 0 else r"\\*\\*")',
        '        text = text.replace(BOLD, "**")',
    ),
    ("delivered figure label", "delivery.py", "            if label not in found:", "            if False:"),
    ("delivered source links", "delivery.py", "        elif path not in targets:", "        elif False:"),
    (
        "delivered numbers",
        "delivery.py",
        '    problems += ["Unchecked number: " + raw for raw, value in numbers(message) if value not in allowed]',
        "    problems += []",
    ),
    ("delivered audit is fresh", "delivery.py", "    if comparable(fresh) != comparable(stored):", "    if False:"),
]


def fixture(root):
    for part in ("src", "tests", "scripts"):
        shutil.copytree(ROOT / part, root / part, ignore=shutil.ignore_patterns("__pycache__"))


def suite(root):
    return subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(root / "src")},
        capture_output=True,
        text=True,
    )


def trial(rule):
    name, file, old, new = rule
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        fixture(root)
        target = root / file if "/" in file else root / BASE / file
        text = target.read_text()
        if text.count(old) != 1:
            return name, "missing pattern"
        mutated = text.replace(old, new)
        try:
            compile(mutated, str(target), "exec")
        except SyntaxError:
            return name, "invalid mutation"
        target.write_text(mutated)
        result = suite(root)
        return name, "caught" if result.returncode else "SURVIVED"


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        fixture(root)
        baseline = suite(root)
        if baseline.returncode:
            print("Isolated baseline failed; mutation results would be unreliable.", file=sys.stderr)
            print(baseline.stderr, file=sys.stderr)
            raise SystemExit(1)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(trial, RULES))
    for name, status in results:
        print(status, name)
    print(str(sum(s == "caught" for _, s in results)) + " of " + str(len(results)) + " selected safeguards caught")
    raise SystemExit(0 if all(s == "caught" for _, s in results) else 1)
