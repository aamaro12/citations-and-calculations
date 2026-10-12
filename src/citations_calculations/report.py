"""A portable local review: read claims, follow inputs, inspect evidence. No telemetry."""

from html import escape
import json
from pathlib import Path
from urllib.parse import quote

from .store import save_json, write, Store

STYLE = """
:root{color-scheme:light;--ink:#17232c;--muted:#54636d;--line:#d5ddd9;--paper:#f6f7f3;--green:#22664c;--red:#9b2927}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.55 system-ui,sans-serif}
header,main,footer{max-width:1150px;margin:auto;padding:28px}header{border-bottom:1px solid var(--line)}
h1{font-size:38px;letter-spacing:-1.1px;margin:8px 0}h2{font-size:22px}p{margin:10px 0}a{color:#235f89}
.eyebrow{font-size:12px;letter-spacing:2px;text-transform:uppercase;color:var(--green)}.muted,small{color:var(--muted)}
.summary{display:flex;gap:12px;flex-wrap:wrap;margin:20px 0}.summary span,.tag{background:#e6ece7;padding:5px 11px;border-radius:6px}
.blocked{color:var(--red)}.grid{display:grid;grid-template-columns:1.1fr 1fr;gap:30px}.card{padding:20px;background:white;border:1px solid var(--line);border-radius:10px;margin:12px 0;scroll-margin-top:16px}
.card:target{outline:3px solid #87ad99}.card h3{margin:0 0 10px;font-size:17px}blockquote{margin:12px 0;padding:10px 14px;border-left:3px solid #9eafa5;background:#f3f5f1}
pre{white-space:pre-wrap;word-break:break-word;font-size:12px;background:#f2f4f0;padding:12px;border-radius:6px}code{font-size:13px}
.controls{display:flex;gap:10px;flex-wrap:wrap}input,select,button{font:inherit;padding:8px 12px;border:1px solid #bbc8c0;border-radius:6px;background:white}input{flex:1}button{cursor:pointer}.hidden{display:none}
label{font-size:13px}details{margin-top:12px}summary{cursor:pointer;color:#235f89}ul{padding-left:22px}.links{display:flex;gap:16px;flex-wrap:wrap}
@media(max-width:800px){.grid{grid-template-columns:1fr}header,main,footer{padding:20px}h1{font-size:30px}}@media print{.controls,button{display:none}details>*{display:block}.grid{display:block}}
"""
JS = """
const search=document.querySelector('#search'),filter=document.querySelector('#filter');
function update(){document.querySelectorAll('[data-claim]').forEach(n=>{const term=search.value.toLowerCase();n.classList.toggle('hidden',!n.textContent.toLowerCase().includes(term)||(filter.value!=='all'&&n.dataset.kind!==filter.value));});}
search.addEventListener('input',update);filter.addEventListener('change',update);
document.querySelector('#print').addEventListener('click',()=>window.print());
window.addEventListener('hashchange',()=>{const n=document.getElementById(location.hash.slice(1));if(n){n.classList.remove('hidden');n.querySelectorAll('details').forEach(d=>d.open=true);}});
"""


def esc(value):
    return escape(str(value), quote=True)


def source_link(e):
    title = esc(e["source"]["title"]).replace("[", "\\[").replace("]", "\\]")
    uri = e["source"].get("uri") or "evidence/" + e["source_id"] + "/units.json"
    uri = quote(uri, safe="/:#?&=@%+;,$")
    return "[" + title + "](<" + uri + ">) · `" + e["unit"]["locator"] + "`"


def evidence_html(e):
    sid = e["source_id"]
    source = e["source"]
    context = {"cited_unit": e["unit"]["text"], "labels": e["unit"].get("context", {})}
    return f"""<blockquote>{esc(e["excerpt"])}</blockquote><small><a href="#source-{esc(sid)}">{esc(source["title"])}</a> · {esc(e["unit"]["locator"])}</small>
<details><summary>Source context</summary><pre>{esc(json.dumps(context, ensure_ascii=False, indent=2))}</pre></details>"""


def render(out, result):
    from .audit import MARKER
    from .chat import literal

    out = Path(out)
    cards = []
    markdown = []
    blocked = result["status"] == "blocked"
    markdown += ["# Citations & Calculations", f"\nStatus: **{result['status']}**", "\n" + result["boundary"]]
    if blocked:
        markdown.append("\n**Draft: checks failed. Inspect the issues before relying on these claims.**")
    if result["warnings"]:
        markdown += ["\nCaveats:"] + ["- " + w["at"] + ": " + literal(w["message"]) for w in result["warnings"]]
    for c in result["claims"]:
        text = esc(c["text"])
        for typ, key in MARKER.findall(c["text"]):
            source = result["facts"].get(key) if typ == "fact" else result["calculations"].get(key)
            if source:
                value = source["rendered"]["display"] if typ == "fact" else source["result"]["display"]
                text = text.replace("{{" + typ + ":" + key + "}}", f'<a href="#{typ}-{esc(key)}">{esc(value)}</a>')
        evidence = "".join(evidence_html(e) for e in c.get("evidence", []))
        review = c.get("support_review") or {}
        cards.append(
            f'''<article class="card {"blocked" if c["status"] == "blocked" else ""}" data-claim="{esc(c["id"])}" data-kind="{esc(c["kind"])}"><small class="tag">{esc(c["kind"])} · {esc(c["status"])}</small><p>{text}</p>{evidence}<details><summary>Support review and inputs</summary><p>{esc(review.get("reviewer", "Declared assumption"))}: {esc(review.get("rationale", c.get("reason", "")))}</p><p>{" ".join(f'<a href="#fact-{esc(fid)}">{esc(fid)}</a>' for fid in c.get("fact_dependencies", []))}</p><small>Review is a recorded judgement; passage matching does not prove this claim.</small></details></article>'''
        )
        markdown.append("\n- **" + c["kind"] + " / " + c["status"] + "**: " + literal(c["rendered_text"]))
        for e in c.get("evidence", []):
            markdown.append("  - " + source_link(e))
        for fid in c.get("fact_dependencies", []):
            f = result["facts"][fid]
            for e in f.get("evidence", []):
                markdown.append("  - Input " + fid + ": " + source_link(e))
    inputs = []
    for fid, f in result["facts"].items():
        inputs.append(
            f"""<article class="card" id="fact-{esc(fid)}"><small class="eyebrow">Input · {esc(f.get("kind", "reported"))}</small><h3>{esc(f["label"])}: {esc(f["rendered"]["display"])}</h3><p>{esc(f["entity"])} · {esc(f["period"])} · {esc(f["unit"])} · scale {esc(f.get("scale", "1"))}</p>{"".join(evidence_html(e) for e in f["evidence"])}<details><summary>Input checks</summary><pre>{esc(json.dumps({"value_check": f["value_check"], "support_review": f["support_review"]}, indent=2))}</pre></details></article>"""
        )
    for cid, c in result["calculations"].items():
        bindings = " ".join(
            f'<a href="#{"calc" if ref in result["calculations"] else "fact"}-{esc(ref)}">{esc(name)} = {esc(ref)}</a>'
            for name, ref in c["inputs"].items()
        )
        inputs.append(
            f"""<article class="card" id="calc-{esc(cid)}"><small class="eyebrow">Python calculation</small><h3>{esc(cid)}: {esc(c["result"]["display"])}</h3><pre>{esc(c["formula"])}</pre><p>{bindings}</p><p>{esc(c.get("comparison_reason", "Same declared entity and period"))}</p><small>Unrounded result: {esc(c["result"]["value"])}. Assumptions: {esc(", ".join(c["assumptions"]) or "none")}.</small></article>"""
        )
    sources = []
    for sid, s in result["sources"].items():
        sources.append(
            f"""<article class="card" id="source-{esc(sid)}"><h3>{esc(s["title"])}</h3><p>{esc(s["origin"])} · coverage: {esc(s["completeness"])}</p><p>{esc(s.get("uri") or "Local uploaded file")}</p><small>{esc(s["authenticity"])}</small><details><summary>Snapshot and metadata</summary><pre>{esc(json.dumps(s, indent=2))}</pre><a href="evidence/{esc(sid)}/raw" download>Download original snapshot</a> · <a href="evidence/{esc(sid)}/units.json" download>Extracted passages</a></details></article>"""
        )
    issues = "".join(f"<li><strong>{esc(i['at'])}</strong>: {esc(i['message'])}</li>" for i in result["issues"])
    warnings = "".join(f"<li>{esc(i['at'])}: {esc(i['message'])}</li>" for i in result["warnings"])
    html = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Citations &amp; Calculations</title><style>{STYLE}</style></head><body>
<header><div class="eyebrow">Citations &amp; Calculations · Local evidence review</div><h1>Follow the evidence.<br>Recompute the figures.</h1><p class="muted">{esc(result["boundary"])}</p><div class="summary"><span class="{"blocked" if blocked else ""}">{esc(result["status"])}</span><span>{len(result["claims"])} claims</span><span>{len(result["facts"])} inputs</span><span>{len(result["calculations"])} calculations</span><span>{len(result["sources"])} sources</span></div><div class="links"><a href="answer.md" download>Answer</a><a href="audit.json" download>Audit data</a><a href="bundle.json" download>Reproduce this bundle</a></div></header>
<main>{'<section class="card blocked"><h2>Blocked checks</h2><ul>' + issues + "</ul></section>" if issues else ""}{'<section class="card"><h2>Caveats</h2><ul>' + warnings + "</ul></section>" if warnings else ""}
<div class="controls"><input id="search" aria-label="Filter claims" placeholder="Search claims"><select id="filter" aria-label="Claim type"><option value="all">All claim types</option><option value="fact">Facts</option><option value="calculated">Calculations</option><option value="inference">Inferences</option><option value="assumption">Assumptions</option></select><button id="print">Print / save PDF</button></div><div class="grid"><section><h2>Answer claims</h2>{"".join(cards)}</section><section><h2>Inputs and calculations</h2>{"".join(inputs)}</section></div><section><h2>Source snapshots</h2>{"".join(sources)}</section></main>
<footer><small>Version {esc(result["tool_version"])} · {esc(result["audited_at"])}<br>Source text is untrusted evidence. This report runs no source scripts and makes no external requests.</small></footer><script>{JS}</script></body></html>'''
    write(out / "review.html", html.encode())
    write(out / "answer.md", ("\n".join(markdown) + "\n").encode())


def export_sources(root, out, result):
    store = Store(root)
    for sid in result["sources"]:
        meta, extracted = store.load(sid)
        folder = Path(out) / "evidence" / sid
        write(folder / "raw", (store.root / "sources" / sid / "raw").read_bytes())
        save_json(folder / "source.json", meta)
        save_json(folder / "units.json", extracted)
