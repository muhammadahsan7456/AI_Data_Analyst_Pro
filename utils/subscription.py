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

        # Normalize sub_status
        sub_status = (sub_status or "free").strip().lower()

        # Super Admin accounts have unlimited lifetime Pro access
        if role in ["Super Admin", "SuperAdmin", "Admin"]:
            return {
                "status": "active",
                "days_left": 999,
                "end_date": "Unlimited Lifetime Access",
                "is_active": True,
                "is_pro": True,
                "max_upload_mb": 500,
                "plan_name": "Enterprise SuperAdmin",
                "banner_type": "",
                "banner_message": "",
                "show_banner": False,
                "has_pending_payment": False
            }

        # Check if user has uploaded a pending payment receipt proof
        has_pending = False
        try:
            with get_db_cursor() as cursor:
                cursor.execute("SELECT TOP 1 PaymentID FROM Payments WHERE UserID = ? AND Status IN ('Pending', 'Pending_Review')", (user_id,))
                if cursor.fetchone():
                    has_pending = True
        except Exception:
            pass

        # Free tier user (free forever, max 1 MB uploads, full platform access)
        if sub_status in ("free", "basic", "trial"):
            banner_type = ""
            banner_message = ""
            show_banner = False
            if has_pending:
                banner_type = "info"
                banner_message = "PAYMENT PROOF UNDER REVIEW: Your Pro Plan upgrade payment proof (PKR 1,499) is currently under Super Admin review. 500 MB access will be unlocked upon approval!"
                show_banner = True

            return {
                "status": "free",
                "days_left": 999,
                "end_date": "Free Forever",
                "is_active": True,
                "is_pro": False,
                "max_upload_mb": 1,
                "plan_name": "Free Plan",
                "banner_type": banner_type,
                "banner_message": banner_message,
                "show_banner": show_banner,
                "has_pending_payment": has_pending
            }

        if sub_status in ("pending_approval", "rejected"):
            return {
                "status": sub_status,
                "days_left": 0,
                "end_date": "N/A",
                "is_active": False,
                "is_pro": False,
                "max_upload_mb": 1,
                "plan_name": "Free Plan",
                "banner_type": "warning",
                "banner_message": "Payment verification rejected or pending. Please contact support or resubmit proof.",
                "show_banner": True,
                "has_pending_payment": has_pending
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

        # Determine true status. "suspended" means an admin ban (IsActive = 0). A row the
        # old expiry sweep marked 'suspended' while still active is just a lapsed Pro plan.
        if not is_active:
            calc_status = "suspended"
        elif sub_status == "suspended":
            calc_status = "expired"
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

        if has_pending and calc_status != "active":
            banner_type = "info"
            banner_message = "PAYMENT PROOF UNDER REVIEW: Your payment receipt proof (PKR 1,499) has been uploaded and is currently under Super Admin review. Account access will be activated immediately upon approval!"
            show_banner = True
        elif calc_status == "suspended":
            banner_type = "danger"
            banner_message = "ACCOUNT SUSPENDED: This account has been suspended by the administrator. Please contact support."
            show_banner = True
        elif calc_status == "expired":
            # Pro lasts 30 days, then the account is simply on the Free plan (the daily
            # sweep sets SubscriptionStatus = 'free'). Nothing is locked.
            banner_type = "info"
            banner_message = f"Your 30-day Pro plan ended on {end_date_str}. You are on the Free plan now (uploads under 1 MB); your datasets are kept. Renew Pro (PKR 1,499) any time for uploads up to 500 MB."
            show_banner = True
        elif calc_status == "expiring_soon":
            banner_type = "warning"
            banner_message = f"ANNOUNCEMENT: Your Pro subscription plan ends in {days_left} day(s) on {end_date_str}! Please renew your PKR 1,499 plan to avoid service interruption."
            show_banner = True

        # "expiring_soon" is still within the paid 30-day window (days_left is 1-5, not
        # 0) - it's a renewal warning, not a downgrade. Only "expired"/"suspended" should
        # drop a user to the free tier; treating "expiring_soon" as not-pro here would
        # cut a paying user's 500 MB access short by up to 5 days before their actual
        # SubscriptionEndDate.
        is_pro_active = (calc_status in ("active", "expiring_soon") and bool(is_active))

        return {
            "status": calc_status,
            "days_left": max(0, days_left),
            "end_date": end_date_str,
            "is_active": bool(is_active) and calc_status != "suspended",
            "is_pro": is_pro_active,
            "max_upload_mb": 500 if is_pro_active else 1,
            "plan_name": "Pro Plan (PKR 1,499/mo)" if is_pro_active else "Free Plan",
            "banner_type": banner_type,
            "banner_message": banner_message,
            "show_banner": show_banner,
            "has_pending_payment": has_pending
        }
    except Exception as err:
        print("Subscription Info Error:", err)
        return {
            "status": "free",
            "days_left": 999,
            "end_date": "Free Tier",
            "is_active": True,
            "is_pro": False,
            "max_upload_mb": 1,
            "plan_name": "Free Plan",
            "show_banner": False
        }
