"""Adversarial evidence and calculation checks; no network or model calls."""

import contextlib
from copy import deepcopy
from decimal import Decimal
import io
import json
from pathlib import Path
import tempfile
import unittest

from citations_calculations.acquire import ingest, capture
from citations_calculations.audit import verify, audit_file
from citations_calculations.cli import main
from citations_calculations.demo import run, EXAMPLES
from citations_calculations.numbers import Quantity, decimal, display, evaluate, quantity, tokens
from citations_calculations.store import Store, EvidenceError, save_json
from test_sources import workbook


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        run(self.root)
        self.plan = json.loads((self.root / "input.json").read_text())

    def tearDown(self):
        self.tmp.cleanup()

    def test_end_to_end_all_source_origins_and_exact_results(self):
        r = verify(self.root, self.plan)
        self.assertNotEqual(r["status"], "blocked")
        self.assertEqual({s["origin"] for s in r["sources"].values()}, {"uploaded_file", "web_capture", "tool_capture"})
        self.assertEqual(r["calculations"]["growth"]["result"]["rounded"], "25.0")
        self.assertEqual(r["calculations"]["margin"]["result"]["rounded"], "20.0")
        self.assertEqual(len(r["claims"]), 4)
        self.assertEqual(
            r["facts"]["revenue_current"]["evidence"][0]["context_fields_checked"], ["label", "period", "scale", "unit"]
        )

    def test_altered_source_bytes_extraction_and_metadata_each_fail(self):
        sid = self.plan["facts"][0]["citations"][0]["source_id"]
        for file in ("raw", "units.json", "source.json"):
            with self.subTest(file=file):
                p = self.root / "sources" / sid / file
                original = p.read_bytes()
                try:
                    if file == "source.json":
                        changed = json.loads(original)
                        changed["title"] = "A different claimed source title"
                        p.write_text(json.dumps(changed))
                    else:
                        p.write_bytes(b"{}" if file.endswith("json") else b"Changed source")
                    self.assertEqual(verify(self.root, self.plan)["status"], "blocked")
                finally:
                    p.write_bytes(original)

    def test_wrong_table_column_rejected_even_if_number_exists_elsewhere(self):
        self.plan["facts"][1]["citations"][0]["locator"] = "cell:R2C3"
        self.assertEqual(verify(self.root, self.plan)["status"], "blocked")

    def test_workbook_period_header_and_cached_formula_are_enforced(self):
        path = self.root / "report.xlsx"
        path.write_bytes(workbook())
        sid = ingest(self.root, path)["source_id"]
        f = self.plan["facts"][1]
        f["citations"] = [{"source_id": sid, "locator": "cell:Revenue!B2", "excerpt": "1250"}]
        self.assertNotEqual(verify(self.root, self.plan)["status"], "blocked")
        f["period"] = "FY2024"
        self.assertTrue(any("workbook column header" in i["message"] for i in verify(self.root, self.plan)["issues"]))
        f["period"] = "FY2025"
        f["value"] = "2500"
        f["citations"] = [{"source_id": sid, "locator": "cell:Revenue!C2", "excerpt": "2500"}]
        for c in self.plan["calculations"]:
            c.pop("reported_result", None)
        self.assertTrue(any("formula cache" in i["message"] for i in verify(self.root, self.plan)["issues"]))

    def test_invalid_claim_shape_is_reported_as_blocked_without_render_crash(self):
        self.plan["claims"].append({"id": "invalid", "text": None})
        save_json(self.root / "invalid.json", self.plan)
        result = audit_file(self.root, self.root / "invalid.json", self.root / "invalid-report")
        self.assertEqual(result["status"], "blocked")
        self.assertTrue((self.root / "invalid-report/review.html").is_file())

    def test_explicit_period_unit_scale_and_metric_cannot_be_relabelled(self):
        for field, value in (("period", "FY2024"), ("unit", "EUR"), ("scale", "1"), ("label", "Profit")):
            with self.subTest(field=field):
                p = deepcopy(self.plan)
                p["facts"][1][field] = value
                r = verify(self.root, p)
                self.assertTrue(any("structured source label" in x["message"] for x in r["issues"]))

    def test_captured_company_label_cannot_be_relabelled(self):
        self.plan["facts"][3]["entity"] = "Another Company"
        self.assertTrue(any("entity" in i["message"] for i in verify(self.root, self.plan)["issues"]))

    def test_number_sign_is_checked(self):
        p = self.root / "loss.txt"
        p.write_text("Operating loss was ($250).")
        sid = ingest(self.root, p)["source_id"]
        f = self.plan["facts"][2]
        f.update(value="250", scale="1", citations=[{"source_id": sid, "locator": "line:1", "excerpt": "($250)"}])
        self.plan["calculations"][1].pop("reported_result")
        self.assertEqual(verify(self.root, self.plan)["status"], "blocked")
        f["value"] = "-250"
        self.assertNotEqual(verify(self.root, self.plan)["status"], "blocked")

    def test_a_citation_cannot_quote_part_of_a_number_or_drop_its_sign(self):
        f = self.plan["facts"][1]
        for c in self.plan["calculations"]:
            c.pop("reported_result", None)
        for value in ("25", "250", "125"):
            with self.subTest(value=value):
                f["value"] = value
                f["citations"][0]["excerpt"] = value
                self.assertEqual(verify(self.root, self.plan)["status"], "blocked")
        path = self.root / "loss.txt"
        path.write_text("Net loss (1,250)\n")
        sid = ingest(self.root, path)["source_id"]
        f["citations"] = [{"source_id": sid, "locator": "line:1", "excerpt": "1,250"}]
        f["value"] = "1250"
        self.assertEqual(verify(self.root, self.plan)["status"], "blocked")
        f["citations"][0]["excerpt"] = "(1,250)"
        f["value"] = "-1250"
        r = verify(self.root, self.plan)
        self.assertFalse(any(i["at"] == "fact:revenue_current" for i in r["issues"]), r["issues"])

    def test_a_real_citation_cannot_support_an_invented_numerical_input(self):
        self.plan["facts"][1]["value"] = "1260"
        for c in self.plan["calculations"]:
            c.pop("reported_result", None)
        result = verify(self.root, self.plan)
        self.assertTrue(any("value or sign is absent" in i["message"] for i in result["issues"]))

    def test_source_excerpt_must_match_exact_locator(self):
        self.plan["claims"][-1]["citations"][0]["excerpt"] = "The business collapsed."
        self.assertEqual(verify(self.root, self.plan)["status"], "blocked")

    def test_source_id_replacement_is_detected(self):
        self.plan["facts"][0]["citations"][0]["source_id"] = "s_" + "0" * 64
        self.assertEqual(verify(self.root, self.plan)["status"], "blocked")

    def test_missing_unclear_and_contradictory_support_reviews_block(self):
        for target in ("facts", "claims"):
            for verdict in (None, "unclear", "contradicts"):
                with self.subTest(target=target, verdict=verdict):
                    p = deepcopy(self.plan)
                    if verdict:
                        p[target][0]["review"]["verdict"] = verdict
                    else:
                        p[target][0].pop("review")
                    self.assertEqual(verify(self.root, p)["status"], "blocked")

    def test_raw_claim_number_is_not_accepted_beside_a_valid_citation(self):
        self.plan["claims"][-1]["text"] = "The subscription business grew by 70%."
        self.assertTrue(any("Unbound number" in i["message"] for i in verify(self.root, self.plan)["issues"]))

    def test_malformed_or_blank_reviews_block_without_crashing(self):
        for target in ("facts", "claims"):
            for invalid in (
                ["supports"],
                "supports",
                1,
                {"verdict": "supports", "reviewer": "codex", "rationale": "   "},
                {"verdict": "supports", "reviewer": "codex", "rationale": True},
            ):
                with self.subTest(target=target, review=invalid):
                    p = deepcopy(self.plan)
                    p[target][0]["review"] = invalid
                    self.assertEqual(verify(self.root, p)["status"], "blocked")

    def test_failed_claim_cannot_import_derived_evidence_or_dependencies(self):
        self.plan["claims"][0].update(
            text="Growth was 900%.",
            evidence=[{"source_id": "forged"}],
            fact_dependencies=["invented"],
            support_review={"verdict": "supports", "reviewer": "human", "rationale": "Cached pass"},
        )
        save_json(self.root / "forged.json", self.plan)
        r = audit_file(self.root, self.root / "forged.json", self.root / "forged-report")
        self.assertEqual(r["status"], "blocked")
        result = json.loads((self.root / "forged-report/audit.json").read_text())
        blocked = result["claims"][0]
        self.assertEqual(blocked["evidence"], [])
        self.assertEqual(blocked["fact_dependencies"], [])
        self.assertIsNone(blocked["support_review"])

    def test_schema_boolean_and_empty_explanations_are_refused(self):
        p = deepcopy(self.plan)
        p["schema_version"] = True
        with self.assertRaises(EvidenceError):
            verify(self.root, p)
        for invalid in ("  ", True, ["comparison"]):
            p = deepcopy(self.plan)
            p["calculations"][0]["comparison_reason"] = invalid
            self.assertEqual(verify(self.root, p)["status"], "blocked")
            p = deepcopy(self.plan)
            p["claims"].append(
                {"id": "unsupported", "kind": "assumption", "text": "Demand will strengthen.", "reason": invalid}
            )
            self.assertEqual(verify(self.root, p)["status"], "blocked")

    def test_extreme_calculation_is_blocked_before_display(self):
        self.plan["facts"].append(
            {
                "id": "large",
                "kind": "assumption",
                "label": "Extreme stress input",
                "value": "1e30",
                "unit": "1",
                "entity": "Northstar",
                "period": "Stress case",
                "reason": "Test numerical boundaries.",
            }
        )
        self.plan["facts"].append(
            {
                "id": "power",
                "kind": "assumption",
                "label": "Stress exponent",
                "value": "8",
                "unit": "1",
                "entity": "Northstar",
                "period": "Stress case",
                "reason": "Declared stress-test parameter.",
            }
        )
        self.plan["calculations"].append(
            {"id": "overflow", "formula": "x ** power", "inputs": {"x": "large", "power": "power"}, "unit": "1"}
        )
        r = verify(self.root, self.plan)
        self.assertEqual(r["status"], "blocked")
        self.assertTrue(any("outside supported range" in i["message"] for i in r["issues"]))

    def test_rendered_numbers_come_from_recomputed_results(self):
        self.plan["cached_verdict"] = "checks_passed"
        self.plan["cached_results"] = {"growth": "999"}
        r = verify(self.root, self.plan)
        self.assertIn("25.0%", r["claims"][0]["rendered_text"])
        self.plan["calculations"][0]["reported_result"] = "999"
        self.assertEqual(verify(self.root, self.plan)["status"], "blocked")

    def test_mixed_periods_require_a_declared_comparison(self):
        self.plan["calculations"][0].pop("period_policy")
        self.assertTrue(any("Mixed period" in i["message"] for i in verify(self.root, self.plan)["issues"]))

    def test_mixed_entities_require_a_declared_comparison(self):
        p = self.root / "other.txt"
        p.write_text("Revenue was 1250.")
        sid = ingest(self.root, p)["source_id"]
        self.plan["facts"][1].update(
            entity="Other Company", citations=[{"source_id": sid, "locator": "line:1", "excerpt": "1250"}]
        )
        self.assertTrue(any("Mixed entity" in i["message"] for i in verify(self.root, self.plan)["issues"]))

    def test_bad_output_unit_and_unknown_dependencies_block(self):
        for change in ({"unit": "USD"}, {"inputs": {"current": "missing", "prior": "revenue_prior"}}):
            p = deepcopy(self.plan)
            p["calculations"][0].update(change)
            p["calculations"][0].pop("reported_result")
            self.assertEqual(verify(self.root, p)["status"], "blocked")

    def test_cycle_is_detected(self):
        self.plan["calculations"][0]["inputs"]["current"] = "margin"
        self.plan["calculations"][1]["inputs"]["revenue"] = "growth"
        self.assertTrue(any("cycle" in i["message"] for i in verify(self.root, self.plan)["issues"]))

    def test_chained_calculations_retain_transitive_fact_inputs(self):
        self.plan["calculations"].append(
            {
                "id": "annual_change",
                "formula": "rate + margin",
                "inputs": {"rate": "growth", "margin": "margin"},
                "unit": "%",
                "places": 1,
                "period_policy": "compare",
                "comparison_reason": "Synthetic arithmetic only: not a meaningful investment metric.",
            }
        )
        r = verify(self.root, self.plan)
        self.assertEqual(r["calculations"]["annual_change"]["result"]["rounded"], "45.0")
        self.assertEqual(
            r["calculations"]["annual_change"]["fact_dependencies"], ["profit", "revenue_current", "revenue_prior"]
        )

    def test_assumptions_are_visible_and_cannot_be_called_reported_facts(self):
        self.plan["facts"].append(
            {
                "id": "forecast_rate",
                "kind": "assumption",
                "label": "Growth assumption",
                "value": "10",
                "unit": "%",
                "entity": "Northstar",
                "period": "Next year",
                "reason": "An illustrative scenario, not a forecast.",
            }
        )
        self.plan["claims"].append(
            {
                "id": "forecast",
                "kind": "fact",
                "text": "Growth will be {{fact:forecast_rate}}.",
                "review": deepcopy(self.plan["claims"][0]["review"]),
            }
        )
        self.assertEqual(verify(self.root, self.plan)["status"], "blocked")
        self.plan["claims"][-1].update(kind="assumption", reason="Illustrative scenario only")
        r = verify(self.root, self.plan)
        self.assertEqual(r["status"], "checks_passed_with_caveats")
        self.assertTrue(any("assumption" in w["message"].casefold() for w in r["warnings"]))

    def test_historical_cutoff_rejects_declared_later_publication(self):
        file = self.root / "later.txt"
        file.write_text("Later source evidence.")
        sid = ingest(self.root, file, published_at="2026-02-01T00:00:00Z")["source_id"]
        self.plan["as_of"] = "2026-01-01T00:00:00Z"
        self.plan["claims"][-1]["citations"] = [
            {"source_id": sid, "locator": "line:1", "excerpt": "Later source evidence."}
        ]
        self.assertEqual(verify(self.root, self.plan)["status"], "blocked")

    def test_partial_tool_response_stays_visible(self):
        payload = json.loads((EXAMPLES / "tool-response.json").read_text())
        payload["next_cursor"] = "another-page"
        file = self.root / "partial.json"
        save_json(file, payload)
        sid = capture(self.root, file)["source_id"]
        self.plan["facts"][3]["citations"][0]["source_id"] = sid
        r = verify(self.root, self.plan)
        self.assertEqual(r["status"], "checks_passed_with_caveats")
        self.assertTrue(any("partial" in w["message"] for w in r["warnings"]))

    def test_duplicate_ids_empty_answer_and_missing_number_marker_block(self):
        for edit in ("duplicate", "empty", "marker"):
            p = deepcopy(self.plan)
            if edit == "duplicate":
                p["facts"].append(p["facts"][0])
            elif edit == "empty":
                p["claims"] = []
            else:
                p["claims"][0]["text"] = "The growth was {{calc:missing}}."
            with self.subTest(edit=edit):
                try:
                    r = verify(self.root, p)
                except EvidenceError:
                    continue
                self.assertEqual(r["status"], "blocked")

    def test_period_and_entity_markers_show_each_facts_declared_labels(self):
        self.plan["claims"][0]["text"] = (
            "{{entity:revenue_current}} revenue grew {{calc:growth}} from {{period:revenue_prior}} "
            "to {{period:revenue_current}}."
        )
        claim = verify(self.root, self.plan)["claims"][0]
        self.assertEqual(claim["status"], "checks_passed")
        self.assertEqual(claim["rendered_text"], "Northstar revenue grew 25.0% from FY2024 to FY2025.")
        self.assertEqual(claim["fact_dependencies"], ["revenue_current", "revenue_prior"])
        save_json(self.root / "input.json", self.plan)
        audit_file(self.root, self.root / "input.json", self.root / "report")
        self.assertIn("from FY2024 to FY2025", (self.root / "report/review.html").read_text())

    def test_a_period_marker_must_name_a_valid_fact(self):
        self.plan["claims"][0]["text"] = "Revenue grew {{calc:growth}} in {{period:growth}}."
        r = verify(self.root, self.plan)
        self.assertEqual(r["claims"][0]["status"], "blocked")

    def test_spelled_out_numbers_in_claim_text_are_refused(self):
        refused = (
            "Revenue grew {{calc:growth}} in fiscal twenty twenty-five.",
            "Revenue grew {{calc:growth}} in the year ended January thirty-first.",
            "Growth is current over prior, times one hundred: {{calc:growth}}.",
            "Growth was {{calc:growth}}, about five percent above plan.",
            "Growth was {{calc:growth}}, or current revenue over prior revenue − one.",
        )
        for text in refused:
            with self.subTest(text=text):
                self.plan["claims"][0]["text"] = text
                r = verify(self.root, self.plan)
                self.assertEqual(r["claims"][0]["status"], "blocked")
                self.assertIn("Spelled-out number", r["issues"][0]["message"])
        allowed = (
            "One of the segments grew, and revenue grew {{calc:growth}}.",
            "Revenue grew {{calc:growth}} in the first full year after the launch.",
            "Hundreds of customers joined, and revenue grew {{calc:growth}}.",
        )
        for text in allowed:
            with self.subTest(text=text):
                self.plan["claims"][0]["text"] = text
                self.assertEqual(verify(self.root, self.plan)["claims"][0]["status"], "checks_passed")

    def test_failed_audit_cli_returns_nonzero_and_keeps_blocked_claims_visible(self):
        self.plan["claims"][0]["text"] = "Growth was 900%."
        save_json(self.root / "bad.json", self.plan)
        with contextlib.redirect_stdout(io.StringIO()):
            code = main(
                [
                    "audit",
                    "--workspace",
                    str(self.root),
                    "--bundle",
                    str(self.root / "bad.json"),
                    "--output",
                    str(self.root / "bad-report"),
                ]
            )
        self.assertEqual(code, 2)
        self.assertIn("checks failed", (self.root / "bad-report/answer.md").read_text())
        self.assertIn("Unbound number", (self.root / "bad-report/review.html").read_text())

    def test_report_exports_valid_source_snapshots_and_escapes_markup(self):
        self.plan["claims"][-1]["text"] = '<script>alert("bad")</script> The company release describes a launch.'
        save_json(self.root / "escape.json", self.plan)
        audit_file(self.root, self.root / "escape.json", self.root / "escape")
        html = (self.root / "escape/review.html").read_text()
        self.assertNotIn("<script>alert", html)
        self.assertIn("&lt;script&gt;", html)
        data = json.loads((self.root / "escape/audit.json").read_text())
        for sid in data["sources"]:
            self.assertTrue((self.root / "escape/evidence" / sid / "raw").is_file())

    def test_raw_and_metadata_symlinks_cannot_escape_workspace(self):
        sid = self.plan["facts"][0]["citations"][0]["source_id"]
        with tempfile.TemporaryDirectory() as external:
            for name in ("raw", "source.json"):
                p = self.root / "sources" / sid / name
                body = p.read_bytes()
                other = Path(external) / name
                other.write_bytes(body)
                p.unlink()
                p.symlink_to(other)
                try:
                    with self.assertRaisesRegex(EvidenceError, "escapes"):
                        Store(self.root).load(sid)
                finally:
                    p.unlink()
                    p.write_bytes(body)


class CalculatorTests(unittest.TestCase):
    def test_decimal_not_binary_float(self):
        q = evaluate("a + b", {"a": quantity("0.1", "USD"), "b": quantity("0.2", "USD")})
        self.assertEqual(q.value, Decimal("0.3"))
        with self.assertRaises(EvidenceError):
            decimal(0.1)

    def test_currency_scale_ratio_and_percentage_points(self):
        q = evaluate("current - prior", {"current": quantity("35", "%"), "prior": quantity("30", "%")})
        self.assertEqual(display(q, "pp", places=1)["rounded"], "5.0")
        q = evaluate(
            "revenue / shares", {"revenue": quantity("2", "USD", "1000000"), "shares": quantity("1000000", "shares")}
        )
        self.assertEqual(display(q, "USD/shares")["rounded"], "2.00")

    def test_currency_mismatch_zero_denominator_and_unused_inputs(self):
        for expr, inputs in [
            ("a + b", {"a": quantity("1", "USD"), "b": quantity("1", "EUR")}),
            ("a / b", {"a": quantity("1", "USD"), "b": quantity("0", "USD")}),
            ("a", {"a": quantity("1", "USD"), "b": quantity("1", "USD")}),
        ]:
            with self.subTest(expr=expr), self.assertRaises(EvidenceError):
                evaluate(expr, inputs)

    def test_no_python_execution_or_unbound_numerical_assumption(self):
        for expression in (
            "__import__('os').system('bad')",
            "a.__class__",
            "[x for x in a]",
            "a * 1.2",
            "a[0]",
            "a ** 100",
            "True",
            "2",
        ):
            with self.subTest(expression=expression), self.assertRaises(EvidenceError):
                evaluate(expression, {"a": quantity("1", "USD")})

    def test_bounded_input_output_and_formula_complexity(self):
        for value in ("NaN", "Infinity", "1e31", "1e-31", "1" * 200):
            with self.subTest(value=value), self.assertRaises(EvidenceError):
                decimal(value)
        with self.assertRaises(EvidenceError):
            evaluate("x+" * 501 + "x", {"x": quantity("1", "1")})
        with self.assertRaises(EvidenceError):
            evaluate("x*x", {"x": quantity("1e20", "1")})

    def test_aggregations_and_no_numeric_value_invention(self):
        inputs = {"a": quantity("20", "USD"), "b": quantity("10", "USD")}
        self.assertEqual(evaluate("mean(a,b)", inputs).value, 15)
        self.assertEqual(evaluate("max(a,b)", inputs).value, 20)
        self.assertEqual(evaluate("min(a,b)", inputs).value, 10)
        self.assertEqual(evaluate("sum(a,b)", inputs).value, 30)

    def test_formula_errors_say_what_is_wrong(self):
        q = {"a": Quantity(Decimal(1), ())}
        with self.assertRaisesRegex(EvidenceError, "nested too deeply"):
            evaluate("(" * 300 + "a" + ")" * 300, q)
        with self.assertRaisesRegex(EvidenceError, "Invalid formula: "):
            evaluate("a +", q)

    def test_negative_parentheses_and_percent_token(self):
        self.assertEqual(
            [n for _, n in tokens("Loss ($1,200.50); rate −4.2%; income 10.")],
            [Decimal("-1200.50"), Decimal("-4.2"), Decimal("10")],
        )

    def test_malformed_comma_group_cannot_be_read_as_a_different_number(self):
        self.assertNotIn(Decimal("12"), [n for _, n in tokens("Values: 1,2")])

    def test_unrounded_result_retains_decimal_precision(self):
        q = evaluate("a / b", {"a": quantity("1", "1"), "b": quantity("3", "1")})
        self.assertEqual(len(display(q, "1")["value"].split(".")[1]), 60)

    def test_scale_and_precision_are_bounded(self):
        for scale in ("0", "-1"):
            with self.assertRaises(EvidenceError):
                quantity("1", "USD", scale)
        for places in (-1, 9, True):
            with self.assertRaises(EvidenceError):
                display(Quantity(Decimal(1), ()), "1", places=places)


if __name__ == "__main__":
    unittest.main()
