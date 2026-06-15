import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from urllib.parse import unquote_plus

import boto3
import psycopg2

from process import process_stock_file

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client('s3')
secrets = boto3.client('secretsmanager')
events = boto3.client('events')

_BUCKET = os.environ['DATA_BUCKET']
_RAW_PREFIX = os.environ.get('RAW_PREFIX', 'raw/')
_PROCESSED_PREFIX = os.environ.get('PROCESSED_PREFIX', 'processed/')
_STOCK_PREFIX = 'Current Stock Balances'
_RATES_PREFIX = 'Product Masters With Rates'


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


def lambda_handler(event, context):
    for record in event.get('Records', []):
        bucket = record['s3']['bucket']['name']
        key = unquote_plus(record['s3']['object']['key'])
        logger.info('S3 event: s3://%s/%s', bucket, key)

        filename = key.split('/')[-1]
        if not (filename.startswith(_STOCK_PREFIX) and filename.endswith('.xlsx')):
            logger.info('Skipping: %s', key)
            continue

        _process(bucket, key, filename)


def _process(bucket: str, key: str, filename: str):
    suffix = filename[len(_STOCK_PREFIX):]
    out_filename = f'Stock - Processed {suffix}'
    out_key = _PROCESSED_PREFIX + out_filename
    archive_key = _PROCESSED_PREFIX + 'raw/' + filename

    with tempfile.TemporaryDirectory() as tmp:
        src_path = os.path.join(tmp, 'stock.xlsx')
        dst_path = os.path.join(tmp, out_filename)
        rates_path = None

        logger.info('Downloading stock file s3://%s/%s', bucket, key)
        s3.download_file(bucket, key, src_path)

        rates_key = _find_latest(bucket, _RATES_PREFIX)
        if rates_key:
            rates_path = os.path.join(tmp, 'rates.xlsx')
            logger.info('Downloading rates file s3://%s/%s', bucket, rates_key)
            s3.download_file(bucket, rates_key, rates_path)
        else:
            logger.warning('No rates file found under s3://%s/%s%s* — proceeding without rates',
                           bucket, _RAW_PREFIX, _RATES_PREFIX)

        entry_date = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
        rows = process_stock_file(src_path, dst_path, entry_date=entry_date, rates_path=rates_path)
        logger.info('Processed %d rows', len(rows))

        s3.upload_file(dst_path, bucket, out_key)
        logger.info('Uploaded to s3://%s/%s', bucket, out_key)

    conn = _get_db_conn()
    try:
        _upsert_snapshot_stock(conn, rows)
        conn.commit()
        logger.info('Upserted %d rows into snapshot_stock', len(rows))
    finally:
        conn.close()

    s3.copy_object(Bucket=bucket, CopySource={'Bucket': bucket, 'Key': key}, Key=archive_key)
    s3.delete_object(Bucket=bucket, Key=key)
    logger.info('Archived source to s3://%s/%s', bucket, archive_key)

    events.put_events(Entries=[{
        'Source': 'iravi.etl',
        'DetailType': 'ETLStocksSuccess',
        'Detail': json.dumps({
            'entry_date': entry_date.isoformat(),
            'rows_processed': len(rows),
        }),
        'EventBusName': os.environ.get('EVENT_BUS_NAME', 'default'),
    }])
    logger.info('Emitted ETLStocksSuccess for entry_date=%s rows=%d', entry_date.isoformat(), len(rows))


def _upsert_snapshot_stock(conn, rows: list[dict]):
    """Snapshot replace: close every active record, then insert the new snapshot."""
    with conn.cursor() as cur:
        cur.execute("UPDATE snapshot_stock SET out_z = NOW() WHERE out_z IS NULL")
        for row in rows:
            cur.execute(
                """
                INSERT INTO snapshot_stock (
                    brand, technical, packing_size, packing_configuration,
                    available_nos, conversion_factor, available_cases, available_qty,
                    branch, special_packing_mention, entry_date, rate, stock_valuation
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    row['brand'], row['technical'], row['packing_size'],
                    row['packing_configuration'], row['available_nos'],
                    row['conversion_factor'], row['available_cases'],
                    row['available_qty'] / 1000, row['branch'],
                    row['special_packing_mention'], row['entry_date'],
                    row['rate'], row['stock_valuation'],
                ),
            )


def _find_latest(bucket: str, filename_prefix: str) -> str | None:
    resp = s3.list_objects_v2(Bucket=bucket, Prefix=_RAW_PREFIX + filename_prefix)
    objects = [o for o in resp.get('Contents', []) if o['Key'].endswith('.xlsx')]
    if not objects:
        return None
    return max(objects, key=lambda o: o['LastModified'])['Key']
