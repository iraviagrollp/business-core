"""Generates the synthetic StockReport CSV fixture used by test_process_csv.py."""
import csv
import os

_DIR = os.path.dirname(__file__)

HEADERS = [
    'BranchId', 'LocationId', 'BinId', 'ProductId', 'ProductGroup', 'ProductCode',
    'ProductDescription', 'ProductHSN', 'BaseUnit', 'Barcodes', 'BarcodeId', 'BrandId',
    'CategoryId', 'SubCategoryId', 'TypeId', 'SizeId', 'ColourId', 'DesignNo', 'StyleNo',
    'PartNo', 'SubTypeId', 'FashionId', 'FitId', 'PatternId', 'BalQty', 'CF', 'Cases',
    'PRate', 'MRP', 'RSP', 'RSPValue', 'Pcost', 'PCostValue', 'RateConversion', 'MRPValue',
    'RSPDiscRate', 'RSPDisc', 'NetRSP', 'WSP', 'OSP', 'NetRSPValue', 'Qty', 'BarcodeAge',
    'ExpiryDate',
]
assert len(HEADERS) == 44, len(HEADERS)


def _row(branch, product_id, product_desc, brand_id, balqty, cf, cases, qty, expiry_date=''):
    d = {h: '' for h in HEADERS}
    d['BranchId'] = branch
    d['ProductId'] = product_id
    d['ProductDescription'] = product_desc
    d['BrandId'] = brand_id
    d['BalQty'] = balqty
    d['CF'] = cf
    d['Cases'] = cases
    d['Qty'] = qty
    d['ExpiryDate'] = expiry_date
    d['ProductGroup'] = 'CHEMICAL'
    d['ProductCode'] = 'PC001'
    d['ProductHSN'] = '38089199'
    d['BaseUnit'] = 'ML'
    return d


ROWS = [
    # Two rows that must merge (same brand/technical/packing/branch/EXPIRY):
    # nos = 1480 + 500 = 1980
    _row('Guntur C &amp; F', 'GLUFOSINATE AMMONIUM 13.5 % W/W SL - GULFONID - 500 ML',
         'GULFONID 500 ML', 'GULFONID', '1480.000000', '20.000000',
         '74.00000000000000000000', '1480.000000', '21-08-2027 00:00:00'),
    _row('Guntur C &amp; F', 'GLUFOSINATE AMMONIUM 13.5 % W/W SL - GULFONID - 500 ML',
         'GULFONID 500 ML', 'GULFONID', '500.000000', '20.000000',
         '25.00000000000000000000', '500.000000', '21-08-2027 00:00:00'),
    # Blank-brand row (label/leaflet SKU) — must be skipped
    _row('Hyderabad', 'SOME LABEL - LEAFLET', 'LEAFLET', '', '10.000000', '1.000000',
         '10.00000000000000000000', '10.000000'),
    # Distinct second product (KG unit, multi-part technical) — separate merge key
    _row('Vijayawada', 'ACEPHATE 75 % SP - VIVAYA PLUS - 1 KG', 'VIVAYA PLUS 1 KG',
         'VIVAYA PLUS', '250.000000', '1.000000', '250.00000000000000000000', '250.000000',
         '15-03-2026 00:00:00'),
    # Same product/branch/packing, DIFFERENT expiry dates — must NOT merge; each
    # row's available_nos must be preserved independently.
    _row('Hyderabad', 'IMIDACLOPRID 17.8 % SL - RIVAL - 250 ML', 'RIVAL 250 ML',
         'RIVAL', '100.000000', '10.000000', '10.00000000000000000000', '100.000000',
         '10-01-2027 00:00:00'),
    _row('Hyderabad', 'IMIDACLOPRID 17.8 % SL - RIVAL - 250 ML', 'RIVAL 250 ML',
         'RIVAL', '200.000000', '10.000000', '20.00000000000000000000', '200.000000',
         '10-06-2027 00:00:00'),
    # Empty ExpiryDate — must be tolerated and parsed as None, row still processed.
    _row('Chennai', 'MANCOZEB 75 % WP - ZERION - 1 KG', 'ZERION 1 KG',
         'ZERION', '50.000000', '1.000000', '50.00000000000000000000', '50.000000', ''),
]


def main():
    path = os.path.join(_DIR, 'fixtures', 'StockReport_20260715_194634.csv')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS, quoting=csv.QUOTE_ALL)
        writer.writeheader()
        for r in ROWS:
            writer.writerow(r)
    print('wrote', path)


if __name__ == '__main__':
    main()
