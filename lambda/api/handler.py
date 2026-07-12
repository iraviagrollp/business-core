import json
import logging
import os
from datetime import datetime, timezone

import boto3
import psycopg2
import redis

import auth
import alerts_eval
import customer_balances_fy as _cbfy
import supplier_balances_fy as _sbfy
import monthly_sales

logger = logging.getLogger()
logger.setLevel(logging.INFO)

secrets = boto3.client('secretsmanager')
s3 = boto3.client('s3')

_DATA_BUCKET = os.environ.get('DATA_BUCKET', '')

# Credit-note sub-category identifier used to split the credit bucket in the FY report.
# Change this constant if the sub_category value ever changes in customer_ledger.
_CREDIT_NOTE_SUBCATEGORY = 'Customer Credit Notes'

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

    # Auth + RBAC admin namespace (login is public; everything else is guarded).
    if path.startswith('/auth/') or path.startswith('/admin/'):
        try:
            return _route_auth_admin(event, method, path)
        except auth.AuthError as exc:
            return _response(exc.status, {'error': exc.message})

    # Alerts — admin-only CRUD + field catalog + test endpoint.
    if path.startswith('/alerts'):
        try:
            return _route_alerts(event, method, path)
        except auth.AuthError as exc:
            return _response(exc.status, {'error': exc.message})

    # Monthly Sale Targets config — admin-only GET + POST.
    if path.startswith('/config/'):
        try:
            return _route_config(event, method, path)
        except auth.AuthError as exc:
            return _response(exc.status, {'error': exc.message})

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
    if path == '/supplier-ledger/range':
        return _handle_supplier_ledger_range()
    if path == '/supplier-ledger':
        params = event.get('queryStringParameters') or {}
        return _handle_supplier_ledger_data(params.get('from_date', ''), params.get('to_date', ''))
    if path == '/supplier-ledger/statement':
        params = event.get('queryStringParameters') or {}
        return _handle_supplier_ledger_statement(
            params.get('account_name', ''),
            params.get('from_date', ''),
            params.get('to_date', ''),
        )
    if path == '/suppliers/details':
        return _handle_supplier_details()
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
    if path == '/reports/customer-balances-fy':
        params = event.get('queryStringParameters') or {}
        return _handle_customer_balances_fy(params.get('fy_count', 'all'))
    if path == '/reports/supplier-balances-fy':
        params = event.get('queryStringParameters') or {}
        return _handle_supplier_balances_fy(params.get('fy_count', 'all'))
    if path == '/reports/monthly-sales':
        params = event.get('queryStringParameters') or {}
        return _handle_monthly_sales(params.get('month', ''))

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


def _handle_supplier_ledger_range():
    """Min/max transaction_date across supplier_ledger (open rows only).

    Mirror of _handle_ledger_range on the supplier_ledger table. Unlike the
    customer range there is no redis_updater step that pre-populates this key,
    so a cache miss always falls through to RDS and then caches the result.
    """
    cache_key = 'iravi:supplier_ledger:range'
    r = _get_redis()
    cached = r.get(cache_key)
    if cached:
        return _response(200, json.loads(cached))

    conn = _get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT MIN(transaction_date), MAX(transaction_date)
                FROM supplier_ledger
                WHERE out_z IS NULL
                  AND LOWER(account_name) NOT LIKE '%%iravi%%'
            """)
            min_date, max_date = cur.fetchone()
    finally:
        conn.close()

    payload = {
        'min_date': min_date.isoformat() if min_date else None,
        'max_date': max_date.isoformat() if max_date else None,
    }
    if min_date:
        r.set(cache_key, json.dumps(payload), ex=_REDIS_TTL)
    return _response(200, payload)


def _handle_supplier_ledger_data(from_date: str, to_date: str):
    """Raw supplier_ledger transaction rows in a date range (for AP aging).

    Mirror of _handle_ledger_data on the supplier_ledger table. Returns the same
    row shape (transaction_date, voucher_no, account_name, category, sub_category,
    amount) so the client-side aging engine can bucket credits by transaction_date.
    """
    if not from_date or not to_date:
        return _response(400, {'error': 'from_date and to_date are required'})

    cache_key = f'iravi:supplier_ledger:data:{from_date}:{to_date}'
    r = _get_redis()
    cached = r.get(cache_key)
    if cached:
        return _response(200, json.loads(cached))

    conn = _get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT transaction_date, voucher_no, account_name, category, sub_category, amount
                FROM supplier_ledger
                WHERE out_z IS NULL
                  AND LOWER(account_name) NOT LIKE '%%iravi%%'
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
    logger.info('Supplier ledger data cached: key=%s rows=%d', cache_key, len(rows))
    return _response(200, rows)


def _handle_supplier_ledger_statement(account_name: str, from_date: str, to_date: str):
    """Per-voucher account statement for one supplier over a date range.

    Exact mirror of _handle_ledger_statement on the supplier_ledger table:
    opening balance = Σ(Db − Cr) strictly before from_date; period rows grouped
    by voucher with the two sides netted (roundoff/GST absorbed); running balance
    is Σ(Db − Cr). Sign convention is the raw ledger one (Db positive), identical
    to the customer statement; the UI applies the supplier Dr/Cr color swap.
    """
    if not account_name or not from_date or not to_date:
        return _response(400, {'error': 'account_name, from_date, and to_date are required'})

    cache_key = f'iravi:supplier_ledger:statement:{account_name}:{from_date}:{to_date}'
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
                FROM supplier_ledger
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
                FROM supplier_ledger
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
        raw_debit = float(row['debit'])
        raw_credit = float(row['credit'])
        # Net the two sides so roundoff/GST sub-components are absorbed into the
        # voucher they belong to.  The voucher shows on only one side; the running
        # balance is numerically unchanged because net = raw_debit − raw_credit.
        net = raw_debit - raw_credit
        if net >= 0:
            debit, credit = net, 0.0
        else:
            debit, credit = 0.0, -net
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
    logger.info('Supplier ledger statement cached: account=%s %s→%s rows=%d',
                account_name, from_date, to_date, len(rows))
    return _response(200, payload)


def _handle_supplier_details():
    """Supplier name + city lookup from supplier_accounts (open rows only).

    Mirror of _handle_customer_details on the supplier_accounts table. Keyed on
    supplier_name so the UI can join a city onto each aged supplier balance.
    """
    r = _get_redis()
    cached = r.get('iravi:suppliers:details')
    if cached:
        return _response(200, json.loads(cached))

    conn = _get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT name, city FROM supplier_accounts
                WHERE out_z IS NULL
                ORDER BY name
            """)
            details = [{'supplier_name': row[0], 'city': row[1]} for row in cur.fetchall()]
    finally:
        conn.close()

    r.set('iravi:suppliers:details', json.dumps(details), ex=_SALES_TTL)
    return _response(200, details)


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
        raw_debit = float(row['debit'])
        raw_credit = float(row['credit'])
        # Net the two sides so roundoff/GST sub-components are absorbed into the
        # voucher they belong to.  The voucher shows on only one side; the running
        # balance is numerically unchanged because net = raw_debit − raw_credit.
        net = raw_debit - raw_credit
        if net >= 0:
            debit, credit = net, 0.0
        else:
            debit, credit = 0.0, -net
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
            cur.execute('SELECT DISTINCT customer_name FROM customer_details WHERE out_z IS NULL ORDER BY customer_name')
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
            cur.execute('SELECT customer_name, city FROM customer_details WHERE out_z IS NULL ORDER BY customer_name')
            details = [{'customer_name': row[0], 'city': row[1]} for row in cur.fetchall()]
    finally:
        conn.close()

    r.set('iravi:customers:details', json.dumps(details), ex=_SALES_TTL)
    return _response(200, details)


def _handle_customer_balances_fy(fy_count_raw: str):
    """Per-customer, multi-FY roll-forward of debits/credits with running balances.

    Computation is delegated to customer_balances_fy.compute_customer_balances_fy
    (shared with the alerts_evaluator PDF path).  This wrapper owns:
      - fy_count param parsing
      - Redis cache-aside (key iravi:reports:customer_balances_fy:{fy_count})
      - _response wrapping

    fy_count=all  -> all FYs in the data, opening balance = 0 for every party.
    fy_count=2|3|4 -> most recent N FYs; first shown FY gets a brought-forward opening.
    """
    # Parse fy_count param
    fy_count: int | str = 'all'
    if fy_count_raw and fy_count_raw != 'all':
        try:
            n = int(fy_count_raw)
            if n >= 1:
                fy_count = n
            # If < 1 we fall back to 'all'
        except (ValueError, TypeError):
            pass  # invalid string -> default to 'all'

    cache_key = f'iravi:reports:customer_balances_fy:{fy_count}'
    r = _get_redis()
    cached = r.get(cache_key)
    if cached:
        return _response(200, json.loads(cached))

    conn = _get_db_conn()
    try:
        payload = _cbfy.compute_customer_balances_fy(conn, fy_count)
    finally:
        conn.close()

    r.set(cache_key, json.dumps(payload), ex=_LEDGER_TTL)
    logger.info('Customer balances FY cached: fy_count=%s fys=%d parties=%d',
                fy_count, len(payload['fys']), len(payload['rows']))
    return _response(200, payload)


def _handle_supplier_balances_fy(fy_count_raw: str):
    """Per-supplier, multi-FY roll-forward of debits/credits with running balances.

    Computation is delegated to supplier_balances_fy.compute_supplier_balances_fy
    (shared with the alerts_evaluator PDF path).  This wrapper owns:
      - fy_count param parsing
      - Redis cache-aside (key iravi:reports:supplier_balances_fy:{fy_count})
      - _response wrapping

    fy_count=all  -> all FYs in the data, opening balance = 0 for every party.
    fy_count=2|3|4 -> most recent N FYs; first shown FY gets a brought-forward opening.

    Key differences from _handle_customer_balances_fy:
    - Reads supplier_ledger / supplier_accounts instead of customer_ledger / customer_details.
    - No credit-note split: per-voucher net > 0 -> debit, net < 0 -> credit, net == 0 -> nothing.
    - No code column: supplier_accounts has no party code; response omits 'code'.
    - Sort order: party name ascending (no code to sort by).
    - Cache key: iravi:reports:supplier_balances_fy:{fy_count}.
    """
    # Parse fy_count param
    fy_count: int | str = 'all'
    if fy_count_raw and fy_count_raw != 'all':
        try:
            n = int(fy_count_raw)
            if n >= 1:
                fy_count = n
            # If < 1 we fall back to 'all'
        except (ValueError, TypeError):
            pass  # invalid string -> default to 'all'

    cache_key = f'iravi:reports:supplier_balances_fy:{fy_count}'
    r = _get_redis()
    cached = r.get(cache_key)
    if cached:
        return _response(200, json.loads(cached))

    conn = _get_db_conn()
    try:
        payload = _sbfy.compute_supplier_balances_fy(conn, fy_count)
    finally:
        conn.close()

    r.set(cache_key, json.dumps(payload), ex=_LEDGER_TTL)
    logger.info('Supplier balances FY cached: fy_count=%s fys=%d parties=%d',
                fy_count, len(payload['fys']), len(payload['rows']))
    return _response(200, payload)


def _handle_monthly_sales(month_raw: str):
    """GET /reports/monthly-sales?month=YYYY-MM

    Delegates computation to monthly_sales.compute_monthly_sales() so the API
    and the alerts-evaluator PDF share one implementation.

    State mapping by branch:
      'Guntur C & F' → andhra
      'Auto Nagar'   → telangana
      anything else  → excluded from totals; name collected into unmapped_branches

    Returns raw rupees (float, 2 dp). The UI converts to lakhs.
    Cache key: iravi:reports:monthly_sales:v2:{month}  TTL: _LEDGER_TTL
    (v2 — bumped 2026-07-11 when targets/YoY comparison keys were added to the
    payload shape, so stale old-shape cache entries never collide with it.)
    """
    from datetime import timedelta as _timedelta

    # Today in IST (UTC+5:30)
    _IST = timezone(_timedelta(hours=5, minutes=30))
    today_ist = datetime.now(_IST).date()

    # Parse ?month=YYYY-MM; fall back to current IST month on absent/invalid input.
    month_str = (month_raw or '').strip()
    try:
        parsed_month = datetime.strptime(month_str, '%Y-%m')
        month_str = parsed_month.strftime('%Y-%m')   # normalise (strips any trailing chars)
    except (ValueError, AttributeError):
        month_str = today_ist.strftime('%Y-%m')

    cache_key = f'iravi:reports:monthly_sales:v2:{month_str}'
    r = _get_redis()
    cached = r.get(cache_key)
    if cached:
        return _response(200, json.loads(cached))

    conn = _get_db_conn()
    try:
        payload = monthly_sales.compute_monthly_sales(conn, month_str)
    finally:
        conn.close()

    r.set(cache_key, json.dumps(payload), ex=_LEDGER_TTL)
    logger.info('Monthly sales cached: month=%s as_on=%s grand=%.2f',
                month_str, payload['as_on_date'], payload['grand_total']['total'])
    return _response(200, payload)


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


# ══════════════════════════════════════════════════════════════════════════════
# CONFIG — Monthly Sale Targets (admin-only)
#
# Table monthly_sale_targets (unitemporal milestoning), natural key (state, month, yr):
#   id BIGSERIAL PK, state VARCHAR(10), month SMALLINT, yr SMALLINT,
#   target_lakhs NUMERIC(14,2), in_z TIMESTAMPTZ NOT NULL DEFAULT NOW(), out_z TIMESTAMPTZ
# ══════════════════════════════════════════════════════════════════════════════

def _route_config(event, method, path):
    if path == '/config/monthly-targets':
        if method == 'GET':
            return _handle_config_monthly_targets_get(event)
        if method == 'POST':
            return _handle_config_monthly_targets_post(event)
        return _response(405, {'error': 'Method not allowed'})
    return _response(404, {'error': 'Not found'})


def _handle_config_monthly_targets_get(event):
    """GET /config/monthly-targets?yr=YYYY — admin-only.

    years = all distinct yr from active rows (out_z IS NULL), ordered DESC.
    yr = query param if provided, else most recent year in years, else current
    calendar year.
    rows = active rows for that yr, ordered by state, month.
    """
    conn = _get_db_conn()
    try:
        with conn.cursor() as cur:
            _require_admin(event, cur)

            cur.execute("""
                SELECT DISTINCT yr FROM monthly_sale_targets
                WHERE out_z IS NULL
                ORDER BY yr DESC
            """)
            years = [r[0] for r in cur.fetchall()]

            params = event.get('queryStringParameters') or {}
            yr_raw = (params.get('yr') or '').strip()
            yr = None
            if yr_raw:
                try:
                    yr = int(yr_raw)
                except (ValueError, TypeError):
                    yr = None
            if yr is None:
                yr = years[0] if years else datetime.now().year

            cur.execute("""
                SELECT state, month, yr, target_lakhs
                FROM monthly_sale_targets
                WHERE out_z IS NULL AND yr = %s
                ORDER BY state, month
            """, (yr,))
            rows = [
                {'state': r[0], 'month': r[1], 'yr': r[2], 'target_lakhs': float(r[3])}
                for r in cur.fetchall()
            ]
    finally:
        conn.close()
    return _response(200, {'yr': yr, 'years': years, 'rows': rows})


def _handle_config_monthly_targets_post(event):
    """POST /config/monthly-targets — admin-only; milestoning upsert on (state, month, yr)."""
    body = _json_body(event)

    state = (body.get('state') or '').strip().upper()
    if state not in ('AP', 'TG'):
        return _response(400, {'error': "state must be 'AP' or 'TG'"})

    try:
        month = int(body.get('month'))
    except (TypeError, ValueError):
        return _response(400, {'error': 'month must be an integer 1-12'})
    if month < 1 or month > 12:
        return _response(400, {'error': 'month must be an integer 1-12'})

    try:
        yr = int(body.get('yr'))
    except (TypeError, ValueError):
        return _response(400, {'error': 'yr must be an integer'})
    if yr < 2000 or yr > 2100:
        return _response(400, {'error': 'yr must be between 2000 and 2100'})

    try:
        target_lakhs = float(body.get('target_lakhs'))
    except (TypeError, ValueError):
        return _response(400, {'error': 'target_lakhs must be numeric'})
    if target_lakhs < 0:
        return _response(400, {'error': 'target_lakhs must be >= 0'})

    conn = _get_db_conn()
    try:
        with conn.cursor() as cur:
            _require_admin(event, cur)
            cur.execute("""
                UPDATE monthly_sale_targets
                SET out_z = NOW()
                WHERE state = %s AND month = %s AND yr = %s AND out_z IS NULL
            """, (state, month, yr))
            cur.execute("""
                INSERT INTO monthly_sale_targets (state, month, yr, target_lakhs)
                VALUES (%s, %s, %s, %s)
            """, (state, month, yr, target_lakhs))
            conn.commit()
    finally:
        conn.close()
    return _response(200, {'state': state, 'month': month, 'yr': yr, 'target_lakhs': target_lakhs})


# ══════════════════════════════════════════════════════════════════════════════
# RBAC — authentication + admin role/user management
#
# Phase 1: login + the /admin/* management endpoints are enforced server-side.
# The read-only data endpoints above are NOT yet authorized per-role (UI-only
# gating) — see the "full server-side enforcement" backlog item.
# ══════════════════════════════════════════════════════════════════════════════

def _route_auth_admin(event, method, path):
    if path == '/auth/login' and method == 'POST':
        return _handle_login(event.get('body') or '')
    if path == '/auth/me' and method == 'GET':
        return _handle_me(event)
    if path == '/admin/screens' and method == 'GET':
        return _handle_admin_list_screens(event)
    if path == '/admin/roles':
        if method == 'GET':
            return _handle_admin_list_roles(event)
        if method == 'POST':
            return _handle_admin_create_role(event)
    if path.startswith('/admin/roles/'):
        role_id = (event.get('pathParameters') or {}).get('role_id')
        if method == 'PUT':
            return _handle_admin_update_role(event, role_id)
        if method == 'DELETE':
            return _handle_admin_delete_role(event, role_id)
    if path == '/admin/users':
        if method == 'GET':
            return _handle_admin_list_users(event)
        if method == 'POST':
            return _handle_admin_create_user(event)
    if path.startswith('/admin/users/'):
        user_id = (event.get('pathParameters') or {}).get('user_id')
        if method == 'PUT':
            return _handle_admin_update_user(event, user_id)
        if method == 'DELETE':
            return _handle_admin_delete_user(event, user_id)
    if path == '/admin/cache/flush' and method == 'POST':
        return _handle_admin_flush_cache(event)
    return _response(404, {'error': 'Not found'})


# ── shared helpers ────────────────────────────────────────────────────────────

def _json_body(event) -> dict:
    try:
        return json.loads(event.get('body') or '{}')
    except json.JSONDecodeError:
        raise auth.AuthError('Invalid JSON body', 400)


def _parse_int_id(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise auth.AuthError('Invalid id', 400)


def _require_admin(event, cur) -> dict:
    """Validate the bearer token AND confirm the user is an active admin (DB-authoritative)."""
    claims = auth.authenticate(event)
    username = (claims.get('sub') or '').lower()
    cur.execute("""
        SELECT u.user_id, u.is_active, r.is_admin
        FROM app_users u JOIN app_roles r ON r.role_id = u.role_id
        WHERE u.username = %s
    """, (username,))
    row = cur.fetchone()
    if not row or not row[1]:
        raise auth.AuthError('User not found or inactive', 401)
    if not row[2]:
        raise auth.AuthError('Administrator access required', 403)
    return {'user_id': row[0], 'username': username}


def _fetch_user_row(cur, username: str):
    cur.execute("""
        SELECT u.user_id, u.username, u.password_hash, u.is_active,
               u.role_id, r.role_name, r.is_admin
        FROM app_users u JOIN app_roles r ON r.role_id = u.role_id
        WHERE u.username = %s
    """, (username,))
    row = cur.fetchone()
    if not row:
        return None
    keys = ['user_id', 'username', 'password_hash', 'is_active', 'role_id', 'role_name', 'is_admin']
    return dict(zip(keys, row))


def _fetch_screens(cur, role_id: int, is_admin: bool) -> list:
    if is_admin:
        cur.execute('SELECT screen_key FROM app_screens ORDER BY sort_order')
    else:
        cur.execute("""
            SELECT s.screen_key
            FROM app_role_screens rs JOIN app_screens s ON s.screen_key = rs.screen_key
            WHERE rs.role_id = %s
            ORDER BY s.sort_order
        """, (role_id,))
    return [r[0] for r in cur.fetchall()]


def _set_role_screens(cur, role_id: int, screen_keys: list):
    cur.execute('DELETE FROM app_role_screens WHERE role_id = %s', (role_id,))
    if not screen_keys:
        return
    cur.execute('SELECT screen_key FROM app_screens')
    valid = {r[0] for r in cur.fetchall()}
    rows = [(role_id, k) for k in dict.fromkeys(screen_keys) if k in valid]
    if rows:
        cur.executemany(
            'INSERT INTO app_role_screens (role_id, screen_key) VALUES (%s, %s)', rows
        )


# ── auth ──────────────────────────────────────────────────────────────────────

def _maybe_bootstrap_admin(cur, username: str, password: str):
    """First-run only: create the admin user from BOOTSTRAP_ADMIN_* if no admin exists yet."""
    boot_user = (os.environ.get('BOOTSTRAP_ADMIN_USERNAME') or '').strip().lower()
    boot_pass = os.environ.get('BOOTSTRAP_ADMIN_PASSWORD') or ''
    if not boot_user or username != boot_user or password != boot_pass:
        return None

    cur.execute("""
        SELECT 1 FROM app_users u JOIN app_roles r ON r.role_id = u.role_id
        WHERE r.is_admin LIMIT 1
    """)
    if cur.fetchone():
        return None  # an admin already exists — do not bootstrap

    cur.execute('SELECT role_id FROM app_roles WHERE is_admin ORDER BY role_id LIMIT 1')
    role_row = cur.fetchone()
    if not role_row:
        cur.execute("INSERT INTO app_roles (role_name, is_admin) VALUES ('Administrator', TRUE) RETURNING role_id")
        role_row = cur.fetchone()
    role_id = role_row[0]

    pw_hash = auth.hash_password(password)
    cur.execute(
        'INSERT INTO app_users (username, password_hash, role_id, is_active) VALUES (%s, %s, %s, TRUE) RETURNING user_id',
        (username, pw_hash, role_id),
    )
    logger.info('Bootstrap admin user created: %s', username)
    return {
        'user_id': cur.fetchone()[0], 'username': username, 'password_hash': pw_hash,
        'is_active': True, 'role_id': role_id, 'role_name': 'Administrator', 'is_admin': True,
    }


def _handle_login(body_str: str):
    try:
        body = json.loads(body_str or '{}')
    except json.JSONDecodeError:
        return _response(400, {'error': 'Invalid JSON body'})

    username = (body.get('username') or '').strip().lower()
    password = body.get('password') or ''
    if not username or not password:
        return _response(400, {'error': 'username and password are required'})

    conn = _get_db_conn()
    try:
        with conn.cursor() as cur:
            user = _fetch_user_row(cur, username)
            if user is None:
                user = _maybe_bootstrap_admin(cur, username, password)
                if user is None:
                    return _response(401, {'error': 'Invalid username or password'})
                conn.commit()
            if not user['is_active']:
                return _response(401, {'error': 'Account is disabled'})
            if not auth.verify_password(password, user['password_hash']):
                return _response(401, {'error': 'Invalid username or password'})
            screens = _fetch_screens(cur, user['role_id'], user['is_admin'])
    finally:
        conn.close()

    token = auth.sign_jwt({'sub': user['username'], 'is_admin': user['is_admin']})
    return _response(200, {
        'token': token,
        'user': {
            'username': user['username'],
            'role_name': user['role_name'],
            'is_admin': user['is_admin'],
            'screens': screens,
        },
    })


def _handle_me(event):
    claims = auth.authenticate(event)
    username = (claims.get('sub') or '').lower()
    conn = _get_db_conn()
    try:
        with conn.cursor() as cur:
            user = _fetch_user_row(cur, username)
            if user is None or not user['is_active']:
                raise auth.AuthError('User not found or inactive', 401)
            screens = _fetch_screens(cur, user['role_id'], user['is_admin'])
    finally:
        conn.close()
    return _response(200, {
        'username': user['username'],
        'role_name': user['role_name'],
        'is_admin': user['is_admin'],
        'screens': screens,
    })


# ── admin: screens ────────────────────────────────────────────────────────────

def _handle_admin_list_screens(event):
    conn = _get_db_conn()
    try:
        with conn.cursor() as cur:
            _require_admin(event, cur)
            cur.execute('SELECT screen_key, label, sort_order FROM app_screens ORDER BY sort_order')
            screens = [{'screen_key': r[0], 'label': r[1], 'sort_order': r[2]} for r in cur.fetchall()]
    finally:
        conn.close()
    return _response(200, screens)


# ── admin: roles ──────────────────────────────────────────────────────────────

def _handle_admin_list_roles(event):
    conn = _get_db_conn()
    try:
        with conn.cursor() as cur:
            _require_admin(event, cur)
            cur.execute("""
                SELECT r.role_id, r.role_name, r.is_admin,
                       COALESCE(array_agg(rs.screen_key) FILTER (WHERE rs.screen_key IS NOT NULL), '{}'),
                       (SELECT COUNT(*) FROM app_users u WHERE u.role_id = r.role_id)
                FROM app_roles r
                LEFT JOIN app_role_screens rs ON rs.role_id = r.role_id
                GROUP BY r.role_id
                ORDER BY r.is_admin DESC, r.role_name
            """)
            roles = [{
                'role_id': r[0], 'role_name': r[1], 'is_admin': r[2],
                'screens': list(r[3]), 'user_count': int(r[4]),
            } for r in cur.fetchall()]
    finally:
        conn.close()
    return _response(200, roles)


def _handle_admin_create_role(event):
    body = _json_body(event)
    name = (body.get('role_name') or '').strip()
    screens = body.get('screens') or []
    if not name:
        return _response(400, {'error': 'role_name is required'})

    conn = _get_db_conn()
    try:
        with conn.cursor() as cur:
            _require_admin(event, cur)
            cur.execute('SELECT 1 FROM app_roles WHERE LOWER(role_name) = LOWER(%s)', (name,))
            if cur.fetchone():
                return _response(409, {'error': 'A role with that name already exists'})
            cur.execute('INSERT INTO app_roles (role_name, is_admin) VALUES (%s, FALSE) RETURNING role_id', (name,))
            role_id = cur.fetchone()[0]
            _set_role_screens(cur, role_id, screens)
            conn.commit()
    finally:
        conn.close()
    return _response(201, {'role_id': role_id})


def _handle_admin_update_role(event, role_id_raw):
    role_id = _parse_int_id(role_id_raw)
    body = _json_body(event)

    conn = _get_db_conn()
    try:
        with conn.cursor() as cur:
            _require_admin(event, cur)
            cur.execute('SELECT is_admin FROM app_roles WHERE role_id = %s', (role_id,))
            row = cur.fetchone()
            if not row:
                return _response(404, {'error': 'Role not found'})
            if row[0]:
                return _response(409, {'error': 'The Administrator role cannot be modified'})

            if 'role_name' in body:
                name = (body.get('role_name') or '').strip()
                if not name:
                    return _response(400, {'error': 'role_name cannot be empty'})
                cur.execute('SELECT 1 FROM app_roles WHERE LOWER(role_name) = LOWER(%s) AND role_id <> %s', (name, role_id))
                if cur.fetchone():
                    return _response(409, {'error': 'A role with that name already exists'})
                cur.execute('UPDATE app_roles SET role_name = %s, updated_at = NOW() WHERE role_id = %s', (name, role_id))
            if 'screens' in body:
                _set_role_screens(cur, role_id, body.get('screens') or [])
            conn.commit()
    finally:
        conn.close()
    return _response(200, {'role_id': role_id})


def _handle_admin_delete_role(event, role_id_raw):
    role_id = _parse_int_id(role_id_raw)
    conn = _get_db_conn()
    try:
        with conn.cursor() as cur:
            _require_admin(event, cur)
            cur.execute('SELECT is_admin FROM app_roles WHERE role_id = %s', (role_id,))
            row = cur.fetchone()
            if not row:
                return _response(404, {'error': 'Role not found'})
            if row[0]:
                return _response(409, {'error': 'The Administrator role cannot be deleted'})
            cur.execute('SELECT COUNT(*) FROM app_users WHERE role_id = %s', (role_id,))
            if cur.fetchone()[0] > 0:
                return _response(409, {'error': 'Cannot delete a role that still has users assigned'})
            cur.execute('DELETE FROM app_roles WHERE role_id = %s', (role_id,))
            conn.commit()
    finally:
        conn.close()
    return _response(200, {'deleted': role_id})


# ── admin: cache ──────────────────────────────────────────────────────────────

def _handle_admin_flush_cache(event):
    """Admin-only: drop every cached dashboard key (iravi:*) so the next request
    rehydrates it from RDS. Scoped to the app namespace rather than FLUSHDB so a
    shared Redis instance is left untouched."""
    conn = _get_db_conn()
    try:
        with conn.cursor() as cur:
            admin = _require_admin(event, cur)
    finally:
        conn.close()

    r = _get_redis()
    deleted = 0
    batch = []
    for key in r.scan_iter(match='iravi:*', count=500):
        batch.append(key)
        if len(batch) >= 500:
            deleted += r.delete(*batch)
            batch = []
    if batch:
        deleted += r.delete(*batch)

    logger.info('Cache flush by %s — %d keys deleted', admin['username'], deleted)
    return _response(200, {'deleted': deleted})


# ── admin: users ──────────────────────────────────────────────────────────────

def _handle_admin_list_users(event):
    conn = _get_db_conn()
    try:
        with conn.cursor() as cur:
            _require_admin(event, cur)
            cur.execute("""
                SELECT u.user_id, u.username, u.role_id, r.role_name, r.is_admin,
                       u.is_active, u.created_at
                FROM app_users u JOIN app_roles r ON r.role_id = u.role_id
                ORDER BY u.username
            """)
            users = [{
                'user_id': r[0], 'username': r[1], 'role_id': r[2], 'role_name': r[3],
                'is_admin': r[4], 'is_active': r[5],
                'created_at': r[6].isoformat() if r[6] else None,
            } for r in cur.fetchall()]
    finally:
        conn.close()
    return _response(200, users)


def _handle_admin_create_user(event):
    body = _json_body(event)
    username = (body.get('username') or '').strip().lower()
    password = body.get('password') or ''
    role_id = body.get('role_id')
    if not username or not password or role_id is None:
        return _response(400, {'error': 'username, password and role_id are required'})
    if len(password) < 6:
        return _response(400, {'error': 'Password must be at least 6 characters'})
    role_id = _parse_int_id(role_id)

    conn = _get_db_conn()
    try:
        with conn.cursor() as cur:
            _require_admin(event, cur)
            cur.execute('SELECT 1 FROM app_roles WHERE role_id = %s', (role_id,))
            if not cur.fetchone():
                return _response(400, {'error': 'role_id does not exist'})
            cur.execute('SELECT 1 FROM app_users WHERE username = %s', (username,))
            if cur.fetchone():
                return _response(409, {'error': 'A user with that username already exists'})
            cur.execute(
                'INSERT INTO app_users (username, password_hash, role_id, is_active) VALUES (%s, %s, %s, TRUE) RETURNING user_id',
                (username, auth.hash_password(password), role_id),
            )
            user_id = cur.fetchone()[0]
            conn.commit()
    finally:
        conn.close()
    return _response(201, {'user_id': user_id})


def _handle_admin_update_user(event, user_id_raw):
    user_id = _parse_int_id(user_id_raw)
    body = _json_body(event)

    conn = _get_db_conn()
    try:
        with conn.cursor() as cur:
            _require_admin(event, cur)
            cur.execute("""
                SELECT u.is_active, r.is_admin
                FROM app_users u JOIN app_roles r ON r.role_id = u.role_id
                WHERE u.user_id = %s
            """, (user_id,))
            row = cur.fetchone()
            if not row:
                return _response(404, {'error': 'User not found'})
            was_active, was_admin = row

            sets, vals = [], []
            resulting_admin = was_admin
            if body.get('role_id') is not None:
                new_role = _parse_int_id(body['role_id'])
                cur.execute('SELECT is_admin FROM app_roles WHERE role_id = %s', (new_role,))
                rr = cur.fetchone()
                if not rr:
                    return _response(400, {'error': 'role_id does not exist'})
                sets.append('role_id = %s'); vals.append(new_role)
                resulting_admin = rr[0]

            new_active = was_active
            if 'is_active' in body:
                new_active = bool(body['is_active'])
                sets.append('is_active = %s'); vals.append(new_active)

            if body.get('password'):
                if len(body['password']) < 6:
                    return _response(400, {'error': 'Password must be at least 6 characters'})
                sets.append('password_hash = %s'); vals.append(auth.hash_password(body['password']))

            # Never strand the system without an active admin.
            if was_active and was_admin and (not resulting_admin or not new_active):
                cur.execute("""
                    SELECT COUNT(*) FROM app_users u JOIN app_roles r ON r.role_id = u.role_id
                    WHERE r.is_admin AND u.is_active AND u.user_id <> %s
                """, (user_id,))
                if cur.fetchone()[0] == 0:
                    return _response(409, {'error': 'Cannot remove the last active administrator'})

            if not sets:
                return _response(400, {'error': 'No updatable fields provided'})
            sets.append('updated_at = NOW()')
            vals.append(user_id)
            cur.execute(f"UPDATE app_users SET {', '.join(sets)} WHERE user_id = %s", vals)
            conn.commit()
    finally:
        conn.close()
    return _response(200, {'user_id': user_id})


def _handle_admin_delete_user(event, user_id_raw):
    user_id = _parse_int_id(user_id_raw)
    conn = _get_db_conn()
    try:
        with conn.cursor() as cur:
            admin = _require_admin(event, cur)
            if admin['user_id'] == user_id:
                return _response(409, {'error': 'You cannot delete your own account'})
            cur.execute("""
                SELECT u.is_active, r.is_admin
                FROM app_users u JOIN app_roles r ON r.role_id = u.role_id
                WHERE u.user_id = %s
            """, (user_id,))
            row = cur.fetchone()
            if not row:
                return _response(404, {'error': 'User not found'})
            if row[0] and row[1]:
                cur.execute("""
                    SELECT COUNT(*) FROM app_users u JOIN app_roles r ON r.role_id = u.role_id
                    WHERE r.is_admin AND u.is_active AND u.user_id <> %s
                """, (user_id,))
                if cur.fetchone()[0] == 0:
                    return _response(409, {'error': 'Cannot delete the last active administrator'})
            cur.execute('DELETE FROM app_users WHERE user_id = %s', (user_id,))
            conn.commit()
    finally:
        conn.close()
    return _response(200, {'deleted': user_id})


# ══════════════════════════════════════════════════════════════════════════════
# ALERTS — admin-only CRUD + field catalog + test endpoint
#
# All routes here require a valid admin session (recomputed from DB via
# _require_admin).  Any AuthError propagates to the caller and is mapped to
# the appropriate HTTP status.
#
# Tables (created by IaC migration 013):
#   alerts(id, name, category, frequency, schedule_day, match_type,
#          is_active, created_by, created_at, updated_at)
#   alert_conditions(id, alert_id, field, op, value, value2)
#   alert_recipients(id, alert_id, channel, address)
#   alert_runs(id, alert_id, run_at, matched, status, error)
# ══════════════════════════════════════════════════════════════════════════════

def _route_alerts(event, method, path):
    """Router for /alerts* — every branch verifies admin credentials."""

    # GET /alerts/fields?category=balances  (field catalog — no {id})
    if path == '/alerts/fields' and method == 'GET':
        return _handle_alerts_fields(event)

    # Collection routes: GET /alerts, POST /alerts
    if path == '/alerts':
        if method == 'GET':
            return _handle_alerts_list(event)
        if method == 'POST':
            return _handle_alerts_create(event)
        return _response(405, {'error': 'Method not allowed'})

    # Instance routes: /alerts/{id} and /alerts/{id}/test
    # Detect sub-path by splitting off the /alerts/ prefix.
    remainder = path[len('/alerts/'):]  # e.g. "42" or "42/test"
    parts = remainder.split('/', 1)
    try:
        alert_id = int(parts[0])
    except (ValueError, IndexError):
        return _response(404, {'error': 'Not found'})

    if len(parts) == 1:
        # /alerts/{id}
        if method == 'GET':
            return _handle_alert_get(event, alert_id)
        if method == 'PUT':
            return _handle_alerts_update(event, alert_id)
        if method == 'DELETE':
            return _handle_alerts_delete(event, alert_id)
        return _response(405, {'error': 'Method not allowed'})

    if len(parts) == 2 and parts[1] == 'test' and method == 'POST':
        return _handle_alerts_test(event, alert_id)

    return _response(404, {'error': 'Not found'})


def _alert_row_to_dict(row: tuple) -> dict:
    """Convert a raw SELECT row from the `alerts` table to a dict.

    Expected column order (must match every query below):
      id, name, category, frequency, schedule_day, schedule_time, match_type,
      is_active, created_by, created_at, updated_at, branch
    """
    (alert_id, name, category, frequency, schedule_day, schedule_time, match_type,
     is_active, created_by, created_at, updated_at, branch) = row
    # schedule_time is stored as a TIME column; psycopg2 returns it as a timedelta
    # (seconds since midnight) or a datetime.time object depending on driver version.
    # Normalise to "HH:MM" string.
    if schedule_time is None:
        st_str = alerts_eval._DEFAULT_SCHEDULE_TIME
    elif hasattr(schedule_time, 'hour'):
        # datetime.time object
        st_str = f"{schedule_time.hour:02d}:{schedule_time.minute:02d}"
    else:
        # timedelta (psycopg2 returns timedelta for TIME WITHOUT TIME ZONE)
        total_seconds = int(schedule_time.total_seconds())
        hours, remainder = divmod(total_seconds, 3600)
        minutes = remainder // 60
        st_str = f"{hours:02d}:{minutes:02d}"
    return {
        'id':            alert_id,
        'name':          name,
        'category':      category,
        'frequency':     frequency,
        'schedule_day':  schedule_day,
        'schedule_time': st_str,
        'match_type':    match_type,
        'is_active':     is_active,
        'created_by':    created_by,
        'created_at':    created_at.isoformat() if created_at else None,
        'updated_at':    updated_at.isoformat() if updated_at else None,
        'branch':        branch,
    }


_ALERT_SELECT = """
    SELECT id, name, category, frequency, schedule_day, schedule_time, match_type,
           is_active, created_by, created_at, updated_at, branch
    FROM alerts
"""


def _fetch_alert_with_children(cur, alert_id: int) -> dict | None:
    """Fetch one alert row plus its conditions and recipients.  Returns None if not found."""
    cur.execute(_ALERT_SELECT + " WHERE id = %s", (alert_id,))
    row = cur.fetchone()
    if not row:
        return None
    alert = _alert_row_to_dict(row)
    cur.execute("""
        SELECT id, field, op, value, value2
        FROM alert_conditions WHERE alert_id = %s ORDER BY id
    """, (alert_id,))
    conditions = []
    for (cid, field, op, value, value2) in cur.fetchall():
        conditions.append({
            'id':     cid,
            'field':  field,
            'op':     op,
            'value':  float(value),
            'value2': float(value2) if value2 is not None else None,
        })
    cur.execute("""
        SELECT id, channel, address
        FROM alert_recipients WHERE alert_id = %s ORDER BY id
    """, (alert_id,))
    recipients = [
        address
        for (_rid, _channel, address) in cur.fetchall()
    ]
    alert['conditions'] = conditions
    alert['recipients'] = recipients
    return alert


def _insert_alert_children(cur, alert_id: int, conditions: list, recipients: list):
    """Insert condition and recipient rows for an alert (used on create and replace)."""
    for cond in conditions:
        cur.execute("""
            INSERT INTO alert_conditions (alert_id, field, op, value, value2)
            VALUES (%s, %s, %s, %s, %s)
        """, (
            alert_id,
            cond['field'],
            cond['op'],
            float(cond['value']),
            float(cond['value2']) if cond.get('value2') is not None else None,
        ))
    for email in recipients:
        cur.execute("""
            INSERT INTO alert_recipients (alert_id, channel, address)
            VALUES (%s, 'email', %s)
        """, (alert_id, email.strip()))


def _handle_alerts_fields(event):
    """GET /alerts/fields?category=<balances|sales|sale_returns> — field catalog (admin-only)."""
    conn = _get_db_conn()
    try:
        with conn.cursor() as cur:
            _require_admin(event, cur)
    finally:
        conn.close()
    params = event.get('queryStringParameters') or {}
    category = (params.get('category') or 'balances').strip()
    catalog = alerts_eval.FIELD_CATALOGS.get(category)
    if catalog is None:
        return _response(400, {
            'error': f"Unknown category {category!r}. Must be one of: {sorted(alerts_eval.FIELD_CATALOGS)}"
        })
    return _response(200, catalog)


def _handle_alerts_list(event):
    """GET /alerts — list all alerts with nested conditions and recipients."""
    conn = _get_db_conn()
    try:
        with conn.cursor() as cur:
            _require_admin(event, cur)
            cur.execute(_ALERT_SELECT + " ORDER BY id")
            alert_rows = cur.fetchall()
            alerts = []
            for row in alert_rows:
                alert = _alert_row_to_dict(row)
                cur.execute("""
                    SELECT id, field, op, value, value2
                    FROM alert_conditions WHERE alert_id = %s ORDER BY id
                """, (alert['id'],))
                alert['conditions'] = [
                    {'id': cid, 'field': f, 'op': op,
                     'value': float(v), 'value2': float(v2) if v2 is not None else None}
                    for cid, f, op, v, v2 in cur.fetchall()
                ]
                cur.execute("""
                    SELECT id, channel, address
                    FROM alert_recipients WHERE alert_id = %s ORDER BY id
                """, (alert['id'],))
                alert['recipients'] = [
                    addr
                    for (_rid, _ch, addr) in cur.fetchall()
                ]
                alerts.append(alert)
    finally:
        conn.close()
    return _response(200, alerts)


def _handle_alert_get(event, alert_id: int):
    """GET /alerts/{id} — single alert with conditions and recipients."""
    conn = _get_db_conn()
    try:
        with conn.cursor() as cur:
            _require_admin(event, cur)
            alert = _fetch_alert_with_children(cur, alert_id)
    finally:
        conn.close()
    if alert is None:
        return _response(404, {'error': 'Alert not found'})
    return _response(200, alert)


def _handle_alerts_create(event):
    """POST /alerts — create alert + conditions + recipients in a transaction."""
    body = _json_body(event)
    try:
        alerts_eval.validate_alert(body)
    except alerts_eval.ValidationError as exc:
        return _response(400, {'error': str(exc)})

    name          = body['name'].strip()
    category      = body.get('category', 'balances')
    frequency     = body['frequency']
    schedule_day  = body.get('schedule_day')
    schedule_time = body.get('schedule_time') or alerts_eval._DEFAULT_SCHEDULE_TIME
    match_type    = body['match_type']
    is_active     = bool(body.get('is_active', True))
    conditions    = body['conditions']
    recipients    = body['recipients']
    branch        = body.get('branch') or None  # NULL stored as NULL; 'ALL' stored as 'ALL'

    conn = _get_db_conn()
    try:
        with conn.cursor() as cur:
            admin = _require_admin(event, cur)
            cur.execute("""
                INSERT INTO alerts
                    (name, category, frequency, schedule_day, schedule_time, match_type,
                     is_active, created_by, created_at, updated_at, branch)
                VALUES (%s, %s, %s, %s, %s::TIME, %s, %s, %s, NOW(), NOW(), %s)
                RETURNING id
            """, (name, category, frequency, schedule_day, schedule_time, match_type,
                  is_active, admin['username'], branch))
            alert_id = cur.fetchone()[0]
            _insert_alert_children(cur, alert_id, conditions, recipients)
            conn.commit()
            alert = _fetch_alert_with_children(cur, alert_id)
    finally:
        conn.close()
    return _response(201, alert)


def _handle_alerts_update(event, alert_id: int):
    """PUT /alerts/{id} — update alert + replace conditions/recipients."""
    body = _json_body(event)
    try:
        alerts_eval.validate_alert(body)
    except alerts_eval.ValidationError as exc:
        return _response(400, {'error': str(exc)})

    name          = body['name'].strip()
    category      = body.get('category', 'balances')
    frequency     = body['frequency']
    schedule_day  = body.get('schedule_day')
    schedule_time = body.get('schedule_time') or alerts_eval._DEFAULT_SCHEDULE_TIME
    match_type    = body['match_type']
    is_active     = bool(body.get('is_active', True))
    conditions    = body['conditions']
    recipients    = body['recipients']
    branch        = body.get('branch') or None

    conn = _get_db_conn()
    try:
        with conn.cursor() as cur:
            _require_admin(event, cur)
            cur.execute('SELECT id FROM alerts WHERE id = %s', (alert_id,))
            if not cur.fetchone():
                return _response(404, {'error': 'Alert not found'})

            cur.execute("""
                UPDATE alerts
                SET name=%s, category=%s, frequency=%s, schedule_day=%s,
                    schedule_time=%s::TIME, match_type=%s, is_active=%s,
                    branch=%s, updated_at=NOW()
                WHERE id=%s
            """, (name, category, frequency, schedule_day, schedule_time, match_type,
                  is_active, branch, alert_id))

            # Replace child rows
            cur.execute('DELETE FROM alert_conditions WHERE alert_id = %s', (alert_id,))
            cur.execute('DELETE FROM alert_recipients WHERE alert_id = %s', (alert_id,))
            _insert_alert_children(cur, alert_id, conditions, recipients)
            conn.commit()
            alert = _fetch_alert_with_children(cur, alert_id)
    finally:
        conn.close()
    return _response(200, alert)


def _handle_alerts_delete(event, alert_id: int):
    """DELETE /alerts/{id} — delete alert (cascade removes conditions + recipients)."""
    conn = _get_db_conn()
    try:
        with conn.cursor() as cur:
            _require_admin(event, cur)
            cur.execute('SELECT id FROM alerts WHERE id = %s', (alert_id,))
            if not cur.fetchone():
                return _response(404, {'error': 'Alert not found'})
            cur.execute('DELETE FROM alerts WHERE id = %s', (alert_id,))
            conn.commit()
    finally:
        conn.close()
    return _response(200, {'deleted': alert_id})


def _handle_alerts_test(event, alert_id: int):
    """POST /alerts/{id}/test — dry-run: evaluate alert conditions NOW (no email sent).

    balances category:
        Returns {matched: <count>, sample: [<up to 20 customer dicts>]}.
    sales / sale_returns categories:
        Returns the aggregate shape:
        {category, matched: <bool>, metrics: {<field>: <float>...}, conditions: [...]}
    """
    conn = _get_db_conn()
    try:
        with conn.cursor() as cur:
            _require_admin(event, cur)
            alert = _fetch_alert_with_children(cur, alert_id)

        if alert is None:
            return _response(404, {'error': 'Alert not found'})

        from datetime import date as _date
        today = _date.today()

        if alert['category'] == 'balances':
            matched = alerts_eval.evaluate_balances(
                conn,
                conditions=alert['conditions'],
                match_type=alert['match_type'],
                today=today,
            )
            result = {'matched': len(matched), 'sample': matched[:20]}
        else:
            # sales / sale_returns — aggregate shape
            result = alerts_eval.evaluate_aggregate(conn, alert=alert, today=today)
    finally:
        conn.close()

    return _response(200, result)


def _response(status: int, body) -> dict:
    return {
        'statusCode': status,
        'headers': {'Content-Type': 'application/json'},
        'body': json.dumps(body),
    }
