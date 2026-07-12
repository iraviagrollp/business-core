import json
import logging
import os
import tempfile
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
_FILE_PREFIX = 'Supplier Accounts Export File'

# Column indices (0-based), General sheet, row 1 is header, data starts row 2
# [0]=Name, [6]=GST, [7]=GSTValid, [12]=City, [13]=State


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
        logger.info('Parsed %d supplier rows', len(rows))

    conn = _get_db_conn()
    try:
        _upsert(conn, rows)
        logger.info('Upserted %d rows into supplier_accounts', len(rows))
    finally:
        conn.close()

    s3.copy_object(Bucket=bucket, CopySource={'Bucket': bucket, 'Key': key}, Key=archive_key)
    s3.delete_object(Bucket=bucket, Key=key)
    logger.info('Archived source to s3://%s/%s', bucket, archive_key)


def _parse(src_path: str) -> list[dict]:
    wb = openpyxl.load_workbook(src_path, data_only=True)

    # Source data is in the 'General' sheet; 'Sheet1' is empty — ignore it.
    ws = wb['General']

    rows = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        # --- name ---
        name = str(row[0] or '').strip()
        if not name:
            continue

        # IRAVI FILTER: drop own-company rows (e.g. "IRAVI AGRO LIFE HYD", "IRAVI AGRO LIFE LLP - GNT")
        if 'iravi' in name.lower():
            logger.debug('Skipping IRAVI own-company row: %s', name)
            continue

        # --- gst ---
        gst = str(row[6] or '').strip() or None

        # --- gst_valid ---
        # row[7] is None -> NULL; else cast to int: 1 -> True, 0 -> False.
        # Treat 0 and None distinctly: None means no GST registered; 0 means present-but-invalid.
        gst_valid_raw = row[7]
        if gst_valid_raw is None:
            gst_valid = None
        else:
            gst_valid = bool(int(gst_valid_raw))

        # --- city (title-case; source casing is inconsistent) ---
        city = str(row[12] or '').strip().title() or None

        # --- state ---
        # "29-Karnataka" -> "Karnataka"; bare "Karnataka" -> "Karnataka"; blank -> None
        state_raw = str(row[13] or '').strip()
        if state_raw:
            if '-' in state_raw:
                state = state_raw.split('-', 1)[1].strip() or None
            else:
                state = state_raw or None
        else:
            state = None

        rows.append({
            'name': name,
            'gst': gst,
            'gst_valid': gst_valid,
            'city': city,
            'state': state,
        })

    return rows


def _upsert(conn, rows: list[dict]):
    """Uni-temporal milestoning: close existing active row then insert fresh row.

    Natural key = name.  Partial unique index on (name) WHERE out_z IS NULL
    ensures at most one active row per supplier name at any time.

    Each export is treated as the authoritative full snapshot: after the
    per-row close+insert loop, any still-active row whose name is NOT present
    in this file is retired (closed) so it disappears from the UI.

    Empty-file guard: if rows is empty, skip the upsert loop AND the retire
    step entirely (a corrupt/empty export must never wipe the whole table).

    Commits once at the end, only when rows were actually processed.
    """
    if not rows:
        logger.warning(
            'supplier accounts: 0 rows parsed — skipping upsert/retire to avoid wiping the table'
        )
        return

    with conn.cursor() as cur:
        for row in rows:
            cur.execute(
                """
                UPDATE supplier_accounts
                   SET out_z = NOW()
                 WHERE name = %s
                   AND out_z IS NULL
                """,
                (row['name'],),
            )
            cur.execute(
                """
                INSERT INTO supplier_accounts (name, gst, gst_valid, city, state)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (row['name'], row['gst'], row['gst_valid'], row['city'], row['state']),
            )

        names = [row['name'] for row in rows]
        cur.execute(
            """
            UPDATE supplier_accounts
               SET out_z = NOW()
             WHERE out_z IS NULL
               AND NOT (name = ANY(%s))
            """,
            (names,),
        )

    conn.commit()
