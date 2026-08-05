"""
issued_pdc_pdf — PDF renderer for the Issued PDC report (GET /pdc/pdf).

Public surface
--------------
render_issued_pdc_pdf(data: dict) -> bytes
    data    : {
        'rows': [
            {po_no, po_date, company_name, technical_name, brand,
             credit_days, qty, rate, gross, gst, amount, disc, adv, bal,
             pdc_amt, pdc_date}, ...
        ],
        'supplier': str|None, 'product': str|None,
        'pdc_from': 'YYYY-MM-DD'|None, 'pdc_to': 'YYYY-MM-DD'|None,
    } — built by handler._handle_pdc_pdf from procurement.pdc (joined to
    procurement.supplier_companies / procurement.technicals), server-side
    filtered on supplier/product/pdc_from/pdc_to.
    returns : raw PDF bytes (landscape A4)

Design mirrors the house report-PDF convention (customer_balances_fy_pdf.py /
stocks_expiry_pdf.py / aging_pdf.py / transactions_register_pdf.py):
landscape A4, 1cm margins, shared letterhead header/footer repeating on
every page, single-row GREEN header band with white bold text, repeatRows=1,
zebra data rows, TOTAL row background #f0f0f0 bold. Helvetica/Helvetica-Bold
body font; DejaVuSans registered only for the inline rupee-glyph token
(`_RS`).

16 columns (mirroring the procurement PDC screen), a smaller 7pt body font
and Paragraph-wrapped text cells for the long Supplier/Product/Brand columns
so this wide table fits landscape A4 without overflow:
  PO | Date | Supplier | Product | Brand | Cr.Days (right) | Qty (right) |
  Rate (right) | Gross (right) | GST (right) | Amount (right) | Disc (right)
  | Adv (right) | Bal (right) | PDC Amt (right) | PDC Date

Formatting: Date/PDC Date as DD-MM-YYYY; Gross/GST/Amount/Disc/Adv/Bal/PDC
Amt as Rupee-token Indian-grouped 2dp (via `_RS`, matching the procurement
UI's currency columns); Qty/Rate as plain numbers (no currency symbol,
matching the procurement UI). None -> hyphen placeholder '-'.
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
    """Plain number (no currency) — trimmed to an int display when whole, else
    2 dp; None -> '-'. Matches the procurement UI's Qty/Rate columns."""
    if value is None:
        return '-'
    v = float(value)
    return str(int(v)) if v == int(v) else f'{v:.2f}'


def _fmt_int(value) -> str:
    if value is None:
        return '-'
    return str(int(value))


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


# label, weight, alignment, wrap (True -> long-text Paragraph cell)
_COLUMNS = [
    ('PO', 0.85, TA_LEFT, False),
    ('Date', 0.65, TA_CENTER, False),
    ('Supplier', 1.35, TA_LEFT, True),
    ('Product', 1.45, TA_LEFT, True),
    ('Brand', 0.85, TA_LEFT, True),
    ('Cr.Days', 0.55, TA_RIGHT, False),
    ('Qty', 0.55, TA_RIGHT, False),
    (f'Rate', 0.6, TA_RIGHT, False),
    (f'Gross ({_RS})', 0.8, TA_RIGHT, False),
    (f'GST ({_RS})', 0.65, TA_RIGHT, False),
    (f'Amount ({_RS})', 0.85, TA_RIGHT, False),
    (f'Disc ({_RS})', 0.65, TA_RIGHT, False),
    (f'Adv ({_RS})', 0.65, TA_RIGHT, False),
    (f'Bal ({_RS})', 0.7, TA_RIGHT, False),
    (f'PDC Amt ({_RS})', 0.8, TA_RIGHT, False),
    ('PDC Date', 0.65, TA_CENTER, False),
]


# ── public API ────────────────────────────────────────────────────────────────

def render_issued_pdc_pdf(data: dict) -> bytes:
    rows = data.get('rows') or []
    supplier = data.get('supplier')
    product = data.get('product')
    pdc_from = data.get('pdc_from')
    pdc_to = data.get('pdc_to')

    buffer = BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=_MARGIN,
        rightMargin=_MARGIN,
        topMargin=letterhead.HEADER_TOP_PAD + letterhead.HEADER_HEIGHT + 0.3 * cm,
        bottomMargin=1.4 * cm,
        title='IAL Issued PDC',
        author='IRAVI AGRO LIFE LLP',
    )

    _BASE, _BOLD = letterhead.BASE_FONT, letterhead.BOLD_FONT
    _W = colors.white

    title_sty = _ps('PDCTitle', _BOLD, 12, TA_LEFT, color=letterhead.GREEN)
    subtitle_sty = _ps('PDCSubtitle', _BASE, 8, TA_LEFT, color=letterhead.MUTED)

    hdr_align = {
        TA_LEFT: _ps('PDCHdrL', _BOLD, 6.5, TA_LEFT, color=_W),
        TA_CENTER: _ps('PDCHdrC', _BOLD, 6.5, TA_CENTER, color=_W),
        TA_RIGHT: _ps('PDCHdrR', _BOLD, 6.5, TA_RIGHT, color=_W),
    }
    dat_align = {
        TA_LEFT: _ps('PDCDatL', _BASE, 7, TA_LEFT),
        TA_CENTER: _ps('PDCDatC', _BASE, 7, TA_CENTER),
        TA_RIGHT: _ps('PDCDatR', _BASE, 7, TA_RIGHT),
    }
    tot_align = {
        TA_LEFT: _ps('PDCTotL', _BOLD, 7, TA_LEFT),
        TA_CENTER: _ps('PDCTotC', _BOLD, 7, TA_CENTER),
        TA_RIGHT: _ps('PDCTotR', _BOLD, 7, TA_RIGHT),
    }

    title_row = Table(
        [[Paragraph('ISSUED PDC', title_sty)]],
        colWidths=[_CONTENT_W],
    )
    title_row.setStyle(TableStyle([
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))

    filter_parts = []
    if supplier:
        filter_parts.append(f'Supplier: {supplier}')
    if product:
        filter_parts.append(f'Product: {product}')
    if pdc_from or pdc_to:
        from_disp = _fmt_date(pdc_from) if pdc_from else '-'
        to_disp = _fmt_date(pdc_to) if pdc_to else '-'
        filter_parts.append(f'PDC Period: {from_disp} to {to_disp}')
    subtitle_text = ' | '.join(filter_parts) if filter_parts else 'All records'

    # Header is drawn on the canvas (letterhead.draw_header, every page) — NOT
    # added here as a flowable, to avoid double-rendering it on page 1.
    elements: list = [
        title_row,
        Spacer(1, 2),
        Paragraph(subtitle_text, subtitle_sty),
        Spacer(1, 5),
    ]

    # ── Column widths ─────────────────────────────────────────────────────────
    weight_total = sum(w for _, w, _, _ in _COLUMNS)
    col_widths = [_CONTENT_W * (w / weight_total) for _, w, _, _ in _COLUMNS]

    table_rows: list = [
        [Paragraph(label, hdr_align[align]) for label, _, align, _ in _COLUMNS]
    ]

    total_gross = total_gst = total_amount = 0.0
    total_disc = total_adv = total_bal = total_pdc_amt = 0.0

    for row in rows:
        total_gross += row.get('gross') or 0.0
        total_gst += row.get('gst') or 0.0
        total_amount += row.get('amount') or 0.0
        total_disc += row.get('disc') or 0.0
        total_adv += row.get('adv') or 0.0
        total_bal += row.get('bal') or 0.0
        total_pdc_amt += row.get('pdc_amt') or 0.0

        table_rows.append([
            Paragraph(row.get('po_no') or '-', dat_align[TA_LEFT]),
            Paragraph(_fmt_date(row.get('po_date')), dat_align[TA_CENTER]),
            Paragraph(row.get('company_name') or '-', dat_align[TA_LEFT]),
            Paragraph(row.get('technical_name') or '-', dat_align[TA_LEFT]),
            Paragraph(row.get('brand') or '-', dat_align[TA_LEFT]),
            Paragraph(_fmt_int(row.get('credit_days')), dat_align[TA_RIGHT]),
            Paragraph(_fmt_num(row.get('qty')), dat_align[TA_RIGHT]),
            Paragraph(_fmt_num(row.get('rate')), dat_align[TA_RIGHT]),
            Paragraph(_amt(row.get('gross')), dat_align[TA_RIGHT]),
            Paragraph(_amt(row.get('gst')), dat_align[TA_RIGHT]),
            Paragraph(_amt(row.get('amount')), dat_align[TA_RIGHT]),
            Paragraph(_amt(row.get('disc')), dat_align[TA_RIGHT]),
            Paragraph(_amt(row.get('adv')), dat_align[TA_RIGHT]),
            Paragraph(_amt(row.get('bal')), dat_align[TA_RIGHT]),
            Paragraph(_amt(row.get('pdc_amt')), dat_align[TA_RIGHT]),
            Paragraph(_fmt_date(row.get('pdc_date')), dat_align[TA_CENTER]),
        ])

    total_row: list = [
        Paragraph('TOTAL', tot_align[TA_LEFT]),
        Paragraph('', tot_align[TA_CENTER]),
        Paragraph('', tot_align[TA_LEFT]),
        Paragraph('', tot_align[TA_LEFT]),
        Paragraph('', tot_align[TA_LEFT]),
        Paragraph('', tot_align[TA_RIGHT]),
        Paragraph('', tot_align[TA_RIGHT]),
        Paragraph('', tot_align[TA_RIGHT]),
        Paragraph(_amt(total_gross) if rows else '-', tot_align[TA_RIGHT]),
        Paragraph(_amt(total_gst) if rows else '-', tot_align[TA_RIGHT]),
        Paragraph(_amt(total_amount) if rows else '-', tot_align[TA_RIGHT]),
        Paragraph(_amt(total_disc) if rows else '-', tot_align[TA_RIGHT]),
        Paragraph(_amt(total_adv) if rows else '-', tot_align[TA_RIGHT]),
        Paragraph(_amt(total_bal) if rows else '-', tot_align[TA_RIGHT]),
        Paragraph(_amt(total_pdc_amt) if rows else '-', tot_align[TA_RIGHT]),
        Paragraph('', tot_align[TA_CENTER]),
    ]
    table_rows.append(total_row)
    total_row_idx = len(table_rows) - 1

    tbl_cmds: list = [
        ('BACKGROUND', (0, 0), (-1, 0), letterhead.GREEN),
        ('BACKGROUND', (0, total_row_idx), (-1, total_row_idx), _TOTAL_BG),
        ('FONTSIZE', (0, 0), (-1, -1), 7),
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
