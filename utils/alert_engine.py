"""
Alert Rules & Threshold Evaluation Engine
Evaluates user-defined alert conditions (e.g. Return Rate > 20%, Duplicates > 5%)
against dataset metrics and dispatches email alerts and AI notifications on breach.
"""

from database.connection import get_db_cursor, run_query, sanitize_identifier
from database.queries import (
    get_active_alert_rules, update_alert_rule_last_triggered,
    record_alert_trigger
)
from auth.email_service import email_service
from ai.data_quality import analyze_dataset_quality


def evaluate_alert_rules_batch() -> list:
    """
    Evaluate all active user alert rules across uploaded dataset tables.
    Returns list of triggered alert event summaries.
    """
    triggered_events = []

    try:
        rules = []
        with get_db_cursor() as cursor:
            cursor.execute(get_active_alert_rules())
            rules = cursor.fetchall()

        if not rules:
            return triggered_events

        for rule in rules:
            alert_id = rule[0]
            user_id = rule[1]
            dataset_id = rule[2]
            ds_name = rule[3]
            tbl_name = rule[4]
            metric_name = rule[5].strip()
            op = rule[6].strip()
            threshold = float(rule[7])
            recipient_email = rule[8]

            safe_tbl = sanitize_identifier(tbl_name)

            # Compute metric value based on metric type
            current_value = 0.0
            metric_label = metric_name

            if "return" in metric_name.lower():
                # Compute return rate percentage
                cnt_df = run_query(f"SELECT COUNT(*) AS [Tot] FROM {safe_tbl};")
                tot = cnt_df.iloc[0]["Tot"] if not cnt_df.empty else 0

                ret_df = run_query(f"SELECT COUNT(*) AS [Ret] FROM {safe_tbl} WHERE CAST([Delivery_Status] AS NVARCHAR(MAX)) LIKE '%return%' OR CAST([Status_Description] AS NVARCHAR(MAX)) LIKE '%return%';")
                ret = ret_df.iloc[0]["Ret"] if not ret_df.empty else 0

                current_value = round((ret / tot * 100.0), 2) if tot > 0 else 0.0
                metric_label = "Return Rate Percentage"

            elif "duplicate" in metric_name.lower():
                quality = analyze_dataset_quality(tbl_name)
                current_value = float(100 - quality.get("duplicates_score", 100))
                metric_label = "Duplicate Record Percentage"

            elif "missing" in metric_name.lower():
                quality = analyze_dataset_quality(tbl_name)
                current_value = float(100 - quality.get("completeness_score", 100))
                metric_label = "Missing Data Percentage"

            else:
                # Custom column numerical metric (e.g. Sales, Amount)
                try:
                    safe_col = sanitize_identifier(metric_name)
                    avg_df = run_query(f"SELECT AVG(CAST({safe_col} AS FLOAT)) AS [Val] FROM {safe_tbl};")
                    if not avg_df.empty and avg_df.iloc[0]["Val"] is not None:
                        current_value = float(avg_df.iloc[0]["Val"])
                except Exception:
                    continue

            # Evaluate condition operator (<, >, <=, >=, ==)
            is_breached = False
            if op == ">" and current_value > threshold:
                is_breached = True
            elif op == ">=" and current_value >= threshold:
                is_breached = True
            elif op == "<" and current_value < threshold:
                is_breached = True
            elif op == "<=" and current_value <= threshold:
                is_breached = True
            elif op in ("=", "==") and current_value == threshold:
                is_breached = True

            if is_breached:
                msg = f"Alert Triggered on '{ds_name}': {metric_label} reached {current_value:.2f}% (Threshold: {op} {threshold:.2f}%)."
                
                # Record trigger history in DB
                with get_db_cursor(commit=True) as cursor:
                    cursor.execute(record_alert_trigger(), (alert_id, user_id, current_value, msg))
                    cursor.execute(update_alert_rule_last_triggered(), (alert_id,))

                # Dispatch Email
                if recipient_email:
                    subject = f"🚨 AI Alert Triggered: {ds_name} - {metric_label}"
                    body = f"""
                    <div style="font-family: Arial, sans-serif; padding: 20px; background: #0f172a; color: #f8fafc; border-radius: 8px;">
                        <h2 style="color: #f43f5e;">🚨 Automated Threshold Alert Triggered</h2>
                        <p><b>Dataset:</b> {ds_name}</p>
                        <p><b>Metric:</b> {metric_label}</p>
                        <p><b>Observed Value:</b> <span style="font-size: 18px; font-weight: bold; color: #fbbf24;">{current_value:.2f}%</span></p>
                        <p><b>Rule Condition:</b> {op} {threshold:.2f}%</p>
                        <hr style="border-color: #334155;">
                        <p style="font-size: 12px; color: #94a3b8;">This is an automated threshold monitoring alert from AI Data Analyst Pro.</p>
                    </div>
                    """
                    email_service._send_email_async(recipient_email, subject, body)

                triggered_events.append({
                    "alert_id": alert_id,
                    "dataset_name": ds_name,
                    "message": msg,
                    "value": current_value
                })

    except Exception as err:
        print("Alert Evaluation Engine Error:", err)

    return triggered_events
