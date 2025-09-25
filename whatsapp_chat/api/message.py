import frappe
import mimetypes



@frappe.whitelist()
def get_all(room: str, user_no: str):
    """Get all the messages of a particular room

    Args:
        room (str): Room's name.

    """
    return frappe.db.sql("""
        SELECT creation,
        case
            when `to` <> '' then `to`
            else
            'Administrator'
        end as sender_user_no,
        case
            when content_type = 'text' then message
            else attach
        end as content
        from `tabWhatsApp Message` where (`to` = %(user_no)s or `from` = %(user_no)s)
        AND message_type <> 'Template'
        order by creation asc
    """, {"user_no": user_no}, as_dict=True)


@frappe.whitelist()
def mark_as_read(room):
    doc = frappe.get_doc("WhatsApp Contact", room)
    doc.is_read = 1
    doc.save(ignore_permissions=True)

    return "ok"



@frappe.whitelist()
def send(content, user, room, user_no, attachment=None):
    content_type = "text"
    if attachment:
        file_type = mimetypes.guess_type(content)[0]
        if file_type in ["image/apng","image/avif","image/gif","image/jpeg","image/png","image/svg","image/webp"]:
            content_type = 'image'
        elif file_type in ["application/pdf", "application/vnd.ms-powerpoint", "application/msword", "application/vnd.ms-excel", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/vnd.openxmlformats-officedocument.presentationml.presentation", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"]:
            content_type = "document"
        elif file_type in ["audio/aac", "audio/mp4", "audio/mpeg", "audio/amr", "audio/ogg"]:
            content_type = 'audio'
        elif file_type in ["video/mp4", "video/3gp"]:
            content_type = "video"

        frappe.get_doc({
            "doctype": "WhatsApp Message",
            "to": user_no,
            "type": "Outgoing",
            "attach": content,
            "content_type": content_type
        }).save()
    else:
        frappe.get_doc({
            "doctype": "WhatsApp Message",
            "to": user_no,
            "type": "Outgoing",
            "message": content,
            "content_type": content_type
        }).save()

    return "ok"


def last_message(doc, method):
    if doc.type == 'Outgoing':
        mobile_no = doc.to
    else:
        mobile_no = doc.get("from")

    # get emp details
    emp = get_lead_owner(mobile_no) or {}

    contact_name = frappe.db.get_value("WhatsApp Contact", filters={"mobile_no": mobile_no})
    if contact_name:
        chat_doc = frappe.get_doc("WhatsApp Contact", contact_name)
        chat_doc.last_message = doc.message
        chat_doc.is_read = 0
        chat_doc.employee = emp.get("name")
        chat_doc.employee_name = emp.get("employee_name")
        chat_doc.email = emp.get("user", "Administrator")
        chat_doc.save(ignore_permissions=True)
    else:
        chat_doc = frappe.get_doc({
            "doctype": "WhatsApp Contact",
            "mobile_no": mobile_no,
            "last_message": doc.message,
            "contact_name": mobile_no,
            "is_read": 0,
            "employee": emp.get("name"),
            "employee_name": emp.get("employee_name"),
            "email": emp.get("user", "Administrator")
        })
        chat_doc.save(ignore_permissions=True)

    if chat_doc.email and doc.type != 'Outgoing':
        frappe.publish_realtime(
            "latest_chat_updates",
            {
                "content":  doc.message,
                "creation": frappe.utils.now(),
                "room": chat_doc.name
            }, user= chat_doc.email
        )

    return "ok"

def get_lead_owner(mobile_no) -> list | None:
    """Get lead owner for the lead with specified mobile number."""
    lead = frappe.get_all(
        "Lead",
        filters={
            "contact_number": ["like", mobile_no[-10:]]
        },
        fields=["name", "lead_owner"],
        order_by="creation desc",
        limit_page_length=1
    )

    if not lead:
        return None
    
    emp = frappe.db.get_value("Employee", lead[0].lead_owner, ["name", "employee_name", "user"], as_dict=True)

    return emp