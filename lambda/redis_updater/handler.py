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

_TTL = 86400  # 24 hours — refreshed nightly by ETL


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
    detail_type = event.get('detail-type', '')
    logger.info('Event detail-type: %s', detail_type)

    if detail_type == 'ETLStocksSuccess':
        _update_stocks_cache()
    elif detail_type == 'ETLSalesSuccess':
        _update_sales_cache()
    else:
        logger.warning('Unknown detail-type: %s — no-op', detail_type)


def _packing_display(packing_size_num: float, packing_config: str) -> str:
    ps = int(packing_size_num) if packing_size_num % 1 == 0 else packing_size_num
    return f"{ps} {packing_config}"


def _update_stocks_cache():
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

    total_kgs = 0.0
    total_vols = 0.0
    total_valuation = 0.0
    product_set = set()
    latest_date = None
    current = []

    for raw in raw_rows:
        row = dict(zip(col_names, raw))

        # packing_configuration is 'gms' or 'ml' (from process.py)
        packing_config = row['packing_configuration'] or ''
        packing_size_num = float(row['packing_size'] or 0)
        available_qty = float(row['available_qty'] or 0)

        if packing_config == 'gms':
            total_kgs += available_qty
        elif packing_config == 'ml':
            total_vols += available_qty

        total_valuation += float(row['stock_valuation'] or 0)
        product_set.add((row['brand'], row['technical'], packing_size_num, packing_config))

        entry_date = row['entry_date']
        if entry_date and (latest_date is None or entry_date > latest_date):
            latest_date = entry_date

        current.append({
            'brand': row['brand'],
            'technical': row['technical'],
            'packing_size': packing_size_num,
            'packing_configuration': packing_config,
            'packing_display': _packing_display(packing_size_num, packing_config),
            'available_nos': float(row['available_nos'] or 0),
            'conversion_factor': float(row['conversion_factor'] or 0),
            'available_cases': float(row['available_cases'] or 0),
            'available_qty': available_qty,
            'branch': row['branch'],
            'special_packing_mention': row['special_packing_mention'],
            'entry_date': entry_date.isoformat() if entry_date else None,
            'rate': float(row['rate']) if row['rate'] is not None else None,
            'stock_valuation': float(row['stock_valuation']) if row['stock_valuation'] is not None else None,
        })

    summary = {
        'total_kgs': round(total_kgs, 2),
        'total_vols': round(total_vols, 2),
        'stock_valuation': round(total_valuation, 2),
        'total_products': len(product_set),
        'as_of': latest_date.isoformat() if latest_date else None,
        'updated_at': datetime.now(timezone.utc).isoformat(),
    }

    r = _get_redis()
    pipe = r.pipeline()
    pipe.set('iravi:stocks:summary', json.dumps(summary), ex=_TTL)
    pipe.set('iravi:stocks:current', json.dumps(current), ex=_TTL)
    pipe.execute()

    logger.info(
        'Stocks cache updated: %d SKUs, %d rows, valuation=%.2f',
        summary['total_products'], len(current), total_valuation,
    )


def _update_sales_cache():
    logger.info('ETLSalesSuccess — sales cache update not yet implemented')
