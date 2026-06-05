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

_REDIS_TTL = 86400   # 24h fallback TTL when populating from RDS on cache miss
_LEDGER_TTL = 3600  # 1h TTL for ledger range-query results


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

    if method != 'GET':
        return _response(405, {'error': 'Method not allowed'})

    if path == '/stocks/summary':
        return _handle_stocks_summary()
    if path == '/stocks/current':
        return _handle_stocks_current()
    if path == '/ledger/range':
        return _handle_ledger_range()
    if path == '/ledger':
        params = event.get('queryStringParameters') or {}
        return _handle_ledger_data(params.get('from_date', ''), params.get('to_date', ''))
    if path == '/sales':
        return _response(200, {'data': []})

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


def _response(status: int, body) -> dict:
    return {
        'statusCode': status,
        'headers': {'Content-Type': 'application/json'},
        'body': json.dumps(body),
    }
