"""
Shared alert evaluation for IRAVI Alerts.

Categories
----------
balances     — per-customer outstanding balance evaluation (FIFO aging)
sales        — aggregate net customer sales over time windows
sale_returns — aggregate customer sale returns over time windows

Used by:
  - lambda/api/handler.py               (POST /alerts/{id}/test, GET /alerts/fields)
  - lambda/alerts_evaluator/handler.py  (15-minute evaluator)

Both packages include this file directly (same logic, one source).
Keep lambda/alerts_evaluator/alerts_eval.py in sync whenever this file changes.

Evaluation contract — balances
------------------------------
Per customer (from customer_ledger, out_z IS NULL, excluding IRAVI internal accounts):

  outstanding  = SUM(amount WHERE category='Db') - SUM(amount WHERE category='Cr')
                 Only customers with outstanding > 0 are kept.

  age_days     = FIFO aging:
                 Order the customer's debits oldest→newest.  Apply total credits to
                 the oldest debits first.  The oldest debit still carrying an unpaid
                 remainder defines age = (today - that debit's date) in days.
                 If fully covered the customer is excluded.

  days_since_last_receipt
               = (today - last_receipt_date).days where last_receipt = most recent
                 credit with sub_category IN ('Bank Receipt','Cash Receipt').
                 If the customer has NEVER received a receipt (last_receipt_date is NULL),
                 days_since_last_receipt is treated as effectively infinite — a large
                 sentinel value (10**9) is used so it satisfies any '> threshold' rule.

  last_receipt_amount / last_receipt_date
               = most recent credit whose sub_category IN ('Bank Receipt','Cash Receipt').
                 NULL if none.

Evaluation contract — sales / sale_returns
------------------------------------------
Source: sales table, out_z IS NULL.
Parties restricted to customers: UPPER(party) IN (SELECT UPPER(customer_name) FROM customer_details)
                                  AND party NOT ILIKE '%iravi%'
Date filter on sales.purchase_date within the selected time window.
Branch filter: if alert.branch is set and not 'ALL'/None, adds branch = <alert.branch>.
Money column: av.

sales metric for a window      = SUM(av WHERE sales_return='N') - SUM(av WHERE sales_return='Y')
sale_returns metric for a window = SUM(av WHERE sales_return='Y')
(Rounded to 2 dp; NULL sums treated as 0.)

Time windows (computed in IST relative to run_date = today):
  prev_day      — yesterday (full day)
  prev_week     — the completed calendar week immediately before the current one (Mon–Sun)
  last_month    — the previous calendar month (1st–last)
  prev_quarter  — the previous FISCAL quarter; FY starts April:
                  Q1=Apr–Jun, Q2=Jul–Sep, Q3=Oct–Dec, Q4=Jan–Mar
  fy            — current FY to date: April 1 of the current FY through yesterday

Condition evaluation
--------------------
Each condition is one of:
  field ∈ category-specific field set (see FIELD_CATALOGS)
  op   ∈ {'gt','gte','lt','lte','eq','between'}   (value2 only used for 'between')

Conditions are combined by match_type:
  'all'  → AND (every condition must pass)
  'any'  → OR  (at least one condition must pass)
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# Sentinel used when a customer has never received any Bank/Cash Receipt.
# Effectively infinite — satisfies any 'days_since_last_receipt > N' condition.
_NEVER_PAID_SENTINEL = 10 ** 9

# ── field catalogs ─────────────────────────────────────────────────────────────

_AGG_OPS = ["gt", "gte", "lt", "lte", "eq", "between"]

FIELD_CATALOG = {
    "category": "balances",
    "fields": [
        {
            "key": "amount",
            "label": "Outstanding amount (₹)",
            "type": "currency",
            "ops": ["gt", "gte", "lt", "lte", "between"],
        },
        {
            "key": "age_days",
            "label": "Age (days)",
            "type": "integer",
            "ops": ["gt", "gte", "lt", "lte", "between"],
        },
        {
            "key": "days_since_last_receipt",
            "label": "Days since last receipt",
            "type": "integer",
            "ops": ["gt", "gte", "lt", "lte", "between"],
        },
    ],
    "match_types": ["all", "any"],
    "frequencies": ["daily", "weekly", "monthly"],
}

FIELD_CATALOG_SALES = {
    "category": "sales",
    "fields": [
        {
            "key": "net_sales_prev_day",
            "label": "Net customer sales — previous day (₹)",
            "type": "currency",
            "ops": _AGG_OPS,
        },
        {
            "key": "net_sales_prev_week",
            "label": "Net customer sales — previous week (₹)",
            "type": "currency",
            "ops": _AGG_OPS,
        },
        {
            "key": "net_sales_last_month",
            "label": "Net customer sales — last month (₹)",
            "type": "currency",
            "ops": _AGG_OPS,
        },
        {
            "key": "net_sales_prev_quarter",
            "label": "Net customer sales — previous fiscal quarter (₹)",
            "type": "currency",
            "ops": _AGG_OPS,
        },
        {
            "key": "net_sales_fy",
            "label": "Net customer sales — FY to date (₹)",
            "type": "currency",
            "ops": _AGG_OPS,
        },
        {
            "key": "net_sales_current_month",
            "label": "Net customer sales — current month to date (₹)",
            "type": "currency",
            "ops": _AGG_OPS,
        },
    ],
    "match_types": ["all", "any"],
    "frequencies": ["daily", "weekly", "monthly"],
    "branch_scoped": True,
}

FIELD_CATALOG_SALE_RETURNS = {
    "category": "sale_returns",
    "fields": [
        {
            "key": "sale_returns_prev_day",
            "label": "Customer sale returns — previous day (₹)",
            "type": "currency",
            "ops": _AGG_OPS,
        },
        {
            "key": "sale_returns_prev_week",
            "label": "Customer sale returns — previous week (₹)",
            "type": "currency",
            "ops": _AGG_OPS,
        },
        {
            "key": "sale_returns_last_month",
            "label": "Customer sale returns — last month (₹)",
            "type": "currency",
            "ops": _AGG_OPS,
        },
        {
            "key": "sale_returns_prev_quarter",
            "label": "Customer sale returns — previous fiscal quarter (₹)",
            "type": "currency",
            "ops": _AGG_OPS,
        },
        {
            "key": "sale_returns_fy",
            "label": "Customer sale returns — FY to date (₹)",
            "type": "currency",
            "ops": _AGG_OPS,
        },
        {
            "key": "sale_returns_current_month",
            "label": "Customer sale returns — current month to date (₹)",
            "type": "currency",
            "ops": _AGG_OPS,
        },
    ],
    "match_types": ["all", "any"],
    "frequencies": ["daily", "weekly", "monthly"],
    "branch_scoped": True,
}

FIELD_CATALOG_CUSTOMER_BALANCES_FY = {
    "category":    "customer_balances_fy",
    "fields":      [],
    "match_types": ["all", "any"],
    "frequencies": ["daily", "weekly", "monthly"],
}

FIELD_CATALOG_SUPPLIER_BALANCES_FY = {
    "category":    "supplier_balances_fy",
    "fields":      [],
    "match_types": ["all", "any"],
    "frequencies": ["daily", "weekly", "monthly"],
}

FIELD_CATALOG_MONTHLY_COLLECTION = {
    "category":    "monthly_collection",
    "fields":      [],
    "match_types": ["all", "any"],
    "frequencies": ["daily", "weekly", "monthly"],
}

# Master catalog lookup by category
FIELD_CATALOGS: dict[str, dict] = {
    "balances":             FIELD_CATALOG,
    "sales":                FIELD_CATALOG_SALES,
    "sale_returns":         FIELD_CATALOG_SALE_RETURNS,
    "customer_balances_fy": FIELD_CATALOG_CUSTOMER_BALANCES_FY,
    "supplier_balances_fy": FIELD_CATALOG_SUPPLIER_BALANCES_FY,
    "monthly_collection":   FIELD_CATALOG_MONTHLY_COLLECTION,
}

_VALID_CATEGORIES: set[str] = set(FIELD_CATALOGS.keys())

# Per-category valid field key sets
_VALID_FIELDS_BY_CATEGORY: dict[str, set] = {
    cat: {f["key"] for f in catalog["fields"]}
    for cat, catalog in FIELD_CATALOGS.items()
}

# Backward-compatible alias used by existing balances code paths
_VALID_FIELDS: set[str] = _VALID_FIELDS_BY_CATEGORY["balances"]

_VALID_OPS: set[str] = {"gt", "gte", "lt", "lte", "eq", "between"}
_RECEIPT_SUBCATEGORIES: set[str] = {"Bank Receipt", "Cash Receipt"}

# Window suffixes in display order
_WINDOW_SUFFIXES = ["prev_day", "prev_week", "last_month", "prev_quarter", "fy", "current_month"]

# Aggregate field name per category per window suffix
_WINDOW_TO_FIELD: dict[str, dict[str, str]] = {
    "sales":        {w: f"net_sales_{w}"     for w in _WINDOW_SUFFIXES},
    "sale_returns": {w: f"sale_returns_{w}"  for w in _WINDOW_SUFFIXES},
}


# ── condition matching ────────────────────────────────────────────────────────

def _match_condition(value: float, op: str, threshold: float, value2: float | None) -> bool:
    if op == "gt":
        return value > threshold
    if op == "gte":
        return value >= threshold
    if op == "lt":
        return value < threshold
    if op == "lte":
        return value <= threshold
    if op == "eq":
        return value == threshold
    if op == "between":
        if value2 is None:
            return False
        lo = min(threshold, value2)
        hi = max(threshold, value2)
        return lo <= value <= hi
    return False


def _customer_matches(
    outstanding: float,
    age_days: int,
    days_since_last_receipt: float,
    conditions: list[dict],
    match_type: str,
) -> bool:
    """Return True if this customer satisfies the conditions under match_type."""
    if not conditions:
        return False
    results = []
    for cond in conditions:
        field = cond["field"]
        op = cond["op"]
        threshold = float(cond["value"])
        value2_raw = cond.get("value2")
        value2 = float(value2_raw) if value2_raw is not None else None
        if field == "amount":
            val = outstanding
        elif field == "age_days":
            val = float(age_days)
        elif field == "days_since_last_receipt":
            val = float(days_since_last_receipt)
        else:
            results.append(False)
            continue
        results.append(_match_condition(val, op, threshold, value2))
    if match_type == "all":
        return all(results)
    return any(results)  # 'any'


# ── FIFO aging helper ─────────────────────────────────────────────────────────

def _compute_fifo_age(debits: list[tuple[date, float]], total_credit: float, today: date) -> int | None:
    """
    debits: list of (date, amount) sorted oldest first.
    total_credit: total credits across all time for this customer.
    today: reference date for age calculation.

    Apply credits FIFO (reduce the oldest debits first).
    Return the age in days of the oldest debit still carrying an unpaid remainder.
    Return None if fully covered.
    """
    remaining_credit = total_credit
    for debit_date, debit_amount in debits:
        if remaining_credit >= debit_amount:
            remaining_credit -= debit_amount
        else:
            return (today - debit_date).days
    return None  # Fully covered


# ── window date computation ───────────────────────────────────────────────────

def compute_window_dates(run_date: date) -> dict[str, tuple[date, date]]:
    """
    Compute inclusive (start, end) date ranges for each aggregate time window.

    Parameters
    ----------
    run_date : today's IST date. All windows are completed periods that end on or
               before yesterday (run_date - 1 day).

    Windows
    -------
    prev_day      — yesterday (the full previous calendar day).
    prev_week     — the completed calendar week immediately before the current one:
                    Monday through Sunday, where "current week" is the week
                    containing run_date.
    last_month    — the previous calendar month (1st through last day).
    prev_quarter  — the previous fiscal quarter. Fiscal year starts April 1:
                    Q1=Apr–Jun, Q2=Jul–Sep, Q3=Oct–Dec, Q4=Jan–Mar.
                    "Previous" is the quarter immediately before the quarter that
                    contains run_date.
    fy            — current fiscal year to date: April 1 of the FY containing
                    run_date, through yesterday. May be an empty range (start > end)
                    if run_date is April 1 itself.
    current_month — current calendar month to date: first day of run_date's month
                    through yesterday. May be an empty range (start > end) if
                    run_date is the 1st of the month (no completed day yet this month).
    """
    yesterday = run_date - timedelta(days=1)

    # prev_week: Monday–Sunday of the completed week before the current one.
    # run_date.weekday(): Mon=0, Tue=1, ..., Sun=6
    start_of_current_week = run_date - timedelta(days=run_date.weekday())
    prev_week_end   = start_of_current_week - timedelta(days=1)   # last Sunday
    prev_week_start = prev_week_end - timedelta(days=6)           # previous Monday

    # last_month: 1st through last day of the previous calendar month.
    first_of_this_month = run_date.replace(day=1)
    last_month_end   = first_of_this_month - timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)

    # prev_quarter: previous fiscal quarter (FY = Apr–Mar).
    m = run_date.month
    y = run_date.year
    if 4 <= m <= 6:      # current Q1 (Apr–Jun)  → prev Q4 (Jan–Mar, same calendar year)
        prev_q_start, prev_q_end = date(y, 1, 1), date(y, 3, 31)
    elif 7 <= m <= 9:    # current Q2 (Jul–Sep)  → prev Q1 (Apr–Jun, same FY)
        prev_q_start, prev_q_end = date(y, 4, 1), date(y, 6, 30)
    elif 10 <= m <= 12:  # current Q3 (Oct–Dec)  → prev Q2 (Jul–Sep, same FY)
        prev_q_start, prev_q_end = date(y, 7, 1), date(y, 9, 30)
    else:                # m in (1,2,3): current Q4 (Jan–Mar) → prev Q3 (Oct–Dec, prev year)
        prev_q_start, prev_q_end = date(y - 1, 10, 1), date(y - 1, 12, 31)

    # fy: current fiscal year to date (April 1 → yesterday).
    # On April 1 itself, fy_end = March 31 < fy_start = April 1 → empty range (returns 0).
    fy_start_year = y if m >= 4 else y - 1
    fy_start = date(fy_start_year, 4, 1)
    fy_end   = yesterday

    # current_month: first day of current calendar month through yesterday (MTD).
    # If run_date is the 1st, current_month_start == run_date > yesterday → empty range.
    current_month_start = run_date.replace(day=1)
    current_month_end   = yesterday

    return {
        "prev_day":      (yesterday,            yesterday),
        "prev_week":     (prev_week_start,      prev_week_end),
        "last_month":    (last_month_start,     last_month_end),
        "prev_quarter":  (prev_q_start,         prev_q_end),
        "fy":            (fy_start,             fy_end),
        "current_month": (current_month_start,  current_month_end),
    }


# ── aggregate metric query ────────────────────────────────────────────────────

def _query_aggregate_metrics(
    conn,
    category: str,
    branch: str | None,
    windows: dict[str, tuple[date, date]],
    windows_needed: set[str],
) -> dict[str, float]:
    """
    Query the `sales` table and return aggregate metric values for the requested
    time windows, scoped to customer-only, non-IRAVI parties.

    Metric definitions
    ------------------
    sales        — SUM(av WHERE sales_return='N') − SUM(av WHERE sales_return='Y')
    sale_returns — SUM(av WHERE sales_return='Y')

    Party restriction
    -----------------
    UPPER(party) IN (SELECT UPPER(customer_name) FROM customer_details)
    AND party NOT ILIKE '%iravi%'

    Branch restriction
    ------------------
    Applied only when branch is set and not 'ALL' / empty.

    Parameters
    ----------
    conn           : open psycopg2 connection
    category       : 'sales' or 'sale_returns'
    branch         : branch name, 'ALL', or None (None/'ALL'/'' → no branch filter)
    windows        : full window dict from compute_window_dates()
    windows_needed : subset of window keys to compute

    Returns
    -------
    dict mapping field_name → float rounded to 2 dp
    e.g. {'net_sales_prev_day': 5000.0, 'net_sales_fy': 120000.0}
    """
    if not windows_needed:
        return {}

    use_branch = bool(branch and branch not in ("ALL", ""))

    # Outer scan range = union of all needed windows so Postgres can use a date index.
    all_starts = [windows[w][0] for w in windows_needed]
    all_ends   = [windows[w][1] for w in windows_needed]
    scan_start = min(all_starts)
    scan_end   = max(all_ends)

    # If the combined outer range is inverted (can happen when all windows are
    # degenerate, e.g. only the fy window on April 1), return zeros immediately.
    if scan_start > scan_end:
        return {_WINDOW_TO_FIELD[category][w]: 0.0 for w in windows_needed}

    params: dict[str, Any] = {
        "scan_start": scan_start,
        "scan_end":   scan_end,
    }
    if use_branch:
        params["branch"] = branch

    select_parts: list[str] = []
    result_keys: list[str] = []

    # Sort windows for deterministic column order in the SELECT / result dict.
    for window in sorted(windows_needed):
        ws, we = windows[window]
        ws_key = f"ws_{window}"
        we_key = f"we_{window}"
        params[ws_key] = ws
        params[we_key] = we

        field_name = _WINDOW_TO_FIELD[category][window]
        result_keys.append(field_name)

        if category == "sales":
            select_parts.append(
                f"ROUND("
                f"  COALESCE(SUM(av) FILTER (WHERE sales_return='N'"
                f"    AND purchase_date BETWEEN %({ws_key})s AND %({we_key})s), 0)"
                f"  - COALESCE(SUM(av) FILTER (WHERE sales_return='Y'"
                f"    AND purchase_date BETWEEN %({ws_key})s AND %({we_key})s), 0)"
                f", 2)"
            )
        else:  # sale_returns
            select_parts.append(
                f"ROUND("
                f"  COALESCE(SUM(av) FILTER (WHERE sales_return='Y'"
                f"    AND purchase_date BETWEEN %({ws_key})s AND %({we_key})s), 0)"
                f", 2)"
            )

    branch_clause = "AND branch = %(branch)s" if use_branch else ""

    sql = f"""
        SELECT {', '.join(select_parts)}
        FROM sales
        WHERE out_z IS NULL
          AND UPPER(party) IN (SELECT UPPER(customer_name) FROM customer_details)
          AND party NOT ILIKE '%%iravi%%'
          {branch_clause}
          AND purchase_date BETWEEN %(scan_start)s AND %(scan_end)s
    """

    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()

    if row is None:
        return {key: 0.0 for key in result_keys}

    return {key: float(val or 0) for key, val in zip(result_keys, row)}


# ── aggregate evaluation ──────────────────────────────────────────────────────

def evaluate_aggregate(conn, alert: dict, today: date | None = None) -> dict:
    """
    Evaluate a sales or sale_returns alert by computing window metrics and
    checking conditions against them.

    Parameters
    ----------
    conn  : open psycopg2 connection (caller owns open/close)
    alert : alert dict with keys: category, conditions, match_type, branch
    today : evaluation date (defaults to date.today())

    Returns
    -------
    {
        "category":   "sales" | "sale_returns",
        "matched":    bool,
        "metrics":    {<field>: <float>}   (only windows referenced in conditions;
                      empty dict when conditions list is empty)
        "conditions": [
            {
                "field": str, "op": str, "value": float, "value2": float | None,
                "actual": float, "breached": bool
            }
        ]  (empty list when conditions list is empty)
    }

    When conditions is an empty list the alert is unconditional:
    matched is always True so the alert fires on every scheduled run.
    metrics and conditions in the returned dict will be empty ({} and []).
    """
    if today is None:
        today = date.today()

    category   = alert["category"]
    conditions = alert["conditions"]
    match_type = alert.get("match_type", "all")
    branch     = alert.get("branch") or "ALL"

    # Determine which time windows are referenced by the conditions.
    windows_needed: set[str] = set()
    for cond in conditions:
        field = cond["field"]
        for suffix in _WINDOW_SUFFIXES:
            if field.endswith(suffix):
                windows_needed.add(suffix)
                break

    all_windows = compute_window_dates(today)
    metrics = _query_aggregate_metrics(conn, category, branch, all_windows, windows_needed)

    # Evaluate each condition against the computed metric.
    condition_results: list[dict] = []
    for cond in conditions:
        field     = cond["field"]
        actual    = metrics.get(field, 0.0)
        threshold = float(cond["value"])
        value2_raw = cond.get("value2")
        value2    = float(value2_raw) if value2_raw is not None else None
        breached  = _match_condition(actual, cond["op"], threshold, value2)
        condition_results.append({
            "field":   field,
            "op":      cond["op"],
            "value":   threshold,
            "value2":  value2,
            "actual":  actual,
            "breached": breached,
        })

    # Combine results according to match_type.
    # Empty conditions → unconditional alert; always fires.
    if not condition_results:
        matched = True
    else:
        breached_flags = [c["breached"] for c in condition_results]
        if match_type == "all":
            matched = all(breached_flags)
        else:  # any
            matched = any(breached_flags)

    return {
        "category":   category,
        "matched":    matched,
        "metrics":    metrics,
        "conditions": condition_results,
    }


# ── main balances evaluation ──────────────────────────────────────────────────

def evaluate_balances(conn, conditions: list[dict], match_type: str, today: date | None = None) -> list[dict]:
    """
    Run the shared balances evaluation against the live DB.

    Parameters
    ----------
    conn       : open psycopg2 connection (caller owns open/close)
    conditions : list of {field, op, value, value2} dicts
    match_type : 'all' | 'any'
    today      : date for age calculation (defaults to date.today())

    Returns
    -------
    List of matching customer dicts (sorted by outstanding desc):
      {customer_name, city, code, outstanding, age_days, days_since_last_receipt,
       last_receipt_amount, last_receipt_date}
    """
    if today is None:
        today = date.today()

    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                cl.account_name,
                cl.transaction_date,
                cl.category,
                cl.sub_category,
                cl.amount,
                cd.city,
                cd.customer_code
            FROM customer_ledger cl
            LEFT JOIN customer_details cd
                ON UPPER(cd.customer_name) = UPPER(cl.account_name)
            WHERE cl.out_z IS NULL
              AND LOWER(cl.account_name) NOT LIKE '%%iravi%%'
            ORDER BY cl.account_name, cl.transaction_date ASC
        """)
        rows = cur.fetchall()

    from collections import defaultdict

    meta: dict[str, dict] = {}
    debits_by_customer: dict[str, list] = defaultdict(list)
    total_credit_by_customer: dict[str, float] = defaultdict(float)
    last_receipt: dict[str, tuple] = {}

    for (account_name, txn_date, category, sub_category, amount,
         city, customer_code) in rows:
        if account_name not in meta:
            meta[account_name] = {"city": city, "code": customer_code}

        amt = float(amount or 0)
        if category == "Db":
            debits_by_customer[account_name].append((txn_date, amt))
        else:  # Cr
            total_credit_by_customer[account_name] += amt
            if sub_category in _RECEIPT_SUBCATEGORIES:
                prev = last_receipt.get(account_name)
                if prev is None or txn_date >= prev[0]:
                    last_receipt[account_name] = (txn_date, amt)

    all_customers = set(meta.keys())
    matched = []

    for cname in all_customers:
        debits      = debits_by_customer.get(cname, [])
        total_debit = sum(amt for _, amt in debits)
        total_credit = total_credit_by_customer.get(cname, 0.0)
        outstanding  = total_debit - total_credit

        if outstanding <= 0:
            continue

        age_days_val = _compute_fifo_age(debits, total_credit, today)
        if age_days_val is None:
            continue

        receipt_info = last_receipt.get(cname)
        if receipt_info is not None:
            last_receipt_date_obj = receipt_info[0]
            days_since_last_receipt: float = (today - last_receipt_date_obj).days
        else:
            last_receipt_date_obj = None
            days_since_last_receipt = float(_NEVER_PAID_SENTINEL)

        if not _customer_matches(outstanding, age_days_val, days_since_last_receipt, conditions, match_type):
            continue

        last_receipt_date   = last_receipt_date_obj.isoformat() if last_receipt_date_obj else None
        last_receipt_amount = float(receipt_info[1]) if receipt_info else None

        info = meta[cname]
        matched.append({
            "customer_name":          cname,
            "city":                   info["city"],
            "code":                   info["code"],
            "outstanding":            round(outstanding, 2),
            "age_days":               age_days_val,
            "days_since_last_receipt": days_since_last_receipt,
            "last_receipt_amount":    last_receipt_amount,
            "last_receipt_date":      last_receipt_date,
        })

    matched.sort(key=lambda r: r["outstanding"], reverse=True)
    return matched


# ── validation helpers (used by the API) ─────────────────────────────────────

class ValidationError(Exception):
    """Raised when an alert body fails validation. message is user-facing."""
    pass


import re as _re

_EMAIL_RE = _re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')

_FREQUENCY_VALID = {"daily", "weekly", "monthly"}
_MATCH_TYPE_VALID = {"all", "any"}

_SCHEDULE_TIME_RE = _re.compile(r'^([01]\d|2[0-3]):([0-5]\d)$')
_DEFAULT_SCHEDULE_TIME = "11:00"


def _validate_schedule_time(value: str) -> str:
    """Validate and normalise a schedule_time string.  Returns the value unchanged if valid.
    Raises ValidationError if not a valid 24h HH:MM string."""
    if not _SCHEDULE_TIME_RE.match(value):
        raise ValidationError(
            f"schedule_time must be a valid 24h HH:MM string (e.g. '14:30'), got {value!r}"
        )
    return value


def validate_alert(body: dict) -> None:
    """
    Validate the body of a POST /alerts or PUT /alerts/{id} request.
    Raises ValidationError with a descriptive message on the first problem found.

    Accepts category in {'balances', 'sales', 'sale_returns', 'customer_balances_fy',
                          'supplier_balances_fy'}.
    Validates conditions' field keys against the per-category field catalog.
    branch is optional for all categories; for sales/sale_returns it defaults to
    'ALL' (all branches) when absent or null.

    customer_balances_fy / supplier_balances_fy:
      Zero conditions are accepted (unconditional; always fires on schedule).
      Not branch-scoped — branch is accepted and stored but not used in evaluation.
      Fields list is empty so no condition field validation is ever applied.
    """
    name = (body.get("name") or "").strip()
    if not name:
        raise ValidationError("name is required")

    category = body.get("category", "balances")
    if category not in _VALID_CATEGORIES:
        raise ValidationError(
            f"category must be one of: {sorted(_VALID_CATEGORIES)}"
        )

    frequency = body.get("frequency")
    if frequency not in _FREQUENCY_VALID:
        raise ValidationError(f"frequency must be one of: {sorted(_FREQUENCY_VALID)}")

    schedule_day = body.get("schedule_day")
    if frequency == "daily":
        if schedule_day is not None:
            raise ValidationError("schedule_day must be null for daily frequency")
    elif frequency == "weekly":
        if schedule_day is None or not isinstance(schedule_day, int) or not (0 <= schedule_day <= 6):
            raise ValidationError("schedule_day must be 0-6 (Mon-Sun) for weekly frequency")
    elif frequency == "monthly":
        if schedule_day is None or not isinstance(schedule_day, int) or not (1 <= schedule_day <= 28):
            raise ValidationError("schedule_day must be 1-28 for monthly frequency")

    schedule_time_raw = body.get("schedule_time")
    if schedule_time_raw is not None:
        _validate_schedule_time(str(schedule_time_raw))

    match_type = body.get("match_type")
    if match_type not in _MATCH_TYPE_VALID:
        raise ValidationError(f"match_type must be one of: {sorted(_MATCH_TYPE_VALID)}")

    conditions = body.get("conditions")
    if not isinstance(conditions, list):
        raise ValidationError("conditions must be a list")
    # balances always requires at least one condition.
    # sales / sale_returns may have zero conditions (unconditional scheduled alert —
    # always fires on schedule regardless of metric values).
    if category == "balances" and len(conditions) == 0:
        raise ValidationError("conditions must be a non-empty list for balances alerts")

    valid_fields = _VALID_FIELDS_BY_CATEGORY[category]

    for i, cond in enumerate(conditions):
        prefix = f"conditions[{i}]"
        field = cond.get("field")
        if field not in valid_fields:
            raise ValidationError(
                f"{prefix}.field must be one of: {sorted(valid_fields)} (for category '{category}')"
            )
        op = cond.get("op")
        if op not in _VALID_OPS:
            raise ValidationError(f"{prefix}.op must be one of: {sorted(_VALID_OPS)}")
        try:
            float(cond["value"])
        except (KeyError, TypeError, ValueError):
            raise ValidationError(f"{prefix}.value must be a numeric value")
        if op == "between":
            try:
                float(cond["value2"])
            except (KeyError, TypeError, ValueError):
                raise ValidationError(
                    f"{prefix}.value2 is required and must be numeric when op='between'"
                )
        else:
            if cond.get("value2") is not None:
                raise ValidationError(
                    f"{prefix}.value2 must be null when op is not 'between'"
                )

    recipients = body.get("recipients")
    if not isinstance(recipients, list) or len(recipients) == 0:
        raise ValidationError("recipients must be a non-empty list of email addresses")
    for email in recipients:
        if not isinstance(email, str) or not _EMAIL_RE.match(email.strip()):
            raise ValidationError(f"Invalid email address in recipients: {email!r}")


def is_alert_due_today(frequency: str, schedule_day, today: date) -> bool:
    """
    Return True if an alert with the given frequency/schedule_day is due on `today` (IST date).

    daily   → always True
    weekly  → today.weekday() == schedule_day  (0=Mon, 6=Sun)
    monthly → today.day == schedule_day  (1-28)
    """
    if frequency == "daily":
        return True
    if frequency == "weekly":
        return today.weekday() == schedule_day
    if frequency == "monthly":
        return today.day == schedule_day
    return False
