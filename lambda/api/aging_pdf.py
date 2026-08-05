"""
aging_pdf — shared landscape A4 PDF renderer for the Customer Aging and
Supplier Aging reports, built on top of aging.compute_aging()'s output.

Public surface
--------------
render_aging_pdf(payload, *, title, party_label, last_label, negative_suffix,
                  positive_color, negative_color) -> bytes
    payload : {
        'rows':  [ {party, city, bucket1, bucket2, bucket3, net,
                    last_receipt_date, last_receipt_amount,
                    last_receipt_age}, ... ],   # 'city' attached by the
                                                  # caller (handler.py) after
                                                  # aging.compute_aging()
        'as_of': 'YYYY-MM-DD',
        'age1': int, 'age2': int, 'age3': int,
    }
    title            : 'Customer Aging' | 'Supplier Aging'
    party_label      : 'Party' | 'Supplier'
    last_label       : 'Receipt' | 'Payment'
    negative_suffix  : 'Cr' (customer, net <= 0) | 'Dr' (supplier, net <= 0)
    positive_color / negative_color : reportlab colors.Color for net > 0 /
                                       net <= 0

Reused by
---------
  customer_aging_pdf.py — render_customer_aging_pdf(data)
  supplier_aging_pdf.py — render_supplier_aging_pdf(data)

Design mirrors the house report-PDF convention already used by
customer_balances_fy_pdf.py / stocks_expiry_pdf.py: landscape A4, 1cm
margins, shared letterhead header/footer repeating on every page (via
letterhead.draw_header/draw_footer canvas callbacks), single-row GREEN
header band with white bold text, repeatRows=1, zebra data rows, TOTAL row
background #f0f0f0 bold. Helvetica/Helvetica-Bold body font; DejaVuSans is
registered only for the inline rupee-glyph token (`_RS`), exactly like the
other report renderers in this package.

Net Amount cell text — ported VERBATIM from the task spec (deliberately NOT
the FY-report's Dr/Cr-both-sides convention used by customer_balances_fy_pdf.py):
    net <= 0  -> '{abs(net)} {negative_suffix}'  (colored negative_color)
    net > 0   -> '{net}'                          (colored positive_color,
                                                     no suffix)

Formatting note: the task's prose uses an en-dash ('0–30 days') and an
em-dash ('—') as placeholders; this renderer uses plain ASCII hyphens
throughout instead (both in bucket-range header labels and in blank/None
cells) — a purely cosmetic, intentional deviation matching this package's
established convention (see customer_balances_fy_pdf.py's 2026-07-20 note on
replacing em-dash placeholders with a hyphen) and avoiding any dependency on
non-ASCII glyph handling for placeholder text.
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
    return _RS + ','.join(groups) + '.' + dec_str


def _amt(value) -> str:
    """Rupee string for a non-zero/non-None value, else a hyphen placeholder."""
    if value is None or value == 0:
        return '-'
    return _fmt_inr(value)


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


# ── public API ────────────────────────────────────────────────────────────────

def render_aging_pdf(
    payload: dict,
    *,
    title: str,
    party_label: str,
    last_label: str,
    negative_suffix: str,
    positive_color=colors.HexColor('#cc0000'),
    negative_color=colors.HexColor('#1a6e35'),
) -> bytes:
    rows  = payload.get('rows') or []
    as_of = payload['as_of']
    age1  = payload['age1']
    age2  = payload['age2']
    age3  = payload['age3']

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

    title_sty = _ps('AGTitle', _BOLD, 12, TA_LEFT, color=letterhead.GREEN)
    right_sty = _ps('AGRight', _BASE, 8, TA_RIGHT, color=letterhead.MUTED)
    subtitle_sty = _ps('AGSubtitle', _BASE, 8, TA_LEFT, color=letterhead.MUTED)

    hdr_sty = {
        TA_LEFT: _ps('AGHdrL', _BOLD, 6.5, TA_LEFT, color=_W),
        TA_CENTER: _ps('AGHdrC', _BOLD, 6.5, TA_CENTER, color=_W),
        TA_RIGHT: _ps('AGHdrR', _BOLD, 6.5, TA_RIGHT, color=_W),
    }
    dat_sty = {
        TA_LEFT: _ps('AGDatL', _BASE, 6.5, TA_LEFT),
        TA_CENTER: _ps('AGDatC', _BASE, 6.5, TA_CENTER),
        TA_RIGHT: _ps('AGDatR', _BASE, 6.5, TA_RIGHT),
    }
    dat_r_pos = _ps('AGDatRPos', _BASE, 6.5, TA_RIGHT, color=positive_color)
    dat_r_neg = _ps('AGDatRNeg', _BASE, 6.5, TA_RIGHT, color=negative_color)

    tot_sty = {
        TA_LEFT: _ps('AGTotL', _BOLD, 6.5, TA_LEFT),
        TA_CENTER: _ps('AGTotC', _BOLD, 6.5, TA_CENTER),
        TA_RIGHT: _ps('AGTotR', _BOLD, 6.5, TA_RIGHT),
    }
    tot_r_pos = _ps('AGTotRPos', _BOLD, 6.5, TA_RIGHT, color=positive_color)

    # ── Letterhead + report title / meta rows ─────────────────────────────────
    title_row = Table(
        [[Paragraph(title.upper(), title_sty), Paragraph(f'Aged as of: {_fmt_date(as_of)}', right_sty)]],
        colWidths=[_CONTENT_W * 0.75, _CONTENT_W * 0.25],
    )
    title_row.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))

    buckets_text = f'Buckets: 0-{age1} / {age1 + 1}-{age2} / {age2 + 1}-{age3} days'

    # Header is drawn on the canvas (letterhead.draw_header, every page) — NOT
    # added here as a flowable, to avoid double-rendering it on page 1.
    elements: list = [
        title_row,
        Spacer(1, 2),
        Paragraph(buckets_text, subtitle_sty),
        Spacer(1, 5),
    ]

    # ── Column definitions + widths ────────────────────────────────────────────
    columns = [
        (party_label, 1.3, TA_LEFT),
        ('City', 0.9, TA_LEFT),
        (f'0-{age1} days', 0.8, TA_RIGHT),
        (f'{age1 + 1}-{age2} days', 0.85, TA_RIGHT),
        (f'{age2 + 1}-{age3} days', 0.85, TA_RIGHT),
        ('Net Amount', 0.95, TA_RIGHT),
        (f'Last {last_label} Date', 0.85, TA_CENTER),
        (f'Last {last_label} Age', 0.7, TA_RIGHT),
        (f'Last {last_label} Amt', 0.95, TA_RIGHT),
    ]
    weight_total = sum(w for _, w, _ in columns)
    col_widths = [_CONTENT_W * (w / weight_total) for _, w, _ in columns]

    table_rows: list = [
        [Paragraph(label, hdr_sty[align]) for label, _, align in columns]
    ]

    total_b1 = total_b2 = total_b3 = 0.0
    total_positive_net = 0.0

    for row in rows:
        net = row['net']
        total_b1 += row['bucket1']
        total_b2 += row['bucket2']
        total_b3 += row['bucket3']

        dr: list = [
            Paragraph(row['party'] or '-', dat_sty[TA_LEFT]),
            Paragraph(row.get('city') or '-', dat_sty[TA_LEFT]),
            Paragraph(_amt(row['bucket1']), dat_sty[TA_RIGHT]),
            Paragraph(_amt(row['bucket2']), dat_sty[TA_RIGHT]),
            Paragraph(_amt(row['bucket3']), dat_sty[TA_RIGHT]),
        ]
        if net <= 0:
            dr.append(Paragraph(f'{_fmt_inr(abs(net))} {negative_suffix}', dat_r_neg))
        else:
            total_positive_net += net
            dr.append(Paragraph(_fmt_inr(net), dat_r_pos))
        dr.append(Paragraph(_fmt_date(row['last_receipt_date']), dat_sty[TA_CENTER]))
        age = row.get('last_receipt_age')
        dr.append(Paragraph(str(age) if age is not None else '-', dat_sty[TA_RIGHT]))
        dr.append(Paragraph(_amt(row.get('last_receipt_amount')), dat_sty[TA_RIGHT]))
        table_rows.append(dr)

    # TOTAL row: bucket sums + sum of POSITIVE nets only (matches the UI's
    # "Outstanding Amount" tile), rest blank.
    total_row: list = [
        Paragraph('TOTAL', tot_sty[TA_LEFT]),
        Paragraph('', tot_sty[TA_LEFT]),
        Paragraph(_amt(total_b1), tot_sty[TA_RIGHT]),
        Paragraph(_amt(total_b2), tot_sty[TA_RIGHT]),
        Paragraph(_amt(total_b3), tot_sty[TA_RIGHT]),
        Paragraph(_fmt_inr(total_positive_net) if total_positive_net else '-', tot_r_pos),
        Paragraph('', tot_sty[TA_CENTER]),
        Paragraph('', tot_sty[TA_RIGHT]),
        Paragraph('', tot_sty[TA_RIGHT]),
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
