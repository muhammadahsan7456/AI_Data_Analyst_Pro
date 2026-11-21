"""
Numbered invoices for approved (Completed) payments.

- Number: ``AIDAP-<year>-<PaymentID, 6 digits>``. Unique because PaymentID is unique,
  ordered by PaymentID, and stored on the payment row when the admin approves it
  (``Payments.InvoiceNumber``), so it never changes after it is issued.
- Plan period: stored at approval (``Payments.PeriodStart`` / ``PeriodEnd``). Payments
  approved before these columns existed get a period computed from the approval date
  (one calendar month), and the invoice says it was computed.
- Company details come from settings, never invented: INVOICE_COMPANY_NAME,
  INVOICE_COMPANY_ADDRESS, INVOICE_COMPANY_TAX_ID (all optional), the PUBLIC_BASE_URL
  website and the single SUPPORT_EMAIL.
Only Completed payments have an invoice; pending/rejected ones do not.
"""
from __future__ import annotations

import calendar
import os
from datetime import date, datetime

from database.connection import get_db_cursor

INVOICE_PREFIX = "AIDAP"

_SELECT = """
    SELECT P.PaymentID, P.UserID, U.FullName, U.Email, P.Amount, P.Currency, P.PaymentMethod,
           P.TransactionID, P.Status, P.PlanName, P.PaymentDate, P.ReviewedAt,
           P.InvoiceNumber, P.PeriodStart, P.PeriodEnd
    FROM Payments P
    LEFT JOIN Users U ON P.UserID = U.UserID
    WHERE P.PaymentID = ?
"""


def _to_date(value):
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:26], fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return None


def add_one_month(d: date) -> date:
    """Same rule as SQL Server DATEADD(month, 1, d): clamp to the target month's last day."""
    y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def invoice_number(payment_id: int, issued: date | datetime | None = None) -> str:
    year = (_to_date(issued) or date.today()).year
    return f"{INVOICE_PREFIX}-{year}-{int(payment_id):06d}"


def company_details() -> dict:
    from site_config import support_email
    try:
        from utils.public_url import public_base_url
        website = public_base_url()
    except Exception:
        website = ""
    return {
        "name": os.getenv("INVOICE_COMPANY_NAME", "").strip() or "AI Data Analyst Pro",
        "address": os.getenv("INVOICE_COMPANY_ADDRESS", "").strip(),
        "tax_id": os.getenv("INVOICE_COMPANY_TAX_ID", "").strip(),
        "website": website.replace("https://", "").replace("http://", "").rstrip("/"),
        "email": support_email(),
    }


def record_invoice_on_approval(cursor, payment_id: int, period_start, period_end) -> str:
    """Store the number and plan period on the payment row (same transaction as the approval)."""
    number = invoice_number(payment_id, date.today())
    cursor.execute(
        "UPDATE Payments SET InvoiceNumber = ?, PeriodStart = ?, PeriodEnd = ? "
        "WHERE PaymentID = ? AND InvoiceNumber IS NULL",
        (number, _to_date(period_start), _to_date(period_end), payment_id),
    )
    return number


def _fmt(d):
    return d.strftime("%d %b %Y") if d else ""


def load_invoice(payment_id: int, user_id: int | None = None) -> dict | None:
    """Invoice data for a payment, or None when it does not exist / is not the user's.
    ``has_invoice`` is False for anything that is not Completed."""
    sql = _SELECT + (" AND P.UserID = ?" if user_id is not None else "")
    params = (payment_id, user_id) if user_id is not None else (payment_id,)
    with get_db_cursor() as cursor:
        cursor.execute(sql, params)
        row = cursor.fetchone()
    if not row:
        return None
    (p_id, u_id, full_name, email, amount, currency, method, txn_id, status, plan,
     p_date, reviewed_at, number, period_start, period_end) = row
    status = status or "Pending"
    issued = _to_date(reviewed_at) or _to_date(p_date)
    start, end = _to_date(period_start), _to_date(period_end)
    period_estimated = False
    if status == "Completed" and not (start and end):
        start = issued
        end = add_one_month(start) if start else None
        period_estimated = True
    return {
        "id": p_id,
        "user_id": u_id,
        "user_name": full_name or "Customer",
        "email": email or "",
        "amount": float(amount or 0.0),
        "currency": currency or "PKR",
        "payment_method": method or "Not recorded",
        "txn_id": txn_id or "Not provided",
        "status": status,
        "plan_name": plan or "Pro plan",
        "payment_date": _fmt(_to_date(p_date)),
        "issue_date": _fmt(issued),
        "invoice_number": number or (invoice_number(p_id, issued) if status == "Completed" else ""),
        "period_start": _fmt(start),
        "period_end": _fmt(end),
        "period_label": f"{_fmt(start)} to {_fmt(end)}" if start and end else "",
        "period_estimated": period_estimated,
        "has_invoice": status == "Completed",
        "company": company_details(),
    }


def invoice_filename(inv: dict) -> str:
    return f"Invoice_{inv['invoice_number']}.pdf"
