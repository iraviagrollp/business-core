#!/usr/bin/env python3
"""
Local unit tests for borrowings.py's monthly interest-accrual engine (added
2026-08-06; reworked the same day to remove FY-boundary interest
capitalization entirely — see the "interest is never carried forward" model
change: only PRINCIPAL is brought forward into the next FY; each FY's own
interest is tracked/displayed separately and accumulates as a payable,
listed FY-by-FY, via `cumulative_interest`/`total`). No AWS, no DB required —
compute_interest_segments() is a pure function.

Run: python test_borrowings_interest.py

2026-08-12 model change — INTEREST STARTS THE DAY AFTER AN ENTRY
----------------------------------------------------------------
The engine used to charge interest on the entry date itself: an amount
booked on the 8th accrued for the 8th. It now accrues from the 9th. Three
engine changes implement it (see borrowings.compute_interest_segments):
the terminal event moved from `to_date + 1` to `to_date`; carry-forward
events moved from each month's 1st to its LAST day; and a segment's
interest is bucketed into the month its accrual DAYS fall in (event date
+ 1) while `month_last_balance` stays keyed on the event's own calendar
month (closing principal is a month-end fact, not an accrual fact).

Every day-count/interest figure below was restated for this rule. The
strongest check is `brute_force_monthly_interest` — an independent
day-by-day oracle sharing no machinery with the engine — which is asserted
month-for-month against the engine on the reference fixture, on the real
60-row history, and (in section 6) on the FY totals.

Covers:
  1. THE REFERENCE FIXTURE (account LEVAKA HARANATHA REDDY, 12% p.a.):
     running balances (unchanged by the 2026-08-12 rule — only the days
     each balance is charged for moved), hand-derived day counts under the
     new rule incl. the headline case of an entry dated to_date earning
     nothing yet, month-end (not month-1st) carry forwards, and a full
     month-for-month cross-check against the day-by-day oracle. (This
     fixture's entire date range sits inside a single FY — 2025-04-30 to
     2026-03-31, all within FY 2025-26 — so it never exercised the old
     capitalization step at all.)
  2. include_interest OFF byte-identical guard — compute_borrowings_rows is
     untouched by this change (shape/behaviour spot check).
  3. NO-COMPOUNDING MODEL (rewritten 2026-08-06 — was "FY capitalization"):
     a flat single drawdown held across 3 full financial years (chosen to
     all be non-leap-Feb FYs, so each FY is exactly 365 days) at a fixed
     rate — confirms the two full FYs accrue the SAME interest (no growth /
     no compounding) while the FIRST FY accrues one day less, because the
     drawdown day itself no longer earns; confirms `closing_principal`
     never changes across any FY
     boundary (interest never folded into balance, ever — not just within a
     year), and confirms `_compute_fy_totals`'s `cumulative_interest`/`total`
     arithmetic across all 3 FYs, plus the key regression-pinning check that
     the next FY's "Brought Forward" is `closing_principal`, NOT `total`
     (explicitly asserted to differ from what the OLD capitalizing model
     would have produced).
  4. compute_borrowings_interest() end-to-end via a stub DB connection —
     row_type/opening-row/interest-line-item shape, missing-rate handling,
     and the multi-account balance=null merge rule.
  5. REAL DATA — the account's actual 60-row transaction history (extracted
     from IaC/helpers/Borrowings.xlsx by an agent with IaC access), 2025-04-30
     through 2026-08-05, spanning FY 25-26 and FY 26-27 for real. This data
     does NOT match section 1's reference figures (the reference omitted real
     transactions and had a few amounts transcribed differently) -- this
     block does not assert against the reference; it proves the engine runs
     end-to-end on real data and exercises a genuine FY boundary crossing
     under the no-capitalization model (balance continuous straight through
     the boundary, no jump), then prints a full monthly interest report.

Fixture note (reference fixture)
--------------------------------
NOTE (2026-08-12): the original worked example's Days/Interest column was
computed under the old accrue-from-the-entry-date rule and is therefore no
longer the expected output. The fixture itself (the reconstructed
transaction history described below) is retained unchanged and is still
useful — the balances it produces are unaffected — but the expectations
asserted against it are now hand-derived under the new rule and confirmed
by the independent day-by-day oracle.

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
balances.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from datetime import date, timedelta

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


def brute_force_monthly_interest(rows, rate, to_date):
    """INDEPENDENT day-by-day oracle for the 2026-08-12 accrual rule.

    Deliberately shares NO machinery with the engine — no events, no
    segments, no month boundaries, no terminal marker. It simply walks every
    calendar day and charges interest on the balance implied by every
    transaction dated STRICTLY BEFORE that day, which is the whole of the
    rule "interest starts the day AFTER an entry": an entry dated the 8th
    first contributes to the balance charged on the 9th.

    Returns {(year, month): unrounded interest} — directly comparable to
    compute_interest_segments()'s `monthly_interest`.
    """
    monthly = {}
    first_date = min(r['transaction_date'] for r in rows)
    day = first_date + timedelta(days=1)
    while day <= to_date:
        balance = round(
            sum(r['credit'] - r['debit'] for r in rows if r['transaction_date'] < day), 2,
        )
        key = (day.year, day.month)
        monthly[key] = monthly.get(key, 0.0) + balance * (rate / 100.0) / 365.0
        day += timedelta(days=1)
    return monthly


def check_against_oracle(label, rows, rate, to_date):
    """Assert the engine's per-month buckets AND grand total both equal the
    day-by-day oracle's, to 2 dp (ignoring buckets that round to zero)."""
    _segments, monthly, _month_bal, _opening = borrowings.compute_interest_segments(
        rows, rate=rate, to_date=to_date,
    )
    oracle = brute_force_monthly_interest(rows, rate, to_date)
    nonzero = lambda d: {k: round(v, 2) for k, v in d.items() if round(v, 2)}
    check(f"{label} — every monthly bucket matches the day-by-day oracle",
          nonzero(oracle), nonzero(monthly))
    check(f"{label} — grand total interest matches the day-by-day oracle",
          round(sum(oracle.values()), 2), round(sum(monthly.values()), 2))


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


# Running PRINCIPAL is unaffected by the 2026-08-12 accrual-date change —
# these are the same balances the original reference table asserted, and they
# still hold exactly (only the day counts each balance is charged for moved).
REFERENCE_BALANCES = [
    (date(2025, 4, 30), 'JE2526-60', 4845.0),
    (date(2025, 5, 12), 'BR2526-1', 104845.0),
    (date(2025, 5, 18), 'BR2526-2', 105844.0),
    (date(2025, 5, 18), 'JE2526-61', 113394.0),
    (date(2025, 5, 19), 'BR2526-3', 513394.0),
    (date(2025, 5, 19), 'BR2526-4', 813394.0),
    (date(2025, 5, 20), 'JE2526-62', 817894.0),
    (date(2025, 5, 29), 'JE2526-70', 885894.0),
    (date(2025, 5, 31), 'JE2526-71', 897640.0),
    (date(2025, 6, 4), 'BP2526-10', 847640.0),
    (date(2025, 6, 9), 'BR2526-26', 2747640.0),
    (date(2026, 3, 4), 'BP2526-112', 4701593.0),
    (date(2026, 3, 31), 'JE2526-87', 4720682.0),
]

for d, voucher, exp_balance in REFERENCE_BALANCES:
    seg = by_key.get((d, voucher))
    label = f"{d.isoformat()} {voucher}"
    if seg is None:
        check(f"{label} — segment exists", True, False)
        continue
    check(f"{label} — balance", exp_balance, r2(seg['balance']))

# ── DAY COUNTS under the 2026-08-12 rule: interest starts the day AFTER an
# entry, so a segment beginning on date D covers the days (D, next_event],
# and carry-forward events sit on each month's LAST day (not its 1st). Each
# expected value below is hand-derived from the fixture's own dates. ────────
REFERENCE_DAYS = [
    # 30-Apr entry -> next event is the 12-May receipt; days = 01..12 May.
    (date(2025, 4, 30), 'JE2526-60', 12),
    # 12-May -> 18-May: days = 13..18 May.
    (date(2025, 5, 12), 'BR2526-1', 6),
    # Two entries share 18-May: the first closes with a 0-day segment.
    (date(2025, 5, 18), 'BR2526-2', 0),
    # 31-May entry -> the next real entry, BP2526-10 on 04-Jun (no carry
    # forward is inserted at 31-May, a transaction already occupies it):
    # days = 01..04 June.
    (date(2025, 5, 31), 'JE2526-71', 4),
    # 04-Mar-2026 -> 31-Mar-2026: days = 05..31 March.
    (date(2026, 3, 4), 'BP2526-112', 27),
    # An entry dated to_date itself earns nothing yet — interest would start
    # 01-Apr, which is outside the window. THIS is the reported bug.
    (date(2026, 3, 31), 'JE2526-87', 0),
]

for d, voucher, exp_days in REFERENCE_DAYS:
    seg = by_key.get((d, voucher))
    label = f"{d.isoformat()} {voucher}"
    if seg is None:
        check(f"{label} — segment exists", True, False)
        continue
    check(f"{label} — days (accrual starts the day after the entry)", exp_days, seg['days'])
    check(f"{label} — interest == balance * 12% * days/365",
          r2(seg['balance'] * 0.12 * exp_days / 365.0), r2(seg['interest']))

# Carry-forward events now sit on each month's LAST day, never its 1st.
check("no carry-forward event is dated the 1st of a month",
      [], [s['date'].isoformat() for s in segments if s['kind'] == 'boundary' and s['date'].day == 1])
check("carry-forward events are dated month-end (31-May-2025 present, 01-Jun-2025 absent)",
      (True, False),
      ((date(2025, 5, 31), '') in by_key or any(
          s['date'] == date(2025, 5, 31) for s in segments),
       (date(2025, 6, 1), '') in by_key))

# April 2025 accrues NOTHING: the account's first entry is dated 30-Apr, so
# its first interest-bearing day is 01-May.
check("April 2025 has no interest bucket (first entry dated 30-Apr earns from 01-May)",
      False, (2025, 4) in monthly_interest)

# The whole fixture, cross-checked month-for-month against the independent
# day-by-day oracle (a completely separate algorithm — see its docstring).
check_against_oracle("reference fixture", fixture, 12.0, to_date)


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
# 3. NO-COMPOUNDING MODEL (rewritten 2026-08-06 — interest is never carried
#    forward; only principal is brought forward into the next FY). A single
#    credit of 100,000 held with NO other activity across 3 FULL financial
#    years, chosen so every one of the 3 FYs has a non-leap February (2025,
#    2026, 2027 are all non-leap) — each FY is therefore exactly 365 days,
#    so "same interest each full FY" can be asserted as an EXACT equality
#    rather than "modulo day counts".
# ══════════════════════════════════════════════════════════════════════════════

print("\n=== 3. No-compounding model across 3 FYs (synthetic, rate=10%) ===")

fy3_fixture = [row(date(2024, 4, 1), 'OPEN-1', credit=100000.0)]
fy3_to_date = date(2027, 4, 5)  # a few days into FY 2027-28, past FY 2026-27's close
fy3_segments, fy3_monthly, fy3_month_bal, _ = borrowings.compute_interest_segments(
    fy3_fixture, rate=10.0, to_date=fy3_to_date,
)

# Balance NEVER changes, in ANY of the 3 FYs — not just "mid-year", but
# straight across every FY boundary too (no capitalization step exists
# anymore at all).
balance_always_flat = all(round(s['balance'], 2) == 100000.0 for s in fy3_segments)
check(
    "balance stays flat at 100,000.00 across all 3 FYs, including every FY boundary "
    "(no capitalization anywhere, ever)",
    True, balance_always_flat,
)

fy_totals_3 = borrowings._compute_fy_totals(fy3_monthly, fy3_month_bal)
check("_compute_fy_totals produced entries for FY 2024-25, 2025-26, 2026-27",
      True, {'2024-25', '2025-26', '2026-27'} <= set(fy_totals_3.keys()))

# FY 2025-26 and FY 2026-27 are each a full 365 interest-bearing days and
# accrue exactly 100000 * 0.10 * 365/365 = 10,000.00 — never growing, since
# the balance the NEXT FY's interest is computed on is never inflated by the
# PREVIOUS FY's interest.
for fy_label in ('2025-26', '2026-27'):
    check(f"FY {fy_label} own interest == 10,000.00 (flat 100,000 @ 10%, 365 days, no compounding)",
          10000.0, fy_totals_3[fy_label]['interest'])

# FY 2024-25 is the one exception, and it is the 2026-08-12 rule in action:
# the drawdown is dated 01-Apr-2024, and interest starts the day AFTER an
# entry, so that FY has 364 interest-bearing days (02-Apr-2024 .. 31-Mar-2025)
# — 100000 * 0.10 * 364/365 = 9,972.60, NOT the 10,000.00 the old
# accrue-from-the-entry-date engine produced.
check("FY 2024-25 own interest == 9,972.60 (364 days — the 01-Apr drawdown day itself earns nothing)",
      9972.60, fy_totals_3['2024-25']['interest'])
check("FY 2024-25 interest is strictly less than a full year's (the entry day is excluded)",
      True, fy_totals_3['2024-25']['interest'] < fy_totals_3['2025-26']['interest'])

# closing_principal is IDENTICAL in every FY — 100,000.00, untouched by any
# amount of accrued interest.
for fy_label in ('2024-25', '2025-26', '2026-27'):
    check(f"FY {fy_label} closing_principal == 100,000.00 (interest never folds into principal)",
          100000.0, fy_totals_3[fy_label]['closing_principal'])

# cumulative_interest / total arithmetic across all 3 FYs — a running sum,
# never reset, never rebased on a capitalized balance.
check("FY 2024-25 cumulative_interest == 9,972.60 (first FY — no earlier interest to add)",
      9972.60, fy_totals_3['2024-25']['cumulative_interest'])
check("FY 2025-26 cumulative_interest == 19,972.60 (9,972.60 + 10,000)",
      19972.60, fy_totals_3['2025-26']['cumulative_interest'])
check("FY 2026-27 cumulative_interest == 29,972.60 (9,972.60 + 10,000 + 10,000)",
      29972.60, fy_totals_3['2026-27']['cumulative_interest'])

check("FY 2024-25 total == closing_principal + cumulative_interest (100,000 + 9,972.60 = 109,972.60)",
      109972.60, fy_totals_3['2024-25']['total'])
check("FY 2025-26 total == closing_principal + cumulative_interest (100,000 + 19,972.60 = 119,972.60)",
      119972.60, fy_totals_3['2025-26']['total'])
check("FY 2026-27 total == closing_principal + cumulative_interest (100,000 + 29,972.60 = 129,972.60)",
      129972.60, fy_totals_3['2026-27']['total'])

# THE key regression-pinning invariant: the next FY's "Brought Forward" is
# `closing_principal`, NEVER `total` — explicitly confirm this differs from
# what the OLD (capitalizing) model would have carried forward.
check(
    "FY 2025-26's closing_principal (what 'Brought Forward' into FY 2026-27 must equal) "
    "== FY 2024-25's closing_principal (100,000.00) — principal-only carry-forward",
    fy_totals_3['2024-25']['closing_principal'], fy_totals_3['2025-26']['closing_principal'],
)
check(
    "FY 2025-26's closing_principal (100,000.00) != FY 2024-25's total (110,000.00) — "
    "'Brought Forward' must NOT equal the old capitalizing model's carried-forward figure",
    True, fy_totals_3['2025-26']['closing_principal'] != fy_totals_3['2024-25']['total'],
)

# ── A second fixture, with a REAL transaction inside the middle FY, so
# closing_principal actually changes FY-to-FY — proves "brought forward ==
# closing_principal" holds even when principal itself moves, and that the
# transaction-driven change is untouched by interest accrual. ────────────────
fy3b_fixture = [
    row(date(2024, 4, 1), 'OPEN-1', credit=100000.0),
    row(date(2025, 6, 1), 'ADD-1', credit=50000.0),   # FY 2025-26 — principal grows here
    row(date(2026, 8, 1), 'PAY-1', debit=20000.0),    # FY 2026-27 — principal shrinks here
]
fy3b_segments, fy3b_monthly, fy3b_month_bal, _ = borrowings.compute_interest_segments(
    fy3b_fixture, rate=10.0, to_date=fy3_to_date,
)
fy_totals_3b = borrowings._compute_fy_totals(fy3b_monthly, fy3b_month_bal)

check("[with mid-FY transactions] FY 2024-25 closing_principal == 100,000.00 (no transactions yet)",
      100000.0, fy_totals_3b['2024-25']['closing_principal'])
check("[with mid-FY transactions] FY 2025-26 closing_principal == 150,000.00 (100,000 + 50,000 credit)",
      150000.0, fy_totals_3b['2025-26']['closing_principal'])
check("[with mid-FY transactions] FY 2026-27 closing_principal == 130,000.00 (150,000 - 20,000 debit)",
      130000.0, fy_totals_3b['2026-27']['closing_principal'])

# Independent cross-check: reconstruct the closing principal directly from
# the raw transaction deltas (bypassing the engine entirely) and confirm it
# matches — proves interest never leaked into the figure the engine reports
# as closing_principal, even with real transactions moving it FY-to-FY.
expected_closing_2026_27 = round(100000.0 + 50000.0 - 20000.0, 2)
check(
    "[with mid-FY transactions] FY 2026-27 closing_principal == sum(credit) - sum(debit) computed "
    "independently from the raw rows (100,000 + 50,000 - 20,000 = 130,000.00)",
    expected_closing_2026_27, fy_totals_3b['2026-27']['closing_principal'],
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
# The account's first entry is dated 2025-04-30 and interest starts the day
# AFTER an entry (2026-08-12 rule), so the first interest-bearing day is
# 2025-05-01 and April 2025 has no bucket at all.
check("April 2025 accrues nothing (first entry dated 30-Apr earns from 01-May)",
      False, (2025, 4) in real_monthly)

first_month = (2025, 5)
last_month = (_REAL_TO_DATE.year, _REAL_TO_DATE.month)
expected_months = []
yy, mm = first_month
while (yy, mm) <= last_month:
    expected_months.append((yy, mm))
    yy, mm = (yy + 1, 1) if mm == 12 else (yy, mm + 1)

check("one monthly interest bucket per calendar month, May 2025 through August 2026 (16 months)",
      16, len(expected_months))
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

# --- 3. interest line items never alter balance -------------------------------
# For EVERY pair of consecutive months -- including FY boundaries, since
# capitalization no longer exists at all -- the balance carried into the
# first event of month M+1 must equal month M's last segment balance
# exactly -- i.e. nothing but real transaction deltas (never the interest
# itself, and never an FY-boundary step) ever moves the balance.
by_month_first_seg = {}
for seg in real_segments:
    key = (seg['date'].year, seg['date'].month)
    if key not in by_month_first_seg:
        by_month_first_seg[key] = seg

interest_never_alters_balance = True
for i in range(len(expected_months) - 1):
    m_cur, m_next = expected_months[i], expected_months[i + 1]
    if m_cur not in real_month_bal or m_next not in by_month_first_seg:
        continue
    next_seg = by_month_first_seg[m_next]
    balance_before_next_delta = round(next_seg['balance'] - next_seg['delta'], 2)
    if balance_before_next_delta != real_month_bal[m_cur]:
        interest_never_alters_balance = False
check(
    "interest line items never alter balance, verified across EVERY month transition "
    "including the FY boundary (no capitalization step exists anymore)",
    True, interest_never_alters_balance,
)

# --- 4. principal balance at 2026-03-31 equals total credit minus total -------
#        debit for rows up to that date, arithmetically (always true now --
#        balance is ALWAYS pure principal, never touched by interest, at any
#        point, not just "before capitalization")
expected_bal_2026_03_31 = round(
    sum(credit for d, _, _, debit, credit in REAL_ROWS_RAW if d <= '2026-03-31')
    - sum(debit for d, _, _, debit, credit in REAL_ROWS_RAW if d <= '2026-03-31'),
    2,
)
actual_bal_2026_03_31 = real_month_bal.get((2026, 3))
check("principal balance at 2026-03-31 == total credit - total debit up to that date",
      expected_bal_2026_03_31, actual_bal_2026_03_31)

# --- 5. FY 26-27 opening balance == FY 25-26 closing principal (interest -----
#        NEVER added — 2026-08-06 model change: only principal carries
#        forward into the next FY)
fy2526_closing_principal = real_month_bal.get((2026, 3))
expected_fy2627_opening = fy2526_closing_principal
# April 2026 has no real transaction before BP2627-90 (2026-06-17), so its
# ENTIRE segment (the auto month-boundary at 2026-04-01) sits at the
# untouched carried-forward principal the whole month -- month_last_balance
# [(2026,4)] IS that value.
actual_fy2627_opening = real_month_bal.get((2026, 4))
check(
    "FY 26-27 opening balance == FY 25-26 closing principal EXACTLY "
    "(no interest added — only principal is brought forward)",
    expected_fy2627_opening, actual_fy2627_opening,
)

# --- 6. total interest reported == sum of the monthly line items ------------
grand_total_interest = round(sum(r['interest'] for r in real_interest_rows), 2)
sum_of_monthly_buckets = round(sum(round(v, 2) for v in real_monthly.values()), 2)
check("total interest reported == sum of the monthly line items",
      sum_of_monthly_buckets, grand_total_interest)

# --- 7. every real month bucket cross-checked against the independent -------
#        day-by-day oracle (60 real rows, 17 calendar months, a real FY
#        crossing) — the strongest single check on the 2026-08-12 rule.
check_against_oracle("real 60-row history", real_fixture, _RATE, _REAL_TO_DATE)

# --- readable report table, printed verbatim for the coordinator/user -------
# Uses borrowings._compute_fy_totals() (the same single-source-of-truth the
# renderer reads) for the FY summary figures — closing_principal / interest
# (that FY only) / cumulative_interest / total (principal + every FY's
# interest accrued so far) — rather than re-deriving them ad hoc here.
real_fy_totals = borrowings._compute_fy_totals(real_monthly, real_month_bal)

print("\n--- Monthly interest line items (LEVAKA HARANATHA REDDY, 12% p.a.) ---")
print(f"{'Month':<10} {'Date':<12} {'Balance':>14} {'Interest':>14}")
fy2627_months = [(y, m) for (y, m) in expected_months if borrowings._fy_start_year(date(y, m, 1)) == 2026]

for r in real_interest_rows:
    y, m = map(int, r['transaction_date'].split('-')[:2])
    label = date(y, m, 1).strftime('%b-%y')
    # r['balance'] (not real_month_bal[(y,m)]) — a month can accrue interest
    # without holding an event of its own (e.g. Aug-2026 here, whose accrual
    # comes off the 31-Jul carry forward), and the row already carries the
    # correct carried-forward principal for exactly that case.
    print(f"{label:<10} {r['transaction_date']:<12} {r['balance']:>14,.2f} {r['interest']:>14,.2f}")
    if (y, m) == (2026, 3):
        fy2526 = real_fy_totals['2025-26']
        print(f"\n--- FY 2025-26 summary ---")
        print(f"{'Total Principal Amount':<28} {fy2526['closing_principal']:>14,.2f}")
        print(f"{'Interest FY 2025-26':<28} {fy2526['interest']:>14,.2f}")
        print(f"{'Total Payable Amount':<28} {fy2526['total']:>14,.2f}")
        print(f"\nBrought forward into FY 2026-27 (PRINCIPAL ONLY): {fy2526['closing_principal']:>14,.2f}")
        print(f"\n--- FY 2026-27 monthly lines ---")
        print(f"{'Month':<10} {'Date':<12} {'Balance':>14} {'Interest':>14}")

fy2627 = real_fy_totals.get('2026-27')
print(f"\n--- FY 2026-27 summary (through {_REAL_TO_DATE.isoformat()}, window end -- not a full FY) ---")
if fy2627:
    print(f"{'Total Principal Amount':<28} {fy2627['closing_principal']:>14,.2f}")
    print(f"{'Interest FY 2026-27':<28} {fy2627['interest']:>14,.2f}")
    print(f"{'Total Payable Amount':<28} {fy2627['total']:>14,.2f}  "
          f"(= principal + cumulative interest {fy2627['cumulative_interest']:,.2f})")

print(f"\n--- Grand total interest, 2025-04-30 to {_REAL_TO_DATE.isoformat()} ---")
print(f"{'Grand Total Interest':<28} {grand_total_interest:>14,.2f}")


# ══════════════════════════════════════════════════════════════════════════════
# 6. FY-BOUNDARY CONTINUITY INVARIANT (2026-08-06; rewritten the same day for
#    the "interest never carried forward" model change) —
#    a FY's closing_principal (NOT total — total now includes cumulative
#    interest, which is NEVER folded into balance) must equal the balance on
#    the first row of the next FY when no transaction intervenes. Uses
#    compute_borrowings_interest() end-to-end (via a stub DB connection) on
#    the REAL LEVAKA HARANATHA REDDY data — this is the exact account/scenario
#    the original FY-boundary rounding bug report was filed against; under the
#    new model the invariant shifts from "Total Amount carries forward
#    byte-identically" to "closing_principal carries forward exactly, and
#    interest is tracked/displayed separately, never folded into balance".
# ══════════════════════════════════════════════════════════════════════════════

print("\n=== 6. FY-boundary continuity invariant (Task 1 fix) ===")


class _StubCursor2:
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


class _StubConn2:
    def __init__(self, plan):
        self._plan = plan

    def cursor(self):
        return _StubCursor2(self._plan)


def _real_history_rows(params):
    return [(date(*map(int, d.split('-'))), v, n, deb, cred) for d, v, n, deb, cred in REAL_ROWS_RAW]


real_plan = [
    ('GROUP BY b.account', ['account'], lambda p: [(_ACCOUNT_NAME,)]),
    ('FROM borrowing_rate br', ['account', 'rate'], lambda p: [(_ACCOUNT_NAME, _RATE)]),
    ('b.transaction_date <= %(to_date)s', ['transaction_date', 'voucher_no', 'transaction_name', 'debit', 'credit'], _real_history_rows),
]
real_stub_conn = _StubConn2(real_plan)

# Window covering the account's entire real history through the same
# _REAL_TO_DATE used in section 5, single-account mode.
real_result = borrowings.compute_borrowings_interest(
    real_stub_conn, _ACCOUNT_NAME, '2025-04-30', _REAL_TO_DATE.isoformat(),
)
check("compute_borrowings_interest returns a fy_totals entry for FY 2025-26",
      True, '2025-26' in real_result['fy_totals'])

fy2526_closing_principal_r6 = real_result['fy_totals']['2025-26']['closing_principal']
fy2526_total_amount = real_result['fy_totals']['2025-26']['total']
# FY 2025-26 is this account's VERY FIRST FY (its earliest transaction,
# 2025-04-30, falls inside it) — so cumulative_interest for this FY equals
# that FY's own interest exactly (nothing earlier to add), which is why the
# reference figure below is UNCHANGED by the interest-never-carried-forward
# model change even though `total`'s general formula did change.
# Regression pins, RESTATED 2026-08-12 (interest now starts the day after an
# entry): FY 2025-26's interest fell 4,14,419.20 -> 4,12,721.75 and its total
# 55,77,494.20 -> 55,75,796.75. Both new figures are independently confirmed
# by the day-by-day oracle immediately below, not merely by the engine.
oracle_fy2526_interest = round(sum(
    v for (y, m), v in brute_force_monthly_interest(real_fixture, _RATE, _REAL_TO_DATE).items()
    if borrowings._fy_start_year(date(y, m, 1)) == 2025
), 2)
check("fy_totals['2025-26']['interest'] == the day-by-day oracle's FY 2025-26 total",
      oracle_fy2526_interest, real_result['fy_totals']['2025-26']['interest'])
check("fy_totals['2025-26']['interest'] == 4,12,721.75 (was 4,14,419.20 before the "
      "2026-08-12 accrue-from-the-next-day fix)",
      412721.75, real_result['fy_totals']['2025-26']['interest'])
check("fy_totals['2025-26']['total'] == 55,75,796.75 (was 55,77,494.20) — this is the "
      "account's first FY, so cumulative_interest == interest here",
      5575796.75, fy2526_total_amount)
check("fy_totals['2025-26']['cumulative_interest'] == fy_totals['2025-26']['interest'] (first FY)",
      real_result['fy_totals']['2025-26']['interest'],
      real_result['fy_totals']['2025-26']['cumulative_interest'])

# The first row chronologically inside FY 2026-27 (April 2026 has no real
# transaction until BP2627-90 on 2026-06-17, so the April interest line
# item's balance IS the untouched brought-forward value — "no transaction
# intervenes").
fy2627_rows = sorted(
    (r for r in real_result['rows'] if r['transaction_date'] >= '2026-04-01'),
    key=lambda r: r['transaction_date'],
)
first_fy2627_row = fy2627_rows[0] if fy2627_rows else None
check("a row exists at the start of FY 2026-27", True, first_fy2627_row is not None)
check(
    "FY 2025-26's closing_principal == the balance on the first FY 2026-27 row "
    "(principal-only carry-forward — interest is tracked separately, never folded into balance)",
    fy2526_closing_principal_r6,
    first_fy2627_row['balance'] if first_fy2627_row else None,
)
check(
    "FY 2025-26's closing_principal != FY 2025-26's total (55,77,494.20) — the balance carried "
    "forward is NOT the old capitalizing model's figure",
    True, fy2526_closing_principal_r6 != fy2526_total_amount,
)


# ══════════════════════════════════════════════════════════════════════════════
# 7. /borrowings/summary-fy ROLL-FORWARD INVARIANT (Task 6; rewritten
#    2026-08-06 for the "interest never carried forward" model change) —
#    closing == opening + taken - paid (PRINCIPAL ONLY, interest excluded),
#    each FY's opening equals the prior FY's closing, cumulative_interest is
#    a running sum of that FY's own interest, and total_payable == closing +
#    cumulative_interest. Two synthetic accounts with activity in different,
#    overlapping FYs, via a stub DB connection.
# ══════════════════════════════════════════════════════════════════════════════

print("\n=== 7. /borrowings/summary-fy roll-forward invariant (Task 6) ===")


class _StubCursor3:
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


class _StubConn3:
    def __init__(self, plan):
        self._plan = plan

    def cursor(self):
        return _StubCursor3(self._plan)


_SUMMARY_HISTORY = {
    'ACCOUNT ONE': [
        (date(2024, 5, 1), 'S1-1', 'Journal Entries', 0.0, 1000000.0),   # FY 2024-25 taken
        (date(2024, 6, 1), 'S1-2', 'Journal Entries', 200000.0, 0.0),    # FY 2024-25 paid
        (date(2025, 5, 1), 'S1-3', 'Journal Entries', 0.0, 500000.0),    # FY 2025-26 taken
        (date(2025, 6, 1), 'S1-4', 'Journal Entries', 100000.0, 0.0),    # FY 2025-26 paid
    ],
    'ACCOUNT TWO': [
        (date(2025, 4, 15), 'S2-1', 'Journal Entries', 0.0, 2000000.0),  # FY 2025-26 taken only
    ],
}


def _summary_accounts_rows(params):
    return [('ACCOUNT ONE',), ('ACCOUNT TWO',)]


def _summary_rate_rows(params):
    return [('ACCOUNT ONE', 12.0), ('ACCOUNT TWO', 10.0)]


def _summary_history_rows(params):
    return _SUMMARY_HISTORY[params['account']]


summary_plan = [
    ('GROUP BY account', ['account'], _summary_accounts_rows),
    ('FROM borrowing_rate br', ['account', 'rate'], _summary_rate_rows),
    ('b.transaction_date <= %(to_date)s', ['transaction_date', 'voucher_no', 'transaction_name', 'debit', 'credit'], _summary_history_rows),
]
summary_stub_conn = _StubConn3(summary_plan)

for label, include_interest_flag in (('include_interest=0', False), ('include_interest=1', True)):
    summary = borrowings.compute_borrowings_summary_fy(summary_stub_conn, include_interest_flag)

    check(f"[{label}] fys is non-empty and covers both accounts' activity", True, len(summary['fys']) >= 2)

    if not include_interest_flag:
        all_interest_zero = all(
            fy_data['interest'] == 0.0
            for row in summary['rows']
            for fy_data in row['fys'].values()
        )
        check(f"[{label}] interest is 0.0 everywhere when include_interest=0", True, all_interest_zero)

    for row in summary['rows']:
        acct = row['account']
        prior_closing = 0.0
        prior_cumulative_interest = 0.0
        roll_forward_ok = True
        opening_matches_prior_ok = True
        cumulative_interest_ok = True
        total_payable_ok = True
        for fy in summary['fys']:
            fy_data = row['fys'].get(fy)
            if fy_data is None:
                roll_forward_ok = False
                continue
            # PRINCIPAL ONLY — interest is never added here (2026-08-06 model
            # change: interest is never carried forward).
            expected_closing = round(fy_data['opening'] + fy_data['taken'] - fy_data['paid'], 2)
            if round(fy_data['closing'], 2) != expected_closing:
                roll_forward_ok = False
            if round(fy_data['opening'], 2) != round(prior_closing, 2):
                opening_matches_prior_ok = False
            expected_cumulative_interest = round(prior_cumulative_interest + fy_data['interest'], 2)
            if round(fy_data['cumulative_interest'], 2) != expected_cumulative_interest:
                cumulative_interest_ok = False
            expected_total_payable = round(fy_data['closing'] + fy_data['cumulative_interest'], 2)
            if round(fy_data['total_payable'], 2) != expected_total_payable:
                total_payable_ok = False
            prior_closing = fy_data['closing']
            prior_cumulative_interest = fy_data['cumulative_interest']
        check(f"[{label}] {acct}: closing == opening + taken - paid (PRINCIPAL ONLY) for every FY",
              True, roll_forward_ok)
        check(f"[{label}] {acct}: each FY's opening == the prior FY's closing",
              True, opening_matches_prior_ok)
        check(f"[{label}] {acct}: cumulative_interest == running sum of that FY's own interest",
              True, cumulative_interest_ok)
        check(f"[{label}] {acct}: total_payable == closing + cumulative_interest for every FY",
              True, total_payable_ok)
        check(f"[{label}] {acct}: row-level 'closing' == the last FY's closing (principal only)",
              round(prior_closing, 2), round(row['closing'], 2))
        check(f"[{label}] {acct}: row-level 'total_payable' == last closing + last cumulative_interest",
              round(prior_closing + prior_cumulative_interest, 2), round(row['total_payable'], 2))

    # Every account's fys map contains an entry for EVERY fy in the list
    # (rectangular matrix, no null-checks needed by the caller).
    rectangular_ok = all(
        set(row['fys'].keys()) == set(summary['fys'])
        for row in summary['rows']
    )
    check(f"[{label}] every row's fys map is rectangular (one entry per global FY)", True, rectangular_ok)

    # totals are column-wise sums across accounts, same shape.
    totals_ok = True
    for fy in summary['fys']:
        for key in ('opening', 'taken', 'paid', 'interest', 'closing', 'cumulative_interest', 'total_payable'):
            expected_sum = round(sum(row['fys'][fy][key] for row in summary['rows']), 2)
            if round(summary['totals'][fy][key], 2) != expected_sum:
                totals_ok = False
    check(f"[{label}] totals (incl. cumulative_interest/total_payable) are column-wise sums across accounts",
          True, totals_ok)

    if include_interest_flag:
        # With a nonzero rate configured for both accounts, interest must be
        # strictly positive once activity exists — proving the toggle
        # produces a real amount, even though it never affects `closing`.
        any_interest_present = any(
            fy_data['interest'] > 0.0
            for row in summary['rows']
            for fy_data in row['fys'].values()
        )
        check(f"[{label}] at least one FY shows nonzero interest (rate configured)",
              True, any_interest_present)
        # And `closing` (principal) must be IDENTICAL to the include_interest=0
        # run for the very same fixture — the interest toggle changes
        # interest/cumulative_interest/total_payable ONLY, never `closing`.
        summary_off_for_compare = borrowings.compute_borrowings_summary_fy(summary_stub_conn, False)
        closing_identical = all(
            round(row_on['fys'][fy]['closing'], 2) == round(row_off['fys'][fy]['closing'], 2)
            for row_on, row_off in zip(summary['rows'], summary_off_for_compare['rows'])
            for fy in summary['fys']
        )
        check(f"[{label}] 'closing' (principal) is IDENTICAL to the include_interest=0 run "
              "(the toggle never changes principal)", True, closing_identical)


# ══════════════════════════════════════════════════════════════════════════════
# 8. /borrowings/summary-fy — DORMANT MIDDLE FY must not be silently skipped
#    (2026-08-06 fix). An account borrows in FY1, has ZERO transactions in
#    FY2, and repays in FY3 — FY2 must still appear (contiguous FY range),
#    zero-filled, with opening == closing == FY1's closing, REGARDLESS of
#    the include_interest toggle. Uses a fixed `as_of` (not date.today())
#    for full determinism.
# ══════════════════════════════════════════════════════════════════════════════

print("\n=== 8. /borrowings/summary-fy — dormant middle FY must not vanish (2026-08-06 fix) ===")


class _StubCursor4:
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


class _StubConn4:
    def __init__(self, plan):
        self._plan = plan

    def cursor(self):
        return _StubCursor4(self._plan)


_GAP_ACCOUNT = 'GAP FY ACCOUNT'
_GAP_AS_OF = date(2025, 6, 1)  # inside FY3 (2025-26)
_GAP_HISTORY = [
    (date(2023, 5, 1), 'G-1', 'Journal Entries', 0.0, 500000.0),   # FY1 (2023-24) -- taken
    # FY2 (2024-25) -- deliberately ZERO transactions
    (date(2025, 5, 1), 'G-2', 'Journal Entries', 200000.0, 0.0),   # FY3 (2025-26) -- paid
]


def _gap_accounts_rows(params):
    return [(_GAP_ACCOUNT,)]


def _gap_rate_rows(params):
    return [(_GAP_ACCOUNT, 12.0)]


def _gap_history_rows(params):
    return _GAP_HISTORY


gap_plan = [
    ('GROUP BY account', ['account'], _gap_accounts_rows),
    ('FROM borrowing_rate br', ['account', 'rate'], _gap_rate_rows),
    ('b.transaction_date <= %(to_date)s', ['transaction_date', 'voucher_no', 'transaction_name', 'debit', 'credit'], _gap_history_rows),
]
gap_stub_conn = _StubConn4(gap_plan)

gap_off = borrowings.compute_borrowings_summary_fy(gap_stub_conn, False, as_of=_GAP_AS_OF)
gap_on = borrowings.compute_borrowings_summary_fy(gap_stub_conn, True, as_of=_GAP_AS_OF)

check("[include_interest=0] fys contains all three FYs contiguously",
      ['2023-24', '2024-25', '2025-26'], gap_off['fys'])
check("[include_interest=1] fys contains all three FYs contiguously",
      ['2023-24', '2024-25', '2025-26'], gap_on['fys'])
check("the FY list is identical between include_interest=0 and include_interest=1 "
      "(the toggle must change interest amounts, never FY coverage)",
      gap_off['fys'], gap_on['fys'])

check("[include_interest=0] totals['2024-25'] (the dormant middle FY) exists",
      True, '2024-25' in gap_off['totals'])
check("[include_interest=1] totals['2024-25'] (the dormant middle FY) exists",
      True, '2024-25' in gap_on['totals'])

gap_off_row = next(r for r in gap_off['rows'] if r['account'] == _GAP_ACCOUNT)
fy2_off = gap_off_row['fys']['2024-25']
fy1_off = gap_off_row['fys']['2023-24']
check("[include_interest=0] FY2 (dormant) has zero taken/paid/interest",
      (0.0, 0.0, 0.0), (fy2_off['taken'], fy2_off['paid'], fy2_off['interest']))
check("[include_interest=0] FY2's opening == FY2's closing (nothing happened that year)",
      True, fy2_off['opening'] == fy2_off['closing'])
check("[include_interest=0] FY2's opening == FY1's closing (correctly carried forward across the gap)",
      fy1_off['closing'], fy2_off['opening'])
check("[include_interest=0] FY2's opening == closing == 5,00,000.00 (FY1's taken, no interest)",
      500000.0, fy2_off['opening'])

gap_on_row = next(r for r in gap_on['rows'] if r['account'] == _GAP_ACCOUNT)
fy2_on = gap_on_row['fys']['2024-25']
fy1_on = gap_on_row['fys']['2023-24']
check("[include_interest=1] FY2 (dormant) still has zero taken/paid (no transactions that year)",
      (0.0, 0.0), (fy2_on['taken'], fy2_on['paid']))
check("[include_interest=1] FY2's opening == the prior FY's (FY1's) closing",
      gap_on_row['fys']['2023-24']['closing'], fy2_on['opening'])
# With a nonzero rate, interest DOES accrue during the dormant FY2 (the
# engine still walks its month-boundary events even with zero real
# transactions) -- demonstrating the toggle changes AMOUNTS, never coverage.
check("[include_interest=1] FY2 accrues nonzero interest (rate=12%, dormant but not interest-free)",
      True, fy2_on['interest'] > 0.0)
# 2026-08-06 model change: `closing` (principal) must be IDENTICAL between
# include_interest=0 and include_interest=1 for EVERY FY, including the
# dormant one — interest never touches principal, in either mode, anymore.
check("[both modes] FY2's closing (principal) is IDENTICAL whether include_interest is 0 or 1",
      fy2_off['closing'], fy2_on['closing'])
check("[both modes] FY1's closing (principal) is IDENTICAL whether include_interest is 0 or 1",
      fy1_off['closing'], fy1_on['closing'])
# cumulative_interest / total_payable arithmetic through the dormant FY —
# FY2 accrues real interest (checked above) even though taken/paid are zero,
# so cumulative_interest must strictly increase from FY1 to FY2, and
# total_payable must reflect principal (unchanged) + the growing interest.
check("[include_interest=1] FY2's cumulative_interest == FY1's cumulative_interest + FY2's own interest",
      round(fy1_on['cumulative_interest'] + fy2_on['interest'], 2),
      round(fy2_on['cumulative_interest'], 2))
check("[include_interest=1] FY2's cumulative_interest > FY1's (strictly increasing, dormant FY still accrues)",
      True, fy2_on['cumulative_interest'] > fy1_on['cumulative_interest'])
check("[include_interest=1] FY2's total_payable == FY2's closing (principal, unchanged) + FY2's cumulative_interest",
      round(fy2_on['closing'] + fy2_on['cumulative_interest'], 2),
      round(fy2_on['total_payable'], 2))


# ══════════════════════════════════════════════════════════════════════════════
print(f"\n{'=' * 60}\nTOTAL: {PASS} passed, {FAIL} failed\n{'=' * 60}")
sys.exit(1 if FAIL else 0)
