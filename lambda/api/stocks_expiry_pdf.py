"""
stocks_expiry_pdf — PDF renderer for the Stock Expiry report.

Public surface
--------------
render_stocks_expiry_pdf(data: dict) -> bytes
    data    : {'rows': [...], 'brand_filter': str|None, 'cutoff_date': 'YYYY-MM-DD'|None}
              (built by handler._handle_stocks_expiry_pdf from raw, un-aggregated
              snapshot_stock rows — one row per distinct expiry_date, no rate/valuation)
    returns : raw PDF bytes (landscape A4)

Design
------
Mirrors the house report-PDF convention (customer_balances_fy_pdf.py /
supplier_balances_fy_pdf.py / monthly_sales_pdf.py / monthly_collection_pdf.py):
- Landscape A4, 1 cm margins, shared `letterhead` header/footer (repeats on
  every page via draw_header/draw_footer canvas callbacks).
- Helvetica/Helvetica-Bold body font (no rupee glyph needed — this report has
  no monetary columns — so `letterhead.register_fonts()` is not called).
- Single-row header band (letterhead.GREEN, white bold text), zebra-striped
  data rows, repeatRows=1 so the header repeats on every page.
- Report title ('STOCK EXPIRY REPORT') + Date on their own row directly under
  the shared letterhead, followed by a subtitle line describing any active
  filters (Brand / Expiring-before-cutoff), e.g.
  'Brand: GULFONID · Expiring before 21-11-2026' — or 'All Stock' when no
  filter is active.

Columns (NO rate / NO valuation — this report is for expiry tracking only):
  Brand | Technical | Packing | Branch | Special Packing | Available Nos |
  Conversion Factor | Available Cases | Available Qty | Entry Date | Expiry Date
"""

from __future__ import annotations

from datetime import date as _date, datetime
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

import letterhead

# ── constants ─────────────────────────────────────────────────────────────────
_ALT_BG      = colors.HexColor('#fafafa')   # subtle zebra stripe
_CELL_BORDER = colors.HexColor('#cccccc')

_PAGE_W, _PAGE_H = landscape(A4)           # 841.89 x 595.27 pt
_MARGIN = 1.0 * cm
_CONTENT_W = _PAGE_W - 2 * _MARGIN         # ~785 pt usable width

_COLUMNS = [
    ('Brand', 1.0, TA_LEFT),
    ('Technical', 2.2, TA_LEFT),
    ('Packing', 0.9, TA_CENTER),
    ('Branch', 1.1, TA_LEFT),
    ('Special Packing', 1.0, TA_LEFT),
    ('Available Nos', 0.9, TA_RIGHT),
    ('Conversion Factor', 0.9, TA_RIGHT),
    ('Available Cases', 0.9, TA_RIGHT),
    ('Available Qty', 0.9, TA_RIGHT),
    ('Entry Date', 0.9, TA_CENTER),
    ('Expiry Date', 0.9, TA_CENTER),
]


# ── formatting helpers ────────────────────────────────────────────────────────

def _fmt_date(date_str: str | None) -> str:
    """'YYYY-MM-DD' -> 'DD-MM-YYYY'; None/blank -> '-'."""
    if not date_str:
        return '-'
    try:
        return datetime.strptime(date_str, '%Y-%m-%d').strftime('%d-%m-%Y')
    except ValueError:
        return date_str


def _fmt_num(value) -> str:
    """Trim to an int display when whole, else 2 dp; None -> '-'."""
    if value is None:
        return '-'
    v = float(value)
    return str(int(v)) if v == int(v) else f'{v:.2f}'


# ── paragraph style factory ───────────────────────────────────────────────────

def _ps(name: str, font: str, size: float, align: int,
        color=colors.black, leading: float | None = None) -> ParagraphStyle:
    return ParagraphStyle(
        name,
        fontName=font,
        fontSize=size,
        alignment=align,
        leading=leading or (size + 1),
        textColor=color,
    )


def _draw_header_footer(canvas, doc):
    """Combined onFirstPage/onLaterPages callback — draws the repeating
    letterhead header and shared footer on every page."""
    letterhead.draw_header(canvas, doc)
    letterhead.draw_footer(canvas, doc)


# ── public API ────────────────────────────────────────────────────────────────

def render_stocks_expiry_pdf(data: dict) -> bytes:
    """Render the Stock Expiry report as a landscape A4 PDF.

    Parameters
    ----------
    data : {'rows': [...], 'brand_filter': str|None, 'cutoff_date': 'YYYY-MM-DD'|None}

    Returns
    -------
    bytes : raw PDF content
    """
    rows = data.get('rows') or []
    brand_filter = data.get('brand_filter')
    cutoff_date = data.get('cutoff_date')

    buffer = BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=_MARGIN,
        rightMargin=_MARGIN,
        topMargin=letterhead.HEADER_TOP_PAD + letterhead.HEADER_HEIGHT + 0.3 * cm,
        bottomMargin=1.4 * cm,
        title='IAL Stock Expiry',
        author='IRAVI AGRO LIFE LLP',
    )

    _BASE, _BOLD = letterhead.BASE_FONT, letterhead.BOLD_FONT
    _W = colors.white

    title_sty = _ps('SEPTitle', _BOLD, 12, TA_LEFT, color=letterhead.GREEN)
    right_sty = _ps('SEPRight', _BASE, 8, TA_RIGHT, color=letterhead.MUTED)
    subtitle_sty = _ps('SEPSubtitle', _BASE, 8, TA_LEFT, color=letterhead.MUTED)

    hdr_sty = {
        TA_LEFT: _ps('SEPHdrL', _BOLD, 7, TA_LEFT, color=_W),
        TA_CENTER: _ps('SEPHdrC', _BOLD, 7, TA_CENTER, color=_W),
        TA_RIGHT: _ps('SEPHdrR', _BOLD, 7, TA_RIGHT, color=_W),
    }
    dat_sty = {
        TA_LEFT: _ps('SEPDatL', _BASE, 6.5, TA_LEFT),
        TA_CENTER: _ps('SEPDatC', _BASE, 6.5, TA_CENTER),
        TA_RIGHT: _ps('SEPDatR', _BASE, 6.5, TA_RIGHT),
    }

    # ── Letterhead + report title / subtitle rows ─────────────────────────────
    today_str = _date.today().strftime('%d-%m-%Y')

    title_row = Table(
        [[Paragraph('STOCK EXPIRY REPORT', title_sty), Paragraph(f'Date: {today_str}', right_sty)]],
        colWidths=[_CONTENT_W * 0.75, _CONTENT_W * 0.25],
    )
    title_row.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))

    filter_parts = []
    if brand_filter:
        filter_parts.append(f'Brand: {brand_filter}')
    if cutoff_date:
        filter_parts.append(f'Expiring before {_fmt_date(cutoff_date)}')
    subtitle_text = ' · '.join(filter_parts) if filter_parts else 'All Stock'

    # Header is drawn on the canvas (letterhead.draw_header, every page) — NOT
    # added here as a flowable, to avoid double-rendering it on page 1.
    elements: list = [
        title_row,
        Spacer(1, 2),
        Paragraph(subtitle_text, subtitle_sty),
        Spacer(1, 5),
    ]

    # ── Column widths ─────────────────────────────────────────────────────────
    weight_total = sum(w for _, w, _ in _COLUMNS)
    col_widths = [_CONTENT_W * (w / weight_total) for _, w, _ in _COLUMNS]

    # ── Header row ─────────────────────────────────────────────────────────────
    table_rows: list = [
        [Paragraph(label, hdr_sty[align]) for label, _, align in _COLUMNS]
    ]

    for row in rows:
        table_rows.append([
            Paragraph(row['brand'] or '-', dat_sty[TA_LEFT]),
            Paragraph(row['technical'] or '-', dat_sty[TA_LEFT]),
            Paragraph(row['packing_display'] or '-', dat_sty[TA_CENTER]),
            Paragraph(row['branch'] or '-', dat_sty[TA_LEFT]),
            Paragraph(row['special_packing_mention'] or '-', dat_sty[TA_LEFT]),
            Paragraph(_fmt_num(row['available_nos']), dat_sty[TA_RIGHT]),
            Paragraph(_fmt_num(row['conversion_factor']), dat_sty[TA_RIGHT]),
            Paragraph(_fmt_num(row['available_cases']), dat_sty[TA_RIGHT]),
            Paragraph(_fmt_num(row['available_qty']), dat_sty[TA_RIGHT]),
            Paragraph(_fmt_date(row['entry_date']), dat_sty[TA_CENTER]),
            Paragraph(_fmt_date(row['expiry_date']), dat_sty[TA_CENTER]),
        ])

    tbl_cmds: list = [
        ('BACKGROUND', (0, 0), (-1, 0), letterhead.GREEN),
        ('FONTSIZE', (0, 0), (-1, -1), 6.5),
        ('GRID', (0, 0), (-1, -1), 0.3, _CELL_BORDER),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ('LEFTPADDING', (0, 0), (-1, -1), 3),
        ('RIGHTPADDING', (0, 0), (-1, -1), 3),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]
    # Zebra stripe on alternate data rows (row 0 is the header)
    for i in range(1, len(table_rows)):
        if (i - 1) % 2 == 1:
            tbl_cmds.append(('BACKGROUND', (0, i), (-1, i), _ALT_BG))

    data_tbl = Table(table_rows, colWidths=col_widths, repeatRows=1)
    data_tbl.setStyle(TableStyle(tbl_cmds))
    elements.append(data_tbl)

    doc.build(elements, onFirstPage=_draw_header_footer, onLaterPages=_draw_header_footer)
    return buffer.getvalue()
