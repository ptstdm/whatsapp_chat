import mimetypes


import frappe
from leanerp.utils.db_retry import run_with_deadlock_retry
# Roles that may reply to any chat. Everyone can *view* every chat; replying is
# restricted to managers, the chat's owner, or unassigned (shared-pool) chats.
MANAGER_ROLES = {"System Manager", "Sales Manager", "Sales Co-ordinator"}

# update_contact_on_message runs in a background job and, on `innodb_snapshot_isolation=ON`, a
# concurrent committed write to the same WhatsApp Contact makes the get_doc->save read-modify-write
# raise 1020 (QueryDeadlockError). A targeted set_value (blind UPDATE, no optimistic-lock read) removes
# that class; the retry belt still covers a transient 1213/1205. Best-effort UI state — the next
# message re-syncs it — so log-only on exhaustion.
_CONTACT_UPDATE_RETRY_ATTEMPTS = 3
_CONTACT_UPDATE_BACKOFF_BASE = 0.1  # seconds; full-jitter exponential backoff


def _wa_normalized_col(direction):
	"""Indexed last-10-digit column on WhatsApp Message, or None.

	`direction` is "from" or "to". Returns `<direction>_normalized` when present so
	phone matching is an index lookup; returns None when the field is missing so
	callers fall back to the un-indexable RIGHT()/leading-wildcard LIKE form.
	"""
	col = f"{direction}_normalized"
	return col if frappe.db.has_column("WhatsApp Message", col) else None


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
def get_all(
	room: str, user_no: str, limit=30, before=None, before_name=None, before_kind=None
):
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

	# Match the contact by last-10-digit phone on either side of the message. Prefer the
	# indexed custom_*_normalized columns (index lookup) over RIGHT() (full-table scan).
	to_col, from_col = _wa_normalized_col("to"), _wa_normalized_col("from")
	if to_col and from_col:
		contact_match = f"(m.{to_col} = %(user_no)s OR m.{from_col} = %(user_no)s)"
	else:
		contact_match = (
			"(RIGHT(m.`to`, 10) = %(user_no)s OR RIGHT(m.`from`, 10) = %(user_no)s)"
		)

	rows = frappe.db.sql(
		f"""
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
					AND {contact_match}
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
					AND {contact_match}
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

	# Prefer the indexed to_normalized column over RIGHT() (full-table scan).
	to_col = _wa_normalized_col("to")
	match_expr = to_col if to_col else "RIGHT(`to`, 10)"
	return frappe.db.sql(
		f"""
		SELECT DISTINCT owner, `to`
		FROM `tabWhatsApp Message`
		WHERE type = 'Outgoing'
		  AND `to` <> ''
		  AND {match_expr} IN %(phones)s
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

	# 1. Find all message names where last 10 digits of `from` match. Prefer the indexed
	# from_normalized column (equality) over a leading-wildcard LIKE (full scan).
	from_col = _wa_normalized_col("from")
	from_filter = {from_col: mobile} if from_col else {"from": ["like", f"%{mobile}"]}
	message_names = frappe.db.get_list(
		"WhatsApp Message", filters=from_filter, pluck="name"
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


def _update_contact_with_retry(name, values):
	"""Apply a targeted WhatsApp Contact update, retrying through transient lock conflicts.
	A blind set_value (no optimistic-lock read) already removes the 1020 RMW class; this
	belt covers the rarer deadlock/lock-wait. Best-effort UI state (the next message
	re-syncs), so log-only on exhaustion."""

	def _op():
		frappe.db.set_value("WhatsApp Contact", name, values)
		frappe.db.commit()

	return run_with_deadlock_retry(
		op=_op,
		attempts=_CONTACT_UPDATE_RETRY_ATTEMPTS,
		backoff_base=_CONTACT_UPDATE_BACKOFF_BASE,
		exceptions=(
			frappe.QueryDeadlockError,
			frappe.QueryTimeoutError,
			frappe.exceptions.TimestampMismatchError,
		),
		log_title="update_contact_on_message: contact update failed after retries",
		log_context=f"WhatsApp Contact: {name}",
		on_exhausted="log",
	)

def update_contact_on_message(
	mobile_no,
	message,
	message_type,
	profile_name,
	is_outgoing,
	owner=None,
	message_id=None,
):
	contact = frappe.db.get_value(
		"WhatsApp Contact",
		filters={"mobile_no": ["like", f"%{mobile_no}"]},
		fieldname=["name", "contact_name", "email"],
		order_by="modified desc",
		as_dict=True,
	)
	if not contact:
		try:
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
			room_name = chat_doc.name
			room_email = chat_doc.email
		except frappe.UniqueValidationError:
			# TOCTOU race: a concurrent message for the same NEW number passed the same existence check
			# above and inserted first, so this insert trips the mobile_no unique index. Roll back the
			# failed insert and fall through to the existing-contact path against the row the winner
			# created, so this message still updates the contact and fires its realtime publish. (The
			# insert branch was the gap the 07-01 update-branch retry fix left open.)
			frappe.db.rollback()
			contact = frappe.db.get_value(
				"WhatsApp Contact",
				filters={"mobile_no": ["like", f"%{mobile_no}"]},
				fieldname=["name", "contact_name", "email"],
				order_by="modified desc",
				as_dict=True,
			)
			if not contact:
				# No winning row to fall back to (deleted meanwhile, or a non-race integrity error) —
				# re-raise so a genuine failure isn't silently swallowed as if it were the race.
				raise

	if contact:
		# Existing (or raced-to-existing) contact. Targeted UPDATE (no get_doc read) so a concurrent
		# committed write to this row can't raise 1020 (ER_CHECKREAD) on the read-modify-write. The
		# WhatsApp Contact update path has no controller lifecycle / doc_event hooks (only after_insert,
		# which runs on insert), so a blind set_value is behaviour-equivalent to a db_update()/save().
		values = {"last_message": message, "message_type": message_type}
		if not is_outgoing:
			values["is_read"] = 0
		current_contact_name = contact.contact_name or ""
		if current_contact_name[-10:] == mobile_no and profile_name:
			values["contact_name"] = profile_name
		_update_contact_with_retry(contact.name, values)
		room_name = contact.name
		room_email = contact.email

	# Push every message (incoming AND outgoing) so the open chat reflects
	# AI/other-agent/template replies live and the list preview/order updates.
	payload = {
		"content": message,
		"creation": frappe.utils.now(),
		"room": room_name,
		"type": message_type,
		"owner": owner,
		"message_id": message_id,
		"sender_user_no": None if is_outgoing else mobile_no,
	}

	# Per-room channel: any open ChatSpace (including managers viewing another
	# agent's chat) subscribes to `this.profile.room` and appends live.
	frappe.publish_realtime(room_name, payload)

	# List channel: updates the owner's chat-list preview / order / unread badge.
	if room_email:
		frappe.publish_realtime(
			"latest_chat_updates",
			payload,
			user=room_email,
		)
