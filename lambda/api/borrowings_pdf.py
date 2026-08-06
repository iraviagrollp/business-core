"""
borrowings_pdf — PDF renderer for the Borrowings Statement report.

Public surface
--------------
render_borrowings_pdf(rows, account, from_date, to_date) -> bytes
    Reworked 2026-08-06 (Task 5) — now sectioned into per-FY blocks, mirroring
    render_borrowings_interest_pdf's FY sectioning (bold FY heading, green
    header band, Brought Forward row, light-green/2px-green-bordered
    "Total Principal Amount" summary box). See the "render_borrowings_pdf"
    section comment below the function for full detail.
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
- Reworked 2026-08-06 (Task 5) — per-FY sectioning (see above); the whole
  table is no longer ONE flat table. Empty-result case: letterhead + title +
  subtitle render as normal, but the table is replaced by a single centered
  'No records for the selected period.' line (no empty/zero-row table, no
  totals row) — handled gracefully rather than erroring.

render_borrowings_interest_pdf(rows, missing_rate_accounts, fy_totals, account, from_date, to_date) -> bytes
    include_interest=ON report — FY-sectioned, merged interest rows, FY
    summary boxes. See the module comment directly above the function for
    full detail (added 2026-08-06; `fy_totals` param added the same day —
    see Task 1 in the function's own comment block).

render_borrowings_summary_fy_pdf(data, include_interest) -> bytes
    "All accounts" FY-summary matrix (added 2026-08-06, Task 6) — landscape
    A4, one row per account, FY column groups (Taken/Paid/[Interest]/Closing),
    bold TOTAL row. `data` : dict returned by
    borrowings.compute_borrowings_summary_fy() — the SAME function
    GET /borrowings/summary-fy itself calls, so the screen and this PDF can
    never disagree. See the function's own comment block for full detail.

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
from reportlab.lib.pagesizes import A4, landscape
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
_ALT_BG      = colors.HexColor('#fafafa')   # subtle zebra stripe
_CELL_BORDER = colors.HexColor('#cccccc')

# FY summary box (Task 4 / Task 5, added 2026-08-06): light-green filled box
# with a 2px solid green border — reuses letterhead.GREEN for the border (no
# new brand color invented, per the task brief).
_SUMMARY_BG = colors.HexColor('#e4f0e8')

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


def _fy_start_year(d: _date) -> int:
    """Indian FY (1 April - 31 March) start year containing date `d`."""
    return d.year if d.month >= 4 else d.year - 1


def _fy_bounds(start_year: int) -> tuple:
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


def _draw_header_footer(canvas, doc):
    """Combined onFirstPage/onLaterPages callback — draws the repeating
    letterhead header (letterhead.draw_header) and the shared footer
    (letterhead.draw_footer) on every page."""
    letterhead.draw_header(canvas, doc)
    letterhead.draw_footer(canvas, doc)


# ── public API ────────────────────────────────────────────────────────────────
#
# render_borrowings_pdf — include_interest OFF report. Reworked 2026-08-06
# (Task 5) to mirror render_borrowings_interest_pdf's FY-sectioning: each
# financial year gets its own bold heading + green-banded table; every FY
# after the first opens with a "Brought Forward" row carrying the previous
# FY's closing principal (single-account mode only — see the deviation note
# below); every FY block ends with a light-green / 2px-green-bordered summary
# box (same Task 4 treatment as the interest report). No Interest column, no
# interest rows, no capitalization — pure principal roll-forward
# (closing = opening + credit - debit). Same Task-2 pagination discipline as
# the interest report (heading keepWithNext, main chunk flows naturally, only
# the last _TAIL_ROW_COUNT rows are kept together with the summary box).
#
# "all accounts" mode (`account` blank): mirrors render_borrowings_interest_
# pdf's documented deviation — a per-account closing PRINCIPAL has no
# well-defined merged analogue once accounts are mixed (the 6th column stays
# Account, not Balance), so there is no Brought-Forward row and the summary
# box is reduced to Total Debit / Total Credit only.

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
    fy_head_sty  = _ps('BRWFyHead', _BOLD, 10, TA_LEFT, color=letterhead.GREEN)
    fy_head_sty.keepWithNext = True  # Task 2: never leave the FY heading alone at page bottom
    summary_sty   = _ps('BRWSummary', _BASE, 8.5, TA_LEFT, color=letterhead.BODY)
    summary_b_sty = _ps('BRWSummaryB', _BOLD, 8.5, TA_LEFT, color=letterhead.BODY)

    hdr_l = _ps('BRWHdrL', _BOLD, 8, TA_LEFT,  color=_W)
    hdr_r = _ps('BRWHdrR', _BOLD, 8, TA_RIGHT, color=_W)

    dat_l = _ps('BRWDatL', _BASE, 8, TA_LEFT)
    dat_r = _ps('BRWDatR', _BASE, 8, TA_RIGHT)

    open_l = _ps('BRWOpenL', _BOLD, 8, TA_LEFT)
    open_r = _ps('BRWOpenR', _BOLD, 8, TA_RIGHT)

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

    def _header_row():
        return [
            Paragraph('Date', hdr_l),
            Paragraph('Voucher No', hdr_l),
            Paragraph('Transaction Name', hdr_l),
            Paragraph(f'Debit ({_RS})', hdr_r),
            Paragraph(f'Credit ({_RS})', hdr_r),
            Paragraph(sixth_header, hdr_l if not single_account else hdr_r),
        ]

    def _table_from_slice(slice_rows, has_header):
        table_rows = ([_header_row()] if has_header else []) + [c for c, _k in slice_rows]
        offset = 1 if has_header else 0
        opening_idx = None
        for i, (_c, kind) in enumerate(slice_rows):
            if kind == 'opening':
                opening_idx = i + offset
        cmds = _fy_table_style(len(table_rows), opening_idx, [], has_header)
        tbl = Table(table_rows, colWidths=col_widths, repeatRows=(1 if has_header else 0))
        tbl.setStyle(TableStyle(cmds))
        return tbl

    # ── Group rows by financial year (ascending) ──────────────────────────────
    fy_groups: dict = {}
    for row in rows:
        fy_start = _fy_start_year(_parse_iso_date(row['transaction_date']))
        fy_groups.setdefault(fy_start, []).append(row)
    fy_start_years = sorted(fy_groups.keys())

    running = 0.0
    brought_forward = None  # single-account mode only

    for fy_idx, fy_start in enumerate(fy_start_years):
        fy_rows = fy_groups[fy_start]
        fy_start_date, fy_end_date = _fy_bounds(fy_start)

        body_rows: list = []

        if single_account and fy_idx > 0 and brought_forward is not None:
            bf_cells = [
                Paragraph('', open_l), Paragraph('', open_l),
                Paragraph('Brought Forward', open_l),
                Paragraph('-', open_r), Paragraph('-', open_r),
                Paragraph(_bal(brought_forward), open_r),
            ]
            body_rows.append((bf_cells, 'opening'))

        fy_total_debit = 0.0
        fy_total_credit = 0.0
        for row in fy_rows:
            debit = row['debit']
            credit = row['credit']
            fy_total_debit += debit
            fy_total_credit += credit
            # Domain semantics: credit (received) increases what the firm
            # owes; debit (repaid) decreases it — see module docstring.
            running = round(running + credit - debit, 2)

            if single_account:
                sixth_cell = Paragraph(_bal(running), dat_r)
            else:
                sixth_cell = Paragraph(row['account'] or '-', dat_l)

            cells = [
                Paragraph(_fmt_date(row['transaction_date']), dat_l),
                Paragraph(row['voucher_no'] or '-', dat_l),
                Paragraph(row['transaction_name'] or '-', dat_l),
                Paragraph(_amt(debit), dat_r),
                Paragraph(_amt(credit), dat_r),
                sixth_cell,
            ]
            body_rows.append((cells, 'normal'))

        heading_text = (
            f'{_fy_label(fy_start)}  '
            f'({_fmt_ddmmyyyy(fy_start_date)} to {_fmt_ddmmyyyy(fy_end_date)})'
        )

        # Task 2: heading glued to the table via keepWithNext (never appended
        # inside a whole-block KeepTogether).
        elements.append(Paragraph(heading_text, fy_head_sty))

        n_body = len(body_rows)
        n_tail = min(_TAIL_ROW_COUNT, n_body)
        main_slice = body_rows[: n_body - n_tail]
        tail_slice = body_rows[n_body - n_tail:]

        if main_slice:
            elements.append(Spacer(1, 3))
            elements.append(_table_from_slice(main_slice, has_header=True))

        tail_table = _table_from_slice(tail_slice, has_header=True)

        if single_account:
            fy_closing_principal = running
            summary_rows = [
                [Paragraph('Total Principal Amount', summary_b_sty), Paragraph(_fmt_inr(fy_closing_principal), summary_b_sty)],
            ]
            brought_forward = fy_closing_principal  # exactly the next FY's Brought Forward
        else:
            summary_rows = [
                [Paragraph('Total Debit Amount', summary_sty), Paragraph(_fmt_inr(round(fy_total_debit, 2)), summary_sty)],
                [Paragraph('Total Credit Amount', summary_sty), Paragraph(_fmt_inr(round(fy_total_credit, 2)), summary_sty)],
            ]

        # Task 4: FY summary as a light-green filled box with a 2px solid
        # green border, spanning the table width.
        summary_table = Table(summary_rows, colWidths=[_CONTENT_W * 0.6, _CONTENT_W * 0.4])
        summary_table.setStyle(TableStyle([
            ('BACKGROUND',    (0, 0), (-1, -1), _SUMMARY_BG),
            ('BOX',           (0, 0), (-1, -1), 2, letterhead.GREEN),
            ('LEFTPADDING',   (0, 0), (-1, -1), 8),
            ('RIGHTPADDING',  (0, 0), (-1, -1), 8),
            ('TOPPADDING',    (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))

        elements.append(KeepTogether([tail_table, Spacer(1, 6), summary_table, Spacer(1, 10)]))

    # ── Build PDF with the letterhead header AND footer repeating on every page ─
    doc.build(elements, onFirstPage=_draw_header_footer, onLaterPages=_draw_header_footer)
    return buffer.getvalue()


# ══════════════════════════════════════════════════════════════════════════════
# render_borrowings_interest_pdf — include_interest=ON report (added 2026-08-06,
# reworked 2026-08-06 — FY-boundary rounding fix, pagination fix, merged
# interest rows, FY-section color shading)
# ══════════════════════════════════════════════════════════════════════════════
#
# rows : list[dict] returned by borrowings.compute_borrowings_interest()['rows']
#        — the SAME data GET /borrowings?include_interest=1 returns; this
#        renderer never queries the DB or recomputes anything itself, so the
#        screen and this PDF can never disagree (per the task's hard
#        requirement).
# fy_totals : dict returned by the same call (['fy_totals']) — single-account
#        mode only; {fy_label: {closing_principal, interest, total}}, ALREADY
#        rounded exactly once by the engine (borrowings._compute_fy_totals).
#        This renderer reads these values directly for "Total Principal
#        Amount" / "Total Interest Amount" / "Total Amount" / the next FY's
#        "Brought Forward" — it NEVER re-sums the already-2dp-rounded per-row
#        `interest` values itself (that double-rounding was the root cause of
#        the FY-boundary discontinuity bug: a FY's printed "Total Amount"
#        could differ by a paisa or two from the balance the engine actually
#        carries into the next FY's first row). Empty `{}` in "all accounts"
#        mode (not applicable there — see below).
#
# Design
# ------
# - Gains an Interest column (per the task spec) on top of every column the
#   plain (interest-OFF) report has.
# - Sectioned into FINANCIAL-YEAR blocks: a bold FY heading
#   ('FY 2025-26 (01-04-2025 to 31-03-2026)') directly above each table, a
#   dark-green header band (letterhead.GREEN) with white bold headers
#   (right-aligned for numeric columns), interest rows in a distinct
#   teal/green text color (letterhead.GREEN2), zebra striping on the
#   transaction rows, and a light-green-filled / 2px-green-bordered summary
#   box after each FY block (_SUMMARY_BG + a ('BOX', ..., 2, letterhead.GREEN)
#   command — reuses the existing green palette, no new brand color).
# - PAGINATION (fixed 2026-08-06 — no more blank leading page): the OLD code
#   wrapped the ENTIRE FY block (heading + table + summary) in a single
#   KeepTogether, which defers the WHOLE block to a fresh page the instant it
#   doesn't fit on the current one — for the common case of a FY block taller
#   than one page, that left page 1 mostly blank. Fixed by NOT wrapping the
#   whole block: the FY heading uses a `keepWithNext=True` paragraph style (so
#   it's never left dangling alone at the bottom of a page); the bulk of the
#   FY's data rows ("main" chunk) flow/paginate naturally as a normal
#   `repeatRows=1` Table (reportlab splits it across pages on its own,
#   repeating the green header band on every continuation page); only the
#   LAST few data rows ("tail" chunk, up to `_TAIL_ROW_COUNT` rows) are pulled
#   into a SEPARATE small table and wrapped in ONE KeepTogether together with
#   the FY summary box, so the summary can never land alone/orphaned on a
#   fresh page with no table context above it. The tail table always carries
#   its own repeated header row (a small, accepted cosmetic tradeoff: if the
#   tail happens to render immediately after the main chunk on the SAME page,
#   the green header band appears twice in a row — reads like a natural
#   "closing entries" sub-table, and is far preferable to the alternative of
#   an un-headed set of rows floating alone on a fresh page if the tail is
#   pushed over by KeepTogether).
# - MERGED INTEREST ROWS (Task 3, single-account mode only): every row with
#   row_type == 'interest' keeps its Date cell, then SPANs the remaining six
#   columns (Voucher No, Transaction Name, Debit, Credit, Interest, Balance)
#   into ONE centered cell reading "{FullMonthName} Interest - Rs.{amount}"
#   (Indian-grouped, 2dp) — the balance is intentionally no longer shown on
#   interest rows at all (per the task: the balance column is never updated
#   on an interest row, which previously read as confusing/misleading).
#   "All accounts" mode is UNCHANGED (no Balance column to hide there, and
#   the task's column list — "Voucher No, Transaction Name, Debit, Credit,
#   Interest, Balance" — only matches the single-account 7-column layout).
# - single-account mode (an `account` was given): the 7th column is a real
#   running Balance (on transaction/opening rows only — see merge above), and
#   the 3-line FY summary/Brought-Forward mechanism reads `fy_totals`
#   directly (see above) rather than re-deriving anything from `rows`.
# - "all accounts" mode (`account` blank): `compute_borrowings_interest`
#   deliberately returns `balance: null` on every row once accounts are
#   mixed (a running/closing PRINCIPAL is not a meaningful single number
#   across different accounts — see that function's docstring), and
#   `fy_totals` is `{}` in this mode for the same reason. The task's
#   "Total Principal Amount" / "Total Amount" / "Brought Forward" concept is
#   inherently a PER-ACCOUNT capitalization figure, so it has no well-defined
#   analogue when accounts are merged; rather than fabricate a misleading
#   number, this mode still sections by FY (per the task's literal ask) but
#   the 7th column is Account (not Balance), interest rows are NOT merged
#   (unchanged _row_cells rendering), and the per-FY summary is reduced to
#   Total Debit / Total Credit / Total Interest only — no Brought-Forward
#   row. This is a deliberate, documented interpretation (flagged in the
#   original task write-up) rather than an oversight.
# - Zero-row case: same graceful 'No records for the selected period.'
#   message as the plain report.

_TAIL_ROW_COUNT = 3  # last-N data rows kept together with the FY summary box


def _fy_table_style(total_rows: int, opening_idx: int | None, interest_idxs: list,
                     has_header: bool) -> list:
    """TableStyle command list for one FY sub-table (main or tail chunk).
    `total_rows` includes the header row when `has_header` is True. Zebra
    striping starts right after the opening/brought-forward row (or right
    after the header when there is none)."""
    cmds: list = []
    if has_header:
        cmds.append(('BACKGROUND', (0, 0), (-1, 0), letterhead.GREEN))
    cmds += [
        ('FONTSIZE',      (0, 0), (-1, -1), 7.5),
        ('GRID',          (0, 0), (-1, -1), 0.3, _CELL_BORDER),
        ('TOPPADDING',    (0, 0), (-1, -1), 2.5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2.5),
        ('LEFTPADDING',   (0, 0), (-1, -1), 3),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 3),
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
    ]
    if opening_idx is not None:
        cmds.append(('BACKGROUND', (0, opening_idx), (-1, opening_idx), _TOTAL_BG))
        cmds.append(('SPAN', (0, opening_idx), (1, opening_idx)))
    for idx in interest_idxs:
        cmds.append(('SPAN', (1, idx), (6, idx)))
    zebra_start = (opening_idx + 1) if opening_idx is not None else (1 if has_header else 0)
    for i in range(zebra_start, total_rows):
        if (i - zebra_start) % 2 == 1:
            cmds.append(('BACKGROUND', (0, i), (-1, i), _ALT_BG))
    return cmds


def render_borrowings_interest_pdf(
    rows: list, missing_rate_accounts: list, fy_totals: dict,
    account: str, from_date: str, to_date: str,
) -> bytes:
    """Render the Borrowings Statement WITH interest as a portrait A4 PDF.

    Parameters
    ----------
    rows : list[dict] returned by borrowings.compute_borrowings_interest()['rows']
    missing_rate_accounts : list[str] returned by the same call — accounts in
        scope with no configured borrowing_rate row (0% was used for them);
        surfaced as a one-line note under the subtitle when non-empty (an
        additive, non-spec-mandated touch for auditability — the JSON
        contract for warning the UI is the `missing_rate_accounts` field
        itself, per the task; this PDF note is just a courtesy mirror of it).
    fy_totals : dict returned by the same call (['fy_totals']) — see the
        module comment above; single-account mode only, `{}` otherwise.
    account : '' / None for "all accounts", else the single account name
    from_date, to_date : 'YYYY-MM-DD' strings

    Returns
    -------
    bytes : raw PDF content
    """
    account = (account or '').strip()
    single_account = bool(account)
    fy_totals = fy_totals or {}

    buffer = BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=_MARGIN,
        rightMargin=_MARGIN,
        topMargin=letterhead.HEADER_TOP_PAD + letterhead.HEADER_HEIGHT + 0.3 * cm,
        bottomMargin=1.4 * cm,
        title='IAL Borrowings Statement (with Interest)',
        author='IRAVI AGRO LIFE LLP',
    )

    _W = colors.white
    _BASE, _BOLD = letterhead.BASE_FONT, letterhead.BOLD_FONT

    title_sty    = _ps('BRWITitle', _BOLD, 13, TA_CENTER, color=letterhead.GREEN)
    subtitle_sty = _ps('BRWISubtitle', _BOLD, 9, TA_CENTER, color=letterhead.BODY)
    note_sty     = _ps('BRWINote', _BASE, 7.5, TA_CENTER, color=letterhead.MUTED)
    empty_sty    = _ps('BRWIEmpty', _BASE, 9.5, TA_CENTER, color=letterhead.MUTED)
    fy_head_sty  = _ps('BRWIFyHead', _BOLD, 10, TA_LEFT, color=letterhead.GREEN)
    fy_head_sty.keepWithNext = True  # Task 2: never leave the FY heading alone at page bottom
    summary_sty  = _ps('BRWISummary', _BASE, 8.5, TA_LEFT, color=letterhead.BODY)
    summary_b_sty = _ps('BRWISummaryB', _BOLD, 8.5, TA_LEFT, color=letterhead.BODY)

    hdr_l = _ps('BRWIHdrL', _BOLD, 7.5, TA_LEFT,  color=_W)
    hdr_r = _ps('BRWIHdrR', _BOLD, 7.5, TA_RIGHT, color=_W)

    dat_l = _ps('BRWIDatL', _BASE, 7.5, TA_LEFT)
    dat_r = _ps('BRWIDatR', _BASE, 7.5, TA_RIGHT)
    dat_i = _ps('BRWIDatI', _BASE, 7.5, TA_LEFT, color=letterhead.GREEN2)  # interest-row emphasis (all-accounts mode)
    # Task 3: merged interest-row label (single-account mode) — same color,
    # centered, italic for visual distinction from ordinary transaction rows.
    dat_i_center = _ps('BRWIDatICenter', _BASE, 7.5, TA_CENTER, color=letterhead.GREEN2)

    open_l = _ps('BRWIOpenL', _BOLD, 7.5, TA_LEFT)
    open_r = _ps('BRWIOpenR', _BOLD, 7.5, TA_RIGHT)

    elements: list = [
        Paragraph('BORROWINGS STATEMENT (WITH INTEREST)', title_sty),
        Spacer(1, 5),
    ]

    period_text = f'Period: {_fmt_date(from_date)} to {_fmt_date(to_date)}'
    account_text = f'Account: {account}' if single_account else 'Account: All accounts'
    elements.append(Paragraph(f'{period_text}  |  {account_text}', subtitle_sty))

    if missing_rate_accounts:
        note_text = (
            'Rate not configured (0% used): ' + ', '.join(missing_rate_accounts)
        )
        elements.append(Spacer(1, 3))
        elements.append(Paragraph(note_text, note_sty))

    elements.append(Spacer(1, 10))

    if not rows:
        elements.append(Paragraph('No records for the selected period.', empty_sty))
        doc.build(elements, onFirstPage=_draw_header_footer, onLaterPages=_draw_header_footer)
        return buffer.getvalue()

    # ── Column widths ─────────────────────────────────────────────────────────
    if single_account:
        date_w, voucher_w, name_w = 55.0, 72.0, 108.0
        remaining = _CONTENT_W - (date_w + voucher_w + name_w)
        amt_w = remaining / 4
        col_widths = [date_w, voucher_w, name_w, amt_w, amt_w, amt_w, amt_w]
        seventh_header = f'Balance ({_RS})'
    else:
        date_w, voucher_w, name_w, acct_w = 50.0, 62.0, 92.0, 95.0
        remaining = _CONTENT_W - (date_w + voucher_w + name_w + acct_w)
        amt_w = remaining / 3
        col_widths = [date_w, voucher_w, name_w, acct_w, amt_w, amt_w, amt_w]
        seventh_header = None  # 4th column is Account, not a trailing 7th

    def _header_row():
        if single_account:
            return [
                Paragraph('Date', hdr_l),
                Paragraph('Voucher No', hdr_l),
                Paragraph('Transaction Name', hdr_l),
                Paragraph(f'Debit ({_RS})', hdr_r),
                Paragraph(f'Credit ({_RS})', hdr_r),
                Paragraph(f'Interest ({_RS})', hdr_r),
                Paragraph(seventh_header, hdr_r),
            ]
        return [
            Paragraph('Date', hdr_l),
            Paragraph('Voucher No', hdr_l),
            Paragraph('Transaction Name', hdr_l),
            Paragraph('Account', hdr_l),
            Paragraph(f'Debit ({_RS})', hdr_r),
            Paragraph(f'Credit ({_RS})', hdr_r),
            Paragraph(f'Interest ({_RS})', hdr_r),
        ]

    def _row_cells(row):
        name_sty = dat_i if row['row_type'] == 'interest' else dat_l
        name_text = row['transaction_name'] or ('Opening balance' if row['row_type'] == 'opening' else '-')
        if single_account:
            bal_text = _bal(row['balance']) if row['balance'] is not None else '-'
            return [
                Paragraph(_fmt_date(row['transaction_date']), dat_l),
                Paragraph(row['voucher_no'] or '-', dat_l),
                Paragraph(name_text, name_sty),
                Paragraph(_amt(row['debit']), dat_r),
                Paragraph(_amt(row['credit']), dat_r),
                Paragraph(_fmt_inr(row['interest']) if row['interest'] else '-', dat_r),
                Paragraph(bal_text, dat_r),
            ]
        return [
            Paragraph(_fmt_date(row['transaction_date']), dat_l),
            Paragraph(row['voucher_no'] or '-', dat_l),
            Paragraph(name_text, name_sty),
            Paragraph(row['account'] or '-', dat_l),
            Paragraph(_amt(row['debit']), dat_r),
            Paragraph(_amt(row['credit']), dat_r),
            Paragraph(_fmt_inr(row['interest']) if row['interest'] else '-', dat_r),
        ]

    def _interest_merged_cells(row):
        """Task 3 (single-account mode only): Date cell kept as-is; the
        remaining six columns (Voucher No, Transaction Name, Debit, Credit,
        Interest, Balance) are collapsed into ONE centered cell reading
        '{FullMonthName} Interest - Rs.{amount}' — the balance is
        intentionally never shown on an interest row (it never changes on
        one, which previously read as confusing). The caller adds the
        matching SPAN command via `_fy_table_style`'s `interest_idxs`."""
        d = _parse_iso_date(row['transaction_date'])
        full_month = d.strftime('%B')
        label = f'{full_month} Interest - {_fmt_inr(row["interest"] or 0.0)}'
        return [
            Paragraph(_fmt_date(row['transaction_date']), dat_l),
            Paragraph(label, dat_i_center),
            '', '', '', '', '',
        ]

    # ── Group rows by financial year (ascending) ──────────────────────────────
    fy_groups: dict = {}
    for row in rows:
        fy_start = _fy_start_year(_parse_iso_date(row['transaction_date']))
        fy_groups.setdefault(fy_start, []).append(row)
    fy_start_years = sorted(fy_groups.keys())

    brought_forward = None  # single-account mode only

    for fy_idx, fy_start in enumerate(fy_start_years):
        fy_rows = fy_groups[fy_start]
        fy_start_date, fy_end_date = _fy_bounds(fy_start)

        # ── Build the FY's body rows as (cells, kind) tuples — 'opening' /
        # 'interest' (single-account only, merged cell) / 'normal'. ────────────
        body_rows: list = []

        if single_account and fy_idx > 0 and brought_forward is not None:
            bf_cells = [
                Paragraph('', open_l), Paragraph('', open_l),
                Paragraph('Brought Forward', open_l),
                Paragraph('-', open_r), Paragraph('-', open_r), Paragraph('-', open_r),
                Paragraph(_bal(brought_forward), open_r),
            ]
            body_rows.append((bf_cells, 'opening'))

        for row in fy_rows:
            if row['row_type'] == 'opening' and single_account:
                # Style the opening row like a Brought-Forward row (bold).
                cells = [
                    Paragraph('', open_l), Paragraph('', open_l),
                    Paragraph('Opening Balance', open_l),
                    Paragraph('-', open_r), Paragraph('-', open_r), Paragraph('-', open_r),
                    Paragraph(_bal(row['balance']) if row['balance'] is not None else '-', open_r),
                ]
                body_rows.append((cells, 'opening'))
            elif row['row_type'] == 'interest' and single_account:
                body_rows.append((_interest_merged_cells(row), 'interest'))
            else:
                body_rows.append((_row_cells(row), 'normal'))

        fy_total_debit = sum(r['debit'] for r in fy_rows if r['row_type'] == 'transaction')
        fy_total_credit = sum(r['credit'] for r in fy_rows if r['row_type'] == 'transaction')

        heading_text = (
            f'{_fy_label(fy_start)}  '
            f'({_fmt_ddmmyyyy(fy_start_date)} to {_fmt_ddmmyyyy(fy_end_date)})'
        )

        # ── Task 2: heading glued to the table via keepWithNext (never
        # append it inside a whole-block KeepTogether) ───────────────────────
        elements.append(Paragraph(heading_text, fy_head_sty))

        # Split into a "main" chunk (flows/paginates naturally) and a "tail"
        # chunk (last _TAIL_ROW_COUNT rows) kept together with the summary
        # box so the summary never orphans alone on a fresh page.
        n_body = len(body_rows)
        n_tail = min(_TAIL_ROW_COUNT, n_body)
        main_slice = body_rows[: n_body - n_tail]
        tail_slice = body_rows[n_body - n_tail:]

        def _table_from_slice(slice_rows, has_header):
            table_rows = ([_header_row()] if has_header else []) + [c for c, _k in slice_rows]
            offset = 1 if has_header else 0
            opening_idx = None
            interest_idxs = []
            for i, (_c, kind) in enumerate(slice_rows):
                ridx = i + offset
                if kind == 'opening':
                    opening_idx = ridx
                elif kind == 'interest':
                    interest_idxs.append(ridx)
            cmds = _fy_table_style(len(table_rows), opening_idx, interest_idxs, has_header)
            tbl = Table(table_rows, colWidths=col_widths, repeatRows=(1 if has_header else 0))
            tbl.setStyle(TableStyle(cmds))
            return tbl

        if main_slice:
            elements.append(Spacer(1, 3))
            elements.append(_table_from_slice(main_slice, has_header=True))

        tail_table = _table_from_slice(tail_slice, has_header=True)

        if single_account:
            fy_key = f'{fy_start}-{str(fy_start + 1)[-2:]}'
            fy_data = fy_totals.get(fy_key, {})
            fy_closing_principal = fy_data.get('closing_principal', 0.0)
            fy_total_interest = fy_data.get('interest', 0.0)
            fy_total_amount = fy_data.get('total', round(fy_closing_principal + fy_total_interest, 2))

            summary_rows = [
                [Paragraph('Total Principal Amount', summary_b_sty), Paragraph(_fmt_inr(fy_closing_principal), summary_b_sty)],
                [Paragraph('Total Interest Amount', summary_sty), Paragraph(_fmt_inr(fy_total_interest), summary_sty)],
                [Paragraph('Total Amount', summary_b_sty), Paragraph(_fmt_inr(fy_total_amount), summary_b_sty)],
            ]
            brought_forward = fy_total_amount  # carried into the next FY block — byte-identical to the engine's own capitalized balance
        else:
            fy_total_interest = round(
                sum(r['interest'] for r in fy_rows if r['row_type'] == 'interest'), 2,
            )
            summary_rows = [
                [Paragraph('Total Debit Amount', summary_sty), Paragraph(_fmt_inr(round(fy_total_debit, 2)), summary_sty)],
                [Paragraph('Total Credit Amount', summary_sty), Paragraph(_fmt_inr(round(fy_total_credit, 2)), summary_sty)],
                [Paragraph('Total Interest Amount', summary_b_sty), Paragraph(_fmt_inr(fy_total_interest), summary_b_sty)],
            ]

        # Task 4: FY summary as a light-green filled box with a 2px solid
        # green border, spanning the table width.
        summary_table = Table(summary_rows, colWidths=[_CONTENT_W * 0.6, _CONTENT_W * 0.4])
        summary_table.setStyle(TableStyle([
            ('BACKGROUND',    (0, 0), (-1, -1), _SUMMARY_BG),
            ('BOX',           (0, 0), (-1, -1), 2, letterhead.GREEN),
            ('LEFTPADDING',   (0, 0), (-1, -1), 8),
            ('RIGHTPADDING',  (0, 0), (-1, -1), 8),
            ('TOPPADDING',    (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))

        # The tail chunk + summary box are kept together as ONE unit so the
        # summary can never land alone at the top of a page with no table
        # context above it (Task 2's third requirement).
        elements.append(KeepTogether([tail_table, Spacer(1, 6), summary_table, Spacer(1, 10)]))

    # ── Build PDF with the letterhead header AND footer repeating on every page ─
    doc.build(elements, onFirstPage=_draw_header_footer, onLaterPages=_draw_header_footer)
    return buffer.getvalue()


# ══════════════════════════════════════════════════════════════════════════════
# render_borrowings_summary_fy_pdf — "all accounts" FY-summary matrix (added
# 2026-08-06, Task 6). Landscape A4, modeled on customer_balances_fy_pdf.py's
# two-row-header FY-matrix layout so the two "*_fy" reports look like
# siblings — same letterhead, same GREEN header band, same TOTAL-row/zebra
# treatment.
#
# data : dict returned by borrowings.compute_borrowings_summary_fy() — the
#        SAME function GET /borrowings/summary-fy itself calls, so the
#        screen and this PDF can never disagree.
# ══════════════════════════════════════════════════════════════════════════════

def render_borrowings_summary_fy_pdf(data: dict, include_interest: bool = False) -> bytes:
    """Render the Borrowings Summary (FY) report as a landscape A4 PDF.

    Parameters
    ----------
    data : dict returned by borrowings.compute_borrowings_summary_fy()
    include_interest : whether the Interest sub-column is shown per FY group
        (must match the `include_interest` the caller passed to
        compute_borrowings_summary_fy() — the handler always passes the same
        flag to both).

    Returns
    -------
    bytes : raw PDF content
    """
    fys = data.get('fys', [])
    rows = data.get('rows', [])
    totals = data.get('totals', {})
    missing_rate_accounts = data.get('missing_rate_accounts', [])
    n_fys = len(fys)

    page_w, page_h = landscape(A4)
    margin = 1.0 * cm
    content_w = page_w - 2 * margin

    buffer = BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=margin,
        rightMargin=margin,
        topMargin=letterhead.HEADER_TOP_PAD + letterhead.HEADER_HEIGHT + 0.3 * cm,
        bottomMargin=1.4 * cm,
        title='IAL Borrowings Summary (FY)',
        author='IRAVI AGRO LIFE LLP',
    )

    _W = colors.white
    _BASE, _BOLD = letterhead.BASE_FONT, letterhead.BOLD_FONT

    title_sty  = _ps('BSFYTitle', _BOLD, 12, TA_LEFT, color=letterhead.GREEN)
    right_sty  = _ps('BSFYRight', _BASE, 8, TA_RIGHT, color=letterhead.MUTED)
    note_sty   = _ps('BSFYNote', _BASE, 7.5, TA_LEFT, color=letterhead.MUTED)
    empty_sty  = _ps('BSFYEmpty', _BASE, 9.5, TA_CENTER, color=letterhead.MUTED)

    hdr_c = _ps('BSFYHdrC', _BOLD, 6.5, TA_CENTER, color=_W)
    hdr_l = _ps('BSFYHdrL', _BOLD, 6.5, TA_LEFT,   color=_W)
    hdr_r = _ps('BSFYHdrR', _BOLD, 6.5, TA_RIGHT,  color=_W)

    dat_l = _ps('BSFYDatL', _BASE, 6.5, TA_LEFT)
    dat_c = _ps('BSFYDatC', _BASE, 6.5, TA_CENTER)
    dat_r = _ps('BSFYDatR', _BASE, 6.5, TA_RIGHT)

    tot_l = _ps('BSFYTotL', _BOLD, 6.5, TA_LEFT)
    tot_c = _ps('BSFYTotC', _BOLD, 6.5, TA_CENTER)
    tot_r = _ps('BSFYTotR', _BOLD, 6.5, TA_RIGHT)

    today_str = _date.today().strftime('%d-%m-%Y')
    title_row = Table(
        [[Paragraph('BORROWINGS SUMMARY (FY)', title_sty), Paragraph(f'Date: {today_str}', right_sty)]],
        colWidths=[content_w * 0.75, content_w * 0.25],
    )
    title_row.setStyle(TableStyle([
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING',   (0, 0), (-1, -1), 0),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 0),
        ('TOPPADDING',    (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))

    elements: list = [title_row, Spacer(1, 4)]

    if include_interest and missing_rate_accounts:
        elements.append(Paragraph(
            'Rate not configured (0% used): ' + ', '.join(missing_rate_accounts), note_sty,
        ))
        elements.append(Spacer(1, 3))

    elements.append(Spacer(1, 4))

    if not rows or not fys:
        elements.append(Paragraph('No records.', empty_sty))
        doc.build(elements, onFirstPage=_draw_header_footer, onLaterPages=_draw_header_footer)
        return buffer.getvalue()

    # ── Column widths ─────────────────────────────────────────────────────────
    sno_w = 22.0
    account_w = 150.0 if n_fys <= 4 else 120.0
    sub_col_count = 4 if include_interest else 3
    fixed_w = sno_w + account_w
    fy_pool = content_w - fixed_w
    sub_col_w = fy_pool / (n_fys * sub_col_count) if n_fys > 0 else fy_pool / sub_col_count

    col_widths: list = [sno_w, account_w]
    for _ in range(n_fys):
        col_widths.extend([sub_col_w] * sub_col_count)
    n_cols = len(col_widths)

    # ── Two-row header ────────────────────────────────────────────────────────
    row0: list = [Paragraph('S.No', hdr_c), Paragraph('Account', hdr_l)]
    for fy in fys:
        row0.append(Paragraph(fy, hdr_c))
        row0.extend([''] * (sub_col_count - 1))
    row1: list = ['', '']
    for _ in range(n_fys):
        sub_headers = [f'Taken ({_RS})', f'Paid ({_RS})']
        if include_interest:
            sub_headers.append(f'Interest ({_RS})')
        sub_headers.append(f'Closing ({_RS})')
        row1.extend(Paragraph(h, hdr_r) for h in sub_headers)

    span_cmds: list = [
        ('SPAN', (0, 0), (0, 1)),
        ('SPAN', (1, 0), (1, 1)),
    ]
    for i in range(n_fys):
        sc = 2 + i * sub_col_count
        span_cmds.append(('SPAN', (sc, 0), (sc + sub_col_count - 1, 0)))

    table_rows: list = [row0, row1]

    def _fy_cells(fy_data):
        if not fy_data:
            cells = [Paragraph('-', dat_r), Paragraph('-', dat_r)]
            if include_interest:
                cells.append(Paragraph('-', dat_r))
            cells.append(Paragraph('-', dat_r))
            return cells
        cells = [Paragraph(_amt(fy_data['taken']), dat_r), Paragraph(_amt(fy_data['paid']), dat_r)]
        if include_interest:
            cells.append(Paragraph(_amt(fy_data['interest']), dat_r))
        cells.append(Paragraph(_bal(fy_data['closing']), dat_r))
        return cells

    for idx, row in enumerate(rows):
        dr: list = [Paragraph(str(idx + 1), dat_c), Paragraph(row['account'], dat_l)]
        for fy in fys:
            dr.extend(_fy_cells(row['fys'].get(fy)))
        table_rows.append(dr)

    total_row: list = [Paragraph('', tot_c), Paragraph('TOTAL', tot_l)]
    for fy in fys:
        fy_totals = totals.get(fy)
        if not fy_totals:
            cells = [Paragraph('-', tot_r), Paragraph('-', tot_r)]
            if include_interest:
                cells.append(Paragraph('-', tot_r))
            cells.append(Paragraph('-', tot_r))
        else:
            cells = [Paragraph(_amt(fy_totals['taken']), tot_r), Paragraph(_amt(fy_totals['paid']), tot_r)]
            if include_interest:
                cells.append(Paragraph(_amt(fy_totals['interest']), tot_r))
            cells.append(Paragraph(_bal(fy_totals['closing']), tot_r))
        total_row.extend(cells)
    table_rows.append(total_row)
    total_row_idx = len(table_rows) - 1

    tbl_cmds: list = span_cmds + [
        ('BACKGROUND', (0, 0), (-1, 1), letterhead.GREEN),
        ('BACKGROUND', (0, total_row_idx), (-1, total_row_idx), _TOTAL_BG),
        ('FONTSIZE',      (0, 0), (-1, -1), 6.5),
        ('GRID',          (0, 0), (-1, -1), 0.3, _CELL_BORDER),
        ('TOPPADDING',    (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ('LEFTPADDING',   (0, 0), (-1, -1), 2),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 2),
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
    ]
    for i in range(2, total_row_idx):
        if (i - 2) % 2 == 1:
            tbl_cmds.append(('BACKGROUND', (0, i), (-1, i), _ALT_BG))

    data_tbl = Table(table_rows, colWidths=col_widths, repeatRows=2)
    data_tbl.setStyle(TableStyle(tbl_cmds))
    elements.append(data_tbl)

    doc.build(elements, onFirstPage=_draw_header_footer, onLaterPages=_draw_header_footer)
    return buffer.getvalue()
