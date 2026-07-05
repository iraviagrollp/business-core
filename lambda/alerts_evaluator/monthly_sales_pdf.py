"""
monthly_sales_pdf — evaluator-only PDF renderer for the Monthly Sales report.

Public surface
--------------
render_monthly_sales_pdf(data: dict) -> bytes
    data    : dict returned by monthly_sales.compute_monthly_sales()
    returns : raw PDF bytes (A4 portrait)

Requires: reportlab (lambda/alerts_evaluator/requirements.txt).
This module is NOT imported by the api Lambda; the api Lambda has no PDF deps.
"""

from __future__ import annotations

from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# ── constants ─────────────────────────────────────────────────────────────────
_HEADER_COLOR   = colors.HexColor('#1a5276')
_TOTAL_BG_COLOR = colors.HexColor('#d5e8f5')
_ALT_ROW_COLOR  = colors.HexColor('#f2f2f2')

_PAGE_W, _PAGE_H = A4            # 595.27 pt × 841.89 pt  (portrait)
_MARGIN           = 1.5 * cm
_CONTENT_W        = _PAGE_W - 2 * _MARGIN   # ≈ 18 cm usable width

_FOOTER_TEXT = (
    "IRAVI AGRO LIFE LLP  |  Reg. Office: Near Old Bus Stand, "
    "Guntur – 522 001, Andhra Pradesh, India"
)


# ── formatting helpers ────────────────────────────────────────────────────────

def _to_lakhs(value: float) -> str:
    """Format a raw rupee value as lakhs (÷100 000) to 2 decimal places."""
    return f"{value / 100_000:.2f}"


def _cell_val(day_date_str: str, value: float, as_on_date: str) -> str:
    """Return the display string for a daily-table value cell.

    Rules (per spec):
      day_date_str > as_on_date  →  ""   (future day — leave blank)
      value == 0.0               →  "-"  (no sales on this day)
      else                       →  lakhs formatted to 2 dp
    """
    if day_date_str > as_on_date:
        return ""
    if value == 0.0:
        return "-"
    return _to_lakhs(value)


def _lk(value: float) -> str:
    """Format value in lakhs; show '-' for zero (used outside the daily table)."""
    if value == 0.0:
        return "-"
    return _to_lakhs(value)


# ── footer callback ───────────────────────────────────────────────────────────

def _draw_footer(canvas, doc):
    """Draw the registered-address footer on every page."""
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(colors.HexColor("#666666"))
    canvas.drawCentredString(_PAGE_W / 2, 0.55 * cm, _FOOTER_TEXT)
    canvas.restoreState()


# ── public API ────────────────────────────────────────────────────────────────

def render_monthly_sales_pdf(data: dict) -> bytes:
    """
    Render the Monthly Sales report as a PDF and return raw bytes.

    Parameters
    ----------
    data : dict returned by monthly_sales.compute_monthly_sales()

    Returns
    -------
    bytes : raw PDF content suitable for attaching to an SES email
    """
    buffer = BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=_MARGIN,
        rightMargin=_MARGIN,
        topMargin=2.0 * cm,
        bottomMargin=1.5 * cm,
        title=f"IAL Monthly Sales — {data['month_label']}",
        author="IRAVI AGRO LIFE LLP",
    )

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "IALTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=14,
        alignment=TA_CENTER,
        spaceAfter=3,
    )
    subtitle_style = ParagraphStyle(
        "IALSubtitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=10,
        alignment=TA_CENTER,
        spaceAfter=2,
    )
    note_style = ParagraphStyle(
        "IALNote",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8,
        alignment=TA_RIGHT,
        spaceAfter=6,
    )
    section_style = ParagraphStyle(
        "IALSection",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=10,
        alignment=TA_LEFT,
        spaceBefore=10,
        spaceAfter=4,
    )

    elements = []

    # ── Title block ───────────────────────────────────────────────────────────
    elements.append(Paragraph("IRAVI AGRO LIFE LLP", title_style))
    elements.append(Paragraph(
        f"STATE WISE NET SALES (WITHOUT TAX) FOR THE MONTH OF {data['month_label']}",
        subtitle_style,
    ))
    elements.append(Paragraph("(Value In Lakhs)", note_style))

    # ── Daily table ───────────────────────────────────────────────────────────
    as_on = data["as_on_date"]
    gt    = data["grand_total"]

    # Column widths sum to _CONTENT_W ≈ 18 cm
    daily_col_w = [3.0 * cm, 5.0 * cm, 5.0 * cm, 5.0 * cm]

    day_rows: list[list] = [["DATE", "ANDHRA", "TELANGANA", "Total"]]
    for day in data["days"]:
        d_str = day["date"]
        if d_str > as_on:
            day_rows.append([d_str, "", "", ""])
        else:
            day_rows.append([
                d_str,
                _cell_val(d_str, day["andhra"],    as_on),
                _cell_val(d_str, day["telangana"], as_on),
                _cell_val(d_str, day["total"],     as_on),
            ])

    # Grand total row
    grand_row_idx = len(day_rows)   # 0-based index of the GRAND TOTAL row
    day_rows.append([
        "GRAND TOTAL",
        _lk(gt["andhra"]),
        _lk(gt["telangana"]),
        _lk(gt["total"]),
    ])

    day_tbl = Table(day_rows, colWidths=daily_col_w, repeatRows=1)
    day_tbl.setStyle(TableStyle([
        # Header row styling
        ("BACKGROUND",      (0, 0),                (-1, 0),                _HEADER_COLOR),
        ("TEXTCOLOR",       (0, 0),                (-1, 0),                colors.white),
        ("FONTNAME",        (0, 0),                (-1, 0),                "Helvetica-Bold"),
        ("FONTSIZE",        (0, 0),                (-1, 0),                9),
        # Data rows styling
        ("FONTNAME",        (0, 1),                (-1, grand_row_idx - 1), "Helvetica"),
        ("FONTSIZE",        (0, 1),                (-1, -1),               9),
        # Alternating row backgrounds (data rows only)
        ("ROWBACKGROUNDS",  (0, 1),                (-1, grand_row_idx - 1),
         [colors.white, _ALT_ROW_COLOR]),
        # Grand total row
        ("FONTNAME",        (0, grand_row_idx),    (-1, grand_row_idx),    "Helvetica-Bold"),
        ("BACKGROUND",      (0, grand_row_idx),    (-1, grand_row_idx),    _TOTAL_BG_COLOR),
        # Alignment and borders
        ("ALIGN",           (0, 0),                (-1, -1),               "CENTER"),
        ("GRID",            (0, 0),                (-1, -1),               0.4, colors.grey),
        ("TOPPADDING",      (0, 0),                (-1, -1),               3),
        ("BOTTOMPADDING",   (0, 0),                (-1, -1),               3),
    ]))
    elements.append(day_tbl)

    # ── Sales Analysis block ──────────────────────────────────────────────────
    analysis    = data["analysis"]
    fy_label    = data["fy_label"]
    prev_m_lbl  = analysis["prev_month_label"]
    month_label = data["month_label"]

    utp = analysis["up_to_prev_month"]
    aod = analysis["as_on_date"]

    col1_hdr = f"{fy_label} up to {prev_m_lbl}"
    col2_hdr = f"{month_label} as on Date"

    elements.append(Paragraph("SALES ANALYSIS", section_style))

    # Column widths: STATE | col1 | col2 — total 18 cm
    an_col_w = [3.0 * cm, 7.5 * cm, 7.5 * cm]

    an_rows: list[list] = [
        ["STATE",  col1_hdr,              col2_hdr],
        ["AP",     _lk(utp["andhra"]),    _lk(aod["andhra"])],
        ["TS",     _lk(utp["telangana"]), _lk(aod["telangana"])],
        ["Total",  _lk(utp["total"]),     _lk(aod["total"])],
    ]
    an_total_idx = len(an_rows) - 1

    an_tbl = Table(an_rows, colWidths=an_col_w)
    an_tbl.setStyle(TableStyle([
        ("BACKGROUND",      (0, 0),              (-1, 0),              _HEADER_COLOR),
        ("TEXTCOLOR",       (0, 0),              (-1, 0),              colors.white),
        ("FONTNAME",        (0, 0),              (-1, 0),              "Helvetica-Bold"),
        ("FONTSIZE",        (0, 0),              (-1, -1),             9),
        ("FONTNAME",        (0, 1),              (-1, an_total_idx - 1), "Helvetica"),
        ("FONTNAME",        (0, an_total_idx),   (-1, an_total_idx),   "Helvetica-Bold"),
        ("BACKGROUND",      (0, an_total_idx),   (-1, an_total_idx),   _TOTAL_BG_COLOR),
        ("ALIGN",           (0, 0),              (-1, -1),             "CENTER"),
        ("GRID",            (0, 0),              (-1, -1),             0.4, colors.grey),
        ("TOPPADDING",      (0, 0),              (-1, -1),             3),
        ("BOTTOMPADDING",   (0, 0),              (-1, -1),             3),
    ]))
    elements.append(an_tbl)

    # ── Month-only block ──────────────────────────────────────────────────────
    month_name = month_label.split()[0]   # e.g. "JUNE" from "JUNE 2026"
    elements.append(Paragraph(f"{month_name} MONTH ONLY", section_style))

    # Narrower table; left-aligned on the page
    mo_col_w = [5.0 * cm, 7.0 * cm]

    mo_rows: list[list] = [
        ["STATE",  "Actual Sales"],
        ["AP",     _lk(gt["andhra"])],
        ["TS",     _lk(gt["telangana"])],
        ["Total",  _lk(gt["total"])],
    ]
    mo_total_idx = len(mo_rows) - 1

    mo_tbl = Table(mo_rows, colWidths=mo_col_w)
    mo_tbl.setStyle(TableStyle([
        ("BACKGROUND",      (0, 0),              (-1, 0),              _HEADER_COLOR),
        ("TEXTCOLOR",       (0, 0),              (-1, 0),              colors.white),
        ("FONTNAME",        (0, 0),              (-1, 0),              "Helvetica-Bold"),
        ("FONTSIZE",        (0, 0),              (-1, -1),             9),
        ("FONTNAME",        (0, 1),              (-1, mo_total_idx - 1), "Helvetica"),
        ("FONTNAME",        (0, mo_total_idx),   (-1, mo_total_idx),   "Helvetica-Bold"),
        ("BACKGROUND",      (0, mo_total_idx),   (-1, mo_total_idx),   _TOTAL_BG_COLOR),
        ("ALIGN",           (0, 0),              (-1, -1),             "CENTER"),
        ("GRID",            (0, 0),              (-1, -1),             0.4, colors.grey),
        ("TOPPADDING",      (0, 0),              (-1, -1),             3),
        ("BOTTOMPADDING",   (0, 0),              (-1, -1),             3),
    ]))
    elements.append(mo_tbl)

    # ── Build PDF with footer on every page ───────────────────────────────────
    doc.build(elements, onFirstPage=_draw_footer, onLaterPages=_draw_footer)
    return buffer.getvalue()
