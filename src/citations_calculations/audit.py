"""Never trust a stored verdict or calculation. Recheck the evidence and recompute the dependency graph."""

from decimal import localcontext
import json
from pathlib import Path
import re

from .acquire import timestamp
from .numbers import decimal, tokens, quantity, evaluate, display
from .store import Store, EvidenceError, canonical, digest, identifier, now, readings, save_json
from . import __version__

MARKER = re.compile(r"\{\{(fact|calc|period|entity):([A-Za-z][A-Za-z0-9_-]{0,79})\}\}")
PERIOD = re.compile(r"(?:FY|CY)?\s?((?:19|20)\d{2})\s?[AEBFP]?", re.I)
# Figures written out in words, which would bypass the rule that every figure comes from a marker.
SMALL = "zero|one|two|three|four|five|six|seven|eight|nine|ten"
SPELLED = re.compile(
    r"(?i)\b(?:eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen"
    r"|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)\b"
    rf"|\b(?:a|{SMALL})\s+(?:hundred|thousand|million|billion|trillion)\b"
    rf"|\b(?:{SMALL})\s+(?:per\s?cent|percent|percentage points?|basis points?)\b"
    rf"|(?:[−×÷=*/+]|\s-|\b(?:minus|plus|times|divided by|multiplied by))\s*(?:{SMALL}|hundred)\b"
)


def review(item):
    r = item.get("review") or {}
    if (
        not isinstance(r, dict)
        or r.get("verdict") not in ("supports", "contradicts", "unclear")
        or r.get("reviewer") not in ("codex", "human")
        or not isinstance(r.get("rationale"), str)
        or not r["rationale"].strip()
    ):
        raise EvidenceError("A support review needs verdict, reviewer (codex or human) and a rationale")
    if r["verdict"] != "supports":
        raise EvidenceError("Support review is " + r["verdict"])
    return {**r, "boundary": "A recorded judgement, not an independent proof that the claim is true"}


def unique(items, kind):
    if not isinstance(items, list):
        raise EvidenceError(kind + " must be a list")
    found = {}
    for item in items:
        if not isinstance(item, dict):
            raise EvidenceError(kind + " entries must be objects")
        key = identifier(item.get("id"))
        if key in found:
            raise EvidenceError("Duplicate " + kind + " ID: " + key)
        found[key] = item
    return found


def check_context(fact, evidence):
    """Check explicit structured labels; absence leaves interpretation to the recorded support review."""
    context = evidence["unit"].get("context", {})
    fields = {str(k).casefold(): str(v) for k, v in context.get("fields", {}).items()}
    checked = []
    for field, aliases in {
        "period": ("period",),
        "entity": ("entity", "company"),
        "unit": ("unit",),
        "label": ("metric",),
    }.items():
        observed = [fields[k] for k in aliases if k in fields]
        if observed:
            if any(v != fact[field] for v in observed):
                raise EvidenceError("Fact " + field + " contradicts the structured source label")
            checked.append(field)
    if "scale" in fields:
        if decimal(fields["scale"]) != decimal(fact.get("scale", "1")):
            raise EvidenceError("Fact scale contradicts the structured source label")
        checked.append("scale")
    # The nearest year heading above a workbook cell must appear in the fact's period: FY2026 allows Q2 FY2026.
    years = [
        (int(re.sub(r"\D", "", ref) or 0), match[1])
        for ref, heading in context.get("column_header", {}).items()
        if (match := PERIOD.fullmatch(str(heading).strip()))
    ]
    if years:
        year = max(years)[1]
        if not re.search(r"(?<!\d)" + year + r"(?!\d)", fact["period"]):
            raise EvidenceError("Fact period contradicts the workbook column header " + year)
        checked.append("period")
    return sorted(set(checked))


def verify(root, plan):
    """Return a report even for blocked claims; a ready report means the declared checks passed, not truth."""
    if not isinstance(plan, dict) or type(plan.get("schema_version")) is not int or plan["schema_version"] != 1:
        raise EvidenceError("Bundle schema_version must be 1")
    store = Store(root)
    cutoff = timestamp(plan["as_of"]) if plan.get("as_of") else None
    facts = unique(plan.get("facts", []), "facts")
    calcs = unique(plan.get("calculations", []), "calculations")
    claims = unique(plan.get("claims", []), "claims")
    if not claims:
        raise EvidenceError("A bundle needs at least 1 claim")
    if set(facts) & set(calcs):
        raise EvidenceError("Fact and calculation IDs must be distinct")
    issues = []
    warnings = []
    values = {}
    source_meta = {}
    fact_results = {}
    calc_results = {}
    claim_results = []

    def defect(where, error):
        issues.append({"at": where, "message": str(error)})

    def citation(c):
        result = store.passage(c)
        meta = result["source"]
        source_meta[result["source_id"]] = meta
        if cutoff and meta.get("published_at") and timestamp(meta["published_at"]) > cutoff:
            raise EvidenceError("Source publication is after the information cutoff")
        if meta["completeness"] != "complete":
            warnings.append({"at": result["source_id"], "message": "Source coverage is " + meta["completeness"]})
        result["source_warnings"] = store.load(result["source_id"])[1].get("warnings", [])
        warnings.extend({"at": result["source_id"], "message": w} for w in result["source_warnings"])
        if cutoff and not meta.get("published_at"):
            warnings.append(
                {
                    "at": result["source_id"],
                    "message": "No declared publication timestamp: the source is not proven to predate the information cutoff.",
                }
            )
        return result

    for fid, f in facts.items():
        try:
            for field in ("label", "entity", "period", "unit"):
                if not isinstance(f.get(field), str) or not f[field].strip():
                    raise EvidenceError("Fact needs explicit " + field)
            q = quantity(f["value"], f["unit"], f.get("scale", "1"))
            provenance = f.get("kind", "reported")
            if provenance == "assumption":
                if not isinstance(f.get("reason"), str) or not f["reason"].strip():
                    raise EvidenceError("An assumption needs its reason")
                evidence = []
                judgement = None
                warnings.append({"at": fid, "message": "Declared assumption: " + f["reason"]})
            elif provenance == "reported":
                if not f.get("citations"):
                    raise EvidenceError("A reported fact requires a citation")
                evidence = [citation(c) for c in f["citations"]]
                expected = decimal(f["value"])
                for e in evidence:
                    if expected not in [n for _, n in tokens(e["excerpt"])]:
                        raise EvidenceError("Fact value or sign is absent from the exact cited excerpt")
                    # The excerpt must not cut a number short: 25 from 1250, or 1,250 from (1,250).
                    if not any(expected in [n for _, n in tokens(text)] for text in readings(e["unit"])):
                        raise EvidenceError(
                            "Fact value is only part of a number at the cited location, or drops its sign"
                        )
                    e["context_fields_checked"] = check_context(f, e)
                    if e["unit"].get("context", {}).get("cached_formula"):
                        raise EvidenceError(
                            "A workbook formula cache must be independently recalculated before use as a numerical input"
                        )
                judgement = review(f)
            else:
                raise EvidenceError("Fact kind is reported or assumption")
            result = {
                **f,
                "evidence": evidence,
                "support_review": judgement,
                "value_check": "exact_numeric_value_and_sign" if evidence else "declared_assumption",
                "context_boundary": "Explicit structured labels are checked when available; other unit, scale, entity, metric and period interpretations depend on the recorded support review.",
                "rendered": display(q, f["unit"], f.get("scale", "1"), f.get("places", 2)),
            }
            fact_results[fid] = result
            values[fid] = q
        except (EvidenceError, KeyError, TypeError) as e:
            defect("fact:" + fid, e)

    visiting = set()

    def calculation(cid):
        if cid in calc_results:
            return values[cid]
        if cid in visiting:
            raise EvidenceError("Calculation dependency cycle")
        if cid not in calcs:
            raise EvidenceError("Unknown or invalid calculation input: " + cid)
        visiting.add(cid)
        try:
            c = calcs[cid]
            bindings = c.get("inputs")
            if not isinstance(bindings, dict) or not bindings:
                raise EvidenceError("Calculation needs named input references")
            variables = {}
            dependencies = set()
            assumptions = set()
            for variable, ref in bindings.items():
                if not isinstance(variable, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", variable):
                    raise EvidenceError("Invalid formula variable")
                if ref in calcs:
                    variables[variable] = calculation(ref)
                    dependencies.update(calc_results[ref]["fact_dependencies"])
                elif ref in values:
                    variables[variable] = values[ref]
                    dependencies.add(ref)
                else:
                    raise EvidenceError("Missing or invalid input: " + str(ref))
            reported = [facts[f] for f in dependencies if facts[f].get("kind", "reported") == "reported"]
            assumptions = {f for f in dependencies if facts[f].get("kind") == "assumption"}
            for field in ("period", "entity"):
                if len({f[field] for f in reported}) > 1:
                    if (
                        c.get(field + "_policy") != "compare"
                        or not isinstance(c.get("comparison_reason"), str)
                        or not c["comparison_reason"].strip()
                    ):
                        raise EvidenceError("Mixed " + field + " inputs need an explicit comparison policy and reason")
            with localcontext() as ctx:
                ctx.prec = 60
                q = evaluate(c.get("formula"), variables)
                result = display(q, c.get("unit", "1"), c.get("scale", "1"), c.get("places", 2))
            if c.get("reported_result") is not None and decimal(c["reported_result"]) != decimal(result["rounded"]):
                raise EvidenceError("Reported result differs from the independently recomputed rounded result")
            calc_results[cid] = {
                **c,
                "result": result,
                "fact_dependencies": sorted(dependencies),
                "assumptions": sorted(assumptions),
                "check": "recomputed_in_python",
            }
            values[cid] = q
            return q
        finally:
            visiting.discard(cid)

    for cid in calcs:
        try:
            calculation(cid)
        except (EvidenceError, KeyError, TypeError, RecursionError) as e:
            defect("calc:" + cid, e)

    for cid, c in claims.items():
        before = len(issues)
        try:
            kind = c.get("kind")
            if kind not in ("fact", "inference", "calculated", "assumption"):
                raise EvidenceError("Claim kind must be fact, inference, calculated or assumption")
            text = c.get("text")
            if not isinstance(text, str) or not text.strip():
                raise EvidenceError("Claim requires text")
            refs = MARKER.findall(text)
            unbound = MARKER.sub("", text)
            if "{{" in unbound or "}}" in unbound:
                raise EvidenceError("Invalid or unknown figure marker")
            if re.search(r"\d", unbound):
                raise EvidenceError("Unbound number in claim text; use a fact or calculation marker")
            spelled = SPELLED.search(unbound)
            if spelled:
                raise EvidenceError(
                    'Spelled-out number in claim text ("' + spelled[0].strip() + '"); '
                    "use a fact, calculation, period or entity marker"
                )
            if kind == "calculated" and not any(k == "calc" for k, _ in refs):
                raise EvidenceError("Calculated claim must reference a recomputed calculation")
            if kind == "fact" and any(k == "calc" for k, _ in refs):
                raise EvidenceError("A computed figure belongs in a calculated claim")
            evidence = [citation(e) for e in c.get("citations", [])]
            if kind == "assumption":
                if not isinstance(c.get("reason"), str) or not c["reason"].strip():
                    raise EvidenceError("Assumption claim needs a reason")
                judgement = None
                warnings.append({"at": cid, "message": "Assumption claim: " + c["reason"]})
            else:
                judgement = review(c)
                if not evidence and not refs:
                    raise EvidenceError("A factual or inferential claim needs cited premises")
            rendered = text
            depends = set()
            for typ, ref in refs:
                if typ in ("fact", "period", "entity"):
                    if ref not in fact_results:
                        raise EvidenceError("Claim references an invalid fact")
                    f = fact_results[ref]
                    number = f["rendered"]["display"] if typ == "fact" else f[typ]
                    depends.add(ref)
                else:
                    if ref not in calc_results:
                        raise EvidenceError("Claim references an invalid calculation")
                    number = calc_results[ref]["result"]["display"]
                    depends.update(calc_results[ref]["fact_dependencies"])
                rendered = rendered.replace("{{" + typ + ":" + ref + "}}", number)
            if kind == "fact" and any(facts[f].get("kind") == "assumption" for f in depends):
                raise EvidenceError("A reported fact claim cannot present an assumption as a fact")
            claim_results.append(
                {
                    **c,
                    "rendered_text": rendered,
                    "evidence": evidence,
                    "support_review": judgement,
                    "fact_dependencies": sorted(depends),
                    "status": "checks_passed",
                }
            )
        except (EvidenceError, KeyError, TypeError) as e:
            defect("claim:" + cid, e)
            claim_results.append(
                {
                    "id": cid,
                    "kind": c.get("kind")
                    if c.get("kind") in ("fact", "inference", "calculated", "assumption")
                    else "unknown",
                    "text": c.get("text") if isinstance(c.get("text"), str) else "",
                    "status": "blocked",
                    "rendered_text": str(c.get("text", "")),
                    "evidence": [],
                    "fact_dependencies": [],
                    "support_review": None,
                }
            )
        assert len(issues) >= before
    warnings = list({(w["at"], w["message"]): w for w in warnings}.values())
    # No positive final verdict can be imported from a previous answer or audit.
    return {
        "schema_version": 1,
        "tool_version": __version__,
        "audited_at": now(),
        "bundle_sha256": digest(canonical(plan)),
        "status": "blocked" if issues else "checks_passed_with_caveats" if warnings else "checks_passed",
        "boundary": "Checks establish snapshot integrity, exact evidence anchors, recorded support reviews and arithmetic. They do not prove source authenticity, semantic truth, completeness or the choice of model.",
        "facts": fact_results,
        "calculations": calc_results,
        "claims": claim_results,
        "sources": source_meta,
        "issues": issues,
        "warnings": warnings,
    }


def audit_file(root, path, output):
    body = Path(path).read_bytes()
    if len(body) > 2 * 1024 * 1024:
        raise EvidenceError("Answer bundle exceeds the 2 MiB limit")
    plan = json.loads(body)
    result = verify(root, plan)
    out = Path(output)
    out.mkdir(parents=True, exist_ok=True)
    save_json(out / "bundle.json", plan)
    save_json(out / "audit.json", result)
    # Content-address the result so later audits cannot silently overwrite this snapshot.
    save_json(out / "audits" / ("a_" + digest(canonical(result)) + ".json"), result)
    from .report import render, export_sources

    export_sources(root, out, result)
    render(out, result)
    from .chat import render_chat

    chat = render_chat(out, result)
    return {
        "status": result["status"],
        "claims": len(result["claims"]),
        "issues": result["issues"],
        "report": str((out / "review.html").resolve()),
        "answer": str((out / "answer.md").resolve()),
        "chat": chat,
    }
