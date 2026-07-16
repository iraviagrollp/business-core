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
    BaseDocTemplate, Frame, HRFlowable, Image, PageTemplate, Paragraph, Spacer, Table, TableStyle,
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
    "This order number must be quoted on all invoices, delivery challans, e-way bills, packing lists "
    "and correspondence.",
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
        kw.setdefault('leading', size * 1.4)
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
        'tc': s('tc', 7.2, textColor=_MUTED, leading=9.4),
        'note': s('note', 8.2, textColor=_BODY),
        'sign': s('sign', 8.3, textColor=_BODY),
        'signr': s('signr', 8.3, textColor=_BODY, alignment=TA_RIGHT),
        'signrb': s('signrb', 9.4, fontName=_BOLD, textColor=_BODY, alignment=TA_RIGHT),
        'signrs': s('signrs', 7.6, textColor=_MUTED, alignment=TA_RIGHT),
    }


def _section_label(text, st, width):
    """Green uppercase label with a hairline rule beneath (full width)."""
    return [Spacer(1, 0.24 * cm),
            Paragraph(text, st['seclabel']),
            HRFlowable(width=width, thickness=0.5, color=_RULE, spaceBefore=2, spaceAfter=4)]


def _draw_footer(canvas, doc):
    canvas.saveState()
    w = A4[0]
    canvas.setStrokeColor(_RULE)
    canvas.setLineWidth(0.6)
    canvas.line(doc.leftMargin, 1.15 * cm, w - doc.rightMargin, 1.15 * cm)
    canvas.setFont(_BASE, 7.5)
    canvas.setFillColor(_MUTED)
    canvas.drawCentredString(w / 2, 0.84 * cm, _FOOTER_1)
    canvas.drawCentredString(w / 2, 0.6 * cm, _FOOTER_2)
    canvas.restoreState()


def _header(st, doc):
    logo_w = 1.65 * cm
    left = (Image(_LOGO_PATH, width=logo_w, height=logo_w * 530.0 / 471.0)
            if os.path.exists(_LOGO_PATH) else Paragraph('', st['body']))
    center = [
        Paragraph('IRAVI AGRO LIFE LLP', st['company']),
        Paragraph(f'<i>{_TAGLINE}</i>', st['tagline']),
        Spacer(1, 2),
        Paragraph(f'GSTIN: {_GSTIN} &nbsp;|&nbsp; LLPIN: {_LLPIN} &nbsp;|&nbsp; {_EMAIL} &nbsp;|&nbsp; {_WEB}',
                  st['identity']),
    ]
    t = Table([[left, center, '']], colWidths=[logo_w + 0.3 * cm, doc.width - 2 * (logo_w + 0.3 * cm), logo_w + 0.3 * cm])
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


def render_po_pdf(po: dict) -> bytes:
    st = _styles()
    buf = BytesIO()
    doc = BaseDocTemplate(
        buf, pagesize=A4,
        leftMargin=1.5 * cm, rightMargin=1.5 * cm, topMargin=0.7 * cm, bottomMargin=1.0 * cm,
        title=f'Purchase Order {po.get("po_no", "")}',
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id='main')
    doc.addPageTemplates([PageTemplate(id='po', frames=[frame], onPage=_draw_footer)])
    dw = doc.width

    qty = float(po.get('quantity') or 0)
    rate = float(po.get('rate') or 0)
    gst_rate = float(po.get('gst_rate') or 0)
    amount = float(po.get('amount') if po.get('amount') is not None else qty * rate)
    gst_amt = float(po.get('gst_amount') if po.get('gst_amount') is not None else round(amount * gst_rate / 100, 2))
    total = float(po.get('total_value') if po.get('total_value') is not None else round(amount + gst_amt, 2))
    gst_lbl = f'{gst_rate:g}'

    flow = [_header(st, doc), Spacer(1, 2)]
    flow.append(HRFlowable(width=dw, thickness=2.2, color=_GREEN, spaceBefore=2, spaceAfter=1.5))
    flow.append(HRFlowable(width=dw, thickness=0.8, color=_ORANGE, spaceAfter=3))

    # Title row + PO box.
    title_cell = [Paragraph('P U R C H A S E &nbsp; O R D E R', st['potitle']),
                  HRFlowable(width=3.2 * cm, thickness=2.2, color=_ORANGE, spaceBefore=4, hAlign='LEFT')]
    trow = Table([[title_cell, _po_box(st, po)]], colWidths=[dw - 6.0 * cm, 6.0 * cm])
    trow.setStyle(TableStyle([('VALIGN', (0, 0), (0, 0), 'MIDDLE'), ('VALIGN', (1, 0), (1, 0), 'TOP'),
                              ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0)]))
    flow.append(trow)

    # Vendor / Supplier.
    flow += _section_label('VENDOR / SUPPLIER', st, dw)
    flow.append(Paragraph(_esc(po.get('supplier_company_name')), st['name']))
    sup_lines = [po.get(k) for k in ('supplier_address_line1', 'supplier_address_line2', 'supplier_address_line3') if po.get(k)]
    if sup_lines:
        flow.append(Paragraph(', '.join(_esc(x) for x in sup_lines), st['body']))
    if po.get('supplier_gstin'):
        flow.append(Paragraph(f'GSTIN: {_esc(po["supplier_gstin"])}', st['body']))

    # Salutation + body.
    flow.append(Spacer(1, 3))
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

    # Terms & conditions.
    flow += _section_label('TERMS & CONDITIONS', st, dw)
    tcd = [[Paragraph(f'{i}.', st['tc']), Paragraph(_esc(t), st['tc'])] for i, t in enumerate(_TERMS, 1)]
    tctab = Table(tcd, colWidths=[0.6 * cm, dw - 0.6 * cm])
    tctab.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 1), ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
        ('LEFTPADDING', (0, 0), (0, -1), 2), ('LEFTPADDING', (1, 0), (1, -1), 2),
    ]))
    flow.append(tctab)

    # Note band.
    note = po.get('note')
    if note:
        nb = Table([[Paragraph(f'<font name="{_BOLD}">Note:</font> {_esc(note)}', st['note'])]], colWidths=[dw])
        nb.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), _PEACH), ('LINEBEFORE', (0, 0), (0, -1), 3, _ORANGE),
            ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ('LEFTPADDING', (0, 0), (-1, -1), 10), ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ]))
        flow.append(Spacer(1, 4))
        flow.append(nb)

    # Signature.
    flow.append(Spacer(1, 3))
    left_sig = [Paragraph('Thanking you,', st['sign']), Paragraph('Yours faithfully,', st['sign'])]
    right_sig = [Paragraph(f'For <font name="{_BOLD}">IRAVI AGRO LIFE LLP</font>', st['signr']),
                 Spacer(1, 22),  # room for a physical signature
                 HRFlowable(width=dw / 2, thickness=0.6, color=_MUTED, hAlign='RIGHT', spaceAfter=4)]
    if po.get('signatory_name'):
        right_sig.append(Paragraph(_esc(po['signatory_name']), st['signrb']))
    sub = ' — '.join(_esc(po[k]) for k in ('signatory_title', 'signatory_department') if po.get(k))
    if sub:
        right_sig.append(Paragraph(sub, st['signrs']))
    sig = Table([[left_sig, right_sig]], colWidths=[dw / 2, dw / 2])
    sig.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'),
                             ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0)]))
    flow.append(sig)

    doc.build(flow)
    return buf.getvalue()
