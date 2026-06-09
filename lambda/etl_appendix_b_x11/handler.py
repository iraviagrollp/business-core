import json
import logging
import os
import tempfile
from datetime import date, datetime
from urllib.parse import unquote_plus

import boto3
import openpyxl
import psycopg2

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client('s3')
secrets = boto3.client('secretsmanager')

_BUCKET = os.environ['DATA_BUCKET']
_RAW_PREFIX = os.environ.get('RAW_PREFIX', 'raw/')
_PROCESSED_PREFIX = os.environ.get('PROCESSED_PREFIX', 'processed/')
_FILE_PREFIX = 'Barcodes Masters'

# Column indices (0-based), row 1 = header, data from row 2:
# [0]=Barcodes, [1]=ProductId, [13]=PartNo (mdf_date), [16]=VendorId, [22]=Expiry Date

_SENTINEL_DATE = date(1800, 1, 1)  # ERP default "no date" — stored as NULL


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
        if not (filename.startswith(_FILE_PREFIX) and filename.endswith('.xlsx')):
            logger.info('Skipping: %s', key)
            continue

        _process(bucket, key, filename)


def _process(bucket: str, key: str, filename: str):
    archive_key = _PROCESSED_PREFIX + 'raw/' + filename

    with tempfile.TemporaryDirectory() as tmp:
        src_path = os.path.join(tmp, filename)
        logger.info('Downloading s3://%s/%s', bucket, key)
        s3.download_file(bucket, key, src_path)
        rows = _parse(src_path)
        logger.info('Parsed %d barcode rows', len(rows))

    conn = _get_db_conn()
    try:
        _upsert(conn, rows)
        conn.commit()
        logger.info('Upserted %d rows into appendix_b_x11_stock', len(rows))
    finally:
        conn.close()

    s3.copy_object(Bucket=bucket, CopySource={'Bucket': bucket, 'Key': key}, Key=archive_key)
    s3.delete_object(Bucket=bucket, Key=key)
    logger.info('Archived source to s3://%s/%s', bucket, archive_key)


def _parse(src_path: str) -> list[dict]:
    wb = openpyxl.load_workbook(src_path, data_only=True)
    ws = wb.active

    rows = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        barcode_raw = row[0]
        technical_name = str(row[1] or '').strip()
        vendor = str(row[16] or '').strip()
        mdf_raw = row[13]
        exp_raw = row[22]

        if not barcode_raw or not technical_name or not vendor:
            continue

        if isinstance(barcode_raw, (int, float)):
            barcode = str(int(barcode_raw))
        else:
            barcode = str(barcode_raw).strip()

        if not barcode:
            continue

        rows.append({
            'barcode': barcode,
            'technical_name': technical_name,
            'vendor': vendor,
            'mdf_date': _parse_date(mdf_raw),
            'exp_date': _parse_date(exp_raw),
        })

    return rows


def _parse_date(val) -> date | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        d = val.date()
    elif isinstance(val, date):
        d = val
    else:
        s = str(val).strip()
        d = None
        for fmt in ('%d-%m-%Y %H:%M:%S', '%d-%m-%Y', '%Y-%m-%d'):
            try:
                d = datetime.strptime(s, fmt).date()
                break
            except ValueError:
                continue
        if d is None:
            logger.warning('Unparseable date %r — storing NULL', val)
            return None
    return None if d == _SENTINEL_DATE else d


def _upsert(conn, rows: list[dict]):
    with conn.cursor() as cur:
        for row in rows:
            cur.execute(
                """
                UPDATE appendix_b_x11_stock
                   SET out_z = NOW()
                 WHERE barcode = %s AND technical_name = %s AND vendor = %s
                   AND out_z IS NULL
                """,
                (row['barcode'], row['technical_name'], row['vendor']),
            )
            cur.execute(
                """
                INSERT INTO appendix_b_x11_stock (barcode, technical_name, vendor, mdf_date, exp_date)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (row['barcode'], row['technical_name'], row['vendor'],
                 row['mdf_date'], row['exp_date']),
            )
