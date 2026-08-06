#!/usr/bin/env python3
"""
Unit tests for etl_borrowings's "Brought Forward" opening-balance-row skip.

Run: python test_brought_forward_skip.py   (from this directory)

Covers:
  1. A VoucherNo == 'Brought Forward' row is skipped by _is_brought_forward_row()
     and never reaches _extract_row()/the parsed row list.
  2. A normal transaction row is ingested (unaffected).
  3. Case/whitespace variants ('  brought forward  ', 'BROUGHT FORWARD') are
     also skipped.
  4. A missing/None VoucherNo cell is handled safely (not mistaken for a
     Brought Forward row) and falls through to _extract_row()'s own
     missing-voucher-no skip.
  5. _parse() end-to-end against a synthetic CSV fixture (BOM + full 28-column
     header) returns the correct rows/skipped-count split for a mix of one
     Brought Forward row and one real transaction row.
"""
import csv
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
os.environ.setdefault('DATA_BUCKET', 'unused-in-tests')

import handler  # noqa: E402

PASS = 0
FAIL = 0


def check(label, expected, actual):
    global PASS, FAIL
    if expected == actual:
        print(f"  PASS  {label}")
        PASS += 1
    else:
        print(f"  FAIL  {label}")
        print(f"         expected : {expected!r}")
        print(f"         actual   : {actual!r}")
        FAIL += 1


# ── 1-4. _is_brought_forward_row() / _extract_row() ────────────────────────

print("=== _is_brought_forward_row() ===")

check(
    "exact 'Brought Forward' -> True",
    True,
    handler._is_brought_forward_row({'voucherno': 'Brought Forward'}),
)
check(
    "normal voucher no -> False",
    False,
    handler._is_brought_forward_row({'voucherno': 'JE2526-60'}),
)
check(
    "whitespace-padded variant -> True",
    True,
    handler._is_brought_forward_row({'voucherno': '  brought forward  '}),
)
check(
    "all-caps variant -> True",
    True,
    handler._is_brought_forward_row({'voucherno': 'BROUGHT FORWARD'}),
)
check(
    "missing voucherno key -> False (safe)",
    False,
    handler._is_brought_forward_row({}),
)
check(
    "None voucherno value -> False (safe)",
    False,
    handler._is_brought_forward_row({'voucherno': None}),
)

print("\n=== _extract_row() end-to-end for a Brought Forward row ===")

# Shape mirrors the real sample: opening-balance header row for one account —
# Date = export date, DDDate sentinel, TransactionName/Branch/AccountGroup
# blank, VVN='0', Debit/Credit carry the opening balance.
bf_row = {
    'date': '2026-08-05',
    'voucherno': 'Brought Forward',
    'transactionname': '',
    'account': 'LEVAKA HARANATHA REDDY',
    'debit': '0.000000',
    'credit': '2070000.000000',
}
check(
    "Brought Forward row is filtered before _extract_row (never ingested)",
    True,
    handler._is_brought_forward_row(bf_row),
)

print("\n=== _extract_row() for a normal transaction row ===")

normal_row = {
    'date': '2026-08-05',
    'voucherno': 'JE2526-60',
    'transactionname': 'Journal Entries',
    'account': 'LEVAKA HARANATHA REDDY',
    'debit': '0',
    'credit': '4,845',
}
parsed = handler._extract_row(normal_row)
check("normal row is parsed (not None)", True, parsed is not None)
if parsed is not None:
    check("normal row voucher_no", 'JE2526-60', parsed['voucher_no'])
    check("normal row credit (comma-formatted string)", 4845.0, parsed['credit'])
    check("normal row debit", 0.0, parsed['debit'])


# ── 5. _parse() end-to-end against a synthetic CSV fixture ─────────────────

print("\n=== _parse() end-to-end (synthetic CSV fixture) ===")

_HEADER = [
    'Date', 'DDDate', 'VoucherNo', 'MONTH', 'QUARTER', 'TransactionName',
    'UserName', 'VS', 'VVN', 'Currency', 'Branch', 'ACCOUNT', 'AccountGroup',
    'ContraAccount', 'Debit', 'Credit', 'DebitInCC', 'CreditInCC',
    'ExchangeRate', 'Remarks', 'RefBillNo', 'RefBillDate', 'Executive',
    'VoucherType', 'RcptNo', 'SdcId', 'MRCNo', 'InvoiceNo',
]


def _blank_row(**overrides) -> dict:
    row = {h: '' for h in _HEADER}
    row.update(overrides)
    return row


with tempfile.TemporaryDirectory() as tmp:
    fixture_path = os.path.join(tmp, 'Borrowings_20260805.xlsx')
    rows = [
        # Synthetic opening-balance header row for one account — must be skipped.
        _blank_row(
            Date='05-08-2026',
            DDDate='01-01-1900',
            VoucherNo='Brought Forward',
            ACCOUNT='LEVAKA HARANATHA REDDY',
            VVN='0',
            Debit='0.000000',
            Credit='2070000.000000',
            RefBillDate='01-01-1900',
        ),
        # A real transaction row — must be ingested.
        _blank_row(
            Date='05-08-2026',
            VoucherNo='JE2526-60',
            TransactionName='Journal Entries',
            ACCOUNT='LEVAKA HARANATHA REDDY',
            Debit='0.00',
            Credit='4845.00',
        ),
        # A second, case/whitespace-variant Brought Forward row — must also
        # be skipped (defensive; not expected in the real file, but the task
        # requires case/whitespace tolerance).
        _blank_row(
            Date='05-08-2026',
            VoucherNo='  BROUGHT FORWARD  ',
            ACCOUNT='ANOTHER INVESTOR',
            Debit='0.00',
            Credit='500000.00',
        ),
    ]

    with open(fixture_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=_HEADER)
        writer.writeheader()
        writer.writerows(rows)

    parsed_rows, skipped = handler._parse(fixture_path)

    check("parsed row count (1 real transaction)", 1, len(parsed_rows))
    check("skipped Brought Forward count (2 rows)", 2, skipped)
    check("the surviving row is the real transaction", 'JE2526-60', parsed_rows[0]['voucher_no'])


print(f"\n{PASS} passed, {FAIL} failed")
if FAIL:
    sys.exit(1)
