"""
Shared balances evaluation for Alerts.

Used by:
  - lambda/api/handler.py  (POST /alerts/{id}/test)
  - lambda/alerts_evaluator/handler.py  (nightly evaluator)

Both packages include this file directly (same logic, one source).

Evaluation contract
-------------------
Per customer (from customer_ledger, out_z IS NULL, excluding IRAVI internal accounts):

  outstanding  = SUM(amount WHERE category='Db') - SUM(amount WHERE category='Cr')
                 Only customers with outstanding > 0 are kept.

  age_days     = FIFO aging:
                 Order the customer's debits oldest→newest.  Apply total credits to
                 the oldest debits first.  The oldest debit still carrying an unpaid
                 remainder defines age = (today - that debit's date) in days.
                 If fully covered the customer is excluded.

  last_receipt_amount / last_receipt_date
               = most recent credit whose sub_category IN ('Bank Receipt','Cash Receipt').
                 NULL if none.

Condition evaluation
--------------------
Each condition is one of:
  field ∈ {'amount', 'age_days'}
  op   ∈ {'gt','gte','lt','lte','eq','between'}   (value2 only used for 'between')

Conditions are combined by match_type:
  'all'  → AND (customer matches every condition)
  'any'  → OR  (customer matches at least one condition)

Return value
------------
A list of dicts, one per matching customer:
  {customer_name, city, code, outstanding, age_days,
   last_receipt_amount, last_receipt_date}
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

logger = logging.getLogger(__name__)

# ── field catalog ─────────────────────────────────────────────────────────────

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
    ],
    "match_types": ["all", "any"],
    "frequencies": ["daily", "weekly", "monthly"],
}

_VALID_FIELDS = {f["key"] for f in FIELD_CATALOG["fields"]}
_VALID_OPS = {"gt", "gte", "lt", "lte", "eq", "between"}
_RECEIPT_SUBCATEGORIES = {"Bank Receipt", "Cash Receipt"}

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


def _customer_matches(outstanding: float, age_days: int, conditions: list[dict], match_type: str) -> bool:
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
            # This debit fully covered — move on
        else:
            # This debit still has an unpaid portion
            return (today - debit_date).days
    return None  # Fully covered


# ── main evaluation ───────────────────────────────────────────────────────────

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
      {customer_name, city, code, outstanding, age_days,
       last_receipt_amount, last_receipt_date}
    """
    if today is None:
        today = date.today()

    with conn.cursor() as cur:
        # Pull all active ledger rows for non-IRAVI customers,
        # joined to customer_details for city + code.
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

    # ── Aggregate per customer ─────────────────────────────────────────────────
    # We need per-customer:
    #   debits (date, amount) list — for FIFO aging
    #   total_credit — sum of all Cr amounts
    #   last_receipt_amount/date — most recent Bank/Cash Receipt credit

    from collections import defaultdict

    # customer_name -> metadata (city, code set on first encounter)
    meta: dict[str, dict] = {}
    # customer_name -> list of (date, amount) for Db rows (oldest first — already sorted by date ASC)
    debits_by_customer: dict[str, list] = defaultdict(list)
    total_credit_by_customer: dict[str, float] = defaultdict(float)
    last_receipt: dict[str, tuple] = {}  # customer_name -> (date, amount) of latest receipt

    for (account_name, txn_date, category, sub_category, amount,
         city, customer_code) in rows:
        if account_name not in meta:
            meta[account_name] = {"city": city, "code": customer_code}

        amt = float(amount or 0)
        if category == "Db":
            debits_by_customer[account_name].append((txn_date, amt))
        else:  # Cr
            total_credit_by_customer[account_name] += amt
            # Track last receipt (Bank Receipt / Cash Receipt)
            if sub_category in _RECEIPT_SUBCATEGORIES:
                prev = last_receipt.get(account_name)
                if prev is None or txn_date >= prev[0]:
                    last_receipt[account_name] = (txn_date, amt)

    all_customers = set(meta.keys())

    # ── Compute outstanding + age_days per customer ────────────────────────────
    matched = []

    for cname in all_customers:
        debits = debits_by_customer.get(cname, [])
        total_debit = sum(amt for _, amt in debits)
        total_credit = total_credit_by_customer.get(cname, 0.0)
        outstanding = total_debit - total_credit

        if outstanding <= 0:
            continue  # fully paid or no debit — exclude

        # FIFO age
        age_days_val = _compute_fifo_age(debits, total_credit, today)
        if age_days_val is None:
            continue  # FIFO says fully covered (shouldn't normally happen if outstanding > 0, but be safe)

        # Check conditions
        if not _customer_matches(outstanding, age_days_val, conditions, match_type):
            continue

        receipt_info = last_receipt.get(cname)
        last_receipt_date = receipt_info[0].isoformat() if receipt_info else None
        last_receipt_amount = float(receipt_info[1]) if receipt_info else None

        info = meta[cname]
        matched.append({
            "customer_name": cname,
            "city": info["city"],
            "code": info["code"],
            "outstanding": round(outstanding, 2),
            "age_days": age_days_val,
            "last_receipt_amount": last_receipt_amount,
            "last_receipt_date": last_receipt_date,
        })

    # Sort by outstanding descending
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


def validate_alert(body: dict) -> None:
    """
    Validate the body of a POST /alerts or PUT /alerts/{id} request.
    Raises ValidationError with a descriptive message on the first problem found.
    """
    name = (body.get("name") or "").strip()
    if not name:
        raise ValidationError("name is required")

    category = body.get("category", "balances")
    if category != "balances":
        raise ValidationError("category must be 'balances'")

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

    match_type = body.get("match_type")
    if match_type not in _MATCH_TYPE_VALID:
        raise ValidationError(f"match_type must be one of: {sorted(_MATCH_TYPE_VALID)}")

    conditions = body.get("conditions")
    if not isinstance(conditions, list) or len(conditions) == 0:
        raise ValidationError("conditions must be a non-empty list")

    for i, cond in enumerate(conditions):
        prefix = f"conditions[{i}]"
        field = cond.get("field")
        if field not in _VALID_FIELDS:
            raise ValidationError(f"{prefix}.field must be one of: {sorted(_VALID_FIELDS)}")
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
                raise ValidationError(f"{prefix}.value2 is required and must be numeric when op='between'")
        else:
            if cond.get("value2") is not None:
                raise ValidationError(f"{prefix}.value2 must be null when op is not 'between'")

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
