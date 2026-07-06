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
- DejaVuSans TTF (registered via pdf_fonts.register_fonts()) so that
  Rs (U+20B9) and em-dash (U+2014) render without KeyError.
- Letterhead: IAL logo top-left, 'IRAVI AGRO LIFE LLP' + 'SUPPLIER BALANCES'
  centred bold, 'Date: DD-MM-YYYY' top-right.
- Two-row header with repeatRows=2 so both header rows repeat on every page.
  Row 1: S.No | Party | City | <FY XX-XX per FY group> | Balance Dr | Balance Cr
  Row 2: (per FY group) Debit (Rs) | Credit (Rs) | Balance (Rs)
  (NO Code column; NO Credit Notes column — supplier data has neither.)
- Amount cells: Rs<indian-grouped>.00 or -- for zero.
- Balance / Balance Dr / Balance Cr: Rs... Dr / Rs... Cr / -- .
- TOTAL row: #f0f0f0 background, bold.
- Header rows: #1a3c2b (dark green) background, white text.
- Footer every page: registered address + generated-by note (drawn directly on
  canvas so it appears on every page, even partial pages).
  Supplier legend: 'Dr = Debit (payable); Cr = Credit (advance/overpayment).'
  (differs from customer legend which uses 'receivable'/'payable')
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

# -- constants -----------------------------------------------------------------
_HEADER_BG   = colors.HexColor('#1a3c2b')   # brand dark green
_TOTAL_BG    = colors.HexColor('#f0f0f0')   # light grey TOTAL row
_ALT_BG      = colors.HexColor('#fafafa')   # subtle zebra stripe
_CELL_BORDER = colors.HexColor('#cccccc')

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
    'Dr = Debit (payable); Cr = Credit (advance/overpayment).'
)


# -- formatting helpers --------------------------------------------------------

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


# -- footer callback -----------------------------------------------------------

def _draw_footer(canvas, doc):
    """Draw two-line registered-address footer on every page."""
    canvas.saveState()
    canvas.setFont('DejaVuSans', 6)
    canvas.setFillColor(colors.HexColor('#666666'))
    canvas.drawCentredString(_PAGE_W / 2, 0.70 * cm, _FOOTER_LINE1)
    canvas.drawCentredString(_PAGE_W / 2, 0.38 * cm, _FOOTER_LINE2)
    canvas.restoreState()


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
        bottomMargin=1.4 * cm,   # footer draws at 0.38-0.70 cm; 1.4 cm clears it
        title='IAL Supplier Balances FY',
        author='IRAVI AGRO LIFE LLP',
    )

    # -- Paragraph styles ------------------------------------------------------
    _W = colors.white

    company_sty  = _ps('SBFYCo',    'DejaVuSans-Bold', 13, TA_CENTER, leading=15)
    title_sty    = _ps('SBFYTitle', 'DejaVuSans-Bold', 10, TA_CENTER, leading=12)
    right_sty    = _ps('SBFYRight', 'DejaVuSans',       7, TA_RIGHT,  leading=9)

    hdr_c = _ps('SBFYHdrC', 'DejaVuSans-Bold', 6, TA_CENTER, color=_W)
    hdr_l = _ps('SBFYHdrL', 'DejaVuSans-Bold', 6, TA_LEFT,   color=_W)
    hdr_r = _ps('SBFYHdrR', 'DejaVuSans-Bold', 6, TA_RIGHT,  color=_W)

    dat_l = _ps('SBFYDatL', 'DejaVuSans', 6, TA_LEFT)
    dat_c = _ps('SBFYDatC', 'DejaVuSans', 6, TA_CENTER)
    dat_r = _ps('SBFYDatR', 'DejaVuSans', 6, TA_RIGHT)

    tot_l = _ps('SBFYTotL', 'DejaVuSans-Bold', 6, TA_LEFT)
    tot_c = _ps('SBFYTotC', 'DejaVuSans-Bold', 6, TA_CENTER)
    tot_r = _ps('SBFYTotR', 'DejaVuSans-Bold', 6, TA_RIGHT)

    # -- Letterhead (3-column header table) ------------------------------------
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
                Paragraph('SUPPLIER BALANCES',    title_sty),
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

    # -- Two-row header --------------------------------------------------------
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
            Paragraph('Debit (₹)',   hdr_r),
            Paragraph('Credit (₹)',  hdr_r),
            Paragraph('Balance (₹)', hdr_r),
        ])
    row1.extend(['', ''])                # summary cols spanned from row 0

    # -- SPAN commands for the two header rows ---------------------------------
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

    for idx, row in enumerate(rows):
        fy_map = {p['fy']: p for p in row['per_fy']}
        dr: list = [
            Paragraph(str(idx + 1),           dat_c),
            Paragraph(row['party'],            dat_l),
            Paragraph(row['city'] or '—', dat_l),
        ]
        for fy in fys:
            p = fy_map.get(fy)
            if p:
                dr.append(Paragraph(_amt(p['debit']),   dat_r))
                dr.append(Paragraph(_amt(p['credit']),  dat_r))
                dr.append(Paragraph(_bal(p['balance']), dat_r))
            else:
                dr.extend([Paragraph('—', dat_r)] * 3)
        dr.append(Paragraph(_bal_dr(row['balance_dr']), dat_r))
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
            total_row.append(Paragraph(_amt(p['debit']),   tot_r))
            total_row.append(Paragraph(_amt(p['credit']),  tot_r))
            total_row.append(Paragraph(_bal(p['balance']), tot_r))
        else:
            total_row.extend([Paragraph('—', tot_r)] * 3)
    total_row.append(Paragraph(_bal_dr(totals['balance_dr']), tot_r))
    total_row.append(Paragraph(_bal_cr(totals['balance_cr']), tot_r))
    table_rows.append(total_row)

    total_row_idx = len(table_rows) - 1   # 0-based

    # -- Table style -----------------------------------------------------------
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
    ]

    # Zebra stripe on alternate data rows (starting from the first data row = index 2)
    for i in range(2, total_row_idx):
        if (i - 2) % 2 == 1:
            tbl_cmds.append(('BACKGROUND', (0, i), (-1, i), _ALT_BG))

    data_tbl = Table(table_rows, colWidths=col_widths, repeatRows=2)
    data_tbl.setStyle(TableStyle(tbl_cmds))
    elements.append(data_tbl)

    # -- Build PDF with footer on every page -----------------------------------
    doc.build(elements, onFirstPage=_draw_footer, onLaterPages=_draw_footer)
    return buffer.getvalue()
