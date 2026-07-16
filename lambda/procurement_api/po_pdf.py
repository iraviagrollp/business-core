"""
Bulk Purchase Order PDF renderer (reportlab).

`render_po_pdf(po: dict) -> bytes` — takes a joined purchase-order row (as returned
by handler._po_get_one) and renders a single-page A4 PDF in the house style of the
Customer Ledger Statement export: IAL logo top-left, "IRAVI AGRO LIFE LLP" centered,
dark-green (#1a3c2b) header bands with white text, green section labels, and a ruled
gray footer (Kukatpally reg address + computer-generated note). Content mirrors the
reference PO (D:\\2026\\IRA\\Reports\\POs\\Bulk\\IAL PO for PENOXSULAM 1.02 OD.pdf).
Helvetica + "Rs." only — no ₹ / no bundled fonts.
"""

import os
from datetime import date, datetime
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    BaseDocTemplate, Frame, Image, PageTemplate, Paragraph, Spacer, Table, TableStyle,
)

_GREEN = colors.HexColor('#1a3c2b')
_GREEN_BORDER = colors.HexColor('#2a5c3b')
_HIGHLIGHT = colors.HexColor('#FFF200')
_LOGO_PATH = os.path.join(os.path.dirname(__file__), 'ial-logo.png')

_INTRO = (
    'Please supply the under mentioned goods, subject to terms &amp; conditions '
    'stated below. Please also quote this order reference in all your supply '
    'documents and future correspondence.'
)
_FOOTER_1 = 'Reg. Address: Flat No: 102, BVR Plaza, H.No.5, 3-112/2, BJP Office Line'
_FOOTER_2 = 'Shanthi Nagar, Kukatpally, Hyderabad, Telangana 500072'
_FOOTER_3 = 'This purchase order is computer-generated.'


def _fmt_date(d) -> str:
    if not d:
        return ''
    if isinstance(d, str):
        d = datetime.strptime(d[:10], '%Y-%m-%d').date()
    if isinstance(d, datetime):
        d = d.date()
    if not isinstance(d, date):
        return str(d)
    day = d.day
    suffix = 'th' if 11 <= day % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th')
    return f'{day}{suffix} {d.strftime("%B")}, {d.year}'


def _fmt_qty(q, unit) -> str:
    unit = unit or ''
    try:
        qf = float(q)
    except (TypeError, ValueError):
        return f'{q} {unit}'.strip()
    s = f'{int(qf):,}' if qf == int(qf) else f'{qf:,.2f}'
    return f'{s} {unit}'.strip()


def _esc(v) -> str:
    return (str(v) if v is not None else '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def _address_html(po, prefix, styles):
    """Multi-line address for a bill/ship party as a single <br/>-joined Paragraph."""
    name = po.get(f'{prefix}_company_name')
    if not name:
        return Paragraph('&mdash;', styles['cell'])
    lines = [f'<b>{_esc(name)}</b>']
    for key in ('address_line1', 'address_line2', 'address_line3'):
        if po.get(f'{prefix}_{key}'):
            lines.append(_esc(po[f'{prefix}_{key}']))
    if po.get(f'{prefix}_state'):
        lines.append(f'State: {_esc(po[f"{prefix}_state"])}')
    if po.get(f'{prefix}_gstin'):
        lines.append(f'GST: {_esc(po[f"{prefix}_gstin"])}')
    return Paragraph('<br/>'.join(lines), styles['cell'])


def _styles():
    return {
        'company': ParagraphStyle('company', fontName='Helvetica-Bold', fontSize=20,
                                  alignment=TA_CENTER, leading=23),
        'title': ParagraphStyle('title', fontName='Helvetica-Bold', fontSize=12,
                                 alignment=TA_CENTER, spaceBefore=2, leading=15),
        'meta': ParagraphStyle('meta', fontName='Helvetica', fontSize=10, leading=15),
        'supplier': ParagraphStyle('supplier', fontName='Helvetica-Bold', fontSize=10.5, leading=16),
        'body': ParagraphStyle('body', fontName='Helvetica', fontSize=10, leading=14, spaceBefore=6),
        'label': ParagraphStyle('label', fontName='Helvetica', fontSize=10, leading=14),
        'value': ParagraphStyle('value', fontName='Helvetica-Bold', fontSize=10, leading=14),
        'addrhead': ParagraphStyle('addrhead', fontName='Helvetica-Bold', fontSize=10,
                                   textColor=colors.white, leading=13),
        'cell': ParagraphStyle('cell', fontName='Helvetica', fontSize=9.5, leading=13),
        'note': ParagraphStyle('note', fontName='Helvetica-Bold', fontSize=10,
                               alignment=TA_CENTER, leading=14),
        'sign': ParagraphStyle('sign', fontName='Helvetica', fontSize=10, leading=15),
    }


def _header_flowable(st, doc):
    """IAL logo left + company name/title centered over the full page width."""
    company = [Paragraph('IRAVI AGRO LIFE LLP', st['company']),
               Paragraph('PURCHASE ORDER', st['title'])]
    logo_w = 2.0 * cm
    if os.path.exists(_LOGO_PATH):
        img = Image(_LOGO_PATH, width=logo_w, height=logo_w * 530.0 / 471.0)
        left = img
    else:
        left = Paragraph('', st['meta'])
    tbl = Table([[left, company, '']], colWidths=[logo_w, doc.width - 2 * logo_w, logo_w])
    tbl.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (0, 0), (0, 0), 'LEFT'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
    ]))
    return tbl


def _draw_footer(canvas, doc):
    canvas.saveState()
    w = A4[0]
    canvas.setStrokeColor(colors.HexColor('#cccccc'))
    canvas.setLineWidth(0.6)
    canvas.line(doc.leftMargin, 1.95 * cm, w - doc.rightMargin, 1.95 * cm)
    canvas.setFont('Helvetica', 7.5)
    canvas.setFillColor(colors.HexColor('#555555'))
    canvas.drawCentredString(w / 2, 1.55 * cm, _FOOTER_1)
    canvas.drawCentredString(w / 2, 1.25 * cm, _FOOTER_2)
    canvas.drawCentredString(w / 2, 0.95 * cm, _FOOTER_3)
    canvas.restoreState()


def render_po_pdf(po: dict) -> bytes:
    st = _styles()
    buf = BytesIO()
    doc = BaseDocTemplate(
        buf, pagesize=A4,
        leftMargin=1.8 * cm, rightMargin=1.8 * cm, topMargin=1.4 * cm, bottomMargin=2.3 * cm,
        title=f'Purchase Order {po.get("po_no", "")}',
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id='main')
    doc.addPageTemplates([PageTemplate(id='po', frames=[frame], onPage=_draw_footer)])

    flow = [_header_flowable(st, doc), Spacer(1, 0.5 * cm)]

    flow.append(Paragraph(f'<b>PO:</b> {_esc(po.get("po_no"))}', st['meta']))
    flow.append(Paragraph(f'<b>Date:</b> {_esc(_fmt_date(po.get("po_date")))}', st['meta']))
    flow.append(Spacer(1, 0.4 * cm))

    # Supplier block (bold).
    flow.append(Paragraph(_esc(po.get('supplier_company_name')), st['supplier']))
    for key in ('supplier_address_line1', 'supplier_address_line2', 'supplier_address_line3'):
        if po.get(key):
            flow.append(Paragraph(_esc(po[key]), st['supplier']))
    if po.get('supplier_gstin'):
        flow.append(Paragraph(f'GSTIN: {_esc(po["supplier_gstin"])}.', st['supplier']))

    flow.append(Paragraph('Dear Sir/Madam,', st['body']))
    flow.append(Paragraph(_INTRO, st['body']))
    flow.append(Spacer(1, 0.25 * cm))

    # Field rows.
    fields = [
        ('Product', _esc(po.get('technical_name'))),
        ('Quantity', _esc(_fmt_qty(po.get('quantity'), po.get('quantity_unit')))),
        ('Price', _esc(po.get('price'))),
        ('GST', _esc(po.get('gst'))),
        ('Terms', _esc(po.get('terms'))),
        ('Dispatch', _esc(po.get('dispatch'))),
        ('Transport', _esc(po.get('transport'))),
    ]
    data = [[Paragraph(lbl, st['label']), Paragraph(':', st['label']),
             Paragraph(val or '&mdash;', st['value'])] for lbl, val in fields]
    ftab = Table(data, colWidths=[3.2 * cm, 0.5 * cm, doc.width - 3.7 * cm])
    ftab.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ('LEFTPADDING', (0, 0), (0, -1), 0),
    ]))
    flow.append(ftab)
    flow.append(Spacer(1, 0.4 * cm))

    # Bill To / Ship To table — green header band (white text) over a bordered box.
    half = doc.width / 2
    addr = Table(
        [
            [Paragraph('BILL TO ADDRESS:', st['addrhead']), Paragraph('SHIP TO ADDRESS:', st['addrhead'])],
            [_address_html(po, 'bill_to', st), _address_html(po, 'ship_to', st)],
        ],
        colWidths=[half, half],
    )
    addr.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BACKGROUND', (0, 0), (-1, 0), _GREEN),
        ('BOX', (0, 0), (-1, -1), 0.75, _GREEN_BORDER),
        ('LINEBEFORE', (1, 0), (1, -1), 0.75, _GREEN_BORDER),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
    ]))
    flow.append(addr)

    # Highlighted note.
    note = po.get('note')
    if note:
        note_tbl = Table([[Paragraph(f'Note: {_esc(note)}', st['note'])]], colWidths=[doc.width])
        note_tbl.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), _HIGHLIGHT),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))
        flow.append(Spacer(1, 0.15 * cm))
        flow.append(note_tbl)

    # Signature block.
    flow.append(Spacer(1, 0.5 * cm))
    flow.append(Paragraph('Thanking You', st['sign']))
    flow.append(Paragraph('Yours Faithfully', st['sign']))
    flow.append(Paragraph('For IRAVI AGRO LIFE LLP', st['sign']))
    flow.append(Spacer(1, 1.1 * cm))  # room for signature / stamp
    if po.get('signatory_name'):
        flow.append(Paragraph(f'<b>{_esc(po["signatory_name"])}</b>', st['sign']))
    if po.get('signatory_title'):
        flow.append(Paragraph(_esc(po['signatory_title']), st['sign']))
    if po.get('signatory_department'):
        flow.append(Paragraph(_esc(po['signatory_department']), st['sign']))

    doc.build(flow)
    return buf.getvalue()
