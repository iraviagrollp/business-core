"""
borrowings_pdf — PDF renderer for the Borrowings Statement report.

Public surface
--------------
render_borrowings_pdf(rows, account, from_date, to_date) -> bytes
    rows      : list[dict] returned by borrowings.compute_borrowings_rows()
                (same shape as the GET /borrowings JSON response — this
                renderer never queries the DB itself).
    account   : the (optional) `account` query param, exactly as received —
                '' / None means "all accounts".
    from_date, to_date : 'YYYY-MM-DD' strings.
    returns   : raw PDF bytes (A4 portrait).

Design (mirrors ledger_statement_pdf.py's shared house style — same
letterhead, fonts, ₹ handling, Dr/Cr suffix helper — adapted to the simpler,
non-FY-split, single-table Borrowings report)
-------------------------------------------------------------------------------------
- Shared letterhead: header repeats on every page via letterhead.draw_header,
  footer via letterhead.draw_footer. Portrait A4, 1 cm margins — this report
  is always exactly 6 columns (see below), the same column-count class as
  ledger_statement_pdf.py's 6-column statement table, so portrait was kept
  rather than switching to landscape.
- Title: centered, bold, letterhead.GREEN — 'BORROWINGS STATEMENT' (static —
  unlike the per-account customer/supplier statements, this report can cover
  either one account or ALL of them, so the title never embeds an account
  name; the account is called out in the subtitle instead).
- Subtitle line (centered, one line): 'Period: DD-MM-YYYY to DD-MM-YYYY  |
  Account: <name>' when a single account is selected, or '...  |  Account:
  All accounts' when not — dates formatted DD-MM-YYYY, the same style the
  other report PDFs in this Lambda use.
- Columns: Date | Voucher No | Transaction Name | Debit (Rs) | Credit (Rs)
  + a 6th column that is EITHER Account (when `account` is blank, i.e. "all
  accounts") OR a running Balance (Rs) (when a single account is selected)
  — mirrors the on-screen table's conditional column exactly.
- Running balance (single-account case only) = cumulative CREDIT − DEBIT
  (NOT debit − credit, unlike the receivable-side ledger/supplier
  statements). Domain semantics: borrowings are the firm's liability to its
  investors — credit = money RECEIVED from the investor (increases what the
  firm owes), debit = REPAYMENT (decreases it). A positive running balance
  means the firm owes the investor.
- Dr/Cr suffix: reuses the exact `_bal()` TEXT convention
  supplier_ledger_statement_pdf.py uses (positive -> 'Dr', negative -> 'Cr',
  zero -> '-') — the "payable-inverted" convention referenced in the task
  brief. Because the balance here is computed as credit − debit (the
  opposite operand order from the ledger-table statements' debit − credit),
  a positive/'Dr' balance correctly reads as "the firm owes the investor" (a
  payable/liability) — the same relationship supplier_ledger_statement_pdf.py
  has between its positive/'Dr' balance and its 'Closing Balance Payable'
  banner label.
- Totals row: Total Debit, Total Credit, and Net Outstanding
  (Sum(credit) − Sum(debit)) rendered with the same Dr/Cr suffix, in the
  report's 6th column — whether that column's per-row content is Account or
  Balance, the Totals row always shows the Net Outstanding figure there
  (mirrors how ledger_statement_pdf.py's Totals row always shows the closing
  balance in its last column).
- No per-FY split (unlike ledger_statement_pdf.py/supplier_ledger_statement_
  pdf.py) — the task spec does not call for one and the borrowings dataset
  is not tied to any FY concept; the whole period renders as ONE table with
  a repeating header (repeatRows=1).
- Empty-result case: letterhead + title + subtitle render as normal, but the
  table is replaced by a single centered 'No records for the selected
  period.' line (no empty/zero-row table, no totals row) — handled
  gracefully rather than erroring.

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
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

import letterhead

# ── constants ─────────────────────────────────────────────────────────────────
_TOTAL_BG    = colors.HexColor('#f0f0f0')   # light grey Totals row
_ALT_BG      = colors.HexColor('#fafafa')   # subtle zebra stripe
_CELL_BORDER = colors.HexColor('#cccccc')

_PAGE_W, _PAGE_H = A4                      # 595.27 x 841.89 pt (portrait)
_MARGIN = 1.0 * cm
_CONTENT_W = _PAGE_W - 2 * _MARGIN         # ~538 pt usable width

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
    """Return balance with Dr/Cr suffix or a hyphen for zero — same textual
    convention as supplier_ledger_statement_pdf.py's `_bal()` (positive ->
    'Dr', negative -> 'Cr'); see the module docstring for why this reads
    correctly as "payable" semantics for a credit-minus-debit balance."""
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
    letterhead header (letterhead.draw_header) and the shared footer
    (letterhead.draw_footer) on every page."""
    letterhead.draw_header(canvas, doc)
    letterhead.draw_footer(canvas, doc)


# ── public API ────────────────────────────────────────────────────────────────

def render_borrowings_pdf(rows: list, account: str, from_date: str, to_date: str) -> bytes:
    """Render the Borrowings Statement report as a portrait A4 PDF.

    Parameters
    ----------
    rows : list[dict] returned by borrowings.compute_borrowings_rows()
    account : '' / None for "all accounts", else the single account name
    from_date, to_date : 'YYYY-MM-DD' strings

    Returns
    -------
    bytes : raw PDF content
    """
    account = (account or '').strip()
    single_account = bool(account)

    buffer = BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=_MARGIN,
        rightMargin=_MARGIN,
        # The letterhead header is drawn on the canvas (letterhead.draw_header,
        # via _draw_header_footer below) so it repeats on every page, not just
        # the first. topMargin reserves that band plus a small gap so flowing
        # content (title, table, ...) never overlaps it.
        topMargin=letterhead.HEADER_TOP_PAD + letterhead.HEADER_HEIGHT + 0.3 * cm,
        bottomMargin=1.4 * cm,   # footer draws at 0.46-0.95 cm; 1.4 cm clears it
        title='IAL Borrowings Statement',
        author='IRAVI AGRO LIFE LLP',
    )

    # ── Paragraph styles ──────────────────────────────────────────────────────
    _W = colors.white
    _BASE, _BOLD = letterhead.BASE_FONT, letterhead.BOLD_FONT

    title_sty    = _ps('BRWTitle', _BOLD, 13, TA_CENTER, color=letterhead.GREEN)
    subtitle_sty = _ps('BRWSubtitle', _BOLD, 9, TA_CENTER, color=letterhead.BODY)
    empty_sty    = _ps('BRWEmpty', _BASE, 9.5, TA_CENTER, color=letterhead.MUTED)

    hdr_l = _ps('BRWHdrL', _BOLD, 8, TA_LEFT,  color=_W)
    hdr_r = _ps('BRWHdrR', _BOLD, 8, TA_RIGHT, color=_W)

    dat_l = _ps('BRWDatL', _BASE, 8, TA_LEFT)
    dat_r = _ps('BRWDatR', _BASE, 8, TA_RIGHT)

    tot_l = _ps('BRWTotL', _BOLD, 8, TA_LEFT)
    tot_r = _ps('BRWTotR', _BOLD, 8, TA_RIGHT)

    # ── Letterhead + title block ──────────────────────────────────────────────
    # Header is drawn on the canvas (letterhead.draw_header, every page) —
    # NOT added here as a flowable, to avoid double-rendering it on page 1.
    elements: list = [
        Paragraph('BORROWINGS STATEMENT', title_sty),
        Spacer(1, 5),
    ]

    period_text = f'Period: {_fmt_date(from_date)} to {_fmt_date(to_date)}'
    account_text = f'Account: {account}' if single_account else 'Account: All accounts'
    elements.append(Paragraph(f'{period_text}  |  {account_text}', subtitle_sty))
    elements.append(Spacer(1, 10))

    if not rows:
        elements.append(Paragraph('No records for the selected period.', empty_sty))
        doc.build(elements, onFirstPage=_draw_header_footer, onLaterPages=_draw_header_footer)
        return buffer.getvalue()

    # ── Column widths ─────────────────────────────────────────────────────────
    date_w, voucher_w, name_w, debit_w, credit_w = 60.0, 85.0, 110.0, 75.0, 75.0
    sixth_w = _CONTENT_W - (date_w + voucher_w + name_w + debit_w + credit_w)
    col_widths = [date_w, voucher_w, name_w, debit_w, credit_w, sixth_w]

    sixth_header = 'Account' if not single_account else f'Balance ({_RS})'
    header_row = [
        Paragraph('Date', hdr_l),
        Paragraph('Voucher No', hdr_l),
        Paragraph('Transaction Name', hdr_l),
        Paragraph(f'Debit ({_RS})', hdr_r),
        Paragraph(f'Credit ({_RS})', hdr_r),
        Paragraph(sixth_header, hdr_l if not single_account else hdr_r),
    ]

    table_rows: list = [header_row]

    running = 0.0
    total_debit = 0.0
    total_credit = 0.0
    for row in rows:
        debit = row['debit']
        credit = row['credit']
        total_debit += debit
        total_credit += credit
        # Domain semantics: credit (received) increases what the firm owes;
        # debit (repaid) decreases it — see module docstring.
        running = round(running + credit - debit, 2)

        if single_account:
            sixth_cell = Paragraph(_bal(running), dat_r)
        else:
            sixth_cell = Paragraph(row['account'] or '-', dat_l)

        table_rows.append([
            Paragraph(_fmt_date(row['transaction_date']), dat_l),
            Paragraph(row['voucher_no'] or '-', dat_l),
            Paragraph(row['transaction_name'] or '-', dat_l),
            Paragraph(_amt(debit), dat_r),
            Paragraph(_amt(credit), dat_r),
            sixth_cell,
        ])

    net_outstanding = round(total_credit - total_debit, 2)
    total_row_idx = len(table_rows)
    table_rows.append([
        Paragraph('', tot_l),
        Paragraph('', tot_l),
        Paragraph('Total', tot_l),
        Paragraph(_amt(round(total_debit, 2)), tot_r),
        Paragraph(_amt(round(total_credit, 2)), tot_r),
        Paragraph(_bal(net_outstanding), tot_r),
    ])

    tbl_cmds: list = [
        ('BACKGROUND', (0, 0), (-1, 0), letterhead.GREEN),
        ('BACKGROUND', (0, total_row_idx), (-1, total_row_idx), _TOTAL_BG),
        ('FONTSIZE',      (0, 0), (-1, -1), 8),
        ('GRID',          (0, 0), (-1, -1), 0.3, _CELL_BORDER),
        ('TOPPADDING',    (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('LEFTPADDING',   (0, 0), (-1, -1), 3),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 3),
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ('SPAN', (0, total_row_idx), (2, total_row_idx)),
    ]

    for i in range(1, total_row_idx):
        if (i - 1) % 2 == 1:
            tbl_cmds.append(('BACKGROUND', (0, i), (-1, i), _ALT_BG))

    brw_table = Table(table_rows, colWidths=col_widths, repeatRows=1)
    brw_table.setStyle(TableStyle(tbl_cmds))
    elements.append(brw_table)

    # ── Build PDF with the letterhead header AND footer repeating on every page ─
    doc.build(elements, onFirstPage=_draw_header_footer, onLaterPages=_draw_header_footer)
    return buffer.getvalue()
