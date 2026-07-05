"""
alerts_evaluator — EventBridge-triggered 15-minute alert evaluation.

Trigger: EventBridge rate(15 minutes) — schedule owned by IaC.

Logic
-----
1. Load all is_active=True alerts from the DB (including schedule_time).
2. Determine current date and time in IST (UTC+5:30).
3. For each alert, decide whether to send on this run — ALL three gates must pass:
   a. Due today (IST):
      - daily   → always
      - weekly  → IST weekday (0=Mon) == schedule_day
      - monthly → IST day-of-month == schedule_day
   b. Time reached: current IST time-of-day (HH:MM) >= alert's schedule_time (HH:MM).
   c. Not already done today: no alert_runs row for this alert with
      run_at (cast to IST date) == today AND status IN ('sent', 'no_match').
      A previous 'failed' run today may retry (it is not deduplicated), BUT only up
      to _MAX_FAILED_ATTEMPTS_PER_DAY failures — once that cap is reached the alert
      is skipped for the rest of the day.
4. For each alert that passes all three gates:
   a. Run the shared balances evaluation (alerts_eval.evaluate_balances).
   b. If ≥1 customer matches → render an HTML email table and send via SES to all recipients.
   c. Write an alert_runs row: status='sent'|'no_match'|'failed', error on exception.
5. One alert failing does NOT abort the others.

This guarantees exactly one send per day per alert, at/after its configured time,
even with 15-minute polling.  A persistently-failing alert makes at most
_MAX_FAILED_ATTEMPTS_PER_DAY attempts per day; on the (N+1)th tick it is skipped
until tomorrow.

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
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import boto3
import psycopg2

import alerts_eval
import monthly_sales
import monthly_sales_pdf

logger = logging.getLogger()
logger.setLevel(logging.INFO)

secrets = boto3.client("secretsmanager")
ses = boto3.client("ses")

_SENDER_EMAIL = os.environ.get("ALERTS_SENDER_EMAIL", "")

# IST = UTC+5:30
_IST_OFFSET = timedelta(hours=5, minutes=30)

# Per-day failed-retry cap.  Once an alert accumulates this many 'failed' runs
# on the current IST date it is skipped for the rest of the day, preventing an
# endlessly-churning alert from hammering the DB and SES on every 15-min tick.
_MAX_FAILED_ATTEMPTS_PER_DAY = 5


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


def _now_ist() -> datetime:
    """Return current datetime in IST (UTC+5:30), timezone-naive."""
    return datetime.now(timezone.utc) + _IST_OFFSET


def _today_ist() -> date:
    """Return today's date in IST (UTC+5:30)."""
    return _now_ist().date()


def _current_hhmm_ist() -> str:
    """Return current IST time as 'HH:MM' string (for comparison with schedule_time)."""
    now = _now_ist()
    return f"{now.hour:02d}:{now.minute:02d}"


def _load_active_alerts(conn) -> list[dict]:
    """Return all is_active alerts with their conditions and recipients."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, name, category, frequency, schedule_day, schedule_time, match_type, branch
            FROM alerts
            WHERE is_active = TRUE
            ORDER BY id
        """)
        alert_rows = cur.fetchall()

        alerts = []
        for (alert_id, name, category, frequency, schedule_day, schedule_time, match_type, branch) in alert_rows:
            # Normalise schedule_time to "HH:MM" string.
            # psycopg2 returns TIME WITHOUT TIME ZONE as datetime.timedelta.
            if schedule_time is None:
                st_str = alerts_eval._DEFAULT_SCHEDULE_TIME
            elif hasattr(schedule_time, 'hour'):
                # datetime.time object
                st_str = f"{schedule_time.hour:02d}:{schedule_time.minute:02d}"
            else:
                # timedelta (seconds since midnight)
                total_seconds = int(schedule_time.total_seconds())
                hours, remainder = divmod(total_seconds, 3600)
                minutes = remainder // 60
                st_str = f"{hours:02d}:{minutes:02d}"

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
                "id":            alert_id,
                "name":          name,
                "category":      category,
                "frequency":     frequency,
                "schedule_day":  schedule_day,
                "schedule_time": st_str,
                "match_type":    match_type,
                "branch":        branch,
                "conditions":    conditions,
                "recipients":    recipients,
            })
    return alerts


def _check_today_runs(conn, alert_id: int, today: date) -> tuple[bool, int]:
    """
    Return ``(done, failed_count)`` for the given alert on the IST date *today*.

    ``done``         — True if any alert_runs row exists today with
                       status IN ('sent', 'no_match').  The alert has already
                       succeeded/matched today and must not run again.
    ``failed_count`` — Number of alert_runs rows today with status='failed'.
                       Used to enforce _MAX_FAILED_ATTEMPTS_PER_DAY.

    run_at is stored as UTC by the DB (NOW()); we convert to IST by adding 5h30m
    before comparing to today.  Both counts come from a single query.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                COUNT(*) FILTER (WHERE status IN ('sent', 'no_match')) AS done,
                COUNT(*) FILTER (WHERE status = 'failed')              AS failed
            FROM alert_runs
            WHERE alert_id = %s
              AND (run_at AT TIME ZONE 'UTC' + INTERVAL '5 hours 30 minutes')::DATE = %s
        """, (alert_id, today))
        row = cur.fetchone()
        done_count, failed_count = row if row else (0, 0)
        return bool(done_count), int(failed_count)


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
            f"&#8377;{row['last_receipt_amount']:,.2f}"
            if row["last_receipt_amount"] is not None
            else ""
        )
        last_date = row["last_receipt_date"] or ""
        # days_since_last_receipt: show sentinel as "Never" for readability
        dslr_raw = row.get("days_since_last_receipt")
        if dslr_raw is None or dslr_raw >= alerts_eval._NEVER_PAID_SENTINEL:
            dslr_display = "Never"
        else:
            dslr_display = str(int(dslr_raw))
        rows_html += (
            f"<tr>"
            f"<td style='padding:6px 10px;border:1px solid #ddd'>{_esc(row['customer_name'])}</td>"
            f"<td style='padding:6px 10px;border:1px solid #ddd'>{_esc(row['city'] or '')}</td>"
            f"<td style='padding:6px 10px;border:1px solid #ddd'>{_esc(row['code'] or '')}</td>"
            f"<td style='padding:6px 10px;border:1px solid #ddd;text-align:right'>&#8377;{row['outstanding']:,.2f}</td>"
            f"<td style='padding:6px 10px;border:1px solid #ddd;text-align:right'>{row['age_days']}</td>"
            f"<td style='padding:6px 10px;border:1px solid #ddd;text-align:right'>{dslr_display}</td>"
            f"<td style='padding:6px 10px;border:1px solid #ddd;text-align:right'>{last_amount}</td>"
            f"<td style='padding:6px 10px;border:1px solid #ddd'>{_esc(last_date)}</td>"
            f"</tr>\n"
        )

    count = len(matched_customers)
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="font-family:Arial,sans-serif;color:#333;max-width:1000px;margin:0 auto">
  <h2 style="color:#1a5276">IRAVI AGRO LIFE LLP &#8212; Alert: {_esc(alert_name)}</h2>
  <p>Date: <strong>{today.strftime('%d %b %Y')}</strong> &nbsp;|&nbsp;
     Matched customers: <strong>{count}</strong></p>
  <table style="border-collapse:collapse;width:100%;font-size:13px">
    <thead>
      <tr style="background:#1a5276;color:#fff">
        <th style="padding:8px 10px;border:1px solid #ddd;text-align:left">Customer</th>
        <th style="padding:8px 10px;border:1px solid #ddd;text-align:left">City</th>
        <th style="padding:8px 10px;border:1px solid #ddd;text-align:left">Code</th>
        <th style="padding:8px 10px;border:1px solid #ddd;text-align:right">Outstanding (&#8377;)</th>
        <th style="padding:8px 10px;border:1px solid #ddd;text-align:right">Age (days)</th>
        <th style="padding:8px 10px;border:1px solid #ddd;text-align:right">Days Since Receipt</th>
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


def _send_ses_email(subject: str, recipients: list[str], html_body: str):
    """Send the HTML email via SES."""
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


def _send_ses_email_with_pdf(
    subject: str,
    recipients: list[str],
    html_body: str,
    pdf_bytes: bytes,
    pdf_filename: str,
):
    """Send an HTML email with a PDF attachment via SES SendRawEmail.

    Uses stdlib MIME classes so no extra dependency is needed beyond what is
    already in the Lambda runtime.  The pdf_bytes are attached as
    application/pdf with Content-Disposition: attachment.
    """
    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = _SENDER_EMAIL
    msg["To"] = ", ".join(recipients)

    # HTML body part
    html_part = MIMEText(html_body, "html", "utf-8")
    msg.attach(html_part)

    # PDF attachment part
    pdf_part = MIMEApplication(pdf_bytes, _subtype="pdf")
    pdf_part.add_header(
        "Content-Disposition", "attachment", filename=pdf_filename
    )
    msg.attach(pdf_part)

    ses.send_raw_email(
        Source=_SENDER_EMAIL,
        Destinations=recipients,
        RawMessage={"Data": msg.as_string()},
    )


def _render_metrics_email(
    alert_name: str, category: str, today: date, result: dict
) -> str:
    """Render an HTML metrics-summary email for a sales or sale_returns alert.

    Includes a conditions table (field label, op, threshold, actual, breached?)
    and a window-metrics table, as specified in the contract.
    """
    cat_label = "Sales" if category == "sales" else "Sale Returns"

    catalog    = alerts_eval.FIELD_CATALOGS[category]
    field_labels = {f["key"]: f["label"] for f in catalog["fields"]}

    # Conditions table
    cond_rows_html = ""
    for c in result["conditions"]:
        label       = field_labels.get(c["field"], c["field"])
        val_display = f"&#8377;{c['value']:,.2f}"
        if c["value2"] is not None:
            val_display += f" &ndash; &#8377;{c['value2']:,.2f}"
        actual_display  = f"&#8377;{c['actual']:,.2f}"
        breached_text   = "Yes" if c["breached"] else "No"
        row_bg          = "background:#fff0f0" if c["breached"] else ""
        cond_rows_html += (
            f"<tr style='{row_bg}'>"
            f"<td style='padding:6px 10px;border:1px solid #ddd'>{_esc(label)}</td>"
            f"<td style='padding:6px 10px;border:1px solid #ddd;text-align:center'>{c['op']}</td>"
            f"<td style='padding:6px 10px;border:1px solid #ddd;text-align:right'>{val_display}</td>"
            f"<td style='padding:6px 10px;border:1px solid #ddd;text-align:right'>{actual_display}</td>"
            f"<td style='padding:6px 10px;border:1px solid #ddd;text-align:center'>{breached_text}</td>"
            f"</tr>\n"
        )

    # Window metrics table
    metrics_rows_html = ""
    for field_key, field_val in result["metrics"].items():
        label = field_labels.get(field_key, field_key)
        metrics_rows_html += (
            f"<tr>"
            f"<td style='padding:6px 10px;border:1px solid #ddd'>{_esc(label)}</td>"
            f"<td style='padding:6px 10px;border:1px solid #ddd;text-align:right'>&#8377;{field_val:,.2f}</td>"
            f"</tr>\n"
        )

    fired = result["matched"]
    status_style = "background:#1a5276;color:#fff" if fired else "background:#888;color:#fff"
    status_text  = "FIRED" if fired else "NO MATCH"

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="font-family:Arial,sans-serif;color:#333;max-width:900px;margin:0 auto">
  <h2 style="color:#1a5276">IRAVI AGRO LIFE LLP &#8212; {_esc(cat_label)} Alert: {_esc(alert_name)}</h2>
  <p>Date: <strong>{today.strftime('%d %b %Y')}</strong> &nbsp;|&nbsp;
     Status: <span style="padding:3px 10px;border-radius:4px;{status_style}">{status_text}</span></p>

  <h3 style="color:#1a5276;margin-top:20px">Conditions</h3>
  <table style="border-collapse:collapse;width:100%;font-size:13px">
    <thead>
      <tr style="background:#1a5276;color:#fff">
        <th style="padding:8px 10px;border:1px solid #ddd;text-align:left">Metric</th>
        <th style="padding:8px 10px;border:1px solid #ddd;text-align:center">Operator</th>
        <th style="padding:8px 10px;border:1px solid #ddd;text-align:right">Threshold</th>
        <th style="padding:8px 10px;border:1px solid #ddd;text-align:right">Actual Value</th>
        <th style="padding:8px 10px;border:1px solid #ddd;text-align:center">Breached?</th>
      </tr>
    </thead>
    <tbody>
{cond_rows_html}    </tbody>
  </table>

  <h3 style="color:#1a5276;margin-top:20px">Window Metrics</h3>
  <table style="border-collapse:collapse;width:100%;font-size:13px">
    <thead>
      <tr style="background:#1a5276;color:#fff">
        <th style="padding:8px 10px;border:1px solid #ddd;text-align:left">Metric</th>
        <th style="padding:8px 10px;border:1px solid #ddd;text-align:right">Value (&#8377;)</th>
      </tr>
    </thead>
    <tbody>
{metrics_rows_html}    </tbody>
  </table>

  <p style="margin-top:20px;font-size:11px;color:#888">
    This is an automated alert from the IRAVI Dashboard.
    Please do not reply to this email.
  </p>
</body>
</html>"""


def lambda_handler(event, context):
    """
    EventBridge-triggered entry point.  Runs every 15 minutes.

    For each active alert, three gates must ALL pass before sending:
      1. Due today  (daily=always, weekly=weekday match, monthly=day-of-month match)
      2. Time reached: current IST HH:MM >= alert's schedule_time HH:MM
      3. Not already done today: no alert_runs row (status sent|no_match) for today (IST),
         AND today's failed-run count < _MAX_FAILED_ATTEMPTS_PER_DAY.

    A previously-failed run today is allowed to retry, but only up to
    _MAX_FAILED_ATTEMPTS_PER_DAY (5) times.  After that the alert is skipped
    for the rest of the day to prevent persistent-failure churn.

    event shape: standard EventBridge scheduled event (detail not used).
    """
    logger.info("alerts_evaluator invoked: %s", json.dumps(event))

    today = _today_ist()
    current_hhmm = _current_hhmm_ist()
    logger.info("Evaluating alerts — IST date: %s  time: %s", today, current_hhmm)

    conn = _get_db_conn()
    try:
        active_alerts = _load_active_alerts(conn)
    except Exception as exc:
        logger.error("Failed to load active alerts: %s", exc)
        conn.close()
        raise

    # ── Gate 1: due today ─────────────────────────────────────────────────────
    due_alerts = [
        a for a in active_alerts
        if alerts_eval.is_alert_due_today(a["frequency"], a["schedule_day"], today)
    ]
    logger.info(
        "Active alerts: %d, due today (%s): %d",
        len(active_alerts), today, len(due_alerts),
    )

    # ── Gate 2 + 3: time reached AND not already done today AND under retry cap ─
    actionable = []
    for alert in due_alerts:
        schedule_time = alert["schedule_time"]  # "HH:MM"
        if current_hhmm < schedule_time:
            logger.info(
                "Alert id=%s skipped — time not yet reached (now=%s, schedule=%s)",
                alert["id"], current_hhmm, schedule_time,
            )
            continue

        done_today, failed_today = _check_today_runs(conn, alert["id"], today)

        if done_today:
            logger.info(
                "Alert id=%s skipped — already sent/no_match today (%s)",
                alert["id"], today,
            )
            continue

        if failed_today >= _MAX_FAILED_ATTEMPTS_PER_DAY:
            logger.info(
                "Alert id=%s: reached %d failed attempts today, skipping until tomorrow",
                alert["id"], failed_today,
            )
            continue

        actionable.append(alert)

    logger.info("Alerts passing all gates (will evaluate): %d", len(actionable))

    results = []
    for alert in actionable:
        alert_id   = alert["id"]
        alert_name = alert["name"]
        category   = alert["category"]
        logger.info("Processing alert id=%s name=%r category=%s", alert_id, alert_name, category)

        matched_count = 0
        status    = "no_match"
        error_msg = None

        try:
            if category == "balances":
                # ── Per-customer balance evaluation ──────────────────────────
                matched_customers: list[dict] = alerts_eval.evaluate_balances(
                    conn,
                    conditions=alert["conditions"],
                    match_type=alert["match_type"],
                    today=today,
                )
                matched_count = len(matched_customers)
                if matched_count >= 1:
                    html_body = _render_html_email(alert_name, today, matched_customers)
                    subject   = f"[IRAVI Alert] {alert_name} — {today.strftime('%d %b %Y')}"
                    _send_ses_email(subject, alert["recipients"], html_body)
                    status = "sent"
                    logger.info("Alert id=%s (balances) sent to %d recipients, matched=%d",
                                alert_id, len(alert["recipients"]), matched_count)
                else:
                    status = "no_match"
                    logger.info("Alert id=%s (balances): no customers matched", alert_id)

            else:
                # ── Aggregate sales / sale_returns evaluation ─────────────────
                agg = alerts_eval.evaluate_aggregate(conn, alert=alert, today=today)
                if agg["matched"]:
                    date_display = today.strftime('%d %b %Y')
                    if category == "sales":
                        # Sales alerts: attach the Monthly Sales PDF; minimal body (no tables).
                        subject = f"IRAVI — Daily Net Sales Report — {date_display}"
                        html_body = (
                            "<!DOCTYPE html>"
                            "<html><head><meta charset=\"UTF-8\"></head>"
                            "<body style=\"font-family:Arial,sans-serif;color:#333;max-width:700px;margin:0 auto\">"
                            f"<p style=\"font-size:15px\">Attached is the Daily Net Sales Report for <strong>{date_display}</strong>.</p>"
                            "<p style=\"margin-top:20px;font-size:11px;color:#888\">"
                            "This is an automated message from the IRAVI Dashboard. Please do not reply to this email."
                            "</p>"
                            "</body></html>"
                        )
                        # Current month in IST (YYYY-MM) for the PDF
                        current_month_str = today.strftime('%Y-%m')
                        sales_data = monthly_sales.compute_monthly_sales(conn, current_month_str)
                        pdf_bytes  = monthly_sales_pdf.render_monthly_sales_pdf(sales_data)
                        pdf_filename = f"IAL_Daily_Net_Sales_{today.strftime('%d-%b-%Y')}.pdf"
                        _send_ses_email_with_pdf(
                            subject,
                            alert["recipients"],
                            html_body,
                            pdf_bytes,
                            pdf_filename,
                        )
                    else:
                        # sale_returns (and any future aggregate categories): metrics email, no attachment.
                        cat_label = "Sale Returns"
                        html_body = _render_metrics_email(alert_name, category, today, agg)
                        subject   = f"[IRAVI Alert] {cat_label} — {date_display}"
                        _send_ses_email(subject, alert["recipients"], html_body)

                    status        = "sent"
                    matched_count = 1  # 1 = the aggregate alert fired
                    logger.info("Alert id=%s (%s) sent to %d recipients",
                                alert_id, category, len(alert["recipients"]))
                else:
                    status        = "no_match"
                    matched_count = 0
                    logger.info("Alert id=%s (%s): conditions did not fire", alert_id, category)

        except Exception as exc:
            status    = "failed"
            error_msg = str(exc)
            logger.error("Alert id=%s failed: %s", alert_id, exc, exc_info=True)

        _write_alert_run(conn, alert_id, matched_count, status, error_msg)
        results.append({
            "alert_id": alert_id,
            "name":     alert_name,
            "matched":  matched_count,
            "status":   status,
        })

    conn.close()
    logger.info("alerts_evaluator complete: processed=%d results=%s",
                len(actionable), results)
    return {"processed": len(actionable), "results": results}
