"""
letterhead — shared IAL letterhead (header + footer) for the alerts_evaluator
report PDFs, ported from procurement_api/po_pdf.py's _header()/_draw_footer()/
_styles() so every emailed report PDF (Customer/Supplier Balances FY, Monthly
Sales, Monthly Collection) matches the Purchase Order house design.

procurement_api/po_pdf.py itself is NOT modified or imported here — this is an
independent adaptation living entirely within the alerts_evaluator Lambda
package (no cross-Lambda import).

Public surface
--------------
GREEN, GREEN2, ORANGE, MUTED, RULE, BODY
    Palette constants — identical hex values to po_pdf.py.
BASE_FONT, BOLD_FONT
    'Helvetica' / 'Helvetica-Bold' — the PRIMARY body font for every report,
    matching the PO (Arial-metric). DejaVuSans is registered separately, ONLY
    to render the rupee glyph (see register_fonts()).
register_fonts() -> str
    Idempotent (delegates the actual TTFont registration to
    pdf_fonts.register_fonts()). Returns the rupee token to embed in Paragraph
    text: '<font name="DejaVuSans">₹</font>' normally, or the ASCII fallback
    'Rs.' if the bundled TTF failed to load — exactly like po_pdf.py's
    _register_fonts(). Callers must route ALL rupee-symbol text through
    Paragraph markup using this token; a plain, un-marked-up ₹ character in a
    Helvetica-styled Paragraph can KeyError (Helvetica/WinAnsiEncoding has no
    U+20B9 glyph).
build_header(dw) -> list[Flowable]
    Logo (left) + centered 'IRAVI AGRO LIFE LLP' / tagline / identity line +
    the green/orange double-rule beneath — the same sizes/colors as
    po_pdf.py's _header(). Callers append their own report title/date row
    immediately after this (mirrors how po_pdf.py appends the PO title + PO
    Number/Date box right after _header()+rules) — the report's own heading
    is NOT part of the shared letterhead.
draw_footer(canvas, doc)
    onFirstPage/onLaterPages callback — 0.6pt rule + two centered 7.5pt muted
    lines: the registered-office address, then
    'This document is computer-generated and is valid without signature.'
    (po_pdf.py's PO-specific wording reworded for generic reports). Reads
    doc.pagesize / doc.leftMargin / doc.rightMargin so it works unmodified on
    both portrait (Monthly Sales/Collection) and landscape (Customer/Supplier
    Balances FY) documents.

Reused by
---------
  customer_balances_fy_pdf.py, supplier_balances_fy_pdf.py,
  monthly_sales_pdf.py, monthly_collection_pdf.py
"""

from __future__ import annotations

import os

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.platypus import HRFlowable, Image, Paragraph, Spacer, Table, TableStyle

import pdf_fonts

_DIR = os.path.dirname(__file__)
_LOGO_PATH = os.path.join(_DIR, 'ial-logo.png')

# ── palette (ported verbatim from procurement_api/po_pdf.py) ────────────────
GREEN  = colors.HexColor('#17452f')
GREEN2 = colors.HexColor('#2d5c44')
ORANGE = colors.HexColor('#c8641e')
MUTED  = colors.HexColor('#555555')
RULE   = colors.HexColor('#c9c9c9')
BODY   = colors.HexColor('#1c1c1c')

BASE_FONT = 'Helvetica'
BOLD_FONT = 'Helvetica-Bold'

# Company identity (IAL's own) — constant, matches po_pdf.py.
_GSTIN = '37AALFI2946J1ZY'
_LLPIN = 'ACM-3958'
_EMAIL = 'info@iraviagrolife.com'
_WEB   = 'www.iraviagrolife.com'
_TAGLINE = 'Nurturing Life, Protecting the Harvest'

FOOTER_LINE1 = (
    'Registered Office: Flat No. 102, BVR Plaza, H.No. 5-3-112/2, BJP Office Line, '
    'Shanthi Nagar, Kukatpally, Hyderabad, Telangana 500072'
)
FOOTER_LINE2 = 'This document is computer-generated and is valid without signature.'


def register_fonts() -> str:
    """Idempotently register DejaVuSans (delegates to pdf_fonts.register_fonts(),
    shared with the rest of this Lambda) and return the inline-font rupee
    token — exactly like po_pdf.py's _register_fonts(). Helvetica/
    Helvetica-Bold remain the PRIMARY font for all report body text;
    DejaVuSans is used ONLY for this token."""
    pdf_fonts.register_fonts()
    try:
        if 'DejaVuSans' in pdfmetrics.getRegisteredFontNames():
            return '<font name="DejaVuSans">₹</font>'
    except Exception:
        pass
    return 'Rs.'


def _header_styles():
    def s(name, size, **kw):
        kw.setdefault('fontName', BASE_FONT)
        kw.setdefault('leading', size * 1.34)
        return ParagraphStyle(name, fontSize=size, **kw)
    return {
        'company':  s('LHCompany', 17, fontName=BOLD_FONT, textColor=GREEN, alignment=TA_CENTER, leading=20),
        'tagline':  s('LHTagline', 8.2, fontName=BOLD_FONT, textColor=ORANGE, alignment=TA_CENTER, leading=11),
        'identity': s('LHIdentity', 7.5, textColor=MUTED, alignment=TA_CENTER, leading=10),
    }


def build_header(dw) -> list:
    """Letterhead flowables: logo / company-name / tagline / identity-line
    table, followed by the green (2.2pt) then orange (0.8pt) double-rule.
    `dw` is the caller's usable content width (reportlab units — same value
    passed to Table colWidths / HRFlowable width elsewhere in the caller)."""
    st = _header_styles()
    logo_w = 1.5 * cm
    left = (Image(_LOGO_PATH, width=logo_w, height=logo_w * 530.0 / 471.0)
            if os.path.exists(_LOGO_PATH) else Paragraph('', st['identity']))
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
    return [
        t,
        Spacer(1, 2),
        HRFlowable(width=dw, thickness=2.2, color=GREEN, spaceBefore=2, spaceAfter=1.5),
        HRFlowable(width=dw, thickness=0.8, color=ORANGE, spaceAfter=3),
    ]


def draw_footer(canvas, doc):
    """onFirstPage/onLaterPages callback — registered-office + computer-
    generated note, styled like po_pdf.py's _draw_footer() but generalized to
    whatever page size/margins the caller's SimpleDocTemplate uses (works on
    both portrait and landscape documents)."""
    canvas.saveState()
    w = doc.pagesize[0]
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.6)
    canvas.line(doc.leftMargin, 0.95 * cm, w - doc.rightMargin, 0.95 * cm)
    canvas.setFont(BASE_FONT, 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawCentredString(w / 2, 0.66 * cm, FOOTER_LINE1)
    canvas.drawCentredString(w / 2, 0.46 * cm, FOOTER_LINE2)
    canvas.restoreState()
