#!/usr/bin/env python3
"""
Local unit tests for alerts_eval aggregate alert logic.

Run: python test_alerts_aggregate.py

Covers:
  1. compute_window_dates() — window date boundaries including fiscal-quarter
     rollover and the April-1 FY boundary (empty range).
  2. _query_aggregate_metrics() — SQL selection logic via a stub connection.
     Verifies field-name mapping, sorted column order, branch handling.
  3. evaluate_aggregate() — full condition-fire logic with match_type all/any
     and the between operator.

No AWS, no DB required.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from datetime import date
import alerts_eval

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


# ── stub connection ───────────────────────────────────────────────────────────

class _StubCursor:
    def __init__(self, row):
        self._row = row
        self.executed_sql = ""
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def execute(self, sql, params=None):
        self.executed_sql = sql
    def fetchone(self):
        return self._row


class _StubConn:
    def __init__(self, row):
        self._cursor = _StubCursor(row)
    def cursor(self):
        return self._cursor


# ── 1. Window date boundaries ─────────────────────────────────────────────────

print("\n=== 1. Window date boundaries ===")

# 2026-06-20 is a Saturday (weekday=5), month=6 (Q1 → prev=Q4 Jan-Mar)
d = date(2026, 6, 20)
w = alerts_eval.compute_window_dates(d)

check("prev_day",
      (date(2026, 6, 19), date(2026, 6, 19)),
      w["prev_day"])
# Monday of current week = June 20 - 5 = June 15; prev week Mon=June 8, Sun=June 14
check("prev_week (Mon 8 Jun – Sun 14 Jun)",
      (date(2026, 6, 8), date(2026, 6, 14)),
      w["prev_week"])
check("last_month (May 2026)",
      (date(2026, 5, 1), date(2026, 5, 31)),
      w["last_month"])
# month=6 → current Q1 → prev=Q4 (Jan-Mar same year)
check("prev_quarter month=6 (Q4 Jan-Mar 2026)",
      (date(2026, 1, 1), date(2026, 3, 31)),
      w["prev_quarter"])
# FY Apr 2026 → yesterday = 2026-06-19
check("fy (Apr 2026 – Jun 19 2026)",
      (date(2026, 4, 1), date(2026, 6, 19)),
      w["fy"])

# 2026-04-01: FY boundary — fy_end (Mar 31) < fy_start (Apr 1) = empty range
d2 = date(2026, 4, 1)
w2 = alerts_eval.compute_window_dates(d2)
check("April-1 fy empty range (start > end)",
      (date(2026, 4, 1), date(2026, 3, 31)),
      w2["fy"])
# month=4 → current Q1 → prev=Q4 (Jan-Mar same year)
check("April-1 prev_quarter (Q4 Jan-Mar 2026)",
      (date(2026, 1, 1), date(2026, 3, 31)),
      w2["prev_quarter"])

# 2026-01-15: month=1 (Q4) → prev=Q3 (Oct-Dec 2025)
d3 = date(2026, 1, 15)
w3 = alerts_eval.compute_window_dates(d3)
check("Jan prev_quarter (Q3 Oct-Dec 2025)",
      (date(2025, 10, 1), date(2025, 12, 31)),
      w3["prev_quarter"])
# FY 25-26: starts Apr 1 2025; yesterday = Jan 14 2026
check("Jan fy (FY 25-26: Apr 2025 – Jan 14 2026)",
      (date(2025, 4, 1), date(2026, 1, 14)),
      w3["fy"])

# 2026-10-01: month=10 (Q3) → prev=Q2 (Jul-Sep 2026)
d4 = date(2026, 10, 1)
w4 = alerts_eval.compute_window_dates(d4)
check("Oct prev_quarter (Q2 Jul-Sep 2026)",
      (date(2026, 7, 1), date(2026, 9, 30)),
      w4["prev_quarter"])

# 2026-07-15: month=7 (Q2) → prev=Q1 (Apr-Jun 2026)
d5 = date(2026, 7, 15)
w5 = alerts_eval.compute_window_dates(d5)
check("Jul prev_quarter (Q1 Apr-Jun 2026)",
      (date(2026, 4, 1), date(2026, 6, 30)),
      w5["prev_quarter"])

# Monday run_date: 2026-06-15 (Monday, weekday=0)
# current week starts June 15; prev week Mon=June 8, Sun=June 14
d6 = date(2026, 6, 15)
w6 = alerts_eval.compute_window_dates(d6)
check("Monday run_date prev_week (Jun 8 – Jun 14)",
      (date(2026, 6, 8), date(2026, 6, 14)),
      w6["prev_week"])

# Sunday run_date: 2026-06-14 (Sunday, weekday=6)
# current week Mon = June 14 - 6 = June 8; prev week Mon=June 1, Sun=June 7
d7 = date(2026, 6, 14)
w7 = alerts_eval.compute_window_dates(d7)
check("Sunday run_date prev_week (Jun 1 – Jun 7)",
      (date(2026, 6, 1), date(2026, 6, 7)),
      w7["prev_week"])


# ── 2. Metric SQL logic (stub) ────────────────────────────────────────────────

print("\n=== 2. Metric SQL logic (stub) ===")

run_date = date(2026, 6, 20)
windows  = alerts_eval.compute_window_dates(run_date)

# sorted({"prev_day","fy"}) = ["fy", "prev_day"]
# result_keys = ["net_sales_fy", "net_sales_prev_day"]
# mock row: (fy_value, prev_day_value) = (120000.0, 5000.0)
stub_sales = _StubConn((120000.0, 5000.0))
metrics = alerts_eval._query_aggregate_metrics(
    stub_sales, "sales", "RAJAHMUNDRY", windows, {"prev_day", "fy"}
)
check("sales net_sales_prev_day", 5000.0, metrics.get("net_sales_prev_day"))
check("sales net_sales_fy",      120000.0, metrics.get("net_sales_fy"))

# sale_returns with single window "prev_week"
# sorted({"prev_week"}) = ["prev_week"]
# result_keys = ["sale_returns_prev_week"]
stub_sr = _StubConn((8000.0,))
metrics2 = alerts_eval._query_aggregate_metrics(
    stub_sr, "sale_returns", None, windows, {"prev_week"}
)
check("sale_returns sale_returns_prev_week", 8000.0, metrics2.get("sale_returns_prev_week"))

# Branch=ALL behaves same as no branch (no branch filter in SQL)
stub_all = _StubConn((3000.0,))
metrics3 = alerts_eval._query_aggregate_metrics(
    stub_all, "sales", "ALL", windows, {"prev_day"}
)
check("sales branch=ALL still returns metric", 3000.0, metrics3.get("net_sales_prev_day"))

# Empty windows_needed → empty dict, no DB call needed
result_empty = alerts_eval._query_aggregate_metrics(
    _StubConn(None), "sales", None, windows, set()
)
check("empty windows_needed → {}", {}, result_empty)

# Degenerate fy window (April 1): scan_start > scan_end for single fy window
# → should return zeros without querying
d_apr1 = date(2026, 4, 1)
w_apr1 = alerts_eval.compute_window_dates(d_apr1)
result_empty_fy = alerts_eval._query_aggregate_metrics(
    _StubConn(None), "sales", None, w_apr1, {"fy"}
)
check("April-1 degenerate fy → {net_sales_fy: 0.0}", {"net_sales_fy": 0.0}, result_empty_fy)


# ── 3. Condition-fire logic ───────────────────────────────────────────────────

print("\n=== 3. Condition-fire logic ===")

# sorted(windows) for conditions on net_sales_prev_day + net_sales_fy:
#   sorted({"prev_day","fy"}) = ["fy","prev_day"]
#   result_keys = ["net_sales_fy","net_sales_prev_day"]
#   mock row order: (fy_val, prev_day_val)

alert_all = {
    "category":   "sales",
    "match_type": "all",
    "branch":     None,
    "conditions": [
        {"field": "net_sales_prev_day", "op": "gte", "value": 1000, "value2": None},
        {"field": "net_sales_fy",       "op": "gte", "value": 50000, "value2": None},
    ],
}

# prev_day=500 (fails), fy=120000 (passes) → all → False
r_all_fail = alerts_eval.evaluate_aggregate(
    _StubConn((120000.0, 500.0)), alert_all, today=run_date
)
check("all: not all conditions fire → matched=False", False, r_all_fail["matched"])
check("all: net_sales_prev_day actual=500",  500.0,    r_all_fail["conditions"][0]["actual"])
check("all: net_sales_fy actual=120000",    120000.0,  r_all_fail["conditions"][1]["actual"])
check("all: prev_day breached=False", False, r_all_fail["conditions"][0]["breached"])
check("all: fy breached=True",        True,  r_all_fail["conditions"][1]["breached"])

# prev_day=2000 (passes), fy=120000 (passes) → all → True
r_all_pass = alerts_eval.evaluate_aggregate(
    _StubConn((120000.0, 2000.0)), alert_all, today=run_date
)
check("all: both conditions fire → matched=True", True, r_all_pass["matched"])

# match_type=any: prev_day=500 (fails), fy=120000 (passes) → any → True
alert_any = dict(alert_all, match_type="any")
r_any = alerts_eval.evaluate_aggregate(
    _StubConn((120000.0, 500.0)), alert_any, today=run_date
)
check("any: one condition fires → matched=True", True, r_any["matched"])

# match_type=any: both fail → False
r_any_fail = alerts_eval.evaluate_aggregate(
    _StubConn((10000.0, 200.0)), alert_any, today=run_date
)
check("any: no conditions fire → matched=False", False, r_any_fail["matched"])

# between operator: sale_returns_prev_week between 5000 and 10000
# sorted({"prev_week"}) → ["prev_week"] → result_keys=["sale_returns_prev_week"]
alert_between = {
    "category":   "sale_returns",
    "match_type": "all",
    "branch":     "RAJAHMUNDRY",
    "conditions": [
        {"field": "sale_returns_prev_week", "op": "between", "value": 5000, "value2": 10000},
    ],
}
r_between = alerts_eval.evaluate_aggregate(
    _StubConn((8000.0,)), alert_between, today=run_date
)
check("between 5000-10000, actual=8000 → matched=True", True, r_between["matched"])

# between: value outside range → False
r_between_out = alerts_eval.evaluate_aggregate(
    _StubConn((12000.0,)), alert_between, today=run_date
)
check("between 5000-10000, actual=12000 → matched=False", False, r_between_out["matched"])

# eq operator
alert_eq = {
    "category":   "sales",
    "match_type": "all",
    "branch":     None,
    "conditions": [
        {"field": "net_sales_prev_day", "op": "eq", "value": 0, "value2": None},
    ],
}
# sorted({"prev_day"}) → ["prev_day"] → result_keys=["net_sales_prev_day"]
r_eq = alerts_eval.evaluate_aggregate(
    _StubConn((0.0,)), alert_eq, today=run_date
)
check("eq: actual=0 == threshold=0 → matched=True", True, r_eq["matched"])

# Verify category + metrics keys in result
check("result has category=sales",         "sales", r_all_fail["category"])
check("result metrics has net_sales_prev_day", True, "net_sales_prev_day" in r_all_fail["metrics"])
check("result metrics has net_sales_fy",       True, "net_sales_fy"       in r_all_fail["metrics"])

# ── catalog sanity checks ─────────────────────────────────────────────────────

print("\n=== 4. Catalog sanity ===")

check("FIELD_CATALOGS has 3 categories", 3, len(alerts_eval.FIELD_CATALOGS))
check("sales catalog branch_scoped=True",
      True, alerts_eval.FIELD_CATALOG_SALES.get("branch_scoped"))
check("sale_returns catalog branch_scoped=True",
      True, alerts_eval.FIELD_CATALOG_SALE_RETURNS.get("branch_scoped"))
check("balances catalog has no branch_scoped key",
      False, "branch_scoped" in alerts_eval.FIELD_CATALOG)
check("sales has 5 fields", 5, len(alerts_eval.FIELD_CATALOG_SALES["fields"]))
check("sale_returns has 5 fields", 5, len(alerts_eval.FIELD_CATALOG_SALE_RETURNS["fields"]))

expected_sales_keys = {
    "net_sales_prev_day", "net_sales_prev_week", "net_sales_last_month",
    "net_sales_prev_quarter", "net_sales_fy",
}
actual_sales_keys = {f["key"] for f in alerts_eval.FIELD_CATALOG_SALES["fields"]}
check("sales field keys correct", expected_sales_keys, actual_sales_keys)

expected_sr_keys = {
    "sale_returns_prev_day", "sale_returns_prev_week", "sale_returns_last_month",
    "sale_returns_prev_quarter", "sale_returns_fy",
}
actual_sr_keys = {f["key"] for f in alerts_eval.FIELD_CATALOG_SALE_RETURNS["fields"]}
check("sale_returns field keys correct", expected_sr_keys, actual_sr_keys)

# validate_alert: new categories accepted
try:
    alerts_eval.validate_alert({
        "name": "Test", "category": "sales", "frequency": "daily",
        "schedule_day": None, "match_type": "all",
        "conditions": [{"field": "net_sales_prev_day", "op": "gt", "value": 100}],
        "recipients": ["a@b.com"],
    })
    check("validate_alert: sales category accepted", True, True)
except alerts_eval.ValidationError as e:
    check("validate_alert: sales category accepted", True, False)

try:
    alerts_eval.validate_alert({
        "name": "Test", "category": "sale_returns", "frequency": "weekly",
        "schedule_day": 0, "match_type": "any",
        "conditions": [{"field": "sale_returns_fy", "op": "gte", "value": 500}],
        "recipients": ["x@y.com"],
    })
    check("validate_alert: sale_returns category accepted", True, True)
except alerts_eval.ValidationError as e:
    check("validate_alert: sale_returns category accepted", True, False)

# validate_alert: wrong field for category should fail
try:
    alerts_eval.validate_alert({
        "name": "Bad", "category": "sales", "frequency": "daily",
        "schedule_day": None, "match_type": "all",
        "conditions": [{"field": "amount", "op": "gt", "value": 100}],
        "recipients": ["a@b.com"],
    })
    check("validate_alert: balances field rejected for sales", False, True)
except alerts_eval.ValidationError:
    check("validate_alert: balances field rejected for sales", True, True)

# ── summary ───────────────────────────────────────────────────────────────────

print(f"\n{'='*45}")
print(f"  PASS: {PASS}   FAIL: {FAIL}")
if FAIL > 0:
    sys.exit(1)
print("  All tests passed.")
