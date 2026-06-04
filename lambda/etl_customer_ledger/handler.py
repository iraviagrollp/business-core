import json
import logging
import os
import tempfile
from datetime import date, datetime, timezone
from urllib.parse import unquote_plus

import boto3
import openpyxl
import psycopg2

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client('s3')
secrets = boto3.client('secretsmanager')
events = boto3.client('events')

_BUCKET = os.environ['DATA_BUCKET']
_RAW_PREFIX = os.environ.get('RAW_PREFIX', 'raw/')
_PROCESSED_PREFIX = os.environ.get('PROCESSED_PREFIX', 'processed/')
_FILE_PREFIX = 'Ledger All Accounts'

# Contra Account → sub_category (checked first — more specific)
_CONTRA_SUBCATEGORY = {
    'CGST Output A/C': 'CGST',
    'SGST Output A/C': 'SGST',
    'IGST Output A/C': 'IGST',
    'Default Sales Account': 'Sale',
}

# Transaction Name → sub_category (fallback when Contra Account has no mapping)
_TXN_SUBCATEGORY = {
    'Bank Receipts': 'Bank Receipt',
    'Cash Receipts': 'Cash Receipt',
}


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
        logger.info('Parsed %d ledger rows', len(rows))

    conn = _get_db_conn()
    try:
        _upsert(conn, rows)
        conn.commit()
        logger.info('Upserted %d rows into customer_ledger', len(rows))
    finally:
        conn.close()

    s3.copy_object(Bucket=bucket, CopySource={'Bucket': bucket, 'Key': key}, Key=archive_key)
    s3.delete_object(Bucket=bucket, Key=key)
    logger.info('Archived source to s3://%s/%s', bucket, archive_key)

    events.put_events(Entries=[{
        'Source': 'iravi.etl',
        'DetailType': 'ETLCustomerLedgerSuccess',
        'Detail': json.dumps({'rows_processed': len(rows)}),
        'EventBusName': os.environ.get('EVENT_BUS_NAME', 'default'),
    }])
    logger.info('Emitted ETLCustomerLedgerSuccess rows=%d', len(rows))


def _parse(src_path: str) -> list[dict]:
    wb = openpyxl.load_workbook(src_path, data_only=True)
    ws = wb.active

    rows = []
    for row in ws.iter_rows(min_row=6, values_only=True):
        transaction_date_raw = row[0]
        voucher_no = str(row[1] or '').strip()
        transaction_name = str(row[2] or '').strip()
        account_name = str(row[4] or '').strip()
        contra_account = str(row[5] or '').strip()
        debit = float(row[6] or 0)
        credit = float(row[7] or 0)

        if transaction_date_raw is None:
            continue
        if not account_name:
            continue
        if voucher_no == 'Brought Forward':
            continue
        if debit == 0 and credit == 0:
            continue

        transaction_date = _parse_date(transaction_date_raw)
        if transaction_date is None:
            logger.warning('Unparseable date %r — skipping row', transaction_date_raw)
            continue

        category = 'Cr' if credit > 0 else 'Db'
        amount = credit if credit > 0 else debit
        sub_category = _map_sub_category(transaction_name, contra_account)

        rows.append({
            'transaction_date': transaction_date,
            'account_name': account_name,
            'category': category,
            'sub_category': sub_category,
            'amount': amount,
        })

    return rows


def _parse_date(val) -> date | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    s = str(val).strip()
    for fmt in ('%Y-%m-%d', '%d-%m-%Y', '%d/%m/%Y'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _map_sub_category(transaction_name: str, contra_account: str) -> str:
    if contra_account in _CONTRA_SUBCATEGORY:
        return _CONTRA_SUBCATEGORY[contra_account]
    if transaction_name in _TXN_SUBCATEGORY:
        return _TXN_SUBCATEGORY[transaction_name]
    return transaction_name or contra_account


def _upsert(conn, rows: list[dict]):
    with conn.cursor() as cur:
        for row in rows:
            biz_key = (
                row['transaction_date'], row['account_name'],
                row['category'], row['sub_category'],
            )
            cur.execute(
                """
                UPDATE customer_ledger
                   SET out_z = NOW()
                 WHERE transaction_date = %s AND account_name = %s
                   AND category = %s AND sub_category = %s
                   AND out_z IS NULL
                """,
                biz_key,
            )
            cur.execute(
                """
                INSERT INTO customer_ledger (transaction_date, account_name, category, sub_category, amount)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (row['transaction_date'], row['account_name'],
                 row['category'], row['sub_category'], row['amount']),
            )
