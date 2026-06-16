import json
import logging
import os
from datetime import datetime, timezone

import boto3
import psycopg2
import redis

logger = logging.getLogger()
logger.setLevel(logging.INFO)

secrets = boto3.client('secretsmanager')
s3 = boto3.client('s3')

_DATA_BUCKET = os.environ.get('DATA_BUCKET', '')

_REDIS_TTL = 86400       # 24h fallback TTL when populating from RDS on cache miss
_LEDGER_TTL = 3600       # 1h TTL for ledger range-query results
_APPENDIX_B_TTL = 900    # 15 min TTL for appendix-b meta and report
_PURCHASES_TTL = 900     # 15 min TTL for purchases meta and summary
_SALES_TTL = 900         # 15 min TTL for sales meta/list and customer names/details


def _get_db_conn():
    secret = json.loads(
        secrets.get_secret_value(SecretId=os.environ['DB_SECRET_ARN'])['SecretString']
    )
    return psycopg2.connect(
        host=secret['host'],
        port=secret.get('port', 5432),
        dbname=secret['dbname'],
        user=secret['username'],
        password=secret['password'],
    )


def _get_redis():
    return redis.Redis(host=os.environ['REDIS_HOST'], port=6379, decode_responses=True)


def _packing_display(packing_size_num: float, packing_config: str) -> str:
    ps = int(packing_size_num) if packing_size_num % 1 == 0 else packing_size_num
    return f"{ps} {packing_config}"


def lambda_handler(event, context):
    method = event.get('requestContext', {}).get('http', {}).get('method', '')
    path = event.get('rawPath', '')
    logger.info('%s %s', method, path)

    if method == 'POST' and path == '/notify':
        return _handle_notify(event.get('body') or '')

    if method != 'GET':
        return _response(405, {'error': 'Method not allowed'})

    if path == '/stocks/summary':
        return _handle_stocks_summary()
    if path == '/stocks/current':
        return _handle_stocks_current()
    if path == '/ledger/range':
        return _handle_ledger_range()
    if path == '/ledger/outstanding':
        params = event.get('queryStringParameters') or {}
        return _handle_ledger_outstanding(params.get('to_date', ''))
    if path == '/ledger/statement':
        params = event.get('queryStringParameters') or {}
        return _handle_ledger_statement(
            params.get('account_name', ''),
            params.get('from_date', ''),
            params.get('to_date', ''),
        )
    if path == '/ledger':
        params = event.get('queryStringParameters') or {}
        return _handle_ledger_data(params.get('from_date', ''), params.get('to_date', ''))
    if path == '/sales':
        return _response(200, {'data': []})
    if path == '/appendix-b/meta':
        return _handle_appendix_b_meta()
    if path == '/appendix-b/report':
        params = event.get('queryStringParameters') or {}
        return _handle_appendix_b_report(params)
    if path == '/purchases/meta':
        return _handle_purchases_meta()
    if path == '/purchases/summary':
        params = event.get('queryStringParameters') or {}
        return _handle_purchases_summary(params)
    if path == '/purchases/monthly':
        params = event.get('queryStringParameters') or {}
        return _handle_purchases_monthly(params)
    if path == '/purchases/list':
        params = event.get('queryStringParameters') or {}
        return _handle_purchases_list(params)
    if path == '/sales/meta':
        return _handle_sales_meta()
    if path == '/sales/list':
        params = event.get('queryStringParameters') or {}
        return _handle_sales_list(params)
    if path == '/customers/names':
        return _handle_customer_names()
    if path == '/customers/details':
        return _handle_customer_details()

    return _response(404, {'error': 'Not found'})


def _handle_stocks_summary():
    r = _get_redis()
    cached = r.get('iravi:stocks:summary')
    if cached:
        return _response(200, json.loads(cached))

    conn = _get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT packing_configuration, available_qty, stock_valuation,
                       brand, technical, packing_size, entry_date
                FROM snapshot_stock
                WHERE out_z IS NULL
            """)
            rows = cur.fetchall()
    finally:
        conn.close()

    total_kgs = 0.0
    total_vols = 0.0
    total_valuation = 0.0
    product_set = set()
    latest_date = None

    for packing_config, available_qty, stock_valuation, brand, technical, packing_size, entry_date in rows:
        qty = float(available_qty or 0)
        pc = packing_config or ''
        if pc == 'gms':
            total_kgs += qty
        elif pc == 'ml':
            total_vols += qty
        total_valuation += float(stock_valuation or 0)
        product_set.add((brand, technical, float(packing_size or 0), pc))
        if entry_date and (latest_date is None or entry_date > latest_date):
            latest_date = entry_date

    summary = {
        'total_kgs': round(total_kgs, 2),
        'total_vols': round(total_vols, 2),
        'stock_valuation': round(total_valuation, 2),
        'total_products': len(product_set),
        'as_of': latest_date.isoformat() if latest_date else None,
        'updated_at': datetime.now(timezone.utc).isoformat(),
    }

    r.set('iravi:stocks:summary', json.dumps(summary), ex=_REDIS_TTL)
    return _response(200, summary)


def _handle_stocks_current():
    r = _get_redis()
    cached = r.get('iravi:stocks:current')
    if cached:
        return _response(200, json.loads(cached))

    conn = _get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    brand, technical, packing_size, packing_configuration,
                    available_nos, conversion_factor, available_cases, available_qty,
                    branch, special_packing_mention, entry_date, rate, stock_valuation
                FROM snapshot_stock
                WHERE out_z IS NULL
                ORDER BY brand, technical, packing_size, branch
            """)
            col_names = [d[0] for d in cur.description]
            raw_rows = cur.fetchall()
    finally:
        conn.close()

    current = []
    for raw in raw_rows:
        row = dict(zip(col_names, raw))
        packing_size_num = float(row['packing_size'] or 0)
        packing_config = row['packing_configuration'] or ''
        current.append({
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
            'rate': float(row['rate']) if row['rate'] is not None else None,
            'stock_valuation': float(row['stock_valuation']) if row['stock_valuation'] is not None else None,
        })

    r.set('iravi:stocks:current', json.dumps(current), ex=_REDIS_TTL)
    return _response(200, current)


def _handle_ledger_range():
    r = _get_redis()
    cached = r.get('iravi:ledger:range')
    if cached:
        return _response(200, json.loads(cached))

    # Fallback to DB (Redis evicted or before first ETL run)
    conn = _get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT MIN(transaction_date), MAX(transaction_date)
                FROM customer_ledger
                WHERE out_z IS NULL
            """)
            min_date, max_date = cur.fetchone()
    finally:
        conn.close()

    payload = {
        'min_date': min_date.isoformat() if min_date else None,
        'max_date': max_date.isoformat() if max_date else None,
    }
    if min_date:
        r.set('iravi:ledger:range', json.dumps(payload), ex=_REDIS_TTL)
    return _response(200, payload)


def _handle_ledger_data(from_date: str, to_date: str):
    if not from_date or not to_date:
        return _response(400, {'error': 'from_date and to_date are required'})

    cache_key = f'iravi:ledger:data:{from_date}:{to_date}'
    r = _get_redis()
    cached = r.get(cache_key)
    if cached:
        return _response(200, json.loads(cached))

    conn = _get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT transaction_date, voucher_no, account_name, category, sub_category, amount
                FROM customer_ledger
                WHERE out_z IS NULL
                  AND transaction_date BETWEEN %(from_date)s AND %(to_date)s
                ORDER BY transaction_date, account_name, voucher_no
            """, {'from_date': from_date, 'to_date': to_date})
            col_names = [d[0] for d in cur.description]
            raw_rows = cur.fetchall()
    finally:
        conn.close()

    rows = []
    for raw in raw_rows:
        row = dict(zip(col_names, raw))
        rows.append({
            'transaction_date': row['transaction_date'].isoformat(),
            'voucher_no': row['voucher_no'],
            'account_name': row['account_name'],
            'category': row['category'],
            'sub_category': row['sub_category'],
            'amount': float(row['amount']),
        })

    r.set(cache_key, json.dumps(rows), ex=_LEDGER_TTL)
    logger.info('Ledger data cached: key=%s rows=%d', cache_key, len(rows))
    return _response(200, rows)


def _handle_ledger_outstanding(to_date: str):
    """Cumulative outstanding as of to_date: sum(all Db) - sum(all Cr) from beginning of time."""
    if not to_date:
        return _response(400, {'error': 'to_date is required'})

    cache_key = f'iravi:ledger:outstanding:{to_date}'
    r = _get_redis()
    cached = r.get(cache_key)
    if cached:
        return _response(200, json.loads(cached))

    conn = _get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    COALESCE(SUM(CASE WHEN category = 'Db' THEN amount ELSE 0 END), 0) -
                    COALESCE(SUM(CASE WHEN category = 'Cr' THEN amount ELSE 0 END), 0)
                FROM customer_ledger
                WHERE out_z IS NULL
                  AND transaction_date <= %(to_date)s
                  AND LOWER(account_name) NOT LIKE '%%iravi%%'
            """, {'to_date': to_date})
            row = cur.fetchone()
    finally:
        conn.close()

    payload = {'outstanding': float(row[0] or 0)}
    r.set(cache_key, json.dumps(payload), ex=_LEDGER_TTL)
    return _response(200, payload)


def _handle_ledger_statement(account_name: str, from_date: str, to_date: str):
    if not account_name or not from_date or not to_date:
        return _response(400, {'error': 'account_name, from_date, and to_date are required'})

    cache_key = f'iravi:ledger:statement:{account_name}:{from_date}:{to_date}'
    r = _get_redis()
    cached = r.get(cache_key)
    if cached:
        return _response(200, json.loads(cached))

    conn = _get_db_conn()
    try:
        with conn.cursor() as cur:
            # Opening balance: all transactions strictly before from_date
            cur.execute("""
                SELECT COALESCE(
                    SUM(CASE WHEN category = 'Db' THEN amount ELSE -amount END), 0
                )
                FROM customer_ledger
                WHERE out_z IS NULL
                  AND account_name = %(account_name)s
                  AND transaction_date < %(from_date)s
            """, {'account_name': account_name, 'from_date': from_date})
            opening_balance = float(cur.fetchone()[0])

            # Period transactions grouped by voucher, determine primary sub_category
            cur.execute("""
                SELECT
                    transaction_date,
                    voucher_no,
                    MAX(CASE WHEN sub_category NOT IN ('CGST', 'SGST', 'IGST', 'Roundoff')
                        THEN sub_category END) AS primary_type,
                    COALESCE(SUM(amount) FILTER (WHERE category = 'Db'), 0) AS debit,
                    COALESCE(SUM(amount) FILTER (WHERE category = 'Cr'), 0) AS credit
                FROM customer_ledger
                WHERE out_z IS NULL
                  AND account_name = %(account_name)s
                  AND transaction_date BETWEEN %(from_date)s AND %(to_date)s
                GROUP BY transaction_date, voucher_no
                ORDER BY transaction_date ASC, voucher_no ASC
            """, {'account_name': account_name, 'from_date': from_date, 'to_date': to_date})
            col_names = [d[0] for d in cur.description]
            raw_rows = cur.fetchall()
    finally:
        conn.close()

    total_debit = 0.0
    total_credit = 0.0
    rows = []
    for raw in raw_rows:
        row = dict(zip(col_names, raw))
        debit = float(row['debit'])
        credit = float(row['credit'])
        total_debit += debit
        total_credit += credit
        rows.append({
            'transaction_date': row['transaction_date'].isoformat(),
            'voucher_no': row['voucher_no'],
            'transaction_type': row['primary_type'],
            'debit': round(debit, 2),
            'credit': round(credit, 2),
        })

    closing_balance = round(opening_balance + total_debit - total_credit, 2)

    payload = {
        'account_name': account_name,
        'from_date': from_date,
        'to_date': to_date,
        'opening_balance': round(opening_balance, 2),
        'rows': rows,
        'total_debit': round(total_debit, 2),
        'total_credit': round(total_credit, 2),
        'closing_balance': closing_balance,
    }
    r.set(cache_key, json.dumps(payload), ex=_LEDGER_TTL)
    logger.info('Ledger statement cached: account=%s %s→%s rows=%d',
                account_name, from_date, to_date, len(rows))
    return _response(200, payload)


def _handle_appendix_b_meta():
    r = _get_redis()
    cached = r.get('iravi:appendix_b:meta')
    if cached:
        return _response(200, json.loads(cached))

    conn = _get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT customer_name FROM customer_details ORDER BY customer_name')
            customers = [row[0] for row in cur.fetchall()]

            cur.execute("""
                SELECT DISTINCT branch FROM appendix_b_x11_stock_ledger
                WHERE out_z IS NULL AND branch IS NOT NULL ORDER BY branch
            """)
            branches = [row[0] for row in cur.fetchall()]

            cur.execute("""
                SELECT DISTINCT technical_name FROM appendix_b_x11_stock_ledger
                WHERE out_z IS NULL ORDER BY technical_name
            """)
            technical_names = [row[0] for row in cur.fetchall()]

            cur.execute("""
                SELECT MIN(purchase_date), MAX(purchase_date)
                FROM appendix_b_x11_stock_ledger WHERE out_z IS NULL
            """)
            min_date, max_date = cur.fetchone()
    finally:
        conn.close()

    payload = {
        'customers': customers,
        'branches': branches,
        'technical_names': technical_names,
        'min_date': min_date.isoformat() if min_date else None,
        'max_date': max_date.isoformat() if max_date else None,
    }
    if min_date:
        r.set('iravi:appendix_b:meta', json.dumps(payload), ex=_APPENDIX_B_TTL)
    return _response(200, payload)


def _handle_appendix_b_report(params: dict):
    branch = (params.get('branch') or '').strip()
    technical_name = (params.get('technical_name') or '').strip()
    from_date = (params.get('from_date') or '').strip()
    to_date = (params.get('to_date') or '').strip()

    if not all([branch, technical_name, from_date, to_date]):
        return _response(400, {'error': 'branch, technical_name, from_date, to_date are required'})

    cache_key = f'iravi:appendix_b:report:{branch}:{technical_name}:{from_date}:{to_date}'
    r = _get_redis()
    cached = r.get(cache_key)
    if cached:
        return _response(200, json.loads(cached))

    conn = _get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT customer_name FROM customer_details')
            customer_set = {row[0] for row in cur.fetchall()}

            # Balance brought forward: net qty strictly before the window
            cur.execute("""
                SELECT COALESCE(
                    SUM(CASE WHEN in_out = 'In' THEN qty ELSE -qty END), 0
                )
                FROM appendix_b_x11_stock_ledger
                WHERE out_z IS NULL
                  AND branch = %(branch)s
                  AND technical_name = %(technical_name)s
                  AND purchase_date < %(from_date)s
            """, {'branch': branch, 'technical_name': technical_name, 'from_date': from_date})
            bf_qty = float(cur.fetchone()[0])

            cur.execute("""
                SELECT purchase_date, iravi_voucher, supplier_voucher, party,
                       barcode, mdf_date, exp_date, in_out, qty
                FROM appendix_b_x11_stock_ledger
                WHERE out_z IS NULL
                  AND branch = %(branch)s
                  AND technical_name = %(technical_name)s
                  AND purchase_date BETWEEN %(from_date)s AND %(to_date)s
                ORDER BY purchase_date, id
            """, {'branch': branch, 'technical_name': technical_name,
                  'from_date': from_date, 'to_date': to_date})
            col_names = [d[0] for d in cur.description]
            raw_rows = cur.fetchall()
    finally:
        conn.close()

    running_bal = bf_qty
    current_mfg_name = None
    result_rows = []

    for sno, raw in enumerate(raw_rows, start=1):
        row = dict(zip(col_names, raw))
        party = row['party'] or ''
        in_out = row['in_out']
        qty = float(row['qty'] or 0)
        is_customer = party in customer_set
        row_bf = running_bal  # opening balance for this row

        # Track the supplier (last 'In' from a non-customer party)
        if in_out == 'In' and not is_customer:
            current_mfg_name = party

        # Purchase → supplier voucher; all other scenarios → iravi voucher
        inv_no = row['supplier_voucher'] if (in_out == 'In' and not is_customer) else row['iravi_voucher']

        if in_out == 'In':
            recd, sold = qty, None
            running_bal += qty
        else:
            recd, sold = None, qty
            running_bal -= qty

        # REMARKS: customer name on outgoing (sales) rows
        remarks = party if (in_out == 'Out' and is_customer) else None

        result_rows.append({
            'sno': sno,
            'recd_date': row['purchase_date'].isoformat(),
            'mfg_name': current_mfg_name,
            'inv_no': inv_no,
            'barcode': row['barcode'],
            'mfg': row['mdf_date'].isoformat() if row['mdf_date'] else None,
            'exp': row['exp_date'].isoformat() if row['exp_date'] else None,
            'bf': round(row_bf, 3),
            'recd': recd,
            'sold': sold,
            'bal': round(running_bal, 3),
            'remarks': remarks,
        })

    payload = {'bf_qty': round(bf_qty, 3), 'rows': result_rows}
    r.set(cache_key, json.dumps(payload), ex=_APPENDIX_B_TTL)
    logger.info('Appendix-B report cached: branch=%s tech=%s %s→%s rows=%d',
                branch, technical_name, from_date, to_date, len(result_rows))
    return _response(200, payload)


def _handle_purchases_meta():
    r = _get_redis()
    cached = r.get('iravi:purchases:meta')
    if cached:
        return _response(200, json.loads(cached))

    conn = _get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT branch FROM purchases
                WHERE out_z IS NULL ORDER BY branch
            """)
            branches = [row[0] for row in cur.fetchall()]

            cur.execute("""
                SELECT MIN(purchase_date), MAX(purchase_date)
                FROM purchases WHERE out_z IS NULL
            """)
            min_date, max_date = cur.fetchone()
    finally:
        conn.close()

    payload = {
        'branches': branches,
        'min_date': min_date.isoformat() if min_date else None,
        'max_date': max_date.isoformat() if max_date else None,
    }
    if min_date:
        r.set('iravi:purchases:meta', json.dumps(payload), ex=_PURCHASES_TTL)
    return _response(200, payload)


def _handle_purchases_summary(params: dict):
    branch = (params.get('branch') or '').strip()
    from_date = (params.get('from_date') or '').strip()
    to_date = (params.get('to_date') or '').strip()
    exclude_internal = params.get('exclude_internal', 'false').lower() == 'true'

    if not from_date or not to_date:
        return _response(400, {'error': 'from_date and to_date are required'})

    ei_suffix = ':ei' if exclude_internal else ''
    cache_key = f'iravi:purchases:summary:{branch or "all"}:{from_date}:{to_date}{ei_suffix}'
    r = _get_redis()
    cached = r.get(cache_key)
    if cached:
        return _response(200, json.loads(cached))

    conn = _get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    COUNT(DISTINCT (purchase_date, voucher_no, branch, party))
                        FILTER (WHERE purchase_return = 'N') AS total_purchase_invoices,
                    COUNT(DISTINCT (purchase_date, voucher_no, branch, party))
                        FILTER (WHERE purchase_return = 'Y') AS total_return_invoices,
                    COALESCE(SUM(av) FILTER (WHERE purchase_return = 'N' AND strpos(product, '%%') > 0), 0)
                        AS total_technical_purchase,
                    COALESCE(SUM(av) FILTER (WHERE purchase_return = 'N' AND strpos(product, '%%') = 0), 0)
                        AS total_non_technical_purchase,
                    COALESCE(SUM(av) FILTER (WHERE purchase_return = 'Y' AND strpos(product, '%%') > 0), 0)
                        AS total_technical_returns,
                    COALESCE(SUM(av) FILTER (WHERE purchase_return = 'Y' AND strpos(product, '%%') = 0), 0)
                        AS total_non_technical_returns
                FROM purchases
                WHERE out_z IS NULL
                  AND purchase_date BETWEEN %(from_date)s AND %(to_date)s
                  AND (%(branch)s = '' OR branch = %(branch)s)
                  AND (NOT %(exclude_internal)s OR LOWER(party) NOT LIKE '%%iravi%%')
            """, {'from_date': from_date, 'to_date': to_date, 'branch': branch,
                  'exclude_internal': exclude_internal})
            (total_purchase_invoices, total_return_invoices,
             total_technical_purchase, total_non_technical_purchase,
             total_technical_returns, total_non_technical_returns) = cur.fetchone()
    finally:
        conn.close()

    payload = {
        'total_purchase_invoices': int(total_purchase_invoices),
        'total_return_invoices': int(total_return_invoices),
        'total_technical_purchase': float(total_technical_purchase),
        'total_non_technical_purchase': float(total_non_technical_purchase),
        'total_technical_returns': float(total_technical_returns),
        'total_non_technical_returns': float(total_non_technical_returns),
    }
    r.set(cache_key, json.dumps(payload), ex=_PURCHASES_TTL)
    logger.info('Purchases summary cached: branch=%s %s→%s', branch or 'all', from_date, to_date)
    return _response(200, payload)


def _handle_purchases_monthly(params: dict):
    branch = (params.get('branch') or '').strip()
    from_date = (params.get('from_date') or '').strip()
    to_date = (params.get('to_date') or '').strip()

    if not from_date or not to_date:
        return _response(400, {'error': 'from_date and to_date are required'})

    cache_key = f'iravi:purchases:monthly:{branch or "all"}:{from_date}:{to_date}'
    r = _get_redis()
    cached = r.get(cache_key)
    if cached:
        return _response(200, json.loads(cached))

    conn = _get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT to_char(purchase_date, 'YYYY-MM') AS month,
                       COALESCE(SUM(av) FILTER (WHERE purchase_return = 'N'), 0) AS total_purchases,
                       COALESCE(SUM(av) FILTER (WHERE purchase_return = 'Y'), 0) AS total_returns
                FROM purchases
                WHERE out_z IS NULL
                  AND purchase_date BETWEEN %(from_date)s AND %(to_date)s
                  AND (%(branch)s = '' OR branch = %(branch)s)
                GROUP BY month
                ORDER BY month
            """, {'from_date': from_date, 'to_date': to_date, 'branch': branch})
            rows = [
                {'month': month, 'total_purchases': float(purchases), 'total_returns': float(returns)}
                for month, purchases, returns in cur.fetchall()
            ]
    finally:
        conn.close()

    payload = {'rows': rows}
    r.set(cache_key, json.dumps(payload), ex=_PURCHASES_TTL)
    logger.info('Purchases monthly cached: branch=%s %s→%s', branch or 'all', from_date, to_date)
    return _response(200, payload)


def _handle_purchases_list(params: dict):
    branch = (params.get('branch') or '').strip()
    from_date = (params.get('from_date') or '').strip()
    to_date = (params.get('to_date') or '').strip()

    if not from_date or not to_date:
        return _response(400, {'error': 'from_date and to_date are required'})

    cache_key = f'iravi:purchases:list:{branch or "all"}:{from_date}:{to_date}'
    r = _get_redis()
    cached = r.get(cache_key)
    if cached:
        return _response(200, json.loads(cached))

    conn = _get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT purchase_date, voucher_no, branch, party, product, qty, rate, av, purchase_return
                FROM purchases
                WHERE out_z IS NULL
                  AND purchase_date BETWEEN %(from_date)s AND %(to_date)s
                  AND (%(branch)s = '' OR branch = %(branch)s)
                ORDER BY purchase_date DESC, voucher_no
            """, {'from_date': from_date, 'to_date': to_date, 'branch': branch})
            col_names = [d[0] for d in cur.description]
            raw_rows = cur.fetchall()
    finally:
        conn.close()

    rows = []
    for raw in raw_rows:
        row = dict(zip(col_names, raw))
        rows.append({
            'purchase_date': row['purchase_date'].isoformat(),
            'voucher_no': row['voucher_no'],
            'branch': row['branch'],
            'party': row['party'],
            'product': row['product'],
            'qty': float(row['qty']) if row['qty'] is not None else None,
            'rate': float(row['rate']) if row['rate'] is not None else None,
            'av': float(row['av']) if row['av'] is not None else None,
            'purchase_return': row['purchase_return'],
        })

    r.set(cache_key, json.dumps(rows), ex=_PURCHASES_TTL)
    logger.info('Purchases list cached: branch=%s %s→%s rows=%d', branch or 'all', from_date, to_date, len(rows))
    return _response(200, rows)


def _handle_sales_meta():
    r = _get_redis()
    cached = r.get('iravi:sales:meta')
    if cached:
        return _response(200, json.loads(cached))

    conn = _get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT branch FROM sales
                WHERE out_z IS NULL ORDER BY branch
            """)
            branches = [row[0] for row in cur.fetchall()]

            cur.execute("""
                SELECT MIN(purchase_date), MAX(purchase_date)
                FROM sales WHERE out_z IS NULL
            """)
            min_date, max_date = cur.fetchone()
    finally:
        conn.close()

    payload = {
        'branches': branches,
        'min_date': min_date.isoformat() if min_date else None,
        'max_date': max_date.isoformat() if max_date else None,
    }
    if min_date:
        r.set('iravi:sales:meta', json.dumps(payload), ex=_SALES_TTL)
    return _response(200, payload)


def _handle_sales_list(params: dict):
    branch = (params.get('branch') or '').strip()
    from_date = (params.get('from_date') or '').strip()
    to_date = (params.get('to_date') or '').strip()

    if not from_date or not to_date:
        return _response(400, {'error': 'from_date and to_date are required'})

    cache_key = f'iravi:sales:list:{branch or "all"}:{from_date}:{to_date}'
    r = _get_redis()
    cached = r.get(cache_key)
    if cached:
        return _response(200, json.loads(cached))

    conn = _get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT purchase_date, voucher_no, branch, party, product, qty, rate, av, sales_return
                FROM sales
                WHERE out_z IS NULL
                  AND purchase_date BETWEEN %(from_date)s AND %(to_date)s
                  AND (%(branch)s = '' OR branch = %(branch)s)
                ORDER BY purchase_date DESC, voucher_no
            """, {'from_date': from_date, 'to_date': to_date, 'branch': branch})
            col_names = [d[0] for d in cur.description]
            raw_rows = cur.fetchall()
    finally:
        conn.close()

    rows = []
    for raw in raw_rows:
        row = dict(zip(col_names, raw))
        rows.append({
            'purchase_date': row['purchase_date'].isoformat(),
            'voucher_no': row['voucher_no'],
            'branch': row['branch'],
            'party': row['party'],
            'product': row['product'],
            'qty': float(row['qty']) if row['qty'] is not None else None,
            'rate': float(row['rate']) if row['rate'] is not None else None,
            'av': float(row['av']) if row['av'] is not None else None,
            'sales_return': row['sales_return'],
        })

    r.set(cache_key, json.dumps(rows), ex=_SALES_TTL)
    logger.info('Sales list cached: branch=%s %s→%s rows=%d', branch or 'all', from_date, to_date, len(rows))
    return _response(200, rows)


def _handle_customer_names():
    r = _get_redis()
    cached = r.get('iravi:customers:names')
    if cached:
        return _response(200, json.loads(cached))

    conn = _get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT DISTINCT customer_name FROM customer_details ORDER BY customer_name')
            names = [row[0] for row in cur.fetchall()]
    finally:
        conn.close()

    r.set('iravi:customers:names', json.dumps(names), ex=_SALES_TTL)
    return _response(200, names)


def _handle_customer_details():
    r = _get_redis()
    cached = r.get('iravi:customers:details')
    if cached:
        return _response(200, json.loads(cached))

    conn = _get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT customer_name, city FROM customer_details ORDER BY customer_name')
            details = [{'customer_name': row[0], 'city': row[1]} for row in cur.fetchall()]
    finally:
        conn.close()

    r.set('iravi:customers:details', json.dumps(details), ex=_SALES_TTL)
    return _response(200, details)


def _handle_notify(body_str: str) -> dict:
    import base64

    try:
        body = json.loads(body_str or '{}')
    except json.JSONDecodeError:
        return _response(400, {'error': 'Invalid JSON body'})

    customer_name = (body.get('customer_name') or '').strip()
    pdf_base64 = (body.get('pdf_base64') or '').strip()

    if not customer_name or not pdf_base64:
        return _response(400, {'error': 'customer_name and pdf_base64 are required'})

    try:
        pdf_bytes = base64.b64decode(pdf_base64)
    except Exception:
        return _response(400, {'error': 'Invalid pdf_base64'})

    safe_name = ''.join(
        c if c.isalnum() or c in '-_' else '_'
        for c in customer_name.replace(' ', '_')
    )[:80]
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    s3_key = f'notifications/pending/{timestamp}_{safe_name}.pdf'

    s3.put_object(
        Bucket=_DATA_BUCKET,
        Key=s3_key,
        Body=pdf_bytes,
        ContentType='application/pdf',
        Metadata={'customer_name': customer_name},
    )
    logger.info('Notification queued: %s → s3://%s/%s', customer_name, _DATA_BUCKET, s3_key)
    return _response(200, {'key': s3_key, 'message': 'Notification queued'})


def _response(status: int, body) -> dict:
    return {
        'statusCode': status,
        'headers': {'Content-Type': 'application/json'},
        'body': json.dumps(body),
    }
