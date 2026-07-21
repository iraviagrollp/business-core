import csv
import json
import logging
import os
import tempfile
from datetime import date, datetime
from urllib.parse import unquote_plus

import boto3
import psycopg2

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client('s3')
secrets = boto3.client('secretsmanager')

_BUCKET = os.environ['DATA_BUCKET']
_RAW_PREFIX = os.environ.get('RAW_PREFIX', 'raw/')
_PROCESSED_PREFIX = os.environ.get('PROCESSED_PREFIX', 'processed/')
_FILE_PREFIX = 'AppendixPurchaseReport'

# The upstream export is now comma-delimited CSV *content* (still shipped under a
# `.xlsx` filename/extension — do NOT try to read it with openpyxl). Header is line 1,
# data from line 2. Columns are mapped by header NAME (not position):
# ProductId -> product/technical_name, Qty -> qty, Rate -> rate, Gross -> gross,
# AV -> av, Barcodes -> barcode/barcodes, Narration -> narration, Date -> purchase_date,
# BranchId -> branch, AccountId -> party, RefBillNo -> ref_bill_no,
# RefBillDate -> ref_bill_date, VoucherNo -> iravi_voucher/voucher_no.


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
            ledger_rows, purchase_rows = _parse(src_path, barcode_dates)
            logger.info('Parsed %d purchase ledger rows, %d purchase rows', len(ledger_rows), len(purchase_rows))
            _upsert(conn, ledger_rows)
            _upsert_purchases(conn, purchase_rows)
            conn.commit()
            logger.info('Upserted %d rows into appendix_b_x11_stock_ledger, %d rows into purchases',
                         len(ledger_rows), len(purchase_rows))
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


def _to_float(v):
    if v is None:
        return None
    s = str(v).strip()
    if s == '':
        return None
    return float(s)


def _extract_purchase_row(row: dict) -> dict | None:
    """Map one CSV DictReader row (by header name) to a purchase-row dict, or None to skip."""
    purchase_date_raw = row.get('Date')
    iravi_voucher = str(row.get('VoucherNo') or '').strip()

    if purchase_date_raw is None or not str(purchase_date_raw).strip():
        return None
    if not iravi_voucher:
        return None

    product = str(row.get('ProductId') or '').strip()
    if not product:
        return None

    purchase_date = _parse_date(purchase_date_raw)
    if purchase_date is None:
        logger.warning('Unparseable date %r — skipping row', purchase_date_raw)
        return None

    branch = str(row.get('BranchId') or '').strip()
    party = str(row.get('AccountId') or '').strip()
    ref_bill_no = str(row.get('RefBillNo') or '').strip() or None
    ref_bill_date = _parse_date(row.get('RefBillDate'))
    barcode_raw = str(row.get('Barcodes') or '').strip()

    return {
        'purchase_date': purchase_date,
        'voucher_no': iravi_voucher,
        'branch': branch,
        'party': party,
        'ref_bill_no': ref_bill_no,
        'ref_bill_date': ref_bill_date,
        'product': product,
        'qty': _to_float(row.get('Qty')),
        'rate': _to_float(row.get('Rate')),
        'gross': _to_float(row.get('Gross')),
        'av': _to_float(row.get('AV')),
        'barcodes': barcode_raw or None,
        'narration': str(row.get('Narration') or '').strip() or None,
        'purchase_return': 'N',
    }


def _parse(src_path: str, barcode_dates: dict) -> tuple[list[dict], list[dict]]:
    ledger_rows = []
    purchase_rows = []
    skipped_multi = 0

    with open(src_path, newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            parsed = _extract_purchase_row(row)
            if parsed is None:
                continue

            purchase_rows.append(parsed)

            barcode_raw = parsed['barcodes'] or ''
            barcodes = [b.strip() for b in barcode_raw.split(',') if b.strip()]
            if len(barcodes) != 1:
                skipped_multi += 1
                continue
            barcode = barcodes[0]

            mdf_date, exp_date = barcode_dates.get((parsed['product'], barcode), (None, None))

            ledger_rows.append({
                'purchase_date': parsed['purchase_date'],
                'iravi_voucher': parsed['voucher_no'],
                'supplier_voucher': parsed['ref_bill_no'],
                'branch': parsed['branch'] or None,
                'party': parsed['party'] or None,
                'technical_name': parsed['product'],
                'barcode': barcode,
                'mdf_date': mdf_date,
                'exp_date': exp_date,
                'in_out': 'In',
                'qty': parsed['qty'],
            })

    if skipped_multi:
        logger.info('Skipped %d rows for ledger (multiple barcodes)', skipped_multi)
    return ledger_rows, purchase_rows


def _parse_date(val) -> date | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    s = str(val).strip()
    if not s:
        return None
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


def _upsert_purchases(conn, rows: list[dict]):
    with conn.cursor() as cur:
        for row in rows:
            cur.execute(
                """
                UPDATE purchases
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
                INSERT INTO purchases
                    (purchase_date, voucher_no, branch, party, ref_bill_no, ref_bill_date,
                     product, qty, rate, gross, av, barcodes, narration, purchase_return)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (row['purchase_date'], row['voucher_no'], row['branch'], row['party'],
                 row['ref_bill_no'], row['ref_bill_date'], row['product'], row['qty'],
                 row['rate'], row['gross'], row['av'], row['barcodes'], row['narration'],
                 row['purchase_return']),
            )
