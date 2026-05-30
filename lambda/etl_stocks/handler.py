import logging
import os
import tempfile
from datetime import datetime, timezone
from urllib.parse import unquote_plus

import boto3

from process import process_stock_file

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client('s3')

_BUCKET = os.environ['DATA_BUCKET']
_RAW_PREFIX = os.environ.get('RAW_PREFIX', 'raw/')
_PROCESSED_PREFIX = os.environ.get('PROCESSED_PREFIX', 'processed/')
_STOCK_PREFIX = 'Current Stock Balances'
_RATES_PREFIX = 'Product Masters With Rates'


def lambda_handler(event, context):
    for record in event.get('Records', []):
        bucket = record['s3']['bucket']['name']
        key = unquote_plus(record['s3']['object']['key'])
        logger.info('S3 event: s3://%s/%s', bucket, key)

        filename = key.split('/')[-1]
        if not (filename.startswith(_STOCK_PREFIX) and filename.endswith('.xlsx')):
            logger.info('Skipping: %s', key)
            continue

        _process(bucket, key, filename)


def _process(bucket: str, key: str, filename: str):
    # "Current Stock Balances12-5-2026(21.42.18).xlsx" → "Stock - Processed 12-5-2026(21.42.18).xlsx"
    suffix = filename[len(_STOCK_PREFIX):]
    out_filename = f'Stock - Processed {suffix}'
    out_key = _PROCESSED_PREFIX + out_filename
    archive_key = _PROCESSED_PREFIX + 'raw/' + filename

    with tempfile.TemporaryDirectory() as tmp:
        src_path = os.path.join(tmp, 'stock.xlsx')
        dst_path = os.path.join(tmp, out_filename)
        rates_path = None

        logger.info('Downloading stock file s3://%s/%s', bucket, key)
        s3.download_file(bucket, key, src_path)

        rates_key = _find_latest(bucket, _RATES_PREFIX)
        if rates_key:
            rates_path = os.path.join(tmp, 'rates.xlsx')
            logger.info('Downloading rates file s3://%s/%s', bucket, rates_key)
            s3.download_file(bucket, rates_key, rates_path)
        else:
            logger.warning('No rates file found under s3://%s/%s%s* — proceeding without rates',
                           bucket, _RAW_PREFIX, _RATES_PREFIX)

        entry_date = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
        rows = process_stock_file(src_path, dst_path, entry_date=entry_date, rates_path=rates_path)
        logger.info('Processed %d rows', rows)

        s3.upload_file(dst_path, bucket, out_key)
        logger.info('Uploaded to s3://%s/%s', bucket, out_key)

    # Archive source outside the tempdir context (file already closed)
    s3.copy_object(Bucket=bucket, CopySource={'Bucket': bucket, 'Key': key}, Key=archive_key)
    s3.delete_object(Bucket=bucket, Key=key)
    logger.info('Archived source to s3://%s/%s', bucket, archive_key)


def _find_latest(bucket: str, filename_prefix: str) -> str | None:
    """Return the key of the most recently modified .xlsx matching RAW_PREFIX+filename_prefix."""
    resp = s3.list_objects_v2(Bucket=bucket, Prefix=_RAW_PREFIX + filename_prefix)
    objects = [o for o in resp.get('Contents', []) if o['Key'].endswith('.xlsx')]
    if not objects:
        return None
    return max(objects, key=lambda o: o['LastModified'])['Key']
