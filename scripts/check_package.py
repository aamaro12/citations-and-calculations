"""Validate package structure and scan only publishable files, never local environments or runs."""

import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def check(root=ROOT):
    root = Path(root)
    portable = json.loads((root / "plugin.json").read_text())
    overlay = json.loads((root / ".codex-plugin/plugin.json").read_text())
    assert portable["name"] == overlay["name"] == "citations-and-calculations"
    assert portable["version"] == overlay["version"]
    assert portable["$schema"] == "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
    assert not {"skills", "mcpServers", "apps", "interface"} & set(portable)
    interface = portable["extensions"]["com.openai"]["interface"]
    assert len(interface["shortDescription"]) <= 30
    assert interface["displayName"] == "Citations & Calculations"
    skill = root / "skills/cite-and-calculate/SKILL.md"
    assert skill.is_file()
    assert (root / "scripts/citecalc.py").is_file()
    assert (root / "src/citations_calculations/cli.py").is_file()
    for link in re.findall(r"\]\(([^)]+)\)", skill.read_text()):
        if not link.startswith("http"):
            assert (skill.parent / link.split("#")[0]).is_file(), link
    process = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    patterns = {
        "personal path": re.compile(r"/Users/(?!Shared\b)[A-Za-z0-9._-]+|/home/[A-Za-z0-9._-]+/"),
        "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
        "credential token": re.compile(
            r"\b(?:sk|ghp|gho|ghu|ghs|ghr)[_-][A-Za-z0-9_-]{25,}|github_pat_[A-Za-z0-9_]{30,}"
        ),
        "credential URL": re.compile(r"(?i)[?&](?:apikey|api_key|access_token|token|signature)=[A-Za-z0-9._-]{20,}"),
    }
    findings = []
    files = 0
    for name in process.stdout.splitlines():
        p = root / name
        if p.is_symlink():
            findings.append(name + ": symbolic link is not part of a portable package")
            continue
        if not p.is_file():
            continue
        files += 1
        if p.name.startswith(".env"):
            findings.append(name + ": environment file would be published")
            continue
        data = p.read_bytes()
        if b"\0" in data[:4096]:
            continue
        for i, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
            for label, pattern in patterns.items():
                if pattern.search(line):
                    findings.append(f"{name}:{i}: {label}")
    if findings:
        raise ValueError("\n".join(findings))
    return {
        "manifest": "valid",
        "skill_links": "valid",
        "publishable_files": files,
        "credentials_and_personal_paths": "no matches",
    }


if __name__ == "__main__":
    try:
        print(json.dumps(check(), indent=2))
    except (AssertionError, ValueError) as e:
        print(str(e), file=sys.stderr)
        raise SystemExit(1)
