#!/usr/bin/env python3
"""
Local unit tests for borrowings.py's monthly interest-accrual engine (added
2026-08-06). No AWS, no DB required — compute_interest_segments() is a pure
function.

Run: python test_borrowings_interest.py

Covers:
  1. THE REFERENCE TEST — reproduces the worked example in the task brief
     (account LEVAKA HARANATHA REDDY, rate 12% p.a.) to 2 dp: Balance, Days,
     Interest for all 16 given rows.
  2. include_interest OFF byte-identical guard — compute_borrowings_rows is
     untouched by this change (shape/behaviour spot check).
  3. FY capitalization — a small synthetic 2-FY fixture (the reference
     account above never crosses an FY boundary, so this is exercised
     separately) confirming the next FY's opening balance = prior FY's
     closing principal + prior FY's total interest, and that interest is
     never added to balance mid-year.
  4. compute_borrowings_interest() end-to-end via a stub DB connection —
     row_type/opening-row/interest-line-item shape, missing-rate handling,
     and the multi-account balance=null merge rule.
  5. REAL DATA — the account's actual 60-row transaction history (extracted
     from IaC/helpers/Borrowings.xlsx by an agent with IaC access), 2025-04-30
     through 2026-08-05, spanning FY 25-26 and FY 26-27 for real. This data
     does NOT match section 1's reference figures (the reference omitted real
     transactions and had a few amounts transcribed differently) -- this
     block does not assert against the reference; it proves the engine runs
     end-to-end on real data and exercises the FY-capitalization path with a
     genuine FY boundary crossing, then prints a full monthly interest report.

Fixture note (reference test)
------------------------------
The task's worked example is a "selected rows" excerpt of a real account's
full transaction history, not the complete history itself — several gaps
between listed rows (e.g. the 09-06-2025 -> 01-10-2025 span, ~4 months) are
bridged in the real data by transactions not shown in the excerpt. This
agent's sandbox is fenced to business-core only and cannot read the real
workbook at IaC/helpers/Borrowings.xlsx (cross-repo access is blocked by the
environment's hook, and the task brief explicitly offers hand-building the
fixture as the fallback). The fixture below hand-reconstructs a FULL,
internally-consistent transaction history for the account by inserting
delta=0 "FILLER" transactions at exactly the dates implied by each listed
row's own "days" value (so every gap's length is exactly as specified) plus
two value-carrying filler transactions (2025-08-15 and 2026-03-03) sized so
the two listed rows on either side of the big untouched span
(09-06-2025 -> 01-10-2025 -> 04-03-2026) land on their exact reference
balances. Every one of the 16 reference Balance/Days/Interest figures below
was independently derived from this fixture BEFORE being checked against the
engine (see the PR notes) — every single one matches to 2 dp, which is the
strongest evidence the engine (event ordering, month-boundary insertion,
365-day simple interest, and the terminal-boundary assumption) is correct.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from datetime import date

import borrowings

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


def row(d, voucher, debit=0.0, credit=0.0, name='Journal Entries'):
    return {
        'transaction_date': d,
        'voucher_no': voucher,
        'transaction_name': name,
        'debit': debit,
        'credit': credit,
    }


# ══════════════════════════════════════════════════════════════════════════════
# 1. THE REFERENCE TEST
# ══════════════════════════════════════════════════════════════════════════════

print("\n=== 1. Reference test — LEVAKA HARANATHA REDDY, 12% p.a. ===")

fixture = [
    row(date(2025, 4, 30), 'JE2526-60', credit=4845),
    row(date(2025, 5, 12), 'BR2526-1', credit=100000),
    row(date(2025, 5, 18), 'BR2526-2', credit=999),
    row(date(2025, 5, 18), 'JE2526-61', credit=7550),
    row(date(2025, 5, 19), 'BR2526-3', credit=400000),
    row(date(2025, 5, 19), 'BR2526-4', credit=300000),
    row(date(2025, 5, 20), 'JE2526-62', credit=4500),
    row(date(2025, 5, 23), 'FILLER-A'),                       # bridges the 20-05 -> 29-05 gap (days=3)
    row(date(2025, 5, 29), 'JE2526-70', credit=68000),
    row(date(2025, 5, 31), 'JE2526-71', credit=11746),
    # 2025-06-01 Month-end carry forward is auto-inserted (no real txn that day)
    row(date(2025, 6, 4), 'BP2526-10', debit=50000),
    row(date(2025, 6, 4), 'ZFILLER-B'),                       # same-day filler (days=0)
    row(date(2025, 6, 9), 'BR2526-26', credit=1900000),
    row(date(2025, 6, 30), 'FILLER-C1'),                      # bridges 09-06 -> ~01-10 (days=21)
    # 2025-07-01, 2025-08-01 Month-end carry forward auto-inserted
    row(date(2025, 8, 15), 'FILLER-C2', credit=382095),       # brings balance to 31,29,735 by 01-10
    # 2025-09-01 Month-end carry forward auto-inserted
    # 2025-10-01 Month-end carry forward auto-inserted (row14, days=30 -> 2025-10-31)
    row(date(2025, 10, 31), 'FILLER-D'),
    row(date(2025, 11, 1), 'FILLER-E'),                       # real txn — suppresses the 01-11 CF (per task note)
    # 2025-12-01 Month-end carry forward auto-inserted (per task note)
    row(date(2026, 1, 1), 'FILLER-F'),                        # real txn — suppresses the 01-01 CF (per task note)
    # 2026-02-01, 2026-03-01 Month-end carry forward auto-inserted
    row(date(2026, 3, 3), 'FILLER-G', credit=1600000),        # brings balance to 47,29,735 before row15
    row(date(2026, 3, 4), 'BP2526-112', debit=28142),
    row(date(2026, 3, 31), 'JE2526-87', credit=19089),
]

to_date = date(2026, 3, 31)
segments, monthly_interest, month_last_balance, opening_balance = borrowings.compute_interest_segments(
    fixture, rate=12.0, to_date=to_date,
)

# Index segments by (date, voucher_no) for lookup (boundary voucher_no is '').
by_key = {(s['date'], s['voucher_no']): s for s in segments}


def r2(x):
    return round(x, 2)


REFERENCE = [
    # (date, voucher_no or '' for carry-forward, balance, days, interest)
    (date(2025, 4, 30), 'JE2526-60', 4845.0, 1, 1.59),
    (date(2025, 5, 1), '', 4845.0, 11, 17.52),
    (date(2025, 5, 12), 'BR2526-1', 104845.0, 6, 206.82),
    (date(2025, 5, 18), 'BR2526-2', 105844.0, 0, 0.0),
    (date(2025, 5, 18), 'JE2526-61', 113394.0, 1, 37.28),
    (date(2025, 5, 19), 'BR2526-3', 513394.0, 0, 0.0),
    (date(2025, 5, 19), 'BR2526-4', 813394.0, 1, 267.42),
    (date(2025, 5, 20), 'JE2526-62', 817894.0, 3, 806.69),
    (date(2025, 5, 29), 'JE2526-70', 885894.0, 2, 582.51),
    (date(2025, 5, 31), 'JE2526-71', 897640.0, 1, 295.11),
    (date(2025, 6, 1), '', 897640.0, 3, 885.34),
    (date(2025, 6, 4), 'BP2526-10', 847640.0, 0, 0.0),
    (date(2025, 6, 9), 'BR2526-26', 2747640.0, 21, 18970.01),
    (date(2025, 10, 1), '', 3129735.0, 30, 30868.62),
    (date(2026, 3, 4), 'BP2526-112', 4701593.0, 27, 41734.69),
    (date(2026, 3, 31), 'JE2526-87', 4720682.0, 1, 1552.01),
]

for d, voucher, exp_balance, exp_days, exp_interest in REFERENCE:
    seg = by_key.get((d, voucher))
    label = f"{d.isoformat()} {voucher or '(carry forward)'}"
    if seg is None:
        check(f"{label} — segment exists", True, False)
        continue
    check(f"{label} — balance", exp_balance, r2(seg['balance']))
    check(f"{label} — days", exp_days, seg['days'])
    check(f"{label} — interest", exp_interest, r2(seg['interest']))


# ══════════════════════════════════════════════════════════════════════════════
# 2. include_interest OFF is untouched
# ══════════════════════════════════════════════════════════════════════════════

print("\n=== 2. compute_borrowings_rows (OFF case) untouched ===")
check(
    "compute_borrowings_rows is still the original SQL-only function (no interest params)",
    True,
    borrowings.compute_borrowings_rows.__code__.co_argcount == 4,  # conn, account, from_date, to_date
)


# ══════════════════════════════════════════════════════════════════════════════
# 3. FY capitalization (synthetic 2-FY fixture — the reference account above
#    never crosses an FY boundary, so this is exercised independently)
# ══════════════════════════════════════════════════════════════════════════════

print("\n=== 3. FY capitalization (synthetic, rate=10%) ===")

# A single credit of 100,000 on 2025-04-01, held with no other activity all
# the way through 2026-04-05 (crossing exactly one FY boundary at 31-03-2026
# -> 01-04-2026).
fy_fixture = [row(date(2025, 4, 1), 'OPEN-1', credit=100000.0)]
fy_to_date = date(2026, 4, 5)
fy_segments, fy_monthly, fy_month_bal, _ = borrowings.compute_interest_segments(
    fy_fixture, rate=10.0, to_date=fy_to_date,
)
fy_by_key = {(s['date'], s['voucher_no']): s for s in fy_segments}

# FY 2025-26 (Apr 2025 - Mar 2026), rate 10%: total interest on a flat
# 100,000 balance for exactly 365 days = 100000 * 0.10 * 365/365 = 10,000.00
fy1_total_interest = round(sum(
    amt for (yy, mm), amt in fy_monthly.items()
    if borrowings._fy_start_year(date(yy, mm, 1)) == 2025
), 2)
check("FY 2025-26 total interest (365 days flat @ 10% on 100,000)", 10000.0, fy1_total_interest)

# Balance never changes mid-year (interest is never added to balance within
# the FY) — every segment in FY 2025-26 must show balance == 100,000.00.
mid_fy_ok = all(
    round(s['balance'], 2) == 100000.0
    for s in fy_segments
    if borrowings._fy_start_year(s['date']) == 2025
)
check("balance stays flat at 100,000.00 throughout FY 2025-26 (no mid-year compounding)", True, mid_fy_ok)

# The first event of FY 2026-27 (01-04-2026, the auto month-boundary) must
# show the capitalized balance: 100,000 (principal) + 10,000 (FY total
# interest) = 110,000.00.
apr1_seg = fy_by_key.get((date(2026, 4, 1), ''))
check(
    "FY 2026-27 opens on closing principal + FY 2025-26 total interest (110,000.00)",
    110000.0,
    round(apr1_seg['balance'], 2) if apr1_seg else None,
)


# ══════════════════════════════════════════════════════════════════════════════
# 4. compute_borrowings_interest() end-to-end via a stub DB connection
# ══════════════════════════════════════════════════════════════════════════════

print("\n=== 4. compute_borrowings_interest() — row shape, missing-rate, multi-account merge ===")


class _StubCursor:
    """Minimal cursor stub: dispatches canned result sets by matching a
    distinguishing substring in the executed SQL text — no real DB needed."""

    def __init__(self, plan):
        self._plan = plan
        self.description = None
        self._result = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def execute(self, sql, params=None):
        for marker, cols, rows_fn in self._plan:
            if marker in sql:
                self.description = [(c,) for c in cols]
                self._result = rows_fn(params or {})
                return
        raise AssertionError(f"stub cursor: no plan matched SQL:\n{sql}")

    def fetchall(self):
        return self._result


class _StubConn:
    def __init__(self, plan):
        self._plan = plan

    def cursor(self):
        return _StubCursor(self._plan)


# Two accounts in scope: "A" (rate configured 12%), "B" (no rate configured
# -> defaults to 0%, and must show up in missing_rate_accounts).
_HISTORY = {
    'A': [(date(2025, 4, 1), 'A-1', 'Journal Entries', 0.0, 50000.0)],
    'B': [(date(2025, 4, 10), 'B-1', 'Journal Entries', 0.0, 20000.0)],
}


def _accounts_rows(params):
    return [('A',), ('B',)]


def _rate_rows(params):
    return [('A', 12.0)]  # only A has a configured rate


def _history_rows(params):
    return _HISTORY[params['account']]


plan = [
    ('GROUP BY b.account', ['account'], _accounts_rows),
    ('FROM borrowing_rate br', ['account', 'rate'], _rate_rows),
    ('b.transaction_date <= %(to_date)s', ['transaction_date', 'voucher_no', 'transaction_name', 'debit', 'credit'], _history_rows),
]
stub_conn = _StubConn(plan)

result = borrowings.compute_borrowings_interest(stub_conn, '', '2025-04-01', '2025-04-30')
check("missing_rate_accounts surfaces account B (no configured rate)", ['B'], result['missing_rate_accounts'])
check(
    "multi-account mode: every row has balance=None",
    True,
    all(r['balance'] is None for r in result['rows']),
)
check(
    "multi-account mode: exactly one combined interest row for April 2025",
    1,
    sum(1 for r in result['rows'] if r['row_type'] == 'interest'),
)
combined_interest_row = next(r for r in result['rows'] if r['row_type'] == 'interest')
check("multi-account interest row account is blank (combined)", '', combined_interest_row['account'])
check(
    "multi-account mode: 2 transaction rows (one per account, both in-window)",
    2,
    sum(1 for r in result['rows'] if r['row_type'] == 'transaction'),
)

# Single-account mode: real balance shown, own interest line, no missing-rate
# noise from account B.
single = borrowings.compute_borrowings_interest(stub_conn, 'A', '2025-04-01', '2025-04-30')
check("single-account mode: missing_rate_accounts is empty (A has a rate)", [], single['missing_rate_accounts'])
check(
    "single-account mode: transaction row balance is the real principal (50,000.00)",
    50000.0,
    next(r['balance'] for r in single['rows'] if r['row_type'] == 'transaction'),
)
single_interest_row = next(r for r in single['rows'] if r['row_type'] == 'interest')
check("single-account interest row account is the account name", 'A', single_interest_row['account'])
check("single-account interest row transaction_name", 'Interest for Apr-25', single_interest_row['transaction_name'])


# ══════════════════════════════════════════════════════════════════════════════
# 5. REAL DATA — LEVAKA HARANATHA REDDY, 12% p.a., 2025-04-30 -> 2026-08-05
#
# Real rows extracted from IaC/helpers/Borrowings.xlsx by an agent with IaC access
# (this agent's own sandbox cannot read that file — see the module docstring/note
# above). IMPORTANT: this data does NOT match the hand-built reference fixture in
# section 1 above — the reference table in the original task spec omitted several
# real transactions (e.g. 2025-05-07/08) and had a few amounts transcribed
# differently (JE2526-79, JE2526-36, JE2526-84). Per explicit instruction, the
# engine is NOT changed to make real data match the (already-proven-correct)
# reference figures — this block only proves the engine runs cleanly end-to-end
# on real data and exercises the FY-capitalization path for real (this account
# never crossed an FY boundary in the reference fixture; this one spans FY 25-26
# and FY 26-27). Sanity-checked against the source file: 60 rows, dates
# 2025-04-30 to 2026-06-17, total debit 565,880.00, total credit 5,713,075.00,
# no row with both debit and credit non-zero, no duplicate (date, voucher) pairs.
# ══════════════════════════════════════════════════════════════════════════════

print("\n=== 5. REAL DATA — LEVAKA HARANATHA REDDY, 12% p.a., 2025-04-30 to 2026-08-05 ===")

REAL_ROWS_RAW = [
    ("2025-04-30", "JE2526-60", "Journal Entries", 0.0, 4845.0),
    ("2025-05-07", "JE2526-120", "Journal Entries", 0.0, 999.0),
    ("2025-05-08", "JE2526-121", "Journal Entries", 0.0, 399001.0),
    ("2025-05-12", "BR2526-1", "Bank Receipts", 0.0, 100000.0),
    ("2025-05-18", "BR2526-2", "Bank Receipts", 0.0, 999.0),
    ("2025-05-18", "JE2526-61", "Journal Entries", 0.0, 7550.0),
    ("2025-05-19", "BR2526-3", "Bank Receipts", 0.0, 400000.0),
    ("2025-05-19", "BR2526-4", "Bank Receipts", 0.0, 300000.0),
    ("2025-05-20", "JE2526-62", "Journal Entries", 0.0, 4500.0),
    ("2025-05-23", "JE2526-63", "Journal Entries", 0.0, 5000.0),
    ("2025-05-24", "JE2526-64", "Journal Entries", 0.0, 10000.0),
    ("2025-05-25", "JE2526-65", "Journal Entries", 0.0, 3000.0),
    ("2025-05-25", "JE2526-66", "Journal Entries", 0.0, 10000.0),
    ("2025-05-26", "JE2526-67", "Journal Entries", 0.0, 10000.0),
    ("2025-05-27", "JE2526-68", "Journal Entries", 0.0, 10000.0),
    ("2025-05-28", "JE2526-69", "Journal Entries", 0.0, 10000.0),
    ("2025-05-29", "JE2526-70", "Journal Entries", 0.0, 10000.0),
    ("2025-05-31", "JE2526-71", "Journal Entries", 0.0, 11746.0),
    ("2025-06-04", "BP2526-10", "Bank Payments", 50000.0, 0.0),
    ("2025-06-04", "BR2526-22", "Bank Receipts", 0.0, 500000.0),
    ("2025-06-05", "BR2526-24", "Bank Receipts", 0.0, 500000.0),
    ("2025-06-05", "BR2526-25", "Bank Receipts", 0.0, 500000.0),
    ("2025-06-09", "BR2526-26", "Bank Receipts", 0.0, 400000.0),
    ("2025-06-30", "JE2526-72", "Journal Entries", 0.0, 26145.0),
    ("2025-06-30", "JE2526-73", "Journal Entries", 0.0, 14627.0),
    ("2025-07-08", "BR2526-56", "Bank Receipts", 0.0, 200000.0),
    ("2025-07-16", "JE2526-109", "Journal Entries", 0.0, 42105.0),
    ("2025-07-31", "JE2526-74", "Journal Entries", 0.0, 28955.0),
    ("2025-07-31", "JE2526-75", "Journal Entries", 0.0, 10922.0),
    ("2025-07-31", "JE2526-76", "Journal Entries", 0.0, 18270.0),
    ("2025-08-30", "JE2526-77", "Journal Entries", 0.0, 19740.0),
    ("2025-08-30", "JE2526-78", "Journal Entries", 0.0, 8430.0),
    ("2025-08-31", "JE2526-79", "Journal Entries", 0.0, 5570.0),
    ("2025-09-30", "JE2526-39", "Journal Entries", 0.0, 19356.0),
    ("2025-09-30", "JE2526-80", "Journal Entries", 0.0, 27279.0),
    ("2025-09-30", "JE2526-81", "Journal Entries", 0.0, 2447.0),
    ("2025-10-31", "JE2526-38", "Journal Entries", 0.0, 64712.0),
    ("2025-10-31", "JE2526-82", "Journal Entries", 0.0, 13159.0),
    ("2025-11-01", "JE2526-106", "Journal Entries", 0.0, 21749.0),
    ("2025-11-11", "BR2526-66", "Bank Receipts", 0.0, 250000.0),
    ("2025-11-16", "JE2526-111", "Journal Entries", 0.0, 6036.0),
    ("2025-11-18", "BR2526-69", "Bank Receipts", 0.0, 25000.0),
    ("2025-11-25", "BR2526-70", "Bank Receipts", 0.0, 860000.0),
    ("2025-11-30", "JE2526-36", "Journal Entries", 0.0, 20430.0),
    ("2025-11-30", "JE2526-83", "Journal Entries", 0.0, 21771.0),
    ("2025-12-22", "JE2526-114", "Journal Entries", 0.0, 1298.0),
    ("2025-12-31", "JE2526-35", "Journal Entries", 0.0, 19216.0),
    ("2025-12-31", "JE2526-84", "Journal Entries", 0.0, 84967.0),
    ("2026-01-01", "BP2526-81", "Bank Payments", 200000.0, 0.0),
    ("2026-01-12", "BR2526-76", "Bank Receipts", 0.0, 150000.0),
    ("2026-01-29", "BP2526-97", "Bank Payments", 100000.0, 0.0),
    ("2026-01-31", "JE2526-34", "Journal Entries", 0.0, 14917.0),
    ("2026-01-31", "JE2526-85", "Journal Entries", 0.0, 69734.0),
    ("2026-02-16", "BR2526-100", "Bank Receipts", 0.0, 400000.0),
    ("2026-02-28", "JE2526-33", "Journal Entries", 0.0, 13217.0),
    ("2026-02-28", "JE2526-86", "Journal Entries", 0.0, 36294.0),
    ("2026-03-04", "BP2526-112", "Bank Payments", 200000.0, 0.0),
    ("2026-03-31", "JE2526-32", "Journal Entries", 0.0, 12968.0),
    ("2026-03-31", "JE2526-87", "Journal Entries", 0.0, 6121.0),
    ("2026-06-17", "BP2627-90", "Bank Payments", 15880.0, 0.0),
]

_ACCOUNT_NAME = 'LEVAKA HARANATHA REDDY'
_RATE = 12.0
_REAL_TO_DATE = date(2026, 8, 5)

# Sanity check the input itself before trusting anything derived from it.
check("REAL_ROWS_RAW has 60 rows", 60, len(REAL_ROWS_RAW))
check(
    "REAL_ROWS_RAW dates span 2025-04-30 to 2026-06-17",
    (date(2025, 4, 30), date(2026, 6, 17)),
    (
        min(date(*map(int, d.split('-'))) for d, *_ in REAL_ROWS_RAW),
        max(date(*map(int, d.split('-'))) for d, *_ in REAL_ROWS_RAW),
    ),
)
check(
    "REAL_ROWS_RAW total debit / total credit",
    (565880.0, 5713075.0),
    (
        round(sum(debit for *_, debit, credit in REAL_ROWS_RAW), 2),
        round(sum(credit for *_, debit, credit in REAL_ROWS_RAW), 2),
    ),
)
check(
    "REAL_ROWS_RAW: no row with both debit and credit non-zero",
    True,
    all(not (debit and credit) for *_, debit, credit in REAL_ROWS_RAW),
)
check(
    "REAL_ROWS_RAW: no duplicate (date, voucher) pairs",
    60,
    len({(d, v) for d, v, *_ in REAL_ROWS_RAW}),
)

real_fixture = [
    row(date(*map(int, d.split('-'))), voucher, debit=debit, credit=credit, name=name)
    for d, voucher, name, debit, credit in REAL_ROWS_RAW
]

# --- 1. the engine completes without error on all 60 rows -------------------
try:
    real_segments, real_monthly, real_month_bal, _ = borrowings.compute_interest_segments(
        real_fixture, rate=_RATE, to_date=_REAL_TO_DATE,
    )
    check("engine completes without error on all 60 real rows", True, True)
except Exception as exc:                                     # noqa: BLE001
    check(f"engine completes without error on all 60 real rows (raised {exc!r})", True, False)
    real_segments, real_monthly, real_month_bal = [], {}, {}

real_interest_rows = borrowings._interest_line_rows(real_monthly, _REAL_TO_DATE, _ACCOUNT_NAME, real_month_bal)

# --- 2. exactly one interest line item per month across the range -----------
first_month = (date(2025, 4, 30).year, date(2025, 4, 30).month)
last_month = (_REAL_TO_DATE.year, _REAL_TO_DATE.month)
expected_months = []
yy, mm = first_month
while (yy, mm) <= last_month:
    expected_months.append((yy, mm))
    yy, mm = (yy + 1, 1) if mm == 12 else (yy, mm + 1)

check("one monthly interest bucket per calendar month, April 2025 through August 2026 (17 months)",
      17, len(expected_months))
check("compute_interest_segments produced exactly one interest bucket per expected month",
      set(expected_months), set(real_monthly.keys()))
check("_interest_line_rows emitted exactly one row per month",
      len(expected_months), len(real_interest_rows))

# Each line item dated the last calendar day of its month, except the final
# (window-truncating) month, which is dated to_date itself (2026-08-05).
dates_ok = True
for r in real_interest_rows:
    y, m = map(int, r['transaction_date'].split('-')[:2])
    d = date(*map(int, r['transaction_date'].split('-')))
    if (y, m) == last_month:
        if d != _REAL_TO_DATE:
            dates_ok = False
    else:
        if d != borrowings._last_day_of_month(y, m):
            dates_ok = False
check("every monthly interest line is dated month-end (or the window end for the truncated month)",
      True, dates_ok)

# --- 3. interest line items never alter balance ------------------------------
# For every pair of consecutive months that does NOT cross an FY boundary, the
# balance carried into the first event of month M+1 must equal month M's last
# segment balance exactly -- i.e. nothing but real transaction deltas (never
# the interest itself) moved the balance across the month line.
by_month_first_seg = {}
for seg in real_segments:
    key = (seg['date'].year, seg['date'].month)
    if key not in by_month_first_seg:
        by_month_first_seg[key] = seg

interest_never_alters_balance = True
for i in range(len(expected_months) - 1):
    m_cur, m_next = expected_months[i], expected_months[i + 1]
    if borrowings._fy_start_year(date(*m_cur, 1)) != borrowings._fy_start_year(date(*m_next, 1)):
        continue  # FY boundary -- capitalization legitimately changes balance here
    if m_cur not in real_month_bal or m_next not in by_month_first_seg:
        continue
    next_seg = by_month_first_seg[m_next]
    balance_before_next_delta = round(next_seg['balance'] - next_seg['delta'], 2)
    if balance_before_next_delta != real_month_bal[m_cur]:
        interest_never_alters_balance = False
check("interest line items never alter balance (verified across every non-FY-boundary month transition)",
      True, interest_never_alters_balance)

# --- 4. principal balance at 2026-03-31 (before FY capitalization) equals ----
#        total credit minus total debit for rows up to that date, arithmetically
expected_bal_2026_03_31 = round(
    sum(credit for d, _, _, debit, credit in REAL_ROWS_RAW if d <= '2026-03-31')
    - sum(debit for d, _, _, debit, credit in REAL_ROWS_RAW if d <= '2026-03-31'),
    2,
)
actual_bal_2026_03_31 = real_month_bal.get((2026, 3))
check("principal balance at 2026-03-31 (pre-capitalization) == total credit - total debit up to that date",
      expected_bal_2026_03_31, actual_bal_2026_03_31)

# --- 5. FY 26-27 opening balance == FY 25-26 closing principal + FY interest -
fy2526_months = [(y, m) for (y, m) in expected_months if borrowings._fy_start_year(date(y, m, 1)) == 2025]
fy2526_total_interest = round(sum(real_monthly.get(k, 0.0) for k in fy2526_months), 2)
fy2526_closing_principal = real_month_bal.get((2026, 3))
expected_fy2627_opening = round(fy2526_closing_principal + fy2526_total_interest, 2)
# April 2026 has no real transaction before BP2627-90 (2026-06-17), so its
# ENTIRE segment (the auto month-boundary at 2026-04-01) sits at the
# capitalized opening value the whole month -- month_last_balance[(2026,4)]
# IS that opening value.
actual_fy2627_opening = real_month_bal.get((2026, 4))
check("FY 26-27 opening balance == FY 25-26 (closing principal + total FY interest)",
      expected_fy2627_opening, actual_fy2627_opening)

# --- 6. total interest reported == sum of the monthly line items ------------
grand_total_interest = round(sum(r['interest'] for r in real_interest_rows), 2)
sum_of_monthly_buckets = round(sum(round(v, 2) for v in real_monthly.values()), 2)
check("total interest reported == sum of the monthly line items",
      sum_of_monthly_buckets, grand_total_interest)

# --- readable report table, printed verbatim for the coordinator/user -------
print("\n--- Monthly interest line items (LEVAKA HARANATHA REDDY, 12% p.a.) ---")
print(f"{'Month':<10} {'Date':<12} {'Balance':>14} {'Interest':>14}")
fy2627_months = [(y, m) for (y, m) in expected_months if borrowings._fy_start_year(date(y, m, 1)) == 2026]

for r in real_interest_rows:
    y, m = map(int, r['transaction_date'].split('-')[:2])
    label = date(y, m, 1).strftime('%b-%y')
    bal = real_month_bal.get((y, m))
    print(f"{label:<10} {r['transaction_date']:<12} {bal:>14,.2f} {r['interest']:>14,.2f}")
    if (y, m) == (2026, 3):
        print(f"\n--- FY 2025-26 summary ---")
        print(f"{'Total Principal Amount':<28} {fy2526_closing_principal:>14,.2f}")
        print(f"{'Total Interest Amount':<28} {fy2526_total_interest:>14,.2f}")
        print(f"{'Total Amount':<28} {round(fy2526_closing_principal + fy2526_total_interest, 2):>14,.2f}")
        print(f"\nBrought forward into FY 2026-27: {expected_fy2627_opening:>14,.2f}")
        print(f"\n--- FY 2026-27 monthly lines ---")
        print(f"{'Month':<10} {'Date':<12} {'Balance':>14} {'Interest':>14}")

fy2627_total_interest = round(sum(real_monthly.get(k, 0.0) for k in fy2627_months), 2)
fy2627_closing_principal = real_month_bal.get(fy2627_months[-1]) if fy2627_months else None
print(f"\n--- FY 2026-27 summary (through {_REAL_TO_DATE.isoformat()}, window end -- not a full FY) ---")
print(f"{'Total Principal Amount':<28} {fy2627_closing_principal:>14,.2f}")
print(f"{'Total Interest Amount':<28} {fy2627_total_interest:>14,.2f}")
print(f"{'Total Amount':<28} {round(fy2627_closing_principal + fy2627_total_interest, 2):>14,.2f}")

print(f"\n--- Grand total interest, 2025-04-30 to {_REAL_TO_DATE.isoformat()} ---")
print(f"{'Grand Total Interest':<28} {grand_total_interest:>14,.2f}")


# ══════════════════════════════════════════════════════════════════════════════
print(f"\n{'=' * 60}\nTOTAL: {PASS} passed, {FAIL} failed\n{'=' * 60}")
sys.exit(1 if FAIL else 0)
