import mimetypes

import frappe

# Roles that may reply to any chat. Everyone can *view* every chat; replying is
# restricted to managers, the chat's owner, or unassigned (shared-pool) chats.
MANAGER_ROLES = {"System Manager", "Sales Manager", "Sales Co-ordinator"}


def can_reply_to_contact(contact_email):
    """Whether the current user may reply to a chat owned by `contact_email`."""
    if MANAGER_ROLES & set(frappe.get_roles(frappe.session.user)):
        return True
    # Unassigned (shared pool) or owned by the current user.
    return not contact_email or contact_email == frappe.session.user


def _contact_email_by_number(to):
    if not to:
        return None
    return frappe.db.get_value(
        "WhatsApp Contact",
        {"mobile_no": ["like", f"%{to[-10:]}"]},
        "email",
        order_by="modified desc",
    )


def assert_can_reply_number(to):
    if not can_reply_to_contact(_contact_email_by_number(to)):
        frappe.throw(
            frappe._("This chat is assigned to another agent — view only."),
            frappe.PermissionError,
        )


@frappe.whitelist()
def get_all(room: str, user_no: str, limit=30, before=None, before_name=None, before_kind=None):
    """Get a page of a room's messages for lazy/infinite scroll.

    Returns the newest `limit` rows (text + attachment), in ascending order for
    display. For the next older page pass the oldest row's composite cursor —
    `before` (its `creation`), `before_name` (its message `name`) and
    `before_kind` (0=text, 1=attachment). The UNION can emit two rows per message
    with the same creation, so the (creation, name, kind) cursor is required to
    avoid skipping/duplicating rows at page boundaries.

    Args:
        room (str): Room's name.
        user_no (str): Counterpart phone number.
        limit (int): Page size (clamped to 1..200).
        before / before_name / before_kind: composite "older than" cursor.
    """
    # limit is user-controlled (whitelisted) — parse defensively and clamp.
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 30
    limit = max(1, min(limit, 200))

    # frappe.call() JSON-encodes args, so a JS `null` arrives as the string "null".
    if before in (None, "", "null", "undefined"):
        before = None
    if before_name in (None, "", "null", "undefined"):
        before_name = None
    try:
        before_kind = int(before_kind)
    except (TypeError, ValueError):
        before_kind = 0

    rows = frappe.db.sql(
        """
        SELECT * FROM (
            (
                -- Row for text message (only if message exists)
                SELECT
                    m.name AS name, 0 AS kind,
                    m.creation,
                    CASE WHEN m.`to` <> '' THEN m.`to` ELSE 'Administrator' END AS sender_user_no,
                    m.message AS content,
                    m.type AS type,
                    COALESCE(u.full_name, m.owner) AS sent_by
                FROM `tabWhatsApp Message` m
                LEFT JOIN `tabUser` u ON u.name = m.owner
                WHERE
                    m.message IS NOT NULL AND m.message <> ''
                    AND (RIGHT(m.`to`, 10) = %(user_no)s OR RIGHT(m.`from`, 10) = %(user_no)s)
            )
            UNION ALL
            (
                -- Row for attachment (only if attach exists)
                SELECT
                    m.name AS name, 1 AS kind,
                    m.creation,
                    CASE WHEN m.`to` <> '' THEN m.`to` ELSE 'Administrator' END AS sender_user_no,
                    m.attach AS content,
                    m.type AS type,
                    COALESCE(u.full_name, m.owner) AS sent_by
                FROM `tabWhatsApp Message` m
                LEFT JOIN `tabUser` u ON u.name = m.owner
                WHERE
                    m.attach IS NOT NULL AND m.attach <> ''
                    AND (RIGHT(m.`to`, 10) = %(user_no)s OR RIGHT(m.`from`, 10) = %(user_no)s)
            )
        ) AS u
        WHERE (
            %(before)s IS NULL
            OR u.creation < %(before)s
            OR (u.creation = %(before)s AND u.name < %(before_name)s)
            OR (u.creation = %(before)s AND u.name = %(before_name)s AND u.kind < %(before_kind)s)
        )
        ORDER BY u.creation DESC, u.name DESC, u.kind DESC
        LIMIT %(limit)s
        """,
        {
            "user_no": user_no[-10:],
            "before": before,
            "before_name": before_name or "",
            "before_kind": before_kind,
            "limit": limit,
        },
        as_dict=True,
    )
    rows.reverse()  # ascending for display
    return rows


@frappe.whitelist()
def get_room_senders(phones=None):
    """Return distinct (owner, `to`) pairs for outgoing messages to the given phone numbers, so the
    client can build an owner -> rooms index for the 'Sent by' filter. Scoped to the numbers passed
    in (the rooms actually shown — contacts with an incoming message in the last 24h), so we don't
    scan/return senders for rooms that aren't displayed. The client maps each `to` to a loaded room
    by last-10-digit match (no non-indexable RIGHT()=RIGHT() join).

    Args:
        phones: JSON list of last-10-digit phone numbers for the displayed rooms.
    """
    phones = frappe.parse_json(phones) if phones else []
    phones = [p for p in phones if p]
    if not phones:
        return []

    return frappe.db.sql(
        """
        SELECT DISTINCT owner, `to`
        FROM `tabWhatsApp Message`
        WHERE type = 'Outgoing'
          AND `to` <> ''
          AND RIGHT(`to`, 10) IN %(phones)s
        """,
        {"phones": tuple(phones)},
        as_dict=True,
    )


@frappe.whitelist()
def mark_as_read(room):
    doc = frappe.get_doc("WhatsApp Contact", room)
    # A read-only viewer (non-owner, non-manager) opening someone else's chat
    # must not clear the owner's unread state — skip silently.
    if not can_reply_to_contact(doc.email):
        return "skipped"
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
    assert_can_reply_number(user_no)
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
        owner=doc.owner,
        message_id=doc.name,
    )


def update_contact_on_message(mobile_no, message, message_type, profile_name, is_outgoing, owner=None, message_id=None):
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
        current_contact_name = chat_doc.contact_name or ""
        if current_contact_name[-10:] == mobile_no and profile_name:
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

    # Push every message (incoming AND outgoing) so the open chat reflects
    # AI/other-agent/template replies live and the list preview/order updates.
    payload = {
        "content": message,
        "creation": frappe.utils.now(),
        "room": chat_doc.name,
        "type": message_type,
        "owner": owner,
        "message_id": message_id,
        "sender_user_no": None if is_outgoing else mobile_no,
    }

    # Per-room channel: any open ChatSpace (including managers viewing another
    # agent's chat) subscribes to `this.profile.room` and appends live.
    frappe.publish_realtime(chat_doc.name, payload)

    # List channel: updates the owner's chat-list preview / order / unread badge.
    if chat_doc.email:
        frappe.publish_realtime(
            "latest_chat_updates",
            payload,
            user=chat_doc.email,
        )
