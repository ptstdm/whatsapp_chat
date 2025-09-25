import frappe

"""
Updates the associated WhatsApp Contact's Employee when the Lead owner changes.

Args:
    doc (Document): The Lead document being updated.
    method (str): The method triggering this function.

Behavior:
- Checks if `lead_owner` has changed.
- Updates the WhatsApp Contact's employee to match the new lead owner.
- Ensures correct employee receives WhatsApp chat updates.
"""
def on_update(doc, method):
    if not doc.has_value_changed('lead_owner'):
        return
    
    whatsapp_contact = frappe.get_all(
        "WhatsApp Contact",
        filters={
            "mobile_no": ["like", f"%{doc.contact_number[-10:]}"]
        },
        fields=["name", "email"],
        order_by="creation desc"
    )

    if not whatsapp_contact:
        return

    new_user = None

    if doc.lead_owner == whatsapp_contact[0].employee:
        return
    
    if doc.lead_owner:
        new_user = frappe.db.get_value("Employee", doc.lead_owner, "user")

    
    whatsapp_contact = frappe.get_cached_doc("WhatsApp Contact", whatsapp_contact[0].name)
    whatsapp_contact.employee = doc.lead_owner
    whatsapp_contact.employee_name = doc.lead_owner_name
    whatsapp_contact.email = new_user
    
    whatsapp_contact.save(ignore_permissions=True)