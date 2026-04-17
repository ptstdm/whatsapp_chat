import mimetypes

import frappe


@frappe.whitelist()
def get_all(room: str, user_no: str):
    """Get all the messages of a particular room

    Args:
        room (str): Room's name.

    """
    return frappe.db.sql(
        """
    (
        -- Row for text message (only if message exists)
        SELECT
            creation,
            CASE WHEN `to` <> '' THEN `to` ELSE 'Administrator' END AS sender_user_no,
            message AS content
        FROM `tabWhatsApp Message`
        WHERE
            message IS NOT NULL AND message <> ''
            AND (RIGHT(`to`, 10) = %(user_no)s OR RIGHT(`from`, 10) = %(user_no)s)
    )
    
    UNION ALL
    
    (
        -- Row for attachment (only if attach exists)
        SELECT
            creation,
            CASE WHEN `to` <> '' THEN `to` ELSE 'Administrator' END AS sender_user_no,
            attach AS content
        FROM `tabWhatsApp Message`
        WHERE
            attach IS NOT NULL AND attach <> ''
            AND (RIGHT(`to`, 10) = %(user_no)s OR RIGHT(`from`, 10) = %(user_no)s)
    )
    
    ORDER BY creation ASC
    """,
        {"user_no": user_no[-10:]},
        as_dict=True,
    )


@frappe.whitelist()
def mark_as_read(room):
    doc = frappe.get_doc("WhatsApp Contact", room)
    doc.is_read = 1
    doc.db_update()

    mobile = doc.mobile_no[-10:]

    # 1. Find all message names where last 10 digits of from_ match
    message_names = frappe.db.get_list(
        "WhatsApp Message", filters={"from": ["like", f"%{mobile}"]}, pluck="name"
    )

    # 2. Update each message individually
    for name in message_names:
        frappe.db.set_value("WhatsApp Message", name, {"status": "marked as read"})

    return "ok"


@frappe.whitelist()
def send(content, user, room, user_no, attachment=None):
    content_type = "text"
    if attachment:
        file_type = mimetypes.guess_type(content)[0]
        if file_type in [
            "image/apng",
            "image/avif",
            "image/gif",
            "image/jpeg",
            "image/png",
            "image/svg",
            "image/webp",
        ]:
            content_type = "image"
        elif file_type in [
            "application/pdf",
            "application/vnd.ms-powerpoint",
            "application/msword",
            "application/vnd.ms-excel",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ]:
            content_type = "document"
        elif file_type in [
            "audio/aac",
            "audio/mp4",
            "audio/mpeg",
            "audio/amr",
            "audio/ogg",
        ]:
            content_type = "audio"
        elif file_type in ["video/mp4", "video/3gp"]:
            content_type = "video"

        frappe.get_doc(
            {
                "doctype": "WhatsApp Message",
                "to": user_no,
                "type": "Outgoing",
                "attach": content,
                "content_type": content_type,
            }
        ).save()
    else:
        frappe.get_doc(
            {
                "doctype": "WhatsApp Message",
                "to": user_no,
                "type": "Outgoing",
                "message": content,
                "content_type": content_type,
            }
        ).save()

    return "ok"


def last_message(doc, method):
    if doc.type == "Outgoing":
        mobile_no = doc.to
    else:
        mobile_no = doc.get("from")

    if not mobile_no:
        frappe.log_error(
            title="WhatsApp Contact Update Skipped - Missing Mobile Number",
            message=(
                f"A WhatsApp message (ID: {doc.name}) was received but has no mobile number.\n\n"
                f"Message Type : {doc.type}\n"
                f"From         : {doc.get('from') or 'Not provided'}\n"
                f"To           : {doc.to or 'Not provided'}\n\n"
                "Action Taken : Contact update was skipped. Please check this message manually in WhatsApp Message list."
            ),
        )
        return

    frappe.enqueue(
        "whatsapp_chat.api.message.update_contact_on_message",
        queue="default",
        mobile_no=mobile_no[-10:],
        message=doc.message,
        message_type=doc.type,
        profile_name=doc.get("profile_name"),
        is_outgoing=doc.type == "Outgoing",
    )


def update_contact_on_message(mobile_no, message, message_type, profile_name, is_outgoing):
    contact_name = frappe.db.get_value(
        "WhatsApp Contact",
        filters={"mobile_no": ["like", f"%{mobile_no}"]},  
        order_by="modified desc",
    )
    if contact_name:
        chat_doc = frappe.get_doc("WhatsApp Contact", contact_name)
        chat_doc.last_message = message
        chat_doc.message_type = message_type
        if not is_outgoing:
            chat_doc.is_read = 0
        if chat_doc.contact_name[-10:] == mobile_no and profile_name:
            chat_doc.contact_name = profile_name
        if is_outgoing:
            chat_doc.db_update()
        else:
            chat_doc.save(ignore_permissions=True)
    else:
        chat_doc = frappe.get_doc(
            {
                "doctype": "WhatsApp Contact",
                "mobile_no": mobile_no,
                "last_message": message,
                "contact_name": profile_name or mobile_no,
                "message_type": message_type,
                "is_read": 0,
            }
        )
        chat_doc.insert(ignore_permissions=True)

    if chat_doc.email and not is_outgoing:
        frappe.publish_realtime(
            "latest_chat_updates",
            {
                "content": message,
                "creation": frappe.utils.now(),
                "room": chat_doc.name,
            },
            user=chat_doc.email,
        )
