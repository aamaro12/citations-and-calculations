"""Deterministic locators over captured content, with table/cell context preserved."""

from bisect import bisect_left
import csv
from decimal import Decimal
from html.parser import HTMLParser
import io
import json
import math
from pathlib import Path
import re
import unicodedata
from urllib.parse import urlsplit, parse_qsl
import zipfile
from xml.etree import ElementTree as ET

from .formats import BUILTIN, display, is_date_format
from .store import EvidenceError, normal

MAX_BYTES = 20 * 1024 * 1024
# Workbooks are read a cell at a time, so they can be larger than other sources.
MAX_WORKBOOK_BYTES = 50 * 1024 * 1024
MAX_WORKBOOK_EXPANDED = 512 * 1024 * 1024
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


def scrambled(text):
    """Text that is not words, as when a PDF's fonts map letters to control codes or glyph names."""
    chars = [c for c in text if not c.isspace()]
    odd = sum(1 for c in chars if c == "\ufffd" or unicodedata.category(c) in ("Cc", "Co", "Cn"))
    words = [w for w in re.findall(r"[^\W\d_]{2,}", text) if w.isascii()]
    vowelless = sum(1 for w in words if not re.search(r"[aeiouy]", w, re.I))
    return odd > 0.1 * len(chars) or (len(words) >= 20 and vowelless > 0.4 * len(words))


def page_list(numbers):
    runs = []
    for n in numbers:
        if runs and n == runs[-1][1] + 1:
            runs[-1][1] = n
        else:
            runs.append([n, n])
    listed = ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in runs)
    return ("Pages " + listed + " have") if len(numbers) > 1 else ("Page " + listed + " has")


def archive(data, expanded=None):
    z = zipfile.ZipFile(io.BytesIO(data))
    if sum(i.file_size for i in z.infolist()) > (expanded or MAX_BYTES * 5):
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


SHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
CELL_REF = re.compile(r"\$?([A-Za-z]{1,3})\$?([1-9][0-9]{0,6})")
MAX_MERGE = 1000  # larger merged areas are layout, not headers


def position(ref):
    match = CELL_REF.fullmatch(ref)
    if not match:
        raise EvidenceError("Range must name cells such as A1:F80")
    column = 0
    for letter in match[1].upper():
        column = column * 26 + ord(letter) - 64
    return int(match[2]), column


def cell_range(text):
    """Rows and columns of a range such as A1:F80, as (top, left, bottom, right)."""
    parts = str(text).strip().split(":")
    if len(parts) not in (1, 2):
        raise EvidenceError("Range must name cells such as A1:F80")
    (r1, c1), (r2, c2) = position(parts[0]), position(parts[-1])
    return min(r1, r2), min(c1, c2), max(r1, r2), max(c1, c2)


def number_formats(z):
    """The number format of each cell style, by its index in styles.xml."""
    if "xl/styles.xml" not in z.namelist():
        return []
    ns = {"m": SHEET_NS}
    root = ET.fromstring(z.read("xl/styles.xml"))
    custom = {int(f.get("numFmtId")): f.get("formatCode") or "General" for f in root.findall("m:numFmts/m:numFmt", ns)}
    ids = [int(xf.get("numFmtId", "0")) for xf in root.findall("m:cellXfs/m:xf", ns)]
    return [custom.get(i, BUILTIN.get(i, "General")) for i in ids]


def shown(raw, kind, code, date1904):
    """The text a cell shows, and what to keep beside it: the stored value, or what a misleading format shows."""
    if kind == "b" and raw in ("0", "1"):
        return ("TRUE" if raw == "1" else "FALSE"), {"stored": raw}
    if kind not in (None, "n") or not raw:
        return raw, {}
    try:
        value = float(raw)
        text = display(value, code, date1904) if math.isfinite(value) else raw
    except (ValueError, ArithmeticError, IndexError, KeyError):
        return raw, {}
    extra = {"number_format": code} if code != "General" else {}
    if text == raw:
        return raw, extra
    # A format that hides the sign, or shows no digits at all, would let a citation drop them.
    if not is_date_format(code) and (not re.search(r"\d", text) or (value < 0 and not re.search(r"[-−(]", text))):
        return raw, {**extra, "shown": text}
    return text, {**extra, "stored": raw}


def sheet_items(z, target):
    """Stream a worksheet's cells and merged ranges, so a large sheet is never held in memory at once."""
    cell, row, merge = "{%s}c" % SHEET_NS, "{%s}row" % SHEET_NS, "{%s}mergeCell" % SHEET_NS
    with z.open(target) as stream:
        for _, elem in ET.iterparse(stream):
            if elem.tag == cell:
                yield "cell", elem
                elem.clear()
            elif elem.tag == row:
                elem.clear()
            elif elem.tag == merge:
                yield "merge", elem.get("ref") or ""


def add_headers(cells, candidates, merges):
    """Each cell's column header: the first row's text, and for a number the nearest labels above it.

    Merged cells count for every column they cover, so a year merged across 4 quarters heads all 4.
    """
    columns = {}
    at = {}
    for entry in candidates + [(c["_row"], c["_col"], c["context"]["cell"], c["text"], c["_number"]) for c in cells]:
        columns.setdefault(entry[1], []).append(entry)
        at[(entry[0], entry[1])] = entry
    for ref in merges:
        try:
            top, left, bottom, right = cell_range(ref)
        except EvidenceError:
            continue
        corner = at.get((top, left))
        if not corner or (bottom - top + 1) * (right - left + 1) > MAX_MERGE:
            continue
        for r in range(top, bottom + 1):
            for c in range(left, right + 1):
                if (r, c) != (top, left) and (r, c) not in at:
                    columns.setdefault(c, []).append((r, c, corner[2], corner[3], corner[4]))
    index = {}
    for c, entries in columns.items():
        entries.sort(key=lambda e: e[0])
        last, previous = -1, []
        for i, entry in enumerate(entries):
            previous.append(last)
            if not entry[4]:
                last = i
        index[c] = ([e[0] for e in entries], entries, previous)
    for cell in cells:
        rows, entries, previous = index[cell["_col"]]
        header = {}
        if entries[0][0] == 1 and cell["_row"] > 1:
            header[entries[0][2]] = entries[0][3]
        i = bisect_left(rows, cell["_row"])
        j = (i - 1 if i and not entries[i - 1][4] else previous[i - 1] if i else -1) if cell["_number"] else -1
        run = 0
        while j >= 0 and not entries[j][4] and run < 3:
            header.setdefault(entries[j][2], entries[j][3])
            run, j = run + 1, j - 1
        cell["context"]["column_header"] = header
        del cell["_row"], cell["_col"], cell["_number"]


def xlsx_units(data, sheets=None, cells=None):
    ns = {"m": SHEET_NS}
    rel = "http://schemas.openxmlformats.org/package/2006/relationships"
    office = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
    v_tag, f_tag, t_tag = "{%s}v" % SHEET_NS, "{%s}f" % SHEET_NS, "{%s}t" % SHEET_NS
    bounds = cell_range(cells) if cells else None
    units = []
    warnings = []
    with archive(data, MAX_WORKBOOK_EXPANDED) as z:
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
        book = ET.fromstring(z.read("xl/workbook.xml"))
        properties = book.find("m:workbookPr", ns)
        date1904 = properties is not None and properties.get("date1904") in ("1", "true")
        formats = number_formats(z)
        listed = [(s.attrib["name"], s.attrib[office]) for s in book.findall("m:sheets/m:sheet", ns)]
        names = [name for name, _ in listed]
        missing = [name for name in sheets or [] if name not in names]
        if missing:
            raise EvidenceError(
                "The workbook has no sheet named " + ", ".join(repr(m) for m in missing)
                + "; its sheets are " + ", ".join(names)
            )  # fmt: skip
        for name, rid in listed:
            if sheets and name not in sheets:
                continue
            target = rels[rid]
            target = target.lstrip("/") if target.startswith("/") else "xl/" + target
            if ".." in Path(target).parts:
                raise EvidenceError("Worksheet path escapes archive")
            found, above, merges, rows = [], {}, [], {}
            cached = uncached = 0
            for kind_of_item, c in sheet_items(z, target):
                if kind_of_item == "merge":
                    merges.append(c)
                    continue
                at = c.attrib["r"]
                row, column = position(at)
                v = c.find(v_tag)
                formula = c.find(f_tag)
                kind = c.get("t")
                if kind == "inlineStr":
                    raw = "".join(t.text or "" for t in c.iter(t_tag))
                elif v is None:
                    raw = ""
                elif kind == "s":
                    raw = strings[int(v.text)]
                else:
                    raw = v.text or ""
                style = int(c.get("s", "0"))
                text, extra = shown(raw, kind, formats[style] if style < len(formats) else "General", date1904)
                number = kind in (None, "n") and bool(raw)
                if bounds and not (bounds[0] <= row <= bounds[2] and bounds[1] <= column <= bounds[3]):
                    # Keep the labels above the range that can head its cells: the first row and the last run.
                    if text and row < bounds[0] and bounds[1] <= column <= bounds[3]:
                        heading = above.setdefault(column, {"first": [], "run": [], "open": False})
                        entry = (row, column, at, text, number)
                        if row == 1:
                            heading["first"] = [entry]
                        elif number:
                            heading["open"] = False
                        else:
                            heading["run"] = (heading["run"] if heading["open"] else [])[-2:] + [entry]
                            heading["open"] = True
                    continue
                if formula is not None:
                    cached, uncached = cached + bool(raw), uncached + (not raw)
                if not text:
                    continue
                if len(units) + len(found) >= MAX_UNITS:
                    raise EvidenceError(
                        f"Workbook has more than {MAX_UNITS:,} non-empty cells, so it was not read past sheet "
                        f"{name!r}; read one sheet with --sheet or a block of cells with --range. "
                        "Its sheets are " + ", ".join(names)
                    )
                rows.setdefault(row, {})[at] = text
                found.append(
                    {
                        "locator": f"cell:{name}!{at}",
                        "text": text,
                        "context": {
                            "sheet": name,
                            "cell": at,
                            **extra,
                            "formula": formula.text if formula is not None else None,
                            "cached_formula": formula is not None,
                            # Nearby labels stay readable without being a verified interpretation of each number.
                            "row_cells": rows[row],
                        },
                        "_row": row,
                        "_col": column,
                        "_number": number,
                    }
                )
            if found:
                add_headers(found, [e for h in above.values() for e in h["first"] + h["run"]], merges)
            if cached:
                warnings.append(
                    f"{name}: {cached:,} formula {'cells show cached values' if cached > 1 else 'cell shows a cached value'}"
                    " that were not recalculated"
                )
            if uncached:
                warnings.append(
                    f"{name}: {uncached:,} formula {'cells have' if uncached > 1 else 'cell has'} no cached value"
                )
            units += found
    return units, warnings


def too_many():
    return EvidenceError(
        f"Source has more than {MAX_UNITS:,} passages (lines, rows or cells); save a smaller part of it"
    )


def extract(data, format, charset=None, sheets=None, cells=None):
    limit = MAX_WORKBOOK_BYTES if format == "xlsx" else MAX_BYTES
    if len(data) > limit:
        raise EvidenceError(f"Source exceeds the {limit / 2**20:g} MiB limit")
    if (sheets or cells) and format != "xlsx":
        raise EvidenceError("--sheet and --range apply only to Excel workbooks")
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
            if len(units) + len(row) >= MAX_UNITS:
                raise too_many()
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
            units, warnings = docx_units(data) if format == "docx" else xlsx_units(data, sheets, cells)
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
        units, blank, garbled = [], [], []
        for page_no, text in enumerate(texts, 1):
            if not text.strip() or scrambled(text):
                (garbled if text.strip() else blank).append(page_no)
                continue
            for unit in text_units(text):
                unit["locator"] = f"page:{page_no}/" + unit["locator"]
                unit["context"] = {"page": page_no}
                units.append(unit)
        if blank:
            warnings.append(page_list(blank) + " no extractable text; OCR needed")
        if garbled:
            warnings.append(page_list(garbled) + " scrambled text; OCR needed")
        warnings.append("PDF text extraction has no bounding boxes; visually check table order, units and columns")
    else:
        raise EvidenceError("Supported formats: txt, md, json, csv, html, pdf, docx, xlsx")
    if len(units) > MAX_UNITS:
        raise too_many()
    result = {"format": format, "units": units, "warnings": warnings}
    if format == "pdf" and (blank or garbled):
        # Searching such a source cannot show that something is absent from it.
        result["unread_pages"] = sorted(blank + garbled)
    return result
