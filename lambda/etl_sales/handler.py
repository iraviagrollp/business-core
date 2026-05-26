import json
import logging
import os

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def lambda_handler(event, context):
    """
    S3 trigger: raw/*.xlsx ObjectCreated.
    Filters to sales files, then parses and upserts into fact_sales + dim_customers.
    """
    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = record["s3"]["object"]["key"]
        logger.info(f"S3 event: s3://{bucket}/{key}")

        if not _is_sales_file(key):
            logger.info(f"Skipping non-sales file: {key}")
            continue

        # TODO: implement
        # 1. Download xlsx from S3
        # 2. Parse: skip rows 1-5, detect/skip total rows
        #    Columns: Date, Voucher No, Branch, Party, Party GSTN,
        #             Qty, Gross, Disc, AV, CGST, SGST, IGST, Net, BillValue
        # 3. Upsert dim_customers on customer_name
        # 4. Upsert fact_sales on (voucher_no, transaction_date)
        # 5. Write etl_runs row (status=success/failed)
        # 6. Emit EventBridge event: source=iravi.etl, detail-type=ETLSalesSuccess
        # 7. Move file from raw/ to processed/


def _is_sales_file(key: str) -> bool:
    filename = key.split("/")[-1]
    return filename.startswith("RGF Sales Book") and filename.endswith(".xlsx")
