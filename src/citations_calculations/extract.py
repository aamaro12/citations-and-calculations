"""Deterministic locators over captured content, with table/cell context preserved."""

import csv
from decimal import Decimal
from html.parser import HTMLParser
import io
import json
from pathlib import Path
import re
from urllib.parse import urlsplit, parse_qsl
import zipfile
from xml.etree import ElementTree as ET

from .store import EvidenceError, normal

MAX_BYTES = 20 * 1024 * 1024
MAX_UNITS = 50_000
SECRET = re.compile(r"(?i)^(?:api[_-]?key|access[_-]?token|authorization|password|client[_-]?secret|secret[_-]?key)$")


def public_uri(uri):
    if not isinstance(uri, str) or any(c in uri for c in ("\r", "\n", "\t")):
        raise EvidenceError("Source URL contains invalid characters")
    p = urlsplit(uri)
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
        raise EvidenceError("Source URL must be ordinary HTTP(S), without credentials")
    if any(SECRET.fullmatch(k) or k.casefold() in ("token", "key", "signature") for k, _ in parse_qsl(p.query)):
        raise EvidenceError("Source URL contains a credential parameter")
    return uri


def check_payload(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if SECRET.fullmatch(key):
                raise EvidenceError("Capture contains a credential field; export only the evidence payload")
            check_payload(child)
    elif isinstance(value, list):
        for child in value:
            check_payload(child)


def text_units(text, prefix="line"):
    return [{"locator": f"{prefix}:{i}", "text": line} for i, line in enumerate(text.splitlines(), 1) if line.strip()]


def json_units(value, pointer="", fields=None):
    if pointer.count("/") > 50:
        raise EvidenceError("Structured source is too deeply nested")
    if isinstance(value, dict):
        return [
            u
            for k, v in value.items()
            for u in json_units(
                v,
                pointer + "/" + str(k).replace("~", "~0").replace("/", "~1"),
                {
                    key: str(child) if isinstance(child, Decimal) else child
                    for key, child in value.items()
                    if isinstance(child, (str, int, float, bool, Decimal)) or child is None
                },
            )
        ]
    if isinstance(value, list):
        return [u for i, v in enumerate(value) for u in json_units(v, pointer + "/" + str(i))]
    if isinstance(value, str):
        text = value
    elif isinstance(value, Decimal):
        text = format(value, "f") if abs(value.adjusted()) <= 100 else str(value)
    else:
        text = json.dumps(value, ensure_ascii=False, allow_nan=False)
    return [{"locator": "json:" + pointer, "text": text, "context": {"pointer": pointer, "fields": fields or {}}}]


VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
BLOCK_START = ("p", "div", "li", "tr", "h1", "h2", "h3", "h4", "section", "br")
BLOCK_END = ("p", "div", "li", "tr", "h1", "h2", "h3", "h4", "section")
# Start tags that close an open paragraph, as browsers do when </p> is omitted.
CLOSES_P = {
    "address", "article", "aside", "blockquote", "dd", "details", "div", "dl", "dt", "fieldset", "figcaption",
    "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li", "main", "menu", "nav",
    "ol", "p", "pre", "section", "table", "ul",
}  # fmt: skip
P_BOUNDS = {"button", "caption", "table", "td", "template", "th"}
# An open item closes when its next sibling starts, within the container that bounds it.
SIBLINGS = {
    "li": ({"li"}, {"ol", "ul"}),
    "dt": ({"dd", "dt"}, {"dl"}),
    "dd": ({"dd", "dt"}, {"dl"}),
    "tr": ({"tr"}, {"table", "tbody", "tfoot", "thead"}),
    "td": ({"td", "th"}, {"table", "tr"}),
    "th": ({"td", "th"}, {"table", "tr"}),
    "option": ({"option"}, {"datalist", "optgroup", "select"}),
}
HIDDEN_STYLE = re.compile(r"(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*hidden)\s*(?:!important\s*)?(?:;|$)", re.I)
HTML_WARNING = (
    "Page text as a reader sees it: scripts, styles, templates and elements hidden by attribute or inline style "
    "are left out; text hidden by a stylesheet is not detected"
)


def hides(tag, attrs):
    a = {k.lower(): (v or "") for k, v in attrs}
    return (
        tag in ("noscript", "script", "style", "template")
        or "hidden" in a
        or bool(HIDDEN_STYLE.search(a.get("style", "")))
    )


class HTML(HTMLParser):
    """Visible page text. Each open element records whether it hides its content, so hidden text is never citable."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.parts = []
        self.units = []

    def hidden(self):
        return any(hidden for _, hidden in self.stack)

    def flush(self):
        text = normal(" ".join(self.parts))
        self.parts = []
        if text:
            self.units.append({"locator": f"block:{len(self.units) + 1}", "text": text})

    def close_open(self, names, bounds):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] in names:
                del self.stack[i:]
                return
            if self.stack[i][0] in bounds:
                return

    def handle_starttag(self, tag, attrs):
        if tag in CLOSES_P:
            self.close_open({"p"}, P_BOUNDS)
        if tag in SIBLINGS:
            self.close_open(*SIBLINGS[tag])
        if not self.hidden() and tag in BLOCK_START:
            self.flush()
        if tag not in VOID:
            self.stack.append((tag, hides(tag, attrs)))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if not self.hidden() and tag in BLOCK_END:
            self.flush()
        self.close_open({tag}, ())

    def handle_data(self, text):
        if not self.hidden():
            self.parts.append(text)


META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_.:-]+)""", re.I)


def decode(data, first, declared=None):
    """Valid UTF-8 first, then a declared charset, then windows-1252 with a warning."""
    try:
        return data.decode(first), []
    except UnicodeDecodeError:
        pass
    if declared:
        try:
            return data.decode(declared), []
        except (UnicodeDecodeError, LookupError):
            pass
    for fallback, name in (("cp1252", "windows-1252"), ("latin-1", "latin-1")):
        try:
            return data.decode(fallback), [
                f"Not UTF-8 and no usable declared charset: decoded as {name}; check accented characters and symbols"
            ]
        except UnicodeDecodeError:
            continue


def archive(data):
    z = zipfile.ZipFile(io.BytesIO(data))
    if sum(i.file_size for i in z.infolist()) > MAX_BYTES * 5:
        raise EvidenceError("Expanded office archive is too large")
    return z


def docx_units(data):
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    with archive(data) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    units = []
    for i, p in enumerate(root.findall(".//w:p", ns), 1):
        text = "".join(n.text or "" for n in p.findall(".//w:t", ns))
        if text:
            units.append({"locator": f"paragraph:{i}", "text": text})
    return units, ["DOCX body only: headers, footers, comments and tracked-change interpretation are not included"]


def xlsx_units(data):
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rel = "http://schemas.openxmlformats.org/package/2006/relationships"
    office = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
    units = []
    warnings = []
    with archive(data) as z:
        strings = []
        if "xl/sharedStrings.xml" in z.namelist():
            strings = [
                "".join(t.text or "" for t in si.findall(".//m:t", ns))
                for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", ns)
            ]
        rels = {
            r.attrib["Id"]: r.attrib["Target"]
            for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels")).findall(f"{{{rel}}}Relationship")
        }
        for sheet in ET.fromstring(z.read("xl/workbook.xml")).findall("m:sheets/m:sheet", ns):
            name = sheet.attrib["name"]
            target = rels[sheet.attrib[office]]
            target = target.lstrip("/") if target.startswith("/") else "xl/" + target
            if ".." in Path(target).parts:
                raise EvidenceError("Worksheet path escapes archive")
            cells = []
            for c in ET.fromstring(z.read(target)).findall(".//m:sheetData/m:row/m:c", ns):
                at = c.attrib["r"]
                v = c.find("m:v", ns)
                formula = c.find("m:f", ns)
                kind = c.get("t")
                if kind == "inlineStr":
                    text = "".join(t.text or "" for t in c.findall(".//m:t", ns))
                elif v is None:
                    text = ""
                elif kind == "s":
                    text = strings[int(v.text)]
                else:
                    text = v.text or ""
                if formula is not None:
                    warnings.append(
                        f"{name}!{at}: formula {'has a cached value; it was not recalculated' if text else 'has no cached value'}"
                    )
                if text:
                    cells.append(
                        {
                            "locator": f"cell:{name}!{at}",
                            "text": text,
                            "context": {
                                "sheet": name,
                                "cell": at,
                                "formula": formula.text if formula is not None else None,
                                "cached_formula": formula is not None,
                            },
                        }
                    )
            # Keep nearby labels readable without treating them as a verified interpretation of each number.
            rows, headers = {}, {}
            for cell in cells:
                at = cell["context"]["cell"]
                row = re.sub(r"\D", "", at)
                col = re.sub(r"\d", "", at)
                rows.setdefault(row, {})[at] = cell["text"]
                if row == "1":
                    headers.setdefault(col, {})[at] = cell["text"]
            for cell in cells:
                row = re.sub(r"\D", "", cell["context"]["cell"])
                col = re.sub(r"\d", "", cell["context"]["cell"])
                cell["context"]["row_cells"] = rows[row]
                cell["context"]["column_header"] = headers.get(col, {})
            units += cells
    return units, warnings


def extract(data, format, charset=None):
    if len(data) > MAX_BYTES:
        raise EvidenceError("Source exceeds the 20 MiB limit")
    warnings = []
    if format in ("txt", "md"):
        text, warnings = decode(data, "utf-8-sig", charset)
        units = text_units(text)
    elif format == "json":
        value = json.loads(data, parse_float=Decimal)
        check_payload(value)
        units = json_units(value)
    elif format == "csv":
        text, warnings = decode(data, "utf-8-sig", charset)
        rows = list(csv.reader(io.StringIO(text)))
        units = []
        for r, row in enumerate(rows, 1):
            units.append(
                {
                    "locator": f"row:{r}",
                    "text": json.dumps(row, ensure_ascii=False),
                    "context": {"row": r, "headers": rows[0] if rows else []},
                }
            )
            for c, value in enumerate(row, 1):
                units.append(
                    {
                        "locator": f"cell:R{r}C{c}",
                        "text": value,
                        "context": {
                            "row": r,
                            "column": c,
                            "header": rows[0][c - 1] if rows and c <= len(rows[0]) else None,
                            "row_values": row,
                            "fields": dict(zip(rows[0], row)),
                        },
                    }
                )
    elif format in ("html", "htm"):
        sniffed = META_CHARSET.search(data[:4096])
        text, warnings = decode(data, "utf-8", charset or (sniffed[1].decode("ascii") if sniffed else None))
        parser = HTML()
        parser.feed(text)
        parser.flush()
        units = parser.units
        warnings.append(HTML_WARNING)
    elif format in ("docx", "xlsx"):
        try:
            units, warnings = (docx_units if format == "docx" else xlsx_units)(data)
        except EvidenceError:
            raise
        except (zipfile.BadZipFile, ET.ParseError, KeyError, IndexError, ValueError) as e:
            raise EvidenceError(f"Unreadable {format.upper()} file: {e}") from e
    elif format == "pdf":
        try:
            from pypdf import PdfReader
            from pypdf.errors import DependencyError, PyPdfError
        except ImportError as e:
            raise EvidenceError(
                "PDF reading needs the optional dependency: pip install 'citations-and-calculations[pdf]'"
            ) from e
        try:
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted and not reader.decrypt(""):
                raise EvidenceError("The PDF is password-protected; supply a copy that opens without a password")
            texts = [page.extract_text() or "" for page in reader.pages]
        except EvidenceError:
            raise
        except DependencyError as e:
            raise EvidenceError("This encrypted PDF needs the pdf extra, which includes cryptography") from e
        except (PyPdfError, KeyError, TypeError, ValueError) as e:
            raise EvidenceError(f"Unreadable PDF file: {e}") from e
        units = []
        for page_no, text in enumerate(texts, 1):
            if not text.strip():
                warnings.append(f"Page {page_no} has no extractable text; OCR needed")
            for unit in text_units(text):
                unit["locator"] = f"page:{page_no}/" + unit["locator"]
                unit["context"] = {"page": page_no}
                units.append(unit)
        warnings.append("PDF text extraction has no bounding boxes; visually check table order, units and columns")
    else:
        raise EvidenceError("Supported formats: txt, md, json, csv, html, pdf, docx, xlsx")
    if len(units) > MAX_UNITS:
        raise EvidenceError("Source has too many evidence units; acquire a smaller scoped export")
    return {"format": format, "units": units, "warnings": warnings}
