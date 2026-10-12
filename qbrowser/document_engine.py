"""Mature-library document creation, text conversion, inspection and deterministic QA.

Uses python-docx, python-pptx, openpyxl, PyMuPDF and ReportLab. Unsupported
layout-preserving conversions fail explicitly rather than silently degrade.
"""
from __future__ import annotations

import csv
import io
import re
from pathlib import Path
from typing import Any

SUPPORTED = {".txt", ".md", ".csv", ".pdf", ".docx", ".xlsx", ".pptx", ".png", ".jpg", ".jpeg"}
OFFICE = {".docx", ".xlsx", ".pptx"}
MAX_TEXT = 2_000_000
MAX_CELLS = 200_000
MAX_SLIDES = 100
MAX_PARAGRAPHS = 2000


def _text(value: Any, max_chars: int = MAX_TEXT) -> str:
    if not isinstance(value, str) or len(value) > max_chars:
        raise ValueError("Expected bounded UTF-8 text")
    return value


def _blocks(data: dict) -> list[dict]:
    blocks = data.get("blocks", [])
    if not isinstance(blocks, list) or len(blocks) > MAX_PARAGRAPHS:
        raise ValueError("blocks must be a bounded list")
    for block in blocks:
        if not isinstance(block, dict) or block.get("type", "paragraph") not in {
            "paragraph", "heading", "bullet", "table", "page_break"
        }:
            raise ValueError("Unsupported document block")
        if block.get("type") == "table":
            rows = block.get("rows")
            if not isinstance(rows, list) or not rows or len(rows) > 500:
                raise ValueError("Invalid table rows")
            width = len(rows[0]) if isinstance(rows[0], list) else 0
            if not 1 <= width <= 20 or any(not isinstance(r, list) or len(r) != width for r in rows):
                raise ValueError("Table rows must have the same length")
            for row in rows:
                for item in row:
                    _text(str(item), 10000)
        else:
            _text(block.get("text", ""), 50000)
    return blocks


def _create_docx(path: Path, data: dict) -> None:
    from docx import Document
    from docx.shared import Inches, Pt

    doc = Document()
    section = doc.sections[0]
    section.top_margin = section.bottom_margin = Inches(1)
    section.left_margin = section.right_margin = Inches(1)
    font = doc.styles["Normal"].font
    font.name = _text(data.get("font", "Times New Roman"), 100)
    font.size = Pt(float(data.get("font_size", 12)))
    if not 8 <= float(data.get("font_size", 12)) <= 36:
        raise ValueError("Font size outside permitted range")
    doc.core_properties.title = _text(data.get("title", ""), 500)
    if data.get("title"):
        doc.add_heading(data["title"], 0)
    for block in _blocks(data):
        kind = block.get("type", "paragraph")
        if kind == "heading":
            doc.add_heading(block["text"], level=max(1, min(4, int(block.get("level", 1)))))
        elif kind == "bullet":
            doc.add_paragraph(block["text"], style="List Bullet")
        elif kind == "paragraph":
            doc.add_paragraph(block["text"])
        elif kind == "page_break":
            doc.add_page_break()
        elif kind == "table":
            rows = block["rows"]
            table = doc.add_table(rows=0, cols=len(rows[0]))
            table.style = "Table Grid"
            for row in rows:
                cells = table.add_row().cells
                for cell, value in zip(cells, row):
                    cell.text = str(value)
    doc.save(path)


def _create_pdf(path: Path, data: dict) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
    from xml.sax.saxutils import escape

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="QBody", parent=styles["Normal"], fontName="Helvetica",
                               fontSize=11, leading=16, spaceAfter=8))
    story = []
    title = _text(data.get("title", ""), 500)
    if title:
        story.append(Paragraph(escape(title), styles["Title"]))
    for block in _blocks(data):
        kind = block.get("type", "paragraph")
        if kind == "page_break":
            story.append(PageBreak())
        elif kind == "table":
            rows = [[Paragraph(escape(str(item)), styles["QBody"]) for item in row]
                    for row in block["rows"]]
            table = Table(rows, repeatRows=1, hAlign="LEFT")
            table.setStyle(TableStyle([
                ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]))
            story.append(table)
            story.append(Spacer(1, 10))
        else:
            text = escape(block["text"]).replace("\n", "<br/>")
            if kind == "bullet":
                story.append(Paragraph("&#8226; " + text, styles["QBody"]))
            elif kind == "heading":
                story.append(Paragraph(text, styles["Heading2"]))
            else:
                story.append(Paragraph(text or " ", styles["QBody"]))
    doc = SimpleDocTemplate(str(path), pagesize=A4, rightMargin=54, leftMargin=54,
                            topMargin=54, bottomMargin=54, title=title or "Qbrowser")
    doc.build(story or [Paragraph(" ", styles["QBody"])])


def _create_xlsx(path: Path, data: dict) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter
    wb = Workbook()
    sheets = data.get("sheets", [])
    if not isinstance(sheets, list) or not sheets or len(sheets) > 30:
        raise ValueError("sheets must contain 1-30 sheets")
    total = 0
    for index, entry in enumerate(sheets):
        if not isinstance(entry, dict):
            raise ValueError("Invalid sheet")
        rows = entry.get("rows", [])
        if not isinstance(rows, list) or len(rows) > 10000:
            raise ValueError("Invalid worksheet rows")
        name = _text(entry.get("name", f"Sheet{index + 1}"), 31)
        if not name or re.search(r"[\[\]:*?/\\]", name):
            raise ValueError("Invalid Excel sheet name")
        ws = wb.active if index == 0 else wb.create_sheet()
        ws.title = name
        for ri, row in enumerate(rows):
            if not isinstance(row, list) or len(row) > 100:
                raise ValueError("Invalid worksheet row")
            total += len(row)
            if total > MAX_CELLS:
                raise ValueError("Workbook exceeds cell limit")
            for ci, value in enumerate(row, start=1):
                if not (value is None or isinstance(value, (str, int, float, bool))):
                    raise ValueError("Only scalar worksheet cells supported")
                if isinstance(value, str):
                    _text(value, 32767)
                    # Never execute formulas supplied as ordinary text.
                    if value.startswith(("=", "+", "-", "@")):
                        value = "'" + value
                ws.cell(ri + 1, ci, value=value)
                if ri == 0 and entry.get("headers", True):
                    cell = ws.cell(ri + 1, ci)
                    cell.font = Font(bold=True, color="FFFFFF")
                    cell.fill = PatternFill("solid", fgColor="23364B")
        if rows and entry.get("headers", True):
            ws.freeze_panes = "A2"
        for col in range(1, min(21, max((len(row) for row in rows), default=0) + 1)):
            ws.column_dimensions[get_column_letter(col)].width = 22
    wb.save(path)


def _create_pptx(path: Path, data: dict) -> None:
    from pptx import Presentation
    from pptx.util import Inches, Pt
    slides = data.get("slides", [])
    if not isinstance(slides, list) or not 1 <= len(slides) <= MAX_SLIDES:
        raise ValueError("slides must contain 1-100 slides")
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    for slide_data in slides:
        if not isinstance(slide_data, dict):
            raise ValueError("Invalid slide")
        title = _text(slide_data.get("title", ""), 300)
        bullets = slide_data.get("bullets", [])
        if not isinstance(bullets, list) or len(bullets) > 12:
            raise ValueError("Slide bullets exceed safe limit")
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        box = slide.shapes.add_textbox(Inches(.9), Inches(.55), Inches(11.5), Inches(.9))
        p = box.text_frame.paragraphs[0]
        p.text = title
        p.font.size = Pt(30)
        p.font.bold = True
        content = slide.shapes.add_textbox(Inches(1.0), Inches(1.65), Inches(11.2), Inches(5.3))
        tf = content.text_frame
        tf.word_wrap = True
        for i, item in enumerate(bullets):
            line = _text(str(item), 400)
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            p.text = "• " + line
            p.font.size = Pt(18 if len(bullets) <= 7 else 15)
            p.space_after = Pt(12 if len(bullets) <= 7 else 7)
    prs.save(path)


def create_document(path: Path, data: dict) -> None:
    """Create a complete document atomically at caller-chosen location."""
    if not isinstance(data, dict):
        raise ValueError("Document payload must be an object")
    suffix = path.suffix.lower()
    if suffix == ".docx":
        _create_docx(path, data)
    elif suffix == ".pdf":
        _create_pdf(path, data)
    elif suffix == ".xlsx":
        _create_xlsx(path, data)
    elif suffix == ".pptx":
        _create_pptx(path, data)
    elif suffix in (".txt", ".md"):
        body = _text(data.get("text", ""), MAX_TEXT)
        path.write_text(body, encoding="utf-8")
    else:
        raise ValueError(f"Document creation unsupported for {suffix}")


def extract_text(path: Path, *, limit: int = MAX_TEXT) -> str:
    """Parse normal document formats with maintained libraries and bound output."""
    suffix = path.suffix.lower()
    chunks: list[str] = []
    total = 0

    def collect(text: Any):
        nonlocal total
        val = str(text)
        if total >= limit:
            return
        chunks.append(val[:limit - total])
        total += len(chunks[-1]) + 1

    if suffix in (".txt", ".md", ".csv"):
        with path.open("r", encoding="utf-8-sig", errors="replace") as fp:
            return fp.read(limit)
    if suffix == ".docx":
        from docx import Document
        doc = Document(path)
        for p in doc.paragraphs:
            collect(p.text)
        for table in doc.tables:
            for row in table.rows:
                collect(" | ".join(cell.text for cell in row.cells))
    elif suffix == ".pdf":
        import fitz
        with fitz.open(path) as doc:
            for page in doc:
                collect(page.get_text("text"))
                if total >= limit:
                    break
    elif suffix == ".pptx":
        from pptx import Presentation
        prs = Presentation(path)
        for idx, slide in enumerate(prs.slides, start=1):
            collect(f"[Slide {idx}]")
            for shape in slide.shapes:
                if shape.has_text_frame:
                    collect(shape.text)
    elif suffix == ".xlsx":
        from openpyxl import load_workbook
        wb = load_workbook(path, read_only=True, data_only=True)
        try:
            for ws in wb.worksheets:
                collect(f"[Sheet {ws.title}]")
                for row in ws.iter_rows():
                    collect("\t".join(str(cell.value) if cell.value is not None else "" for cell in row))
                    if total >= limit:
                        break
        finally:
            wb.close()
    else:
        raise ValueError(f"Text extraction unsupported for {suffix}")
    return "\n".join(chunks)[:limit]


def convert_document(source: Path, target: Path) -> None:
    """Supported conversions preserve text/data; refuse silent layout loss to PDF."""
    fmt = target.suffix.lower()
    if fmt in (".txt", ".md") and source.suffix.lower() in SUPPORTED - {".png", ".jpg", ".jpeg"}:
        target.write_text(extract_text(source), encoding="utf-8")
    elif source.suffix.lower() in (".txt", ".md") and fmt in (".docx", ".pdf"):
        body = extract_text(source)
        create_document(target, {"title": "", "blocks": [
            {"type": "paragraph", "text": p} for p in body.split("\n\n")[:MAX_PARAGRAPHS]
        ]})
    elif source.suffix.lower() == ".csv" and fmt == ".xlsx":
        with source.open(newline="", encoding="utf-8-sig") as fp:
            rows = list(csv.reader(fp))
        create_document(target, {"sheets": [{"name": "Data", "rows": rows}]})
    elif source.suffix.lower() == ".xlsx" and fmt == ".csv":
        from openpyxl import load_workbook
        wb = load_workbook(source, read_only=True, data_only=True)
        try:
            with target.open("w", newline="", encoding="utf-8") as fp:
                out = csv.writer(fp)
                for row in wb.active.iter_rows(values_only=True):
                    out.writerow([str(x) if x is not None else "" for x in row])
        finally:
            wb.close()
    else:
        raise ValueError(f"Unsupported conversion: {source.suffix} to {fmt}; layout-preserving Office-to-PDF is unavailable")


def inspect_document(path: Path) -> dict:
    """Lightweight metadata, content, formatting concerns and citation heuristics."""
    suffix = path.suffix.lower()
    info: dict[str, Any] = {"format": suffix.lstrip("."), "issues": []}
    if suffix not in SUPPORTED:
        raise ValueError(f"Unsupported format {suffix}")
    if suffix in (".png", ".jpg", ".jpeg"):
        from PIL import Image
        with Image.open(path) as im:
            im.verify()
        with Image.open(path) as im:
            info["width"], info["height"] = im.size
            info["mode"] = im.mode
        return info
    body = extract_text(path)
    info["characters"] = len(body)
    info["words"] = len(body.split())
    info["preview"] = body[:1500]
    if not body.strip():
        info["issues"].append({"code": "empty_document", "severity": "warning", "detail": "No extractable text"})
    if suffix == ".pdf":
        import fitz
        with fitz.open(path) as doc:
            info["pages"] = len(doc)
            info["title"] = doc.metadata.get("title", "")
    elif suffix == ".docx":
        from docx import Document
        doc = Document(path)
        info["title"] = doc.core_properties.title or ""
        info["paragraphs"] = len(doc.paragraphs)
        info["tables"] = len(doc.tables)
        for p in doc.paragraphs:
            for run in p.runs:
                if run.font.size and not 8 <= run.font.size.pt <= 36:
                    info["issues"].append({"code": "font_size", "severity": "warning",
                                           "detail": "Unusually small or large font"})
                    break
    elif suffix == ".xlsx":
        from openpyxl import load_workbook
        wb = load_workbook(path, read_only=True, data_only=False)
        try:
            info["sheets"] = [ws.title for ws in wb.worksheets]
            info["dimensions"] = [{"sheet": ws.title, "rows": ws.max_row, "columns": ws.max_column}
                                  for ws in wb.worksheets]
        finally:
            wb.close()
    elif suffix == ".pptx":
        from pptx import Presentation
        prs = Presentation(path)
        info["slides"] = len(prs.slides)
        for idx, slide in enumerate(prs.slides, 1):
            for shape in slide.shapes:
                if shape.left < 0 or shape.top < 0 or (
                    shape.left + shape.width > prs.slide_width + 10000 or
                    shape.top + shape.height > prs.slide_height + 10000
                ):
                    info["issues"].append({"code": "out_of_slide", "severity": "error",
                        "detail": f"Slide {idx} has an off-canvas shape"})
    # Citation signals are advisory; never claim external source verification.
    citations = re.findall(r"\([A-Z][A-Za-z\-]+(?: et al\.)?,\s*(?:19|20)\d{2}\)", body)
    info["citation_signals"] = len(citations)
    info["references_heading"] = bool(re.search(r"(?im)^\s*(references|bibliography)\s*$", body))
    if citations and not info["references_heading"]:
        info["issues"].append({"code": "missing_references_heading", "severity": "warning",
                               "detail": "In-text author/year patterns appear without a references heading"})
    info["source_verification"] = "not_performed"
    return info


def preview_png(path: Path, *, page: int = 0) -> bytes:
    """Real page preview for PDFs; thumbnail for images. No invented document rendering."""
    from PIL import Image
    if page < 0:
        raise ValueError("Page must be nonnegative")
    out = io.BytesIO()
    if path.suffix.lower() == ".pdf":
        import fitz
        with fitz.open(path) as document:
            if page >= len(document):
                raise ValueError("Page outside document")
            pix = document[page].get_pixmap(matrix=fitz.Matrix(1.25, 1.25), alpha=False)
            return pix.tobytes("png")
    if path.suffix.lower() in (".png", ".jpg", ".jpeg"):
        if page:
            raise ValueError("Image has only one page")
        with Image.open(path) as image:
            image.thumbnail((1200, 1200))
            image.convert("RGB").save(out, "PNG")
        return out.getvalue()
    raise ValueError("Rendered preview is available for PDF and image files only")
