import csv
import json
import logging
import os
import re
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
events = boto3.client('events')

_BUCKET = os.environ['DATA_BUCKET']
_RAW_PREFIX = os.environ.get('RAW_PREFIX', 'raw/')
_PROCESSED_PREFIX = os.environ.get('PROCESSED_PREFIX', 'processed/')
_FILE_PREFIX = 'Borrowings'

_XLSX_MAGIC = b'PK\x03\x04'

_AMOUNT_STRIP_RE = re.compile(r'[^\d.\-]')

# Synthetic opening-balance header rows (one per account) that must never be
# ingested as transactions. Observed exact value in the source file's
# VoucherNo column is 'Brought Forward' (no surrounding whitespace, that
# exact casing) — compared stripped + case-insensitively for safety. No
# other spellings ("B/F", "Opening Balance", ...) have been observed.
_BROUGHT_FORWARD_VOUCHER = 'brought forward'


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

    conn = _get_db_conn()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            src_path = os.path.join(tmp, filename)
            logger.info('Downloading s3://%s/%s', bucket, key)
            s3.download_file(bucket, key, src_path)
            rows, skipped_brought_forward = _parse(src_path)
            logger.info(
                'Parsed %d borrowings rows (%d Brought Forward opening-balance rows skipped)',
                len(rows), skipped_brought_forward,
            )

        _upsert(conn, rows)
        conn.commit()
        logger.info('Upserted %d rows into borrowings', len(rows))
    finally:
        conn.close()

    s3.copy_object(Bucket=bucket, CopySource={'Bucket': bucket, 'Key': key}, Key=archive_key)
    s3.delete_object(Bucket=bucket, Key=key)
    logger.info('Archived source to s3://%s/%s', bucket, archive_key)

    events.put_events(Entries=[{
        'Source': 'iravi.etl',
        'DetailType': 'ETLBorrowingsSuccess',
        'Detail': json.dumps({'rows_processed': len(rows)}),
        'EventBusName': os.environ.get('EVENT_BUS_NAME', 'default'),
    }])
    logger.info('Emitted ETLBorrowingsSuccess rows=%d', len(rows))


def _clean_text(v) -> str:
    """Stringify + strip a text cell; the literal string 'NULL' (any case) -> ''.

    Several columns in this feed use the literal text "NULL" rather than a
    blank cell — this normalises both to the empty string.
    """
    if v is None:
        return ''
    s = str(v).strip()
    if s.upper() == 'NULL':
        return ''
    return s


def _to_amount(v) -> float:
    """Parse a Debit/Credit cell to float.

    Handles: real numeric types (openpyxl), blank/None, the literal string
    'NULL', and CSV-form strings with commas/spaces/currency symbols.
    """
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if s == '' or s.upper() == 'NULL':
        return 0.0
    s = _AMOUNT_STRIP_RE.sub('', s)
    if s in ('', '-', '.'):
        return 0.0
    return float(s)


def _parse_date(val) -> date | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    s = str(val).strip()
    if not s or s.upper() == 'NULL':
        return None
    for fmt in ('%Y-%m-%d', '%d-%m-%Y', '%d/%m/%Y'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _is_brought_forward_row(norm: dict) -> bool:
    """True if this row is a synthetic opening-balance header row (VoucherNo
    == 'Brought Forward', stripped + case-insensitive — see the module-level
    comment on _BROUGHT_FORWARD_VOUCHER). Safe against a missing/None cell.
    """
    voucher_no_raw = norm.get('voucherno')
    if voucher_no_raw is None:
        return False
    return str(voucher_no_raw).strip().lower() == _BROUGHT_FORWARD_VOUCHER


def _extract_row(norm: dict) -> dict | None:
    """Map one normalised (lowercase-header-keyed) row dict to a borrowings row.

    Only the following normalised keys are consumed: date, voucherno,
    transactionname, account, debit, credit. Every other column in the file
    (28 total) is ignored. Returns None (skip) if Date, VoucherNo, or Account
    is missing/empty after cleaning.
    """
    date_raw = norm.get('date')
    voucher_no = _clean_text(norm.get('voucherno'))
    transaction_name = _clean_text(norm.get('transactionname'))
    account = _clean_text(norm.get('account'))
    debit = _to_amount(norm.get('debit'))
    credit = _to_amount(norm.get('credit'))

    if date_raw is None or (isinstance(date_raw, str) and not date_raw.strip()):
        return None
    if not voucher_no:
        return None
    if not account:
        return None

    transaction_date = _parse_date(date_raw)
    if transaction_date is None:
        logger.warning('Unparseable date %r — skipping row', date_raw)
        return None

    return {
        'transaction_date': transaction_date,
        'voucher_no': voucher_no,
        'transaction_name': transaction_name or None,
        'account': account,
        'debit': debit,
        'credit': credit,
    }


def _parse(src_path: str) -> tuple[list[dict], int]:
    """Parse the Borrowings feed, supporting BOTH a real binary xlsx workbook
    (sniffed via the PK\\x03\\x04 zip magic) and CSV text shipped under a
    `.xlsx` filename/extension (same dual-format uncertainty as the other
    FUSIL PRO exports) — production may send either. Header row 1 (any
    casing), data from row 2. Headers are matched case-insensitively via a
    normalised lowercase-key lookup so `ACCOUNT` (all caps, as in the sample)
    and `Date`/`VoucherNo`/`TransactionName`/`Debit`/`Credit` (TitleCase, as
    in the other feeds) both resolve correctly.

    Returns (rows, skipped_brought_forward) — synthetic opening-balance
    header rows (VoucherNo == 'Brought Forward') are filtered out before
    _extract_row is even called and are never ingested as transactions.
    """
    with open(src_path, 'rb') as f:
        sig = f.read(4)

    rows = []
    skipped_brought_forward = 0

    if sig == _XLSX_MAGIC:
        wb = openpyxl.load_workbook(src_path, read_only=True, data_only=True)
        ws = wb.worksheets[0]
        row_iter = ws.iter_rows(values_only=True)
        header_row = next(row_iter, None)
        if header_row is None:
            return rows, skipped_brought_forward
        headers = [str(h).strip().lower() if h is not None else '' for h in header_row]
        for values in row_iter:
            norm = dict(zip(headers, values))
            if _is_brought_forward_row(norm):
                skipped_brought_forward += 1
                continue
            parsed = _extract_row(norm)
            if parsed is not None:
                rows.append(parsed)
    else:
        with open(src_path, newline='', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                norm = {(k.strip().lower() if k else ''): v for k, v in row.items()}
                if _is_brought_forward_row(norm):
                    skipped_brought_forward += 1
                    continue
                parsed = _extract_row(norm)
                if parsed is not None:
                    rows.append(parsed)

    return rows, skipped_brought_forward


def _upsert(conn, rows: list[dict]):
    """Uni-temporal milestoning: close existing active row then insert fresh row.

    Natural key = (transaction_date, voucher_no, account) — transaction_date IS
    part of the key here (unlike the snapshot tables), per the borrowings schema.
    Mirrors etl_customer_ledger's _upsert exactly: every parsed row is
    unconditionally closed-then-reinserted (no value-changed check).
    """
    with conn.cursor() as cur:
        for row in rows:
            cur.execute(
                """
                UPDATE borrowings
                   SET out_z = NOW()
                 WHERE transaction_date = %s AND voucher_no = %s AND account = %s
                   AND out_z IS NULL
                """,
                (row['transaction_date'], row['voucher_no'], row['account']),
            )
            cur.execute(
                """
                INSERT INTO borrowings
                    (transaction_date, voucher_no, transaction_name, account, debit, credit)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (row['transaction_date'], row['voucher_no'], row['transaction_name'],
                 row['account'], row['debit'], row['credit']),
            )
