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
    BaseDocTemplate, Frame, HRFlowable, Image, KeepTogether, PageBreak, PageTemplate, Paragraph,
    Spacer, Table, TableStyle,
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
        'boxlabel': s('boxlabel', 8.4, fontName=_BOLD, textColor=_BODY),
        'boxval': s('boxval', 8.4, textColor=_BODY),
        'name': s('name', 9.8, fontName=_BOLD, textColor=_GREEN),
        'body': s('body', 8.3, textColor=_BODY),
        'bodyc': s('bodyc', 8.3, textColor=_BODY, alignment=TA_CENTER),
        'bodyb': s('bodyb', 9.4, fontName=_BOLD, textColor=_BODY),
        'th': s('th', 7.5, fontName=_BOLD, textColor=colors.white),
        'thr': s('thr', 7.5, fontName=_BOLD, textColor=colors.white, alignment=TA_RIGHT),
        'thc': s('thc', 7.5, fontName=_BOLD, textColor=colors.white, alignment=TA_CENTER),
        'cell': s('cell', 8.7, textColor=_BODY),
        'cellc': s('cellc', 8.7, textColor=_BODY, alignment=TA_CENTER),
        'cellr': s('cellr', 8.7, textColor=_BODY, alignment=TA_RIGHT),
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


def _section_label(text, st, width):
    """Green uppercase label with a hairline rule beneath (full width)."""
    return [Spacer(1, 0.11 * cm),
            Paragraph(text, st['seclabel']),
            HRFlowable(width=width, thickness=0.5, color=_RULE, spaceBefore=2, spaceAfter=4)]


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
        [Paragraph('PO Number', st['boxlabel']), Paragraph(_esc(po.get('po_no')), st['boxval'])],
        [Paragraph('PO Date', st['boxlabel']), Paragraph(_esc(_fmt_date(po.get('po_date'))), st['boxval'])],
    ]
    t = Table(rows, colWidths=[2.4 * cm, 3.6 * cm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, -1), _GRAYLABEL),
        ('GRID', (0, 0), (-1, -1), 0.5, _RULE),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
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


def _vendor_box(po, st, dw):
    """Supplier/vendor identity box: name + address, with the GSTIN appended inline
    to the last address line (same paragraph) instead of on its own line."""
    ven = [Paragraph(_esc(po.get('supplier_company_name')), st['name'])]
    sup_lines = [po.get(k) for k in
                 ('supplier_address_line1', 'supplier_address_line2', 'supplier_address_line3') if po.get(k)]
    addr_text = ', '.join(_esc(x) for x in sup_lines) if sup_lines else ''
    if po.get('supplier_gstin'):
        gstin_text = f'GSTIN: {_esc(po["supplier_gstin"])}'
        addr_text = f'{addr_text}&nbsp;&nbsp;&nbsp;{gstin_text}' if addr_text else gstin_text
    if addr_text:
        ven.append(Paragraph(addr_text, st['body']))
    vbox = Table([[ven]], colWidths=[dw])
    vbox.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BOX', (0, 0), (-1, -1), 0.5, _RULE),
        ('BACKGROUND', (0, 0), (-1, -1), _TINT),
        ('TOPPADDING', (0, 0), (-1, -1), 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING', (0, 0), (-1, -1), 9), ('RIGHTPADDING', (0, 0), (-1, -1), 9),
    ]))
    return vbox


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
        ('TOPPADDING', (0, 0), (-1, -1), 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING', (0, 0), (-1, -1), 10), ('RIGHTPADDING', (0, 0), (-1, -1), 8),
    ]))
    return [Spacer(1, 4), nb]


def _signature_flow(po, st, dw):
    """Thanking-you / for-IAL / signature-line block — shared by BULK and JOB_WORK.
    Wrapped in KeepTogether so the block moves as one atomic unit if it doesn't fit
    in the remaining space on the page, instead of splitting mid-block (e.g. the
    HRFlowable signature line landing on one page and the signatory name on the
    next)."""
    inner = [
        Spacer(1, 3),
        Paragraph('Thanking you,', st['sign']),
        Paragraph('Yours faithfully,', st['sign']),
        Spacer(1, 6),
        Paragraph(f'For <font name="{_BOLD}">IRAVI AGRO LIFE LLP</font>', st['signr']),
        Spacer(1, 30),  # room for a physical signature
        HRFlowable(width=dw / 2, thickness=0.6, color=_MUTED, hAlign='RIGHT', spaceAfter=4),
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
    append it inline or push it onto a fresh page."""
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


def _build_pdf(flow_builder, title):
    """Two-pass build so the core PO content (everything up to and including the
    signature block) never splits around the Terms & Conditions section.

    `flow_builder()` returns a FRESH `(core_flow, terms_flow)` pair of flowable
    lists on every call — reportlab flowables are stateful (wrap()/split() mutate
    internal layout caches during doc.build()), so each build pass must be handed
    brand-new Paragraph/Table instances rather than reusing ones from a prior pass.

    Pass 1: render core_flow + terms_flow back-to-back on the normal frame flow. If
    that already fits on a single page (or there's no terms section to move), keep
    it — no forced page break, no blank page.

    Pass 2 (only if pass 1 overflowed AND a terms section exists): insert an explicit
    page break immediately before Terms & Conditions and rebuild from scratch, so the
    whole section lands together on a fresh page 2 instead of splitting across the
    boundary. This works for any number of grid/item rows — no per-line hacks.
    """
    def _try(flow):
        buf = BytesIO()
        doc = BaseDocTemplate(
            buf, pagesize=A4,
            leftMargin=1.5 * cm, rightMargin=1.5 * cm, topMargin=0.4 * cm, bottomMargin=1.1 * cm,
            title=title,
        )
        frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id='main')
        page_count = [0]

        def _on_page(canvas, d):
            page_count[0] += 1
            _draw_footer(canvas, d)

        doc.addPageTemplates([PageTemplate(id='po', frames=[frame], onPage=_on_page)])
        doc.build(flow)
        return buf.getvalue(), page_count[0]

    core_flow, terms_flow = flow_builder()
    pdf_bytes, pages = _try(core_flow + terms_flow)
    if pages > 1 and terms_flow:
        core_flow2, terms_flow2 = flow_builder()
        pdf_bytes, _pages2 = _try(core_flow2 + [PageBreak()] + terms_flow2)
    return pdf_bytes


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

    def _flow():
        return _bulk_flow(po, st, dw, qty, rate, gst_rate, gst_lbl, amount, gst_amt, total)

    return _build_pdf(_flow, f'Purchase Order {po.get("po_no", "")}')


def _bulk_flow(po, st, dw, qty, rate, gst_rate, gst_lbl, amount, gst_amt, total):
    """Builds a FRESH (core_flow, terms_flow) pair — called once per _build_pdf pass."""
    flow = [_header(st, dw), Spacer(1, 2)]
    flow.append(HRFlowable(width=dw, thickness=2.2, color=_GREEN, spaceBefore=2, spaceAfter=1.5))
    flow.append(HRFlowable(width=dw, thickness=0.8, color=_ORANGE, spaceAfter=3))

    # Title row + PO box.
    trow = Table([[_po_title_cell(st), _po_box(st, po)]], colWidths=[dw - 6.0 * cm, 6.0 * cm])
    trow.setStyle(TableStyle([('VALIGN', (0, 0), (0, 0), 'MIDDLE'), ('VALIGN', (1, 0), (1, 0), 'TOP'),
                              ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0)]))
    flow.append(trow)

    # Vendor / Supplier — boxed so it reads as a distinct unit, separate from the
    # salutation below.
    flow += _section_label('VENDOR / SUPPLIER', st, dw)
    flow.append(_vendor_box(po, st, dw))

    # Salutation + body.
    flow.append(Spacer(1, 11))
    flow.append(Paragraph('Dear Sir / Madam,', st['bodyb']))
    po_no = _esc(po.get('po_no'))
    body = (f'We are pleased to place the following order with you, on the terms set out below. Please '
            f'<font name="{_BOLD}" color="#17452f">acknowledge this order</font> and quote '
            f'<font name="{_BOLD}" color="#17452f">{po_no}</font> on every invoice, delivery challan, '
            f'e-way bill and communication relating to this supply.')
    flow.append(Paragraph(body, st['bodyb']))

    # Order details table.
    flow += _section_label('ORDER DETAILS', st, dw)
    head = [Paragraph('SL.', st['thc']), Paragraph('DESCRIPTION OF GOODS', st['th']),
            Paragraph('QUANTITY', st['thc']), Paragraph('UOM', st['thc']),
            Paragraph(f'RATE ({_RS})', st['thr']), Paragraph(f'AMOUNT ({_RS})', st['thr'])]
    desc = Paragraph(_esc(po.get('technical_name')), st['prod'])
    row = [Paragraph('1', st['cellc']), desc, Paragraph(_fmt_qty(qty), st['cellc']),
           Paragraph(_esc(po.get('quantity_unit')), st['cellc']),
           Paragraph(_inr(rate), st['cellr']), Paragraph(_inr(amount), st['cellr'])]
    col = [1.15 * cm, dw - 1.15 * cm - 2.3 * cm - 1.3 * cm - 2.5 * cm - 2.9 * cm, 2.3 * cm, 1.3 * cm, 2.5 * cm, 2.9 * cm]
    gtab = Table([head, row], colWidths=col)
    gtab.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), _GREEN),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('VALIGN', (1, 1), (1, 1), 'TOP'),
        # Column separators — lighter green in the header row, light gray in the data row.
        ('LINEBEFORE', (1, 0), (5, 0), 0.7, _GREEN2),
        ('LINEBEFORE', (1, 1), (5, 1), 0.7, _GRAYLABEL),
        ('LINEBELOW', (0, 1), (-1, 1), 0.6, colors.HexColor('#dcdcdc')),
        ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('LEFTPADDING', (0, 0), (-1, -1), 5), ('RIGHTPADDING', (0, 0), (-1, -1), 5),
    ]))
    flow.append(gtab)

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
        ('LINEBELOW', (0, 0), (-1, 0), 0.5, _RULE), ('LINEBELOW', (0, 1), (-1, 1), 0.5, _RULE),
        ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 8), ('RIGHTPADDING', (0, 0), (-1, -1), 8),
    ]))
    tot = Table([[words_cell, right]], colWidths=[dw / 2, dw / 2])
    tot.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, 0), _TINT), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BOX', (0, 0), (0, 0), 0.5, _RULE),
        ('TOPPADDING', (0, 0), (0, 0), 5), ('BOTTOMPADDING', (0, 0), (0, 0), 5),
        ('LEFTPADDING', (0, 0), (0, 0), 8), ('RIGHTPADDING', (0, 0), (0, 0), 8),
        ('LEFTPADDING', (1, 0), (1, 0), 6), ('RIGHTPADDING', (1, 0), (1, 0), 0),
        ('TOPPADDING', (1, 0), (1, 0), 0), ('BOTTOMPADDING', (1, 0), (1, 0), 0),
    ]))
    flow.append(Spacer(1, 4))
    flow.append(tot)

    # Bill To / Ship To.
    flow.append(Spacer(1, 3))
    bs = Table(
        [[Paragraph('BILL TO', st['seclabel']), Paragraph('SHIP TO', st['seclabel'])],
         [_addr_para(po, 'bill_to', st), _addr_para(po, 'ship_to', st)]],
        colWidths=[dw / 2, dw / 2])
    bs.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('BOX', (0, 0), (-1, -1), 0.5, _RULE),
        ('LINEBEFORE', (1, 0), (1, -1), 0.5, _RULE),
        ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('LEFTPADDING', (0, 0), (-1, -1), 8), ('RIGHTPADDING', (0, 0), (-1, -1), 8),
    ]))
    flow.append(bs)

    # Commercial terms.
    flow += _section_label('COMMERCIAL TERMS', st, dw)
    ct = [
        ('Payment Terms', po.get('terms')),
        ('Dispatch Schedule', po.get('dispatch')),
        ('Mode of Transport', po.get('transport')),
        ('Taxes', f'GST @ {gst_lbl}% extra as applicable; rate quoted is exclusive of GST'),
    ]
    ctd = [[Paragraph(lbl, st['ctlabel']), Paragraph(_esc(val) if val else '&mdash;', st['ctval'])] for lbl, val in ct]
    ctab = Table(ctd, colWidths=[4.6 * cm, dw - 4.6 * cm])
    ctab.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, -1), _GRAYLABEL), ('GRID', (0, 0), (-1, -1), 0.5, _RULE),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 2.5), ('BOTTOMPADDING', (0, 0), (-1, -1), 2.5),
        ('LEFTPADDING', (0, 0), (-1, -1), 8), ('RIGHTPADDING', (0, 0), (-1, -1), 8),
    ]))
    flow.append(ctab)

    # Note band, then signature — both are "core" content that must stay on page 1
    # alongside everything above. Terms & Conditions (below) is the section that may
    # be pushed to a fresh page 2 if the core content doesn't leave room for it.
    flow += _note_flow(po, st, dw)
    flow += _signature_flow(po, st, dw)

    terms_flow = _terms_flow(st, dw) if po.get('include_terms', True) else []
    return flow, terms_flow


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

    def _flow():
        return _job_work_flow(po, st, dw, items, gst_rate, gst_lbl, amount, gst_amt, total,
                               header_unit, base_unit, dash)

    return _build_pdf(_flow, f'Job Work Purchase Order {po.get("po_no", "")}')


def _job_work_flow(po, st, dw, items, gst_rate, gst_lbl, amount, gst_amt, total,
                    header_unit, base_unit, dash):
    """Builds a FRESH (core_flow, terms_flow) pair — called once per _build_pdf pass."""
    flow = [_header(st, dw), Spacer(1, 2)]
    flow.append(HRFlowable(width=dw, thickness=2.2, color=_GREEN, spaceBefore=2, spaceAfter=1.5))
    flow.append(HRFlowable(width=dw, thickness=0.8, color=_ORANGE, spaceAfter=3))

    # Title row + PO box — same "PURCHASE ORDER" banner as BULK.
    trow = Table([[_po_title_cell(st), _po_box(st, po)]], colWidths=[dw - 6.0 * cm, 6.0 * cm])
    trow.setStyle(TableStyle([('VALIGN', (0, 0), (0, 0), 'MIDDLE'), ('VALIGN', (1, 0), (1, 0), 'TOP'),
                              ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0)]))
    flow.append(trow)

    # Job Worker (the reused supplier_company_id).
    flow += _section_label('JOB WORKER', st, dw)
    flow.append(_vendor_box(po, st, dw))

    # Product + header quantity — adjacent to the Job Worker block; center-aligned
    # per the approved layout.
    flow += _section_label('PRODUCT', st, dw)
    prod_line = (f'<font name="{_BOLD}">{_esc(po.get("technical_name"))}</font>'
                 f' &nbsp;{dash}&nbsp; Brand: {_esc(po.get("brand_name") or dash)}'
                 f' &nbsp;{dash}&nbsp; Quantity: <font name="{_BOLD}">{_fmt_qty(po.get("quantity"))} '
                 f'{_esc(header_unit)}</font>')
    pbox = Table([[Paragraph(prod_line, st['bodyc'])]], colWidths=[dw])
    pbox.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BOX', (0, 0), (-1, -1), 0.5, _RULE),
        ('BACKGROUND', (0, 0), (-1, -1), _TINT),
        ('TOPPADDING', (0, 0), (-1, -1), 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING', (0, 0), (-1, -1), 9), ('RIGHTPADDING', (0, 0), (-1, -1), 9),
    ]))
    flow.append(pbox)

    # Salutation + body.
    flow.append(Spacer(1, 11))
    flow.append(Paragraph('Dear Sir / Madam,', st['bodyb']))
    po_no = _esc(po.get('po_no'))
    body = (f'We are pleased to place the following job work order with you, on the terms set out below. '
            f'Please <font name="{_BOLD}" color="#17452f">acknowledge this order</font> and quote '
            f'<font name="{_BOLD}" color="#17452f">{po_no}</font> on every invoice, delivery challan, '
            f'e-way bill and communication relating to this supply.')
    flow.append(Paragraph(body, st['bodyb']))

    # Order details — multi-row particulars grid (one row per item).
    flow += _section_label('ORDER DETAILS', st, dw)
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
    total_row = ['', Paragraph('TOTAL', st['bodyb']), '', '', Paragraph(_inr(amount), st['cellr'])]
    gtab = Table([head] + body_rows + [total_row], colWidths=col, repeatRows=1)
    gtab.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), _GREEN),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LINEBEFORE', (1, 0), (4, 0), 0.7, _GREEN2),
        ('LINEBELOW', (0, 1), (-1, total_row_idx - 1), 0.4, colors.HexColor('#dcdcdc')),
        ('SPAN', (0, total_row_idx), (3, total_row_idx)),
        ('LINEABOVE', (0, total_row_idx), (-1, total_row_idx), 0.7, _GREEN),
        ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('LEFTPADDING', (0, 0), (-1, -1), 5), ('RIGHTPADDING', (0, 0), (-1, -1), 5),
    ]))
    flow.append(gtab)

    # Totals: words (left) + taxable/gst/total (right) — same band as BULK, fed by
    # the item-grid's Σ amount.
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
        ('LINEBELOW', (0, 0), (-1, 0), 0.5, _RULE), ('LINEBELOW', (0, 1), (-1, 1), 0.5, _RULE),
        ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 8), ('RIGHTPADDING', (0, 0), (-1, -1), 8),
    ]))
    tot = Table([[words_cell, right]], colWidths=[dw / 2, dw / 2])
    tot.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, 0), _TINT), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BOX', (0, 0), (0, 0), 0.5, _RULE),
        ('TOPPADDING', (0, 0), (0, 0), 5), ('BOTTOMPADDING', (0, 0), (0, 0), 5),
        ('LEFTPADDING', (0, 0), (0, 0), 8), ('RIGHTPADDING', (0, 0), (0, 0), 8),
        ('LEFTPADDING', (1, 0), (1, 0), 6), ('RIGHTPADDING', (1, 0), (1, 0), 0),
        ('TOPPADDING', (1, 0), (1, 0), 0), ('BOTTOMPADDING', (1, 0), (1, 0), 0),
    ]))
    flow.append(Spacer(1, 4))
    flow.append(tot)

    # To Be Billed On / Delivered At (relabeled BILL TO / SHIP TO, same _addr_para()).
    flow.append(Spacer(1, 3))
    bs = Table(
        [[Paragraph('TO BE BILLED ON', st['seclabel']), Paragraph('DELIVERED AT', st['seclabel'])],
         [_addr_para(po, 'bill_to', st), _addr_para(po, 'ship_to', st)]],
        colWidths=[dw / 2, dw / 2])
    bs.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('BOX', (0, 0), (-1, -1), 0.5, _RULE),
        ('LINEBEFORE', (1, 0), (1, -1), 0.5, _RULE),
        ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('LEFTPADDING', (0, 0), (-1, -1), 8), ('RIGHTPADDING', (0, 0), (-1, -1), 8),
    ]))
    flow.append(bs)

    # Commercial terms.
    flow += _section_label('COMMERCIAL TERMS', st, dw)
    ct = [
        ('Payment Terms', po.get('terms')),
        ('Dispatch Schedule', po.get('dispatch')),
        ('Mode of Transport', po.get('transport')),
        ('Taxes', f'GST @ {gst_lbl}% extra as applicable; rate quoted is exclusive of GST'),
    ]
    ctd = [[Paragraph(lbl, st['ctlabel']), Paragraph(_esc(val) if val else '&mdash;', st['ctval'])] for lbl, val in ct]
    ctab = Table(ctd, colWidths=[4.6 * cm, dw - 4.6 * cm])
    ctab.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, -1), _GRAYLABEL), ('GRID', (0, 0), (-1, -1), 0.5, _RULE),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 2.5), ('BOTTOMPADDING', (0, 0), (-1, -1), 2.5),
        ('LEFTPADDING', (0, 0), (-1, -1), 8), ('RIGHTPADDING', (0, 0), (-1, -1), 8),
    ]))
    flow.append(ctab)

    # Note band, then signature (shared) — both are "core" content that must stay
    # on page 1. Terms & Conditions (below) may be pushed to a fresh page 2.
    flow += _note_flow(po, st, dw)
    flow += _signature_flow(po, st, dw)

    terms_flow = _terms_flow(st, dw) if po.get('include_terms', True) else []
    return flow, terms_flow


def render_po_pdf(po: dict) -> bytes:
    """Dispatch on po['po_type']. BULK renders byte-for-byte as before; JOB_WORK
    renders the multi-item layout."""
    if (po.get('po_type') or 'BULK').upper() == 'JOB_WORK':
        return _render_job_work_po_pdf(po)
    return _render_bulk_po_pdf(po)
