import frappe

def execute():
    wa_msgs = frappe.get_all(
        "WhatsApp Message",
        filters={
            "message_type": ["!=", "Template"],
            "type": "Incoming",
            "attach": ["is", "not set"]
        },
        fields=["name", "from", "reference_doctype", "reference_name"],
        group_by="`from`",
        order_by="creation desc"
    )

    wa_contacts = frappe.get_all(
        "WhatsApp Contact",
        fields=["*"]
    )

    mobile_nos = [d.get("from")[-10:] for d in wa_msgs]

    employees = frappe.get_all(
        "Employee",
        filters={
            "name": ["in", [d.get("lead_owner") for d in wa_msgs if d.get("lead_owner")]]
        },
        fields=["name", "employee_name", "user"]
    )
    employees = {emp.name: emp for emp in employees}

    for contact in wa_contacts:
        mobile_no = contact.get("mobile_no")[-10:]
        if mobile_no not in mobile_nos:
            continue

        lead = frappe.db.get_value("Lead", {"contact_number": ["like", f"%{mobile_no}"]}, ["name", "lead_owner"], order_by="creation desc", as_dict=True)

        if not lead or not lead.lead_owner:
            continue

        wa_contact_doc = frappe.get_doc("WhatsApp Contact", contact.name)
        wa_contact_doc.employee = lead.lead_owner
        wa_contact_doc.employee_name = employees.get(lead.lead_owner, {}).get("employee_name")
        wa_contact_doc.email = employees.get(lead.lead_owner, {}).get("user") or "Administrator"

        wa_contact_doc.save()