"""
ledger_statement — shared compute for the Customer Ledger Statement report.

Public surface
--------------
compute_ledger_statement(conn, account_name, from_date, to_date) -> dict
    Extracted verbatim (2026-07-20) from handler._handle_ledger_statement so it
    can be reused by the new /ledger/statement/pdf route (ledger_statement_pdf.py)
    without duplicating SQL. handler._handle_ledger_statement now delegates to
    this function; the Redis cache-aside + _response wrapping stay in the
    handler (mirrors customer_balances_fy.py / supplier_balances_fy.py).

Returns dict shape (unchanged from the pre-refactor inline handler, plus 'city'
added 2026-07-21 for the redesigned PDF's Location line):
    {
        'account_name': str, 'from_date': str, 'to_date': str,
        'opening_balance': float,
        'rows': [{'transaction_date': 'YYYY-MM-DD', 'voucher_no': str,
                   'transaction_type': str | None, 'debit': float, 'credit': float}, ...],
        'total_debit': float, 'total_credit': float, 'closing_balance': float,
        'city': str | None,
    }
"""

from __future__ import annotations


def compute_ledger_statement(conn, account_name: str, from_date: str, to_date: str) -> dict:
    with conn.cursor() as cur:
        # Opening balance: all transactions strictly before from_date
        cur.execute("""
            SELECT COALESCE(
                SUM(CASE WHEN category = 'Db' THEN amount ELSE -amount END), 0
            )
            FROM customer_ledger
            WHERE out_z IS NULL
              AND account_name = %(account_name)s
              AND transaction_date < %(from_date)s
        """, {'account_name': account_name, 'from_date': from_date})
        opening_balance = float(cur.fetchone()[0])

        # Period transactions grouped by voucher, determine primary sub_category
        cur.execute("""
            SELECT
                transaction_date,
                voucher_no,
                MAX(CASE WHEN sub_category NOT IN ('CGST', 'SGST', 'IGST', 'Roundoff')
                    THEN sub_category END) AS primary_type,
                COALESCE(SUM(amount) FILTER (WHERE category = 'Db'), 0) AS debit,
                COALESCE(SUM(amount) FILTER (WHERE category = 'Cr'), 0) AS credit
            FROM customer_ledger
            WHERE out_z IS NULL
              AND account_name = %(account_name)s
              AND transaction_date BETWEEN %(from_date)s AND %(to_date)s
            GROUP BY transaction_date, voucher_no
            ORDER BY transaction_date ASC, voucher_no ASC
        """, {'account_name': account_name, 'from_date': from_date, 'to_date': to_date})
        col_names = [d[0] for d in cur.description]
        raw_rows = cur.fetchall()

    total_debit = 0.0
    total_credit = 0.0
    rows = []
    for raw in raw_rows:
        row = dict(zip(col_names, raw))
        raw_debit = float(row['debit'])
        raw_credit = float(row['credit'])
        # Net the two sides so roundoff/GST sub-components are absorbed into the
        # voucher they belong to.  The voucher shows on only one side; the running
        # balance is numerically unchanged because net = raw_debit − raw_credit.
        net = raw_debit - raw_credit
        if net >= 0:
            debit, credit = net, 0.0
        else:
            debit, credit = 0.0, -net
        total_debit += debit
        total_credit += credit
        rows.append({
            'transaction_date': row['transaction_date'].isoformat(),
            'voucher_no': row['voucher_no'],
            'transaction_type': row['primary_type'],
            'debit': round(debit, 2),
            'credit': round(credit, 2),
        })

    closing_balance = round(opening_balance + total_debit - total_credit, 2)

    # Customer city, for the statement's Location line (LEFT JOIN-style lookup;
    # same case-insensitive match pattern as customer_balances_fy.py).
    with conn.cursor() as cur:
        cur.execute("""
            SELECT city
            FROM customer_details
            WHERE UPPER(customer_name) = UPPER(%(account_name)s)
              AND out_z IS NULL
            LIMIT 1
        """, {'account_name': account_name})
        city_row = cur.fetchone()
        city = city_row[0] if city_row else None

    return {
        'account_name': account_name,
        'from_date': from_date,
        'to_date': to_date,
        'opening_balance': round(opening_balance, 2),
        'rows': rows,
        'total_debit': round(total_debit, 2),
        'total_credit': round(total_credit, 2),
        'closing_balance': closing_balance,
        'city': city,
    }
