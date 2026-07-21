"""
supplier_ledger_statement_pdf — PDF renderer for the Supplier Ledger Statement report.

Public surface
--------------
render_supplier_ledger_statement_pdf(data: dict) -> bytes
    data    : dict returned by supplier_ledger_statement.compute_supplier_ledger_statement()
    returns : raw PDF bytes (A4 portrait)

Design
------
Exact mirror of ledger_statement_pdf.py (independent, fully self-contained
file — matches this package's existing convention of NOT cross-importing
between the paired customer/supplier report renderers; see
customer_balances_fy_pdf.py / supplier_balances_fy_pdf.py), with the
supplier-specific Dr/Cr color SWAP noted in the repo docs:
  Customer: Dr (receivable) -> RED, Cr (credit/advance) -> GREEN
  Supplier: Dr (payable)    -> GREEN, Cr (advance/overpayment) -> RED
Everything else — portrait A4, 1 cm margins, shared letterhead, one-row
repeating header, opening-balance row, running-balance data rows, TOTAL /
closing row, zebra striping, ₹ inline-font token, hyphen placeholders — is
identical to ledger_statement_pdf.py. See that module's docstring for the
full layout/₹-handling rationale.
"""

from __future__ import annotations

from datetime import date as _date, datetime as _datetime
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
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
_TOTAL_BG    = colors.HexColor('#f0f0f0')   # light grey TOTAL/closing row
_OPEN_BG     = colors.HexColor('#f0f0f0')   # light grey opening-balance row
_ALT_BG      = colors.HexColor('#fafafa')   # subtle zebra stripe
_CELL_BORDER = colors.HexColor('#cccccc')

_RED   = colors.HexColor('#cc0000')   # Cr balance — supplier advance/overpayment
_GREEN = colors.HexColor('#1a6e35')   # Dr balance — supplier payable (normal)

_PAGE_W, _PAGE_H = A4                      # 595.27 x 841.89 pt (portrait)
_MARGIN = 1.0 * cm
_CONTENT_W = _PAGE_W - 2 * _MARGIN         # ~538 pt usable width

_TITLE = 'SUPPLIER LEDGER STATEMENT'

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
    return _RS + ','.join(groups) + '.' + dec_str


def _amt(value: float) -> str:
    """Return Indian-grouped rupee string for non-zero value, else a hyphen."""
    return _fmt_inr(value) if value > 0 else '-'


def _bal(balance: float) -> str:
    """Return balance with Dr/Cr suffix or a hyphen for zero."""
    if balance > 0:
        return f'{_fmt_inr(balance)} Dr'
    if balance < 0:
        return f'{_fmt_inr(abs(balance))} Cr'
    return '-'


def _fmt_date(iso_date: str) -> str:
    """'YYYY-MM-DD' -> 'DD-MM-YYYY'; passes through unparseable values unchanged."""
    try:
        return _datetime.strptime(iso_date, '%Y-%m-%d').strftime('%d-%m-%Y')
    except (ValueError, TypeError):
        return iso_date or '-'


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


# ── public API ────────────────────────────────────────────────────────────────

def render_supplier_ledger_statement_pdf(data: dict) -> bytes:
    """Render the Supplier Ledger Statement report as a portrait A4 PDF.

    Parameters
    ----------
    data : dict returned by
        supplier_ledger_statement.compute_supplier_ledger_statement()

    Returns
    -------
    bytes : raw PDF content
    """
    title = _TITLE
    # SWAPPED vs the customer statement: Dr -> GREEN (payable), Cr -> RED (advance).
    dr_color, cr_color = _GREEN, _RED
    account_name    = data['account_name']
    from_date       = data['from_date']
    to_date         = data['to_date']
    opening_balance = data['opening_balance']
    rows            = data['rows']
    total_debit     = data['total_debit']
    total_credit    = data['total_credit']
    closing_balance = data['closing_balance']

    buffer = BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=_MARGIN,
        rightMargin=_MARGIN,
        topMargin=0.6 * cm,
        bottomMargin=1.4 * cm,   # footer draws at 0.46-0.95 cm; 1.4 cm clears it
        title=f'IAL {title.title()}',
        author='IRAVI AGRO LIFE LLP',
    )

    # ── Paragraph styles ──────────────────────────────────────────────────────
    _W = colors.white
    _BASE, _BOLD = letterhead.BASE_FONT, letterhead.BOLD_FONT

    title_sty   = _ps('SLSTitle', _BOLD, 12, TA_LEFT,  color=letterhead.GREEN)
    right_sty   = _ps('SLSRight', _BASE, 8,  TA_RIGHT, color=letterhead.MUTED)
    right_bold  = _ps('SLSRightBold', _BOLD, 9, TA_RIGHT, color=letterhead.BODY)

    hdr_c = _ps('SLSHdrC', _BOLD, 8, TA_CENTER, color=_W)
    hdr_l = _ps('SLSHdrL', _BOLD, 8, TA_LEFT,   color=_W)
    hdr_r = _ps('SLSHdrR', _BOLD, 8, TA_RIGHT,  color=_W)

    dat_l = _ps('SLSDatL', _BASE, 8, TA_LEFT)
    dat_c = _ps('SLSDatC', _BASE, 8, TA_CENTER)
    dat_r = _ps('SLSDatR', _BASE, 8, TA_RIGHT)

    # Color-specific data-row styles for the Balance column (Dr / Cr)
    dat_r_dr = _ps('SLSDatRDr', _BASE, 8, TA_RIGHT, color=dr_color)
    dat_r_cr = _ps('SLSDatRCr', _BASE, 8, TA_RIGHT, color=cr_color)

    tot_l = _ps('SLSTotL', _BOLD, 8, TA_LEFT)
    tot_c = _ps('SLSTotC', _BOLD, 8, TA_CENTER)
    tot_r = _ps('SLSTotR', _BOLD, 8, TA_RIGHT)

    tot_r_dr = _ps('SLSTotRDr', _BOLD, 8, TA_RIGHT, color=dr_color)
    tot_r_cr = _ps('SLSTotRCr', _BOLD, 8, TA_RIGHT, color=cr_color)

    open_l = _ps('SLSOpenL', _BOLD, 8, TA_LEFT)
    open_r_dr = _ps('SLSOpenRDr', _BOLD, 8, TA_RIGHT, color=dr_color)
    open_r_cr = _ps('SLSOpenRCr', _BOLD, 8, TA_RIGHT, color=cr_color)
    open_r    = _ps('SLSOpenR', _BOLD, 8, TA_RIGHT)

    # ── Letterhead + report title row ─────────────────────────────────────────
    today_str = _date.today().strftime('%d-%m-%Y')
    period_str = f'{_fmt_date(from_date)} to {_fmt_date(to_date)}'

    title_row = Table(
        [[Paragraph(title, title_sty),
          Paragraph(f'Date: {today_str}', right_sty)]],
        colWidths=[_CONTENT_W * 0.6, _CONTENT_W * 0.4],
    )
    title_row.setStyle(TableStyle([
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING',   (0, 0), (-1, -1), 0),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 0),
        ('TOPPADDING',    (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))

    subtitle_row = Table(
        [[Paragraph(f'Account: {account_name}', right_bold),
          Paragraph(f'Statement Period: {period_str}', right_sty)]],
        colWidths=[_CONTENT_W * 0.6, _CONTENT_W * 0.4],
    )
    subtitle_row.setStyle(TableStyle([
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING',   (0, 0), (-1, -1), 0),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 0),
        ('TOPPADDING',    (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))

    elements: list = list(letterhead.build_header(_CONTENT_W)) + [
        title_row, subtitle_row, Spacer(1, 6),
    ]

    # ── Column widths ─────────────────────────────────────────────────────────
    date_w, voucher_w, type_w = 65.0, 105.0, 125.0
    remaining = _CONTENT_W - (date_w + voucher_w + type_w)
    amt_w = remaining / 3
    col_widths = [date_w, voucher_w, type_w, amt_w, amt_w, amt_w]

    # ── Header row ─────────────────────────────────────────────────────────────
    header_row = [
        Paragraph('Date', hdr_l),
        Paragraph('Voucher No', hdr_l),
        Paragraph('Type', hdr_l),
        Paragraph(f'Debit ({_RS})', hdr_r),
        Paragraph(f'Credit ({_RS})', hdr_r),
        Paragraph(f'Balance ({_RS})', hdr_r),
    ]

    table_rows: list = [header_row]

    # Opening balance row
    if opening_balance > 0:
        open_bal_para = Paragraph(_bal(opening_balance), open_r_dr)
    elif opening_balance < 0:
        open_bal_para = Paragraph(_bal(opening_balance), open_r_cr)
    else:
        open_bal_para = Paragraph(_bal(opening_balance), open_r)
    table_rows.append([
        Paragraph('', open_l),
        Paragraph('', open_l),
        Paragraph('Opening Balance', open_l),
        Paragraph('-', open_r),
        Paragraph('-', open_r),
        open_bal_para,
    ])
    opening_row_idx = len(table_rows) - 1

    # ── Data rows with running balance ────────────────────────────────────────
    running = opening_balance
    color_cmds: list = []
    for idx, row in enumerate(rows):
        debit = row['debit']
        credit = row['credit']
        running = round(running + debit - credit, 2)
        tbl_row = len(table_rows)
        if running > 0:
            bal_para = Paragraph(_bal(running), dat_r_dr)
            color_cmds.append(('TEXTCOLOR', (5, tbl_row), (5, tbl_row), dr_color))
        elif running < 0:
            bal_para = Paragraph(_bal(running), dat_r_cr)
            color_cmds.append(('TEXTCOLOR', (5, tbl_row), (5, tbl_row), cr_color))
        else:
            bal_para = Paragraph(_bal(running), dat_r)
        table_rows.append([
            Paragraph(_fmt_date(row['transaction_date']), dat_l),
            Paragraph(row['voucher_no'] or '-', dat_l),
            Paragraph(row['transaction_type'] or '-', dat_l),
            Paragraph(_amt(debit), dat_r),
            Paragraph(_amt(credit), dat_r),
            bal_para,
        ])

    # ── TOTAL / closing row ────────────────────────────────────────────────────
    if closing_balance > 0:
        close_para = Paragraph(_bal(closing_balance), tot_r_dr)
    elif closing_balance < 0:
        close_para = Paragraph(_bal(closing_balance), tot_r_cr)
    else:
        close_para = Paragraph(_bal(closing_balance), tot_r)
    table_rows.append([
        Paragraph('', tot_c),
        Paragraph('', tot_c),
        Paragraph('TOTAL / Closing Balance', tot_l),
        Paragraph(_amt(total_debit), tot_r),
        Paragraph(_amt(total_credit), tot_r),
        close_para,
    ])
    total_row_idx = len(table_rows) - 1

    if closing_balance > 0:
        color_cmds.append(('TEXTCOLOR', (5, total_row_idx), (5, total_row_idx), dr_color))
    elif closing_balance < 0:
        color_cmds.append(('TEXTCOLOR', (5, total_row_idx), (5, total_row_idx), cr_color))

    # ── Table style ───────────────────────────────────────────────────────────
    tbl_cmds: list = [
        ('BACKGROUND', (0, 0), (-1, 0), letterhead.GREEN),
        ('BACKGROUND', (0, opening_row_idx), (-1, opening_row_idx), _OPEN_BG),
        ('BACKGROUND', (0, total_row_idx), (-1, total_row_idx), _TOTAL_BG),
        ('FONTSIZE',      (0, 0), (-1, -1), 8),
        ('GRID',          (0, 0), (-1, -1), 0.3, _CELL_BORDER),
        ('TOPPADDING',    (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('LEFTPADDING',   (0, 0), (-1, -1), 3),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 3),
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ('SPAN', (0, opening_row_idx), (1, opening_row_idx)),
    ] + color_cmds

    # Zebra stripe on alternate data rows (between opening row and total row)
    for i in range(opening_row_idx + 1, total_row_idx):
        if (i - opening_row_idx - 1) % 2 == 1:
            tbl_cmds.append(('BACKGROUND', (0, i), (-1, i), _ALT_BG))

    data_tbl = Table(table_rows, colWidths=col_widths, repeatRows=1)
    data_tbl.setStyle(TableStyle(tbl_cmds))
    elements.append(data_tbl)

    # ── Build PDF with footer on every page ───────────────────────────────────
    doc.build(elements, onFirstPage=letterhead.draw_footer, onLaterPages=letterhead.draw_footer)
    return buffer.getvalue()
