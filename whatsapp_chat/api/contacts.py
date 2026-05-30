import frappe
from frappe.utils import add_to_date, cint, now_datetime


def _get_chat_window_days(default_days: int = 7) -> int:
    """Window (in days) for the chat-list 'recent incoming message' filter.

    Sourced from the Leanerp WhatsApp Settings single. Falls back to the default
    when the value/doctype is unset or non-positive so the filter stays active.
    """
    try:
        days = cint(
            frappe.db.get_single_value("Leanerp WhatsApp Settings", "chat_list_window_days")
        )
    except Exception:
        return default_days
    return days if days > 0 else default_days


@frappe.whitelist()
def create(contact_name, mobile_no, email):
    """Create contact."""
    frappe.get_doc(
        {
            "doctype": "WhatsApp Contact",
            "contact_name": contact_name,
            "mobile_no": mobile_no,
            "email": email,
        }
    ).save()
    return "email"


@frappe.whitelist()
def get(email):
    """Get all contacts assigned to email."""
    contacts = frappe.db.get_list(
        "WhatsApp Contact", fields=["*"], order_by="modified desc"
    )

    # Only include contacts with an incoming message within the configured window.
    window_start = add_to_date(now_datetime(), days=-_get_chat_window_days())

    mobile_nos = frappe.db.sql(
        """
        SELECT DISTINCT RIGHT(`from`, 10) AS mobile_no
        FROM `tabWhatsApp Message`
        WHERE `from` IS NOT NULL
            AND TRIM(`from`) != ''
            AND LENGTH(TRIM(`from`)) >= 10
            AND creation >= %(window_start)s
        HAVING mobile_no IS NOT NULL
            AND mobile_no != ''
        ORDER BY mobile_no;
    """,
        {"window_start": window_start},
        as_list=True,
    )

    mobile_nos = [no[0] for no in mobile_nos]
    if not mobile_nos:
        return []

    return [
        contact
        for contact in contacts
        if contact.mobile_no and contact.mobile_no[-10:] in mobile_nos
    ]
