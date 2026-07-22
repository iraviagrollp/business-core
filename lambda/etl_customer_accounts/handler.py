import csv
import json
import logging
import os
import tempfile
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
_FILE_PREFIX = 'Customer Accounts Export File'

# The upstream export is now comma-delimited CSV *content* (still shipped under a
# `.xlsx` filename/extension — do NOT try to read it with openpyxl). Header is line 1,
# data from line 2, one row per customer (single sheet — the old General / Delivery
# Address two-sheet merge is gone). 27 columns; `MstId` appears twice (harmless — never
# read). Columns are mapped by header NAME (not position):
#   Name       -> customer_name (uppercased)
#   Code       -> customer_code
#   Address3   -> district (title-cased)
#   City       -> city (title-cased)
#   StateName  -> state (mapped via _STATE_MAP; NOTE: not the plain `State` column,
#                 which is blank in this feed)
#   PIN        -> pin
#   MobileNo   -> mobile_no (last 10 digits if > 10)

_STATE_MAP = {
    '37-Andhra Pradesh': 'AP',
    '36-Telangana': 'TG',
    # FLAG: GST-code pattern assumed from the AP/TG entries above — NOT yet
    # verified against a real Customer Accounts export. Confirm the exact
    # "<code>-<StateName>" string FUSIL emits for Tamil Nadu / Odisha before
    # relying on these in production (2026-07-12).
    '33-Tamil Nadu': 'TN',
    '21-Odisha':     'OR',
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
        logger.info('Parsed %d customer rows', len(rows))

    conn = _get_db_conn()
    try:
        _upsert(conn, rows)
        logger.info('Upserted %d rows into customer_details', len(rows))
    finally:
        conn.close()

    s3.copy_object(Bucket=bucket, CopySource={'Bucket': bucket, 'Key': key}, Key=archive_key)
    s3.delete_object(Bucket=bucket, Key=key)
    logger.info('Archived source to s3://%s/%s', bucket, archive_key)


def _extract_customer_row(row: dict) -> dict | None:
    """Map one CSV DictReader row (by header name) to a customer_details row dict.

    Returns None if `Name` is blank (row is skipped).

    Transformations mirror the pre-CSV two-sheet behavior exactly:
      - customer_name: uppercased
      - customer_code: stripped string; blank -> None
      - district (from Address3): title-cased; blank -> None
      - city: title-cased; blank -> None
      - state (from StateName, NOT the blank `State` column): mapped via _STATE_MAP;
        no match -> None
      - pin: stripped string; blank -> None
      - mobile_no: strip spaces; last 10 digits if > 10; blank -> None
    """
    name = str(row.get('Name') or '').strip()
    if not name:
        return None

    code_raw = str(row.get('Code') or '').strip()
    code = code_raw or None

    district_raw = str(row.get('Address3') or '').strip()
    city_raw = str(row.get('City') or '').strip()
    state_raw = str(row.get('StateName') or '').strip()
    pin_raw = str(row.get('PIN') or '').strip()

    mobile_raw = str(row.get('MobileNo') or '').replace(' ', '').strip()
    mobile_no = mobile_raw or None
    if mobile_no and len(mobile_no) > 10:
        mobile_no = mobile_no[-10:]

    return {
        'customer_name': name.upper(),
        'district': district_raw.title() if district_raw else None,
        'city': city_raw.title() if city_raw else None,
        'state': _STATE_MAP.get(state_raw),
        'pin': pin_raw or None,
        'mobile_no': mobile_no,
        'customer_code': code,
    }


def _parse(src_path: str) -> list[dict]:
    """Parse the single-sheet CSV into one customer_details row dict per customer.

    First occurrence of a name wins on duplicate names (matches the old behavior of
    both `_build_code_lookup` and `_build_delivery_lookup`).
    """
    rows_by_name: dict[str, dict] = {}

    with open(src_path, newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            parsed = _extract_customer_row(row)
            if parsed is None:
                continue
            name = parsed['customer_name']
            if name in rows_by_name:
                continue
            rows_by_name[name] = parsed

    logger.info('Total unique customers parsed: %d', len(rows_by_name))
    return [rows_by_name[name] for name in sorted(rows_by_name)]


def _upsert(conn, rows: list[dict]):
    """Uni-temporal milestoning: close existing active row then insert fresh row.

    Natural key = customer_name.  Partial unique index
    uix_customer_details_active ON customer_details (customer_name)
    WHERE out_z IS NULL ensures at most one active row per customer at any
    time. `id`, `in_z`, `out_z` are handled by column defaults — never set
    explicitly here.

    Each export is treated as the authoritative full snapshot: after the
    per-row close+insert loop, any still-active row whose customer_name is
    NOT present in this file is retired (closed) so it disappears from the
    UI. Names passed to the retire step are exactly the uppercased values
    produced by _parse(), so the match is exact.

    Empty-file guard: if rows is empty, skip the upsert loop AND the retire
    step entirely (a corrupt/empty export must never wipe the whole table).

    Commits once at the end, only when rows were actually processed.
    """
    if not rows:
        logger.warning(
            'customer accounts: 0 rows parsed — skipping upsert/retire to avoid wiping the table'
        )
        return

    with conn.cursor() as cur:
        for row in rows:
            cur.execute(
                """
                UPDATE customer_details
                   SET out_z = NOW()
                 WHERE customer_name = %s
                   AND out_z IS NULL
                """,
                (row['customer_name'],),
            )
            cur.execute(
                """
                INSERT INTO customer_details
                    (customer_name, district, city, state, pin, mobile_no, customer_code)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (row['customer_name'], row['district'], row['city'],
                 row['state'], row['pin'], row['mobile_no'], row['customer_code']),
            )

        names = [row['customer_name'] for row in rows]
        cur.execute(
            """
            UPDATE customer_details
               SET out_z = NOW()
             WHERE out_z IS NULL
               AND NOT (customer_name = ANY(%s))
            """,
            (names,),
        )

    conn.commit()
