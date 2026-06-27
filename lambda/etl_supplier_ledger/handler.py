"""
etl_supplier_ledger — EventBridge-triggered Lambda.

Reads the same "Ledger All Accounts*.xlsx" export used by etl_customer_ledger
but keeps ONLY rows where col[10] (Account Group) == 'All Supplier Accounts'.
Applies purchase-side category/sub-category logic and writes to the
supplier_ledger table using uni-temporal milestoning.

This Lambda is STRICTLY READ-ONLY on S3:
  - It does NOT copy, move, or delete any S3 object.
  - It does NOT emit any EventBridge event.
  - etl_customer_ledger owns the file lifecycle (archive to processed/raw/).

Trigger: EventBridge "Object Created" rule (NOT an S3 Records event).
Event shape:
  event['detail']['bucket']['name']  -> bucket
  event['detail']['object']['key']   -> URL-encoded key (spaces as %20)

If the raw object is already gone (etl_customer_ledger archived it), the
Lambda falls back to downloading from {PROCESSED_PREFIX}raw/{filename}.

Environment variables:
  DATA_BUCKET      – S3 bucket name (required)
  DB_SECRET_ARN    – Secrets Manager ARN for RDS credentials (required)
  RAW_PREFIX       – S3 prefix for raw uploads  (default: 'raw/')
  PROCESSED_PREFIX – S3 prefix for processed/   (default: 'processed/')
"""

import json
import logging
import os
import tempfile
from datetime import date, datetime
from urllib.parse import unquote  # EventBridge encodes spaces as %20, not '+'

import boto3
import botocore.exceptions
import openpyxl
import psycopg2

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client('s3')
secrets = boto3.client('secretsmanager')

_BUCKET = os.environ['DATA_BUCKET']
_RAW_PREFIX = os.environ.get('RAW_PREFIX', 'raw/')
_PROCESSED_PREFIX = os.environ.get('PROCESSED_PREFIX', 'processed/')
_FILE_PREFIX = 'Ledger All Accounts'

# Contra Account → sub_category (purchase-side)
# Purchase Vouchers credit the supplier; contra accounts are the debit side.
_PURCHASE_CONTRA_SUBCATEGORY = {
    'CGST Input A/C': 'CGST',
    'SGST Input A/C': 'SGST',
    'IGST Input A/C': 'IGST',
    'Default Purchase Account': 'Purchase',
    'Roundoff A/C': 'Roundoff',
}

# Transaction Name → sub_category (fallback for payment / receipt transactions)
_TXN_SUBCATEGORY = {
    'Bank Payments': 'Bank Payment',
    'Cash Payments': 'Cash Payment',
    'Bank Receipts': 'Bank Receipt',
    'Cash Receipts': 'Cash Receipt',
}


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Lambda entry point
# ---------------------------------------------------------------------------

def lambda_handler(event, context):
    # Parse the EventBridge "Object Created" event shape.
    # This is NOT the S3 Records shape used by etl_customer_ledger.
    try:
        bucket = event['detail']['bucket']['name']
        # EventBridge encodes spaces as %20; use unquote (not unquote_plus).
        key = unquote(event['detail']['object']['key'])
    except (KeyError, TypeError) as exc:
        logger.error('Unexpected event shape: %s — %s', exc, json.dumps(event))
        return

    logger.info('EventBridge Object Created: s3://%s/%s', bucket, key)

    filename = key.split('/')[-1]
    if not (filename.startswith(_FILE_PREFIX) and filename.endswith('.xlsx')):
        logger.info('Skipping non-matching file: %s', key)
        return

    _process(bucket, key, filename)


# ---------------------------------------------------------------------------
# Core processing
# ---------------------------------------------------------------------------

def _process(bucket: str, key: str, filename: str):
    """Download, parse, and upsert supplier ledger rows.  Read-only on S3."""
    fallback_key = _PROCESSED_PREFIX + 'raw/' + filename

    conn = _get_db_conn()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            src_path = os.path.join(tmp, filename)
            used_key = _download_with_fallback(bucket, key, fallback_key, src_path)
            logger.info('Downloaded from s3://%s/%s', bucket, used_key)
            rows = _parse(src_path)
            logger.info('Parsed %d supplier ledger rows (after Account Group filter)', len(rows))

        _upsert(conn, rows)
        conn.commit()
        logger.info('Upserted %d rows into supplier_ledger', len(rows))
    finally:
        conn.close()

    # Strictly read-only on S3 — no copy, no delete, no EventBridge event.
    # etl_customer_ledger owns the file lifecycle.


def _download_with_fallback(bucket: str, primary_key: str, fallback_key: str, dest: str) -> str:
    """Download from primary_key; fall back to fallback_key on 404/NoSuchKey.

    etl_customer_ledger may have already archived the file to processed/raw/
    before this Lambda runs.  In that case the primary key no longer exists and
    we transparently read the archived copy.

    Returns the key that was successfully downloaded.
    """
    try:
        s3.download_file(bucket, primary_key, dest)
        return primary_key
    except botocore.exceptions.ClientError as exc:
        code = exc.response['Error']['Code']
        if code in ('404', 'NoSuchKey'):
            logger.info(
                'Primary key not found (%s) — falling back to s3://%s/%s',
                code, bucket, fallback_key,
            )
            s3.download_file(bucket, fallback_key, dest)
            return fallback_key
        raise


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------

def _parse(src_path: str) -> list[dict]:
    """Parse the active sheet of the Ledger All Accounts workbook.

    Column layout (0-indexed), data from min_row=6:
      [0] transaction_date   [1] voucher_no      [2] transaction_name
      [4] account_name       [5] contra_account  [6] debit   [7] credit
      [10] account_group

    Only rows where account_group == 'All Supplier Accounts' (case-insensitive)
    are kept.  IRAVI own-company rows within that group are explicitly dropped.
    No database read is required for filtering — the ledger file itself carries
    the account group in col[10].
    """
    wb = openpyxl.load_workbook(src_path, data_only=True)
    ws = wb.active  # Sheet is named "Invoice" in the real export

    rows = []
    for row in ws.iter_rows(min_row=6, values_only=True):
        transaction_date_raw = row[0]
        voucher_no           = str(row[1] or '').strip()
        transaction_name     = str(row[2] or '').strip()
        account_name         = str(row[4] or '').strip()
        contra_account       = str(row[5] or '').strip()
        debit                = float(row[6] or 0)
        credit               = float(row[7] or 0)
        account_group        = str(row[10] or '').strip()

        # --- Sign normalization (identical to etl_customer_ledger) ---
        # FUSIL writes some adjustments (e.g. Roundoff) as a negative value on
        # one side.  A negative debit is economically a credit of its magnitude.
        if debit < 0:
            credit += -debit
            debit = 0.0
        if credit < 0:
            debit += -credit
            credit = 0.0

        # --- Skip rules ---
        if transaction_date_raw is None:
            continue
        if not account_name:
            continue
        if voucher_no == 'Brought Forward':
            continue
        if debit == 0 and credit == 0:
            continue
        # Defensive: drop any row contra'd against the sales account
        # (mirrors customer_ledger's 'Default Purchase Account' skip on the
        # customer side; should not appear in supplier rows but guards against
        # unexpected cross-account data).
        if contra_account == 'Default Sales Account':
            continue
        # Exclude sales-side transactions: a supplier account that is mis-used
        # on a Sales Invoice in FUSIL (e.g. a debtor whose Account Group is
        # 'All Supplier Accounts') must not pollute the purchase/payable ledger.
        # Covers 'Sales Invoice' and 'Sales Invoice Returns' (trailing spaces
        # already stripped above).  This also eliminates the natural-key
        # collision caused by duplicate GST-output legs on the same voucher.
        if transaction_name.lower().startswith('sales'):
            continue
        # Account Group filter: keep only supplier rows identified by the
        # ledger file itself (col[10]).  Case-insensitive comparison for safety.
        if account_group.lower() != 'all supplier accounts':
            continue
        # Explicit IRAVI exclusion: IRAVI own-company accounts appear under
        # 'All Supplier Accounts' in the ledger but must not land in
        # supplier_ledger.
        if 'iravi' in account_name.lower():
            continue

        transaction_date = _parse_date(transaction_date_raw)
        if transaction_date is None:
            logger.warning('Unparseable date %r — skipping row', transaction_date_raw)
            continue

        # --- Category (purchase-side semantics) ---
        # Purchase Vouchers credit the supplier (liability increases): category = 'Cr'
        # Bank/Cash Payments debit the supplier (liability decreases):  category = 'Db'
        category = 'Cr' if credit > 0 else 'Db'
        amount   = credit if credit > 0 else debit

        # --- Sub-category resolution order ---
        # 1. Contra account mapping (CGST/SGST/IGST/Purchase/Roundoff)
        # 2. Transaction name mapping (Bank Payment / Cash Payment / etc.)
        # 3. Fallback: transaction_name then contra_account
        sub_category = (
            _PURCHASE_CONTRA_SUBCATEGORY.get(contra_account)
            or _TXN_SUBCATEGORY.get(transaction_name)
            or transaction_name
            or contra_account
        )

        rows.append({
            'transaction_date': transaction_date,
            'voucher_no':       voucher_no,
            'account_name':     account_name,
            'category':         category,
            'sub_category':     sub_category,
            'amount':           amount,
        })

    return rows


def _parse_date(val) -> date | None:
    """Parse a cell value into a date.  Returns None if unparseable."""
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


# ---------------------------------------------------------------------------
# Upsert (uni-temporal milestoning)
# ---------------------------------------------------------------------------

def _upsert(conn, rows: list[dict]):
    """Close-then-insert milestoning into supplier_ledger.

    Natural key: (transaction_date, voucher_no, account_name, category, sub_category).
    Partial unique index WHERE out_z IS NULL guarantees at most one active row
    per natural key.  All rows are written in a single transaction; the caller
    commits once after this function returns.
    """
    with conn.cursor() as cur:
        for row in rows:
            biz_key = (
                row['transaction_date'],
                row['voucher_no'],
                row['account_name'],
                row['category'],
                row['sub_category'],
            )
            cur.execute(
                """
                UPDATE supplier_ledger
                   SET out_z = NOW()
                 WHERE transaction_date = %s
                   AND voucher_no       = %s
                   AND account_name     = %s
                   AND category         = %s
                   AND sub_category     = %s
                   AND out_z IS NULL
                """,
                biz_key,
            )
            cur.execute(
                """
                INSERT INTO supplier_ledger
                    (transaction_date, voucher_no, account_name, category, sub_category, amount)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    row['transaction_date'],
                    row['voucher_no'],
                    row['account_name'],
                    row['category'],
                    row['sub_category'],
                    row['amount'],
                ),
            )
