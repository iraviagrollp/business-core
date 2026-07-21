"""
ledger_statement_pdf — PDF renderer for the Customer Ledger Statement report.

Public surface
--------------
render_ledger_statement_pdf(data: dict) -> bytes
    data    : dict returned by ledger_statement.compute_ledger_statement()
    returns : raw PDF bytes (A4 portrait)

Design (reworked 2026-07-21 to match the client-approved "account statement" layout)
-------------------------------------------------------------------------------------
- Shared letterhead (letterhead.build_header) at the top, letterhead.draw_footer on
  every page. Portrait A4, 1 cm margins.
- Title: centered, bold, letterhead.GREEN — '{ACCOUNT NAME} ACCOUNT STATEMENT'.
- Location + Statement Date row directly under the title: 'Location: {city}' (left,
  bold; '-' when no city on file) / 'Statement Date: {DD-MM-YYYY}' (right, muted).
- Statement Period line (centered): full Indian-FY boundaries ('FY DD-MM-YYYY to
  DD-MM-YYYY') when from_date/to_date fall in the same FY (Apr 1 -> Mar 31), else
  'DD-MM-YYYY to DD-MM-YYYY' snapping only the start to that FY's April 1.
- The statement is split into one table PER FINANCIAL YEAR (ascending), each with a
  small bold FY heading ('FY 2025-26  (01-04-2025 to 31-03-2026)'), a one-row
  repeating green header band (repeatRows=1: Date | Voucher No | Type | Debit (Rs) |
  Credit (Rs) | Balance (Rs)), a synthetic first row ('Opening Balance' for the
  first FY shown, 'Brought Forward' for every later FY), one row per transaction,
  and a bold light-grey 'Totals' row (that FY's debit/credit sums + closing
  balance). The running balance carries across FY boundaries. Each FY section
  (heading + table) is wrapped in KeepTogether so it is never split across a page
  boundary unless the table itself is taller than a full page (repeatRows keeps the
  header visible on any such continuation).
- Balance column is ALWAYS rendered in plain black (no Dr=red/Cr=green coloring) —
  the '_bal()' text keeps its 'Dr'/'Cr' suffix ('-' for zero).
- After the LAST FY table only: a bordered 'Bank Particulars for Payment' box with
  IAL's hardcoded account details, followed by a muted italic disclaimer line.

₹ / em-dash handling
---------------------
- All ₹ amounts route through `_RS` (letterhead.register_fonts()'s inline
  `<font name="DejaVuSans">₹</font>` markup token), never a bare '₹' char,
  and are always rendered via reportlab Paragraph so the markup is
  interpreted (Helvetica/WinAnsiEncoding cannot encode U+20B9 directly).
- Any placeholder dash uses a plain hyphen '-' (not an em-dash) throughout.
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
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

import letterhead

# ── constants ─────────────────────────────────────────────────────────────────
_TOTAL_BG    = colors.HexColor('#f0f0f0')   # light grey Totals row
_OPEN_BG     = colors.HexColor('#f0f0f0')   # light grey opening/brought-forward row
_ALT_BG      = colors.HexColor('#fafafa')   # subtle zebra stripe
_CELL_BORDER = colors.HexColor('#cccccc')

# Kept defined (per spec) but no longer applied to any balance text — the
# Balance column is always rendered in plain black now.
_RED   = colors.HexColor('#cc0000')
_GREEN = colors.HexColor('#1a6e35')

_PAGE_W, _PAGE_H = A4                      # 595.27 x 841.89 pt (portrait)
_MARGIN = 1.0 * cm
_CONTENT_W = _PAGE_W - 2 * _MARGIN         # ~538 pt usable width

# Hardcoded IAL bank particulars (client's approved design) — mirrors how
# letterhead.py hardcodes GSTIN/LLPIN/etc.
_BANK_ACCOUNT_NAME = 'IRAVI AGRO LIFE LLP'
_BANK_ACCOUNT_NO   = '925020021374991'
_BANK_NAME         = 'Axis Bank, Moti Nagar, Hyderabad'
_BANK_IFSC         = 'UTIB0001922'

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


def _parse_iso_date(iso_date: str) -> _date:
    return _datetime.strptime(iso_date, '%Y-%m-%d').date()


def _fmt_ddmmyyyy(d: _date) -> str:
    return d.strftime('%d-%m-%Y')


def _fy_start_year(d: _date) -> int:
    """Indian FY (Apr 1 -> Mar 31) start year containing date `d`."""
    return d.year if d.month >= 4 else d.year - 1


def _fy_bounds(start_year: int) -> tuple[_date, _date]:
    return _date(start_year, 4, 1), _date(start_year + 1, 3, 31)


def _fy_label(start_year: int) -> str:
    return f'FY {start_year}-{str(start_year + 1)[-2:]}'


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

def render_ledger_statement_pdf(data: dict) -> bytes:
    """Render the Customer Ledger Statement report as a portrait A4 PDF.

    Parameters
    ----------
    data : dict returned by ledger_statement.compute_ledger_statement()

    Returns
    -------
    bytes : raw PDF content
    """
    account_name    = data['account_name']
    from_date       = data['from_date']
    to_date         = data['to_date']
    opening_balance = data['opening_balance']
    rows            = data['rows']
    city            = data.get('city')

    buffer = BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=_MARGIN,
        rightMargin=_MARGIN,
        topMargin=0.6 * cm,
        bottomMargin=1.4 * cm,   # footer draws at 0.46-0.95 cm; 1.4 cm clears it
        title='IAL Customer Ledger Statement',
        author='IRAVI AGRO LIFE LLP',
    )

    # ── Paragraph styles ──────────────────────────────────────────────────────
    _W = colors.white
    _BASE, _BOLD = letterhead.BASE_FONT, letterhead.BOLD_FONT

    title_sty     = _ps('LSTitle', _BOLD, 13, TA_CENTER, color=letterhead.GREEN)
    loc_sty       = _ps('LSLoc', _BOLD, 9, TA_LEFT, color=letterhead.BODY)
    date_sty      = _ps('LSDate', _BASE, 8, TA_RIGHT, color=letterhead.MUTED)
    period_sty    = _ps('LSPeriod', _BOLD, 9, TA_CENTER, color=letterhead.BODY)
    fy_head_sty   = _ps('LSFyHead', _BOLD, 10, TA_LEFT, color=letterhead.GREEN)

    hdr_l = _ps('LSHdrL', _BOLD, 8, TA_LEFT,   color=_W)
    hdr_r = _ps('LSHdrR', _BOLD, 8, TA_RIGHT,  color=_W)

    dat_l = _ps('LSDatL', _BASE, 8, TA_LEFT)
    dat_r = _ps('LSDatR', _BASE, 8, TA_RIGHT)

    tot_l = _ps('LSTotL', _BOLD, 8, TA_LEFT)
    tot_r = _ps('LSTotR', _BOLD, 8, TA_RIGHT)

    open_l = _ps('LSOpenL', _BOLD, 8, TA_LEFT)
    open_r = _ps('LSOpenR', _BOLD, 8, TA_RIGHT)

    bank_label_sty = _ps('LSBankLabel', _BOLD, 8.5, TA_LEFT, color=letterhead.BODY)
    bank_value_sty = _ps('LSBankValue', _BASE, 8.5, TA_LEFT, color=letterhead.BODY)
    bank_head_sty  = _ps('LSBankHead', _BOLD, 10, TA_LEFT, color=letterhead.GREEN)
    disclaimer_sty = _ps('LSDisclaimer', _BASE, 8, TA_LEFT, color=letterhead.MUTED)

    # ── Letterhead + title block ──────────────────────────────────────────────
    today_str = _date.today().strftime('%d-%m-%Y')

    elements: list = list(letterhead.build_header(_CONTENT_W)) + [
        Paragraph(f'{account_name.upper()} ACCOUNT STATEMENT', title_sty),
        Spacer(1, 5),
    ]

    loc_date_row = Table(
        [[Paragraph(f'Location: {city or "-"}', loc_sty),
          Paragraph(f'Statement Date: {today_str}', date_sty)]],
        colWidths=[_CONTENT_W * 0.6, _CONTENT_W * 0.4],
    )
    loc_date_row.setStyle(TableStyle([
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING',   (0, 0), (-1, -1), 0),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 0),
        ('TOPPADDING',    (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))
    elements.append(loc_date_row)
    elements.append(Spacer(1, 4))

    # ── Statement Period line ─────────────────────────────────────────────────
    from_d = _parse_iso_date(from_date)
    to_d   = _parse_iso_date(to_date)
    from_fy_start = _fy_start_year(from_d)
    to_fy_start   = _fy_start_year(to_d)

    if from_fy_start == to_fy_start:
        fy_start_date, fy_end_date = _fy_bounds(from_fy_start)
        period_text = (
            f'Statement Period: FY {_fmt_ddmmyyyy(fy_start_date)} '
            f'to {_fmt_ddmmyyyy(fy_end_date)}'
        )
    else:
        fy_start_of_from, _ = _fy_bounds(from_fy_start)
        period_text = (
            f'Statement Period: {_fmt_ddmmyyyy(fy_start_of_from)} '
            f'to {_fmt_ddmmyyyy(to_d)}'
        )

    elements.append(Paragraph(period_text, period_sty))
    elements.append(Spacer(1, 8))

    # ── Group rows by FY (ascending) ──────────────────────────────────────────
    fy_groups: dict = {}
    for row in rows:
        fy_start = _fy_start_year(_parse_iso_date(row['transaction_date']))
        fy_groups.setdefault(fy_start, []).append(row)

    if not fy_groups:
        # No transactions in the period — still show one FY table (the period's
        # starting FY) carrying just the opening/closing position.
        fy_groups[from_fy_start] = []

    fy_start_years = sorted(fy_groups.keys())

    # ── Column widths (shared by every FY table) ─────────────────────────────
    date_w, voucher_w, type_w = 65.0, 105.0, 125.0
    remaining = _CONTENT_W - (date_w + voucher_w + type_w)
    amt_w = remaining / 3
    col_widths = [date_w, voucher_w, type_w, amt_w, amt_w, amt_w]

    header_row = [
        Paragraph('Date', hdr_l),
        Paragraph('Voucher No', hdr_l),
        Paragraph('Type', hdr_l),
        Paragraph(f'Debit ({_RS})', hdr_r),
        Paragraph(f'Credit ({_RS})', hdr_r),
        Paragraph(f'Balance ({_RS})', hdr_r),
    ]

    running = opening_balance

    for fy_idx, fy_start in enumerate(fy_start_years):
        fy_rows = fy_groups[fy_start]
        fy_start_date, fy_end_date = _fy_bounds(fy_start)
        fy_opening = running
        opening_label = 'Opening Balance' if fy_idx == 0 else 'Brought Forward'

        table_rows: list = [header_row]

        table_rows.append([
            Paragraph('', open_l),
            Paragraph('', open_l),
            Paragraph(opening_label, open_l),
            Paragraph('-', open_r),
            Paragraph('-', open_r),
            Paragraph(_bal(fy_opening), open_r),
        ])
        opening_row_idx = len(table_rows) - 1

        fy_total_debit = 0.0
        fy_total_credit = 0.0
        for row in fy_rows:
            debit = row['debit']
            credit = row['credit']
            running = round(running + debit - credit, 2)
            fy_total_debit += debit
            fy_total_credit += credit
            table_rows.append([
                Paragraph(_fmt_date(row['transaction_date']), dat_l),
                Paragraph(row['voucher_no'] or '-', dat_l),
                Paragraph(row['transaction_type'] or '-', dat_l),
                Paragraph(_amt(debit), dat_r),
                Paragraph(_amt(credit), dat_r),
                Paragraph(_bal(running), dat_r),
            ])

        table_rows.append([
            Paragraph('', tot_l),
            Paragraph('', tot_l),
            Paragraph('Totals', tot_l),
            Paragraph(_amt(round(fy_total_debit, 2)), tot_r),
            Paragraph(_amt(round(fy_total_credit, 2)), tot_r),
            Paragraph(_bal(running), tot_r),
        ])
        total_row_idx = len(table_rows) - 1

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
            ('SPAN', (0, total_row_idx), (1, total_row_idx)),
        ]

        for i in range(opening_row_idx + 1, total_row_idx):
            if (i - opening_row_idx - 1) % 2 == 1:
                tbl_cmds.append(('BACKGROUND', (0, i), (-1, i), _ALT_BG))

        fy_table = Table(table_rows, colWidths=col_widths, repeatRows=1)
        fy_table.setStyle(TableStyle(tbl_cmds))

        heading_text = (
            f'{_fy_label(fy_start)}  '
            f'({_fmt_ddmmyyyy(fy_start_date)} to {_fmt_ddmmyyyy(fy_end_date)})'
        )
        fy_section = [
            Paragraph(heading_text, fy_head_sty),
            Spacer(1, 3),
            fy_table,
            Spacer(1, 10),
        ]
        elements.append(KeepTogether(fy_section))

    # ── Bank Particulars for Payment (after the LAST FY table only) ──────────
    bank_rows = [
        [Paragraph('Account Name:', bank_label_sty), Paragraph(_BANK_ACCOUNT_NAME, bank_value_sty)],
        [Paragraph('Account No.:',  bank_label_sty), Paragraph(_BANK_ACCOUNT_NO,   bank_value_sty)],
        [Paragraph('Bank:',         bank_label_sty), Paragraph(_BANK_NAME,         bank_value_sty)],
        [Paragraph('IFSC Code:',    bank_label_sty), Paragraph(_BANK_IFSC,         bank_value_sty)],
    ]
    bank_table = Table(bank_rows, colWidths=[110.0, _CONTENT_W - 110.0])
    bank_table.setStyle(TableStyle([
        ('GRID',          (0, 0), (-1, -1), 0.4, _CELL_BORDER),
        ('TOPPADDING',    (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('LEFTPADDING',   (0, 0), (-1, -1), 5),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 5),
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
    ]))

    elements.append(Paragraph('Bank Particulars for Payment', bank_head_sty))
    elements.append(Spacer(1, 4))
    elements.append(bank_table)
    elements.append(Spacer(1, 4))
    elements.append(Paragraph(
        '<i>Should the payment have already been made, kindly disregard this notice.</i>',
        disclaimer_sty,
    ))

    # ── Build PDF with footer on every page ───────────────────────────────────
    doc.build(elements, onFirstPage=letterhead.draw_footer, onLaterPages=letterhead.draw_footer)
    return buffer.getvalue()
