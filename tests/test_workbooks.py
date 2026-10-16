"""Excel workbooks: numbers as Excel shows them, headers above each cell, and limits that fail fast."""

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from citations_calculations.acquire import ingest
from citations_calculations.audit import audit_file, verify
from citations_calculations.cli import main
from citations_calculations.extract import extract
from citations_calculations.store import EvidenceError, Store
from test_sources import zipped

NS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
STYLES = (
    f"<styleSheet {NS}><numFmts>"
    '<numFmt numFmtId="164" formatCode="0.0%"/>'
    '<numFmt numFmtId="165" formatCode="#,##0;#,##0"/>'
    '<numFmt numFmtId="166" formatCode="#,##0;(#,##0);&quot;-&quot;"/>'
    "</numFmts><cellXfs>"
    '<xf numFmtId="0"/><xf numFmtId="164"/><xf numFmtId="3"/><xf numFmtId="165"/><xf numFmtId="166"/><xf numFmtId="15"/>'
    "</cellXfs></styleSheet>"
)


def book(sheets, styles=None):
    names = list(sheets)
    files = {
        "xl/workbook.xml": f'<workbook {NS} xmlns:r="{REL}"><sheets>'
        + "".join(f'<sheet name="{n}" sheetId="{i}" r:id="rId{i}"/>' for i, n in enumerate(names, 1))
        + "</sheets></workbook>",
        "xl/_rels/workbook.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(f'<Relationship Id="rId{i}" Target="worksheets/sheet{i}.xml"/>' for i in range(1, len(names) + 1))
        + "</Relationships>",
    }
    for i, body in enumerate(sheets.values(), 1):
        files[f"xl/worksheets/sheet{i}.xml"] = (
            f"<worksheet {NS}>{body}</worksheet>" if body.startswith("<sheet") else body
        )
    if styles:
        files["xl/styles.xml"] = styles
    return zipped(files)


def grid(*rows, merges=()):
    body = "<sheetData>" + "".join(f'<row r="{r}">{"".join(cells)}</row>' for r, *cells in rows) + "</sheetData>"
    if merges:
        body += "<mergeCells>" + "".join(f'<mergeCell ref="{m}"/>' for m in merges) + "</mergeCells>"
    return body


def n(ref, value, style=None):
    return f'<c r="{ref}"' + (f' s="{style}"' if style is not None else "") + f"><v>{value}</v></c>"


def t(ref, text):
    return f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>'


def cells(data, **options):
    return {u["locator"]: u for u in extract(data, "xlsx", **options)["units"]}


FORMATTED = book(
    {
        "Model": grid(
            (1, t("A1", "Metric"), t("B1", "FY2025")),
            (
                2,
                t("A2", "Tax rate"),
                n("B2", "0.25", 1),
                n("C2", "3613.857118", 2),
                n("D2", "-1250", 3),
                n("E2", "0", 4),
                n("F2", "0.30000000000000004", 0),
                n("G2", "45291", 5),
                n("H2", "1250", 0),
                n("I2", "-3614.2", 4),
            ),
        )
    },
    STYLES,
)


class DisplayTests(unittest.TestCase):
    def test_numbers_are_indexed_as_excel_shows_them(self):
        units = cells(FORMATTED)
        shown = {at.split("!")[1]: u["text"] for at, u in units.items()}
        self.assertEqual(shown["B2"], "25.0%")
        self.assertEqual(shown["C2"], "3,614")
        self.assertEqual(shown["F2"], "0.3")
        self.assertEqual(shown["G2"], "31-Dec-23")
        self.assertEqual(shown["H2"], "1250")
        self.assertEqual(shown["I2"], "(3,614)")
        self.assertEqual(units["cell:Model!B2"]["context"]["stored"], "0.25")
        self.assertEqual(units["cell:Model!B2"]["context"]["number_format"], "0.0%")
        self.assertNotIn("stored", units["cell:Model!H2"]["context"])

    def test_a_format_that_hides_a_sign_or_every_digit_keeps_the_stored_value(self):
        units = cells(FORMATTED)
        self.assertEqual(units["cell:Model!D2"]["text"], "-1250")
        self.assertEqual(units["cell:Model!D2"]["context"]["shown"], "1,250")
        self.assertEqual(units["cell:Model!E2"]["text"], "0")
        self.assertEqual(units["cell:Model!E2"]["context"]["shown"], "-")

    def test_shown_and_stored_values_are_both_citable_and_searchable(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "model.xlsx"
            path.write_bytes(FORMATTED)
            sid = ingest(td, path)["source_id"]
            self.assertIn("cell:Model!C2", [u["locator"] for u in Store(td).search(sid, "3,614")["units"]])
            self.assertEqual([u["locator"] for u in Store(td).search(sid, "3613.857")["units"]], ["cell:Model!C2"])

            def check(locator, excerpt, value, unit):
                fact = {
                    "id": "f",
                    "label": "Tax rate",
                    "entity": "Harbour",
                    "period": "FY2025",
                    "unit": unit,
                    "value": value,
                    "citations": [{"source_id": sid, "locator": locator, "excerpt": excerpt}],
                    "review": {"verdict": "supports", "reviewer": "codex", "rationale": "The row is the tax rate."},
                }
                claim = {"id": "c", "kind": "fact", "text": "The rate was {{fact:f}}.", "review": fact["review"]}
                return verify(td, {"schema_version": 1, "facts": [fact], "claims": [claim]})["status"]

            self.assertNotEqual(check("cell:Model!B2", "25.0%", "25.0", "%"), "blocked")
            self.assertNotEqual(check("cell:Model!B2", "0.25", "0.25", "1"), "blocked")
            self.assertNotEqual(check("cell:Model!C2", "3,614", "3614", "USD"), "blocked")
            self.assertNotEqual(check("cell:Model!C2", "3613.857118", "3613.857118", "USD"), "blocked")
            self.assertEqual(check("cell:Model!C2", "3,615", "3615", "USD"), "blocked")

    def test_the_citation_page_shows_the_stored_value_beside_the_shown_one(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "model.xlsx"
            path.write_bytes(FORMATTED)
            sid = ingest(td, path)["source_id"]
            review = {"verdict": "supports", "reviewer": "codex", "rationale": "The row is the tax rate."}
            fact = {
                "id": "rate",
                "label": "Tax rate",
                "entity": "Harbour",
                "period": "FY2025",
                "unit": "%",
                "value": "25.0",
                "places": 1,
                "citations": [{"source_id": sid, "locator": "cell:Model!B2", "excerpt": "25.0%"}],
                "review": review,
            }
            claim = {"id": "c", "kind": "fact", "text": "The tax rate was {{fact:rate}}.", "review": review}
            bundle = Path(td) / "bundle.json"
            bundle.write_text(json.dumps({"schema_version": 1, "facts": [fact], "claims": [claim]}))
            chat = audit_file(td, bundle, Path(td) / "report")["chat"]
            note = Path(chat["citations"][0]["target"][:-2]).read_text()
            self.assertIn("**Stored value:** 0.25, shown as 25.0%", note)


class HeaderTests(unittest.TestCase):
    QUARTERS = book(
        {
            "Plan": grid(
                (1, t("B1", "FY2026")),
                (2, t("B2", "Q1"), t("C2", "Q2")),
                (3, t("A3", "Revenue"), n("B3", "100"), n("C3", "120")),
                (5, t("B5", "FY2025")),
                (6, t("A6", "Revenue"), n("B6", "90")),
                merges=("B1:C1",),
            )
        }
    )

    def test_headers_come_from_the_rows_above_including_merged_cells(self):
        units = cells(self.QUARTERS)
        self.assertEqual(units["cell:Plan!C3"]["context"]["column_header"], {"B1": "FY2026", "C2": "Q2"})
        self.assertEqual(units["cell:Plan!B6"]["context"]["column_header"], {"B1": "FY2026", "B5": "FY2025"})

    def test_the_period_check_uses_the_nearest_year_above(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "plan.xlsx"
            path.write_bytes(self.QUARTERS)
            sid = ingest(td, path)["source_id"]

            def status(locator, value, period):
                fact = {
                    "id": "f",
                    "label": "Revenue",
                    "entity": "Harbour",
                    "period": period,
                    "unit": "USD",
                    "value": value,
                    "citations": [{"source_id": sid, "locator": locator, "excerpt": value}],
                    "review": {"verdict": "supports", "reviewer": "codex", "rationale": "Revenue row, quarter column."},
                }
                claim = {"id": "c", "kind": "fact", "text": "Revenue was {{fact:f}}.", "review": fact["review"]}
                r = verify(td, {"schema_version": 1, "facts": [fact], "claims": [claim]})
                return r["status"], [i["message"] for i in r["issues"]]

            self.assertNotEqual(status("cell:Plan!C3", "120", "Q2 FY2026")[0], "blocked")
            blocked = status("cell:Plan!C3", "120", "FY2025")
            self.assertEqual(blocked[0], "blocked")
            self.assertTrue(any("workbook column header" in m for m in blocked[1]))
            self.assertNotEqual(status("cell:Plan!B6", "90", "FY2025")[0], "blocked")
            self.assertEqual(status("cell:Plan!B6", "90", "FY2026")[0], "blocked")


TWO_SHEETS = book(
    {
        "First": grid((1, t("A1", "Metric"), t("B1", "FY2025")), (2, t("A2", "Revenue"), n("B2", "1250"))),
        "Second": grid(
            (1, t("A1", "Metric"), t("B1", "FY2024")),
            (5, t("A5", "Revenue"), n("B5", "1000")),
            (6, t("A6", "Profit"), n("B6", "200"), n("C6", "7")),
        ),
    }
)


class LimitTests(unittest.TestCase):
    def test_a_workbook_over_the_limit_is_refused_before_reading_the_rest(self):
        broken = book({"First": grid(*[(r, n(f"A{r}", r)) for r in range(1, 8)]), "Second": "<not-xml"})
        with patch("citations_calculations.extract.MAX_UNITS", 5):
            with self.assertRaises(EvidenceError) as caught:
                extract(broken, "xlsx")
        message = str(caught.exception)
        self.assertIn("more than 5 non-empty cells", message)
        self.assertIn("--sheet", message)
        self.assertIn("First, Second", message)

    def test_one_sheet_and_a_range_can_be_read(self):
        units = cells(TWO_SHEETS, sheets=["Second"], cells="A5:B6")
        self.assertEqual(sorted(units), ["cell:Second!A5", "cell:Second!A6", "cell:Second!B5", "cell:Second!B6"])
        self.assertEqual(units["cell:Second!B5"]["context"]["column_header"], {"B1": "FY2024"})

    def test_a_scoped_ingest_is_partial_and_records_its_scope(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "two.xlsx"
            path.write_bytes(TWO_SHEETS)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(["ingest", "--workspace", td, "--file", str(path), "--sheet", "Second", "--range", "a5:b6"])
            self.assertEqual(code, 0)
            source = json.loads(output.getvalue())["source"]
            self.assertEqual(source["completeness"], "partial")
            self.assertEqual(source["scope"], {"sheets": ["Second"], "range": "A5:B6"})
            self.assertNotIn("scope", ingest(td, path)["source"])

    def test_unknown_sheets_bad_ranges_and_scopes_on_other_formats_are_refused(self):
        with self.assertRaisesRegex(EvidenceError, "no sheet named 'Third'; its sheets are First, Second"):
            extract(TWO_SHEETS, "xlsx", sheets=["Third"])
        with self.assertRaisesRegex(EvidenceError, "Range"):
            extract(TWO_SHEETS, "xlsx", cells="B6:A")
        with self.assertRaisesRegex(EvidenceError, "Excel"):
            extract(b"a,b\n1,2\n", "csv", sheets=["First"])

    def test_workbooks_may_be_larger_than_other_sources_because_they_are_streamed(self):
        with patch("citations_calculations.extract.MAX_BYTES", 200):
            self.assertGreater(len(TWO_SHEETS), 200)
            self.assertEqual(len(extract(TWO_SHEETS, "xlsx")["units"]), 11)
            with self.assertRaisesRegex(EvidenceError, "limit"):
                extract(b"Revenue was 1250.\n" * 20, "txt")
        with patch("citations_calculations.extract.MAX_WORKBOOK_BYTES", 200):
            with self.assertRaisesRegex(EvidenceError, "limit"):
                extract(TWO_SHEETS, "xlsx")

    def test_formula_warnings_are_one_line_per_sheet(self):
        formulas = book(
            {"Calc": grid((1, *[f'<c r="{c}1"><f>A2*2</f><v>2</v></c>' for c in "ABC"], '<c r="D1"><f>A2*3</f></c>'))}
        )
        warnings = extract(formulas, "xlsx")["warnings"]
        self.assertEqual(len(warnings), 2, warnings)
        self.assertTrue(any(w.startswith("Calc: 3 formula cells") for w in warnings), warnings)
        self.assertTrue(any("no cached value" in w for w in warnings), warnings)


if __name__ == "__main__":
    unittest.main()
