"""
alerts_evaluator — EventBridge-triggered nightly alert evaluation.

Trigger: EventBridge cron daily at 11:00 IST (05:30 UTC) — schedule owned by IaC.

Logic
-----
1. Load all is_active=True alerts from the DB.
2. Determine today's date in IST.
3. Filter to alerts that are DUE today (daily=always, weekly=weekday match, monthly=day-of-month match).
4. For each due alert:
   a. Run the shared balances evaluation (alerts_eval.evaluate_balances).
   b. If ≥1 customer matches → render an HTML email table and send via SES to all recipients.
   c. Write an alert_runs row: status='sent'|'no_match'|'failed', error on exception.
5. One alert failing does NOT abort the others.

Environment variables
---------------------
  DB_SECRET_ARN       — Secrets Manager ARN for RDS credentials (host/port/dbname/user/password)
  ALERTS_SENDER_EMAIL — Verified SES sender address (e.g. alerts@iravi.in)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timezone, timedelta

import boto3
import psycopg2

import alerts_eval

logger = logging.getLogger()
logger.setLevel(logging.INFO)

secrets = boto3.client("secretsmanager")
ses = boto3.client("ses")

_SENDER_EMAIL = os.environ.get("ALERTS_SENDER_EMAIL", "")

# IST = UTC+5:30
_IST_OFFSET = timedelta(hours=5, minutes=30)


def _get_db_conn():
    secret = json.loads(
        secrets.get_secret_value(SecretId=os.environ["DB_SECRET_ARN"])["SecretString"]
    )
    return psycopg2.connect(
        host=secret["host"],
        port=secret.get("port", 5432),
        dbname=secret["dbname"],
        user=secret["username"],
        password=secret["password"],
    )


def _today_ist() -> date:
    """Return today's date in IST (UTC+5:30)."""
    return (datetime.now(timezone.utc) + _IST_OFFSET).date()


def _load_active_alerts(conn) -> list[dict]:
    """Return all is_active alerts with their conditions and recipients."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, name, category, frequency, schedule_day, match_type
            FROM alerts
            WHERE is_active = TRUE
            ORDER BY id
        """)
        alert_rows = cur.fetchall()

        alerts = []
        for (alert_id, name, category, frequency, schedule_day, match_type) in alert_rows:
            cur.execute("""
                SELECT field, op, value, value2
                FROM alert_conditions WHERE alert_id = %s ORDER BY id
            """, (alert_id,))
            conditions = [
                {
                    "field":  field,
                    "op":     op,
                    "value":  float(value),
                    "value2": float(value2) if value2 is not None else None,
                }
                for field, op, value, value2 in cur.fetchall()
            ]
            cur.execute("""
                SELECT address FROM alert_recipients
                WHERE alert_id = %s AND channel = 'email'
                ORDER BY id
            """, (alert_id,))
            recipients = [row[0] for row in cur.fetchall()]

            alerts.append({
                "id":           alert_id,
                "name":         name,
                "category":     category,
                "frequency":    frequency,
                "schedule_day": schedule_day,
                "match_type":   match_type,
                "conditions":   conditions,
                "recipients":   recipients,
            })
    return alerts


def _write_alert_run(conn, alert_id: int, matched: int, status: str, error: str | None):
    """Insert a row into alert_runs (best-effort, non-transactional)."""
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO alert_runs (alert_id, run_at, matched, status, error)
                VALUES (%s, NOW(), %s, %s, %s)
            """, (alert_id, matched, status, error))
            conn.commit()
    except Exception as exc:
        logger.error("Failed to write alert_run for alert_id=%s: %s", alert_id, exc)


def _render_html_email(alert_name: str, today: date, matched_customers: list[dict]) -> str:
    """Render an HTML email body with a table of matched customers."""
    rows_html = ""
    for row in matched_customers:
        last_amount = (
            f"₹{row['last_receipt_amount']:,.2f}"
            if row["last_receipt_amount"] is not None
            else ""
        )
        last_date = row["last_receipt_date"] or ""
        rows_html += (
            f"<tr>"
            f"<td style='padding:6px 10px;border:1px solid #ddd'>{_esc(row['customer_name'])}</td>"
            f"<td style='padding:6px 10px;border:1px solid #ddd'>{_esc(row['city'] or '')}</td>"
            f"<td style='padding:6px 10px;border:1px solid #ddd'>{_esc(row['code'] or '')}</td>"
            f"<td style='padding:6px 10px;border:1px solid #ddd;text-align:right'>₹{row['outstanding']:,.2f}</td>"
            f"<td style='padding:6px 10px;border:1px solid #ddd;text-align:right'>{row['age_days']}</td>"
            f"<td style='padding:6px 10px;border:1px solid #ddd;text-align:right'>{last_amount}</td>"
            f"<td style='padding:6px 10px;border:1px solid #ddd'>{_esc(last_date)}</td>"
            f"</tr>\n"
        )

    count = len(matched_customers)
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="font-family:Arial,sans-serif;color:#333;max-width:900px;margin:0 auto">
  <h2 style="color:#1a5276">IRAVI AGRO LIFE LLP — Alert: {_esc(alert_name)}</h2>
  <p>Date: <strong>{today.strftime('%d %b %Y')}</strong> &nbsp;|&nbsp;
     Matched customers: <strong>{count}</strong></p>
  <table style="border-collapse:collapse;width:100%;font-size:13px">
    <thead>
      <tr style="background:#1a5276;color:#fff">
        <th style="padding:8px 10px;border:1px solid #ddd;text-align:left">Customer</th>
        <th style="padding:8px 10px;border:1px solid #ddd;text-align:left">City</th>
        <th style="padding:8px 10px;border:1px solid #ddd;text-align:left">Code</th>
        <th style="padding:8px 10px;border:1px solid #ddd;text-align:right">Outstanding (₹)</th>
        <th style="padding:8px 10px;border:1px solid #ddd;text-align:right">Age (days)</th>
        <th style="padding:8px 10px;border:1px solid #ddd;text-align:right">Last Receipt Amt</th>
        <th style="padding:8px 10px;border:1px solid #ddd;text-align:left">Last Receipt Date</th>
      </tr>
    </thead>
    <tbody>
{rows_html}    </tbody>
  </table>
  <p style="margin-top:20px;font-size:11px;color:#888">
    This is an automated alert from the IRAVI Dashboard.
    Please do not reply to this email.
  </p>
</body>
</html>"""


def _esc(text: str) -> str:
    """Minimal HTML escaping for text content."""
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
    )


def _send_ses_email(alert_name: str, recipients: list[str], html_body: str, today: date):
    """Send the HTML email via SES."""
    subject = f"[IRAVI Alert] {alert_name} — {today.strftime('%d %b %Y')}"
    ses.send_email(
        Source=_SENDER_EMAIL,
        Destination={"ToAddresses": recipients},
        Message={
            "Subject": {"Data": subject, "Charset": "UTF-8"},
            "Body": {
                "Html": {"Data": html_body, "Charset": "UTF-8"},
            },
        },
    )


def lambda_handler(event, context):
    """
    EventBridge-triggered entry point.

    event shape: standard EventBridge scheduled event (detail not used).
    """
    logger.info("alerts_evaluator invoked: %s", json.dumps(event))

    today = _today_ist()
    logger.info("Evaluating alerts for IST date: %s", today)

    conn = _get_db_conn()
    try:
        active_alerts = _load_active_alerts(conn)
    except Exception as exc:
        logger.error("Failed to load active alerts: %s", exc)
        conn.close()
        raise

    due_alerts = [
        a for a in active_alerts
        if alerts_eval.is_alert_due_today(a["frequency"], a["schedule_day"], today)
    ]
    logger.info(
        "Active alerts: %d, due today (%s): %d",
        len(active_alerts), today, len(due_alerts),
    )

    results = []
    for alert in due_alerts:
        alert_id   = alert["id"]
        alert_name = alert["name"]
        logger.info("Processing alert id=%s name=%r", alert_id, alert_name)

        matched_customers: list[dict] = []
        status = "no_match"
        error_msg = None

        try:
            matched_customers = alerts_eval.evaluate_balances(
                conn,
                conditions=alert["conditions"],
                match_type=alert["match_type"],
                today=today,
            )
            matched_count = len(matched_customers)

            if matched_count >= 1:
                html_body = _render_html_email(alert_name, today, matched_customers)
                _send_ses_email(alert_name, alert["recipients"], html_body, today)
                status = "sent"
                logger.info("Alert id=%s sent to %d recipients, matched=%d",
                            alert_id, len(alert["recipients"]), matched_count)
            else:
                status = "no_match"
                logger.info("Alert id=%s: no customers matched — skipping email", alert_id)

        except Exception as exc:
            status = "failed"
            error_msg = str(exc)
            matched_count = len(matched_customers)
            logger.error("Alert id=%s failed: %s", alert_id, exc, exc_info=True)

        _write_alert_run(conn, alert_id, matched_count, status, error_msg)
        results.append({
            "alert_id": alert_id,
            "name":     alert_name,
            "matched":  matched_count,
            "status":   status,
        })

    conn.close()
    logger.info("alerts_evaluator complete: %s", results)
    return {"processed": len(due_alerts), "results": results}
