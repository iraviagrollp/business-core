import logging
import os
from urllib.parse import unquote_plus

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client('s3')

_PENDING_PREFIX = 'notifications/pending/'
_PROCESSED_PREFIX = 'notifications/processed/'


def lambda_handler(event, context):
    for record in event.get('Records', []):
        bucket = record['s3']['bucket']['name']
        key = unquote_plus(record['s3']['object']['key'])
        logger.info('S3 event: s3://%s/%s', bucket, key)

        if not key.startswith(_PENDING_PREFIX):
            logger.info('Skipping unexpected key: %s', key)
            continue

        _process(bucket, key)


def _process(bucket: str, key: str):
    filename = key[len(_PENDING_PREFIX):]
    archive_key = _PROCESSED_PREFIX + filename

    # Phase 2: read customer name from object metadata, look up mobile_no from
    # customer_details (prepend '91' for India), fetch bearer token from
    # Secrets Manager (iravi/dashboard/whatsapp), call Meta WhatsApp Cloud API
    # to send the HTML as a document message.
    #
    # head = s3.head_object(Bucket=bucket, Key=key)
    # customer_name = head['Metadata'].get('customer_name', '')
    # _send_whatsapp(bucket, key, customer_name)

    s3.copy_object(
        Bucket=bucket,
        CopySource={'Bucket': bucket, 'Key': key},
        Key=archive_key,
    )
    s3.delete_object(Bucket=bucket, Key=key)
    logger.info('Moved notification: s3://%s/%s → %s', bucket, key, archive_key)
