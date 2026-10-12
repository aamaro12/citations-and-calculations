"""Synthetic, reproducible end-to-end use of all 3 source origins; no API key or network."""

from pathlib import Path

from .acquire import ingest, capture
from .audit import audit_file
from .store import save_json

EXAMPLES = Path(__file__).resolve().parent / "example_data"
REVIEW = {
    "verdict": "supports",
    "reviewer": "codex",
    "rationale": "Synthetic example: inspect the cell and its adjacent metric, period, unit and scale labels.",
}


def run(out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    local = ingest(out, EXAMPLES / "financials.csv")["source_id"]
    web = ingest(
        out, EXAMPLES / "company-release.html", uri="https://northstar.example/annual-update", completeness="complete"
    )["source_id"]
    tool = capture(out, EXAMPLES / "tool-response.json")["source_id"]

    def f(fid, label, value, period, at, unit="USD", scale="1000000", source=local):
        return {
            "id": fid,
            "kind": "reported",
            "label": label,
            "value": str(value),
            "unit": unit,
            "scale": scale,
            "places": 0,
            "entity": "Northstar",
            "period": period,
            "citations": [{"source_id": source, "locator": at, "excerpt": str(value)}],
            "review": REVIEW,
        }

    facts = [
        f("revenue_prior", "Revenue", 1000, "FY2024", "cell:R2C3"),
        f("revenue_current", "Revenue", 1250, "FY2025", "cell:R3C3"),
        f("profit", "Operating profit", 250, "FY2025", "cell:R4C3"),
        f(
            "customers",
            "Active customers",
            500,
            "FY2025",
            "json:/tool_result/active_customers",
            unit="customers",
            scale="1",
            source=tool,
        ),
    ]
    calculations = [
        {
            "id": "growth",
            "formula": "current / prior - 1",
            "inputs": {"current": "revenue_current", "prior": "revenue_prior"},
            "unit": "%",
            "places": 1,
            "period_policy": "compare",
            "comparison_reason": "Compare revenue in consecutive fiscal years for the same company.",
            "reported_result": "25.0",
        },
        {
            "id": "margin",
            "formula": "profit / revenue",
            "inputs": {"profit": "profit", "revenue": "revenue_current"},
            "unit": "%",
            "places": 1,
            "reported_result": "20.0",
        },
    ]
    claims = [
        {
            "id": "growth_claim",
            "kind": "calculated",
            "text": "Revenue grew {{calc:growth}} to {{fact:revenue_current}}.",
            "review": REVIEW,
        },
        {
            "id": "margin_claim",
            "kind": "calculated",
            "text": "The operating margin was {{calc:margin}}.",
            "review": REVIEW,
        },
        {
            "id": "customer_claim",
            "kind": "fact",
            "text": "The CRM export reported {{fact:customers}} as active.",
            "review": REVIEW,
        },
        {
            "id": "product_claim",
            "kind": "fact",
            "text": "The company release links subscription expansion to the analytics product launch.",
            "citations": [
                {
                    "source_id": web,
                    "locator": "block:3",
                    "excerpt": "Northstar's subscription business expanded following the launch of its analytics product.",
                }
            ],
            "review": {
                "verdict": "supports",
                "reviewer": "codex",
                "rationale": "This reports what the synthetic release says; it does not independently establish causality.",
            },
        },
    ]
    bundle = {"schema_version": 1, "facts": facts, "calculations": calculations, "claims": claims}
    save_json(out / "input.json", bundle)
    return audit_file(out, out / "input.json", out / "report")
