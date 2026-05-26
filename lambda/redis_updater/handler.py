import json
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def lambda_handler(event, context):
    """
    EventBridge trigger: ETLSalesSuccess from etl_sales Lambda.
    Reads key sales metrics from RDS and writes to ElastiCache (7-day TTL).
    Key schema: dashboard:sales:{date}
    """
    logger.info(f"Event: {json.dumps(event)}")

    # TODO: implement
    # 1. Get DB credentials from Secrets Manager (DB_SECRET_ARN env var)
    # 2. Connect to RDS, query daily sales totals + top customers
    # 3. Connect to ElastiCache (REDIS_HOST env var — set when elasticache.tf is added)
    # 4. Write metrics with 7-day TTL
