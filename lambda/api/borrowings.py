"""
borrowings — shared compute for the Borrowings ledger, including the monthly
interest-accrual engine (added 2026-08-06).

Public surface
--------------
compute_borrowings_rows(conn, account, from_date, to_date) -> list[dict]
    Extracted verbatim (2026-08-05) from handler._handle_borrowings_data's
    SQL/row-building logic so GET /borrowings (JSON) and GET /borrowings/pdf
    can never disagree in the `include_interest` OFF case — both call this
    same function. UNCHANGED by the 2026-08-06 interest work — the OFF
    response stays byte-identical.

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

compute_borrowing_rate_map(conn, accounts=None) -> dict[str, float]
    Per-account annual interest rate (percent), from `borrowing_rate`
    (open rows only, out_z IS NULL), keyed on `account`. Accounts with no
    configured row are simply absent from the returned dict — callers treat
    a missing key as rate 0 (no interest; never an error).

compute_interest_segments(txn_rows, rate, to_date, from_date=None)
    -> (segments, monthly_interest, month_last_balance, opening_balance)
    The low-level, per-EVENT interest-accrual engine — this is the exact
    algorithm from the task spec (event list, month-boundary carry-forward
    events, terminal boundary, simple 365-day interest per segment, FY
    capitalization at 31 March) and is what the reference test in
    test_borrowings_interest.py exercises directly. See the docstring below
    for the full algorithm description.

compute_borrowings_interest(conn, account, from_date, to_date) -> dict
    The GET /borrowings?include_interest=1 (and GET /borrowings/pdf
    ?include_interest=1) entry point — single shared implementation used by
    BOTH the JSON endpoint and the PDF renderer, per the task's hard
    requirement that "the screen and the PDF must never be able to disagree".
    Returns {'rows': [...], 'missing_rate_accounts': [...], 'fy_totals': {...}}
    — see its docstring for the full row shape / multi-account merge rules /
    fy_totals semantics (fy_totals added 2026-08-06, single-account mode only).
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta


# ══════════════════════════════════════════════════════════════════════════════
# include_interest OFF — unchanged from 2026-08-05
# ══════════════════════════════════════════════════════════════════════════════

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


# ══════════════════════════════════════════════════════════════════════════════
# include_interest ON — monthly interest accrual engine (added 2026-08-06)
# ══════════════════════════════════════════════════════════════════════════════

_ROW_TYPE_RANK = {'opening': 0, 'transaction': 1, 'interest': 2}


def _parse_iso_date(value: str) -> date:
    return datetime.strptime(value, '%Y-%m-%d').date()


def _next_month(y: int, m: int) -> tuple[int, int]:
    return (y + 1, 1) if m == 12 else (y, m + 1)


def _fy_start_year(d: date) -> int:
    """Indian FY (1 April - 31 March) start year containing date `d`."""
    return d.year if d.month >= 4 else d.year - 1


def _last_day_of_month(y: int, m: int) -> date:
    return date(y, m, calendar.monthrange(y, m)[1])


def _line_item_date(y: int, m: int, to_date: date) -> date:
    """Interest line-item date for calendar month (y, m): the last calendar
    day of that month, UNLESS to_date falls inside that same month (the
    "current" month is truncated by the window) — in which case to_date
    itself is used. Every earlier month is always "complete" (to_date can
    never precede it, since the engine only ever builds boundaries through
    the month containing to_date)."""
    natural_end = _last_day_of_month(y, m)
    if (y, m) == (to_date.year, to_date.month):
        return min(natural_end, to_date)
    return natural_end


# ── rate lookup ──────────────────────────────────────────────────────────────

def compute_borrowing_rate_map(conn, accounts: list[str] | None = None) -> dict[str, float]:
    """Per-account annual interest rate (percent) from `borrowing_rate`
    (open rows only). Accounts with no configured rate are simply absent
    from the dict — callers must treat a missing key as 0% (never an error).
    """
    with conn.cursor() as cur:
        if accounts:
            cur.execute("""
                SELECT br.account, br.rate
                FROM borrowing_rate br
                WHERE br.out_z IS NULL AND br.account = ANY(%(accounts)s)
            """, {'accounts': list(accounts)})
        else:
            cur.execute("""
                SELECT br.account, br.rate
                FROM borrowing_rate br
                WHERE br.out_z IS NULL
            """)
        return {row[0]: float(row[1]) for row in cur.fetchall()}


def _distinct_accounts_in_range(conn, from_date: str, to_date: str) -> list[str]:
    """Accounts in scope for the "all accounts" (account param blank) case —
    the same set GET /borrowings (interest OFF) would show for this window.
    GROUP BY (not SELECT DISTINCT) + ORDER BY LOWER(...) — SELECT DISTINCT
    combined with an ORDER BY expression not in the select list is invalid
    Postgres (see the /borrowings/meta production bug in CLAUDE.md); GROUP BY
    avoids that class of bug entirely.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT b.account
            FROM borrowings b
            WHERE b.out_z IS NULL
              AND b.transaction_date BETWEEN %(from_date)s AND %(to_date)s
            GROUP BY b.account
            ORDER BY LOWER(b.account)
        """, {'from_date': from_date, 'to_date': to_date})
        return [row[0] for row in cur.fetchall()]


def _fetch_account_history(conn, account: str, to_date: date) -> list[dict]:
    """ALL open borrowings rows for one account, from the very first
    transaction through to_date (inclusive) — NOT sliced to any display
    window; interest depends on the balance carried in from before the
    window, so the full history is always fetched (per the task spec)."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT b.transaction_date, b.voucher_no, b.transaction_name, b.debit, b.credit
            FROM borrowings b
            WHERE b.out_z IS NULL
              AND b.account = %(account)s
              AND b.transaction_date <= %(to_date)s
            ORDER BY b.transaction_date ASC, b.voucher_no ASC
        """, {'account': account, 'to_date': to_date})
        col_names = [d[0] for d in cur.description]
        rows = []
        for raw in cur.fetchall():
            r = dict(zip(col_names, raw))
            rows.append({
                'transaction_date': r['transaction_date'],
                'voucher_no': r['voucher_no'] or '',
                'transaction_name': r['transaction_name'] or '',
                'debit': float(r['debit']),
                'credit': float(r['credit']),
            })
        return rows


# ── the low-level interest-accrual engine ────────────────────────────────────

def compute_interest_segments(
    txn_rows: list[dict],
    rate: float,
    to_date: date,
    from_date: date | None = None,
) -> tuple[list[dict], dict[tuple[int, int], float], dict[tuple[int, int], float], float]:
    """The exact per-EVENT algorithm from the task spec, for ONE account.

    Parameters
    ----------
    txn_rows : list[dict] — every open borrowings row for the account from
        its very first transaction through `to_date` (see
        _fetch_account_history); each needs transaction_date (date),
        voucher_no, transaction_name, debit, credit.
    rate : annual interest rate, percent (0 => no interest; the engine still
        walks every event so balances/segments are always produced).
    to_date : date — a terminal boundary is appended at to_date + 1 day so a
        balance held on to_date itself earns one day of interest (this is an
        explicit assumption of this implementation — see the module/task
        docs; without it the very last segment would have 0 days and accrue
        no interest at all).
    from_date : optional date — when given, `opening_balance` (4th return
        value) is captured as the running principal immediately before the
        first event whose date is >= from_date (i.e. the balance "brought
        forward" into the display window). Ignored otherwise (0.0).

    Returns
    -------
    (segments, monthly_interest, month_last_balance, opening_balance)

    segments : list[dict], one per EVENT (transaction or month-boundary, in
        chronological order — the terminal boundary itself is excluded, it
        only serves to close the final segment):
        {'kind': 'transaction'|'boundary', 'date': date, 'voucher_no': str,
         'transaction_name': str, 'debit': float, 'credit': float,
         'delta': float, 'balance': float, 'days': int, 'interest': float}
        'balance' is the running PRINCIPAL after this event's delta (and
        after any FY capitalization applied at this event, if it is the
        first event of a new financial year — see below). Interest is
        stored UNROUNDED (full float precision) — round only for display.
    monthly_interest : {(year, month): float} — sum of every segment's
        (unrounded) interest whose event date falls in that (year, month) —
        i.e. attributed to the segment's START month (a segment never
        straddles a month boundary, since a boundary event always exists at
        every month's 1st unless a real transaction already occupies it).
    month_last_balance : {(year, month): float} — the running principal as
        of the LAST segment starting in that month (captured before any
        later capitalization jump, which by construction always lands in a
        different month — April). Used as the "balance" shown on that
        month's synthetic interest line item.
    opening_balance : float — see `from_date` above; 0.0 if from_date is
        None or txn_rows is empty.

    Algorithm
    ---------
    1. Every transaction is an event with delta = credit - debit.
    2. A synthetic month-boundary event (delta 0, 'Month-end carry forward')
       is inserted on the 1st of every month from the month AFTER the first
       transaction's month through the month containing to_date — but only
       when no transaction already exists on that exact date.
    3. Events are sorted by (date, voucher_no); boundary events sort before
       same-day transactions (rank 0 vs 1) — they never actually collide
       with each other given rule 2's exclusion.
    4. A terminal boundary is appended at to_date + 1 day.
    5. Walking events in order: balance += delta; days = date-gap to the
       NEXT event; interest_segment = balance * (rate/100) * days/365 —
       365-day year, simple interest, never compounded within a year
       (interest is never added to balance except at the FY step below).
    6. FY capitalization (1 April - 31 March): when an event's date crosses
       into a new financial year (relative to the previous event), the FY
       just closed's total accrued interest (sum of its Apr-Mar monthly
       buckets, which are guaranteed complete by this point since events are
       processed strictly chronologically) is added to `balance` BEFORE
       applying this event's own delta — so the new FY opens on
       closing principal + that FY's total interest, and the next FY's
       interest compounds on the higher figure (interest compounds
       annually, never monthly).
    """
    if not txn_rows:
        return [], {}, {}, 0.0

    txn_rows = sorted(txn_rows, key=lambda r: (r['transaction_date'], r['voucher_no']))

    events: list[dict] = []
    txn_dates = set()
    for r in txn_rows:
        d = r['transaction_date']
        txn_dates.add(d)
        events.append({
            'kind': 'transaction',
            'date': d,
            'sort_rank': 1,
            'voucher_no': r['voucher_no'],
            'transaction_name': r['transaction_name'],
            'debit': r['debit'],
            'credit': r['credit'],
            'delta': r['credit'] - r['debit'],
        })

    first_date = txn_rows[0]['transaction_date']
    y, m = _next_month(first_date.year, first_date.month)
    while (y, m) <= (to_date.year, to_date.month):
        bd = date(y, m, 1)
        if bd not in txn_dates:
            events.append({
                'kind': 'boundary',
                'date': bd,
                'sort_rank': 0,
                'voucher_no': '',
                'transaction_name': 'Month-end carry forward',
                'debit': 0.0,
                'credit': 0.0,
                'delta': 0.0,
            })
        y, m = _next_month(y, m)

    events.append({
        'kind': 'terminal',
        'date': to_date + timedelta(days=1),
        'sort_rank': 0,
        'voucher_no': '',
        'transaction_name': '',
        'debit': 0.0,
        'credit': 0.0,
        'delta': 0.0,
    })

    events.sort(key=lambda e: (e['date'], e['sort_rank'], e['voucher_no']))

    segments: list[dict] = []
    monthly_interest: dict[tuple[int, int], float] = {}
    month_last_balance: dict[tuple[int, int], float] = {}
    balance = 0.0
    opening_balance = 0.0
    current_fy = _fy_start_year(events[0]['date'])

    for i in range(len(events) - 1):  # last event is the terminal boundary — no segment starts there
        ev = events[i]
        ev_fy = _fy_start_year(ev['date'])
        if ev_fy != current_fy:
            # Crossing into a new financial year: capitalize the FY just
            # closed's total accrued interest into principal (step 6) BEFORE
            # applying this event's own delta.
            fy_total = sum(
                amt for (yy, mm), amt in monthly_interest.items()
                if _fy_start_year(date(yy, mm, 1)) == current_fy
            )
            balance = round(balance + fy_total, 2)
            current_fy = ev_fy

        balance = round(balance + ev['delta'], 2)

        if from_date is not None and ev['date'] < from_date:
            opening_balance = balance

        next_date = events[i + 1]['date']
        days = (next_date - ev['date']).days
        interest = balance * (rate / 100.0) * days / 365.0

        key = (ev['date'].year, ev['date'].month)
        monthly_interest[key] = monthly_interest.get(key, 0.0) + interest
        month_last_balance[key] = balance

        segments.append({
            'kind': ev['kind'],
            'date': ev['date'],
            'voucher_no': ev['voucher_no'],
            'transaction_name': ev['transaction_name'],
            'debit': ev['debit'],
            'credit': ev['credit'],
            'delta': ev['delta'],
            'balance': balance,
            'days': days,
            'interest': interest,
        })

    return segments, monthly_interest, month_last_balance, opening_balance


# ── per-account output rows (transaction + opening; interest lines separate) ─

def _account_rows_and_monthly(
    account: str, history_rows: list[dict], rate: float, from_date: date, to_date: date,
) -> tuple[list[dict], dict[tuple[int, int], float], dict[tuple[int, int], float]]:
    segments, monthly_interest, month_last_balance, opening_balance = compute_interest_segments(
        history_rows, rate, to_date, from_date=from_date,
    )

    rows: list[dict] = []
    if history_rows and from_date > history_rows[0]['transaction_date']:
        rows.append({
            'row_type': 'opening',
            'transaction_date': from_date.isoformat(),
            'voucher_no': '',
            'transaction_name': 'Opening balance',
            'account': account,
            'debit': 0.0,
            'credit': 0.0,
            'balance': round(opening_balance, 2),
            'interest': None,
        })

    for seg in segments:
        if seg['kind'] != 'transaction':
            continue
        rows.append({
            'row_type': 'transaction',
            'transaction_date': seg['date'].isoformat(),
            'voucher_no': seg['voucher_no'],
            'transaction_name': seg['transaction_name'],
            'account': account,
            'debit': seg['debit'],
            'credit': seg['credit'],
            'balance': round(seg['balance'], 2),
            'interest': None,
        })

    return rows, monthly_interest, month_last_balance


def _fy_key(fy_start_year: int) -> str:
    """'YYYY-YY' label for the FY starting 1 April of `fy_start_year` (e.g.
    2025 -> '2025-26') — no 'FY ' prefix, matching the fy_totals/summary-fy
    JSON contract (added 2026-08-06, Task 1 / Task 6)."""
    return f'{fy_start_year}-{str(fy_start_year + 1)[-2:]}'


def _compute_fy_totals(
    monthly_interest: dict[tuple[int, int], float],
    month_last_balance: dict[tuple[int, int], float],
) -> dict[str, dict]:
    """Per-FY {closing_principal, interest, total}, derived ENTIRELY from the
    engine's own (unrounded) monthly_interest / month_last_balance — the
    single source of truth for FY summary figures (added 2026-08-06, fixes
    the FY-boundary rounding discontinuity: the old PDF renderer re-summed
    already-2dp-rounded per-row interest values, a double-rounding that could
    disagree by a paisa or two with the balance the engine itself capitalizes
    into the next FY's opening balance).

    closing_principal : the running principal as of the LAST event in that FY
        — i.e. month_last_balance's value for that FY's last (year, month)
        key — captured BEFORE that FY's own interest is capitalized into the
        next FY (see compute_interest_segments: the capitalization happens on
        the FIRST event of the FOLLOWING fy, strictly after this value was
        already recorded, so it is exactly the pre-capitalization balance).
    interest : sum of that FY's (unrounded) monthly_interest values, rounded
        to 2dp exactly once here.
    total : closing_principal + interest, rounded to 2dp exactly once — this
        is byte-identical to the balance compute_interest_segments itself
        computes when it capitalizes this FY (`round(balance + fy_total, 2)`
        with the SAME unrounded fy_total and the SAME pre-capitalization
        balance), so "Total Amount" and the next FY's "Brought Forward" can
        never disagree.
    """
    if not monthly_interest and not month_last_balance:
        return {}

    interest_by_fy: dict[int, float] = {}
    for (y, m), amt in monthly_interest.items():
        fy = _fy_start_year(date(y, m, 1))
        interest_by_fy[fy] = interest_by_fy.get(fy, 0.0) + amt

    closing_principal_by_fy: dict[int, tuple[tuple[int, int], float]] = {}
    for (y, m), bal in month_last_balance.items():
        fy = _fy_start_year(date(y, m, 1))
        key = (y, m)
        current = closing_principal_by_fy.get(fy)
        if current is None or key > current[0]:
            closing_principal_by_fy[fy] = (key, bal)

    fy_totals: dict[str, dict] = {}
    for fy in sorted(set(interest_by_fy) | set(closing_principal_by_fy)):
        closing_principal = round(closing_principal_by_fy.get(fy, (None, 0.0))[1], 2)
        interest = round(interest_by_fy.get(fy, 0.0), 2)
        fy_totals[_fy_key(fy)] = {
            'closing_principal': closing_principal,
            'interest': interest,
            'total': round(closing_principal + interest, 2),
        }
    return fy_totals


def _interest_line_rows(
    monthly_interest: dict[tuple[int, int], float],
    to_date: date,
    account: str,
    month_last_balance: dict[tuple[int, int], float] | None,
) -> list[dict]:
    """One row per (year, month) present in monthly_interest, dated the last
    calendar day of that month (or to_date if that month is truncated by the
    window). `balance` is the account's running principal as of that point
    (month_last_balance) when given, else None (multi-account combined mode,
    where balance is meaningless — see compute_borrowings_interest)."""
    rows = []
    for (y, m) in sorted(monthly_interest.keys()):
        line_date = _line_item_date(y, m, to_date)
        bal = month_last_balance.get((y, m)) if month_last_balance is not None else None
        rows.append({
            'row_type': 'interest',
            'transaction_date': line_date.isoformat(),
            'voucher_no': '',
            'transaction_name': f'Interest for {date(y, m, 1):%b-%y}',
            'account': account,
            'debit': 0.0,
            'credit': 0.0,
            'balance': round(bal, 2) if bal is not None else None,
            'interest': round(monthly_interest[(y, m)], 2),
        })
    return rows


# ── top-level entry point (shared by GET /borrowings and GET /borrowings/pdf) ─

def compute_borrowings_interest(conn, account: str, from_date: str, to_date: str) -> dict:
    """The include_interest=ON computation — the SINGLE shared implementation
    behind both GET /borrowings?include_interest=1 and
    GET /borrowings/pdf?include_interest=1 (the handler passes the resulting
    dict straight to the JSON response / to the PDF renderer — neither one
    recomputes anything, so the screen and the PDF can never disagree).

    `account` optional (blank = all accounts in scope for the window, same
    set GET /borrowings without interest would show). `from_date`/`to_date`
    are 'YYYY-MM-DD' strings and are assumed already validated as present
    by the caller (mirrors compute_borrowings_rows).

    Per-account computation:
      - Rate: from borrowing_rate (0% if unconfigured — never an error).
      - Full history (from the very first transaction through to_date) is
        always fetched, regardless of from_date — interest depends on the
        balance carried in from before the window.
      - Single account (`account` given): every row keeps its own real
        running `balance`; one interest line item per month for that
        account.
      - All accounts (`account` blank): transaction/opening rows are
        interleaved by date across every in-scope account, EACH with
        `balance: null` (a running balance is meaningless once rows from
        different accounts are mixed together) — and interest is combined
        into ONE line per month, summing every in-scope account's interest
        for that month (`account: ''`, `balance: null`).

    Returns
    -------
    {'rows': [...], 'missing_rate_accounts': [...], 'fy_totals': {...}}
        rows : list[dict], sorted (transaction_date, row-type rank
            [opening < transaction < interest], voucher_no), sliced to
            transaction_date in [from_date, to_date] (opening rows are
            always dated exactly at from_date, so this slice is a no-op for
            them — they always survive).
        missing_rate_accounts : sorted list of in-scope accounts that had no
            configured borrowing_rate row (rate defaulted to 0% for them).
        fy_totals : {fy_label: {closing_principal, interest, total}} (added
            2026-08-06, Task 1) — SINGLE-ACCOUNT mode only (populated only
            when `account` is given); empty dict `{}` in all-accounts mode,
            where a per-account capitalized principal has no well-defined
            merged analogue (same reasoning the multi-account balance=null
            rule already documents). Computed via _compute_fy_totals() from
            the engine's own unrounded monthly_interest/month_last_balance —
            see that function's docstring for why this is the single source
            of truth a renderer must read instead of re-summing already-
            rounded per-row interest values (the double-rounding that caused
            the FY-boundary discontinuity bug).
    """
    account = (account or '').strip()
    from_d = _parse_iso_date(from_date)
    to_d = _parse_iso_date(to_date)

    if account:
        accounts = [account]
    else:
        accounts = _distinct_accounts_in_range(conn, from_date, to_date)

    rate_map = compute_borrowing_rate_map(conn, accounts)
    missing_rate_accounts = sorted(a for a in accounts if a not in rate_map)

    single_account = bool(account)
    all_rows: list[dict] = []
    combined_monthly: dict[tuple[int, int], float] = {}
    fy_totals: dict[str, dict] = {}

    for acct in accounts:
        rate = rate_map.get(acct, 0.0)
        history = _fetch_account_history(conn, acct, to_d)
        acct_rows, monthly, month_last_balance = _account_rows_and_monthly(
            acct, history, rate, from_d, to_d,
        )
        if single_account:
            all_rows.extend(acct_rows)
            all_rows.extend(_interest_line_rows(monthly, to_d, acct, month_last_balance))
            # Single account in scope (accounts == [account]) — this runs
            # exactly once, so no overwrite risk.
            fy_totals = _compute_fy_totals(monthly, month_last_balance)
        else:
            # A running balance is meaningless once rows from different
            # accounts are interleaved — null it out rather than emit a
            # misleading number (per the task spec).
            for r in acct_rows:
                r['balance'] = None
            all_rows.extend(acct_rows)
            for key, amt in monthly.items():
                combined_monthly[key] = combined_monthly.get(key, 0.0) + amt

    if not single_account:
        all_rows.extend(_interest_line_rows(combined_monthly, to_d, '', None))

    # Slice to the display window (step 7). Opening rows are always dated
    # exactly at from_date so this is a no-op for them.
    all_rows = [r for r in all_rows if from_date <= r['transaction_date'] <= to_date]

    all_rows.sort(key=lambda r: (
        r['transaction_date'], _ROW_TYPE_RANK[r['row_type']], r['voucher_no'] or '',
    ))

    return {'rows': all_rows, 'missing_rate_accounts': missing_rate_accounts, 'fy_totals': fy_totals}


# ══════════════════════════════════════════════════════════════════════════════
# compute_borrowings_summary_fy — "all accounts" FY-summary (added 2026-08-06,
# Task 6). Shared by GET /borrowings/summary-fy (JSON) and
# GET /borrowings/summary-fy/pdf so the screen and the PDF can never disagree.
# ══════════════════════════════════════════════════════════════════════════════

def compute_borrowings_summary_fy(conn, include_interest: bool, as_of: date | None = None) -> dict:
    """Per-account, multi-FY roll-forward across the FULL history of the
    `borrowings` table — no date params, covers every account and every FY
    that has any recorded activity.

    Reuses the SAME per-account interest engine `compute_borrowings_interest`
    is built on (compute_interest_segments + _compute_fy_totals) — no second
    interest implementation.

    Semantics (see the API docstring in handler.py for the full contract):
      taken   = that FY's Σ credit for the account (money received)
      paid    = that FY's Σ debit for the account (repayment)
      interest = that FY's capitalized interest from the engine (0.0 for
                 every FY when include_interest is False)
      closing = opening + taken - paid + interest
      opening = prior FY's closing (0.0 for the first FY in `fys`, and for
                any FY before the account's own first appearance — both
                fall out naturally since taken/paid/interest are all 0.0
                there, so closing stays equal to the carried-forward
                opening).

    `fys` is the ascending union of every FY with recorded activity (via
    taken/paid) across ALL accounts, plus — only when include_interest is
    True — every FY the interest engine produced a bucket for (its own
    month-boundary walk can surface a dormant-but-still-accruing FY that had
    zero real transactions that year). Every account's `fys` map is filled
    for EVERY key in this list (zero-filled where the account had no
    activity that year) so the caller can render a rectangular matrix with
    no null-checks.

    Returns
    -------
    {'fys': [...], 'rows': [...], 'totals': {...}, 'missing_rate_accounts': [...]}
        rows[i] = {'account': str, 'fys': {fy_label: {opening, taken, paid,
            interest, closing}}, 'closing': float}  (`closing` = the final,
            most-recent FY's closing balance — the account's current
            outstanding balance)
        totals[fy_label] = {opening, taken, paid, interest, closing} —
            column-wise sums across every account, same shape as a row's
            per-FY entry.
        missing_rate_accounts : only meaningful when include_interest is
            True (empty list otherwise) — sorted list of in-scope accounts
            with no configured borrowing_rate row (0% was used for them).
    """
    as_of_d = as_of or date.today()

    with conn.cursor() as cur:
        cur.execute("""
            SELECT account FROM borrowings
            WHERE out_z IS NULL
            GROUP BY account
            ORDER BY LOWER(account)
        """)
        accounts = [row[0] for row in cur.fetchall()]

    if not accounts:
        return {'fys': [], 'rows': [], 'totals': {}, 'missing_rate_accounts': []}

    rate_map = compute_borrowing_rate_map(conn, accounts) if include_interest else {}
    missing_rate_accounts = (
        sorted(a for a in accounts if a not in rate_map) if include_interest else []
    )

    per_account_taken: dict[str, dict[int, float]] = {}
    per_account_paid: dict[str, dict[int, float]] = {}
    per_account_interest: dict[str, dict[int, float]] = {}
    all_fy_starts: set[int] = set()
    in_scope_accounts: list[str] = []

    for acct in accounts:
        history = _fetch_account_history(conn, acct, as_of_d)
        if not history:
            continue
        in_scope_accounts.append(acct)

        taken_by_fy: dict[int, float] = {}
        paid_by_fy: dict[int, float] = {}
        for r in history:
            fy = _fy_start_year(r['transaction_date'])
            taken_by_fy[fy] = taken_by_fy.get(fy, 0.0) + r['credit']
            paid_by_fy[fy] = paid_by_fy.get(fy, 0.0) + r['debit']
        per_account_taken[acct] = taken_by_fy
        per_account_paid[acct] = paid_by_fy
        all_fy_starts.update(taken_by_fy.keys())
        all_fy_starts.update(paid_by_fy.keys())

        interest_by_fy: dict[int, float] = {}
        if include_interest:
            rate = rate_map.get(acct, 0.0)
            _segments, monthly_interest, month_last_balance, _opening = compute_interest_segments(
                history, rate, as_of_d,
            )
            fy_totals = _compute_fy_totals(monthly_interest, month_last_balance)
            for fy_key, fy_data in fy_totals.items():
                fy_start_int = int(fy_key.split('-')[0])
                interest_by_fy[fy_start_int] = fy_data['interest']
            all_fy_starts.update(interest_by_fy.keys())
        per_account_interest[acct] = interest_by_fy

    fy_starts_sorted = sorted(all_fy_starts)
    fys = [_fy_key(fy) for fy in fy_starts_sorted]

    totals_by_fy: dict[str, dict] = {
        fy_label: {'opening': 0.0, 'taken': 0.0, 'paid': 0.0, 'interest': 0.0, 'closing': 0.0}
        for fy_label in fys
    }

    rows: list[dict] = []
    for acct in in_scope_accounts:
        taken_by_fy = per_account_taken[acct]
        paid_by_fy = per_account_paid[acct]
        interest_by_fy = per_account_interest[acct]

        acct_fys: dict[str, dict] = {}
        opening = 0.0
        for fy in fy_starts_sorted:
            fy_label = _fy_key(fy)
            taken = round(taken_by_fy.get(fy, 0.0), 2)
            paid = round(paid_by_fy.get(fy, 0.0), 2)
            interest = round(interest_by_fy.get(fy, 0.0), 2)
            opening_r = round(opening, 2)
            closing = round(opening_r + taken - paid + interest, 2)
            acct_fys[fy_label] = {
                'opening': opening_r,
                'taken': taken,
                'paid': paid,
                'interest': interest,
                'closing': closing,
            }
            t = totals_by_fy[fy_label]
            t['opening'] += opening_r
            t['taken'] += taken
            t['paid'] += paid
            t['interest'] += interest
            t['closing'] += closing
            opening = closing

        rows.append({'account': acct, 'fys': acct_fys, 'closing': round(opening, 2)})

    for fy_label in totals_by_fy:
        for key in totals_by_fy[fy_label]:
            totals_by_fy[fy_label][key] = round(totals_by_fy[fy_label][key], 2)

    return {
        'fys': fys,
        'rows': rows,
        'totals': totals_by_fy,
        'missing_rate_accounts': missing_rate_accounts,
    }
