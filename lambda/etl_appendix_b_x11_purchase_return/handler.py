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
_FILE_PREFIX = 'AppendixPurReturn'

# Column indices (0-based), header in row 5, data from row 6:
# [0]=Date, [1]=Voucher No, [2]=Branch, [3]=Party, [4]=Ref BillNo,
# [6]=Product, [7]=Qty, [22]=Barcodes
# Note: return file has no Location/Storage Bin columns — layout differs from purchase file


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

        conn = _get_db_conn()
        try:
            barcode_dates = _load_barcode_dates(conn)
            logger.info('Loaded %d entries from appendix_b_x11_stock', len(barcode_dates))
            rows = _parse(src_path, barcode_dates)
            logger.info('Parsed %d purchase return rows', len(rows))
            _upsert(conn, rows)
            conn.commit()
            logger.info('Upserted %d rows into appendix_b_x11_stock_ledger', len(rows))
        finally:
            conn.close()

    s3.copy_object(Bucket=bucket, CopySource={'Bucket': bucket, 'Key': key}, Key=archive_key)
    s3.delete_object(Bucket=bucket, Key=key)
    logger.info('Archived source to s3://%s/%s', bucket, archive_key)


def _load_barcode_dates(conn) -> dict:
    """Load active appendix_b_x11_stock rows into a (technical_name, barcode) → (mdf_date, exp_date) dict."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT technical_name, barcode, mdf_date, exp_date
            FROM appendix_b_x11_stock
            WHERE out_z IS NULL
        """)
        return {(row[0], row[1]): (row[2], row[3]) for row in cur.fetchall()}


def _parse(src_path: str, barcode_dates: dict) -> list[dict]:
    wb = openpyxl.load_workbook(src_path, data_only=True)
    ws = wb.active

    rows = []
    skipped_multi = 0
    for row in ws.iter_rows(min_row=6, values_only=True):
        purchase_date_raw = row[0]
        iravi_voucher = str(row[1] or '').strip()

        if purchase_date_raw is None:
            continue
        if not iravi_voucher:
            continue

        barcode_raw = str(row[22] or '').strip()
        barcodes = [b.strip() for b in barcode_raw.split(',') if b.strip()]
        if len(barcodes) != 1:
            skipped_multi += 1
            continue
        barcode = barcodes[0]

        technical_name = str(row[6] or '').replace(',', '').strip()
        if not technical_name:
            continue

        purchase_date = _parse_date(purchase_date_raw)
        if purchase_date is None:
            logger.warning('Unparseable date %r — skipping row', purchase_date_raw)
            continue

        mdf_date, exp_date = barcode_dates.get((technical_name, barcode), (None, None))

        rows.append({
            'purchase_date': purchase_date,
            'iravi_voucher': iravi_voucher,
            'supplier_voucher': str(row[4] or '').strip() or None,
            'branch': str(row[2] or '').strip() or None,
            'party': str(row[3] or '').strip() or None,
            'technical_name': technical_name,
            'barcode': barcode,
            'mdf_date': mdf_date,
            'exp_date': exp_date,
            'in_out': 'Out',
            'qty': float(row[7]) if row[7] is not None else None,
        })

    if skipped_multi:
        logger.info('Skipped %d rows with multiple barcodes', skipped_multi)
    return rows


def _parse_date(val) -> date | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    s = str(val).strip()
    for fmt in ('%d-%m-%Y %H:%M:%S', '%d-%m-%Y', '%Y-%m-%d'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    logger.warning('Unparseable date string %r', val)
    return None


def _upsert(conn, rows: list[dict]):
    with conn.cursor() as cur:
        for row in rows:
            cur.execute(
                """
                UPDATE appendix_b_x11_stock_ledger
                   SET out_z = NOW()
                 WHERE purchase_date = %s AND iravi_voucher = %s
                   AND technical_name = %s AND barcode = %s
                   AND out_z IS NULL
                """,
                (row['purchase_date'], row['iravi_voucher'],
                 row['technical_name'], row['barcode']),
            )
            cur.execute(
                """
                INSERT INTO appendix_b_x11_stock_ledger
                    (purchase_date, iravi_voucher, supplier_voucher, branch, party,
                     technical_name, barcode, mdf_date, exp_date, in_out, qty)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (row['purchase_date'], row['iravi_voucher'], row['supplier_voucher'],
                 row['branch'], row['party'], row['technical_name'], row['barcode'],
                 row['mdf_date'], row['exp_date'], row['in_out'], row['qty']),
            )
