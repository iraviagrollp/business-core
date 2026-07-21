"""
pdf_fonts — shared Unicode TTF font registration for PDF generation.

Public surface
--------------
register_fonts() -> None
    Idempotent.  Registers DejaVuSans and DejaVuSans-Bold with reportlab's
    pdfmetrics so that U+20B9 (₹) and U+2014 (—) can be rendered without
    KeyError in doc.build().  Falls back to Helvetica with a warning if the
    bundled TTF files are not found or cannot be loaded.

Bundled fonts (same directory as this module):
  DejaVuSans.ttf        — DejaVu Sans Regular (full Unicode including ₹ / —)
  DejaVuSans-Bold.ttf   — DejaVu Sans Bold

Source of bundled TTFs
----------------------
Copied from matplotlib's bundled fonts directory:
    python -c "import matplotlib,os; print(os.path.join(
        os.path.dirname(matplotlib.__file__), 'mpl-data/fonts/ttf'))"

Reused by
---------
  customer_balances_fy_pdf.py     — Customer Balances (FY) PDF renderer
  (future) supplier_balances_fy_pdf.py — Supplier Balances (FY) PDF renderer
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_FONTS_REGISTERED: bool = False

_DIR = os.path.dirname(__file__)
_DEJAVU_REGULAR = os.path.join(_DIR, 'DejaVuSans.ttf')
_DEJAVU_BOLD    = os.path.join(_DIR, 'DejaVuSans-Bold.ttf')


def register_fonts() -> None:
    """Idempotently register DejaVuSans and DejaVuSans-Bold with reportlab.

    Safe to call multiple times — subsequent calls are no-ops (guarded by the
    module-level ``_FONTS_REGISTERED`` flag, which is set before the attempt so
    that even a failure does not trigger repeated warning logs on every call).

    On failure (missing TTF files or reportlab import error) logs a WARNING and
    returns; the PDF renderer will then fall back to Helvetica, which cannot
    encode ₹ or — and may raise a KeyError during doc.build().  This failure
    path should never occur on Lambda where the bundled TTF files are present.
    """
    global _FONTS_REGISTERED
    if _FONTS_REGISTERED:
        return
    # Mark as attempted before the try so repeated calls are always no-ops.
    _FONTS_REGISTERED = True
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        pdfmetrics.registerFont(TTFont('DejaVuSans',      _DEJAVU_REGULAR))
        pdfmetrics.registerFont(TTFont('DejaVuSans-Bold', _DEJAVU_BOLD))
        logger.info(
            'pdf_fonts: DejaVuSans and DejaVuSans-Bold registered from %s', _DIR
        )
    except Exception as exc:
        logger.warning(
            'pdf_fonts: failed to register DejaVuSans (%s) — '
            'falling back to Helvetica; ₹ (U+20B9) and — (U+2014) '
            'may cause KeyError in doc.build()',
            exc,
        )
