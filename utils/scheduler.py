"""
Non-Blocking Background Scheduler Thread
Executes scheduled email reports (Daily, Weekly, Monthly) and evaluates alert rules
periodically in a background daemon thread without blocking web server requests.
"""

import time
import threading
from datetime import datetime
from database.connection import get_db_cursor
from database.queries import get_due_scheduled_reports, update_scheduled_report_last_run
from utils.exporters import export_to_pdf_report, generate_fast_export_insights
from ai.insight_engine import generate_automated_insights
from auth.email_service import email_service
from utils.alert_engine import evaluate_alert_rules_batch

_scheduler_running = False
_scheduler_thread = None


def process_due_scheduled_reports():
    """
    Check and execute due scheduled email reports.
    """
    try:
        due_reports = []
        with get_db_cursor() as cursor:
            cursor.execute(get_due_scheduled_reports())
            due_reports = cursor.fetchall()

        for report in due_reports:
            report_id = report[0]
            user_id = report[1]
            dataset_id = report[2]
            ds_name = report[3]
            tbl_name = report[4]
            report_type = report[5]
            frequency = report[6]
            recipient_email = report[7]

            # Generate Insights & Executive PDF Report
            insights_data = generate_automated_insights(tbl_name)
            exec_summary = insights_data.get("executive_summary", {})
            insights_list = exec_summary.get("key_findings", [])

            # Fetch sample DataFrame for PDF rendering
            from database.connection import run_query, sanitize_identifier
            df = run_query(f"SELECT TOP 500 * FROM {sanitize_identifier(tbl_name)};")

            if df is not None and not df.empty and recipient_email:
                subject = f"📊 Scheduled {frequency} {report_type}: {ds_name}"
                body = f"""
                <div style="font-family: Arial, sans-serif; padding: 20px; background: #0f172a; color: #f8fafc; border-radius: 8px;">
                    <h2 style="color: #38bdf8;">📊 {frequency} Executive AI Report</h2>
                    <p><b>Dataset:</b> {ds_name}</p>
                    <p><b>Report Type:</b> {report_type}</p>
                    <p><b>Key Finding:</b> {insights_list[0] if insights_list else 'Data volume stable.'}</p>
                    <hr style="border-color: #334155;">
                    <p style="font-size: 12px; color: #94a3b8;">This automated report was generated and dispatched by AI Data Analyst Pro Scheduler.</p>
                </div>
                """
                email_service._send_email_async(recipient_email, subject, body)

            # Update last run timestamp
            with get_db_cursor(commit=True) as cursor:
                cursor.execute(update_scheduled_report_last_run(), (report_id,))

    except Exception as err:
        print("Scheduled Reports Worker Error:", err)


def _scheduler_loop():
    """
    Main background daemon loop. Runs every 60 seconds.
    """
    global _scheduler_running
    print("[SCHEDULER ENGINE] Started background reporting daemon thread.")
    
    while _scheduler_running:
        try:
            # 1. Process Alert Rules
            evaluate_alert_rules_batch()

            # 2. Process Scheduled Reports
            process_due_scheduled_reports()
        except Exception as err:
            print("[SCHEDULER ENGINE LOG]", err)

        # Sleep for 60 seconds
        time.sleep(60)


def start_scheduler():
    """
    Start the background scheduler thread if not already running.
    """
    global _scheduler_running, _scheduler_thread
    if _scheduler_running:
        return

    _scheduler_running = True
    _scheduler_thread = threading.Thread(target=_scheduler_loop, daemon=True)
    _scheduler_thread.start()


def stop_scheduler():
    global _scheduler_running
    _scheduler_running = False
