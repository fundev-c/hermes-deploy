"""Text out of the binary formats teams actually send: Excel workbooks and PDFs.

Everything read here arrived from a member of the team and is parsed as hostile:
openpyxl parses through the venv's defusedxml (no entity expansion), both parsers
see a file already capped at MAX_BINARY_BYTES, and every loop is bounded by rows,
pages and output characters rather than by what the file claims about itself --
a zip bomb or a PDF declaring a million pages stops at the cap, not at the end.
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path
from typing import Dict, Optional, Tuple

MAX_BINARY_BYTES = 25_000_000
MAX_TEXT_CHARS = 1_000_000
MAX_SHEET_ROWS = 5_000
MAX_SHEET_COLS = 100
MAX_PDF_PAGES = 300

EXCEL_SUFFIXES = {".xlsx", ".xlsm"}
PDF_SUFFIXES = {".pdf"}
BINARY_SUFFIXES = EXCEL_SUFFIXES | PDF_SUFFIXES


class ExtractError(Exception):
    """The file could not be turned into text; the message is safe to show the model."""


def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def extract_excel(path: Path, sheet: Optional[str] = None) -> Tuple[str, Dict]:
    """Each sheet as CSV under a ``## Sheet: <name>`` heading.

    ``data_only=True`` returns the values Excel cached at last save, not formulas: that
    is what a person looking at the sheet sees. A workbook written by a script and never
    opened in Excel has no cached values, so formula cells come back empty -- reported
    in the metadata rather than silently looking like blanks.
    """
    import openpyxl
    # A handle, not the path: openpyxl refuses a *name* not ending in .xlsx, which is every
    # BearDrive conflict copy (report.xlsx.bdrive-conflict-...). read_only streams from the
    # handle, so it stays open until the workbook is closed.
    fh = open(path, "rb")
    try:
        wb = openpyxl.load_workbook(fh, read_only=True, data_only=True)
    except Exception as e:  # corrupt, encrypted, not really a workbook
        fh.close()
        raise ExtractError(f"could not open the workbook: {type(e).__name__}") from e
    try:
        names = wb.sheetnames
        if sheet is not None:
            match = next((n for n in names if n.casefold() == sheet.strip().casefold()), None)
            if match is None:
                raise ExtractError(f"no sheet named {sheet!r}; sheets: {', '.join(names)}")
            names = [match]
        out, meta = io.StringIO(), {"format": "xlsx", "sheets": wb.sheetnames, "truncated": []}
        for name in names:
            ws = wb[name]
            out.write(f"## Sheet: {name}\n")
            writer = csv.writer(out, lineterminator="\n")
            rows = 0
            for row in ws.iter_rows(values_only=True):
                if rows >= MAX_SHEET_ROWS:
                    meta["truncated"].append(f"{name}: first {MAX_SHEET_ROWS} rows only")
                    break
                cells = [_cell(v) for v in row[:MAX_SHEET_COLS]]
                while cells and cells[-1] == "":
                    cells.pop()
                writer.writerow(cells)
                rows += 1
                if out.tell() > MAX_TEXT_CHARS:
                    meta["truncated"].append(f"{name}: output limit reached")
                    break
            out.write("\n")
            if out.tell() > MAX_TEXT_CHARS:
                break
        return out.getvalue()[:MAX_TEXT_CHARS], meta
    finally:
        wb.close()
        fh.close()


_PAGES_RE = re.compile(r"^\s*(\d+)\s*(?:-\s*(\d+))?\s*$")


def _page_range(pages: Optional[str], total: int) -> range:
    if not pages:
        return range(0, min(total, MAX_PDF_PAGES))
    m = _PAGES_RE.match(pages)
    if not m:
        raise ExtractError("'pages' must look like '3' or '10-20'")
    first = int(m.group(1))
    last = int(m.group(2) or first)
    if first < 1 or last < first:
        raise ExtractError("'pages' must be a positive range, first <= last")
    last = min(last, total, first + MAX_PDF_PAGES - 1)
    return range(first - 1, last)


def extract_pdf(path: Path, pages: Optional[str] = None) -> Tuple[str, Dict]:
    """Page text under ``## Page N`` headings. Scanned PDFs have no text layer and say so."""
    from pypdf import PdfReader
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted and not reader.decrypt(""):
            raise ExtractError("the PDF is password-protected")
        total = len(reader.pages)
    except ExtractError:
        raise
    except Exception as e:
        raise ExtractError(f"could not open the PDF: {type(e).__name__}") from e

    wanted = _page_range(pages, total)
    out, meta = io.StringIO(), {"format": "pdf", "pages_total": total, "truncated": []}
    empty = 0
    for i in wanted:
        try:
            text = reader.pages[i].extract_text() or ""
        except Exception:
            text = ""
        if not text.strip():
            empty += 1
        out.write(f"## Page {i + 1}\n{text.strip()}\n\n")
        if out.tell() > MAX_TEXT_CHARS:
            meta["truncated"].append(f"output limit reached at page {i + 1}")
            break
    meta["pages_read"] = f"{wanted.start + 1}-{min(wanted.stop, total)}" if total else "none"
    if len(wanted) < total and not pages:
        meta["truncated"].append(f"first {len(wanted)} of {total} pages; pass pages='N-M' for more")
    if wanted and empty == len(wanted):
        meta["note"] = "no extractable text: the PDF is probably scanned images (OCR is not available)"
    return out.getvalue()[:MAX_TEXT_CHARS], meta


def extract(path: Path, suffix: str, *, sheet: Optional[str] = None, pages: Optional[str] = None) -> Tuple[str, Dict]:
    if suffix in EXCEL_SUFFIXES:
        return extract_excel(path, sheet)
    if suffix in PDF_SUFFIXES:
        return extract_pdf(path, pages)
    raise ExtractError(f"'{suffix}' is not a supported document type")
