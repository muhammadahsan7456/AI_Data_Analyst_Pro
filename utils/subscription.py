import datetime
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from database.connection import get_db_cursor

def get_user_subscription_info(user_id: int) -> dict:
    """
    Calculate real-time user subscription plan status, days remaining, and banner notice.
    Returns:
      {
         "status": "active" | "expiring_soon" | "expired" | "suspended",
         "days_left": int,
         "end_date": str,
         "is_active": bool,
         "banner_type": str,
         "banner_message": str,
         "show_banner": bool
      }
    """
    if not user_id:
        return {
            "status": "active",
            "days_left": 30,
            "end_date": "N/A",
            "is_active": True,
            "banner_type": "",
            "banner_message": "",
            "show_banner": False
        }

    try:
        with get_db_cursor() as cursor:
            cursor.execute("""
                SELECT UserID, IsActive, SubscriptionStatus, SubscriptionEndDate, CreatedAt, Role
                FROM Users WHERE UserID = ?
            """, (user_id,))
            row = cursor.fetchone()

        if not row:
            return {"status": "active", "days_left": 30, "end_date": "N/A", "is_active": True, "show_banner": False}

        u_id, is_active, sub_status, sub_end, created_at, role = row

        # Super Admin accounts never expire
        if role in ["Super Admin", "SuperAdmin", "Admin"]:
            return {
                "status": "active",
                "days_left": 999,
                "end_date": "Unlimited Lifetime Access",
                "is_active": True,
                "banner_type": "",
                "banner_message": "",
                "show_banner": False
            }

        # These two states are only ever entered/exited by an explicit Super Admin
        # decision (payment approval/rejection) - they have no SubscriptionEndDate yet,
        # so the date-math below must never run for them. Doing so would compute a
        # bogus "active" status from a fabricated 30-day fallback end date and then
        # WRITE it back to the DB (see the sync below), silently granting access to a
        # user who was never approved.
        if sub_status in ("pending_approval", "rejected"):
            return {
                "status": sub_status,
                "days_left": 0,
                "end_date": "N/A",
                "is_active": False,
                "banner_type": "",
                "banner_message": "",
                "show_banner": False
            }

        now = datetime.datetime.now()
        if sub_end is None:
            created_dt = created_at if isinstance(created_at, datetime.datetime) else now
            sub_end = created_dt + datetime.timedelta(days=30)

        if not isinstance(sub_end, datetime.datetime):
            try:
                sub_end = datetime.datetime.strptime(str(sub_end)[:19], "%Y-%m-%d %H:%M:%S")
            except Exception:
                sub_end = now + datetime.timedelta(days=30)

        delta = sub_end - now
        days_left = delta.days

        # Determine true status
        if not is_active or sub_status == "suspended":
            calc_status = "suspended"
        elif days_left <= 0:
            calc_status = "expired"
        elif days_left <= 5:
            calc_status = "expiring_soon"
        else:
            calc_status = "active"

        # Update DB if status changed
        if calc_status != sub_status and sub_status != "suspended":
            try:
                with get_db_cursor(commit=True) as cursor:
                    cursor.execute("UPDATE Users SET SubscriptionStatus = ? WHERE UserID = ?", (calc_status, user_id))
            except Exception:
                pass

        end_date_str = sub_end.strftime("%b %d, %Y")

        banner_type = ""
        banner_message = ""
        show_banner = False

        # Check if user has uploaded a pending payment receipt proof
        has_pending = False
        try:
            with get_db_cursor() as cursor:
                cursor.execute("SELECT TOP 1 PaymentID FROM Payments WHERE UserID = ? AND Status IN ('Pending', 'Pending_Review')", (user_id,))
                if cursor.fetchone():
                    has_pending = True
        except Exception:
            pass

        if has_pending and calc_status != "active":
            banner_type = "info"
            banner_message = "⏳ PAYMENT PROOF UNDER REVIEW: Your payment receipt proof (PKR 25,000) has been uploaded and is currently under Super Admin review. Account access will be activated immediately upon approval!"
            show_banner = True
        elif calc_status == "suspended":
            banner_type = "danger"
            banner_message = "🛑 ACCOUNT SUSPENDED: Your account access has been suspended due to unpaid subscription renewal. Please upload your payment receipt below for immediate reactivation."
            show_banner = True
        elif calc_status == "expired":
            banner_type = "danger"
            banner_message = f"🚨 ANNOUNCEMENT: Your 30-day subscription plan expired on {end_date_str}! Please upload your payment receipt proof to renew your access."
            show_banner = True
        elif calc_status == "expiring_soon":
            banner_type = "warning"
            banner_message = f"⚠️ ANNOUNCEMENT: Your 30-day subscription plan ends in {days_left} day(s) on {end_date_str}! Please renew your plan bill to avoid service interruption."
            show_banner = True

        return {
            "status": calc_status,
            "days_left": max(0, days_left),
            "end_date": end_date_str,
            "is_active": bool(is_active) and calc_status != "suspended",
            "banner_type": banner_type,
            "banner_message": banner_message,
            "show_banner": show_banner,
            "has_pending_payment": has_pending
        }
    except Exception as err:
        print("Subscription Info Error:", err)
        return {
            "status": "active",
            "days_left": 30,
            "end_date": "N/A",
            "is_active": True,
            "show_banner": False
        }
