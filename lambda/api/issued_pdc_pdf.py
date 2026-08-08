"""
issued_pdc_pdf — PDF renderer for the Issued PDC report (GET /pdc/pdf).

Public surface
--------------
render_issued_pdc_pdf(data: dict) -> bytes
    data    : {
        'rows': [
            {po_no, po_date, company_name, technical_name, brand,
             credit_days, qty, rate, gross, gst, amount, disc, adv, bal,
             pdc_amt, pdc_date, id}, ...
        ],
        'supplier': str|None, 'product': str|None,
        'pdc_from': 'YYYY-MM-DD'|None, 'pdc_to': 'YYYY-MM-DD'|None,
    } — built by handler._handle_pdc_pdf from procurement.pdc (joined to
    procurement.supplier_companies / procurement.technicals), server-side
    filtered on supplier/product/pdc_from/pdc_to. `id` is carried through
    purely as a defensive tertiary sort key for the month-grouping below —
    it is never rendered as a column.
    returns : raw PDF bytes (landscape A4)

Design mirrors the house report-PDF convention (customer_balances_fy_pdf.py /
stocks_expiry_pdf.py / aging_pdf.py / transactions_register_pdf.py):
landscape A4, shared letterhead header/footer repeating on every page,
single-row GREEN header band with white bold text, repeatRows=1, zebra data
rows, TOTAL row background #f0f0f0 bold. Helvetica/Helvetica-Bold body font;
DejaVuSans registered only for the inline rupee-glyph token (`_RS`), used
now ONLY in the subtitle ("All amounts in Rs" line) — see the "column
sizing" note below.

16 columns (mirroring the procurement PDC screen):
  PO | Date | Supplier | Product | Brand | Cr.Days (right) | Qty (right) |
  Rate (right) | Gross (right) | GST (right) | Amount (right) | Disc (right)
  | Adv (right) | Bal (right) | PDC Amt (right) | PDC Date

Month-wise grouping (added — see the task's "Issued PDC PDF, grouped
month-wise" spec)
--------------------------------------------------------------------------
The body is no longer one flat table. Rows are grouped by `pdc_date[:7]`
('YYYY-MM'), rendered as a sequence of month sections in chronological
ASCENDING order, each with its own repeating 16-column header row
(`repeatRows=1`) and its own subtotal row at the bottom (7 money columns,
styled distinctly lighter than the grand TOTAL row). Rows with a
NULL/blank `pdc_date` are grouped last, under the heading "No PDC Date" —
never dropped. Within a month, rows are sorted `pdc_date ASC, po_date DESC
NULLS LAST, id DESC` (`_pdc_row_cmp`) — a DEFENSIVE sort applied here in
the renderer regardless of the SQL's own ORDER BY (`handler.py`'s
`_handle_pdc_pdf` also orders `pdc_date ASC NULLS LAST, po_date DESC NULLS
LAST, id DESC` for the same rows, so in practice the two agree — this is
belt-and-suspenders, not a correction of the SQL). A single grand TOTAL row
(identical in shape/position to the pre-grouping design) closes the
document, across ALL rows regardless of month.

`_compute_layout()` is still called EXACTLY ONCE, over the full row set
(and the grand totals) — not per month — so every month's table shares the
identical `col_widths`/font size/padding and lines up perfectly with its
neighbours. The 'po' column's width measurement additionally accounts for
the per-month subtotal label strings (e.g. "Oct-2026 Total"), since that
column is one of the 13 unbreakable columns and subtotal labels are new
text the pre-grouping design never had to measure.

Column sizing (measured, not guessed)
--------------------------------------
Every column here except Supplier/Product/Brand holds a value that can NEVER
wrap — dates, PO numbers, and rupee amounts are single unbreakable tokens
(no spaces), and a reportlab Paragraph cannot break an unbreakable token; it
just overflows the cell instead. A fixed weight-based column-width split
(the previous design) therefore reliably overflowed on real data (long
dates, lakh/crore-scale amounts).

`_measure()` / `_compute_layout()` instead measure, PER RENDER, the actual
`pdfmetrics.stringWidth` of every header label, every data cell, the
TOTAL row cell, and every per-month subtotal label in this specific
payload, at the exact font/size that cell will be drawn with:
  - The 13 unbreakable columns (PO, Date, PDC Date, Cr.Days, Qty, Rate, and
    the 7 money columns) always get AT LEAST their measured required width.
  - Supplier/Product/Brand wrap on spaces, so they get a measured MINIMUM
    (header label vs. the longest single word in the column — the true wrap
    floor) and then absorb whatever width is left over, split across the
    three proportional to their average full-cell width (so Product, which
    tends to hold the longest text, gets the biggest share of the slack).
If the 13 unbreakable columns' own required widths already exceed the page
at the default 7pt/3pt-padding/1cm-margin layout, `_compute_layout()`
degrades — in order, re-measuring after each step — cell padding 3pt->2pt,
body font 7pt->6.5pt->6pt, then side margin 1.0cm->0.75cm — until everything
fits, exactly as this package's other report renderers do NOT need to (they
have far fewer / narrower unbreakable columns) but this one, with 16 columns
including 7 money columns, sometimes does.

Formatting: Date/PDC Date as DD-MM-YYYY; Gross/GST/Amount/Disc/Adv/Bal/PDC
Amt as Indian-grouped 2dp numbers with NO per-cell rupee glyph (the unit is
stated once, in the subtitle, via `_RS` — this reclaims ~4-5pt per money
cell x 7 columns, which is what makes the unbreakable columns fit without
crushing Supplier/Product/Brand to unreadable widths); the money column
headers are therefore plain 'Gross'/'GST'/'Amount'/'Disc'/'Adv'/'Bal'/
'PDC Amt' (no '(Rs)' suffix). Qty/Rate as plain numbers (no currency
symbol, matching the procurement UI). None -> hyphen placeholder '-'.
"""

from __future__ import annotations

import logging
from datetime import datetime
from functools import cmp_to_key
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

import letterhead

_LOG = logging.getLogger('issued_pdc_pdf')

# ── constants ─────────────────────────────────────────────────────────────────
_TOTAL_BG       = colors.HexColor('#f0f0f0')
_MONTH_TOTAL_BG = colors.HexColor('#f7f7f7')  # lighter than _TOTAL_BG — per-month subtotal
_MONTH_TOTAL_FG = colors.HexColor('#444444')  # muted text — distinguishes from the grand TOTAL
_ALT_BG      = colors.HexColor('#fafafa')
_CELL_BORDER = colors.HexColor('#cccccc')

_PAGE_W, _PAGE_H = landscape(A4)
_MARGIN       = 1.0 * cm
_MARGIN_TIGHT = 0.75 * cm

_BASE_FONT = letterhead.BASE_FONT
_BOLD_FONT = letterhead.BOLD_FONT

# Rupee token — Helvetica-primary; DejaVuSans is registered only for this
# glyph. Used ONLY in the subtitle ("All amounts in <_RS>") — data/TOTAL
# cells are plain Helvetica numbers now (see module docstring).
_RS = letterhead.register_fonts()

_MONTH_NAMES = [
    'January', 'February', 'March', 'April', 'May', 'June',
    'July', 'August', 'September', 'October', 'November', 'December',
]


# ── formatting helpers ────────────────────────────────────────────────────────

def _fmt_inr(value: float) -> str:
    """Format |value| as Indian-grouped rupees with NO currency glyph, e.g.
    '1,23,456.00' / '-1,23,456.00'. The unit (Rs) is stated once in the
    report subtitle instead of on every cell — see module docstring."""
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
    return sign + ','.join(groups) + '.' + dec_str


def _amt(value) -> str:
    """Rupee-free amount string for a non-None value (including 0), else a
    hyphen placeholder."""
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


# ── month-grouping helpers ────────────────────────────────────────────────────

def _month_key(pdc_date) -> str | None:
    """'YYYY-MM-DD' -> 'YYYY-MM'; None/blank -> None (the "No PDC Date"
    bucket)."""
    if not pdc_date:
        return None
    return pdc_date[:7]


def _month_heading(month_key: str | None) -> str:
    """'YYYY-MM' -> 'October-2026' (full English month name, explicitly
    mapped — never locale-dependent strftime, which could break in the
    Lambda runtime). None -> 'No PDC Date'."""
    if month_key is None:
        return 'No PDC Date'
    year_str, month_str = month_key.split('-')
    month_num = int(month_str)
    return f'{_MONTH_NAMES[month_num - 1]}-{year_str}'


def _month_subtotal_label(month_key: str | None) -> str:
    """'YYYY-MM' -> 'Oct-2026 Total'; None -> 'No PDC Date Total'."""
    if month_key is None:
        return 'No PDC Date Total'
    year_str, month_str = month_key.split('-')
    month_num = int(month_str)
    return f'{_MONTH_NAMES[month_num - 1][:3]}-{year_str} Total'


def _pdc_row_cmp(a: dict, b: dict) -> int:
    """Defensive in-renderer sort comparator — pdc_date ASC, then po_date
    DESC NULLS LAST, then id DESC. Applied within each month group
    regardless of the caller's SQL ORDER BY (see module docstring)."""
    a_pdc, b_pdc = a.get('pdc_date') or '', b.get('pdc_date') or ''
    if a_pdc != b_pdc:
        return -1 if a_pdc < b_pdc else 1

    a_po, b_po = a.get('po_date'), b.get('po_date')
    if a_po != b_po:
        if a_po is None:
            return 1
        if b_po is None:
            return -1
        return -1 if a_po > b_po else 1

    a_id, b_id = a.get('id') or 0, b.get('id') or 0
    if a_id != b_id:
        return -1 if a_id > b_id else 1
    return 0


def _group_rows_by_month(rows: list) -> list[tuple]:
    """rows -> [(month_key, sorted_rows), ...] in chronological ASCENDING
    order of month_key, with the None ("No PDC Date") group, if present,
    always last."""
    groups: dict = {}
    for row in rows:
        key = _month_key(row.get('pdc_date'))
        groups.setdefault(key, []).append(row)

    for key, group_rows in groups.items():
        groups[key] = sorted(group_rows, key=cmp_to_key(_pdc_row_cmp))

    ordered_keys = sorted(k for k in groups if k is not None)
    ordered = [(k, groups[k]) for k in ordered_keys]
    if None in groups:
        ordered.append((None, groups[None]))
    return ordered


# ── paragraph style factory ───────────────────────────────────────────────────

def _ps(name: str, font: str, size: float, align: int,
        color=colors.black, leading: float | None = None,
        keep_with_next: bool = False) -> ParagraphStyle:
    return ParagraphStyle(
        name,
        fontName=font,
        fontSize=size,
        alignment=align,
        leading=leading or (size + 1),
        textColor=color,
        keepWithNext=keep_with_next,
    )


def _draw_header_footer(canvas, doc):
    """Combined onFirstPage/onLaterPages callback — draws the repeating
    letterhead header and shared footer on every page."""
    letterhead.draw_header(canvas, doc)
    letterhead.draw_footer(canvas, doc)


# ── column definitions ────────────────────────────────────────────────────────
# key, header label, alignment, wrap (True -> long-text column that can break
# on spaces), money (True -> summed into the TOTAL row), cell(row) -> str.
_COL_SPECS = [
    dict(key='po', header='PO', align=TA_LEFT, wrap=False, money=False,
         cell=lambda r: r.get('po_no') or '-'),
    dict(key='date', header='Date', align=TA_CENTER, wrap=False, money=False,
         cell=lambda r: _fmt_date(r.get('po_date'))),
    dict(key='supplier', header='Supplier', align=TA_LEFT, wrap=True, money=False,
         cell=lambda r: r.get('company_name') or '-'),
    dict(key='product', header='Product', align=TA_LEFT, wrap=True, money=False,
         cell=lambda r: r.get('technical_name') or '-'),
    dict(key='brand', header='Brand', align=TA_LEFT, wrap=True, money=False,
         cell=lambda r: r.get('brand') or '-'),
    dict(key='cr_days', header='Cr.Days', align=TA_RIGHT, wrap=False, money=False,
         cell=lambda r: _fmt_int(r.get('credit_days'))),
    dict(key='qty', header='Qty', align=TA_RIGHT, wrap=False, money=False,
         cell=lambda r: _fmt_num(r.get('qty'))),
    dict(key='rate', header='Rate', align=TA_RIGHT, wrap=False, money=False,
         cell=lambda r: _fmt_num(r.get('rate'))),
    dict(key='gross', header='Gross', align=TA_RIGHT, wrap=False, money=True,
         cell=lambda r: _amt(r.get('gross'))),
    dict(key='gst', header='GST', align=TA_RIGHT, wrap=False, money=True,
         cell=lambda r: _amt(r.get('gst'))),
    dict(key='amount', header='Amount', align=TA_RIGHT, wrap=False, money=True,
         cell=lambda r: _amt(r.get('amount'))),
    dict(key='disc', header='Disc', align=TA_RIGHT, wrap=False, money=True,
         cell=lambda r: _amt(r.get('disc'))),
    dict(key='adv', header='Adv', align=TA_RIGHT, wrap=False, money=True,
         cell=lambda r: _amt(r.get('adv'))),
    dict(key='bal', header='Bal', align=TA_RIGHT, wrap=False, money=True,
         cell=lambda r: _amt(r.get('bal'))),
    dict(key='pdc_amt', header='PDC Amt', align=TA_RIGHT, wrap=False, money=True,
         cell=lambda r: _amt(r.get('pdc_amt'))),
    dict(key='pdc_date', header='PDC Date', align=TA_CENTER, wrap=False, money=False,
         cell=lambda r: _fmt_date(r.get('pdc_date'))),
]

_MONEY_KEYS = [spec['key'] for spec in _COL_SPECS if spec['money']]

# Degradation ladder — tried in order, re-measured at each step, until the 13
# unbreakable columns' required widths + the 3 wrap columns' minimum widths
# fit the page. See module docstring.
_DEGRADE_STEPS = [
    # (font_size, cell_padding, side_margin)
    (7.0, 3, _MARGIN),
    (7.0, 2, _MARGIN),
    (6.5, 2, _MARGIN),
    (6.0, 2, _MARGIN),
    (6.0, 2, _MARGIN_TIGHT),
]


def _summary_cell_text(spec: dict, label: str, totals: dict, has_rows: bool) -> str:
    """Summary-row (grand TOTAL or per-month subtotal) text for one column —
    `label` in PO, summed amount in the 7 money columns (only when there is
    at least one row), blank everywhere else."""
    if spec['key'] == 'po':
        return label
    if spec['money']:
        return _amt(totals.get(spec['key'])) if has_rows else '-'
    return ''


def _total_cell_text(spec: dict, totals: dict, has_rows: bool) -> str:
    """Backward-compatible wrapper — the grand TOTAL row's label is always
    'TOTAL'."""
    return _summary_cell_text(spec, 'TOTAL', totals, has_rows)


def _measure(rows: list, totals: dict, font_size: float, pad: float,
             extra_po_labels: list | None = None):
    """Required width for the 13 unbreakable columns, and (minimum, 'want')
    width for the 3 wrap columns, measured over THIS render's actual header
    label / data cells / TOTAL cell / per-month subtotal labels, at
    `font_size`/`pad`. `extra_po_labels` (the per-month subtotal label
    strings, e.g. 'Oct-2026 Total') are folded into the 'po' column's width
    so subtotal labels never overflow it — see module docstring."""
    nonwrap: dict = {}
    wrap_min: dict = {}
    wrap_want: dict = {}
    has_rows = bool(rows)
    extra_po_labels = extra_po_labels or []

    for spec in _COL_SPECS:
        header_w = pdfmetrics.stringWidth(spec['header'], _BOLD_FONT, font_size)

        if spec['wrap']:
            longest_word_w = 0.0
            cell_w_sum = 0.0
            for row in rows:
                text = spec['cell'](row)
                for word in text.split():
                    longest_word_w = max(longest_word_w,
                                          pdfmetrics.stringWidth(word, _BASE_FONT, font_size))
                cell_w_sum += pdfmetrics.stringWidth(text, _BASE_FONT, font_size)
            avg_cell_w = (cell_w_sum / len(rows)) if rows else 0.0
            floor = max(header_w, longest_word_w) + 2 * pad
            wrap_min[spec['key']] = floor
            wrap_want[spec['key']] = max(floor, avg_cell_w + 2 * pad)
        else:
            data_w = max(
                [pdfmetrics.stringWidth(spec['cell'](row), _BASE_FONT, font_size)
                 for row in rows] or [0.0]
            )
            total_text = _total_cell_text(spec, totals, has_rows)
            total_w = (pdfmetrics.stringWidth(total_text, _BOLD_FONT, font_size)
                       if total_text else 0.0)
            extra_w = 0.0
            if spec['key'] == 'po' and extra_po_labels:
                extra_w = max(
                    pdfmetrics.stringWidth(label, _BOLD_FONT, font_size)
                    for label in extra_po_labels
                )
            nonwrap[spec['key']] = max(header_w, data_w, total_w, extra_w) + 2 * pad

    return nonwrap, wrap_min, wrap_want


def _compute_layout(rows: list, totals: dict, extra_po_labels: list | None = None):
    """Pick the least-aggressive degradation step that fits, then distribute
    any leftover width across Supplier/Product/Brand proportional to their
    measured 'want'. Returns (col_widths_in_COL_SPECS_order, font_size, pad,
    margin, content_w). Called EXACTLY ONCE per render, over the full row
    set (+ grand totals + per-month subtotal labels) — never per month."""
    chosen = None
    for font_size, pad, margin in _DEGRADE_STEPS:
        content_w = _PAGE_W - 2 * margin
        nonwrap, wrap_min, wrap_want = _measure(rows, totals, font_size, pad, extra_po_labels)
        nonwrap_total = sum(nonwrap.values())
        wrap_min_total = sum(wrap_min.values())
        remaining = content_w - nonwrap_total
        if remaining >= wrap_min_total:
            chosen = (font_size, pad, margin, content_w, nonwrap, wrap_min, wrap_want, remaining)
            break

    if chosen is None:
        # Every degradation step exhausted and the 13 unbreakable columns'
        # OWN required widths (plus the wrap columns' hard word-wrap minimums)
        # still exceed the page — genuinely unfittable content (this is a
        # data-outlier case, e.g. a single 30-40+ character unbreakable token
        # in Supplier/Product/Brand; see module docstring). Per the house
        # policy for this case: the 13 unbreakable columns keep their own
        # required width (they cannot shrink without overflowing their own
        # cell), the 3 wrap columns are clamped to their hard minimum (their
        # own word-wrap floor, not shrunk further), and — since that
        # necessarily means the table is wider than the page — we log a
        # warning (CloudWatch) instead of silently shipping the overflow.
        # This is NOT scaled/silenced: it is a bounded, reported edge case.
        font_size, pad, margin = _DEGRADE_STEPS[-1]
        content_w = _PAGE_W - 2 * margin
        nonwrap, wrap_min, wrap_want = _measure(rows, totals, font_size, pad, extra_po_labels)
        nonwrap_total = sum(nonwrap.values())
        wrap_min_total = sum(wrap_min.values())
        total = nonwrap_total + wrap_min_total
        if total > content_w:
            _LOG.warning(
                'issued_pdc_pdf: content genuinely does not fit landscape A4 even at the '
                'smallest degradation step (font=%.1fpt pad=%.1fpt margin=%.2fcm) — table '
                'width %.1fpt exceeds page content width %.1fpt by %.1fpt. Unbreakable '
                'columns: %s. Wrap columns clamped to their minimum: %s.',
                font_size, pad, margin / cm, total, content_w, total - content_w,
                nonwrap, wrap_min,
            )
        remaining = content_w - nonwrap_total
        chosen = (font_size, pad, margin, content_w, nonwrap, wrap_min, wrap_want, remaining)

    font_size, pad, margin, content_w, nonwrap, wrap_min, wrap_want, remaining = chosen
    wrap_min_total = sum(wrap_min.values())
    extra = max(0.0, remaining - wrap_min_total)
    want_total = sum(wrap_want.values())
    wrap_widths = {}
    for key, min_w in wrap_min.items():
        share = (wrap_want[key] / want_total) if want_total > 0 else (1.0 / len(wrap_min))
        wrap_widths[key] = min_w + extra * share

    col_widths = [wrap_widths[spec['key']] if spec['wrap'] else nonwrap[spec['key']]
                  for spec in _COL_SPECS]
    return col_widths, font_size, pad, margin, content_w


# ── row/table-building helpers ────────────────────────────────────────────────

def _header_row_cells(hdr_align: dict) -> list:
    return [Paragraph(spec['header'], hdr_align[spec['align']]) for spec in _COL_SPECS]


def _data_row_cells(row: dict, dat_align: dict) -> list:
    return [Paragraph(spec['cell'](row), dat_align[spec['align']]) for spec in _COL_SPECS]


def _summary_row_cells(label: str, totals: dict, align_styles: dict, has_rows: bool) -> list:
    """One summary row (grand TOTAL or a per-month subtotal) — `align_styles`
    is a dict of {TA_LEFT/TA_CENTER/TA_RIGHT: ParagraphStyle}."""
    row = []
    for spec in _COL_SPECS:
        if spec['wrap']:
            row.append(Paragraph('', align_styles[TA_LEFT]))
        else:
            text = _summary_cell_text(spec, label, totals, has_rows)
            row.append(Paragraph(text, align_styles[spec['align']]))
    return row


def _month_totals(rows: list) -> dict:
    return {key: sum(row.get(key) or 0.0 for row in rows) for key in _MONEY_KEYS}


# ── public API ────────────────────────────────────────────────────────────────

def render_issued_pdc_pdf(data: dict) -> bytes:
    rows = data.get('rows') or []
    supplier = data.get('supplier')
    product = data.get('product')
    pdc_from = data.get('pdc_from')
    pdc_to = data.get('pdc_to')

    grand_totals = {key: sum(row.get(key) or 0.0 for row in rows) for key in _MONEY_KEYS}
    grouped = _group_rows_by_month(rows)
    subtotal_labels = [_month_subtotal_label(month_key) for month_key, _ in grouped]

    # Shared layout — computed ONCE over the full row set + grand totals +
    # every month's subtotal label — then reused verbatim for every month's
    # table and the grand-total row. See module docstring.
    col_widths, font_size, pad, margin, content_w = _compute_layout(
        rows, grand_totals, subtotal_labels)

    buffer = BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=margin,
        rightMargin=margin,
        topMargin=letterhead.HEADER_TOP_PAD + letterhead.HEADER_HEIGHT + 0.3 * cm,
        bottomMargin=1.4 * cm,
        title='IAL Issued PDC',
        author='IRAVI AGRO LIFE LLP',
    )

    _W = colors.white

    title_sty = _ps('PDCTitle', _BOLD_FONT, 12, TA_LEFT, color=letterhead.GREEN)
    subtitle_sty = _ps('PDCSubtitle', _BASE_FONT, 8, TA_LEFT, color=letterhead.MUTED)
    month_heading_sty = _ps(
        'PDCMonthHeading', _BOLD_FONT, max(font_size + 2.5, 10.5), TA_LEFT,
        color=letterhead.GREEN, keep_with_next=True,
    )

    hdr_align = {
        TA_LEFT: _ps('PDCHdrL', _BOLD_FONT, font_size, TA_LEFT, color=_W),
        TA_CENTER: _ps('PDCHdrC', _BOLD_FONT, font_size, TA_CENTER, color=_W),
        TA_RIGHT: _ps('PDCHdrR', _BOLD_FONT, font_size, TA_RIGHT, color=_W),
    }
    dat_align = {
        TA_LEFT: _ps('PDCDatL', _BASE_FONT, font_size, TA_LEFT),
        TA_CENTER: _ps('PDCDatC', _BASE_FONT, font_size, TA_CENTER),
        TA_RIGHT: _ps('PDCDatR', _BASE_FONT, font_size, TA_RIGHT),
    }
    tot_align = {
        TA_LEFT: _ps('PDCTotL', _BOLD_FONT, font_size, TA_LEFT),
        TA_CENTER: _ps('PDCTotC', _BOLD_FONT, font_size, TA_CENTER),
        TA_RIGHT: _ps('PDCTotR', _BOLD_FONT, font_size, TA_RIGHT),
    }
    # Per-month subtotal row — distinctly lighter than the grand TOTAL row
    # (muted gray text, lighter background — see _MONTH_TOTAL_BG/_FG).
    subtot_align = {
        TA_LEFT: _ps('PDCSubtotL', _BOLD_FONT, font_size, TA_LEFT, color=_MONTH_TOTAL_FG),
        TA_CENTER: _ps('PDCSubtotC', _BOLD_FONT, font_size, TA_CENTER, color=_MONTH_TOTAL_FG),
        TA_RIGHT: _ps('PDCSubtotR', _BOLD_FONT, font_size, TA_RIGHT, color=_MONTH_TOTAL_FG),
    }

    title_row = Table(
        [[Paragraph('ISSUED PDC', title_sty)]],
        colWidths=[content_w],
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
    unit_note = f'All amounts in {_RS}'
    subtitle_text = unit_note + ' | ' + (' | '.join(filter_parts) if filter_parts else 'All records')

    # Header is drawn on the canvas (letterhead.draw_header, every page) — NOT
    # added here as a flowable, to avoid double-rendering it on page 1.
    elements: list = [
        title_row,
        Spacer(1, 2),
        Paragraph(subtitle_text, subtitle_sty),
        Spacer(1, 5),
    ]

    def _base_table_style(total_row_idx: int, is_total_bold: bool = True) -> list:
        cmds = [
            ('BACKGROUND', (0, 0), (-1, 0), letterhead.GREEN),
            ('FONTSIZE', (0, 0), (-1, -1), font_size),
            ('GRID', (0, 0), (-1, -1), 0.3, _CELL_BORDER),
            ('TOPPADDING', (0, 0), (-1, -1), 2),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
            ('LEFTPADDING', (0, 0), (-1, -1), pad),
            ('RIGHTPADDING', (0, 0), (-1, -1), pad),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]
        for i in range(1, total_row_idx):
            if (i - 1) % 2 == 1:
                cmds.append(('BACKGROUND', (0, i), (-1, i), _ALT_BG))
        return cmds

    if not rows:
        # Empty-state: unchanged behaviour — header row + a single TOTAL row
        # (dashes, has_rows=False).
        table_rows = [_header_row_cells(hdr_align)]
        table_rows.append(_summary_row_cells('TOTAL', grand_totals, tot_align, has_rows=False))
        total_row_idx = len(table_rows) - 1

        tbl_cmds = _base_table_style(total_row_idx)
        tbl_cmds.append(('BACKGROUND', (0, total_row_idx), (-1, total_row_idx), _TOTAL_BG))

        data_tbl = Table(table_rows, colWidths=col_widths, repeatRows=1)
        data_tbl.setStyle(TableStyle(tbl_cmds))
        elements.append(data_tbl)
    else:
        for idx, (month_key, month_rows) in enumerate(grouped):
            elements.append(Spacer(1, 10 if idx > 0 else 2))
            elements.append(Paragraph(_month_heading(month_key), month_heading_sty))
            elements.append(Spacer(1, 3))

            table_rows = [_header_row_cells(hdr_align)]
            for row in month_rows:
                table_rows.append(_data_row_cells(row, dat_align))
            month_totals = _month_totals(month_rows)
            table_rows.append(_summary_row_cells(
                _month_subtotal_label(month_key), month_totals, subtot_align, has_rows=True))
            subtotal_row_idx = len(table_rows) - 1

            tbl_cmds = _base_table_style(subtotal_row_idx)
            tbl_cmds.append(
                ('BACKGROUND', (0, subtotal_row_idx), (-1, subtotal_row_idx), _MONTH_TOTAL_BG))

            month_tbl = Table(table_rows, colWidths=col_widths, repeatRows=1)
            month_tbl.setStyle(TableStyle(tbl_cmds))
            elements.append(month_tbl)

        # Grand TOTAL row — across ALL rows, at the very end of the document.
        elements.append(Spacer(1, 8))
        grand_total_cells = _summary_row_cells('TOTAL', grand_totals, tot_align, has_rows=True)
        grand_tbl = Table([grand_total_cells], colWidths=col_widths)
        grand_tbl.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), _TOTAL_BG),
            ('FONTSIZE', (0, 0), (-1, -1), font_size),
            ('GRID', (0, 0), (-1, -1), 0.3, _CELL_BORDER),
            ('TOPPADDING', (0, 0), (-1, -1), 2),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
            ('LEFTPADDING', (0, 0), (-1, -1), pad),
            ('RIGHTPADDING', (0, 0), (-1, -1), pad),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        elements.append(grand_tbl)

    doc.build(elements, onFirstPage=_draw_header_footer, onLaterPages=_draw_header_footer)
    return buffer.getvalue()
