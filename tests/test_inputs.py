"""Awkward inputs are read faithfully or refused with a clear error, never a crash."""

import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import zipfile

from citations_calculations import __version__
from citations_calculations.cli import main
from citations_calculations.extract import extract
from citations_calculations.store import EvidenceError
from citations_calculations.web import fetch


def zipped(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, body in files.items():
            z.writestr(name, body)
    return buf.getvalue()


def text_pdf():
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
    xref = len(body)
    body += b"xref\n0 6\n0000000000 65535 f \n" + b"".join(b"%010d 00000 n \n" % o for o in offsets)
    return body + b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n" + str(xref).encode() + b"\n%%EOF\n"


def encrypted(user_password):
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(text_pdf())))
    writer.encrypt(user_password=user_password, owner_password="owner", algorithm="AES-256")
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


class OfficeFileTests(unittest.TestCase):
    def test_corrupt_or_incomplete_office_files_are_refused_cleanly(self):
        sheet = '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="s"><v>7</v></c></row></sheetData></worksheet>'
        workbook = zipped(
            {
                "xl/workbook.xml": '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="S" sheetId="1" r:id="rId1"/></sheets></workbook>',
                "xl/_rels/workbook.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>',
                "xl/worksheets/sheet1.xml": sheet,  # a shared-string index with no shared strings
            }
        )
        cases = {
            "docx: not a zip": (b"not a zip", "docx"),
            "docx: no document part": (zipped({"word/other.xml": "<x/>"}), "docx"),
            "docx: malformed XML": (zipped({"word/document.xml": "<w:document"}), "docx"),
            "xlsx: missing string": (workbook, "xlsx"),
        }
        for name, (data, kind) in cases.items():
            with self.subTest(name), self.assertRaisesRegex(EvidenceError, "Unreadable " + kind.upper()):
                extract(data, kind)

    def test_the_command_line_reports_a_corrupt_file_as_an_error(self):
        with tempfile.TemporaryDirectory() as td:
            broken = Path(td) / "broken.docx"
            broken.write_bytes(b"not a zip")
            with patch("sys.stderr", new_callable=io.StringIO) as err:
                code = main(["ingest", "--workspace", str(Path(td) / "run"), "--file", str(broken)])
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(err.getvalue())["status"], "error")


class PdfTests(unittest.TestCase):
    def setUp(self):
        try:
            import cryptography  # noqa: F401
            import pypdf  # noqa: F401
        except ImportError:
            self.skipTest("The pdf extra, with its crypto support, is not installed")

    def test_a_pdf_encrypted_without_a_user_password_is_read(self):
        units = extract(encrypted(""), "pdf")["units"]
        self.assertIn("Revenue 1250", [u["text"] for u in units])

    def test_a_password_protected_pdf_is_refused_cleanly(self):
        with self.assertRaisesRegex(EvidenceError, "password"):
            extract(encrypted("secret"), "pdf")


class EncodingTests(unittest.TestCase):
    def test_text_that_is_not_utf8_is_decoded_with_a_warning(self):
        for kind, data in {
            "csv": "Company,Revenue\nCafé SA,1250\n".encode("cp1252"),
            "txt": "Café SA reported 1250\n".encode("cp1252"),
            "html": "<p>Café SA reported 1250</p>".encode("cp1252"),
        }.items():
            with self.subTest(kind):
                result = extract(data, kind)
                self.assertTrue(any("Café SA" in u["text"] for u in result["units"]))
                self.assertTrue(any("windows-1252" in w for w in result["warnings"]))

    def test_a_declared_charset_is_used_without_a_fallback_warning(self):
        page = '<meta charset="iso-8859-1"><p>Café SA reported 1250</p>'.encode("latin-1")
        result = extract(page, "html")
        self.assertTrue(any("Café SA" in u["text"] for u in result["units"]))
        self.assertFalse(any("windows-1252" in w for w in result["warnings"]))
        header = extract("<p>Café SA reported 1250</p>".encode("latin-1"), "html", charset="ISO-8859-1")
        self.assertTrue(any("Café SA" in u["text"] for u in header["units"]))

    def test_utf8_text_is_unchanged(self):
        result = extract("Café SA reported 1250\n".encode(), "txt")
        self.assertEqual(result["units"][0]["text"], "Café SA reported 1250")
        self.assertEqual(result["warnings"], [])


class HiddenTextTests(unittest.TestCase):
    def texts(self, page):
        return [u["text"] for u in extract(page.encode(), "html")["units"]]

    def test_text_the_page_hides_is_not_citable(self):
        page = (
            "<p>Revenue was 100 million</p>"
            '<p style="color: red; display:none">Revenue was 999 million</p>'
            "<p hidden>Revenue was 777 million</p>"
            '<div style="visibility: hidden">Revenue was 444 million</div>'
            "<template><p>Revenue was 333 million</p></template>"
            "<script>var revenue = 222;</script>"
            "<p>Margin was 20 percent</p>"
        )
        self.assertEqual(self.texts(page), ["Revenue was 100 million", "Margin was 20 percent"])

    def test_text_hidden_only_from_screen_readers_stays_citable(self):
        # aria-hidden removes text from the accessibility tree, not from the screen: sighted readers still see it.
        page = (
            '<p>Revenue was <span aria-hidden="true">$1.2bn</span></p><div aria-hidden="true"><p>Margin 20%</p></div>'
        )
        self.assertEqual(self.texts(page), ["Revenue was $1.2bn", "Margin 20%"])

    def test_an_unclosed_hidden_element_does_not_hide_what_follows(self):
        page = (
            "<p hidden>Revenue was 777 million"
            "<p>Revenue was 100 million"
            "<ul><li hidden>Hidden item<li>Visible item</ul>"
            '<img hidden src="x.png"><input type="hidden" value="9">'
            "<div>Margin was 20 percent</div>"
        )
        self.assertEqual(self.texts(page), ["Revenue was 100 million", "Visible item", "Margin was 20 percent"])


class FetchTests(unittest.TestCase):
    def test_the_user_agent_names_the_package_version(self):
        response = MagicMock(status=200)
        response.getheader.side_effect = lambda name: {"Content-Type": "text/plain; charset=utf-8"}.get(name)
        response.read.return_value = b"Revenue was 1250"
        connection = MagicMock()
        connection.getresponse.return_value = response
        with (
            tempfile.TemporaryDirectory() as td,
            patch("citations_calculations.web.addresses", return_value=["93.184.216.34"]),
            patch("citations_calculations.web.PinnedConnection", return_value=connection),
        ):
            fetch(td, "https://example.com/release")
        headers = connection.request.call_args.kwargs["headers"]
        self.assertEqual(headers["User-Agent"].split(" ")[0], "citations-and-calculations/" + __version__)


if __name__ == "__main__":
    unittest.main()
