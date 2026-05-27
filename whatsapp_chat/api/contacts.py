import frappe
from frappe.utils import add_to_date, now_datetime


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

    # Get datetime for 1 day ago
    one_day_ago = add_to_date(now_datetime(), days=-1)

    mobile_nos = frappe.db.sql(
        """
        SELECT DISTINCT RIGHT(`from`, 10) AS mobile_no
        FROM `tabWhatsApp Message`
        WHERE `from` IS NOT NULL
            AND TRIM(`from`) != ''
            AND LENGTH(TRIM(`from`)) >= 10
            AND creation >= %(one_day_ago)s
        HAVING mobile_no IS NOT NULL
            AND mobile_no != ''
        ORDER BY mobile_no;
    """,
        {"one_day_ago": one_day_ago},
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
