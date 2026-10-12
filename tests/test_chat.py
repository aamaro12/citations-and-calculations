"""Chat delivery must preserve checked figures, failures and pinned provenance."""

import contextlib
import io
import json
from pathlib import Path
import re
import tempfile
import unittest

from citations_calculations.audit import audit_file
from citations_calculations.chat import render_chat
from citations_calculations.cli import main
from citations_calculations.demo import run, REVIEW
from citations_calculations.store import EvidenceError, save_json


LINK = re.compile(r"(?<!\\)\[([^\]]*)\]\(<([^>]+)>\)")


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="citecalc chat ")
        self.root = Path(self.tmp.name)
        self.first = run(self.root)
        self.plan = json.loads((self.root / "input.json").read_text())

    def tearDown(self):
        self.tmp.cleanup()

    def audit(self):
        save_json(self.root / "input.json", self.plan)
        return audit_file(self.root, self.root / "input.json", self.root / "report")

    def test_chat_returns_linked_figures_and_exact_citation_map(self):
        chat = self.first["chat"]
        text = chat["answer_markdown"]
        plain = LINK.sub(lambda m: m[1], text)
        self.assertIn("Revenue grew 25.0% to 1,250 million USD.", plain)
        self.assertIn("The operating margin was 20.0%.", plain)
        self.assertNotIn("{{", text)
        self.assertEqual(len(chat["calculations"]), 2)
        refs = {(c["source_id"], c["locator"], c["excerpt"]) for c in chat["citations"]}
        expected = {(e["source_id"], e["locator"], e["excerpt"]) for f in self.plan["facts"] for e in f["citations"]}
        self.assertTrue(expected <= refs)
        for _, target in LINK.findall(text):
            self.assertTrue(target.startswith("/"))
            self.assertTrue(target.endswith(":1"))
            self.assertTrue(Path(target[:-2]).is_file())
            self.assertIn("/chat/a_" + chat["audit_sha256"] + "/", target)
        for path in Path(chat["audit_target"][:-2]).parent.glob("*.md"):
            for _, target in LINK.findall(path.read_text()):
                if target.startswith("/"):
                    self.assertTrue(Path(target[:-2]).is_file(), target)

    def test_sources_include_exact_unit_context_and_original_snapshot(self):
        e = next(e for e in self.first["chat"]["citations"] if e["locator"] == "cell:R3C3")
        note = Path(e["target"][:-2]).read_text()
        self.assertIn("1250", note)
        self.assertIn("FY2025", note)
        self.assertIn("Context", note)
        self.assertIn("Saved copy", note)
        audit = json.loads(Path(self.first["chat"]["audit_target"][:-2]).read_text())
        self.assertIn("raw_sha256", audit["sources"][e["source_id"]])

    def test_answer_command_reaudits_instead_of_reading_cached_verdict(self):
        cached = self.root / "report/audit.json"
        data = json.loads(cached.read_text())
        data["status"] = "checks_passed"
        data["calculations"]["growth"]["result"]["display"] = "999%"
        save_json(cached, data)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(
                [
                    "answer",
                    "--workspace",
                    str(self.root),
                    "--bundle",
                    str(self.root / "input.json"),
                    "--output",
                    str(self.root / "report"),
                ]
            )
        self.assertEqual(code, 0)
        self.assertIn("[25.0%]", output.getvalue())
        self.assertNotIn("999%", output.getvalue())
        self.assertFalse(output.getvalue().startswith("{"))
        sid = self.plan["facts"][0]["citations"][0]["source_id"]
        (self.root / "sources" / sid / "raw").write_bytes(b"altered")
        with contextlib.redirect_stdout(io.StringIO()) as blocked:
            code = main(
                [
                    "answer",
                    "--workspace",
                    str(self.root),
                    "--bundle",
                    str(self.root / "input.json"),
                    "--output",
                    str(self.root / "report"),
                ]
            )
        self.assertEqual(code, 2)
        self.assertIn("Draft", blocked.getvalue())
        self.assertIn("Source bytes or extraction changed", blocked.getvalue())
        self.assertNotIn("[25.0%]", blocked.getvalue())

    def test_blocked_claims_remain_visible_without_checked_figure_links(self):
        self.plan["claims"][0]["text"] = "Growth was 900%."
        chat = self.audit()["chat"]
        self.assertEqual(chat["status"], "blocked")
        self.assertIn("**Blocked claim:** Growth was 900%.", chat["answer_markdown"])
        self.assertIn("Unbound number", chat["answer_markdown"])
        self.assertNotIn("[900%]", chat["answer_markdown"])
        self.assertIn("**Caveats:**", chat["answer_markdown"])

    def test_later_audit_cannot_retarget_existing_chat_links(self):
        old = self.first["chat"]
        paths = [Path(old["audit_target"][:-2])] + [Path(c["target"][:-2]) for c in old["calculations"]]
        bodies = {p: p.read_bytes() for p in paths}
        self.plan["calculations"][0]["formula"] = "prior / current - 1"
        self.plan["calculations"][0].pop("reported_result")
        new = self.audit()["chat"]
        self.assertNotEqual(old["audit_sha256"], new["audit_sha256"])
        self.assertIn("[-20.0%]", new["answer_markdown"])
        for p, body in bodies.items():
            self.assertEqual(p.read_bytes(), body)

    def test_assumptions_and_transitive_formula_inputs_are_visible(self):
        self.plan["facts"].append(
            {
                "id": "multiple",
                "kind": "assumption",
                "label": "Scenario multiple",
                "value": "1.1",
                "unit": "1",
                "entity": "Northstar",
                "period": "FY2026",
                "reason": "Illustrative sensitivity, not management guidance.",
            }
        )
        self.plan["calculations"].append(
            {
                "id": "scenario",
                "formula": "revenue * multiple",
                "inputs": {"revenue": "revenue_current", "multiple": "multiple"},
                "unit": "USD",
                "scale": "1000000",
                "places": 0,
            }
        )
        self.plan["calculations"].append(
            {
                "id": "follow_on",
                "formula": "scenario / revenue",
                "inputs": {"scenario": "scenario", "revenue": "revenue_current"},
                "unit": "1",
                "places": 1,
            }
        )
        self.plan["claims"].append(
            {
                "id": "scenario_claim",
                "kind": "inference",
                "text": "Scenario revenue would be {{calc:scenario}}.",
                "review": REVIEW,
            }
        )
        chat = self.audit()["chat"]
        self.assertNotEqual(chat["status"], "blocked")
        self.assertIn("**Uses assumptions:** **Inference:**", chat["answer_markdown"])
        assumption = next(f for f in chat["facts"] if f["id"] == "multiple")
        self.assertEqual(assumption["citations"], [])
        scenario = next(c for c in chat["calculations"] if c["id"] == "scenario")
        self.assertEqual(scenario["assumptions"], ["multiple"])
        note = Path(scenario["target"][:-2]).read_text()
        self.assertIn("Illustrative sensitivity", note)
        follow_on = next(c for c in chat["calculations"] if c["id"] == "follow_on")
        self.assertIn(scenario["target"], Path(follow_on["target"][:-2]).read_text())

    def test_claim_markup_cannot_inject_links_images_or_html(self):
        self.plan["claims"][-1]["text"] = (
            '<script>alert("bad")</script> [fake](https://evil.example) ![image](https://evil.example/image) The release describes a launch.'
        )
        text = self.audit()["chat"]["answer_markdown"]
        self.assertNotIn("<script>", text)
        self.assertNotIn("[fake](", text)
        self.assertNotIn("![image](", text)
        self.assertIn(r"\[fake\]", text)
        self.assertTrue(all(target.startswith("/") for _, target in LINK.findall(text)))

    def test_chat_export_tampering_and_existing_note_changes_are_refused(self):
        result = json.loads((self.root / "report/audit.json").read_text())
        exported = next((self.root / "report/evidence").glob("*/raw"))
        body = exported.read_bytes()
        exported.write_bytes(b"tampered")
        with self.assertRaisesRegex(EvidenceError, "Exported source changed"):
            render_chat(self.root / "report", result)
        exported.write_bytes(body)
        note = Path(self.first["chat"]["citations"][0]["target"][:-2])
        note.write_text("Altered details")
        with self.assertRaisesRegex(EvidenceError, "Chat snapshot content changed"):
            render_chat(self.root / "report", result)

    def test_chat_snapshot_symlink_cannot_escape_output_folder(self):
        report = self.root / "other-report"
        report.mkdir()
        with tempfile.TemporaryDirectory() as external:
            (report / "chat").symlink_to(external, target_is_directory=True)
            with self.assertRaisesRegex(EvidenceError, "escapes"):
                audit_file(self.root, self.root / "input.json", report)
            self.assertEqual(list(Path(external).iterdir()), [])
