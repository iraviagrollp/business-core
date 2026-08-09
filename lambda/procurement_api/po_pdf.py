"""
Bulk Purchase Order PDF renderer (reportlab) — formal IAL house design.

`render_po_pdf(po: dict) -> bytes` renders a single-page A4 PDF matching the approved
formal template (IAL_PO_..._formal_2.pdf): two-tone letterhead (logo + centered name +
orange tagline + identity line), a PURCHASE ORDER title with a PO Number/Date box, a
green ORDER DETAILS goods table with computed Amount, a Taxable/GST/Total block plus
amount-in-words, BILL TO / SHIP TO, a COMMERCIAL TERMS table, standard TERMS &
CONDITIONS, a highlighted note, signature block and a registered-office footer.

Palette: green #17452f, secondary green #2d5c44, orange #c8641e, gray label #ececec,
tint #f7f9f7, peach note #fdf6ef. Base font is built-in Helvetica (Arial-metric — matches
the template's Liberation Sans); font sizes are taken directly from the template. DejaVuSans
is bundled ONLY to render the ₹ glyph (which Helvetica lacks); if it's missing, ₹ degrades
to "Rs.".
"""

import os
import re
from datetime import date, datetime
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate, Frame, HRFlowable, Image, KeepTogether, PageTemplate, Paragraph, Spacer,
    Table, TableStyle,
)

_DIR = os.path.dirname(__file__)
_LOGO_PATH = os.path.join(_DIR, 'ial-logo.png')

_GREEN = colors.HexColor('#17452f')
_GREEN2 = colors.HexColor('#2d5c44')
_ORANGE = colors.HexColor('#c8641e')
_GRAYLABEL = colors.HexColor('#ececec')
_TINT = colors.HexColor('#f7f9f7')
_PEACH = colors.HexColor('#fdf6ef')
_RULE = colors.HexColor('#c9c9c9')
_BODY = colors.HexColor('#1c1c1c')
_MUTED = colors.HexColor('#555555')

# Company identity (IAL's own) — constant.
_GSTIN = '37AALFI2946J1ZY'
_LLPIN = 'ACM-3958'
_EMAIL = 'info@iraviagrolife.com'
_WEB = 'www.iraviagrolife.com'
_TAGLINE = 'Nurturing Life, Protecting the Harvest'
_FOOTER_1 = ('Registered Office: Flat No. 102, BVR Plaza, H.No. 5-3-112/2, BJP Office Line, '
             'Shanthi Nagar, Kukatpally, Hyderabad, Telangana 500072')
_FOOTER_2 = 'This purchase order is computer-generated and is valid without signature.'

# NOTE: no PO type (BULK, JOB_WORK, GENERIC) currently renders Terms & Conditions — all three
# call `_build_pdf(flow, [], ...)`, ignoring `po.get('include_terms')`. `_TERMS`/`_terms_flow`
# are kept defined (unused) purely so the section can be reinstated for one or more PO types
# without re-authoring the clause text or the rendering logic.
_TERMS = [
    "Goods must conform to the specification, grade and packing stated in this order. Non-conforming "
    "material is liable to be rejected and returned at the vendor's cost.",
    "Technical grade standards and a batch-wise Certificate of Analysis must accompany every invoice; "
    "invoices received without them will not be processed for payment.",
    "Quantity supplied shall not exceed the ordered quantity without prior written approval; short or "
    "excess supplies will be adjusted against the invoice value.",
    "Delivery shall be completed within the dispatch schedule stated above. Delay entitles IAL to cancel "
    "the order in whole or in part without liability.",
    "Title and risk pass to IAL only on delivery at the ship-to location and acceptance after inspection. "
    "The rate is firm and not subject to escalation.",
    "Disputes arising out of this order are subject to the exclusive jurisdiction of the courts at "
    "Hyderabad, Telangana.",
]


def _register_fonts():
    """Base font is built-in Helvetica (Arial-metric, matches the template's Liberation
    Sans). DejaVuSans is registered ONLY to render the ₹ glyph, which Helvetica lacks;
    the returned rupee token is an inline <font> span (or 'Rs.' if the TTF is absent)."""
    rupee = 'Rs.'
    try:
        if 'DejaVuSans' not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont('DejaVuSans', os.path.join(_DIR, 'DejaVuSans.ttf')))
        rupee = '<font name="DejaVuSans">₹</font>'
    except Exception:
        rupee = 'Rs.'
    return 'Helvetica', 'Helvetica-Bold', rupee


_BASE, _BOLD, _RS = _register_fonts()


def _fmt_date(d) -> str:
    if not d:
        return ''
    if isinstance(d, str):
        d = datetime.strptime(d[:10], '%Y-%m-%d').date()
    if isinstance(d, datetime):
        d = d.date()
    if not isinstance(d, date):
        return str(d)
    return f'{d.day} {d.strftime("%B %Y")}'


def _inr(n) -> str:
    """Indian-grouped amount with 2 decimals, e.g. 200000 -> '2,00,000.00'."""
    try:
        n = float(n)
    except (TypeError, ValueError):
        return '0.00'
    neg = n < 0
    intp, dec = f'{abs(n):.2f}'.split('.')
    if len(intp) > 3:
        rest, last3 = intp[:-3], intp[-3:]
        rest = re.sub(r'(?<=\d)(?=(\d\d)+$)', ',', rest)
        intp = f'{rest},{last3}'
    return ('-' if neg else '') + f'{intp}.{dec}'


_ONES = ['', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine', 'Ten',
         'Eleven', 'Twelve', 'Thirteen', 'Fourteen', 'Fifteen', 'Sixteen', 'Seventeen',
         'Eighteen', 'Nineteen']
_TENS = ['', '', 'Twenty', 'Thirty', 'Forty', 'Fifty', 'Sixty', 'Seventy', 'Eighty', 'Ninety']


def _two(n):
    if n < 20:
        return _ONES[n]
    return (_TENS[n // 10] + ((' ' + _ONES[n % 10]) if n % 10 else '')).strip()


def _three(n):
    h, r = n // 100, n % 100
    s = (_ONES[h] + ' Hundred') if h else ''
    if r:
        s = (s + ' ' + _two(r)).strip()
    return s


def _words(num):
    if num == 0:
        return 'Zero'
    parts = []
    crore, num = num // 10000000, num % 10000000
    lakh, num = num // 100000, num % 100000
    thou, num = num // 1000, num % 1000
    if crore:
        parts.append(_words(crore) + ' Crore')
    if lakh:
        parts.append(_two(lakh) + ' Lakh')
    if thou:
        parts.append(_two(thou) + ' Thousand')
    if num:
        parts.append(_three(num))
    return ' '.join(parts)


def _amount_in_words(total) -> str:
    total = float(total or 0)
    rupees = int(total)
    paise = int(round((total - rupees) * 100))
    txt = 'Indian Rupees ' + _words(rupees)
    if paise:
        txt += ' and ' + _words(paise) + ' Paise'
    return txt + ' Only.'


def _fmt_qty(q) -> str:
    try:
        qf = float(q)
    except (TypeError, ValueError):
        return str(q)
    return f'{int(qf):,}.00' if qf == int(qf) else f'{qf:,.2f}'


def _esc(v) -> str:
    return (str(v) if v is not None else '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def _styles():
    # Sizes taken directly from the template (Liberation Sans → Helvetica, same metrics).
    def s(name, size, **kw):
        kw.setdefault('fontName', _BASE)
        kw.setdefault('leading', size * 1.34)
        return ParagraphStyle(name, fontSize=size, **kw)
    return {
        'company': s('company', 17, fontName=_BOLD, textColor=_GREEN, alignment=TA_CENTER, leading=20),
        'tagline': s('tagline', 8.2, fontName=_BOLD, textColor=_ORANGE, alignment=TA_CENTER, leading=11),
        'identity': s('identity', 7.5, textColor=_MUTED, alignment=TA_CENTER, leading=10),
        'potitle': s('potitle', 13.5, fontName=_BOLD, textColor=_GREEN, leading=16),
        'seclabel': s('seclabel', 9.5, fontName=_BOLD, textColor=_GREEN),
        'seclabelc': s('seclabelc', 9.5, fontName=_BOLD, textColor=_GREEN, alignment=TA_CENTER),
        'boxlabel': s('boxlabel', 8.4, fontName=_BOLD, textColor=_BODY),
        'boxval': s('boxval', 8.4, textColor=_BODY),
        'boxvalorange': s('boxvalorange', 8.4, fontName=_BOLD, textColor=_ORANGE),
        'name': s('name', 9.8, fontName=_BOLD, textColor=_GREEN),
        'body': s('body', 8.3, textColor=_BODY),
        'bodyc': s('bodyc', 8.3, textColor=_BODY, alignment=TA_CENTER),
        'bodyb': s('bodyb', 9.4, fontName=_BOLD, textColor=_BODY),
        # Shared across all three PO types: regular-weight intro paragraph (Dear Sir/Madam
        # stays 'bodyb' bold; the sentence beneath it should NOT be the heaviest text block on
        # the page — only the PO number span, rendered inline in _BOLD/_ORANGE, is emphasised).
        # Same size class as 'bodyb', looser leading for comfortable reading. Does not touch
        # 'bodyb' itself, which every PO type still uses for its own salutation line and any
        # other intentionally-bold text (e.g. the optional GENERIC Subject: line).
        'intro': s('intro', 9.2, textColor=_BODY, leading=12.5),
        'th': s('th', 7.5, fontName=_BOLD, textColor=colors.white),
        'thr': s('thr', 7.5, fontName=_BOLD, textColor=colors.white, alignment=TA_RIGHT),
        'thc': s('thc', 7.5, fontName=_BOLD, textColor=colors.white, alignment=TA_CENTER),
        'cell': s('cell', 8.5, textColor=_BODY),
        'cellc': s('cellc', 8.5, textColor=_BODY, alignment=TA_CENTER),
        'cellr': s('cellr', 8.5, textColor=_BODY, alignment=TA_RIGHT),
        'prod': s('prod', 9.3, fontName=_BOLD, textColor=_GREEN),
        'sub': s('sub', 7.5, textColor=_MUTED),
        'words': s('words', 8.4, textColor=_BODY),
        'tot': s('tot', 10, fontName=_BOLD, textColor=colors.white),
        'totr': s('totr', 10, fontName=_BOLD, textColor=colors.white, alignment=TA_RIGHT),
        'bsname': s('bsname', 8.8, fontName=_BOLD, textColor=_BODY),
        'addr': s('addr', 8.1, textColor=_BODY),
        'ctlabel': s('ctlabel', 8.4, fontName=_BOLD, textColor=_GREEN),
        'ctval': s('ctval', 8.4, textColor=_BODY),
        'tc': s('tc', 7.2, textColor=_MUTED, leading=8),
        'note': s('note', 8.2, textColor=_BODY),
        'sign': s('sign', 8.3, textColor=_BODY),
        'signr': s('signr', 8.3, textColor=_BODY, alignment=TA_RIGHT),
        'signrb': s('signrb', 9.4, fontName=_BOLD, textColor=_BODY, alignment=TA_RIGHT),
        'signrs': s('signrs', 7.6, textColor=_MUTED, alignment=TA_RIGHT),
    }


def _section_label(text, st, width, align='left', space_before=0.11 * cm, space_after=2.5):
    """Green uppercase label with a hairline rule beneath (full width).

    All three PO types (BULK, JOB_WORK, GENERIC) now pass `align='center'` plus the looser
    `space_before=0.4*cm`/`space_after=5.5` for every section heading, so the whole document
    family shares one heading rhythm. The `align='left'` / tight-spacing DEFAULTS are no longer
    reached by any live caller — the only remaining default-args call is the shared
    `_terms_flow()`'s `'TERMS & CONDITIONS'` heading, and since no PO type renders Terms &
    Conditions any more (see `_terms_flow`'s own docstring), that call is itself dead code.
    The defaults are kept as-is (not removed) so `_section_label`/`_terms_flow` continue to
    work unchanged if Terms & Conditions is ever reinstated for some PO type.
    """
    label_style = st['seclabelc'] if align == 'center' else st['seclabel']
    return [Spacer(1, space_before),
            Paragraph(text, label_style),
            HRFlowable(width=width, thickness=0.5, color=_RULE, spaceBefore=1.5, spaceAfter=space_after)]


def _draw_footer(canvas, doc):
    canvas.saveState()
    w = A4[0]
    canvas.setStrokeColor(_RULE)
    canvas.setLineWidth(0.6)
    canvas.line(doc.leftMargin, 0.95 * cm, w - doc.rightMargin, 0.95 * cm)
    canvas.setFont(_BASE, 7.5)
    canvas.setFillColor(_MUTED)
    canvas.drawCentredString(w / 2, 0.66 * cm, _FOOTER_1)
    canvas.drawCentredString(w / 2, 0.46 * cm, _FOOTER_2)
    canvas.restoreState()


def _header(st, dw):
    logo_w = 1.5 * cm
    left = (Image(_LOGO_PATH, width=logo_w, height=logo_w * 530.0 / 471.0)
            if os.path.exists(_LOGO_PATH) else Paragraph('', st['body']))
    center = [
        Paragraph('IRAVI AGRO LIFE LLP', st['company']),
        Paragraph(f'<i>{_TAGLINE}</i>', st['tagline']),
        Spacer(1, 2),
        Paragraph(f'GSTIN: {_GSTIN} &nbsp;|&nbsp; LLPIN: {_LLPIN} &nbsp;|&nbsp; {_EMAIL} &nbsp;|&nbsp; {_WEB}',
                  st['identity']),
    ]
    t = Table([[left, center, '']], colWidths=[logo_w + 0.3 * cm, dw - 2 * (logo_w + 0.3 * cm), logo_w + 0.3 * cm])
    t.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                           ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0)]))
    return t


def _po_box(st, po):
    rows = [
        [Paragraph('PO Number', st['boxlabel']), Paragraph(_esc(po.get('po_no')), st['boxvalorange'])],
        [Paragraph('PO Date', st['boxlabel']), Paragraph(_esc(_fmt_date(po.get('po_date'))), st['boxval'])],
    ]
    t = Table(rows, colWidths=[2.4 * cm, 3.6 * cm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, -1), _GRAYLABEL),
        ('GRID', (0, 0), (-1, -1), 0.5, _RULE),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 7), ('RIGHTPADDING', (0, 0), (-1, -1), 7),
    ]))
    return t


def _addr_para(po, prefix, st):
    name = po.get(f'{prefix}_company_name')
    if not name:
        return Paragraph('&mdash;', st['addr'])
    lines = [f'<font name="{_BOLD}" size="8.8">{_esc(name)}</font>']
    for k in ('address_line1', 'address_line2', 'address_line3'):
        if po.get(f'{prefix}_{k}'):
            lines.append(_esc(po[f'{prefix}_{k}']))
    if po.get(f'{prefix}_state'):
        lines.append(f'State: {_esc(po[f"{prefix}_state"])}')
    if po.get(f'{prefix}_gstin'):
        lines.append(f'GSTIN: {_esc(po[f"{prefix}_gstin"])}')
    return Paragraph('<br/>'.join(lines), st['addr'])


def _po_title_cell(st):
    """The 'PURCHASE ORDER' banner — identical styling (letter-spacing, color,
    underline) for both BULK and JOB_WORK layouts."""
    return [Paragraph('P U R C H A S E &nbsp; O R D E R', st['potitle']),
            HRFlowable(width=3.2 * cm, thickness=2.2, color=_ORANGE, spaceBefore=4, hAlign='LEFT')]


def _vendor_box_stacked(po, st, dw, extra_flow=None):
    """The one true supplier/vendor identity box, shared by all three PO types (BULK's
    "VENDOR / SUPPLIER", JOB_WORK's "JOB WORKER", and GENERIC's "VENDOR / SUPPLIER"). Renamed
    from the old BULK-only `_vendor_box_bulk` — its address format is now the house standard:
    name bold on its own line, then each address line (line1/line2/line3) on its own line,
    then `GSTIN: ...` on its own final line — no comma-joining, no GSTIN appended inline to the
    last address line. (The old comma-joined/inline-GSTIN `_vendor_box` helper has been
    deleted — every former caller now uses this one, so BULK/JOB_WORK/GENERIC render an
    identical vendor-box treatment.)

    `extra_flow` (default `None`) — an optional list of extra flowables appended inside the
    SAME bordered/tinted box, after the address block. BULK and GENERIC never pass this (stays
    `None`), so their output is byte-identical to before this parameter existed. JOB_WORK uses
    it to fold its PRODUCT line into this box instead of a separate section (2026-08-08 one-page
    density pass) — see `_render_job_work_po_pdf`. The box's own padding (7/11) and the address
    leading (11.5) below are UNCHANGED regardless of `extra_flow`."""
    ven = [Paragraph(_esc(po.get('supplier_company_name')), st['name'])]
    lines = [po.get(k) for k in
             ('supplier_address_line1', 'supplier_address_line2', 'supplier_address_line3') if po.get(k)]
    if po.get('supplier_gstin'):
        lines.append(f'GSTIN: {po["supplier_gstin"]}')
    if lines:
        # Looser leading (~11.5, vs the shared `body` style's ~11.1) so the up-to-4 stacked
        # address/GSTIN lines don't crowd — local style, doesn't touch the shared `body` style
        # used elsewhere (note callout, JOB_WORK salutation body, etc.).
        addr_style = ParagraphStyle('vendor_stacked_addr', parent=st['body'], leading=11.5)
        ven.append(Paragraph('<br/>'.join(_esc(x) for x in lines), addr_style))
    if extra_flow:
        ven.extend(extra_flow)
    vbox = Table([[ven]], colWidths=[dw])
    vbox.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BOX', (0, 0), (-1, -1), 0.5, _RULE),
        ('BACKGROUND', (0, 0), (-1, -1), _TINT),
        ('TOPPADDING', (0, 0), (-1, -1), 7), ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
        ('LEFTPADDING', (0, 0), (-1, -1), 11), ('RIGHTPADDING', (0, 0), (-1, -1), 11),
    ]))
    return vbox


def _label_value_flow(pairs, st, dw, row_padding=5):
    """Shared borderless `label : value` list — no borders, no shading, fixed-width label
    column, centered colon column, values left. `pairs` is a list of `(label, value_html,
    bold)`; `value_html` may contain inline markup (e.g. a bold/orange span), `bold` wraps the
    whole value in `<font name="{_BOLD}">...</font>` when true. Generalised out of the old
    BULK-only `_bulk_order_details_flow` table-building code — BULK's ORDER DETAILS list still
    renders byte-identically through this helper (see `_bulk_order_details_flow` below, now a
    thin wrapper, which does NOT pass `row_padding` and therefore keeps the default 5 —
    BULK's own row padding is untouched); JOB_WORK's COMMERCIAL TERMS list also renders
    through this helper, passing a tighter `row_padding` (see `_render_job_work_po_pdf`), so
    the two lists share identical column geometry but may use different row padding."""
    colon_style = ParagraphStyle('lv_colon', parent=st['ctval'], alignment=TA_CENTER)
    data = []
    for label, value, bold in pairs:
        val_html = f'<font name="{_BOLD}">{value}</font>' if bold else value
        data.append([Paragraph(label, st['ctlabel']), Paragraph(':', colon_style),
                     Paragraph(val_html, st['ctval'])])
    tab = Table(data, colWidths=[3.6 * cm, 0.4 * cm, dw - 4.0 * cm])
    tab.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), row_padding), ('BOTTOMPADDING', (0, 0), (-1, -1), row_padding),
        ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
    ]))
    return tab


def _bulk_order_details_flow(po, st, dw, qty, rate, gst_lbl):
    """BULK-only ORDER DETAILS body: a plain label : value list (reference-PO style) —
    replaces the old 6-column gridded goods table (SL/DESCRIPTION/QUANTITY/UOM/RATE/AMOUNT).
    Terms/Dispatch/Transport move here from the now-deleted COMMERCIAL TERMS section, so no
    data is lost. Thin wrapper around the shared `_label_value_flow` — renders byte-identically
    to before that helper was extracted."""
    rows = [
        ('Product', _esc(po.get('technical_name')), True),
        ('Quantity', f'{_fmt_qty(qty)} {_esc(po.get("quantity_unit"))}', True),
        ('Price', f'{_RS} {_inr(rate)}/{_esc(po.get("quantity_unit"))}', True),
        ('GST', f'{gst_lbl}%', False),
    ]
    for label, key in (('Terms', 'terms'), ('Dispatch', 'dispatch'), ('Transport', 'transport')):
        val = po.get(key)
        if val:
            rows.append((label, _esc(val), False))
    return _label_value_flow(rows, st, dw)


def _note_flow(po, st, dw):
    """Highlighted note callout — empty list if there's no note."""
    note = po.get('note')
    if not note:
        return []
    note_html = (f'<font name="{_BOLD}" color="#c8641e">Note:</font> '
                 f'<font backColor="#E9FF2E">&nbsp;{_esc(note)}&nbsp;</font>')
    nb = Table([[Paragraph(note_html, st['note'])]], colWidths=[dw])
    nb.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#fffdf3')),
        ('LINEBEFORE', (0, 0), (0, -1), 3, _ORANGE),
        ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#efe4cf')),
        ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 10), ('RIGHTPADDING', (0, 0), (-1, -1), 8),
    ]))
    return [Spacer(1, 3), nb]


def _signature_flow(po, st, dw, sig_gap=20):
    """Thanking-you / for-IAL / signature-line block — shared by BULK, JOB_WORK and GENERIC.
    Wrapped in KeepTogether so the block moves as one atomic unit if it doesn't fit
    in the remaining space on the page, instead of splitting mid-block (e.g. the
    HRFlowable signature line landing on one page and the signatory name on the
    next).

    `sig_gap` (default `20`, the original fixed value) is the physical-signature gap above the
    signature line. BULK/GENERIC never pass it, so their output is byte-identical to before this
    parameter existed. JOB_WORK trims it (2026-08-08 one-page density pass) — see
    `_render_job_work_po_pdf`."""
    inner = [
        Spacer(1, 2),
        Paragraph('Thanking you,', st['sign']),
        Paragraph('Yours faithfully,', st['sign']),
        Spacer(1, 4),
        Paragraph(f'For <font name="{_BOLD}">IRAVI AGRO LIFE LLP</font>', st['signr']),
        Spacer(1, sig_gap),  # room for a physical signature
        HRFlowable(width=dw / 2, thickness=0.6, color=_MUTED, hAlign='RIGHT', spaceAfter=3),
    ]
    if po.get('signatory_name'):
        inner.append(Paragraph(_esc(po['signatory_name']), st['signrb']))
    if po.get('signatory_title'):
        inner.append(Paragraph(_esc(po['signatory_title']), st['signrs']))
    if po.get('signatory_department'):
        inner.append(Paragraph(_esc(po['signatory_department']), st['signrs']))
    return [KeepTogether(inner)]


def _terms_flow(st, dw):
    """Terms & Conditions section — its own flowable list, so callers can either
    append it inline or push it onto a fresh page.

    Currently UNUSED — no PO type renders Terms & Conditions any more (see `_TERMS`'s
    module-level comment above). Kept defined so the section can be reinstated without
    re-authoring it."""
    flow = _section_label('TERMS & CONDITIONS', st, dw)
    tcd = [[Paragraph(f'{i}.', st['tc']), Paragraph(_esc(t), st['tc'])] for i, t in enumerate(_TERMS, 1)]
    tctab = Table(tcd, colWidths=[0.6 * cm, dw - 0.6 * cm])
    tctab.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 1), ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
        ('LEFTPADDING', (0, 0), (0, -1), 2), ('LEFTPADDING', (1, 0), (1, -1), 2),
    ]))
    flow.append(tctab)
    return flow


def _build_pdf(core_flow, terms_flow, title):
    """Single-pass build. The Terms & Conditions section (terms_flow) is wrapped in
    one KeepTogether so reportlab treats it as an atomic block: if it fits in the
    space remaining on the current page, it stays right there (no forced blank
    page); if it doesn't fit, reportlab pushes the WHOLE block onto a fresh page
    instead of splitting the numbered list across the boundary. This is robust for
    any number of grid/item rows in core_flow — no per-line hacks, no manual
    page-count measurement/rebuild needed."""
    flow = list(core_flow)
    if terms_flow:
        flow.append(KeepTogether(terms_flow))

    buf = BytesIO()
    doc = BaseDocTemplate(
        buf, pagesize=A4,
        leftMargin=1.5 * cm, rightMargin=1.5 * cm, topMargin=0.4 * cm, bottomMargin=1.1 * cm,
        title=title,
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id='main')
    doc.addPageTemplates([PageTemplate(id='po', frames=[frame], onPage=_draw_footer)])
    doc.build(flow)
    return buf.getvalue()


def _render_bulk_po_pdf(po: dict) -> bytes:
    st = _styles()
    dw = A4[0] - 3.0 * cm  # leftMargin + rightMargin = 1.5cm + 1.5cm, matches _build_pdf

    qty = float(po.get('quantity') or 0)
    rate = float(po.get('rate') or 0)
    gst_rate = float(po.get('gst_rate') or 0)
    amount = float(po.get('amount') if po.get('amount') is not None else qty * rate)
    gst_amt = float(po.get('gst_amount') if po.get('gst_amount') is not None else round(amount * gst_rate / 100, 2))
    total = float(po.get('total_value') if po.get('total_value') is not None else round(amount + gst_amt, 2))
    gst_lbl = f'{gst_rate:g}'

    flow = [_header(st, dw), Spacer(1, 2)]
    flow.append(HRFlowable(width=dw, thickness=2.2, color=_GREEN, spaceBefore=2, spaceAfter=1.5))
    flow.append(HRFlowable(width=dw, thickness=0.8, color=_ORANGE, spaceAfter=3))

    # Title row + PO box.
    trow = Table([[_po_title_cell(st), _po_box(st, po)]], colWidths=[dw - 6.0 * cm, 6.0 * cm])
    trow.setStyle(TableStyle([('VALIGN', (0, 0), (0, 0), 'MIDDLE'), ('VALIGN', (1, 0), (1, 0), 'TOP'),
                              ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0)]))
    flow.append(trow)

    # Vendor / Supplier — boxed so it reads as a distinct unit, separate from the
    # salutation below. Uses the shared `_vendor_box_stacked` helper (each address line on its
    # own line, GSTIN on its own final line) — matching the reference PO; JOB_WORK/GENERIC now
    # use the same helper (renamed from the old BULK-only `_vendor_box_bulk`).
    # Centered, with looser space above/below (~0.4cm before, ~5.5pt after the rule) than the
    # shared default — the core of the "clamped together" complaint being addressed here.
    flow += _section_label('VENDOR / SUPPLIER', st, dw, align='center', space_before=0.4 * cm, space_after=5.5)
    flow.append(_vendor_box_stacked(po, st, dw))

    # Salutation + intro paragraph (reference-PO wording, adapted since Terms & Conditions
    # are removed for BULK — see the include_terms note at the bottom of this function).
    flow.append(Spacer(1, 16))
    flow.append(Paragraph('Dear Sir / Madam,', st['bodyb']))
    flow.append(Spacer(1, 8))
    po_no = _esc(po.get('po_no'))
    body = (f'Please supply the under mentioned goods on the terms set out below. Please also quote '
            f'this order reference <font name="{_BOLD}" color="#c8641e">{po_no}</font> in all your '
            f'supply documents, invoices, delivery challans, e-way bills and future correspondence.')
    flow.append(Paragraph(body, st['intro']))

    # Order details — plain label : value list (reference-PO style), not a gridded table.
    flow += _section_label('ORDER DETAILS', st, dw, align='center', space_before=0.4 * cm, space_after=5.5)
    flow.append(_bulk_order_details_flow(po, st, dw, qty, rate, gst_lbl))

    # Totals: words (left) + taxable/gst/total (right).
    words_cell = [Paragraph('TOTAL ORDER VALUE IN WORDS', st['seclabel']), Spacer(1, 3),
                  Paragraph(_amount_in_words(total), st['words'])]
    right = Table(
        [[Paragraph('Taxable Value', st['cell']), Paragraph(_inr(amount), st['cellr'])],
         [Paragraph(f'GST @ {gst_lbl}%', st['cell']), Paragraph(_inr(gst_amt), st['cellr'])],
         [Paragraph('Total Order Value', st['tot']), Paragraph(f'{_RS} {_inr(total)}', st['totr'])]],
        colWidths=[(dw / 2) - 3.4 * cm, 3.4 * cm])
    right.setStyle(TableStyle([
        ('BACKGROUND', (0, 2), (-1, 2), _GREEN),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('BOX', (0, 0), (-1, -1), 0.5, _RULE),
        ('LINEBELOW', (0, 0), (-1, 0), 0.5, _RULE), ('LINEBELOW', (0, 1), (-1, 1), 0.5, _RULE),
        ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 8), ('RIGHTPADDING', (0, 0), (-1, -1), 8),
    ]))
    # BULK-only: `right` now carries its own BOX (above) so it reads as an enclosed component
    # next to the boxed left panel — previously it had only LINEBELOW hairlines between rows and
    # no outer border. RIGHTPADDING on this wrapper cell stays 0 so the new box's right edge
    # stays flush with the content area's right edge (matching the BILL TO/SHIP TO table and the
    # rest of the document, which all span the full `dw` width) — do not add right padding here.
    # TOPPADDING/BOTTOMPADDING bumped 0->2 so the box isn't flush against the row's top/bottom
    # (previously would have sat exactly at the cell edge with zero breathing room); 2pt is small
    # enough that the new box's top edge still lines up closely with the left tinted panel's top
    # (whose own BOX wraps its outer cell including that cell's 5pt TOPPADDING).
    tot = Table([[words_cell, right]], colWidths=[dw / 2, dw / 2])
    tot.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, 0), _TINT), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BOX', (0, 0), (0, 0), 0.5, _RULE),
        ('TOPPADDING', (0, 0), (0, 0), 5), ('BOTTOMPADDING', (0, 0), (0, 0), 5),
        ('LEFTPADDING', (0, 0), (0, 0), 8), ('RIGHTPADDING', (0, 0), (0, 0), 8),
        ('LEFTPADDING', (1, 0), (1, 0), 6), ('RIGHTPADDING', (1, 0), (1, 0), 0),
        ('TOPPADDING', (1, 0), (1, 0), 2), ('BOTTOMPADDING', (1, 0), (1, 0), 2),
    ]))
    flow.append(Spacer(1, 10))
    flow.append(tot)

    # Bill To Address / Ship To Address — labels match the reference PO
    # ("BILL TO ADDRESS" / "SHIP TO ADDRESS", not the plain "BILL TO" / "SHIP TO" used
    # by JOB_WORK/GENERIC); everything else about this bordered two-column table is
    # unchanged. This is the reference PO's only bordered element, same as here.
    # Headings centered (seclabelc) with their own band — extra top/bottom padding + a
    # hairline LINEBELOW separates the heading from the address content beneath it.
    flow.append(Spacer(1, 10))
    bs = Table(
        [[Paragraph('BILL TO ADDRESS', st['seclabelc']), Paragraph('SHIP TO ADDRESS', st['seclabelc'])],
         [_addr_para(po, 'bill_to', st), _addr_para(po, 'ship_to', st)]],
        colWidths=[dw / 2, dw / 2])
    bs.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('BOX', (0, 0), (-1, -1), 0.5, _RULE),
        ('LINEBELOW', (0, 0), (-1, 0), 0.5, _RULE),
        ('LINEBEFORE', (1, 0), (1, -1), 0.5, _RULE),
        # General padding first (applies to every cell, including row 0), then the row-0
        # heading band's own wider padding is applied AFTER so it isn't clobbered by the
        # blanket rule below (TableStyle commands apply in order — later wins on overlap).
        ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('LEFTPADDING', (0, 0), (-1, -1), 8), ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, 0), 6), ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
    ]))
    flow.append(bs)

    # COMMERCIAL TERMS section deleted for BULK — Terms/Dispatch/Transport now live in
    # the ORDER DETAILS list above; the "Taxes" line is dropped (the GST row in that
    # list already covers it).

    # Note band, then signature.
    flow += _note_flow(po, st, dw)
    flow += _signature_flow(po, st, dw)

    # Terms & Conditions — intentionally OMITTED for BULK (the reference PO has none);
    # po.get('include_terms') is ignored here on purpose. JOB_WORK/GENERIC still honor
    # that flag via _terms_flow() unchanged.
    return _build_pdf(flow, [], f'Purchase Order {po.get("po_no", "")}')


# ── Job Work Purchase Order ────────────────────────────────────────────────────

# Header quantity_unit -> base unit each line item's own 'quantity' is stored in
# (TONNE items are in KGS; KL items are in LTRS) — mirrors handler.py's _UNIT_BASE.
_UNIT_BASE_LABEL = {'KGS': 'KGS', 'TONNE': 'KGS', 'LTRS': 'LTRS', 'KL': 'LTRS'}


def _job_work_particulars(it) -> str:
    """'{technical_name} - {brand_name} - {packaging}', gracefully omitting missing
    brand/packaging."""
    parts = [it.get('technical_name') or '']
    if it.get('brand_name'):
        parts.append(it['brand_name'])
    if it.get('packaging'):
        parts.append(it['packaging'])
    return ' - '.join(p for p in parts if p)


def _render_job_work_po_pdf(po: dict) -> bytes:
    st = _styles()
    dw = A4[0] - 3.0 * cm  # leftMargin + rightMargin = 1.5cm + 1.5cm, matches _build_pdf

    items = po.get('items') or []
    gst_rate = float(po.get('gst_rate') or 0)
    # Amount is the sum of item amounts (NOT the header quantity*rate SQL field —
    # items carry the real per-line rate/quantity for JOB_WORK POs).
    amount = round(sum(float(it.get('amount') or 0) for it in items), 2)
    gst_amt = round(amount * gst_rate / 100, 2)
    total = round(amount + gst_amt, 2)
    gst_lbl = f'{gst_rate:g}'

    header_unit = (po.get('quantity_unit') or '').upper()
    base_unit = _UNIT_BASE_LABEL.get(header_unit, header_unit)
    dash = '—'

    flow = [_header(st, dw), Spacer(1, 2)]
    flow.append(HRFlowable(width=dw, thickness=2.2, color=_GREEN, spaceBefore=2, spaceAfter=1.5))
    flow.append(HRFlowable(width=dw, thickness=0.8, color=_ORANGE, spaceAfter=3))

    # Title row + PO box — same "PURCHASE ORDER" banner as BULK.
    trow = Table([[_po_title_cell(st), _po_box(st, po)]], colWidths=[dw - 6.0 * cm, 6.0 * cm])
    trow.setStyle(TableStyle([('VALIGN', (0, 0), (0, 0), 'MIDDLE'), ('VALIGN', (1, 0), (1, 0), 'TOP'),
                              ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0)]))
    flow.append(trow)

    # Job Worker (the reused supplier_company_id) — shared stacked vendor box, same treatment
    # as BULK/GENERIC. Section heading switched to the BULK centered treatment.
    #
    # PRODUCT is now folded INSIDE this same box (2026-08-08 one-page density pass) instead of
    # its own heading + separate tinted box — the standalone PRODUCT section largely duplicated
    # what the item grid below already shows per line (technical name, brand, quantity) and its
    # heading + box padding cost ~45pt of vertical space on a document that was running to a
    # second page for only its Note + signature block. A thin rule + one compact line inside the
    # JOB WORKER box keeps the same summary one glance away without a second bordered panel. The
    # vendor box's own padding (7/11) and address leading (11.5) are untouched — only new content
    # is appended via `_vendor_box_stacked`'s `extra_flow` param (default `None`, so BULK/GENERIC,
    # which never pass it, render byte-identically to before this change).
    #
    # Heading space_before/space_after tightened 0.4cm/5.5 -> 0.28cm/4 (2026-08-08, one-page
    # density pass round 2) — JOB WORKER no longer needs the extra breathing room BULK's own
    # (much shorter) VENDOR/SUPPLIER heading gets, now that this box carries the merged PRODUCT
    # line too; matches the same tightened treatment already used for ORDER DETAILS/COMMERCIAL
    # TERMS below.
    flow += _section_label('JOB WORKER', st, dw, align='center', space_before=0.28 * cm, space_after=4)
    prod_line = (f'<font name="{_BOLD}">Product:</font> {_esc(po.get("technical_name"))}'
                 f' &nbsp;{dash}&nbsp; Brand: {_esc(po.get("brand_name") or dash)}'
                 f' &nbsp;{dash}&nbsp; Quantity: <font name="{_BOLD}">{_fmt_qty(po.get("quantity"))} '
                 f'{_esc(header_unit)}</font>')
    prod_style = ParagraphStyle('jw_product_line', parent=st['body'], leading=11.5)
    # Separator rule spacing tightened 5/4 -> 3/3 (2026-08-08, round 2).
    flow.append(_vendor_box_stacked(po, st, dw, extra_flow=[
        HRFlowable(width=dw - 22, thickness=0.5, color=_RULE, spaceBefore=3, spaceAfter=3, hAlign='LEFT'),
        Paragraph(prod_line, prod_style),
    ]))

    # Salutation + intro paragraph — same rhythm as BULK (Spacer(1,16) before the salutation,
    # Spacer(1,8) between salutation and intro). Regular-weight intro via the shared `intro`
    # style; only the PO number is emphasised bold/orange — "acknowledge this order" is no
    # longer bold-orange, matching BULK's calmer hierarchy.
    flow.append(Spacer(1, 16))
    flow.append(Paragraph('Dear Sir / Madam,', st['bodyb']))
    flow.append(Spacer(1, 8))
    po_no = _esc(po.get('po_no'))
    body = (f'We are pleased to place the following job work order with you, on the terms set out below. '
            f'Please acknowledge this order and quote '
            f'<font name="{_BOLD}" color="#c8641e">{po_no}</font> on every invoice, delivery challan, '
            f'e-way bill and communication relating to this supply.')
    flow.append(Paragraph(body, st['intro']))

    # Order details — multi-row particulars grid (one row per item). Section heading switched
    # to the BULK centered treatment. space_before tightened 0.4cm->0.28cm (2026-08-08, one-page
    # density pass round 1); space_after also now tightened 5.5->4 (2026-08-08, round 2 — the
    # round-1 pass explicitly left it at the shared default per that task's own scoped list, but
    # that fence is lifted for this follow-up pass).
    flow += _section_label('ORDER DETAILS', st, dw, align='center', space_before=0.28 * cm, space_after=4)
    head = [Paragraph('SL.', st['thc']), Paragraph('PARTICULARS', st['th']),
            Paragraph('QUANTITY', st['thc']), Paragraph(f'RATE ({_RS})', st['thr']),
            Paragraph(f'AMOUNT ({_RS})', st['thr'])]
    col = [1.15 * cm, dw - 1.15 * cm - 3.6 * cm - 2.5 * cm - 2.9 * cm, 3.6 * cm, 2.5 * cm, 2.9 * cm]
    body_rows = []
    for i, it in enumerate(items, 1):
        qty = float(it.get('quantity') or 0)
        rate = float(it.get('rate') or 0)
        it_amount = float(it.get('amount') if it.get('amount') is not None else qty * rate)
        body_rows.append([
            Paragraph(str(i), st['cellc']),
            Paragraph(_esc(_job_work_particulars(it)), st['cell']),
            Paragraph(f'{_fmt_qty(qty)} {_esc(base_unit)}', st['cellc']),
            Paragraph(_inr(rate), st['cellr']),
            Paragraph(_inr(it_amount), st['cellr']),
        ])
    total_row_idx = len(body_rows) + 1
    # SPAN (0, idx) -> (3, idx) below means reportlab only renders the CONTENT of cell
    # index 0 of that span (the other spanned cells' own content is discarded, even if
    # non-empty) — so the 'TOTAL' label must live in cell 0, not cell 1, or it never
    # appears at all (previously rendered as an unlabeled amount). Right-aligned so it
    # sits immediately adjacent to the amount column, matching how a 'TOTAL' row reads
    # in the other grid-style tables in this file.
    total_label_style = ParagraphStyle('jw_total_label', parent=st['bodyb'], alignment=TA_RIGHT)
    total_row = [Paragraph('TOTAL', total_label_style), '', '', '', Paragraph(_inr(amount), st['cellr'])]
    gtab = Table([head] + body_rows + [total_row], colWidths=col, repeatRows=1)
    gtab.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), _GREEN),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LINEBEFORE', (1, 0), (4, 0), 0.7, _GREEN2),
        ('LINEBELOW', (0, 1), (-1, total_row_idx - 1), 0.4, colors.HexColor('#dcdcdc')),
        ('SPAN', (0, total_row_idx), (3, total_row_idx)),
        ('LINEABOVE', (0, total_row_idx), (-1, total_row_idx), 0.7, _GREEN),
        # Row TOP/BOTTOMPADDING tightened 4->3 (2026-08-08, round 1) then 3->2.5 (2026-08-08,
        # round 2) — was raised 2->4 previously; 2.5 keeps the row denser still without going
        # back to the original 2 (still comfortably legible at the grid's 8.5pt cell font).
        ('TOPPADDING', (0, 0), (-1, -1), 2.5), ('BOTTOMPADDING', (0, 0), (-1, -1), 2.5),
        ('LEFTPADDING', (0, 0), (-1, -1), 5), ('RIGHTPADDING', (0, 0), (-1, -1), 5),
    ]))
    flow.append(gtab)

    # Totals: single-row 4-cell strip — GST label|amount, Total Order Value label|amount
    # (2026-08-09 follow-up — collapses the previous two-row `right` table into one row so the
    # GST figure and the highlighted Total Order Value figure sit side by side instead of
    # stacked). "TOTAL ORDER VALUE IN WORDS"/Taxable Value stay dropped (previous pass).
    # `_amount_in_words`/`_section_label` stay imported/defined — BULK's totals block (untouched)
    # still uses `_amount_in_words`, and `_section_label` is still used elsewhere in this
    # function. `amount`/`total` are still needed here (gst_amt/the Total Order Value cell) and
    # by the item grid above, so neither was removed.
    right = Table(
        [[Paragraph(f'GST @ {gst_lbl}%', st['cell']), Paragraph(_inr(gst_amt), st['cellr']),
          Paragraph('Total Order Value', st['tot']), Paragraph(f'{_RS} {_inr(total)}', st['totr'])]],
        colWidths=[3.6 * cm, 3.0 * cm, 4.2 * cm, 3.4 * cm])
    right.setStyle(TableStyle([
        ('BACKGROUND', (2, 0), (3, 0), _GREEN),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('BOX', (0, 0), (-1, -1), 0.5, _RULE),
        ('LINEAFTER', (1, 0), (1, 0), 0.5, _RULE), ('LINEBEFORE', (2, 0), (2, 0), 0.5, _RULE),
        ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 8), ('RIGHTPADDING', (0, 0), (-1, -1), 8),
    ]))
    right.hAlign = 'RIGHT'
    # Spacer before the totals block tightened 10->6 (round 1), then 6->4 (2026-08-08, round 2).
    flow.append(Spacer(1, 4))
    flow.append(right)

    # To Be Billed On / Delivered At — these labels are meaningfully different from BULK's
    # "BILL TO ADDRESS"/"SHIP TO ADDRESS" so they're kept, but the table now uses BULK's exact
    # treatment: centered headings (seclabelc), header-row top/bottom padding 6, a LINEBELOW
    # under the header row, address row padding 3. Spacer before the table tightened 10->6
    # (round 1), then 6->4 (2026-08-08, round 2).
    flow.append(Spacer(1, 4))
    bs = Table(
        [[Paragraph('TO BE BILLED ON', st['seclabelc']), Paragraph('DELIVERED AT', st['seclabelc'])],
         [_addr_para(po, 'bill_to', st), _addr_para(po, 'ship_to', st)]],
        colWidths=[dw / 2, dw / 2])
    bs.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('BOX', (0, 0), (-1, -1), 0.5, _RULE),
        ('LINEBELOW', (0, 0), (-1, 0), 0.5, _RULE),
        ('LINEBEFORE', (1, 0), (1, -1), 0.5, _RULE),
        # General padding first (applies to every cell, including row 0), then the row-0
        # heading band's own wider padding is applied AFTER so it isn't clobbered — same
        # ordering convention as BULK's BILL TO ADDRESS / SHIP TO ADDRESS table. Row padding
        # 3->2.5 and header-row padding 6->5 (2026-08-08, round 2) — JOB_WORK-only table (BULK
        # builds its own separate `bs` table for BILL TO ADDRESS/SHIP TO ADDRESS, untouched).
        ('TOPPADDING', (0, 0), (-1, -1), 2.5), ('BOTTOMPADDING', (0, 0), (-1, -1), 2.5),
        ('LEFTPADDING', (0, 0), (-1, -1), 8), ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, 0), 5), ('BOTTOMPADDING', (0, 0), (-1, 0), 5),
    ]))
    flow.append(bs)

    # Commercial terms — borderless label : value list via the shared helper (replaces the old
    # grey-shaded bordered grid), same column geometry as BULK's ORDER DETAILS list but a
    # tighter row_padding (BULK's own call passes no row_padding, so it keeps the default 5 —
    # untouched). Heading space_before tightened 0.4cm->0.28cm (round 1); space_after also now
    # tightened 5.5->4 and row_padding 3.5->3 (2026-08-08, round 2 — round 1 explicitly left
    # these at their round-1 values per that task's own scoped list, but that fence is lifted
    # for this follow-up pass).
    flow += _section_label('COMMERCIAL TERMS', st, dw, align='center', space_before=0.28 * cm, space_after=4)
    ct_pairs = [
        ('Payment Terms', _esc(po.get('terms')) if po.get('terms') else '&mdash;', False),
        ('Dispatch Schedule', _esc(po.get('dispatch')) if po.get('dispatch') else '&mdash;', False),
        ('Mode of Transport', _esc(po.get('transport')) if po.get('transport') else '&mdash;', False),
        ('Taxes', f'GST @ {gst_lbl}% extra as applicable; rate quoted is exclusive of GST', False),
    ]
    flow.append(_label_value_flow(ct_pairs, st, dw, row_padding=3))

    # Note band, then signature (shared).
    #
    # Orphan check (2026-08-08, one-page density follow-up): considered wrapping Note+Signature
    # in one outer KeepTogether at this call site so the signature block (already
    # KeepTogether-wrapped inside _signature_flow) could never strand alone on an otherwise
    # near-empty trailing page. Verified with an actual 12-item stress render instead of
    # reasoning about it — with the two appended independently (as below), a JOB_WORK PO whose
    # core content genuinely doesn't fit on one page lands TO BE BILLED ON/DELIVERED AT +
    # COMMERCIAL TERMS + Note + the full signature block together on page 2 (2 pages total, no
    # orphan — reportlab's own KeepTogether-on-signature-only handling is already acceptable
    # here). Pairing Note+Signature into one bigger atomic block made this WORSE, not better: the
    # combined block no longer fit in the room left after COMMERCIAL TERMS either, so it forced
    # an unnecessary extra page (3 instead of 2) for the exact scenario this fix was meant to
    # help. Pairing was reverted; `_note_flow`/`_signature_flow` are appended independently here,
    # unchanged from before this task, exactly as BULK/GENERIC already do.
    flow += _note_flow(po, st, dw)
    # sig_gap trimmed 20->13 (2026-08-08, one-page density pass) — JOB_WORK-only; BULK/GENERIC
    # still call _signature_flow with no sig_gap arg, so they keep the original 20pt untouched
    # (byte-identical output).
    flow += _signature_flow(po, st, dw, sig_gap=13)

    # Terms & Conditions — DROPPED for JOB_WORK, exactly like BULK; po.get('include_terms') is
    # intentionally ignored here. _terms_flow()/_TERMS remain defined (unused) so the section
    # can be reinstated later if needed.
    return _build_pdf(flow, [], f'Job Work Purchase Order {po.get("po_no", "")}')


# ── Generic Purchase Order ─────────────────────────────────────────────────────

# Defensive fallback only — handler.py's _po_validate already defaults an empty body
# to this exact text before the row is ever persisted, so in practice generic_config
# always arrives with a non-empty body. Kept in sync with handler._GENERIC_DEFAULT_BODY.
_GENERIC_DEFAULT_BODY = (
    'Please supply the under mentioned goods, subject to terms & conditions stated below. '
    'Please also quote this order reference in all your supply documents and future '
    'correspondence. Please dispatch the stock within 3 days of receipt of this PO.'
)


def _is_serial_col(header) -> bool:
    """True if a column header looks like a serial-number column (S No., Sl., Sr No., ...)."""
    h = re.sub(r'[^a-z]', '', (header or '').lower())
    return h in ('sno', 'slno', 'sl', 'srno', 'sr', 'no')


def _is_particulars_col(header) -> bool:
    h = (header or '').lower()
    return any(k in h for k in ('particular', 'description', 'desc', 'item', 'name'))


def _generic_col_widths(columns, dw):
    """Sensible auto column widths for the arbitrary Generic table: a narrow width for
    a leading serial-number-looking column, the widest share for a Particulars/
    description-type column if present, otherwise an even split of what's left."""
    n = len(columns)
    if n == 0:
        return []
    if n == 1:
        return [dw]
    serial_idx = 0 if _is_serial_col(columns[0]) else None
    serial_w = 1.1 * cm if serial_idx is not None else 0.0
    remaining = max(dw - serial_w, 0.0)
    other_idxs = [i for i in range(n) if i != serial_idx]
    if not other_idxs:
        return [dw]

    particulars_idx = next((i for i in other_idxs if _is_particulars_col(columns[i])), None)
    if particulars_idx is not None and len(other_idxs) > 1:
        particulars_w = remaining * 0.4
        rest_each = (remaining - particulars_w) / (len(other_idxs) - 1)
        return [serial_w if i == serial_idx else (particulars_w if i == particulars_idx else rest_each)
                for i in range(n)]

    each = remaining / len(other_idxs)
    return [serial_w if i == serial_idx else each for i in range(n)]


def _render_generic_po_pdf(po: dict) -> bytes:
    st = _styles()
    dw = A4[0] - 3.0 * cm  # leftMargin + rightMargin = 1.5cm + 1.5cm, matches _build_pdf

    gc = po.get('generic_config') or {}
    columns = gc.get('columns') or []
    rows = gc.get('rows') or []
    subject = (gc.get('subject') or '').strip()
    body = (gc.get('body') or '').strip() or _GENERIC_DEFAULT_BODY

    flow = [_header(st, dw), Spacer(1, 2)]
    flow.append(HRFlowable(width=dw, thickness=2.2, color=_GREEN, spaceBefore=2, spaceAfter=1.5))
    flow.append(HRFlowable(width=dw, thickness=0.8, color=_ORANGE, spaceAfter=3))

    # Title row + PO box — same "PURCHASE ORDER" banner as BULK/JOB_WORK.
    trow = Table([[_po_title_cell(st), _po_box(st, po)]], colWidths=[dw - 6.0 * cm, 6.0 * cm])
    trow.setStyle(TableStyle([('VALIGN', (0, 0), (0, 0), 'MIDDLE'), ('VALIGN', (1, 0), (1, 0), 'TOP'),
                              ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0)]))
    flow.append(trow)

    # Vendor / Supplier — shared stacked vendor box, same treatment as BULK/JOB_WORK. Section
    # heading switched to the BULK centered treatment.
    flow += _section_label('VENDOR / SUPPLIER', st, dw, align='center', space_before=0.4 * cm, space_after=5.5)
    flow.append(_vendor_box_stacked(po, st, dw))

    # Subject line (optional), above the salutation, with comparable spacing (8pt before, then
    # the same 16pt lead-in the salutation always gets — whether or not a subject is present).
    flow.append(Spacer(1, 8))
    if subject:
        flow.append(Paragraph(f'<font name="{_BOLD}">Subject:</font> {_esc(subject)}', st['bodyb']))

    # Salutation + configurable body, with the standard acknowledgment/quote-PO sentence
    # appended (PO number kept in bold orange, same treatment as BULK/JOB_WORK —
    # "acknowledge this order" itself is no longer bold-orange). Same rhythm as BULK/JOB_WORK:
    # Spacer(1,16) before the salutation, Spacer(1,8) after it. Regular-weight intro via the
    # shared `intro` style.
    flow.append(Spacer(1, 16))
    flow.append(Paragraph('Dear Sir / Madam,', st['bodyb']))
    flow.append(Spacer(1, 8))
    po_no = _esc(po.get('po_no'))
    ack = (f' Please acknowledge this order and quote '
           f'<font name="{_BOLD}" color="#c8641e">{po_no}</font> on every related document and '
           f'correspondence.')
    flow.append(Paragraph(_esc(body) + ack, st['intro']))

    # The configurable table — arbitrary columns/rows, free text, no totals row
    # (Generic is non-priced). Natural pagination if it overflows (Table is not
    # wrapped in KeepTogether here). Section heading switched to the BULK centered treatment.
    flow += _section_label('ORDER DETAILS', st, dw, align='center', space_before=0.4 * cm, space_after=5.5)
    if columns:
        widths = _generic_col_widths(columns, dw)
        serial_idx = 0 if _is_serial_col(columns[0]) else None
        head = [Paragraph(_esc(c), st['thc']) for c in columns]
        body_rows = []
        for r in rows:
            cells = []
            for i in range(len(columns)):
                val = r[i] if i < len(r) else ''
                style = st['cellc'] if i == serial_idx else st['cell']
                cells.append(Paragraph(_esc(val), style))
            body_rows.append(cells)
        gtab = Table([head] + body_rows, colWidths=widths, repeatRows=1)
        gtab.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), _GREEN),
            ('GRID', (0, 0), (-1, -1), 0.4, _RULE),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('TOPPADDING', (0, 0), (-1, -1), 4.5), ('BOTTOMPADDING', (0, 0), (-1, -1), 4.5),
            ('LEFTPADDING', (0, 0), (-1, -1), 5), ('RIGHTPADDING', (0, 0), (-1, -1), 5),
        ]))
        flow.append(gtab)

    # Bill To Address / Ship To Address — BULK's exact treatment: centered headings
    # (seclabelc), header-row top/bottom padding 6, a LINEBELOW under the header row, address
    # row padding 3, Spacer(1,10) before the table.
    flow.append(Spacer(1, 10))
    bs = Table(
        [[Paragraph('BILL TO ADDRESS', st['seclabelc']), Paragraph('SHIP TO ADDRESS', st['seclabelc'])],
         [_addr_para(po, 'bill_to', st), _addr_para(po, 'ship_to', st)]],
        colWidths=[dw / 2, dw / 2])
    bs.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('BOX', (0, 0), (-1, -1), 0.5, _RULE),
        ('LINEBELOW', (0, 0), (-1, 0), 0.5, _RULE),
        ('LINEBEFORE', (1, 0), (1, -1), 0.5, _RULE),
        # General padding first (applies to every cell, including row 0), then the row-0
        # heading band's own wider padding is applied AFTER so it isn't clobbered — same
        # ordering convention as BULK's BILL TO ADDRESS / SHIP TO ADDRESS table.
        ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('LEFTPADDING', (0, 0), (-1, -1), 8), ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, 0), 6), ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
    ]))
    flow.append(bs)

    # Note band, then the shared closing greeting + signature block ("Thanking you," /
    # "Yours faithfully," / For IAL / signatory name-title-department).
    flow += _note_flow(po, st, dw)
    flow += _signature_flow(po, st, dw)

    # Terms & Conditions — DROPPED for GENERIC, exactly like BULK/JOB_WORK;
    # po.get('include_terms') is intentionally ignored here. _terms_flow()/_TERMS remain
    # defined (unused) so the section can be reinstated later if needed.
    return _build_pdf(flow, [], f'Purchase Order {po.get("po_no", "")}')


def render_po_pdf(po: dict) -> bytes:
    """Dispatch on po['po_type']. BULK renders byte-for-byte as before; JOB_WORK
    renders the multi-item layout; GENERIC renders the free-form configurable-table
    layout."""
    po_type = (po.get('po_type') or 'BULK').upper()
    if po_type == 'JOB_WORK':
        return _render_job_work_po_pdf(po)
    if po_type == 'GENERIC':
        return _render_generic_po_pdf(po)
    return _render_bulk_po_pdf(po)
