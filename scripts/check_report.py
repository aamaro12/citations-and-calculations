"""Static report checks and mocked DOM interactions; no browser rendering."""

from html.parser import HTMLParser
import json
from pathlib import Path
import subprocess
import sys
import tempfile


class Document(HTMLParser):
    def __init__(self):
        super().__init__()
        self.nodes = []
        self.scripts = []
        self.script = False

    def handle_starttag(self, tag, attrs):
        self.nodes.append({"tag": tag, "attrs": dict(attrs)})
        if tag == "script":
            self.script = True
            self.scripts.append("")

    def handle_endtag(self, tag):
        if tag == "script":
            self.script = False

    def handle_data(self, text):
        if self.script:
            self.scripts[-1] += text


def check(path, node="node"):
    path = Path(path)
    html = path.read_text()
    doc = Document()
    doc.feed(html)
    ids = [n["attrs"]["id"] for n in doc.nodes if "id" in n["attrs"]]
    assert len(ids) == len(set(ids)), "Duplicate IDs"
    assert len(doc.scripts) == 1, "Unexpected source script in report"
    for n in doc.nodes:
        href = n["attrs"].get("href")
        if not href:
            continue
        if href.startswith("#"):
            assert href[1:] in ids, "Broken input/source anchor: " + href
        elif not href.startswith(("https://", "http://")):
            assert (path.parent / href).is_file(), "Missing export: " + href
    assert not any(n["tag"] in ("iframe", "img") for n in doc.nodes), "Report must not load third-party content"
    data = json.loads((path.parent / "audit.json").read_text())
    claims = [n for n in doc.nodes if "data-claim" in n["attrs"]]
    assert len(claims) == len(data["claims"])
    fixture = {
        "claims": [{"id": c["id"], "kind": c["kind"], "text": c["rendered_text"]} for c in data["claims"]],
        "script": doc.scripts[0],
    }
    with tempfile.TemporaryDirectory() as td:
        file = Path(td) / "ui.json"
        file.write_text(json.dumps(fixture))
        result = subprocess.run(
            [node, str(Path(__file__).with_name("ui_check.js")), str(file)], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
    return {
        "checks": "passed",
        "scope": "Static report/source-link checks and mocked DOM interactions; no browser rendering",
        "claims": len(claims),
        "ui": result.stdout.strip(),
    }


if __name__ == "__main__":
    print(json.dumps(check(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "node"), indent=2))
