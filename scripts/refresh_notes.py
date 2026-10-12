"""Refresh saved citation-page presentation on request, without changing audit evidence.

This is not verification and does not issue a new positive verdict. Original
pages are backed up; content-addressed audit data and source snapshots stay put.
"""

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from citations_calculations.chat import source_note, fact_note, calculation_note, local_target
from citations_calculations.store import EvidenceError, canonical, digest, inside, write, save_json, identifier, Store


def refresh(root):
    root = Path(root).resolve()
    backup = root / ("note-backup-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    changes = []
    proposed = []
    for audit in sorted(root.rglob("audit.json")):
        snapshot = audit.parent
        if snapshot.parent.name != "chat" or not re.fullmatch(r"a_[a-f0-9]{64}", snapshot.name):
            continue
        inside(root, audit.relative_to(root))
        result = json.loads(audit.read_bytes())
        if snapshot.name != "a_" + digest(canonical(result)):
            raise EvidenceError("Saved audit content does not match its directory")
        payload_path = inside(snapshot, "answer.json")
        payload = json.loads(payload_path.read_bytes())
        if payload["audit_sha256"] != snapshot.name[2:]:
            raise EvidenceError("Saved chat payload references a different audit")
        audit_target = local_target(audit)
        evidence = {}
        # The report keeps exports in evidence/; use an isolated Store to reuse
        # exact-passage validation without altering any saved source files.
        with tempfile.TemporaryDirectory() as td:
            store = Store(td)
            for sid, meta in result["sources"].items():
                raw = inside(snapshot, "evidence/" + sid + "/raw").read_bytes()
                units = json.loads(inside(snapshot, "evidence/" + sid + "/units.json").read_bytes())
                if digest(raw) != meta["raw_sha256"] or digest(canonical(units)) != meta["extracted_sha256"]:
                    raise EvidenceError("Saved source content changed")
                if store.add(raw, units, meta) != sid:
                    raise EvidenceError("Saved source identity changed")
            for item in list(result["facts"].values()) + result["claims"]:
                for e in item.get("evidence", []):
                    key = (e["source_id"], e["unit"]["locator"], e["excerpt"])
                    checked = store.passage({"source_id": key[0], "locator": key[1], "excerpt": key[2]})
                    if checked["source"] != e["source"] or checked["unit"] != e["unit"]:
                        raise EvidenceError("Saved evidence differs from its original snapshot")
                    evidence[key] = e
        pages = {}
        citation_map = {}
        seen = set()
        for c in payload["citations"]:
            if not re.fullmatch(r"S[1-9][0-9]*", c["id"]) or c["id"] in seen:
                raise EvidenceError("Invalid or duplicate saved citation ID")
            seen.add(c["id"])
            e = evidence[(c["source_id"], c["locator"], c["excerpt"])]
            name = "source-" + c["id"] + ".md"
            target = local_target(inside(snapshot, name))
            citation_map[(c["source_id"], c["locator"], c["excerpt"])] = {
                "title": e["source"]["title"],
                "target": target,
            }
            raw_target = local_target(inside(snapshot, "evidence/" + c["source_id"] + "/raw"))
            pages[name] = source_note(e, raw_target, audit_target)
        fact_map = {
            fid: {
                "display": f["rendered"]["display"],
                "target": local_target(inside(snapshot, "fact-" + identifier(fid) + ".md")),
            }
            for fid, f in result["facts"].items()
        }
        calc_map = {
            cid: {
                "display": c["result"]["display"],
                "target": local_target(inside(snapshot, "calc-" + identifier(cid) + ".md")),
            }
            for cid, c in result["calculations"].items()
        }
        for fid, f in result["facts"].items():
            refs = [citation_map[(e["source_id"], e["unit"]["locator"], e["excerpt"])] for e in f["evidence"]]
            pages["fact-" + fid + ".md"] = fact_note(f, refs, audit_target)
        for cid, c in result["calculations"].items():
            pages["calc-" + cid + ".md"] = calculation_note(cid, c, result, fact_map, calc_map, audit_target)
        for name, text in pages.items():
            path = inside(snapshot, name)
            if not path.is_file():
                raise EvidenceError("Expected saved note is missing: " + name)
            before, after = path.read_bytes(), text.encode()
            if before != after:
                proposed.append((path, before, after))
    # All records are inspected before any existing page changes.
    for path, before, after in proposed:
        relative = path.relative_to(root)
        write(inside(backup, relative), before)
        write(path, after)
        changes.append({"file": str(relative), "before_sha256": digest(before), "after_sha256": digest(after)})
    if changes:
        save_json(backup / "changes.json", changes)
    return {
        "notes_refreshed": len(changes),
        "backup": str(backup) if changes else None,
        "scope": "Presentation only; no new audit verdict. Audit data and original sources are unchanged.",
    }


if __name__ == "__main__":
    print(json.dumps(refresh(sys.argv[1]), indent=2))
