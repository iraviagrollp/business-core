"""
aging — shared FIFO aging engine for the Customer Aging / Supplier Aging PDF
reports (GET /reports/customer-aging/pdf, GET /reports/supplier-aging/pdf).

Ported faithfully (including the r2 rounding and day-count semantics) from
the browser-side aging algorithm already used by the dashboard's own
Customer Balances / Supplier Balances screens
(ui/src/pages/Customers/CustomerBalances.tsx and
ui/src/pages/Suppliers/SupplierBalances.tsx) so the server-rendered PDF
exports match the numbers already shown on those two screens exactly.

Public surface
--------------
compute_aging(conn, table, invoice_category, payment_subcats, age1, age2,
              age3, as_of) -> list[dict]
    conn              : open psycopg2 connection (caller owns open/close)
    table             : 'customer_ledger' | 'supplier_ledger'
    invoice_category  : the ledger 'category' value that represents an
                         unpaid invoice for this party type —
                         'Db' for customers (a sale we're owed for),
                         'Cr' for suppliers (a purchase we owe for)
    payment_subcats   : set[str] of 'sub_category' values that count toward
                         the Last Receipt/Payment Date/Amount columns —
                         {'Bank Receipt'} for customers,
                         {'Bank Payment', 'Cash Payment'} for suppliers
    age1/age2/age3    : bucket boundaries in days — 0..age1, age1+1..age2,
                         age2+1..age3 (older than age3 is excluded from every
                         bucket but still counted in `net`)
    as_of             : datetime.date — reference date for all day-count math

    Returns one dict per party with a non-zero net balance, sorted by `net`
    DESCENDING (matches both UI screens):
        {
            'party': str,
            'bucket1': float, 'bucket2': float, 'bucket3': float,
            'net': float,
            'last_receipt_date': 'YYYY-MM-DD' | None,
            'last_receipt_amount': float | None,
            'last_receipt_age': int | None,   # daysSince(last_receipt_date)
        }
    'city' is NOT populated here — the caller (handler.py's
    _handle_customer_aging_pdf / _handle_supplier_aging_pdf) attaches it from
    a separate customer_details / supplier_accounts lookup, mirroring the
    existing city-lookup pattern used throughout this codebase
    (customer_balances_fy.py, supplier_balances_fy.py, ledger_statement.py).

Algorithm (verbatim port)
--------------------------
    r2(n)          = round(n * 100) / 100
    daysSince(d)   = (as_of - d).days   — Python date subtraction is already
                     an integer day count, equivalent to the UI's floor()
    Source rows    : ALL rows in `table` with out_z IS NULL, EXCLUDING
                     accounts where 'iravi' appears in account_name
                     (case-insensitive) — full history, no date filter.
    Per account:
      invoices = [(transaction_date, amount) for rows where
                  category == invoice_category]
      payments = sum(amount for rows where category != invoice_category)
      last_receipt = the row with the MAX transaction_date among rows where
          category != invoice_category AND sub_category IN payment_subcats
      net = r2(sum(inv.amount for inv in invoices) - payments)
      FIFO: sort invoices by date ascending; apply `payments` against the
      oldest invoices first (a full or partial credit against each invoice,
      oldest-first, until the payment pool is exhausted); whatever remains
      outstanding on an invoice after that ages into bucket1/2/3 based on
      daysSince(invoice date); invoices older than age3 still reduce nothing
      (their outstanding balance is simply not added to any bucket, though
      it is still part of `net`).
      Accounts where net == 0 are dropped entirely.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date


def _r2(value: float) -> float:
    return round(value * 100) / 100.0


def compute_aging(
    conn,
    table: str,
    invoice_category: str,
    payment_subcats: set,
    age1: int,
    age2: int,
    age3: int,
    as_of: date,
) -> list[dict]:
    if table not in ('customer_ledger', 'supplier_ledger'):
        raise ValueError(f'unsupported aging table: {table!r}')

    # `table` is always one of the two hardcoded, whitelisted literals above
    # (validated on the line before) — never user-controlled — so the
    # f-string interpolation into the SQL text is not a SQL-injection risk.
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT transaction_date, account_name, category, sub_category, amount
            FROM {table}
            WHERE out_z IS NULL
              AND LOWER(account_name) NOT LIKE '%%iravi%%'
            ORDER BY account_name, transaction_date
        """)
        col_names = [d[0] for d in cur.description]
        raw_rows = cur.fetchall()

    by_party: dict = defaultdict(list)
    for raw in raw_rows:
        row = dict(zip(col_names, raw))
        by_party[row['account_name']].append(row)

    results = []
    for party, rows in by_party.items():
        invoices = [
            (r['transaction_date'], float(r['amount']))
            for r in rows if r['category'] == invoice_category
        ]
        payments = sum(
            float(r['amount']) for r in rows if r['category'] != invoice_category
        )

        last_receipt_date = None
        last_receipt_amount = None
        for r in rows:
            if r['category'] != invoice_category and r['sub_category'] in payment_subcats:
                if last_receipt_date is None or r['transaction_date'] > last_receipt_date:
                    last_receipt_date = r['transaction_date']
                    last_receipt_amount = float(r['amount'])

        net = _r2(sum(amt for _, amt in invoices) - payments)
        if net == 0:
            continue

        invoices_sorted = sorted(invoices, key=lambda pair: pair[0])
        remaining = payments
        b1 = b2 = b3 = 0.0
        for inv_date, amt in invoices_sorted:
            outstanding = amt
            if remaining > 0:
                applied = min(remaining, outstanding)
                outstanding -= applied
                remaining -= applied
            if outstanding <= 0:
                continue
            days = (as_of - inv_date).days
            if days <= age1:
                b1 += outstanding
            elif days <= age2:
                b2 += outstanding
            elif days <= age3:
                b3 += outstanding
            # older than age3: counts in `net`, in no bucket

        results.append({
            'party': party,
            'bucket1': _r2(b1),
            'bucket2': _r2(b2),
            'bucket3': _r2(b3),
            'net': net,
            'last_receipt_date': last_receipt_date.isoformat() if last_receipt_date else None,
            'last_receipt_amount': last_receipt_amount,
            'last_receipt_age': (as_of - last_receipt_date).days if last_receipt_date else None,
        })

    results.sort(key=lambda r: r['net'], reverse=True)
    return results
