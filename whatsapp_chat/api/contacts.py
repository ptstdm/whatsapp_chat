import frappe


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
        "WhatsApp Contact", fields=["*"], order_by="creation desc"
    )

    mobile_nos = frappe.db.sql(
        """
        SELECT RIGHT(`from`, 10) AS mobile_no 
        FROM `tabWhatsApp Message`
        WHERE `from` IS NOT NULL AND creation >= CONVERT_TZ(NOW() - INTERVAL 1 DAY, 'UTC', 'Asia/Kolkata');
    """,
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
