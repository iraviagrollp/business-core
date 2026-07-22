import csv
import json
import logging
import os
import re
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
_FILE_PREFIX = 'Supplier Accounts Export File'

# The upstream export is now comma-delimited CSV *content* (still shipped under a
# `.xlsx` filename/extension — do NOT try to read it with openpyxl). Header is line 1,
# data from line 2, one row per supplier (single sheet — the old General / Sheet1
# two-sheet workbook is gone; Sheet1 was always empty). 46 columns; several headers are
# DUPLICATED (MstId x4, EntityId/MenuItemId/TransId x2 — csv.DictReader collapses
# duplicates to last-wins; harmless because none of the 5 fields we map are duplicated).
# Columns are mapped by header NAME (not position):
#   Name      -> name (leading "<digits> - " prefix stripped first, e.g.
#                "29 - CLICKTECH RETAIL PRIVATE LIMITED" -> "CLICKTECH RETAIL PRIVATE
#                LIMITED"; names without that pattern are left unchanged)
#   GST       -> gst (blank/"NULL" -> None)
#   GSTValid  -> gst_valid (tri-state: blank/"NULL" -> None; else bool(int(...)))
#   City      -> city (title-cased if non-blank, else None)
#   StateName -> state (NOT the plain `State` column, which holds a numeric master id;
#                "29-Karnataka" -> "Karnataka" via split on the FIRST '-'; blank/"NULL"
#                -> None)

_NAME_PREFIX_RE = re.compile(r'^\s*\d+\s*-\s*')


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


def _extract_supplier_row(row: dict) -> dict | None:
    """Map one CSV DictReader row (by header name) to a supplier_accounts row dict.

    Returns None if `Name` is blank after stripping a leading "<digits> - " prefix, or
    if the row is an IRAVI own-company row.

    Transformations mirror the pre-CSV single-sheet ('General') behavior, plus the new
    numeric-prefix strip on the name:
      - name: strip a leading `^\\s*\\d+\\s*-\\s*` prefix (e.g. "29 - CLICKTECH RETAIL
        PRIVATE LIMITED" -> "CLICKTECH RETAIL PRIVATE LIMITED"; "AGROKING PESTICIDES
        PVT. LTD." is left unchanged), then strip whitespace; blank -> skip row
      - IRAVI FILTER: 'iravi' in name.lower() (applied AFTER the prefix strip) -> skip
      - gst: stripped string; blank or literal "NULL" -> None
      - gst_valid: tri-state — blank/"NULL" -> None; else bool(int(...)) (1 -> True,
        0 -> False)
      - city: title-cased if non-blank, else None
      - state (from StateName, NOT the blank/numeric-id `State` column): if it contains
        '-', take everything after the FIRST '-' (e.g. "29-Karnataka" -> "Karnataka");
        bare "Karnataka" stays; blank or literal "NULL" -> None
    """
    name_raw = str(row.get('Name') or '')
    name = _NAME_PREFIX_RE.sub('', name_raw).strip()
    if not name:
        return None

    # IRAVI FILTER: drop own-company rows (applied after the prefix strip)
    if 'iravi' in name.lower():
        return None

    # --- gst ---
    gst_raw = str(row.get('GST') or '').strip()
    gst = gst_raw if gst_raw and gst_raw.upper() != 'NULL' else None

    # --- gst_valid ---
    gst_valid_raw = str(row.get('GSTValid') or '').strip()
    if not gst_valid_raw or gst_valid_raw.upper() == 'NULL':
        gst_valid = None
    else:
        gst_valid = bool(int(gst_valid_raw))

    # --- city (title-case; source casing is inconsistent) ---
    city_raw = str(row.get('City') or '').strip()
    city = city_raw.title() if city_raw else None

    # --- state ---
    # "29-Karnataka" -> "Karnataka"; bare "Karnataka" -> "Karnataka"; blank/"NULL" -> None
    state_raw = str(row.get('StateName') or '').strip()
    if state_raw and state_raw.upper() != 'NULL':
        if '-' in state_raw:
            state = state_raw.split('-', 1)[1].strip() or None
        else:
            state = state_raw or None
    else:
        state = None

    return {
        'name': name,
        'gst': gst,
        'gst_valid': gst_valid,
        'city': city,
        'state': state,
    }


def _parse(src_path: str) -> list[dict]:
    """Parse the single-sheet CSV into one supplier_accounts row dict per supplier.

    First occurrence of a name wins on duplicate names.
    """
    rows_by_name: dict[str, dict] = {}

    with open(src_path, newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            parsed = _extract_supplier_row(row)
            if parsed is None:
                continue
            name = parsed['name']
            if name in rows_by_name:
                continue
            rows_by_name[name] = parsed

    logger.info('Total unique suppliers parsed: %d', len(rows_by_name))
    return [rows_by_name[name] for name in sorted(rows_by_name)]


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
