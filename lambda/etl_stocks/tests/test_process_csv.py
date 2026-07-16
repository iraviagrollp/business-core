#!/usr/bin/env python3
"""
Unit test for etl_stocks process.process_stock_file() against the NEW
StockReport CSV format (replaces the old 'Current Stock Balances*.xlsx' input).

Run: python test_process_csv.py   (from this directory)

Covers:
  1. CSV read by header name (BranchId/ProductId/BrandId/Qty/CF), UTF-8 BOM handled.
  2. Blank-brand row (label/leaflet SKU) skipped.
  3. technical/packing_size/packing_config parsed correctly from ProductId via
     the untouched _parse_product().
  4. available_nos = float(Qty), conversion_factor = float(CF).
  5. Row-merge: two rows sharing the same
     (brand, technical, packing_size, packing_config, branch, spec) are summed.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from process import process_stock_file  # noqa: E402

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


_FIXTURE = os.path.join(os.path.dirname(__file__), 'fixtures', 'StockReport_20260715_194634.csv')

print("=== process_stock_file() against StockReport CSV fixture ===")

with tempfile.TemporaryDirectory() as tmp:
    dst_path = os.path.join(tmp, 'Stock - Processed.xlsx')
    rows = process_stock_file(_FIXTURE, dst_path, rates_path=None)

    # Blank-brand LEAFLET row skipped + two GULFONID rows merged into one
    # + one VIVAYA PLUS row => 2 output rows total.
    check("row count (merge + skip applied)", 2, len(rows))

    by_key = {(r['brand'], r['branch']): r for r in rows}

    gulfonid = by_key.get(('GULFONID', 'Guntur C &amp; F'))
    check("GULFONID row present", True, gulfonid is not None)
    if gulfonid:
        check("GULFONID technical", 'GLUFOSINATE AMMONIUM 13.5 % W/W SL', gulfonid['technical'])
        check("GULFONID packing_size (500 ML -> ml, no conversion)", 500.0, gulfonid['packing_size'])
        check("GULFONID packing_configuration", 'ml', gulfonid['packing_configuration'])
        check("GULFONID available_nos summed (1480 + 500)", 1980.0, gulfonid['available_nos'])
        check("GULFONID conversion_factor = float(CF)", 20.0, gulfonid['conversion_factor'])
        check("GULFONID available_qty (packing_size * nos)", 500.0 * 1980.0, gulfonid['available_qty'])
        check("GULFONID branch preserved (incl. &amp; entity)", 'Guntur C &amp; F', gulfonid['branch'])

    vivaya = by_key.get(('VIVAYA PLUS', 'Vijayawada'))
    check("VIVAYA PLUS row present", True, vivaya is not None)
    if vivaya:
        check("VIVAYA PLUS technical (multi-part)", 'ACEPHATE 75 % SP', vivaya['technical'])
        check("VIVAYA PLUS packing_size (1 KG -> gms x1000)", 1000.0, vivaya['packing_size'])
        check("VIVAYA PLUS packing_configuration", 'gms', vivaya['packing_configuration'])
        check("VIVAYA PLUS available_nos = float(Qty)", 250.0, vivaya['available_nos'])
        check("VIVAYA PLUS conversion_factor = float(CF)", 1.0, vivaya['conversion_factor'])

    # LEAFLET (blank BrandId) must not appear at all
    check("blank-brand LEAFLET row skipped", False, any(r['technical'] == 'SOME LABEL' for r in rows))

print(f"\n  PASS: {PASS}   FAIL: {FAIL}")
if FAIL > 0:
    sys.exit(1)
