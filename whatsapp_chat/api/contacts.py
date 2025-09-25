import frappe



@frappe.whitelist()
def create(contact_name, mobile_no, email):
    """Create contact."""
    frappe.get_doc({
        "doctype": "WhatsApp Contact",
        "contact_name": contact_name,
        "mobile_no": mobile_no,
        "email": email
    }).save()
    return "email"


@frappe.whitelist()
def get(email):
    """Get all contacts assigned to email."""
    email = [email]
    if email == "Administrator":
        email.append("")
    
    return frappe.db.get_all(
        "WhatsApp Contact",
        filters={
            "email": ["in", email]
        },
        fields=["*"]
    )
