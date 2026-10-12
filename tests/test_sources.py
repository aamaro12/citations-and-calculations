"""Source acquisition preserves exact locators and rejects misleading or unsafe inputs."""

import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock
import zipfile

from citations_calculations.acquire import ingest, capture, timestamp
from citations_calculations.extract import extract
from citations_calculations.store import Store, EvidenceError, save_json
from citations_calculations.web import fetch, addresses, PinnedConnection


def zipped(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, body in files.items():
            z.writestr(name, body)
    return buf.getvalue()


def workbook():
    return zipped(
        {
            "xl/workbook.xml": '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Revenue" sheetId="1" r:id="rId1"/></sheets></workbook>',
            "xl/_rels/workbook.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>',
            "xl/worksheets/sheet1.xml": '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Metric</t></is></c><c r="B1" t="inlineStr"><is><t>FY2025</t></is></c></row><row r="2"><c r="A2" t="inlineStr"><is><t>Revenue</t></is></c><c r="B2"><v>1250</v></c><c r="C2"><f>B2*2</f><v>2500</v></c><c r="D2"><f>B2*3</f></c></row></sheetData></worksheet>',
        }
    )


class SourceTests(unittest.TestCase):
    def test_json_numeric_scalars_do_not_lose_precision_to_binary_float(self):
        raw = b'{"amount":0.12345678901234567890123456789}'
        self.assertEqual(extract(raw, "json")["units"][0]["text"], "0.12345678901234567890123456789")
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "precise.json"
            p.write_bytes(b'{"tool_name":"example","tool_result":' + raw + b"}")
            self.assertEqual(capture(td, p)["units"][0]["text"], "0.12345678901234567890123456789")

    def test_text_pdf_has_real_page_and_line_locators(self):
        try:
            import pypdf
        except ImportError:
            self.skipTest("Optional PDF dependency not installed")
        self.assertTrue(pypdf.PdfReader)
        stream = b"BT /F1 12 Tf 10 150 Td (Revenue 1250) Tj ET"
        objects = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        ]
        body = b"%PDF-1.4\n"
        offsets = []
        for i, obj in enumerate(objects, 1):
            offsets.append(len(body))
            body += str(i).encode() + b" 0 obj\n" + obj + b"\nendobj\n"
        at = len(body)
        body += b"xref\n0 6\n0000000000 65535 f \n"
        body += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
        body += b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n" + str(at).encode() + b"\n%%EOF"
        units = extract(body, "pdf")["units"]
        self.assertEqual(units[0]["locator"], "page:1/line:1")
        self.assertEqual(units[0]["text"], "Revenue 1250")

    def test_text_json_csv_html_and_word_locators(self):
        self.assertEqual(extract(b"Line one\nLine two", "txt")["units"][1]["locator"], "line:2")
        self.assertEqual(extract(b'{"a/b":{"~key":3}}', "json")["units"][0]["locator"], "json:/a~1b/~0key")
        unit = next(
            u
            for u in extract(b"Metric,Period,Value\nRevenue,FY2025,1250", "csv")["units"]
            if u["locator"] == "cell:R2C3"
        )
        self.assertEqual(unit["context"]["fields"]["Period"], "FY2025")
        html = extract(b"<div>Text outside paragraphs</div><script>Danger 900</script><p>Visible paragraph</p>", "html")
        self.assertEqual([u["text"] for u in html["units"]], ["Text outside paragraphs", "Visible paragraph"])
        word = zipped(
            {
                "word/document.xml": '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Revenue is </w:t></w:r><w:r><w:t>1250.</w:t></w:r></w:p></w:body></w:document>'
            }
        )
        self.assertEqual(extract(word, "docx")["units"][0], {"locator": "paragraph:1", "text": "Revenue is 1250."})

    def test_workbook_cell_context_and_cache_flags(self):
        data = extract(workbook(), "xlsx")
        units = {u["locator"]: u for u in data["units"]}
        self.assertEqual(units["cell:Revenue!B2"]["text"], "1250")
        self.assertEqual(units["cell:Revenue!B2"]["context"]["column_header"], {"B1": "FY2025"})
        self.assertTrue(units["cell:Revenue!C2"]["context"]["cached_formula"])
        self.assertNotIn("cell:Revenue!D2", units)
        self.assertTrue(any("no cached value" in w for w in data["warnings"]))

    def test_blank_pdf_requires_ocr_instead_of_silently_succeeding(self):
        try:
            from pypdf import PdfWriter
        except ImportError:
            self.skipTest("Optional PDF dependency not installed")
        stream = io.BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        writer.write(stream)
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "blank.pdf"
            p.write_bytes(stream.getvalue())
            with self.assertRaisesRegex(EvidenceError, "OCR"):
                ingest(td, p)

    def test_source_search_is_bounded_and_marks_truncation(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "many.txt"
            p.write_text("\n".join("row " + str(i) for i in range(40)))
            sid = ingest(td, p)["source_id"]
            r = Store(td).search(sid, limit=3)
            self.assertTrue(r["truncated"])
            self.assertEqual(r["matched"], 40)
            self.assertEqual(len(r["units"]), 3)
            with self.assertRaises(EvidenceError):
                Store(td).search(sid, limit=1000)

    def test_tool_query_arguments_are_not_citable_result_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "tool.json"
            save_json(
                p,
                {
                    "tool_name": "search",
                    "tool_args": {"invented_number": 900},
                    "tool_result": {"value": 500},
                    "completeness": "complete",
                },
            )
            result = capture(td, p)
            self.assertEqual([u["text"] for u in result["units"]], ["500"])
            self.assertEqual(result["units"][0]["locator"], "json:/tool_result/value")

    def test_nested_pagination_flags_override_a_complete_declaration(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "nested.json"
            save_json(
                p,
                {
                    "tool_name": "search",
                    "completeness": "complete",
                    "tool_result": {"results": [{"value": 500}], "pagination": {"hasMore": True}},
                },
            )
            self.assertEqual(capture(td, p)["source"]["completeness"], "partial")

    def test_error_missing_tool_payload_and_credentials_are_rejected(self):
        examples = [
            {"tool_name": "search", "tool_result": {"value": 1}, "is_error": True},
            {"tool_name": "search", "tool_result": {"isError": True}},
            {"tool_name": "search"},
            {"tool_name": "search", "tool_result": {"access_token": "not-to-be-stored"}},
        ]
        with tempfile.TemporaryDirectory() as td:
            for envelope in examples:
                p = Path(td) / "tool.json"
                save_json(p, envelope)
                with self.subTest(envelope=envelope), self.assertRaises(EvidenceError):
                    capture(td, p)
            self.assertFalse((Path(td) / "sources").exists())

    def test_environment_file_and_credential_url_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / ".env"
            p.write_text("TEST=value")
            with self.assertRaises(EvidenceError):
                ingest(td, p)
            p = Path(td) / "page.txt"
            p.write_text("Public evidence")
            with self.assertRaises(EvidenceError):
                ingest(td, p, uri="https://example.org?api_key=secret")

    def test_timezone_required(self):
        for date in ("2026-01-01T00:00:00", "bad"):
            with self.assertRaises(EvidenceError):
                timestamp(date)

    def test_public_fetch_checks_every_redirect_and_preserves_body_and_query(self):
        first = MagicMock()
        first.status = 302
        first.getheader.side_effect = lambda key: "https://public.test/data?q=one" if key == "Location" else None
        second = MagicMock()
        second.status = 200
        second.getheader.side_effect = lambda key: "text/plain" if key == "Content-Type" else None
        second.read.return_value = b"Revenue was 1250."
        connections = [MagicMock(), MagicMock()]
        connections[0].getresponse.return_value = first
        connections[1].getresponse.return_value = second
        with (
            tempfile.TemporaryDirectory() as td,
            patch("citations_calculations.web.addresses", return_value=["93.184.216.34"]) as dns,
            patch("citations_calculations.web.PinnedConnection", side_effect=connections),
        ):
            result = fetch(td, "https://public.test/start")
            self.assertEqual(dns.call_count, 2)
            self.assertEqual(result["source"]["uri"], "https://public.test/data?q=one")
            self.assertEqual(connections[1].request.call_args.args[1], "/data?q=one")
            self.assertEqual(result["units"][0]["text"], "Revenue was 1250.")

    def test_local_address_credentials_custom_port_and_downgrade_fail(self):
        with patch("socket.getaddrinfo", return_value=[(None, None, None, None, ("127.0.0.1", 443))]):
            with self.assertRaises(EvidenceError):
                addresses("local.test", 443)
        with tempfile.TemporaryDirectory() as td:
            for url in (
                "https://user:password@public.test",
                "https://public.test?token=secret",
                "https://public.test:8443/",
            ):
                with self.subTest(url=url), self.assertRaises(EvidenceError):
                    fetch(td, url)
            r = MagicMock()
            r.status = 302
            r.getheader.return_value = "http://public.test/"
            c = MagicMock()
            c.getresponse.return_value = r
            with (
                patch("citations_calculations.web.addresses", return_value=["93.184.216.34"]),
                patch("citations_calculations.web.PinnedConnection", return_value=c),
            ):
                with self.assertRaisesRegex(EvidenceError, "downgrade"):
                    fetch(td, "https://public.test/")

    def test_pinned_connection_uses_approved_ip_and_original_tls_hostname(self):
        sock = MagicMock()
        context = MagicMock()
        with (
            patch("socket.create_connection", return_value=sock) as connect,
            patch("ssl.create_default_context", return_value=context),
        ):
            c = PinnedConnection("public.test", 443, "93.184.216.34", True)
            c.connect()
            self.assertEqual(connect.call_args.args[0], ("93.184.216.34", 443))
            context.wrap_socket.assert_called_once_with(sock, server_hostname="public.test")

    def test_http_failure_oversize_and_redirect_loops_do_not_store_evidence(self):
        for mode in ("failure", "oversize", "redirect"):
            with (
                tempfile.TemporaryDirectory() as td,
                patch("citations_calculations.web.addresses", return_value=["93.184.216.34"]),
            ):
                r = MagicMock()
                r.status = 404 if mode == "failure" else 302 if mode == "redirect" else 200
                r.getheader.side_effect = lambda key: (
                    "https://public.test/again"
                    if key == "Location"
                    else "999999999"
                    if key == "Content-Length"
                    else "text/plain"
                )
                c = MagicMock()
                c.getresponse.return_value = r
                with (
                    patch("citations_calculations.web.PinnedConnection", return_value=c),
                    self.assertRaises(EvidenceError),
                ):
                    fetch(td, "https://public.test/")
                self.assertFalse((Path(td) / "sources").exists())


if __name__ == "__main__":
    unittest.main()
