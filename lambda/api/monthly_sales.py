"""
monthly_sales — shared monthly-sales computation (no PDF dependencies).

Public surface
--------------
compute_monthly_sales(conn, month_str) -> dict
    conn      : open psycopg2 connection (caller owns open/close)
    month_str : YYYY-MM string (must be valid; caller must normalise before calling)
    returns   : payload dict matching the GET /reports/monthly-sales JSON contract —
                month, month_label, fy_label, as_on_date, days[], grand_total{},
                analysis{}, unmapped_branches[], projections{}, excess_short{},
                targets_available, annual_position{}, month_only{}, cumulative_as_on{}

This module is imported by:
  - lambda/api/handler.py          (GET /reports/monthly-sales thin wrapper)
  - lambda/alerts_evaluator/       (sales-alert PDF-attachment path)

No PDF library is imported here; the api Lambda remains free of reportlab.
Both copies (api/ and alerts_evaluator/) must remain byte-identical.
Keep lambda/alerts_evaluator/monthly_sales.py in sync whenever this file changes.
"""

from __future__ import annotations

import calendar as _calendar
import logging
from datetime import date, datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# Branch → state mapping (authoritative; must match alerts_eval._query_aggregate_metrics).
_BRANCH_STATE: dict[str, str] = {
    'Guntur C & F': 'andhra',
    'Auto Nagar':   'telangana',
}

# IST = UTC+5:30
_IST_OFFSET = timedelta(hours=5, minutes=30)


def _today_ist() -> date:
    """Return today's date in IST (UTC+5:30)."""
    return (datetime.now(timezone.utc) + _IST_OFFSET).date()


def _pack(a: float, t: float) -> dict:
    """Round a/t to 2dp and build the standard {andhra, telangana, total} shape."""
    a = round(a, 2)
    t = round(t, 2)
    return {'andhra': a, 'telangana': t, 'total': round(a + t, 2)}


def _growth_pct(cur_val: float, prev_val: float):
    """Percentage growth of cur_val vs prev_val, 2dp; None if prev_val == 0."""
    if prev_val == 0:
        return None
    return round((cur_val - prev_val) / prev_val * 100, 2)


def _net_sales_by_state(cur, start_date: date, end_date: date, unmapped_branches: set) -> tuple[float, float]:
    """
    Run the branch-grouped net-sales query over an inclusive date range and return
    (andhra_rupees, telangana_rupees), mapping branches via _BRANCH_STATE. Branches
    with no state mapping are added to the shared `unmapped_branches` set (and
    excluded from the totals). Returns (0.0, 0.0) without querying if the range
    is empty (end_date < start_date).
    """
    if end_date < start_date:
        return 0.0, 0.0

    cur.execute("""
        SELECT
            branch,
            ROUND(
                COALESCE(SUM(av) FILTER (WHERE sales_return = 'N'), 0) -
                COALESCE(SUM(av) FILTER (WHERE sales_return = 'Y'), 0)
            , 2) AS net_sales
        FROM sales
        WHERE out_z IS NULL
          AND purchase_date BETWEEN %(start_date)s AND %(end_date)s
          AND UPPER(party) IN (SELECT UPPER(customer_name) FROM customer_details)
          AND party NOT ILIKE '%%iravi%%'
        GROUP BY branch
    """, {'start_date': start_date, 'end_date': end_date})

    a, t = 0.0, 0.0
    for branch, net_sales in cur.fetchall():
        state = _BRANCH_STATE.get(branch)
        if state is None:
            unmapped_branches.add(branch)
            continue
        if state == 'andhra':
            a = round(a + float(net_sales), 2)
        else:
            t = round(t + float(net_sales), 2)
    return a, t


def compute_monthly_sales(conn, month_str: str) -> dict:
    """
    Compute state-wise net customer sales for one calendar month, plus targets
    and year-over-year / annual comparison data.

    Parameters
    ----------
    conn      : open psycopg2 connection (caller owns lifecycle — do not close here)
    month_str : YYYY-MM string, already validated and normalised by the caller

    Returns
    -------
    dict with keys matching the GET /reports/monthly-sales JSON contract:
      month, month_label, fy_label, as_on_date,
      days (list of {date, andhra, telangana, total}),
      grand_total ({andhra, telangana, total}),
      analysis ({prev_month_label, up_to_prev_month, as_on_date}),
      unmapped_branches (sorted list of branch strings),
      projections ({andhra, telangana, total}) — monthly target, 0 if none,
      excess_short ({andhra, telangana, total}) — grand_total - projections,
      targets_available (bool),
      annual_position ({prev_fy_label, cur_fy_label, prev_month_label_full,
                         actual_sales_prev_fy, annual_target_cur_fy, upto_prev_month}),
      month_only ({month_name, prev_fy, cur_fy, diff}),
      cumulative_as_on ({month_abbr, prev_fy_label, cur_fy_label,
                          prev_fy_upto, cur_fy_as_on, diff})

    Values are raw rupees (float, 2 dp).  The UI converts to lakhs;
    the evaluator's PDF renderer converts internally.
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

    # Previous-month info (for analysis block)
    prev_mon = mon - 1 if mon > 1 else 12
    prev_yr  = year if mon > 1 else year - 1
    prev_month_label = date(prev_yr, prev_mon, 1).strftime('%b')   # e.g. "May"

    # FY start and prev-month-end for the analysis.up_to_prev_month range
    fy_start            = date(fy_start_year, 4, 1)
    prev_month_end_date = month_start - timedelta(days=1)

    # ── DB queries ──────────────────────────────────────────────────────────────
    with conn.cursor() as cur:
        # Main query: net sales aggregated by (purchase_date, branch) for the month
        cur.execute("""
            SELECT
                purchase_date,
                branch,
                ROUND(
                    COALESCE(SUM(av) FILTER (WHERE sales_return = 'N'), 0) -
                    COALESCE(SUM(av) FILTER (WHERE sales_return = 'Y'), 0)
                , 2) AS net_sales
            FROM sales
            WHERE out_z IS NULL
              AND purchase_date BETWEEN %(month_start)s AND %(month_end)s
              AND UPPER(party) IN (SELECT UPPER(customer_name) FROM customer_details)
              AND party NOT ILIKE '%%iravi%%'
            GROUP BY purchase_date, branch
            ORDER BY purchase_date
        """, {'month_start': month_start, 'month_end': month_end})
        db_rows = cur.fetchall()

        # FY-to-prev-month query for analysis.up_to_prev_month.
        # If the selected month is April, prev_month_end_date < fy_start → zeros.
        if prev_month_end_date >= fy_start:
            cur.execute("""
                SELECT
                    branch,
                    ROUND(
                        COALESCE(SUM(av) FILTER (WHERE sales_return = 'N'), 0) -
                        COALESCE(SUM(av) FILTER (WHERE sales_return = 'Y'), 0)
                    , 2) AS net_sales
                FROM sales
                WHERE out_z IS NULL
                  AND purchase_date BETWEEN %(fy_start)s AND %(prev_end)s
                  AND UPPER(party) IN (SELECT UPPER(customer_name) FROM customer_details)
                  AND party NOT ILIKE '%%iravi%%'
                GROUP BY branch
            """, {'fy_start': fy_start, 'prev_end': prev_month_end_date})
            utp_rows = cur.fetchall()
        else:
            utp_rows = []

    # ── Build daily map (all zeros; SQL provides only days with activity) ────────
    days_map: dict = {
        date(year, mon, d).isoformat(): {'andhra': 0.0, 'telangana': 0.0}
        for d in range(1, last_day + 1)
    }
    unmapped_branches: set = set()

    for purchase_date, branch, net_sales in db_rows:
        state = _BRANCH_STATE.get(branch)
        if state is None:
            unmapped_branches.add(branch)
            logger.warning('monthly_sales: unmapped branch %r — excluded from totals', branch)
            continue
        date_str = purchase_date.isoformat()
        if date_str in days_map:
            days_map[date_str][state] = round(days_map[date_str][state] + float(net_sales), 2)

    # Ordered days list (all calendar days of the month, including future ones)
    days = []
    for d in range(1, last_day + 1):
        date_str = date(year, mon, d).isoformat()
        entry = days_map[date_str]
        a, t = entry['andhra'], entry['telangana']
        days.append({
            'date':      date_str,
            'andhra':    a,
            'telangana': t,
            'total':     round(a + t, 2),
        })

    # grand_total: sum across all days (future days are 0.0)
    grand_a = round(sum(day['andhra']    for day in days), 2)
    grand_t = round(sum(day['telangana'] for day in days), 2)
    grand_total = {
        'andhra':    grand_a,
        'telangana': grand_t,
        'total':     round(grand_a + grand_t, 2),
    }

    # analysis.up_to_prev_month: FY-start through end of previous month
    utp_a, utp_t = 0.0, 0.0
    for branch, net_sales in utp_rows:
        state = _BRANCH_STATE.get(branch)
        if state is None:
            unmapped_branches.add(branch)
            continue
        if state == 'andhra':
            utp_a = round(utp_a + float(net_sales), 2)
        else:
            utp_t = round(utp_t + float(net_sales), 2)

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
        pf_month_a, pf_month_t = _net_sales_by_state(
            cur, prev_month_start_py, prev_month_end_py, unmapped_branches)
        pf_utp_a, pf_utp_t = _net_sales_by_state(
            cur, prev_fy_start, prev_fy_utp_end, unmapped_branches)
        pf_cum_a, pf_cum_t = _net_sales_by_state(
            cur, prev_fy_start, prev_fy_cum_end, unmapped_branches)
        pf_annual_a, pf_annual_t = _net_sales_by_state(
            cur, prev_fy_start, prev_fy_annual_end, unmapped_branches)

    # ── Targets (monthly_sale_targets table — may not exist yet) ─────────────────
    targets_available = False
    proj_a = proj_t = 0.0
    annual_target_a = annual_target_t = 0.0

    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.monthly_sale_targets')")
        table_exists = cur.fetchone()[0] is not None

        if table_exists:
            targets_available = True

            cur.execute("""
                SELECT state, target_lakhs FROM monthly_sale_targets
                WHERE out_z IS NULL AND month = %s AND yr = %s AND state IN ('AP', 'TG')
            """, (mon, year))
            for state, target_lakhs in cur.fetchall():
                rupees = round(float(target_lakhs) * 100_000, 2)
                if state == 'AP':
                    proj_a = rupees
                elif state == 'TG':
                    proj_t = rupees

            cur.execute("""
                SELECT state, COALESCE(SUM(target_lakhs), 0) FROM monthly_sale_targets
                WHERE out_z IS NULL AND state IN ('AP', 'TG')
                  AND ((yr = %s AND month BETWEEN 4 AND 12) OR (yr = %s AND month BETWEEN 1 AND 3))
                GROUP BY state
            """, (fy_start_year, fy_start_year + 1))
            for state, total_lakhs in cur.fetchall():
                rupees = round(float(total_lakhs) * 100_000, 2)
                if state == 'AP':
                    annual_target_a = rupees
                elif state == 'TG':
                    annual_target_t = rupees

    # ── Assemble new comparison blocks ────────────────────────────────────────────
    projections  = _pack(proj_a, proj_t)
    excess_short = _pack(grand_a - proj_a, grand_t - proj_t)

    prev_fy_label = f'{fy_start_year - 1}-{str(fy_start_year)[2:]}'
    prev_month_label_full = date(prev_yr, prev_mon, 1).strftime('%B').upper()

    upto_prev_month_prev_fy = _pack(pf_utp_a, pf_utp_t)
    upto_prev_month_cur_fy  = _pack(utp_a, utp_t)
    upto_prev_month_diff    = _pack(utp_a - pf_utp_a, utp_t - pf_utp_t)
    upto_prev_month_growth  = {
        'andhra':    _growth_pct(utp_a, pf_utp_a),
        'telangana': _growth_pct(utp_t, pf_utp_t),
        'total':     _growth_pct(utp_a + utp_t, pf_utp_a + pf_utp_t),
    }

    annual_position = {
        'prev_fy_label':         prev_fy_label,
        'cur_fy_label':          fy_label,
        'prev_month_label_full': prev_month_label_full,
        'actual_sales_prev_fy':  _pack(pf_annual_a, pf_annual_t),
        'annual_target_cur_fy':  _pack(annual_target_a, annual_target_t),
        'upto_prev_month': {
            'prev_fy':    upto_prev_month_prev_fy,
            'cur_fy':     upto_prev_month_cur_fy,
            'diff':       upto_prev_month_diff,
            'growth_pct': upto_prev_month_growth,
        },
    }

    month_only = {
        'month_name': date(year, mon, 1).strftime('%B').upper(),
        'prev_fy':    _pack(pf_month_a, pf_month_t),
        'cur_fy':     dict(grand_total),
        'diff':       _pack(grand_a - pf_month_a, grand_t - pf_month_t),
    }

    cur_as_on_a = utp_a + grand_a
    cur_as_on_t = utp_t + grand_t
    cur_fy_as_on = _pack(cur_as_on_a, cur_as_on_t)
    prev_fy_upto = _pack(pf_cum_a, pf_cum_t)

    cumulative_as_on = {
        'month_abbr':    date(year, mon, 1).strftime('%b').upper(),
        'prev_fy_label': prev_fy_label,
        'cur_fy_label':  fy_label,
        'prev_fy_upto':  prev_fy_upto,
        'cur_fy_as_on':  cur_fy_as_on,
        'diff':          _pack(cur_as_on_a - pf_cum_a, cur_as_on_t - pf_cum_t),
    }

    return {
        'month':       month_str,
        'month_label': month_label,
        'fy_label':    fy_label,
        'as_on_date':  as_on_date.isoformat(),
        'days':        days,
        'grand_total': grand_total,
        'analysis': {
            'prev_month_label': prev_month_label,
            'up_to_prev_month': {
                'andhra':    utp_a,
                'telangana': utp_t,
                'total':     round(utp_a + utp_t, 2),
            },
            'as_on_date': {
                'andhra':    grand_a,
                'telangana': grand_t,
                'total':     round(grand_a + grand_t, 2),
            },
        },
        'unmapped_branches': sorted(unmapped_branches),
        'projections':        projections,
        'excess_short':       excess_short,
        'targets_available':  targets_available,
        'annual_position':    annual_position,
        'month_only':         month_only,
        'cumulative_as_on':   cumulative_as_on,
    }
