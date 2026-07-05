"""
monthly_sales — shared monthly-sales computation (no PDF dependencies).

Public surface
--------------
compute_monthly_sales(conn, month_str) -> dict
    conn      : open psycopg2 connection (caller owns open/close)
    month_str : YYYY-MM string (must be valid; caller must normalise before calling)
    returns   : payload dict matching the GET /reports/monthly-sales JSON contract

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


def compute_monthly_sales(conn, month_str: str) -> dict:
    """
    Compute state-wise net customer sales for one calendar month.

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
      unmapped_branches (sorted list of branch strings)

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
    }
