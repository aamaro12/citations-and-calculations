"""Content-addressed snapshots. Hashes detect changes, not source authenticity."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import tempfile


class EvidenceError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def digest(body):
    return hashlib.sha256(body).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def write(path, body):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
        f.write(body)
        temporary = Path(f.name)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def save_json(path, value):
    write(path, canonical(value) + b"\n")


def inside(root, name):
    root = Path(root).resolve()
    path = (root / name).resolve()
    if not path.is_relative_to(root):
        raise EvidenceError("Evidence path escapes the workspace")
    return path


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,79}", value):
        raise EvidenceError(
            "IDs must start with a letter and contain at most 80 letters, digits, underscores or hyphens"
        )
    return value


def normal(text):
    return re.sub(r"\s+", " ", str(text)).strip()


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def add(self, raw, extraction, provenance):
        if not extraction["units"]:
            raise EvidenceError("No readable evidence extracted; OCR or a different acquisition method is needed")
        extracted = canonical(extraction)
        meta = {"schema_version": 1, "raw_sha256": digest(raw), "extracted_sha256": digest(extracted), **provenance}
        sid = "s_" + digest(canonical(meta))
        folder = inside(self.root, "sources/" + sid)
        if folder.exists():
            self.load(sid)
            return sid
        folder.mkdir(parents=True)
        write(folder / "raw", raw)
        write(folder / "units.json", extracted)
        write(folder / "source.json", canonical(meta))
        return sid

    def load(self, sid):
        if not re.fullmatch(r"s_[a-f0-9]{64}", str(sid)):
            raise EvidenceError("Invalid source ID")
        inside(self.root, "sources/" + sid)
        try:
            meta = json.loads(inside(self.root, f"sources/{sid}/source.json").read_bytes())
            if "s_" + digest(canonical(meta)) != sid:
                raise EvidenceError("Source metadata changed")
            raw = inside(self.root, f"sources/{sid}/raw").read_bytes()
            extracted = inside(self.root, f"sources/{sid}/units.json").read_bytes()
        except (OSError, json.JSONDecodeError) as e:
            raise EvidenceError(f"Source snapshot is missing or unreadable: {sid}") from e
        if digest(raw) != meta["raw_sha256"] or digest(extracted) != meta["extracted_sha256"]:
            raise EvidenceError("Source bytes or extraction changed")
        return meta, json.loads(extracted)

    def passage(self, citation):
        if not isinstance(citation, dict):
            raise EvidenceError("A citation must name source_id, locator and excerpt")
        meta, extracted = self.load(citation.get("source_id"))
        locator, excerpt = citation.get("locator"), citation.get("excerpt")
        if not isinstance(excerpt, str) or not normal(excerpt):
            raise EvidenceError("A citation needs a non-empty exact excerpt")
        found = [u for u in extracted["units"] if u["locator"] == locator]
        if len(found) != 1:
            raise EvidenceError("Citation locator is absent or ambiguous")
        if normal(excerpt) not in normal(found[0]["text"]):
            raise EvidenceError("Citation excerpt does not match its locator")
        return {"source": meta, "unit": found[0], "source_id": citation["source_id"], "excerpt": excerpt}

    def search(self, sid, query="", limit=30):
        meta, extraction = self.load(sid)
        matches = [u for u in extraction["units"] if query.casefold() in u["text"].casefold()]
        if not 1 <= limit <= 200:
            raise EvidenceError("Read limit is 1 to 200 passages")
        return {
            "source_id": sid,
            "source": meta,
            "units": matches[:limit],
            "matched": len(matches),
            "truncated": len(matches) > limit,
            "warnings": extraction.get("warnings", []),
        }
