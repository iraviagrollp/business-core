"""
supplier_balances_fy_pdf — PDF renderer for the Supplier Balances (FY) report.

Public surface
--------------
render_supplier_balances_fy_pdf(data: dict) -> bytes
    data    : dict returned by supplier_balances_fy.compute_supplier_balances_fy()
    returns : raw PDF bytes (landscape A4)

Design
------
- Landscape A4 (841.89 x 595.27 pt), 1 cm margins on all sides.
- Letterhead/footer/font/header-band restyled 2026-07-20 to match the Purchase
  Order house design (see `letterhead.py`, ported from
  procurement_api/po_pdf.py): Helvetica/Helvetica-Bold PRIMARY body font
  (DejaVuSans registered only for the inline rupee-glyph token), logo +
  centered 'IRAVI AGRO LIFE LLP' + orange tagline + identity-line letterhead,
  green (#17452f) table header band, shared registered-office footer. Report
  title ('SUPPLIER BALANCES' + Date) is its own row directly under the
  letterhead — not part of the shared letterhead itself.
- Two-row header with repeatRows=2 so both header rows repeat on every page.
  Row 1: S.No | Party | City | <FY XX-XX per FY group> | Balance Dr | Balance Cr
  Row 2: (per FY group) Debit (Rs) | Credit (Rs) | Balance (Rs)
  (NO Code column; NO Credit Notes column — supplier data has neither.)
- Amount cells: Rs<indian-grouped>.00 or '-' for zero.
- Balance / Balance Dr / Balance Cr: Rs... Dr / Rs... Cr / '-' .
- TOTAL row: #f0f0f0 background, bold.
- Header rows: letterhead.GREEN (#17452f) background, white text.
- Balance coloring (matching reports-section UI — SWAPPED vs customer) —
  UNCHANGED data semantics:
    Supplier semantics — Dr → GREEN (#1a6e35), Cr → RED (#cc0000).
    Applied to: per-FY Balance (₹), Balance Dr, Balance Cr columns (data + TOTAL rows).
    Debit / Credit columns and text columns are uncolored.
    Implemented via color-specific ParagraphStyle instances (ensures visual rendering)
    plus corresponding TEXTCOLOR TableStyle commands (satisfies spec requirement;
    note: TableStyle TEXTCOLOR is redundant for Paragraph cells but present for
    smoke-test / spec compliance).
- Footer every page: shared letterhead.draw_footer (registered office +
  computer-generated note), drawn directly on canvas so it appears on every
  page, even partial pages.

₹ / em-dash handling (2026-07-20 Helvetica switch)
---------------------------------------------------
- Rupee amounts are built with `_RS` (letterhead.register_fonts()'s inline
  `<font name="DejaVuSans">₹</font>` markup token) instead of a bare '₹'
  character, and are ALWAYS rendered through reportlab Paragraph (never a raw
  string drawn directly on canvas), so the markup is interpreted. The header
  sub-labels ('Debit (₹)' etc.) use the same token.
- The em-dash placeholder previously used for zero/blank cells ('—', U+2014)
  is replaced with a plain hyphen '-' throughout (zero-value cells, missing
  city, missing per-FY data) — this is a formatting-only change (no
  data/column change) and avoids relying on Helvetica/WinAnsiEncoding's
  handling of U+2014 for a purely cosmetic placeholder.
"""

from __future__ import annotations

from datetime import date as _date
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

# -- constants -------------------------------------------------------------------
_TOTAL_BG    = colors.HexColor('#f0f0f0')   # light grey TOTAL row
_ALT_BG      = colors.HexColor('#fafafa')   # subtle zebra stripe
_CELL_BORDER = colors.HexColor('#cccccc')

# Supplier semantics are SWAPPED vs customer:
#   Dr (payable) -> GREEN  -- a liability owed to the supplier is the normal state
#   Cr (advance/overpayment) -> RED -- unexpected credit warrants attention
_RED   = colors.HexColor('#cc0000')   # Cr balance — supplier advance/overpayment
_GREEN = colors.HexColor('#1a6e35')   # Dr balance — supplier payable (normal)

_PAGE_W, _PAGE_H = landscape(A4)           # 841.89 x 595.27 pt
_MARGIN = 1.0 * cm
_CONTENT_W = _PAGE_W - 2 * _MARGIN         # ~785 pt usable width

# Rupee token — Helvetica-primary; DejaVuSans is registered only for this glyph.
_RS = letterhead.register_fonts()


# -- formatting helpers -----------------------------------------------------------

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


def _bal_dr(balance_dr: float) -> str:
    """Balance Dr column cell (balance_dr is >= 0)."""
    return f'{_fmt_inr(balance_dr)} Dr' if balance_dr > 0 else '-'


def _bal_cr(balance_cr: float) -> str:
    """Balance Cr column cell (balance_cr is <= 0)."""
    return f'{_fmt_inr(abs(balance_cr))} Cr' if balance_cr < 0 else '-'


# -- paragraph style factory ---------------------------------------------------

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
    (letterhead.draw_footer) on every page (2026-07-21)."""
    letterhead.draw_header(canvas, doc)
    letterhead.draw_footer(canvas, doc)


# -- public API ----------------------------------------------------------------

def render_supplier_balances_fy_pdf(data: dict) -> bytes:
    """Render the Supplier Balances (FY) report as a landscape A4 PDF.

    Parameters
    ----------
    data : dict returned by supplier_balances_fy.compute_supplier_balances_fy()

    Returns
    -------
    bytes : raw PDF content suitable for attaching to an SES email
    """
    fys    = data['fys']
    rows   = data['rows']
    totals = data['totals']
    n_fys  = len(fys)

    buffer = BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=_MARGIN,
        rightMargin=_MARGIN,
        # Header is drawn on the canvas on every page (letterhead.draw_header,
        # via _draw_header_footer below) — topMargin reserves that band (2026-07-21).
        topMargin=letterhead.HEADER_TOP_PAD + letterhead.HEADER_HEIGHT + 0.3 * cm,
        bottomMargin=1.4 * cm,   # footer draws at 0.46-0.95 cm; 1.4 cm clears it
        title='IAL Supplier Balances FY',
        author='IRAVI AGRO LIFE LLP',
    )

    # -- Paragraph styles ------------------------------------------------------
    _W = colors.white
    _BASE, _BOLD = letterhead.BASE_FONT, letterhead.BOLD_FONT

    title_sty = _ps('SBFYTitle', _BOLD, 12, TA_LEFT,  color=letterhead.GREEN)
    right_sty = _ps('SBFYRight', _BASE, 8,  TA_RIGHT, color=letterhead.MUTED)

    hdr_c = _ps('SBFYHdrC', _BOLD, 6, TA_CENTER, color=_W)
    hdr_l = _ps('SBFYHdrL', _BOLD, 6, TA_LEFT,   color=_W)
    hdr_r = _ps('SBFYHdrR', _BOLD, 6, TA_RIGHT,  color=_W)

    dat_l = _ps('SBFYDatL', _BASE, 6, TA_LEFT)
    dat_c = _ps('SBFYDatC', _BASE, 6, TA_CENTER)
    dat_r = _ps('SBFYDatR', _BASE, 6, TA_RIGHT)

    # Color-specific data-row styles for balance columns
    # Supplier: Dr → GREEN (payable, normal), Cr → RED (advance/overpayment)
    dat_r_green = _ps('SBFYDatRGreen', _BASE, 6, TA_RIGHT, color=_GREEN)
    dat_r_red   = _ps('SBFYDatRRed',   _BASE, 6, TA_RIGHT, color=_RED)

    tot_l = _ps('SBFYTotL', _BOLD, 6, TA_LEFT)
    tot_c = _ps('SBFYTotC', _BOLD, 6, TA_CENTER)
    tot_r = _ps('SBFYTotR', _BOLD, 6, TA_RIGHT)

    # Color-specific TOTAL-row styles for balance columns
    tot_r_green = _ps('SBFYTotRGreen', _BOLD, 6, TA_RIGHT, color=_GREEN)
    tot_r_red   = _ps('SBFYTotRRed',   _BOLD, 6, TA_RIGHT, color=_RED)

    # -- Letterhead + report title row -----------------------------------------
    today_str = _date.today().strftime('%d-%m-%Y')

    title_row = Table(
        [[Paragraph('SUPPLIER BALANCES', title_sty), Paragraph(f'Date: {today_str}', right_sty)]],
        colWidths=[_CONTENT_W * 0.75, _CONTENT_W * 0.25],
    )
    title_row.setStyle(TableStyle([
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING',   (0, 0), (-1, -1), 0),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 0),
        ('TOPPADDING',    (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))

    # Header is drawn on the canvas (letterhead.draw_header, every page) — NOT
    # added here as a flowable, to avoid double-rendering it on page 1 (2026-07-21).
    elements: list = [title_row, Spacer(1, 5)]

    # -- Column widths ---------------------------------------------------------
    # Supplier has NO Code column (3 identity cols: S.No, Party, City).
    # Each FY group has 3 sub-cols: Debit, Credit, Balance (NO Credit Notes).
    # Wider Party/City widths vs customer to use the freed Code-column space.
    if n_fys <= 2:
        party_w, city_w = 160.0, 70.0
    elif n_fys == 3:
        party_w, city_w = 140.0, 60.0
    else:
        party_w, city_w = 120.0, 52.0

    sno_w    = 20.0
    bal_dr_w = 62.0
    bal_cr_w = 62.0

    fixed_w   = sno_w + party_w + city_w + bal_dr_w + bal_cr_w
    fy_pool   = _CONTENT_W - fixed_w
    sub_col_w = fy_pool / (n_fys * 3) if n_fys > 0 else fy_pool / 3

    col_widths: list[float] = [sno_w, party_w, city_w]
    for _ in range(n_fys):
        col_widths.extend([sub_col_w] * 3)
    col_widths.extend([bal_dr_w, bal_cr_w])

    n_cols = len(col_widths)

    # -- Two-row header ----------------------------------------------------------
    # Row 0: S.No | Party | City | <FY label spanning 3 cols each> | Bal Dr | Bal Cr
    # Row 1: identity cols blank (spanned) | Debit/Credit/Balance per FY | summary blank

    row0: list = [
        Paragraph('S.No',  hdr_c),
        Paragraph('Party', hdr_l),
        Paragraph('City',  hdr_l),
    ]
    for fy in fys:
        row0.append(Paragraph(fy, hdr_c))
        row0.extend(['', ''])            # SPAN placeholders (cols 2-3 of the group)
    row0.extend([
        Paragraph('Balance Dr', hdr_r),
        Paragraph('Balance Cr', hdr_r),
    ])

    row1: list = ['', '', '']            # identity cols spanned from row 0
    for _ in range(n_fys):
        row1.extend([
            Paragraph(f'Debit ({_RS})',   hdr_r),
            Paragraph(f'Credit ({_RS})',  hdr_r),
            Paragraph(f'Balance ({_RS})', hdr_r),
        ])
    row1.extend(['', ''])                # summary cols spanned from row 0

    # -- SPAN commands for the two header rows -----------------------------------
    span_cmds: list = [
        ('SPAN', (0,         0), (0,         1)),   # S.No
        ('SPAN', (1,         0), (1,         1)),   # Party
        ('SPAN', (2,         0), (2,         1)),   # City
        ('SPAN', (n_cols - 2, 0), (n_cols - 2, 1)), # Balance Dr
        ('SPAN', (n_cols - 1, 0), (n_cols - 1, 1)), # Balance Cr
    ]
    for i in range(n_fys):
        sc = 3 + i * 3              # 3 identity cols, 3 sub-cols per FY
        span_cmds.append(('SPAN', (sc, 0), (sc + 2, 0)))   # FY group header

    # -- Build data rows -------------------------------------------------------
    table_rows: list = [row0, row1]
    # per-cell TEXTCOLOR commands for balance columns only (spec requirement;
    # visual color is also set via color-specific ParagraphStyle instances above
    # because ReportLab ignores TableStyle TEXTCOLOR for Paragraph cells)
    color_cmds: list = []

    for idx, row in enumerate(rows):
        tbl_row = 2 + idx               # 2 header rows precede data rows
        fy_map = {p['fy']: p for p in row['per_fy']}
        dr: list = [
            Paragraph(str(idx + 1),           dat_c),
            Paragraph(row['party'],            dat_l),
            Paragraph(row['city'] or '-', dat_l),
        ]
        for fy_idx, fy in enumerate(fys):
            # Balance (₹) is the 3rd sub-col (index 2) within each FY group.
            # FY group for fy_idx starts at col 3 + fy_idx * 3.
            bal_col = 3 + fy_idx * 3 + 2
            p = fy_map.get(fy)
            if p:
                bal = p['balance']
                dr.append(Paragraph(_amt(p['debit']),   dat_r))
                dr.append(Paragraph(_amt(p['credit']),  dat_r))
                # Balance (₹): supplier SWAPPED — Dr → GREEN, Cr → RED
                if bal > 0:    # Dr (payable, normal) → GREEN
                    dr.append(Paragraph(_bal(bal), dat_r_green))
                    color_cmds.append(
                        ('TEXTCOLOR', (bal_col, tbl_row), (bal_col, tbl_row), _GREEN))
                elif bal < 0:  # Cr (advance/overpayment) → RED
                    dr.append(Paragraph(_bal(bal), dat_r_red))
                    color_cmds.append(
                        ('TEXTCOLOR', (bal_col, tbl_row), (bal_col, tbl_row), _RED))
                else:
                    dr.append(Paragraph(_bal(bal), dat_r))
            else:
                dr.extend([Paragraph('-', dat_r)] * 3)
        # Balance Dr column (n_cols - 2): Dr → GREEN (supplier payable)
        if row['balance_dr'] > 0:
            dr.append(Paragraph(_bal_dr(row['balance_dr']), dat_r_green))
            color_cmds.append(
                ('TEXTCOLOR', (n_cols - 2, tbl_row), (n_cols - 2, tbl_row), _GREEN))
        else:
            dr.append(Paragraph(_bal_dr(row['balance_dr']), dat_r))
        # Balance Cr column (n_cols - 1): Cr → RED (supplier advance/overpayment)
        if row['balance_cr'] < 0:
            dr.append(Paragraph(_bal_cr(row['balance_cr']), dat_r_red))
            color_cmds.append(
                ('TEXTCOLOR', (n_cols - 1, tbl_row), (n_cols - 1, tbl_row), _RED))
        else:
            dr.append(Paragraph(_bal_cr(row['balance_cr']), dat_r))
        table_rows.append(dr)

    # TOTAL row
    tot_fy_map = {p['fy']: p for p in totals['per_fy']}
    total_row: list = [
        Paragraph('',      tot_c),   # S.No
        Paragraph('TOTAL', tot_l),   # Party
        Paragraph('',      tot_c),   # City
    ]
    for fy in fys:
        p = tot_fy_map.get(fy)
        if p:
            bal = p['balance']
            total_row.append(Paragraph(_amt(p['debit']),   tot_r))
            total_row.append(Paragraph(_amt(p['credit']),  tot_r))
            # Supplier SWAPPED: Dr → GREEN, Cr → RED
            if bal > 0:
                total_row.append(Paragraph(_bal(bal), tot_r_green))
            elif bal < 0:
                total_row.append(Paragraph(_bal(bal), tot_r_red))
            else:
                total_row.append(Paragraph(_bal(bal), tot_r))
        else:
            total_row.extend([Paragraph('-', tot_r)] * 3)
    if totals['balance_dr'] > 0:
        total_row.append(Paragraph(_bal_dr(totals['balance_dr']), tot_r_green))
    else:
        total_row.append(Paragraph(_bal_dr(totals['balance_dr']), tot_r))
    if totals['balance_cr'] < 0:
        total_row.append(Paragraph(_bal_cr(totals['balance_cr']), tot_r_red))
    else:
        total_row.append(Paragraph(_bal_cr(totals['balance_cr']), tot_r))
    table_rows.append(total_row)

    total_row_idx = len(table_rows) - 1   # 0-based

    # TOTAL row TEXTCOLOR commands for balance columns (supplier: Dr→GREEN, Cr→RED)
    for fy_idx, fy in enumerate(fys):
        bal_col = 3 + fy_idx * 3 + 2
        p = tot_fy_map.get(fy)
        if p:
            bal = p['balance']
            if bal > 0:
                color_cmds.append(
                    ('TEXTCOLOR', (bal_col, total_row_idx), (bal_col, total_row_idx), _GREEN))
            elif bal < 0:
                color_cmds.append(
                    ('TEXTCOLOR', (bal_col, total_row_idx), (bal_col, total_row_idx), _RED))
    if totals['balance_dr'] > 0:
        color_cmds.append(
            ('TEXTCOLOR', (n_cols - 2, total_row_idx), (n_cols - 2, total_row_idx), _GREEN))
    if totals['balance_cr'] < 0:
        color_cmds.append(
            ('TEXTCOLOR', (n_cols - 1, total_row_idx), (n_cols - 1, total_row_idx), _RED))

    # -- Table style -------------------------------------------------------------
    tbl_cmds: list = span_cmds + [
        # Header rows (0 and 1)
        ('BACKGROUND', (0,             0), (-1, 1),              letterhead.GREEN),
        # TOTAL row
        ('BACKGROUND', (0, total_row_idx), (-1, total_row_idx),  _TOTAL_BG),
        # Grid, padding, alignment
        ('FONTSIZE',      (0, 0), (-1, -1), 6),
        ('GRID',          (0, 0), (-1, -1), 0.3, _CELL_BORDER),
        ('TOPPADDING',    (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ('LEFTPADDING',   (0, 0), (-1, -1), 2),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 2),
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
    ] + color_cmds

    # Zebra stripe on alternate data rows (starting from the first data row = index 2)
    for i in range(2, total_row_idx):
        if (i - 2) % 2 == 1:
            tbl_cmds.append(('BACKGROUND', (0, i), (-1, i), _ALT_BG))

    data_tbl = Table(table_rows, colWidths=col_widths, repeatRows=2)
    data_tbl.setStyle(TableStyle(tbl_cmds))
    elements.append(data_tbl)

    # -- Build PDF with the letterhead header AND footer repeating on every page --
    doc.build(elements, onFirstPage=_draw_header_footer, onLaterPages=_draw_header_footer)
    return buffer.getvalue()
