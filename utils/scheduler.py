"""
Non-Blocking Background Scheduler Thread
Executes scheduled email reports (Daily, Weekly, Monthly) and evaluates alert rules
periodically in a background daemon thread without blocking web server requests.
"""

import time
import threading
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from database.connection import get_db_cursor
from database.queries import (
    get_due_scheduled_reports, update_scheduled_report_last_run,
    auto_suspend_overdue_users, get_users_due_on_date,
    has_reminder_been_sent, log_reminder_sent, create_ai_notification
)
from ai.insight_engine import generate_automated_insights
from auth.email_service import email_service
from utils.alert_engine import evaluate_alert_rules_batch
from utils.db_backup import run_database_backup

_scheduler_running = False
_scheduler_thread = None
_last_backup_date = None

PKT = ZoneInfo("Asia/Karachi")

# Configurable: how many days before the due date to send the early reminder.
REMINDER_DAYS_BEFORE = 3


def process_daily_backup():
    """
    Runs one full database backup per calendar day. Checked every scheduler tick (cheap
    date comparison) rather than sleeping for 24h separately, so it self-corrects even
    if the app was restarted and missed the exact moment a day rolled over.
    """
    global _last_backup_date
    today = date.today()
    if _last_backup_date == today:
        return
    _last_backup_date = today
    run_database_backup()


def process_due_scheduled_reports():
    """
    Check and execute due scheduled email reports.
    """
    try:
        due_reports = []
        try:
            with get_db_cursor() as cursor:
                cursor.execute(get_due_scheduled_reports())
                due_reports = cursor.fetchall()
        except Exception:
            return

        for report in due_reports:
            report_id = report[0]
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

    except Exception:
        pass


def process_subscription_expiry_sweep():
    """
    Daily (safe to run more often - every check is idempotent) subscription lifecycle
    sweep, using Asia/Karachi time for all "which day is it" decisions per the feature
    spec, regardless of what timezone the DB server itself is running in:

      1. Reminder 3 days before the due date.
      2. Reminder on the due date itself.
      3. Auto-suspend anyone whose due date has passed without a renewed payment.

    Reminders are logged in ReminderLog (UNIQUE on user+type+due-date) so re-running
    this within the same day - or even the same minute, since the scheduler tick is
    60s - never double-sends. Suspension itself is naturally idempotent: the bulk
    UPDATE only ever matches rows still marked 'active'.
    """
    today_pkt = datetime.now(PKT).date()

    # 1 & 2: reminders (3-day-before, then due-date-itself).
    for days_before, reminder_type in [(REMINDER_DAYS_BEFORE, "3day"), (0, "due")]:
        target_date = today_pkt + timedelta(days=days_before)
        try:
            with get_db_cursor() as cursor:
                cursor.execute(get_users_due_on_date(), (target_date,))
                due_users = cursor.fetchall() or []
        except Exception as err:
            print(f"[SCHEDULER ENGINE] Reminder lookup error ({reminder_type}):", err)
            continue

        for u_id, full_name, email, end_date in due_users:
            try:
                with get_db_cursor() as cursor:
                    cursor.execute(has_reminder_been_sent(), (u_id, reminder_type, target_date))
                    if cursor.fetchone()[0] > 0:
                        continue  # already sent for this exact user/type/due-date

                due_label = end_date.strftime("%d %b %Y") if hasattr(end_date, "strftime") else str(end_date)
                if email:
                    email_service.send_subscription_reminder_email(email, full_name or "Valued Customer", due_label, days_before)

                with get_db_cursor(commit=True) as cursor:
                    cursor.execute(
                        create_ai_notification(),
                        (u_id, "subscription_reminder", "Subscription Renewal Reminder",
                         f"Your monthly subscription of PKR 25,000 is due on {due_label}. Please pay and upload your screenshot to avoid suspension.", None)
                    )
                    # Recorded even if the email above failed, since the notification
                    # itself succeeded - a transient SMTP hiccup shouldn't cause the
                    # reminder to be silently retried (and duplicated) every tick.
                    cursor.execute(log_reminder_sent(), (u_id, reminder_type, target_date))
            except Exception as err:
                print(f"[SCHEDULER ENGINE] Reminder send error ({reminder_type}, user {u_id}):", err)

    # 3: auto-suspend anyone past due (bulk UPDATE, OUTPUT gives back who got hit so
    # they can be emailed/notified without a second SELECT).
    try:
        with get_db_cursor(commit=True) as cursor:
            cursor.execute(auto_suspend_overdue_users())
            suspended_users = cursor.fetchall() or []
    except Exception as err:
        print("[SCHEDULER ENGINE] Auto-suspend error:", err)
        suspended_users = []

    for u_id, full_name, email in suspended_users:
        try:
            if email:
                email_service.send_subscription_suspended_email(email, full_name or "Valued Customer")
            with get_db_cursor(commit=True) as cursor:
                cursor.execute(
                    create_ai_notification(),
                    (u_id, "subscription_suspended", "Dashboard Suspended",
                     "Your subscription has expired and your dashboard has been suspended. Please pay PKR 25,000 and upload the screenshot to re-activate.", None)
                )
            print(f"[SCHEDULER ENGINE] Auto-suspended user #{u_id} ({email}) - subscription expired.")
        except Exception as err:
            print(f"[SCHEDULER ENGINE] Suspension notification error (user {u_id}):", err)


def _scheduler_loop():
    """
    Main background daemon loop. Runs every 60 seconds.
    """
    print("[SCHEDULER ENGINE] Started background reporting daemon thread.")

    while _scheduler_running:
        try:
            # 1. Process Alert Rules
            evaluate_alert_rules_batch()

            # 2. Process Scheduled Reports
            process_due_scheduled_reports()

            # 3. Keep subscription status/freeze state accurate for all users
            process_subscription_expiry_sweep()

            # 4. One full database backup per day
            process_daily_backup()
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
