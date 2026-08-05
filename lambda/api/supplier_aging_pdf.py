"""
supplier_aging_pdf — PDF renderer for the Supplier Aging report
(GET /reports/supplier-aging/pdf).

Public surface
--------------
render_supplier_aging_pdf(data: dict) -> bytes
    data    : {'rows': [...], 'as_of': 'YYYY-MM-DD', 'age1': int, 'age2': int,
               'age3': int} — built by handler._handle_supplier_aging_pdf
               from aging.compute_aging() output + a supplier_accounts city
               lookup attached per row.
    returns : raw PDF bytes (landscape A4)

Thin wrapper around the shared aging_pdf.render_aging_pdf() — see that
module's docstring for the full column/formatting/coloring spec. Supplier
semantics (per the task's authoritative port of
ui/src/pages/Suppliers/SupplierBalances.tsx): net <= 0 -> '{abs(net)} Dr' in
GREEN (we have overpaid the supplier — an amount recoverable from them);
net > 0 -> '{net}' in RED (an outstanding payable). Same colors as the
customer report, only the suffix word differs (Dr vs Cr).
"""

from __future__ import annotations

from reportlab.lib import colors

import aging_pdf

_RED = colors.HexColor('#cc0000')
_GREEN = colors.HexColor('#1a6e35')


def render_supplier_aging_pdf(data: dict) -> bytes:
    return aging_pdf.render_aging_pdf(
        data,
        title='Supplier Aging',
        party_label='Supplier',
        last_label='Payment',
        negative_suffix='Dr',
        positive_color=_RED,
        negative_color=_GREEN,
    )
