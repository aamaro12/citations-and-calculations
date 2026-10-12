"""Compile a freshly audited answer for Codex chat, with pinned local drill-downs."""

import json
from pathlib import Path
import re
from urllib.parse import quote

from .audit import MARKER
from .extract import HTML_WARNING
from .store import EvidenceError, canonical, digest, inside, write


def prose(value):
    """Evidence and claim text cannot inject Markdown links, images or raw HTML."""
    return literal(value).strip()


def literal(value):
    text = re.sub(r"\s+", " ", str(value))
    return re.sub(r"([\\`*_{}\[\]<>#|~!&$])", r"\\\1", text)


def link(label, target):
    return "[" + prose(label) + "](<" + target + ">)"


def local_target(path):
    text = str(Path(path).resolve())
    if any(c in text for c in "\r\n<>"):
        raise EvidenceError("Chat output paths cannot contain line breaks or angle brackets")
    return text + ":1"


def quoted(text):
    return "\n".join("> " + prose(line) for line in str(text).splitlines())


def location(locator):
    """Readable location; the exact machine locator stays in the audit record."""
    patterns = (
        (r"block:(\d+)", lambda m: "Web passage " + m[1]),
        (r"page:(\d+)/line:(\d+)", lambda m: "Page " + m[1] + ", line " + m[2]),
        (r"cell:R(\d+)C(\d+)", lambda m: "Table row " + m[1] + ", column " + m[2]),
        (r"cell:(.+)!([^!]+)", lambda m: m[1] + ", cell " + m[2]),
        (r"line:(\d+)", lambda m: "Line " + m[1]),
        (r"row:(\d+)", lambda m: "Table row " + m[1]),
    )
    for pattern, describe in patterns:
        match = re.fullmatch(pattern, locator)
        if match:
            return describe(match)
    if locator.startswith("json:"):
        field = locator.rsplit("/", 1)[-1].replace("~1", "/").replace("~0", "~").replace("_", " ")
        return "Tool response · " + field if locator.startswith("json:/tool_result/") else "JSON field · " + field
    return locator


def source_note(e, raw_target, audit_target):
    source, unit = e["source"], e["unit"]
    lines = [
        "# " + prose(source["title"]),
        "",
        quoted(e["excerpt"]),
        "",
        "**Where:** " + prose(location(unit["locator"])),
    ]
    fields = {str(k).casefold(): str(v) for k, v in unit.get("context", {}).get("fields", {}).items()}
    labels = []
    for aliases in (("metric",), ("entity", "company"), ("period",)):
        value = next((fields[k] for k in aliases if fields.get(k)), None)
        if value:
            labels.append(value)
    if fields.get("unit"):
        scale = {"1000": "thousands", "1000000": "millions", "1000000000": "billions"}.get(fields.get("scale", "1"))
        suffix = ", in " + scale if scale else ", scale " + fields["scale"] if fields.get("scale", "1") != "1" else ""
        labels.append(fields["unit"] + suffix)
    headers = unit.get("context", {}).get("column_header", {})
    labels.extend(str(v) for v in headers.values() if str(v) not in labels)
    if labels:
        lines += ["", "**Context:** " + prose(" · ".join(labels))]
    notes = []
    if source["completeness"] != "complete":
        notes.append(
            "This is a partial source." if source["completeness"] == "partial" else "Source coverage is unknown."
        )
    short_warnings = {
        "Visible HTML text only: scripts, hidden-state semantics and interactive content are not executed": "Saved page text; interactive content was not captured.",
        HTML_WARNING: "Saved page text; hidden and interactive content was not captured.",
        "This verifies the captured response, not the original service or a full document": "Saved tool response; original source completeness is not independently checked.",
    }
    notes.extend(short_warnings.get(w, w) for w in e.get("source_warnings", []))
    if notes:
        lines += ["", "**Note:** " + prose(" ".join(dict.fromkeys(notes)))]
    links = []
    if source.get("uri"):
        links.append(link("Open original source", quote(source["uri"], safe="/:#?&=@%+;,$")))
    links += [link("Saved copy", raw_target), link("Audit details", audit_target)]
    lines += ["", " · ".join(links)]
    return "\n".join(lines) + "\n"


def fact_note(f, evidence, audit_target):
    assumption = f.get("kind", "reported") == "assumption"
    lines = [
        "# " + prose(f["label"]),
        "",
        "**" + prose(f["rendered"]["display"]) + "**",
        "",
        prose(f["entity"]) + " · " + prose(f["period"]) + " · " + ("Assumption" if assumption else "Reported value"),
    ]
    if assumption:
        lines += ["", prose(f["reason"])]
    else:
        lines += ["", "**Source:** " + " · ".join(link(e["title"], e["target"]) for e in evidence)]
    lines += ["", link("Audit details", audit_target)]
    return "\n".join(lines) + "\n"


def calculation_note(cid, c, result, fact_map, calc_map, audit_target):
    title = cid.replace("_", " ").replace("-", " ").capitalize()
    lines = [
        "# " + prose(title),
        "",
        "**" + prose(c["result"]["display"]) + "**",
        "",
        "Formula: " + prose(c["formula"]),
        "",
        "## Inputs",
        "",
    ]
    for variable, ref in c["inputs"].items():
        item = fact_map.get(ref) or calc_map[ref]
        context = result["facts"].get(ref)
        suffix = " · " + prose(context["label"] + ", " + context["period"]) if context else ""
        lines.append(
            "- "
            + prose(variable.replace("_", " ").capitalize())
            + ": "
            + link(item["display"], item["target"])
            + suffix
        )
    if c.get("comparison_reason"):
        lines += ["", prose(c["comparison_reason"])]
    if c["assumptions"]:
        lines += ["", "## Assumptions", ""]
        for fid in c["assumptions"]:
            lines.append(
                "- "
                + link(result["facts"][fid]["label"], fact_map[fid]["target"])
                + ": "
                + prose(result["facts"][fid]["reason"])
            )
    lines += ["", "Calculated in Python. " + link("Audit details", audit_target)]
    return "\n".join(lines) + "\n"


def render_chat(out, result):
    """Internal renderer: callers must supply a new verify() result, never a stored verdict.

    Every link pins this audit's content-addressed directory. It will not point
    at a later run's latest report. Only audit_file exposes this via the CLI.
    """
    out = Path(out).resolve()
    audit_sha = digest(canonical(result))
    snapshot = inside(out, "chat/a_" + audit_sha)
    local_target(snapshot)
    citations = []
    indexed = {}
    fact_map = {}
    calc_map = {}

    def save(name, body):
        path = inside(snapshot, name)
        body = body.encode() if isinstance(body, str) else body
        if path.exists():
            if path.read_bytes() != body:
                raise EvidenceError("Chat snapshot content changed: " + name)
        else:
            write(path, body)
        return local_target(path)

    audit_target = save("audit.json", canonical(result) + b"\n")
    for sid, meta in result["sources"].items():
        raw = inside(out, "evidence/" + sid + "/raw").read_bytes()
        units = json.loads(inside(out, "evidence/" + sid + "/units.json").read_bytes())
        if digest(raw) != meta["raw_sha256"] or digest(canonical(units)) != meta["extracted_sha256"]:
            raise EvidenceError("Exported source changed before chat rendering")
        save("evidence/" + sid + "/raw", raw)
        save("evidence/" + sid + "/units.json", canonical(units) + b"\n")
        save("evidence/" + sid + "/source.json", canonical(meta) + b"\n")

    def citation(e):
        identity = canonical({"source_id": e["source_id"], "locator": e["unit"]["locator"], "excerpt": e["excerpt"]})
        key = digest(identity)
        if key in indexed:
            return indexed[key]
        cid = "S" + str(len(citations) + 1)
        sid, source, unit = e["source_id"], e["source"], e["unit"]
        uri = source.get("uri")
        raw_target = local_target(inside(snapshot, "evidence/" + sid + "/raw"))
        title = source["title"]
        target = save("source-" + cid + ".md", source_note(e, raw_target, audit_target))
        item = {
            "id": cid,
            "source_id": sid,
            "title": title,
            "locator": unit["locator"],
            "excerpt": e["excerpt"],
            "target": target,
            "original_url": uri,
            "coverage": source["completeness"],
        }
        indexed[key] = item
        citations.append(item)
        return item

    for fid, f in result["facts"].items():
        evidence = [citation(e) for e in f["evidence"]]
        assumption = f.get("kind", "reported") == "assumption"
        target = save("fact-" + fid + ".md", fact_note(f, evidence, audit_target))
        fact_map[fid] = {
            "id": fid,
            "display": f["rendered"]["display"],
            "target": target,
            "kind": "assumption" if assumption else "reported",
            "citations": [e["id"] for e in evidence],
        }

    # Targets are known before writing, so forward calculation references work.
    for cid, c in result["calculations"].items():
        calc_map[cid] = {
            "id": cid,
            "display": c["result"]["display"],
            "target": local_target(inside(snapshot, "calc-" + cid + ".md")),
            "inputs": c["inputs"],
            "assumptions": c["assumptions"],
        }
    for cid, c in result["calculations"].items():
        save("calc-" + cid + ".md", calculation_note(cid, c, result, fact_map, calc_map, audit_target))

    lines = []
    if result["status"] == "blocked":
        lines += ["**Draft: checks failed. Blocked claims below are not verified.**", ""]
    for c in result["claims"]:
        if c["status"] == "blocked":
            lines += ["**Blocked claim:** " + prose(c["rendered_text"]), ""]
            continue
        pieces, start = [], 0
        for match in MARKER.finditer(c["text"]):
            pieces.append(literal(c["text"][start : match.start()]))
            item = fact_map[match[2]] if match[1] == "fact" else calc_map[match[2]]
            pieces.append(link(item["display"], item["target"]))
            start = match.end()
        pieces.append(literal(c["text"][start:]))
        text = "".join(pieces)
        refs = [citation(e) for e in c.get("evidence", [])]
        for fid in c["fact_dependencies"]:
            refs.extend(citation(e) for e in result["facts"][fid]["evidence"])
        refs = list({e["id"]: e for e in refs}.values())
        prefix = {"inference": "**Inference:** ", "assumption": "**Assumption:** "}.get(c["kind"], "")
        if any(result["facts"][fid].get("kind") == "assumption" for fid in c["fact_dependencies"]):
            prefix = "**Uses assumptions:** " + prefix
        text += " " + " ".join(link(e["id"] + " · " + e["locator"], e["target"]) for e in refs) if refs else ""
        lines += [prefix + text, ""]
    if result["issues"]:
        lines += ["**Failed checks:**", ""]
        lines += ["- " + prose(i["at"] + ": " + i["message"]) for i in result["issues"]]
        lines.append("")
    if result["warnings"]:
        lines += ["**Caveats:**", ""]
        for w in result["warnings"]:
            owner = (
                result["sources"].get(w["at"], {}).get("title")
                or result["facts"].get(w["at"], {}).get("label")
                or w["at"]
            )
            lines.append("- " + prose(owner + ": " + w["message"]))
        lines.append("")
    lines += [
        "Checks: " + prose(result["status"]) + ". " + link("Audit details", audit_target),
        "",
        ("Citation matches and arithmetic are checked" if result["calculations"] else "Citation matches are checked")
        + "; semantic support remains a recorded judgement.",
    ]
    payload = {
        "schema_version": 1,
        "status": result["status"],
        "answer_markdown": "\n".join(lines) + "\n",
        "citations": citations,
        "facts": list(fact_map.values()),
        "calculations": list(calc_map.values()),
        "audit_sha256": audit_sha,
        "audit_target": audit_target,
        "boundary": result["boundary"],
        "delivery": "Display answer_markdown directly in chat. Preserve its links, figures, labels, failed checks and caveats. Do not invent native citation IDs or claim host-wide verification.",
    }
    save("answer.json", canonical(payload) + b"\n")
    return payload
