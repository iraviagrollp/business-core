"""
supplier_balances_fy — shared Supplier Balances (FY) computation.

Public surface
--------------
compute_supplier_balances_fy(conn, fy_count) -> dict
    conn     : open psycopg2 connection (caller owns open/close)
    fy_count : 'all' (show every FY, zero opening) OR int >= 1 (most recent N
               FYs, first shown FY gets a brought-forward opening balance)
    returns  : payload dict matching the GET /reports/supplier-balances-fy JSON
               contract (fys, rows[], totals; NO code field, NO credit_notes field)

Shared by
---------
  lambda/api/handler.py             — GET /reports/supplier-balances-fy thin
                                      wrapper (cache-aside + _response)
  lambda/alerts_evaluator/handler.py — supplier_balances_fy alert branch (PDF
                                      email path; always calls with fy_count='all')

Keep lambda/alerts_evaluator/supplier_balances_fy.py byte-identical to this file
whenever changes are made here.

Aggregation notes
-----------------
Per-voucher netting (roundoff absorption):
  Rows are grouped by (party, voucher_no, fy_label). For each voucher:
    net = sum(Db rows) - sum(Cr rows)
  net > 0  -> debit  += net
  net < 0  -> credit += -net
  net == 0 -> nothing

No credit-note split: supplier_ledger has no credit-note sub-category.
FY definition: April 1 -> March 31.  Label: 'FY YY-YY' (e.g. 'FY 25-26').
Sort order: party name ascending (no code to sort by).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date as _date

logger = logging.getLogger(__name__)


def _fy_start_year(d: _date) -> int:
    """Return the April-1 year that starts the FY containing date d."""
    return d.year if d.month >= 4 else d.year - 1


def _fy_label(start_year: int) -> str:
    yy1 = start_year % 100
    yy2 = (start_year + 1) % 100
    return f'FY {yy1:02d}-{yy2:02d}'


def compute_supplier_balances_fy(conn, fy_count) -> dict:
    """Compute per-supplier, multi-FY roll-forward of debits/credits.

    Parameters
    ----------
    conn     : open psycopg2 connection — caller is responsible for lifecycle.
    fy_count : 'all'  — show every FY present in supplier_ledger; zero opening.
               int>=1 — show the most recent N FYs; first shown FY gets a
                        brought-forward opening from all transactions before it.

    Returns
    -------
    dict matching the GET /reports/supplier-balances-fy JSON contract:
      {
        'fys':  [fy_label, ...],           # oldest -> newest
        'rows': [{party, city, opening, per_fy, balance_dr, balance_cr}],
        'totals': {per_fy, balance_dr, balance_cr}
      }
    Note: no 'code' field (supplier_accounts has no party code).
          no 'credit_notes' field (supplier_ledger has no credit-note sub-category).
    """
    # -- 1. Determine the FY range present in the data -------------------------
    with conn.cursor() as cur:
        cur.execute("""
            SELECT MIN(transaction_date), MAX(transaction_date)
            FROM supplier_ledger
            WHERE out_z IS NULL
              AND LOWER(account_name) NOT LIKE '%%iravi%%'
        """)
        min_date, max_date = cur.fetchone()

    if min_date is None:
        return {
            'fys':  [],
            'rows': [],
            'totals': {'per_fy': [], 'balance_dr': 0.0, 'balance_cr': 0.0},
        }

    # All FY start years present in the data
    all_fy_start = _fy_start_year(min_date)
    all_fy_end   = _fy_start_year(max_date)
    all_fy_years = list(range(all_fy_start, all_fy_end + 1))

    # Determine which FYs to show and the cutoff date for opening balances
    if fy_count == 'all' or (isinstance(fy_count, int) and len(all_fy_years) <= fy_count):
        shown_fy_years = all_fy_years
        cutoff_date = None          # no opening balance needed
    else:
        shown_fy_years = all_fy_years[-fy_count:]
        cutoff_date = _date(shown_fy_years[0], 4, 1)

    shown_fy_labels = [_fy_label(y) for y in shown_fy_years]

    # -- 2. City lookup from supplier_accounts ---------------------------------
    with conn.cursor() as cur:
        cur.execute("""
            SELECT UPPER(name), city FROM supplier_accounts WHERE out_z IS NULL
        """)
        city_map: dict = {}
        for upper_name, city in cur.fetchall():
            city_map[upper_name] = city

        # Active supplier-master names (UPPER(name), out_z IS NULL) — only
        # parties present here are included in the report output below.
        active_supplier_names: set = set(city_map.keys())

        # -- 3. Opening balances (only when cutoff_date is set) ----------------
        opening_by_party: dict = {}
        if cutoff_date is not None:
            cur.execute("""
                SELECT account_name,
                       COALESCE(SUM(CASE WHEN category = 'Db' THEN amount ELSE -amount END), 0)
                FROM supplier_ledger
                WHERE out_z IS NULL
                  AND LOWER(account_name) NOT LIKE '%%iravi%%'
                  AND transaction_date < %(cutoff)s
                GROUP BY account_name
            """, {'cutoff': cutoff_date})
            for acct, bal in cur.fetchall():
                opening_by_party[acct] = float(bal)

        # -- 4. Per-party, per-FY ledger rows ----------------------------------
        window_start = _date(shown_fy_years[0], 4, 1)
        window_end   = _date(shown_fy_years[-1] + 1, 3, 31)

        cur.execute("""
            SELECT account_name,
                   voucher_no,
                   transaction_date,
                   category,
                   amount
            FROM supplier_ledger
            WHERE out_z IS NULL
              AND LOWER(account_name) NOT LIKE '%%iravi%%'
              AND transaction_date BETWEEN %(ws)s AND %(we)s
        """, {'ws': window_start, 'we': window_end})
        ledger_rows = cur.fetchall()

    # -- 5. Aggregate by (party, voucher_no, fy_label) with per-voucher netting
    # Roundoff and GST sub-components on the opposite side are absorbed so that
    # phantom paise credits/debits disappear.  Bucketing after netting:
    #   net > 0 -> debit  += net
    #   net < 0 -> credit += -net
    #   net == 0 -> nothing
    voucher_acc: dict = {}
    all_parties: set  = set()

    for account_name, voucher_no, transaction_date, category, amount in ledger_rows:
        fy_year = _fy_start_year(transaction_date)
        label = _fy_label(fy_year)
        if label not in shown_fy_labels:
            continue  # outside shown window (safety guard)
        amt = float(amount)
        key = (account_name, voucher_no, label)
        if key not in voucher_acc:
            voucher_acc[key] = {
                'net':   0.0,
                'party': account_name,
                'label': label,
            }
        if category == 'Db':
            voucher_acc[key]['net'] += amt
        else:
            voucher_acc[key]['net'] -= amt
        all_parties.add(account_name)

    # party -> {fy_label: {'debit': float, 'credit': float}}
    agg: dict = defaultdict(lambda: defaultdict(
        lambda: {'debit': 0.0, 'credit': 0.0}
    ))
    for v in voucher_acc.values():
        net   = v['net']
        party = v['party']
        label = v['label']
        if net > 0:
            agg[party][label]['debit'] += net
        elif net < 0:
            agg[party][label]['credit'] += -net

    # Also include parties that appear only in the opening period
    for party in opening_by_party:
        all_parties.add(party)

    # -- 6. Build result rows --------------------------------------------------
    result_rows = []
    totals_per_fy = {
        label: {'debit': 0.0, 'credit': 0.0, 'balance': 0.0}
        for label in shown_fy_labels
    }
    total_balance_dr = 0.0
    total_balance_cr = 0.0

    for party in sorted(all_parties, key=lambda p: (p.upper(), p)):
        if party.upper() not in active_supplier_names:
            # Not present as an active (out_z IS NULL) row in supplier_accounts
            # — drop from the report entirely, before any totals accumulate.
            continue

        opening = round(opening_by_party.get(party, 0.0), 2)
        running = opening
        per_fy  = []

        for label in shown_fy_labels:
            d      = agg[party][label]
            debit  = round(d['debit'],  2)
            credit = round(d['credit'], 2)
            running = round(running + debit - credit, 2)
            per_fy.append({
                'fy':      label,
                'debit':   debit,
                'credit':  credit,
                'balance': running,
            })

            totals_per_fy[label]['debit']  = round(totals_per_fy[label]['debit']  + debit,  2)
            totals_per_fy[label]['credit'] = round(totals_per_fy[label]['credit'] + credit, 2)

        # Skip parties with zero activity across all shown FYs and zero opening
        if opening == 0.0 and all(
            r['debit'] == 0.0 and r['credit'] == 0.0
            for r in per_fy
        ):
            continue

        balance_dr = round(running, 2) if running > 0 else 0.0
        balance_cr = round(running, 2) if running < 0 else 0.0

        city = city_map.get(party.upper())

        result_rows.append({
            'party':      party,
            'city':       city,
            'opening':    opening,
            'per_fy':     per_fy,
            'balance_dr': balance_dr,
            'balance_cr': balance_cr,
        })

        total_balance_dr = round(total_balance_dr + balance_dr, 2)
        total_balance_cr = round(total_balance_cr + balance_cr, 2)

    # Compute running totals balance per FY (net = debit - credit)
    for label in shown_fy_labels:
        t = totals_per_fy[label]
        t['balance'] = round(t['debit'] - t['credit'], 2)
        t['fy'] = label

    payload = {
        'fys':  shown_fy_labels,
        'rows': result_rows,
        'totals': {
            'per_fy':     [totals_per_fy[label] for label in shown_fy_labels],
            'balance_dr': total_balance_dr,
            'balance_cr': total_balance_cr,
        },
    }
    logger.info(
        'compute_supplier_balances_fy: fy_count=%s fys=%d parties=%d',
        fy_count, len(shown_fy_labels), len(result_rows),
    )
    return payload
