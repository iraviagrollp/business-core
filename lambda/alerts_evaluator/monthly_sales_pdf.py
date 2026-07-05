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

import os
from datetime import date as _date, datetime
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# ── constants ─────────────────────────────────────────────────────────────────
_HEADER_COLOR   = colors.HexColor('#1a3c2b')   # dark green — matches iravi-ui brand
_TOTAL_BG_COLOR = colors.HexColor('#f0f0f0')   # light grey for GRAND TOTAL / Total rows
_ALT_ROW_COLOR  = colors.HexColor('#fafafa')   # subtle zebra stripe on even data rows

_PAGE_W, _PAGE_H = A4                          # 595.27 pt x 841.89 pt (portrait)
_MARGIN          = 1.0 * cm                    # left and right
_CONTENT_W       = _PAGE_W - 2 * _MARGIN       # approx 538 pt usable width

# Logo bundled alongside this module; falls back gracefully if absent
_LOGO_PATH = os.path.join(os.path.dirname(__file__), 'ial-logo.png')

# Footer text — matches iravi-ui buildMonthlySalesHtml exactly
_FOOTER_LINE1 = (
    "Reg. Address: Flat No: 102, BVR Plaza, H.No.5, 3-112/2, BJP Office Line  "
    "Shanthi Nagar, Kukatpally, Hyderabad, Telangana 500072"
)
_FOOTER_LINE2 = (
    "This report is computer-generated. Values are in Lakhs (1 Lakh = ₹1,00,000). "
    "AP = Andhra Pradesh; TS = Telangana."
)


# ── formatting helpers ────────────────────────────────────────────────────────

def _to_lakhs(value: float) -> str:
    """Format a raw rupee value as lakhs (/ 100 000) to 2 decimal places."""
    return f"{value / 100_000:.2f}"


def _fmt_date(date_str: str) -> str:
    """'YYYY-MM-DD' -> 'DD-Mon-YYYY', e.g. '01-Jul-2026'."""
    try:
        return datetime.strptime(date_str, '%Y-%m-%d').strftime('%d-%b-%Y')
    except ValueError:
        return date_str


def _cell_val(day_date_str: str, value: float, as_on_date: str) -> str:
    """Return the display string for a daily-table value cell.

    Rules (per spec):
      day_date_str > as_on_date  ->  ""   (future day — leave blank)
      value == 0.0               ->  "-"  (no sales on this day)
      else                       ->  lakhs formatted to 2 dp
    """
    if day_date_str > as_on_date:
        return ""
    if value == 0.0:
        return "-"
    return _to_lakhs(value)


def _lk(value: float) -> str:
    """Format value in lakhs; show '-' for zero."""
    if value == 0.0:
        return "-"
    return _to_lakhs(value)


# ── footer callback ───────────────────────────────────────────────────────────

def _draw_footer(canvas, doc):
    """Draw the two-line Kukatpally address footer on every page."""
    canvas.saveState()
    canvas.setFont("Helvetica", 6.5)
    canvas.setFillColor(colors.HexColor("#666666"))
    canvas.drawCentredString(_PAGE_W / 2, 0.70 * cm, _FOOTER_LINE1)
    canvas.drawCentredString(_PAGE_W / 2, 0.38 * cm, _FOOTER_LINE2)
    canvas.restoreState()


# ── shared table-style builder ────────────────────────────────────────────────

def _tbl_cmds(header_row: int, total_row: int, data_start: int, data_end: int) -> list:
    """Base TableStyle commands common to all three report tables.

    header_row  : row index of the dark-green header
    total_row   : row index of the grey-shaded total row
    data_start  : first ordinary data row
    data_end    : last ordinary data row (inclusive)
    """
    return [
        # Header
        ("BACKGROUND",    (0, header_row), (-1, header_row), _HEADER_COLOR),
        ("TEXTCOLOR",     (0, header_row), (-1, header_row), colors.white),
        ("FONTNAME",      (0, header_row), (-1, header_row), "Helvetica-Bold"),
        # Data rows
        ("FONTNAME",      (0, data_start), (-1, data_end),   "Helvetica"),
        # Total row
        ("FONTNAME",      (0, total_row),  (-1, total_row),  "Helvetica-Bold"),
        ("BACKGROUND",    (0, total_row),  (-1, total_row),  _TOTAL_BG_COLOR),
        # Universal
        ("FONTSIZE",      (0, 0),          (-1, -1),         7),
        ("GRID",          (0, 0),          (-1, -1),         0.3, colors.HexColor("#cccccc")),
        ("TOPPADDING",    (0, 0),          (-1, -1),         2),
        ("BOTTOMPADDING", (0, 0),          (-1, -1),         2),
        ("LEFTPADDING",   (0, 0),          (-1, -1),         3),
        ("RIGHTPADDING",  (0, 0),          (-1, -1),         3),
    ]


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
        topMargin=0.8 * cm,
        bottomMargin=1.4 * cm,      # footer draws at 0.4–0.7 cm; 1.4 cm leaves clearance
        title=f"IAL Monthly Sales — {data['month_label']}",
        author="IRAVI AGRO LIFE LLP",
    )

    # ── Paragraph styles ──────────────────────────────────────────────────────
    company_style = ParagraphStyle(
        "IALCompany",
        fontName="Helvetica-Bold",
        fontSize=13,
        alignment=TA_CENTER,
        leading=15,
        spaceAfter=2,
    )
    subtitle_style = ParagraphStyle(
        "IALSubtitle",
        fontName="Helvetica-Bold",
        fontSize=7,
        alignment=TA_CENTER,
        leading=9,
        spaceAfter=0,
    )
    right_style = ParagraphStyle(
        "IALRight",
        fontName="Helvetica",
        fontSize=7,
        alignment=TA_RIGHT,
        leading=9,
        spaceAfter=0,
    )
    section_style = ParagraphStyle(
        "IALSection",
        fontName="Helvetica-Bold",
        fontSize=8,
        alignment=TA_CENTER,
        spaceBefore=6,
        spaceAfter=3,
    )

    # ── Letterhead: 3-column header table ────────────────────────────────────
    today_str = _date.today().strftime('%d-%m-%Y')

    # Load logo; on any failure render without it (never crash)
    try:
        logo_cell = Image(_LOGO_PATH, width=1.1 * cm, height=1.1 * cm)
    except Exception:
        logo_cell = Spacer(1.1 * cm, 1.1 * cm)

    logo_col_w  = 1.3 * cm
    right_col_w = 2.8 * cm
    ctr_col_w   = _CONTENT_W - logo_col_w - right_col_w

    hdr_tbl = Table(
        [[
            logo_cell,
            [
                Paragraph("IRAVI AGRO LIFE LLP", company_style),
                Paragraph(
                    f"STATE WISE NET SALES (WITHOUT TAX) FOR THE MONTH OF {data['month_label']}",
                    subtitle_style,
                ),
            ],
            [
                Paragraph(f"Date: {today_str}", right_style),
                Paragraph("(Value In Lakhs)", right_style),
            ],
        ]],
        colWidths=[logo_col_w, ctr_col_w, right_col_w],
    )
    hdr_tbl.setStyle(TableStyle([
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING",   (0, 0), (-1, -1), 0),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 0),
        ("TOPPADDING",    (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))

    elements: list = [hdr_tbl, Spacer(1, 5)]

    # ── Daily table ───────────────────────────────────────────────────────────
    as_on = data["as_on_date"]
    gt    = data["grand_total"]

    # DATE col slightly wider to fit 'DD-Mon-YYYY'; remaining three val cols share equally
    date_col_w  = 2.8 * cm
    val_col_w   = (_CONTENT_W - date_col_w) / 3
    daily_col_w = [date_col_w, val_col_w, val_col_w, val_col_w]

    day_rows: list[list] = [["DATE", "ANDHRA", "TELANGANA", "Total"]]
    for day in data["days"]:
        d_str = day["date"]
        day_rows.append([
            _fmt_date(d_str),
            _cell_val(d_str, day["andhra"],    as_on),
            _cell_val(d_str, day["telangana"], as_on),
            _cell_val(d_str, day["total"],     as_on),
        ])

    grand_row_idx = len(day_rows)   # 0-based index of the GRAND TOTAL row
    day_rows.append([
        "GRAND TOTAL",
        _lk(gt["andhra"]),
        _lk(gt["telangana"]),
        _lk(gt["total"]),
    ])

    day_cmds = _tbl_cmds(
        header_row=0,
        total_row=grand_row_idx,
        data_start=1,
        data_end=grand_row_idx - 1,
    )
    day_cmds += [
        # Zebra stripe: white for odd rows, #fafafa for even rows (matches UI)
        ("ROWBACKGROUNDS", (0, 1), (-1, grand_row_idx - 1), [colors.white, _ALT_ROW_COLOR]),
        ("ALIGN",          (0, 0), (-1, -1),                "CENTER"),
    ]

    day_tbl = Table(day_rows, colWidths=daily_col_w, repeatRows=1)
    day_tbl.setStyle(TableStyle(day_cmds))
    elements.append(day_tbl)

    # ── Sales Analysis section ────────────────────────────────────────────────
    analysis    = data["analysis"]
    fy_label    = data["fy_label"]
    prev_m_lbl  = analysis["prev_month_label"]
    month_label = data["month_label"]
    utp         = analysis["up_to_prev_month"]
    aod         = analysis["as_on_date"]

    col1_hdr = f"{fy_label} up to {prev_m_lbl}"
    col2_hdr = f"{month_label} as on Date"

    elements.append(Paragraph("<u>SALES ANALYSIS</u>", section_style))

    # 70 % of content width, centred; State col left-aligned, values centred
    an_w     = _CONTENT_W * 0.70
    an_st_w  = 2.0 * cm
    an_val_w = (an_w - an_st_w) / 2

    an_rows: list[list] = [
        ["State", col1_hdr,              col2_hdr],
        ["AP",    _lk(utp["andhra"]),    _lk(aod["andhra"])],
        ["TS",    _lk(utp["telangana"]), _lk(aod["telangana"])],
        ["Total", _lk(utp["total"]),     _lk(aod["total"])],
    ]
    an_total_idx = len(an_rows) - 1

    an_cmds = _tbl_cmds(0, an_total_idx, 1, an_total_idx - 1)
    an_cmds += [
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("ALIGN", (0, 0), (0, -1),  "LEFT"),   # State column left-aligned
    ]

    an_tbl = Table(an_rows, colWidths=[an_st_w, an_val_w, an_val_w], hAlign='CENTER')
    an_tbl.setStyle(TableStyle(an_cmds))
    elements.append(an_tbl)

    # ── Month-only section ────────────────────────────────────────────────────
    month_name = month_label.split()[0]   # e.g. "JUNE" from "JUNE 2026"
    elements.append(Paragraph(f"<u>{month_name} MONTH ONLY</u>", section_style))

    # 45 % of content width, centred; STATE col left-aligned, Actual Sales centred
    mo_w     = _CONTENT_W * 0.45
    mo_st_w  = mo_w * 0.45
    mo_val_w = mo_w - mo_st_w

    mo_rows: list[list] = [
        ["STATE",  "Actual Sales"],
        ["AP",     _lk(gt["andhra"])],
        ["TS",     _lk(gt["telangana"])],
        ["Total",  _lk(gt["total"])],
    ]
    mo_total_idx = len(mo_rows) - 1

    mo_cmds = _tbl_cmds(0, mo_total_idx, 1, mo_total_idx - 1)
    mo_cmds += [
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("ALIGN", (0, 0), (0, -1),  "LEFT"),   # STATE column left-aligned
    ]

    mo_tbl = Table(mo_rows, colWidths=[mo_st_w, mo_val_w], hAlign='CENTER')
    mo_tbl.setStyle(TableStyle(mo_cmds))
    elements.append(mo_tbl)

    # ── Build PDF with footer on every page ───────────────────────────────────
    doc.build(elements, onFirstPage=_draw_footer, onLaterPages=_draw_footer)
    return buffer.getvalue()
