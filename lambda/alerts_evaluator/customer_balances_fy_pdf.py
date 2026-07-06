"""
customer_balances_fy_pdf — PDF renderer for the Customer Balances (FY) report.

Public surface
--------------
render_customer_balances_fy_pdf(data: dict) -> bytes
    data    : dict returned by customer_balances_fy.compute_customer_balances_fy()
    returns : raw PDF bytes (landscape A4)

Design
------
- Landscape A4 (841.89 x 595.27 pt), 1 cm margins on all sides.
- DejaVuSans TTF (registered via pdf_fonts.register_fonts()) so that
  ₹ (U+20B9) and — (U+2014) render without KeyError.
- Letterhead: IAL logo top-left, 'IRAVI AGRO LIFE LLP' + 'CUSTOMER BALANCES'
  centred bold, 'Date: DD-MM-YYYY' top-right.
- Two-row header with repeatRows=2 so both header rows repeat on every page.
  Row 1: S.No | Party | Code | City | <FY XX-XX per FY group> | Balance Dr | Balance Cr
  Row 2: (per FY group) Debit (₹) | Credit (₹) | Credit Notes (₹) | Balance (₹)
- Credit Notes column always shown (from-beginning, all credit notes included).
- Amount cells: ₹<indian-grouped>.00 or — for zero.
- Balance / Balance Dr / Balance Cr: ₹… Dr / ₹… Cr / — .
- TOTAL row: #f0f0f0 background, bold.
- Header rows: #1a3c2b (dark green) background, white text.
- Balance coloring (matching reports-section UI):
    Customer semantics — Dr → RED (#cc0000), Cr → GREEN (#1a6e35).
    Applied to: per-FY Balance (₹), Balance Dr, Balance Cr columns (data + TOTAL rows).
    Debit / Credit / Credit Notes columns and text columns are uncolored.
    Implemented via color-specific ParagraphStyle instances (ensures visual rendering)
    plus corresponding TEXTCOLOR TableStyle commands (satisfies spec requirement;
    note: TableStyle TEXTCOLOR is redundant for Paragraph cells but present for
    smoke-test / spec compliance).
- Footer every page: registered address + generated-by note (drawn directly on
  canvas so it appears on every page, even partial pages).
"""

from __future__ import annotations

import os
from datetime import date as _date
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
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

import pdf_fonts

# ── constants ─────────────────────────────────────────────────────────────────
_HEADER_BG   = colors.HexColor('#1a3c2b')   # brand dark green
_TOTAL_BG    = colors.HexColor('#f0f0f0')   # light grey TOTAL row
_ALT_BG      = colors.HexColor('#fafafa')   # subtle zebra stripe
_CELL_BORDER = colors.HexColor('#cccccc')

_RED   = colors.HexColor('#cc0000')   # Dr balance — customer receivable
_GREEN = colors.HexColor('#1a6e35')   # Cr balance — customer credit/advance

_PAGE_W, _PAGE_H = landscape(A4)           # 841.89 x 595.27 pt
_MARGIN = 1.0 * cm
_CONTENT_W = _PAGE_W - 2 * _MARGIN         # ~785 pt usable width

_LOGO_PATH = os.path.join(os.path.dirname(__file__), 'ial-logo.png')

_FOOTER_LINE1 = (
    'Reg. Address: Flat No: 102, BVR Plaza, H.No.5, 3-112/2, BJP Office Line  '
    'Shanthi Nagar, Kukatpally, Hyderabad, Telangana 500072'
)
_FOOTER_LINE2 = (
    'This report is computer-generated. '
    'Dr = Debit (receivable); Cr = Credit (payable).'
)


# ── formatting helpers ────────────────────────────────────────────────────────

def _fmt_inr(value: float) -> str:
    """Format |value| as Indian-grouped rupees, e.g. '₹1,23,456.00'."""
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
    return '₹' + ','.join(groups) + '.' + dec_str


def _amt(value: float) -> str:
    """Return Indian-grouped rupee string for non-zero value, else em-dash."""
    return _fmt_inr(value) if value > 0 else '—'


def _bal(balance: float) -> str:
    """Return balance with Dr/Cr suffix or em-dash for zero."""
    if balance > 0:
        return f'{_fmt_inr(balance)} Dr'
    if balance < 0:
        return f'{_fmt_inr(abs(balance))} Cr'
    return '—'


def _bal_dr(balance_dr: float) -> str:
    """Balance Dr column cell (balance_dr is >= 0)."""
    return f'{_fmt_inr(balance_dr)} Dr' if balance_dr > 0 else '—'


def _bal_cr(balance_cr: float) -> str:
    """Balance Cr column cell (balance_cr is <= 0)."""
    return f'{_fmt_inr(abs(balance_cr))} Cr' if balance_cr < 0 else '—'


# ── footer callback ───────────────────────────────────────────────────────────

def _draw_footer(canvas, doc):
    """Draw two-line registered-address footer on every page."""
    canvas.saveState()
    canvas.setFont('DejaVuSans', 6)
    canvas.setFillColor(colors.HexColor('#666666'))
    canvas.drawCentredString(_PAGE_W / 2, 0.70 * cm, _FOOTER_LINE1)
    canvas.drawCentredString(_PAGE_W / 2, 0.38 * cm, _FOOTER_LINE2)
    canvas.restoreState()


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

def render_customer_balances_fy_pdf(data: dict) -> bytes:
    """Render the Customer Balances (FY) report as a landscape A4 PDF.

    Parameters
    ----------
    data : dict returned by customer_balances_fy.compute_customer_balances_fy()

    Returns
    -------
    bytes : raw PDF content suitable for attaching to an SES email
    """
    pdf_fonts.register_fonts()

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
        topMargin=0.8 * cm,
        bottomMargin=1.4 * cm,   # footer draws at 0.38–0.70 cm; 1.4 cm clears it
        title='IAL Customer Balances FY',
        author='IRAVI AGRO LIFE LLP',
    )

    # ── Paragraph styles ──────────────────────────────────────────────────────
    _W = colors.white

    company_sty  = _ps('CBFYCo',    'DejaVuSans-Bold', 13, TA_CENTER, leading=15)
    title_sty    = _ps('CBFYTitle', 'DejaVuSans-Bold', 10, TA_CENTER, leading=12)
    right_sty    = _ps('CBFYRight', 'DejaVuSans',       7, TA_RIGHT,  leading=9)

    hdr_c = _ps('CBFYHdrC', 'DejaVuSans-Bold', 6, TA_CENTER, color=_W)
    hdr_l = _ps('CBFYHdrL', 'DejaVuSans-Bold', 6, TA_LEFT,   color=_W)
    hdr_r = _ps('CBFYHdrR', 'DejaVuSans-Bold', 6, TA_RIGHT,  color=_W)

    dat_l = _ps('CBFYDatL', 'DejaVuSans', 6, TA_LEFT)
    dat_c = _ps('CBFYDatC', 'DejaVuSans', 6, TA_CENTER)
    dat_r = _ps('CBFYDatR', 'DejaVuSans', 6, TA_RIGHT)

    # Color-specific data-row styles for balance columns (Dr=RED, Cr=GREEN)
    dat_r_red   = _ps('CBFYDatRRed',   'DejaVuSans',      6, TA_RIGHT, color=_RED)
    dat_r_green = _ps('CBFYDatRGreen', 'DejaVuSans',      6, TA_RIGHT, color=_GREEN)

    tot_l = _ps('CBFYTotL', 'DejaVuSans-Bold', 6, TA_LEFT)
    tot_c = _ps('CBFYTotC', 'DejaVuSans-Bold', 6, TA_CENTER)
    tot_r = _ps('CBFYTotR', 'DejaVuSans-Bold', 6, TA_RIGHT)

    # Color-specific TOTAL-row styles for balance columns (Dr=RED, Cr=GREEN)
    tot_r_red   = _ps('CBFYTotRRed',   'DejaVuSans-Bold', 6, TA_RIGHT, color=_RED)
    tot_r_green = _ps('CBFYTotRGreen', 'DejaVuSans-Bold', 6, TA_RIGHT, color=_GREEN)

    # ── Letterhead (3-column header table) ───────────────────────────────────
    today_str = _date.today().strftime('%d-%m-%Y')

    try:
        logo_cell = Image(_LOGO_PATH, width=1.2 * cm, height=1.2 * cm)
    except Exception:
        logo_cell = Spacer(1.2 * cm, 1.2 * cm)

    logo_col_w  = 1.5 * cm
    right_col_w = 3.0 * cm
    ctr_col_w   = _CONTENT_W - logo_col_w - right_col_w

    hdr_tbl = Table(
        [[
            logo_cell,
            [
                Paragraph('IRAVI AGRO LIFE LLP', company_sty),
                Paragraph('CUSTOMER BALANCES',    title_sty),
            ],
            Paragraph(f'Date: {today_str}', right_sty),
        ]],
        colWidths=[logo_col_w, ctr_col_w, right_col_w],
    )
    hdr_tbl.setStyle(TableStyle([
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING',   (0, 0), (-1, -1), 0),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 0),
        ('TOPPADDING',    (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))

    elements: list = [hdr_tbl, Spacer(1, 5)]

    # ── Column widths ─────────────────────────────────────────────────────────
    # Fixed identity columns (left) and summary columns (right).
    # Reduce party/code/city slightly for more FYs so sub-cols stay readable.
    if n_fys <= 2:
        party_w, code_w, city_w = 110.0, 38.0, 48.0
    elif n_fys == 3:
        party_w, code_w, city_w =  95.0, 35.0, 44.0
    else:
        party_w, code_w, city_w =  80.0, 32.0, 40.0

    sno_w     = 20.0
    bal_dr_w  = 58.0
    bal_cr_w  = 58.0

    fixed_w   = sno_w + party_w + code_w + city_w + bal_dr_w + bal_cr_w
    fy_pool   = _CONTENT_W - fixed_w
    sub_col_w = fy_pool / (n_fys * 4) if n_fys > 0 else fy_pool / 4

    col_widths: list[float] = [sno_w, party_w, code_w, city_w]
    for _ in range(n_fys):
        col_widths.extend([sub_col_w] * 4)
    col_widths.extend([bal_dr_w, bal_cr_w])

    n_cols = len(col_widths)

    # ── Two-row header ────────────────────────────────────────────────────────
    # Row 0: fixed identity cols (span 2 rows) + FY group label (span 4 cols)
    #        + summary cols (span 2 rows)
    # Row 1: sub-headers per FY group (Debit / Credit / Credit Notes / Balance)

    row0: list = [
        Paragraph('S.No',  hdr_c),
        Paragraph('Party', hdr_l),
        Paragraph('Code',  hdr_l),
        Paragraph('City',  hdr_l),
    ]
    for fy in fys:
        row0.append(Paragraph(fy, hdr_c))
        row0.extend(['', '', ''])            # SPAN placeholders (cols 2-4 of the group)
    row0.extend([
        Paragraph('Balance Dr', hdr_r),
        Paragraph('Balance Cr', hdr_r),
    ])

    row1: list = ['', '', '', '']            # identity cols spanned from row 0
    for _ in range(n_fys):
        row1.extend([
            Paragraph('Debit (₹)',        hdr_r),
            Paragraph('Credit (₹)',       hdr_r),
            Paragraph('Credit Notes (₹)', hdr_r),
            Paragraph('Balance (₹)',      hdr_r),
        ])
    row1.extend(['', ''])                    # summary cols spanned from row 0

    # ── SPAN commands for the two header rows ─────────────────────────────────
    span_cmds: list = [
        ('SPAN', (0,         0), (0,         1)),   # S.No
        ('SPAN', (1,         0), (1,         1)),   # Party
        ('SPAN', (2,         0), (2,         1)),   # Code
        ('SPAN', (3,         0), (3,         1)),   # City
        ('SPAN', (n_cols - 2, 0), (n_cols - 2, 1)), # Balance Dr
        ('SPAN', (n_cols - 1, 0), (n_cols - 1, 1)), # Balance Cr
    ]
    for i in range(n_fys):
        sc = 4 + i * 4
        span_cmds.append(('SPAN', (sc, 0), (sc + 3, 0)))   # FY group header

    # ── Build data rows ───────────────────────────────────────────────────────
    table_rows: list = [row0, row1]
    # per-cell TEXTCOLOR commands for balance columns only (spec requirement;
    # visual color is also set via color-specific ParagraphStyle instances above
    # because ReportLab ignores TableStyle TEXTCOLOR for Paragraph cells)
    color_cmds: list = []

    for idx, row in enumerate(rows):
        tbl_row = 2 + idx               # 2 header rows precede data rows
        fy_map = {p['fy']: p for p in row['per_fy']}
        dr: list = [
            Paragraph(str(idx + 1),              dat_c),
            Paragraph(row['party'],               dat_l),
            Paragraph(row['code'] or '—',    dat_l),
            Paragraph(row['city'] or '—',    dat_l),
        ]
        for fy_idx, fy in enumerate(fys):
            # Balance (₹) is the 4th sub-col (index 3) within each FY group.
            # FY group for fy_idx starts at col 4 + fy_idx * 4.
            bal_col = 4 + fy_idx * 4 + 3
            p = fy_map.get(fy)
            if p:
                bal = p['balance']
                dr.append(Paragraph(_amt(p['debit']),        dat_r))
                dr.append(Paragraph(_amt(p['credit']),       dat_r))
                dr.append(Paragraph(_amt(p['credit_notes']), dat_r))
                # Balance (₹): Dr → RED, Cr → GREEN, zero/dash → default
                if bal > 0:    # Dr (receivable) → RED
                    dr.append(Paragraph(_bal(bal), dat_r_red))
                    color_cmds.append(
                        ('TEXTCOLOR', (bal_col, tbl_row), (bal_col, tbl_row), _RED))
                elif bal < 0:  # Cr (credit/advance) → GREEN
                    dr.append(Paragraph(_bal(bal), dat_r_green))
                    color_cmds.append(
                        ('TEXTCOLOR', (bal_col, tbl_row), (bal_col, tbl_row), _GREEN))
                else:
                    dr.append(Paragraph(_bal(bal), dat_r))
            else:
                dr.extend([Paragraph('—', dat_r)] * 4)
        # Balance Dr column (n_cols - 2): Dr → RED, else default (dash)
        if row['balance_dr'] > 0:
            dr.append(Paragraph(_bal_dr(row['balance_dr']), dat_r_red))
            color_cmds.append(
                ('TEXTCOLOR', (n_cols - 2, tbl_row), (n_cols - 2, tbl_row), _RED))
        else:
            dr.append(Paragraph(_bal_dr(row['balance_dr']), dat_r))
        # Balance Cr column (n_cols - 1): Cr → GREEN, else default (dash)
        if row['balance_cr'] < 0:
            dr.append(Paragraph(_bal_cr(row['balance_cr']), dat_r_green))
            color_cmds.append(
                ('TEXTCOLOR', (n_cols - 1, tbl_row), (n_cols - 1, tbl_row), _GREEN))
        else:
            dr.append(Paragraph(_bal_cr(row['balance_cr']), dat_r))
        table_rows.append(dr)

    # TOTAL row
    tot_fy_map = {p['fy']: p for p in totals['per_fy']}
    total_row: list = [
        Paragraph('',      tot_c),
        Paragraph('TOTAL', tot_l),
        Paragraph('',      tot_c),
        Paragraph('',      tot_c),
    ]
    for fy in fys:
        p = tot_fy_map.get(fy)
        if p:
            bal = p['balance']
            total_row.append(Paragraph(_amt(p['debit']),        tot_r))
            total_row.append(Paragraph(_amt(p['credit']),       tot_r))
            total_row.append(Paragraph(_amt(p['credit_notes']), tot_r))
            if bal > 0:
                total_row.append(Paragraph(_bal(bal), tot_r_red))
            elif bal < 0:
                total_row.append(Paragraph(_bal(bal), tot_r_green))
            else:
                total_row.append(Paragraph(_bal(bal), tot_r))
        else:
            total_row.extend([Paragraph('—', tot_r)] * 4)
    if totals['balance_dr'] > 0:
        total_row.append(Paragraph(_bal_dr(totals['balance_dr']), tot_r_red))
    else:
        total_row.append(Paragraph(_bal_dr(totals['balance_dr']), tot_r))
    if totals['balance_cr'] < 0:
        total_row.append(Paragraph(_bal_cr(totals['balance_cr']), tot_r_green))
    else:
        total_row.append(Paragraph(_bal_cr(totals['balance_cr']), tot_r))
    table_rows.append(total_row)

    total_row_idx = len(table_rows) - 1   # 0-based

    # TOTAL row TEXTCOLOR commands for balance columns
    for fy_idx, fy in enumerate(fys):
        bal_col = 4 + fy_idx * 4 + 3
        p = tot_fy_map.get(fy)
        if p:
            bal = p['balance']
            if bal > 0:
                color_cmds.append(
                    ('TEXTCOLOR', (bal_col, total_row_idx), (bal_col, total_row_idx), _RED))
            elif bal < 0:
                color_cmds.append(
                    ('TEXTCOLOR', (bal_col, total_row_idx), (bal_col, total_row_idx), _GREEN))
    if totals['balance_dr'] > 0:
        color_cmds.append(
            ('TEXTCOLOR', (n_cols - 2, total_row_idx), (n_cols - 2, total_row_idx), _RED))
    if totals['balance_cr'] < 0:
        color_cmds.append(
            ('TEXTCOLOR', (n_cols - 1, total_row_idx), (n_cols - 1, total_row_idx), _GREEN))

    # ── Table style ───────────────────────────────────────────────────────────
    tbl_cmds: list = span_cmds + [
        # Header rows (0 and 1)
        ('BACKGROUND', (0,             0), (-1, 1),              _HEADER_BG),
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

    # ── Build PDF with footer on every page ───────────────────────────────────
    doc.build(elements, onFirstPage=_draw_footer, onLaterPages=_draw_footer)
    return buffer.getvalue()
