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
_FILE_PREFIX = 'AppendixRetSales'

# Column indices (0-based), header in row 5, data from row 6:
# [0]=Date, [1]=Voucher No, [2]=Branch, [3]=Party, [4]=Ref BillNo, [5]=Ref BillDate,
# [6]=Product, [7]=Qty, [8]=Rate, [9]=Gross, [14]=AV, [22]=Barcodes


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
            ledger_rows, sales_rows = _parse(src_path, barcode_dates)
            logger.info('Parsed %d sale return ledger rows, %d sales rows', len(ledger_rows), len(sales_rows))
            _upsert(conn, ledger_rows)
            _upsert_sales(conn, sales_rows)
            conn.commit()
            logger.info('Upserted %d rows into appendix_b_x11_stock_ledger, %d rows into sales',
                         len(ledger_rows), len(sales_rows))
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


def _parse(src_path: str, barcode_dates: dict) -> tuple[list[dict], list[dict]]:
    wb = openpyxl.load_workbook(src_path, data_only=True)
    ws = wb.active

    ledger_rows = []
    sales_rows = []
    skipped_multi = 0
    for row in ws.iter_rows(min_row=6, values_only=True):
        purchase_date_raw = row[0]
        iravi_voucher = str(row[1] or '').strip()

        if purchase_date_raw is None:
            continue
        if not iravi_voucher:
            continue

        # Product: the field is a CSV — strip all commas
        product = str(row[6] or '').replace(',', '').strip()
        if not product:
            continue

        purchase_date = _parse_date(purchase_date_raw)
        if purchase_date is None:
            logger.warning('Unparseable date %r — skipping row', purchase_date_raw)
            continue

        branch = str(row[2] or '').strip()
        party = str(row[3] or '').strip()
        ref_bill_no = str(row[4] or '').strip() or None
        ref_bill_date = _parse_date(row[5])
        barcode_raw = str(row[22] or '').strip()

        sales_rows.append({
            'purchase_date': purchase_date,
            'voucher_no': iravi_voucher,
            'branch': branch,
            'party': party,
            'ref_bill_no': ref_bill_no,
            'ref_bill_date': ref_bill_date,
            'product': product,
            'qty': float(row[7]) if row[7] is not None else None,
            'rate': float(row[8]) if row[8] is not None else None,
            'gross': float(row[9]) if row[9] is not None else None,
            'av': float(row[14]) if row[14] is not None else None,
            'barcodes': barcode_raw or None,
            'narration': None,
            'sales_return': 'Y',
        })

        barcodes = [b.strip() for b in barcode_raw.split(',') if b.strip()]
        if len(barcodes) != 1:
            skipped_multi += 1
            continue
        barcode = barcodes[0]

        mdf_date, exp_date = barcode_dates.get((product, barcode), (None, None))

        ledger_rows.append({
            'purchase_date': purchase_date,
            'iravi_voucher': iravi_voucher,
            'supplier_voucher': ref_bill_no,
            'branch': branch or None,
            'party': party or None,
            'technical_name': product,
            'barcode': barcode,
            'mdf_date': mdf_date,
            'exp_date': exp_date,
            'in_out': 'In',
            'qty': float(row[7]) if row[7] is not None else None,
        })

    if skipped_multi:
        logger.info('Skipped %d rows for ledger (multiple barcodes)', skipped_multi)
    return ledger_rows, sales_rows


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


def _upsert_sales(conn, rows: list[dict]):
    with conn.cursor() as cur:
        for row in rows:
            cur.execute(
                """
                UPDATE sales
                   SET out_z = NOW()
                 WHERE purchase_date = %s AND voucher_no = %s AND branch = %s
                   AND party = %s AND product = %s
                   AND COALESCE(barcodes, '') = COALESCE(%s, '')
                   AND out_z IS NULL
                """,
                (row['purchase_date'], row['voucher_no'], row['branch'],
                 row['party'], row['product'], row['barcodes']),
            )
            cur.execute(
                """
                INSERT INTO sales
                    (purchase_date, voucher_no, branch, party, ref_bill_no, ref_bill_date,
                     product, qty, rate, gross, av, barcodes, narration, sales_return)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (row['purchase_date'], row['voucher_no'], row['branch'], row['party'],
                 row['ref_bill_no'], row['ref_bill_date'], row['product'], row['qty'],
                 row['rate'], row['gross'], row['av'], row['barcodes'], row['narration'],
                 row['sales_return']),
            )
