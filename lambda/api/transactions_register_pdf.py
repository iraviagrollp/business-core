"""
transactions_register_pdf — shared landscape A4 renderer for the Sales
Register (GET /sales/pdf) and Purchases Register (GET /purchases/pdf)
reports.

The two reports are structurally identical (same 9-column table, same TOTAL
row, same title/meta-line/letterhead conventions) — only the title text, the
set of active filters, and the source table differ, and all of that is
already resolved by the caller (handler.py's _handle_sales_pdf /
_handle_purchases_pdf) before this module is ever invoked. This module is
therefore purely presentational: it takes an already-filtered, already-
ordered row list plus a pre-built title/meta-lines/totals payload and lays
it out — no filtering, sorting, or business logic here.

Public surface
--------------
render_register_pdf(payload: dict) -> bytes
    payload : {
        'title':      'Sales Register' | 'Purchases Register',
        'meta_lines': [str, ...],   # pre-built, one Paragraph line each
        'rows': [
            {
                'date': 'YYYY-MM-DD', 'voucher_no': str, 'party': str,
                'product': str, 'qty': float|None, 'rate': float|None,
                'amount': float|None, 'type_label': 'Sale'|'Return',
                'branch': str,
            }, ...
        ],
        'totals': {'qty': float, 'amount': float},
    }
    returns : raw PDF bytes (landscape A4)

Reused by
---------
  sales_register_pdf.py::render_sales_register_pdf(data)
  purchases_register_pdf.py::render_purchases_register_pdf(data)

Design mirrors the house report-PDF convention (customer_balances_fy_pdf.py /
stocks_expiry_pdf.py / aging_pdf.py): landscape A4, 1cm margins, shared
letterhead header/footer repeating on every page, single-row GREEN header
band with white bold text, repeatRows=1, zebra data rows, TOTAL row
background #f0f0f0 bold. Helvetica/Helvetica-Bold body font; DejaVuSans
registered only for the inline rupee-glyph token (`_RS`).

Columns (exact order + alignment, per the task spec):
  Date | Voucher No | Party | Product | Qty (right) | Rate (Rs) (right) |
  Amount (Rs) (right) | Type (center) | Branch
Blank/None numerics render as a hyphen '-' (not an em-dash — see
aging_pdf.py's docstring note on the same house convention).
"""

from __future__ import annotations

from datetime import datetime
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
_TOTAL_BG    = colors.HexColor('#f0f0f0')
_ALT_BG      = colors.HexColor('#fafafa')
_CELL_BORDER = colors.HexColor('#cccccc')

_PAGE_W, _PAGE_H = landscape(A4)
_MARGIN = 1.0 * cm
_CONTENT_W = _PAGE_W - 2 * _MARGIN

# Rupee token — Helvetica-primary; DejaVuSans is registered only for this glyph.
_RS = letterhead.register_fonts()


# ── formatting helpers ────────────────────────────────────────────────────────

def _fmt_inr(value: float) -> str:
    """Format |value| as Indian-grouped rupees, e.g. '<font ...>₹</font>1,23,456.00'."""
    formatted = f'{abs(value):.2f}'
    int_str, dec_str = formatted.split('.')
    s = int_str
    if len(s) <= 3:
        groups = [s]
    else:
        groups = [s[-3:]]
        s = s[:-3]
        while s:
            groups.insert(0, s[-2:])
            s = s[:-2]
    sign = '-' if value < 0 else ''
    return sign + _RS + ','.join(groups) + '.' + dec_str


def _amt(value) -> str:
    """Rupee string for a non-None value (including 0), else a hyphen placeholder."""
    if value is None:
        return '-'
    return _fmt_inr(value)


def _fmt_num(value) -> str:
    """Trim to an int display when whole, else 2 dp; None -> '-'."""
    if value is None:
        return '-'
    v = float(value)
    return str(int(v)) if v == int(v) else f'{v:.2f}'


def _fmt_date(date_str) -> str:
    """'YYYY-MM-DD' -> 'DD-MM-YYYY'; None/blank -> '-'."""
    if not date_str:
        return '-'
    try:
        return datetime.strptime(date_str, '%Y-%m-%d').strftime('%d-%m-%Y')
    except ValueError:
        return date_str


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


_COLUMNS = [
    ('Date', 0.75, TA_LEFT),
    ('Voucher No', 1.0, TA_LEFT),
    ('Party', 1.8, TA_LEFT),
    ('Product', 1.9, TA_LEFT),
    ('Qty', 0.65, TA_RIGHT),
    (f'Rate ({_RS})', 0.85, TA_RIGHT),
    (f'Amount ({_RS})', 0.95, TA_RIGHT),
    ('Type', 0.6, TA_CENTER),
    ('Branch', 1.0, TA_LEFT),
]


# ── public API ────────────────────────────────────────────────────────────────

def render_register_pdf(payload: dict) -> bytes:
    title = payload['title']
    meta_lines = payload.get('meta_lines') or []
    rows = payload.get('rows') or []
    totals = payload.get('totals') or {'qty': 0.0, 'amount': 0.0}

    buffer = BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=_MARGIN,
        rightMargin=_MARGIN,
        topMargin=letterhead.HEADER_TOP_PAD + letterhead.HEADER_HEIGHT + 0.3 * cm,
        bottomMargin=1.4 * cm,
        title=f'IAL {title}',
        author='IRAVI AGRO LIFE LLP',
    )

    _BASE, _BOLD = letterhead.BASE_FONT, letterhead.BOLD_FONT
    _W = colors.white

    title_sty = _ps('TRTitle', _BOLD, 12, TA_LEFT, color=letterhead.GREEN)
    meta_sty = _ps('TRMeta', _BASE, 8, TA_LEFT, color=letterhead.MUTED)

    hdr_sty = {
        TA_LEFT: _ps('TRHdrL', _BOLD, 6.5, TA_LEFT, color=_W),
        TA_CENTER: _ps('TRHdrC', _BOLD, 6.5, TA_CENTER, color=_W),
        TA_RIGHT: _ps('TRHdrR', _BOLD, 6.5, TA_RIGHT, color=_W),
    }
    dat_sty = {
        TA_LEFT: _ps('TRDatL', _BASE, 6.5, TA_LEFT),
        TA_CENTER: _ps('TRDatC', _BASE, 6.5, TA_CENTER),
        TA_RIGHT: _ps('TRDatR', _BASE, 6.5, TA_RIGHT),
    }
    tot_sty = {
        TA_LEFT: _ps('TRTotL', _BOLD, 6.5, TA_LEFT),
        TA_CENTER: _ps('TRTotC', _BOLD, 6.5, TA_CENTER),
        TA_RIGHT: _ps('TRTotR', _BOLD, 6.5, TA_RIGHT),
    }

    # Header is drawn on the canvas (letterhead.draw_header, every page) — NOT
    # added here as a flowable, to avoid double-rendering it on page 1.
    elements: list = [Paragraph(title.upper(), title_sty), Spacer(1, 3)]
    for line in meta_lines:
        elements.append(Paragraph(line, meta_sty))
    elements.append(Spacer(1, 6))

    # ── Column widths ─────────────────────────────────────────────────────────
    weight_total = sum(w for _, w, _ in _COLUMNS)
    col_widths = [_CONTENT_W * (w / weight_total) for _, w, _ in _COLUMNS]

    table_rows: list = [
        [Paragraph(label, hdr_sty[align]) for label, _, align in _COLUMNS]
    ]

    for row in rows:
        table_rows.append([
            Paragraph(_fmt_date(row.get('date')), dat_sty[TA_LEFT]),
            Paragraph(row.get('voucher_no') or '-', dat_sty[TA_LEFT]),
            Paragraph(row.get('party') or '-', dat_sty[TA_LEFT]),
            Paragraph(row.get('product') or '-', dat_sty[TA_LEFT]),
            Paragraph(_fmt_num(row.get('qty')), dat_sty[TA_RIGHT]),
            Paragraph(_amt(row.get('rate')), dat_sty[TA_RIGHT]),
            Paragraph(_amt(row.get('amount')), dat_sty[TA_RIGHT]),
            Paragraph(row.get('type_label') or '-', dat_sty[TA_CENTER]),
            Paragraph(row.get('branch') or '-', dat_sty[TA_LEFT]),
        ])

    total_row: list = [
        Paragraph('TOTAL', tot_sty[TA_LEFT]),
        Paragraph('', tot_sty[TA_LEFT]),
        Paragraph('', tot_sty[TA_LEFT]),
        Paragraph('', tot_sty[TA_LEFT]),
        Paragraph(_fmt_num(totals.get('qty')), tot_sty[TA_RIGHT]),
        Paragraph('', tot_sty[TA_RIGHT]),
        Paragraph(_amt(totals.get('amount')), tot_sty[TA_RIGHT]),
        Paragraph('', tot_sty[TA_CENTER]),
        Paragraph('', tot_sty[TA_LEFT]),
    ]
    table_rows.append(total_row)
    total_row_idx = len(table_rows) - 1

    tbl_cmds: list = [
        ('BACKGROUND', (0, 0), (-1, 0), letterhead.GREEN),
        ('BACKGROUND', (0, total_row_idx), (-1, total_row_idx), _TOTAL_BG),
        ('FONTSIZE', (0, 0), (-1, -1), 6.5),
        ('GRID', (0, 0), (-1, -1), 0.3, _CELL_BORDER),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ('LEFTPADDING', (0, 0), (-1, -1), 3),
        ('RIGHTPADDING', (0, 0), (-1, -1), 3),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]
    # Zebra stripe on alternate data rows (row 0 is the header)
    for i in range(1, total_row_idx):
        if (i - 1) % 2 == 1:
            tbl_cmds.append(('BACKGROUND', (0, i), (-1, i), _ALT_BG))

    data_tbl = Table(table_rows, colWidths=col_widths, repeatRows=1)
    data_tbl.setStyle(TableStyle(tbl_cmds))
    elements.append(data_tbl)

    doc.build(elements, onFirstPage=_draw_header_footer, onLaterPages=_draw_header_footer)
    return buffer.getvalue()
