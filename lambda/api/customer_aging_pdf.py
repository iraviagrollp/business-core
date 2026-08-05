"""
customer_aging_pdf — PDF renderer for the Customer Aging report
(GET /reports/customer-aging/pdf).

Public surface
--------------
render_customer_aging_pdf(data: dict) -> bytes
    data    : {'rows': [...], 'as_of': 'YYYY-MM-DD', 'age1': int, 'age2': int,
               'age3': int} — built by handler._handle_customer_aging_pdf
               from aging.compute_aging() output + a customer_details city
               lookup attached per row.
    returns : raw PDF bytes (landscape A4)

Thin wrapper around the shared aging_pdf.render_aging_pdf() — see that
module's docstring for the full column/formatting/coloring spec. Customer
semantics: net <= 0 -> '{abs(net)} Cr' in GREEN (the customer has a credit/
advance); net > 0 -> '{net}' in RED (the customer owes us — a receivable).
"""

from __future__ import annotations

from reportlab.lib import colors

import aging_pdf

_RED = colors.HexColor('#cc0000')
_GREEN = colors.HexColor('#1a6e35')


def render_customer_aging_pdf(data: dict) -> bytes:
    return aging_pdf.render_aging_pdf(
        data,
        title='Customer Aging',
        party_label='Party',
        last_label='Receipt',
        negative_suffix='Cr',
        positive_color=_RED,
        negative_color=_GREEN,
    )
