"""
stocks_expiry — shared Stock Expiry computation.

Public surface
--------------
compute_stocks_expiry(conn, branch=None, expires_before_months=None, brand=None) -> dict
    conn                   : open psycopg2 connection (caller owns open/close)
    branch                 : str | None — EXACT match on `branch` (blank/None = no filter)
    expires_before_months  : str | int | None — one of '3'/'6'/'9'/'12' (or the int
                              3/6/9/12); anything else (including 'all'/absent) means
                              no cutoff filter
    brand                  : str | None — case-insensitive substring match on `brand`
                              (blank/None = no filter)
    returns                : {
        'rows': [ {brand, technical, packing_size, packing_configuration,
                   packing_display, available_nos, conversion_factor,
                   available_cases, available_qty, branch,
                   special_packing_mention, entry_date, expiry_date}, ... ],
        'branch_filter': str | None,
        'brand_filter': str | None,
        'cutoff_date': 'YYYY-MM-DD' | None,
    }

Shared by
---------
  lambda/api/handler.py — GET /stocks/expiry (drops the filter/cutoff keys, no
                           filters ever applied — returns `rows` as a bare JSON
                           array, cached in Redis) and GET /stocks/expiry/pdf
                           (uses every key of the returned dict; no Redis cache,
                           always computed fresh).

Un-aggregated snapshot_stock rows — one row per distinct expiry_date (no
rate/valuation; those live only in the aggregated /stocks/current view).
"""

from __future__ import annotations

import calendar
from datetime import date as _date, datetime, timezone


def _add_months(d, months: int):
    """Add `months` calendar months to date `d`, clamping the day to the
    target month's length (e.g. 31-Jan + 1 month -> 28/29-Feb)."""
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return d.replace(year=year, month=month, day=day)


def _packing_display(packing_size_num: float, packing_config: str) -> str:
    ps = int(packing_size_num) if packing_size_num % 1 == 0 else packing_size_num
    return f"{ps} {packing_config}"


def compute_stocks_expiry(conn, branch=None, expires_before_months=None, brand=None) -> dict:
    branch_filter = (branch or '').strip()
    brand_filter = (brand or '').strip()
    months_raw = str(expires_before_months if expires_before_months is not None else '').strip().lower()

    cutoff_date = None
    if months_raw in ('3', '6', '9', '12'):
        today = datetime.now(timezone.utc).date()
        cutoff_date = _add_months(today, int(months_raw))

    query = """
        SELECT
            brand, technical, packing_size, packing_configuration,
            available_nos, conversion_factor, available_cases, available_qty,
            branch, special_packing_mention, entry_date, expiry_date
        FROM snapshot_stock
        WHERE out_z IS NULL
    """
    query_params: list = []
    if brand_filter:
        query += " AND brand ILIKE %s"
        query_params.append(f'%{brand_filter}%')
    if branch_filter:
        query += " AND branch = %s"
        query_params.append(branch_filter)
    if cutoff_date is not None:
        query += " AND expiry_date IS NOT NULL AND expiry_date <= %s"
        query_params.append(cutoff_date)
    query += " ORDER BY expiry_date ASC NULLS LAST, brand, technical, branch"

    with conn.cursor() as cur:
        cur.execute(query, query_params)
        col_names = [d[0] for d in cur.description]
        raw_rows = cur.fetchall()

    rows = []
    for raw in raw_rows:
        row = dict(zip(col_names, raw))
        packing_size_num = float(row['packing_size'] or 0)
        packing_config = row['packing_configuration'] or ''
        rows.append({
            'brand': row['brand'],
            'technical': row['technical'],
            'packing_size': packing_size_num,
            'packing_configuration': packing_config,
            'packing_display': _packing_display(packing_size_num, packing_config),
            'available_nos': float(row['available_nos'] or 0),
            'conversion_factor': float(row['conversion_factor'] or 0),
            'available_cases': float(row['available_cases'] or 0),
            'available_qty': float(row['available_qty'] or 0),
            'branch': row['branch'],
            'special_packing_mention': row['special_packing_mention'],
            'entry_date': row['entry_date'].isoformat() if row['entry_date'] else None,
            'expiry_date': row['expiry_date'].isoformat() if row['expiry_date'] else None,
        })

    return {
        'rows': rows,
        'branch_filter': branch_filter or None,
        'brand_filter': brand_filter or None,
        'cutoff_date': cutoff_date.isoformat() if cutoff_date else None,
    }
