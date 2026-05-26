import json
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def lambda_handler(event, context):
    """
    API Gateway HTTP API (v2) trigger.
    Cache-aside: Redis fast path → RDS fallback → populate Redis.
    Phase 1: GET /sales only.
    """
    method = event.get("requestContext", {}).get("http", {}).get("method", "")
    path = event.get("rawPath", "")
    logger.info(f"{method} {path}")

    if path == "/sales" and method == "GET":
        return _handle_sales(event)

    return _response(404, {"error": "Not found"})


def _handle_sales(event):
    # TODO: implement
    # 1. Get DB credentials from Secrets Manager (DB_SECRET_ARN env var)
    # 2. Check Redis cache: dashboard:sales:{date} (REDIS_HOST env var)
    # 3. Cache miss → query RDS fact_sales, populate Redis, return data
    # 4. Cache hit → return cached data directly
    return _response(200, {"data": []})


def _response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }
