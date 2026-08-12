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
    The low-level, per-EVENT interest-accrual engine — event list,
    month-end carry-forward events, terminal boundary, simple 365-day
    interest per segment on PRINCIPAL ONLY. Interest is NEVER capitalized
    into the running balance (2026-08-06 model change) — `balance` moves
    only on transaction deltas, so interest never compounds and each FY's
    interest is tracked/reported separately (see _compute_fy_totals).
    Interest accrues from the day AFTER an entry (2026-08-12 model change):
    a balance created on the 8th first earns interest on the 9th. See the
    docstring below for the full algorithm description.

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


def _month_key(d: date) -> tuple[int, int]:
    """The calendar month a segment starting on event date `d` accrues into.

    Interest accrues from the day AFTER the event (2026-08-12 model change),
    so a segment that starts on the last day of a month accrues entirely
    into the FOLLOWING month, and a month-end carry-forward event dated
    31-Aug is what produces September's interest. Every segment lies wholly
    inside one calendar month (see compute_interest_segments' algorithm
    notes), so this single key is exact — never an approximation.
    """
    eff = d + timedelta(days=1)
    return (eff.year, eff.month)


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
    """The per-EVENT interest-accrual algorithm, for ONE account. Interest
    is NEVER capitalized into principal (2026-08-06 model change) — only
    transaction deltas ever change `balance`; each FY's interest is
    computed and reported separately (see _compute_fy_totals), never
    carried forward or compounded.

    Parameters
    ----------
    txn_rows : list[dict] — every open borrowings row for the account from
        its very first transaction through `to_date` (see
        _fetch_account_history); each needs transaction_date (date),
        voucher_no, transaction_name, debit, credit.
    rate : annual interest rate, percent (0 => no interest; the engine still
        walks every event so balances/segments are always produced).
    to_date : date — a terminal event is appended AT to_date (2026-08-12: was
        to_date + 1 day) and sorts after every real event on that date, so
        the window's last accrual day is to_date itself and an entry made on
        to_date earns nothing yet — interest starts the day after an entry.
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
        'balance' is the running PRINCIPAL after this event's delta — ONLY
        ever changed by transaction deltas, never by interest (there is no
        capitalization step anymore). Interest is stored UNROUNDED (full
        float precision) — round only for display.
    monthly_interest : {(year, month): float} — sum of every segment's
        (unrounded) interest, attributed to the month its accrual days fall
        in, i.e. the month of the event date PLUS ONE DAY (see _month_key).
        A segment never straddles a month boundary: a carry-forward event
        exists at every month's LAST day unless a real transaction already
        occupies it, so a segment's days always lie in one calendar month.
        Zero-day segments (two events on the same date, or an entry made on
        to_date itself) contribute no bucket at all.
    month_last_balance : {(year, month): float} — the running principal as
        of the LAST event dated in that CALENDAR month (keyed on the event's
        own date, NOT the accrual month) — i.e. the closing principal for
        that month, which is what an FY's closing principal and the interest
        line item's balance column both need.
    opening_balance : float — see `from_date` above; 0.0 if from_date is
        None or txn_rows is empty.

    Algorithm
    ---------
    1. Every transaction is an event with delta = credit - debit.
    2. A synthetic month-boundary event (delta 0, 'Month-end carry forward')
       is inserted on the LAST day of every month from the first
       transaction's own month through the month containing to_date — but
       only when that date is strictly before to_date and no transaction
       already exists on it. (2026-08-12: these sat on the 1st of each month
       before interest was moved to start the day after an entry; month-END
       boundaries are what keep each segment's accrual days inside a single
       calendar month under the new rule.)
    3. Events are sorted by (date, voucher_no); boundary events sort before
       same-day transactions (rank 0 vs 1) — they never actually collide
       with each other given rule 2's exclusion.
    4. A terminal event is appended AT to_date, ranked to sort AFTER every
       real event on that date, so an entry dated to_date closes with a
       0-day segment and earns no interest yet.
    5. Walking events in order: balance += delta; days = date-gap to the
       NEXT event — the days AFTER this event, up to and including the next
       one; interest_segment = balance * (rate/100) * days/365 —
       365-day year, simple interest on PRINCIPAL ONLY. `balance` is NEVER
       adjusted for interest, at any point — no FY-boundary capitalization
       step, no compounding of any kind (2026-08-06 model change: interest
       is never carried forward, only principal is; each FY's interest is
       tracked separately via monthly_interest / _compute_fy_totals and
       accumulates as an outstanding payable rather than being folded back
       into the balance that future interest is computed on).
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
    y, m = first_date.year, first_date.month
    while (y, m) <= (to_date.year, to_date.month):
        bd = _last_day_of_month(y, m)
        # `bd < to_date` keeps the terminal event the sole event on to_date;
        # `bd >= first_date` holds by construction (the loop starts in the
        # first transaction's own month).
        if bd < to_date and bd not in txn_dates:
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
        # sort_rank 2 (> a transaction's 1): the terminal now shares to_date
        # with any transaction booked that day, and must still be the LAST
        # event so the walk below never treats a real transaction as the
        # closing marker.
        'date': to_date,
        'sort_rank': 2,
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

    for i in range(len(events) - 1):  # last event is the terminal boundary — no segment starts there
        ev = events[i]
        # balance changes ONLY via this event's own transaction delta —
        # interest is never capitalized into it (2026-08-06 model change:
        # interest is never carried forward, only principal is).
        balance = round(balance + ev['delta'], 2)

        if from_date is not None and ev['date'] < from_date:
            opening_balance = balance

        next_date = events[i + 1]['date']
        days = (next_date - ev['date']).days
        interest = balance * (rate / 100.0) * days / 365.0

        if days:
            # Accrual month = the month the segment's DAYS fall in (event
            # date + 1); a 0-day segment has no days to attribute at all and
            # would otherwise invent an empty bucket a month past to_date.
            accrual_key = _month_key(ev['date'])
            monthly_interest[accrual_key] = monthly_interest.get(accrual_key, 0.0) + interest
        # Closing principal is a calendar-month fact, keyed on the event's own
        # date — an entry on 31-March belongs to March's closing balance even
        # though its interest only starts accruing in April.
        month_last_balance[(ev['date'].year, ev['date'].month)] = balance

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
    """Per-FY {closing_principal, interest, cumulative_interest, total},
    derived ENTIRELY from the engine's own (unrounded) monthly_interest /
    month_last_balance — the single source of truth for FY summary figures,
    so both the JSON endpoint and the PDF renderer read these same numbers
    and round exactly once (avoids double-rounding drift between the two).

    INVARIANT (2026-08-06 model change — interest is never carried forward):
    the next FY's "Brought Forward" is `closing_principal`, NEVER `total` —
    interest is tracked/displayed separately per FY and accumulates as an
    outstanding payable, but it never feeds back into the balance future
    interest is computed on.

    closing_principal : the running principal as of that FY's 31-March close
        — the value carried by the latest month_last_balance key at or
        before (fy + 1, 3). Looked up "as of" rather than "that FY's own
        last key" (2026-08-12) so an FY whose final months hold no events at
        all still reports the principal carried into it, instead of 0.
        Since compute_interest_segments no longer capitalizes interest into
        `balance` at all, this is simply the principal after every
        transaction delta up to and including that FY — nothing is ever
        added to or subtracted from it on account of interest.
    interest : sum of that FY's (unrounded) monthly_interest values, rounded
        to 2dp exactly once here — THAT FY's own interest only.
    cumulative_interest : running sum of `interest` across every FY from the
        earliest through this one (inclusive) — since interest is never
        capitalized into principal, it must be tracked FY-by-FY as an
        accumulating payable instead.
    total : closing_principal + cumulative_interest, rounded to 2dp exactly
        once — the "Total Payable Amount": this FY's principal PLUS every
        FY's interest accrued so far (not just this FY's own interest).
    """
    if not monthly_interest and not month_last_balance:
        return {}

    interest_by_fy: dict[int, float] = {}
    for (y, m), amt in monthly_interest.items():
        fy = _fy_start_year(date(y, m, 1))
        interest_by_fy[fy] = interest_by_fy.get(fy, 0.0) + amt

    sorted_month_balances = sorted(month_last_balance.items())

    def _principal_asof(y: int, m: int) -> float:
        """Principal carried at the end of calendar month (y, m) — the latest
        recorded month-closing balance at or before it (0.0 before any)."""
        principal = 0.0
        for key, bal in sorted_month_balances:
            if key > (y, m):
                break
            principal = bal
        return principal

    fy_starts = set(interest_by_fy)
    fy_starts |= {_fy_start_year(date(y, m, 1)) for (y, m) in month_last_balance}

    fy_totals: dict[str, dict] = {}
    cumulative_interest = 0.0
    for fy in sorted(fy_starts):
        closing_principal = round(_principal_asof(fy + 1, 3), 2)
        interest = round(interest_by_fy.get(fy, 0.0), 2)
        cumulative_interest = round(cumulative_interest + interest, 2)
        fy_totals[_fy_key(fy)] = {
            'closing_principal': closing_principal,
            'interest': interest,
            'cumulative_interest': cumulative_interest,
            'total': round(closing_principal + cumulative_interest, 2),
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
    where balance is meaningless — see compute_borrowings_interest).

    A month can carry interest without holding any event of its own (its
    accrual is driven by the previous month's closing carry-forward event),
    so the balance falls back to the latest month closed at or before it."""
    sorted_month_balances = sorted(month_last_balance.items()) if month_last_balance else []
    rows = []
    for (y, m) in sorted(monthly_interest.keys()):
        line_date = _line_item_date(y, m, to_date)
        bal = None
        if month_last_balance is not None:
            for key, month_bal in sorted_month_balances:
                if key > (y, m):
                    break
                bal = month_bal
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
        fy_totals : {fy_label: {closing_principal, interest,
            cumulative_interest, total}} (added 2026-08-06, Task 1;
            `cumulative_interest` added when interest capitalization was
            removed the same day) — SINGLE-ACCOUNT mode only (populated only
            when `account` is given); empty dict `{}` in all-accounts mode,
            where a per-account principal roll-forward has no well-defined
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
      interest = that FY's own interest accrued from the engine (0.0 for
                 every FY when include_interest is False) — NEVER folded
                 into `closing` (2026-08-06 model change: interest is never
                 carried forward, only principal is brought forward into
                 the next FY).
      closing = opening + taken - paid  (PRINCIPAL ONLY — interest is never
                added here)
      opening = prior FY's closing (0.0 for the first FY in `fys`, and for
                any FY before the account's own first appearance — both
                fall out naturally since taken/paid are 0.0 there, so
                closing stays equal to the carried-forward opening).
      cumulative_interest = running sum of `interest` across every FY from
                the earliest through this one (inclusive) — tracked
                separately since interest is never capitalized; this is the
                account's accumulating interest payable.
      total_payable = closing + cumulative_interest — the FY's "Total
                Payable Amount" (this FY's principal PLUS every FY's
                interest accrued so far, not just this FY's own interest).

    `fys` is the CONTIGUOUS ascending range of financial years from the
    earliest FY with any recorded activity (taken/paid) across ALL accounts
    through the FY containing `as_of` (today by default) — independent of
    `include_interest` and independent of per-FY activity. This is
    deliberate: a FY in which NO account had any transaction, but in which a
    nonzero balance was still carried forward from a prior FY, must still
    appear (with `taken=0, paid=0, interest=0` and `opening == closing`) so
    the report never silently skips a dormant year's outstanding liability.
    (An earlier version derived `fys` from the union of active FYs — plus,
    only when `include_interest` was True, whatever FYs the interest
    engine's month-boundary walk happened to surface — which meant a
    transaction-free middle year could vanish from the report entirely when
    `include_interest=0`, and the FY coverage could silently differ
    depending on the interest toggle. Fixed 2026-08-06.) Every account's
    `fys` map is filled for EVERY key in this contiguous list (zero-filled
    where the account had no activity that year) so the caller can render a
    rectangular matrix with no null-checks — the existing per-account
    roll-forward loop already carries `opening`/`closing` correctly through
    a gap year unchanged; only the FY-range construction changed.

    Returns
    -------
    {'fys': [...], 'rows': [...], 'totals': {...}, 'missing_rate_accounts': [...]}
        rows[i] = {'account': str, 'rate': float | None, 'fys': {fy_label:
            {opening, taken, paid, interest, closing, cumulative_interest,
            total_payable}}, 'closing': float, 'total_payable': float}
            (`closing` = the final, most-recent FY's closing PRINCIPAL — the
            account's current outstanding principal, interest excluded;
            `total_payable` = that same `closing` plus the account's final
            cumulative interest — the account's true current outstanding
            liability). `rate` is the account's SINGLE CURRENT annual
            interest rate (percent) from `borrowing_rate` — `None` when no
            rate is configured for that account (never coerced to 0.0, so
            the caller can distinguish "0% configured" from "not
            configured"). Always populated (independent of
            `include_interest`) — worth showing even in the no-interest
            view. Because `borrowing_rate` has no effective dating (one row
            per account, no date range), this single current rate is applied
            uniformly across every FY shown for that account — it is NOT a
            per-FY value and is therefore never placed inside
            `rows[i]['fys'][fy_label]` or `totals[fy_label]`.
        totals[fy_label] = {opening, taken, paid, interest, closing,
            cumulative_interest, total_payable} — column-wise sums across
            every account, same shape as a row's per-FY entry.
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

    # Rate is always looked up (worth showing even in the no-interest view) —
    # only `missing_rate_accounts` (the "rate not configured" warning tied to
    # the interest calculation) stays gated on include_interest, unchanged.
    rate_map = compute_borrowing_rate_map(conn, accounts)
    missing_rate_accounts = (
        sorted(a for a in accounts if a not in rate_map) if include_interest else []
    )

    per_account_taken: dict[str, dict[int, float]] = {}
    per_account_paid: dict[str, dict[int, float]] = {}
    per_account_interest: dict[str, dict[int, float]] = {}
    earliest_activity_fy: int | None = None
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
        acct_fys = set(taken_by_fy) | set(paid_by_fy)
        if acct_fys:
            acct_min_fy = min(acct_fys)
            if earliest_activity_fy is None or acct_min_fy < earliest_activity_fy:
                earliest_activity_fy = acct_min_fy

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
        per_account_interest[acct] = interest_by_fy

    # FY enumeration is CONTIGUOUS and independent of include_interest / of
    # which FYs happen to have activity — the earliest FY with ANY recorded
    # activity (taken/paid) across all accounts through the FY containing
    # `as_of_d`, no gaps. A dormant middle year (no transactions that FY, but
    # a nonzero balance carried forward from a prior FY) must still appear —
    # see the docstring above for the full rationale. This is deliberately
    # NOT derived from `interest_by_fy` at all (that used to be the ONLY
    # thing that could surface a dormant year, and only when
    # include_interest=True — the exact bug this fixes).
    if earliest_activity_fy is None:
        fy_starts_sorted: list[int] = []
    else:
        as_of_fy = _fy_start_year(as_of_d)
        latest_fy = max(earliest_activity_fy, as_of_fy)
        fy_starts_sorted = list(range(earliest_activity_fy, latest_fy + 1))

    fys = [_fy_key(fy) for fy in fy_starts_sorted]

    totals_by_fy: dict[str, dict] = {
        fy_label: {
            'opening': 0.0, 'taken': 0.0, 'paid': 0.0, 'interest': 0.0,
            'closing': 0.0, 'cumulative_interest': 0.0, 'total_payable': 0.0,
        }
        for fy_label in fys
    }

    rows: list[dict] = []
    for acct in in_scope_accounts:
        taken_by_fy = per_account_taken[acct]
        paid_by_fy = per_account_paid[acct]
        interest_by_fy = per_account_interest[acct]

        acct_fys: dict[str, dict] = {}
        opening = 0.0
        cumulative_interest = 0.0
        for fy in fy_starts_sorted:
            fy_label = _fy_key(fy)
            taken = round(taken_by_fy.get(fy, 0.0), 2)
            paid = round(paid_by_fy.get(fy, 0.0), 2)
            interest = round(interest_by_fy.get(fy, 0.0), 2)
            opening_r = round(opening, 2)
            # PRINCIPAL ONLY — interest is never folded into closing (2026-08-06
            # model change: interest is never carried forward).
            closing = round(opening_r + taken - paid, 2)
            cumulative_interest = round(cumulative_interest + interest, 2)
            total_payable = round(closing + cumulative_interest, 2)
            acct_fys[fy_label] = {
                'opening': opening_r,
                'taken': taken,
                'paid': paid,
                'interest': interest,
                'closing': closing,
                'cumulative_interest': cumulative_interest,
                'total_payable': total_payable,
            }
            t = totals_by_fy[fy_label]
            t['opening'] += opening_r
            t['taken'] += taken
            t['paid'] += paid
            t['interest'] += interest
            t['closing'] += closing
            t['cumulative_interest'] += cumulative_interest
            t['total_payable'] += total_payable
            opening = closing

        rows.append({
            'account': acct,
            # Single CURRENT borrowing_rate row per account (no effective
            # dating — see compute_borrowing_rate_map) applied across every
            # FY shown. float percent, or None when no rate is configured for
            # this account — callers must distinguish "0% configured" (0.0)
            # from "not configured" (None), so this is never coerced to 0.0.
            'rate': rate_map.get(acct),
            'fys': acct_fys,
            'closing': round(opening, 2),
            'total_payable': round(opening + cumulative_interest, 2),
        })

    for fy_label in totals_by_fy:
        for key in totals_by_fy[fy_label]:
            totals_by_fy[fy_label][key] = round(totals_by_fy[fy_label][key], 2)

    return {
        'fys': fys,
        'rows': rows,
        'totals': totals_by_fy,
        'missing_rate_accounts': missing_rate_accounts,
    }
