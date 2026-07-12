"""
monthly_collection — shared monthly-collection computation (no PDF dependencies).

Public surface
--------------
compute_monthly_collection(conn, month_str) -> dict
    conn      : open psycopg2 connection (caller owns open/close)
    month_str : YYYY-MM string (must be valid; caller must normalise before calling)
    returns   : payload dict matching the GET /reports/monthly-collection JSON contract —
                month, month_label, fy_label, as_on_date, days[], grand_total{},
                projections{}, excess_short{}, targets_available, annual_position{},
                month_only{}, cumulative_as_on{}, unmapped_collections_total

This module mirrors lambda/api/monthly_sales.py structure exactly, but works over
FOUR states (AP, TS, TN, OR) instead of two, and sources collections from
customer_ledger (Bank/Cash Receipt credits) instead of net sales.

No PDF library is imported here; the api Lambda remains free of reportlab.
There is no alerts_evaluator twin for this module (collection has no alert type
yet) — unlike monthly_sales.py, this file is NOT duplicated elsewhere.
"""

from __future__ import annotations

import calendar as _calendar
import logging
from datetime import date, datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# customer_details.state code -> report bucket key (authoritative; note the TG->TS rename).
_STATE_TO_BUCKET: dict[str, str] = {
    'AP': 'ap',
    'TG': 'ts',
    'TN': 'tn',
    'OR': 'or',
}

# monthly_collection_targets.state code -> report bucket key (targets table already uses 'TS').
_TARGET_STATE_TO_BUCKET: dict[str, str] = {
    'AP': 'ap',
    'TS': 'ts',
    'TN': 'tn',
    'OR': 'or',
}

# IST = UTC+5:30
_IST_OFFSET = timedelta(hours=5, minutes=30)


def _today_ist() -> date:
    """Return today's date in IST (UTC+5:30)."""
    return (datetime.now(timezone.utc) + _IST_OFFSET).date()


def _pack4(ap: float, ts: float, tn: float, or_: float) -> dict:
    """Round ap/ts/tn/or to 2dp and build the standard {ap, ts, tn, or, total} shape."""
    ap = round(ap, 2)
    ts = round(ts, 2)
    tn = round(tn, 2)
    or_ = round(or_, 2)
    return {'ap': ap, 'ts': ts, 'tn': tn, 'or': or_, 'total': round(ap + ts + tn + or_, 2)}


def _growth_pct(cur_val: float, prev_val: float):
    """Percentage growth of cur_val vs prev_val, 2dp; None if prev_val == 0."""
    if prev_val == 0:
        return None
    return round((cur_val - prev_val) / prev_val * 100, 2)


def _collections_by_state(cur, start_date: date, end_date: date) -> tuple[dict, float]:
    """
    Run the state-grouped collections query over an inclusive date range and return
    ({ap, ts, tn, or}, unmapped_rupees). Rows whose customer_details.state does not
    map to a known bucket (NULL or unrecognized code) are excluded from the buckets
    and accumulated into unmapped_rupees. Returns ({0,0,0,0}, 0.0) without querying
    if the range is empty (end_date < start_date).
    """
    buckets = {'ap': 0.0, 'ts': 0.0, 'tn': 0.0, 'or': 0.0}
    if end_date < start_date:
        return buckets, 0.0

    cur.execute("""
        SELECT cd.state, ROUND(COALESCE(SUM(cl.amount),0),2)
        FROM customer_ledger cl
        LEFT JOIN customer_details cd
          ON UPPER(cd.customer_name) = UPPER(cl.account_name) AND cd.out_z IS NULL
        WHERE cl.out_z IS NULL
          AND cl.category = 'Cr'
          AND cl.sub_category IN ('Bank Receipt','Cash Receipt')
          AND cl.account_name NOT ILIKE '%%iravi%%'
          AND cl.transaction_date BETWEEN %(s)s AND %(e)s
        GROUP BY cd.state
    """, {'s': start_date, 'e': end_date})

    unmapped_rupees = 0.0
    for state, amount in cur.fetchall():
        amount = float(amount)
        bucket = _STATE_TO_BUCKET.get(state)
        if bucket is None:
            unmapped_rupees = round(unmapped_rupees + amount, 2)
            continue
        buckets[bucket] = round(buckets[bucket] + amount, 2)
    return buckets, unmapped_rupees


def compute_monthly_collection(conn, month_str: str) -> dict:
    """
    Compute state-wise collections (Bank/Cash Receipts) for one calendar month,
    plus targets and year-over-year / annual comparison data.

    Parameters
    ----------
    conn      : open psycopg2 connection (caller owns lifecycle — do not close here)
    month_str : YYYY-MM string, already validated and normalised by the caller

    Returns
    -------
    dict with keys matching the GET /reports/monthly-collection JSON contract:
      month, month_label, fy_label, as_on_date,
      days (list of {date, ap, ts, tn, or, total}),
      grand_total ({ap, ts, tn, or, total}),
      projections ({ap, ts, tn, or, total}) — monthly target, 0 if none,
      excess_short ({ap, ts, tn, or, total}) — grand_total - projections,
      targets_available (bool),
      annual_position ({prev_fy_label, cur_fy_label, prev_month_label_full,
                         actual_collections_prev_fy, annual_target_cur_fy, upto_prev_month}),
      month_only ({month_name, prev_fy, cur_fy, diff}),
      cumulative_as_on ({month_abbr, prev_fy_label, cur_fy_label,
                          prev_fy_upto, cur_fy_as_on, diff}),
      unmapped_collections_total (raw rupees; collections for the as-on month range
                                   whose customer has no active customer_details row
                                   or a NULL/unrecognized state)

    Values are raw rupees (float, 2 dp). The UI converts to lakhs.
    """
    parsed = datetime.strptime(month_str, '%Y-%m')
    year, mon = parsed.year, parsed.month

    today_ist = _today_ist()

    last_day    = _calendar.monthrange(year, mon)[1]
    month_start = date(year, mon, 1)
    month_end   = date(year, mon, last_day)

    # as_on_date = min(today IST, last calendar day of the selected month)
    as_on_date = min(today_ist, month_end)

    # FY label: "YYYY-YY" for the FY containing the selected month (Apr → Mar).
    fy_start_year = year if mon >= 4 else year - 1
    fy_label = f'{fy_start_year}-{str(fy_start_year + 1)[2:]}'

    # month_label: e.g. "JUNE 2026"
    month_label = date(year, mon, 1).strftime('%B %Y').upper()

    # Previous-month info
    prev_mon = mon - 1 if mon > 1 else 12
    prev_yr  = year if mon > 1 else year - 1

    # FY start and prev-month-end for the up_to_prev_month range
    fy_start            = date(fy_start_year, 4, 1)
    prev_month_end_date = month_start - timedelta(days=1)

    # ── DB queries ──────────────────────────────────────────────────────────────
    with conn.cursor() as cur:
        # Main query: collections aggregated by (transaction_date, state) for the month
        cur.execute("""
            SELECT
                cl.transaction_date,
                cd.state,
                ROUND(COALESCE(SUM(cl.amount),0),2) AS collections
            FROM customer_ledger cl
            LEFT JOIN customer_details cd
              ON UPPER(cd.customer_name) = UPPER(cl.account_name) AND cd.out_z IS NULL
            WHERE cl.out_z IS NULL
              AND cl.category = 'Cr'
              AND cl.sub_category IN ('Bank Receipt','Cash Receipt')
              AND cl.account_name NOT ILIKE '%%iravi%%'
              AND cl.transaction_date BETWEEN %(month_start)s AND %(month_end)s
            GROUP BY cl.transaction_date, cd.state
            ORDER BY cl.transaction_date
        """, {'month_start': month_start, 'month_end': month_end})
        db_rows = cur.fetchall()

        # FY-to-prev-month query for annual_position.upto_prev_month.cur_fy.
        # If the selected month is April, prev_month_end_date < fy_start → zeros.
        if prev_month_end_date >= fy_start:
            cur.execute("""
                SELECT cd.state, ROUND(COALESCE(SUM(cl.amount),0),2)
                FROM customer_ledger cl
                LEFT JOIN customer_details cd
                  ON UPPER(cd.customer_name) = UPPER(cl.account_name) AND cd.out_z IS NULL
                WHERE cl.out_z IS NULL
                  AND cl.category = 'Cr'
                  AND cl.sub_category IN ('Bank Receipt','Cash Receipt')
                  AND cl.account_name NOT ILIKE '%%iravi%%'
                  AND cl.transaction_date BETWEEN %(fy_start)s AND %(prev_end)s
                GROUP BY cd.state
            """, {'fy_start': fy_start, 'prev_end': prev_month_end_date})
            utp_rows = cur.fetchall()
        else:
            utp_rows = []

    # ── Build daily map (all zeros; SQL provides only days with activity) ────────
    days_map: dict = {
        date(year, mon, d).isoformat(): {'ap': 0.0, 'ts': 0.0, 'tn': 0.0, 'or': 0.0}
        for d in range(1, last_day + 1)
    }
    unmapped_collections_total = 0.0

    for transaction_date, state, amount in db_rows:
        amount = float(amount)
        bucket = _STATE_TO_BUCKET.get(state)
        if bucket is None:
            unmapped_collections_total = round(unmapped_collections_total + amount, 2)
            logger.warning(
                'monthly_collection: unmapped state %r on %s — excluded from totals',
                state, transaction_date,
            )
            continue
        date_str = transaction_date.isoformat()
        if date_str in days_map:
            days_map[date_str][bucket] = round(days_map[date_str][bucket] + amount, 2)

    # Ordered days list (all calendar days of the month, including future ones)
    days = []
    for d in range(1, last_day + 1):
        date_str = date(year, mon, d).isoformat()
        entry = days_map[date_str]
        ap, ts, tn, or_ = entry['ap'], entry['ts'], entry['tn'], entry['or']
        days.append({
            'date':  date_str,
            'ap':    ap,
            'ts':    ts,
            'tn':    tn,
            'or':    or_,
            'total': round(ap + ts + tn + or_, 2),
        })

    # grand_total: sum across all days (future days are 0.0)
    grand_ap = round(sum(day['ap'] for day in days), 2)
    grand_ts = round(sum(day['ts'] for day in days), 2)
    grand_tn = round(sum(day['tn'] for day in days), 2)
    grand_or = round(sum(day['or'] for day in days), 2)
    grand_total = {
        'ap':    grand_ap,
        'ts':    grand_ts,
        'tn':    grand_tn,
        'or':    grand_or,
        'total': round(grand_ap + grand_ts + grand_tn + grand_or, 2),
    }

    # up_to_prev_month (current FY, FY-start through end of previous month)
    utp = {'ap': 0.0, 'ts': 0.0, 'tn': 0.0, 'or': 0.0}
    for state, amount in utp_rows:
        bucket = _STATE_TO_BUCKET.get(state)
        if bucket is None:
            continue
        utp[bucket] = round(utp[bucket] + float(amount), 2)

    # ── Year-over-year / annual comparison ranges (prior FY) ─────────────────────
    prev_fy_start_year = fy_start_year - 1
    prev_fy_start      = date(prev_fy_start_year, 4, 1)

    prev_year_last_day  = _calendar.monthrange(year - 1, mon)[1]
    prev_month_start_py = date(year - 1, mon, 1)
    prev_month_end_py   = date(year - 1, mon, prev_year_last_day)   # prevFY same report month end

    prev_fy_utp_end    = date(year - 1, mon, 1) - timedelta(days=1)   # prevFY up-to-prev-month end
    prev_fy_cum_end    = prev_month_end_py                            # prevFY up-to-report-month-end
    prev_fy_annual_end = date(fy_start_year, 3, 31)                   # prevFY full annual actual end

    with conn.cursor() as cur:
        pf_month, _unused = _collections_by_state(cur, prev_month_start_py, prev_month_end_py)
        pf_utp, _unused   = _collections_by_state(cur, prev_fy_start, prev_fy_utp_end)
        pf_cum, _unused   = _collections_by_state(cur, prev_fy_start, prev_fy_cum_end)
        pf_annual, _unused = _collections_by_state(cur, prev_fy_start, prev_fy_annual_end)

    # ── Targets (monthly_collection_targets table — may not exist yet) ───────────
    targets_available = False
    proj = {'ap': 0.0, 'ts': 0.0, 'tn': 0.0, 'or': 0.0}
    annual_target = {'ap': 0.0, 'ts': 0.0, 'tn': 0.0, 'or': 0.0}

    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.monthly_collection_targets')")
        table_exists = cur.fetchone()[0] is not None

        if table_exists:
            targets_available = True

            cur.execute("""
                SELECT state, target_lakhs FROM monthly_collection_targets
                WHERE out_z IS NULL AND month = %s AND yr = %s AND state IN ('AP', 'TS', 'TN', 'OR')
            """, (mon, year))
            for state, target_lakhs in cur.fetchall():
                rupees = round(float(target_lakhs) * 100_000, 2)
                bucket = _TARGET_STATE_TO_BUCKET.get(state)
                if bucket:
                    proj[bucket] = rupees

            cur.execute("""
                SELECT state, COALESCE(SUM(target_lakhs), 0) FROM monthly_collection_targets
                WHERE out_z IS NULL AND state IN ('AP', 'TS', 'TN', 'OR')
                  AND ((yr = %s AND month BETWEEN 4 AND 12) OR (yr = %s AND month BETWEEN 1 AND 3))
                GROUP BY state
            """, (fy_start_year, fy_start_year + 1))
            for state, total_lakhs in cur.fetchall():
                rupees = round(float(total_lakhs) * 100_000, 2)
                bucket = _TARGET_STATE_TO_BUCKET.get(state)
                if bucket:
                    annual_target[bucket] = rupees

    # ── Assemble comparison blocks ────────────────────────────────────────────────
    projections  = _pack4(proj['ap'], proj['ts'], proj['tn'], proj['or'])
    excess_short = _pack4(
        grand_ap - proj['ap'], grand_ts - proj['ts'], grand_tn - proj['tn'], grand_or - proj['or'])

    prev_fy_label = f'{fy_start_year - 1}-{str(fy_start_year)[2:]}'
    prev_month_label_full = date(prev_yr, prev_mon, 1).strftime('%B').upper()

    upto_prev_month_prev_fy = _pack4(pf_utp['ap'], pf_utp['ts'], pf_utp['tn'], pf_utp['or'])
    upto_prev_month_cur_fy  = _pack4(utp['ap'], utp['ts'], utp['tn'], utp['or'])
    upto_prev_month_diff    = _pack4(
        utp['ap'] - pf_utp['ap'], utp['ts'] - pf_utp['ts'], utp['tn'] - pf_utp['tn'], utp['or'] - pf_utp['or'])
    upto_prev_month_growth  = {
        'ap':    _growth_pct(utp['ap'], pf_utp['ap']),
        'ts':    _growth_pct(utp['ts'], pf_utp['ts']),
        'tn':    _growth_pct(utp['tn'], pf_utp['tn']),
        'or':    _growth_pct(utp['or'], pf_utp['or']),
        'total': _growth_pct(
            utp['ap'] + utp['ts'] + utp['tn'] + utp['or'],
            pf_utp['ap'] + pf_utp['ts'] + pf_utp['tn'] + pf_utp['or']),
    }

    annual_position = {
        'prev_fy_label':               prev_fy_label,
        'cur_fy_label':                fy_label,
        'prev_month_label_full':       prev_month_label_full,
        'actual_collections_prev_fy':  _pack4(pf_annual['ap'], pf_annual['ts'], pf_annual['tn'], pf_annual['or']),
        'annual_target_cur_fy':        _pack4(annual_target['ap'], annual_target['ts'], annual_target['tn'], annual_target['or']),
        'upto_prev_month': {
            'prev_fy':    upto_prev_month_prev_fy,
            'cur_fy':     upto_prev_month_cur_fy,
            'diff':       upto_prev_month_diff,
            'growth_pct': upto_prev_month_growth,
        },
    }

    month_only = {
        'month_name': date(year, mon, 1).strftime('%B').upper(),
        'prev_fy':    _pack4(pf_month['ap'], pf_month['ts'], pf_month['tn'], pf_month['or']),
        'cur_fy':     dict(grand_total),
        'diff':       _pack4(
            grand_ap - pf_month['ap'], grand_ts - pf_month['ts'],
            grand_tn - pf_month['tn'], grand_or - pf_month['or']),
    }

    cur_as_on_ap = utp['ap'] + grand_ap
    cur_as_on_ts = utp['ts'] + grand_ts
    cur_as_on_tn = utp['tn'] + grand_tn
    cur_as_on_or = utp['or'] + grand_or
    cur_fy_as_on = _pack4(cur_as_on_ap, cur_as_on_ts, cur_as_on_tn, cur_as_on_or)
    prev_fy_upto = _pack4(pf_cum['ap'], pf_cum['ts'], pf_cum['tn'], pf_cum['or'])

    cumulative_as_on = {
        'month_abbr':    date(year, mon, 1).strftime('%b').upper(),
        'prev_fy_label': prev_fy_label,
        'cur_fy_label':  fy_label,
        'prev_fy_upto':  prev_fy_upto,
        'cur_fy_as_on':  cur_fy_as_on,
        'diff': _pack4(
            cur_as_on_ap - pf_cum['ap'], cur_as_on_ts - pf_cum['ts'],
            cur_as_on_tn - pf_cum['tn'], cur_as_on_or - pf_cum['or']),
    }

    return {
        'month':       month_str,
        'month_label': month_label,
        'fy_label':    fy_label,
        'as_on_date':  as_on_date.isoformat(),
        'days':        days,
        'grand_total': grand_total,
        'projections':        projections,
        'excess_short':       excess_short,
        'targets_available':  targets_available,
        'annual_position':    annual_position,
        'month_only':         month_only,
        'cumulative_as_on':   cumulative_as_on,
        'unmapped_collections_total': unmapped_collections_total,
    }
