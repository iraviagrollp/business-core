"""
borrowings — shared compute for the Borrowings ledger.

Public surface
--------------
compute_borrowings_rows(conn, account, from_date, to_date) -> list[dict]
    Extracted verbatim (2026-08-05) from handler._handle_borrowings_data's
    SQL/row-building logic so GET /borrowings (JSON) and GET /borrowings/pdf
    can never disagree — both call this same function.

    `account` is OPTIONAL (empty/blank = all accounts, via the same
    "%(account)s = '' OR account = %(account)s" pattern
    _handle_purchases_summary uses for its optional `branch` param).
    `from_date`/`to_date` are required by the CALLER (this function does not
    validate them — mirrors ledger_statement.compute_ledger_statement, which
    also leaves required-param validation to the handler).

Returns a list of dicts, one per borrowings row, ordered
transaction_date ASC, voucher_no ASC (byte-identical shape to the
GET /borrowings JSON response):
    [{'transaction_date': 'YYYY-MM-DD', 'voucher_no': str,
      'transaction_name': str, 'account': str, 'debit': float, 'credit': float}, ...]
"""

from __future__ import annotations


def compute_borrowings_rows(conn, account: str, from_date: str, to_date: str) -> list[dict]:
    account = (account or '').strip()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT transaction_date, voucher_no, transaction_name, account, debit, credit
            FROM borrowings
            WHERE out_z IS NULL
              AND transaction_date BETWEEN %(from_date)s AND %(to_date)s
              AND (%(account)s = '' OR account = %(account)s)
            ORDER BY transaction_date ASC, voucher_no ASC
        """, {'from_date': from_date, 'to_date': to_date, 'account': account})
        col_names = [d[0] for d in cur.description]
        raw_rows = cur.fetchall()

    rows = []
    for raw in raw_rows:
        row = dict(zip(col_names, raw))
        rows.append({
            'transaction_date': row['transaction_date'].isoformat(),
            'voucher_no': row['voucher_no'],
            'transaction_name': row['transaction_name'] or '',
            'account': row['account'],
            'debit': float(row['debit']),
            'credit': float(row['credit']),
        })
    return rows
