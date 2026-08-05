"""
purchases_register_pdf — PDF renderer for the Purchases Register report
(GET /purchases/pdf).

Public surface
--------------
render_purchases_register_pdf(data: dict) -> bytes
    data    : payload built by handler._handle_purchases_pdf — already
              filtered (type / product_filter / exclude_internal / search
              applied in that exact order) and already in query order (never
              re-sorted). See transactions_register_pdf.py's docstring for
              the exact shape.
    returns : raw PDF bytes (landscape A4)

Thin wrapper around the shared transactions_register_pdf.render_register_pdf()
— sales and purchases registers are structurally identical, so all layout
code lives in that one shared module.
"""

from __future__ import annotations

import transactions_register_pdf


def render_purchases_register_pdf(data: dict) -> bytes:
    return transactions_register_pdf.render_register_pdf(data)
