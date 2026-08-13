"""
AI Smart Greeting & Personalized Assistant Engine (Enterprise Level)
AI Data Analyst Pro
Handles user recognition, dynamic prompt card formatting, multi-user isolation,
device security analysis, and persistent notification tracking.
"""

import json
from datetime import datetime
from flask import session, request
from database.connection import get_db_cursor
from database.queries import (
    create_ai_notification,
    get_total_datasets,
    get_total_queries,
    get_total_charts_generated
)


def get_first_name(full_name=None, first_name=None, username=None):
    """
    Extract the user's registered first name dynamically.
    Never uses hardcoded names. Supports compound names gracefully.
    Examples:
        "Muhammad Ahsan" -> "Ahsan"
        "Ahsan" -> "Ahsan"
        "Sarah Jenkins" -> "Sarah"
    """
    if first_name and first_name.strip():
        return first_name.strip().split()[0].capitalize()

    if full_name and full_name.strip():
        parts = [p for p in full_name.strip().split() if p.isalpha()]
        if len(parts) > 1 and parts[0].lower() in ["mr", "mr.", "ms", "ms.", "mrs", "mrs.", "dr", "dr.", "muhammad", "mohammad", "md", "syed"]:
            return parts[1].capitalize()
        elif parts:
            return parts[0].capitalize()

    if username and username.strip():
        return username.strip().split()[0].capitalize()

    return "User"


def parse_client_device(user_agent_str=""):
    """
    Parse client User-Agent string to extract human-readable Device and Browser details.
    """
    ua = user_agent_str or ""

    # Device Detection
    device = "Windows Desktop"
    if "Windows NT 10.0" in ua or "Windows NT 11.0" in ua:
        device = "Windows 11"
    elif "Windows NT" in ua:
        device = "Windows 10"
    elif "Macintosh" in ua or "Mac OS" in ua:
        device = "macOS"
    elif "iPhone" in ua or "iPad" in ua:
        device = "iOS Device"
    elif "Android" in ua:
        device = "Android Device"
    elif "Linux" in ua:
        device = "Linux Workstation"

    # Browser Detection
    browser = "Chrome Browser"
    if "Edg/" in ua or "Edge" in ua:
        browser = "Microsoft Edge"
    elif "Firefox/" in ua:
        browser = "Mozilla Firefox"
    elif "Safari/" in ua and "Chrome" not in ua:
        browser = "Apple Safari"
    elif "Chrome/" in ua:
        browser = "Google Chrome"

    # Location (Local default with fallback)
    location = "Karachi, Pakistan"

    return {
        "device": device,
        "browser": browser,
        "location": location
    }


def fetch_user_workspace_stats(user_id):
    """
    Retrieve real live dataset counts, report counts, and query counts for a given user.
    Strictly isolated per UserID.
    """
    datasets_cnt = 0
    queries_cnt = 0
    reports_cnt = 0
    last_login_str = "Recent Session"

    if not user_id:
        return last_login_str, 0, 0, 0

    try:
        with get_db_cursor() as cursor:
            # Datasets count
            cursor.execute(get_total_datasets(user_id=user_id))
            r1 = cursor.fetchone()
            if r1:
                datasets_cnt = int(r1[0])

            # Total Queries count
            cursor.execute(get_total_queries(user_id=user_id))
            r2 = cursor.fetchone()
            if r2:
                queries_cnt = int(r2[0])

            # Reports / Charts count
            cursor.execute(get_total_charts_generated(user_id=user_id))
            r3 = cursor.fetchone()
            if r3:
                reports_cnt = int(r3[0])

            # Last Login Timestamp from LoginHistory
            cursor.execute("""
                SELECT TOP 2 CreatedAt
                FROM LoginHistory
                WHERE UserID = ? AND Status = 'Success'
                ORDER BY CreatedAt DESC
            """, (user_id,))
            logins = cursor.fetchall()
            if len(logins) > 1 and logins[1][0]:
                dt = logins[1][0]
                if isinstance(dt, str):
                    dt = datetime.fromisoformat(dt)
                last_login_str = dt.strftime("%d %B %Y\n%I:%M %p")
            elif len(logins) == 1 and logins[0][0]:
                dt = logins[0][0]
                if isinstance(dt, str):
                    dt = datetime.fromisoformat(dt)
                last_login_str = dt.strftime("%d %B %Y\n%I:%M %p")
            else:
                last_login_str = datetime.now().strftime("%d %B %Y\n%I:%M %p")

    except Exception as err:
        print("Error fetching user workspace stats:", err)

    return last_login_str, datasets_cnt, reports_cnt, queries_cnt


# ==========================================
# PROMPT CARD BUILDERS
# ==========================================

def build_welcome_back_card(user_id, name):
    """
    Format AI welcome back card upon successful login.
    """
    first_name = get_first_name(full_name=name)
    last_login, datasets_cnt, reports_cnt, queries_cnt = fetch_user_workspace_stats(user_id)

    speech = f"Hello {first_name}! Welcome back. You have logged in successfully."

    return {
        "category": "login",
        "title": f"Welcome back, {first_name}!",
        "lines": [f"Hello {first_name}!", "You have logged in successfully."],
        "speech": speech,
        "metrics": {
            "Last Login": last_login,
            "Datasets": datasets_cnt,
            "Reports": reports_cnt,
            "AI Queries": queries_cnt
        },
        "auto_dismiss": 6
    }


def build_farewell_card(name):
    """
    Format AI farewell message upon logout.
    """
    first_name = get_first_name(full_name=name)
    speech = f"Goodbye {first_name}. You have logged out successfully."
    return {
        "category": "logout",
        "title": f"Goodbye {first_name}",
        "lines": [f"Goodbye {first_name}.", "You have logged out successfully."],
        "speech": speech,
        "auto_dismiss": 5
    }


def build_signup_card(name):
    """
    Format AI card after user registration.
    """
    first_name = get_first_name(full_name=name)
    speech = f"Welcome {first_name}! Your account has been created successfully."
    return {
        "category": "signup",
        "title": f"Welcome {first_name}!",
        "lines": [f"Welcome {first_name}!", "Your account has been created successfully."],
        "speech": speech,
        "auto_dismiss": 6
    }


def build_email_verified_card(name):
    """
    Format AI card after email verification.
    """
    first_name = get_first_name(full_name=name)
    speech = f"Congratulations {first_name}! Your email has been verified."
    return {
        "category": "email_verified",
        "title": "Email Verified",
        "lines": [f"Congratulations {first_name}!", "Your email has been verified successfully."],
        "speech": speech,
        "auto_dismiss": 5
    }


def build_forgot_password_card(name):
    """
    Format AI card after requesting password reset link.
    """
    first_name = get_first_name(full_name=name)
    speech = f"Hello {first_name}. A password reset link has been sent to your email."
    return {
        "category": "forgot_password",
        "title": "Password Reset Link Sent",
        "lines": [f"Hello {first_name}.", "Password reset link sent to your registered email."],
        "speech": speech,
        "auto_dismiss": 5
    }


def build_password_changed_card():
    """
    Format AI card after password update.
    """
    speech = "Your password has been updated successfully."
    return {
        "category": "password_changed",
        "title": "Password Updated",
        "lines": ["Your password has been updated successfully."],
        "speech": speech,
        "auto_dismiss": 5
    }


def build_profile_updated_card(name):
    """
    Format AI card after profile update.
    """
    first_name = get_first_name(full_name=name)
    speech = f"Profile updated successfully."
    return {
        "category": "profile_updated",
        "title": "Profile Saved",
        "lines": [f"Great job {first_name}!", "Your profile has been updated successfully."],
        "speech": speech,
        "auto_dismiss": 5
    }


def build_avatar_updated_card(name):
    """
    Format AI card after profile picture update.
    """
    first_name = get_first_name(full_name=name)
    speech = f"Profile picture updated successfully."
    return {
        "category": "avatar_updated",
        "title": "Profile Picture Updated",
        "lines": ["Profile picture updated successfully."],
        "speech": speech,
        "auto_dismiss": 5
    }


def build_dataset_upload_card(name, dataset_name, rows, columns):
    """
    Format AI card after successful dataset upload (Speech disabled for file operations).
    """
    return {
        "category": "upload",
        "title": f"Dataset Uploaded: {dataset_name}",
        "lines": [f"Dataset '{dataset_name}' uploaded successfully."],
        "speech": "",
        "metrics": {
            "Dataset": dataset_name,
            "Rows": f"{rows:,}",
            "Columns": columns
        },
        "auto_dismiss": 5
    }


def build_dataset_delete_card():
    """
    Format AI card after deleting a dataset (Speech disabled for file operations).
    """
    return {
        "category": "delete",
        "title": "Dataset Deleted",
        "lines": ["Dataset deleted successfully."],
        "speech": "",
        "auto_dismiss": 5
    }


def build_dataset_edit_card():
    """
    Format AI card after updating dataset info (Speech disabled for file operations).
    """
    return {
        "category": "edit",
        "title": "Dataset Updated",
        "lines": ["Dataset information updated successfully."],
        "speech": "",
        "auto_dismiss": 5
    }


def build_ai_query_initiated_card(name):
    """
    Format AI card when query begins execution (Speech disabled for file operations).
    """
    return {
        "category": "query_init",
        "title": "Analyzing Dataset...",
        "lines": [f"Analyzing dataset. Please wait..."],
        "speech": "",
        "auto_dismiss": 0
    }


def build_ai_query_completed_card():
    """
    Format AI card when query completes execution (Speech disabled for file operations).
    """
    return {
        "category": "query_complete",
        "title": "Analysis Complete",
        "lines": ["Analysis completed successfully."],
        "speech": "",
        "auto_dismiss": 5
    }


def build_report_export_card(export_type="PDF"):
    """
    Format AI card after exporting a report (Speech disabled for file operations).
    """
    formatted_type = export_type.upper()
    if formatted_type == "PPTX":
        formatted_type = "PowerPoint"
    return {
        "category": "export",
        "title": f"{formatted_type} Report Exported",
        "lines": [f"{formatted_type} report exported successfully."],
        "speech": "",
        "auto_dismiss": 5
    }


def build_security_alert_card(name, device, browser, location):
    """
    Format AI security alert card upon login from a new device/location.
    """
    first_name = get_first_name(full_name=name)
    speech = f"Hello {first_name}. Security notice: login from a new device detected."
    return {
        "category": "security_alert",
        "title": "New Device Alert",
        "lines": [f"Hello {first_name}.", "Security Notice: Login from a new device detected."],
        "speech": speech,
        "metrics": {
            "Device": device,
            "Browser": browser,
            "Location": location
        },
        "auto_dismiss": 8
    }


def build_failed_login_card():
    """
    Format AI card for failed login attempt.
    """
    speech = "Login failed. Incorrect email or password."
    return {
        "category": "failed_login",
        "title": "Login Failed",
        "lines": ["Login failed. Incorrect email or password."],
        "speech": speech,
        "auto_dismiss": 5
    }


def build_session_expired_card():
    """
    Format AI card for expired session.
    """
    speech = "Your session has expired. Please log in again."
    return {
        "category": "session_expired",
        "title": "Session Expired",
        "lines": ["Your session has expired. Please log in again."],
        "speech": speech,
        "auto_dismiss": 6
    }


def build_account_locked_card():
    """
    Format AI card when account is locked.
    """
    speech = "Account locked due to multiple failed login attempts. Try again in 15 minutes."
    return {
        "category": "account_locked",
        "title": "Account Locked",
        "lines": ["Account locked due to multiple failed login attempts. Try again in 15 minutes."],
        "speech": speech,
        "auto_dismiss": 8
    }


# ==========================================
# EVENT DISPATCHER & DB PERSISTENCE
# ==========================================

def trigger_ai_event(user_id, card_data, save_to_history=True):
    """
    Store prompt card in session['pending_ai_card'] for immediate client UI popup
    and insert into AINotifications table for user history tracking.
    """
    session["pending_ai_card"] = card_data

    if save_to_history and user_id:
        try:
            category = card_data.get("category", "general")
            title = card_data.get("title", "AI Assistant Alert")

            message_body = "\n".join(card_data.get("lines", []))
            if card_data.get("metrics"):
                metric_items = [f"• {k}: {v}" for k, v in card_data["metrics"].items()]
                message_body += "\n\n" + "\n".join(metric_items)
            if card_data.get("footer"):
                message_body += "\n\n" + card_data["footer"]

            metadata_json = json.dumps({
                "metrics": card_data.get("metrics"),
                "footer": card_data.get("footer"),
                "auto_dismiss": card_data.get("auto_dismiss", 8)
            })

            with get_db_cursor(commit=True) as cursor:
                cursor.execute(
                    create_ai_notification(),
                    (user_id, category, title, message_body, metadata_json)
                )
        except Exception as err:
            print("Error persisting AI Notification:", err)
