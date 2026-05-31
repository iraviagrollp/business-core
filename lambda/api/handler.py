import json
import logging
import os
import re
from datetime import datetime, timezone

import boto3
import psycopg2
import redis

logger = logging.getLogger()
logger.setLevel(logging.INFO)

secrets = boto3.client('secretsmanager')

_REDIS_TTL = 86400  # 24h fallback TTL when populating from RDS on cache miss

_WEIGHT_RE = re.compile(r'\b(KG|GMS|GM)\b', re.IGNORECASE)
_VOLUME_RE = re.compile(r'\b(LTR|LT|ML|L)\b', re.IGNORECASE)


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
                SELECT packing_size, available_qty, stock_valuation, brand, technical, entry_date
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

    for packing_size, available_qty, stock_valuation, brand, technical, entry_date in rows:
        qty = float(available_qty or 0)
        ps = packing_size or ''
        if _WEIGHT_RE.search(ps):
            total_kgs += qty / 1000.0
        elif _VOLUME_RE.search(ps):
            total_vols += qty / 1000.0
        total_valuation += float(stock_valuation or 0)
        product_set.add((brand, technical, ps))
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
        current.append({
            'brand': row['brand'],
            'technical': row['technical'],
            'packing_size': row['packing_size'] or '',
            'packing_configuration': row['packing_configuration'],
            'available_nos': int(row['available_nos'] or 0),
            'conversion_factor': int(row['conversion_factor'] or 0),
            'available_cases': int(row['available_cases'] or 0),
            'available_qty': float(row['available_qty'] or 0),
            'branch': row['branch'],
            'special_packing_mention': row['special_packing_mention'],
            'entry_date': row['entry_date'].isoformat() if row['entry_date'] else None,
            'rate': float(row['rate']) if row['rate'] is not None else None,
            'stock_valuation': float(row['stock_valuation']) if row['stock_valuation'] is not None else None,
        })

    r.set('iravi:stocks:current', json.dumps(current), ex=_REDIS_TTL)
    return _response(200, current)


def _response(status: int, body) -> dict:
    return {
        'statusCode': status,
        'headers': {'Content-Type': 'application/json'},
        'body': json.dumps(body),
    }
