from unittest.mock import ANY, MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from whatsapp_chat.api.message import _update_contact_with_retry, update_contact_on_message


class TestUpdateContactOnMessage(FrappeTestCase):
    """FIX 4 (2026-07-01 triage): update_contact_on_message ran a get_doc -> save read-modify-write on
    WhatsApp Contact inside a background job, so on `innodb_snapshot_isolation=ON` a concurrent committed
    write raised 1020 (ER_CHECKREAD). It now does a targeted set_value (a blind UPDATE with no
    optimistic-lock read) behind a bounded retry. Fully mocked — no site data required."""

    def test_update_retries_then_succeeds(self):
        # One 1020 then success: set_value is retried, committed once, nothing logged.
        calls = {"n": 0}

        def flaky_set_value(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise frappe.QueryDeadlockError("1020 Record has changed in tabWhatsApp Contact")

        with (
            patch.object(frappe.db, "set_value", side_effect=flaky_set_value),
            patch.object(frappe.db, "commit") as mock_commit,
            patch.object(frappe.db, "rollback") as mock_rollback,
            patch("whatsapp_chat.api.message.time.sleep"),
            patch("frappe.log_error") as mock_log,
        ):
            _update_contact_with_retry("WA-CONTACT-1", {"last_message": "hi"})

        self.assertEqual(calls["n"], 2, "set_value retried once after the 1020")
        self.assertEqual(mock_rollback.call_count, 1)
        mock_commit.assert_called_once()
        mock_log.assert_not_called()

    def test_update_logs_and_returns_on_exhaustion(self):
        # Every attempt deadlocks: log once and return (best-effort UI state, the next message re-syncs);
        # never raise (it would just fail the background job).
        def always_deadlock(*args, **kwargs):
            raise frappe.QueryDeadlockError("1020")

        with (
            patch.object(frappe.db, "set_value", side_effect=always_deadlock),
            patch.object(frappe.db, "commit") as mock_commit,
            patch.object(frappe.db, "rollback"),
            patch("whatsapp_chat.api.message.time.sleep"),
            patch("frappe.log_error") as mock_log,
        ):
            # Must not raise.
            _update_contact_with_retry("WA-CONTACT-1", {"last_message": "hi"})

        mock_commit.assert_not_called()
        self.assertEqual(mock_log.call_count, 1, "logs exactly once on exhaustion")

    def test_existing_contact_uses_targeted_set_value_no_get_doc(self):
        # Structural change: an existing contact is updated via a blind set_value — NOT a get_doc + save
        # — so there is no read-modify-write to raise a 1020. Incoming clears is_read; profile_name that
        # matches the bare-number contact_name upgrades it; realtime uses the fetched name/email.
        contact = frappe._dict({"name": "WA-CONTACT-1", "contact_name": "9998887776", "email": "a@x.com"})
        set_value_calls = []

        with (
            patch.object(frappe.db, "get_value", return_value=contact),
            patch.object(
                frappe.db, "set_value", side_effect=lambda dt, name, vals: set_value_calls.append((name, vals))
            ),
            patch.object(frappe.db, "commit"),
            patch("frappe.get_doc") as mock_get_doc,
            patch("frappe.publish_realtime") as mock_publish,
            patch("frappe.utils.now", return_value="2026-07-01 00:00:00"),
        ):
            update_contact_on_message(
                mobile_no="9998887776",
                message="hi",
                message_type="Incoming",
                profile_name="Cust",
                is_outgoing=False,
                owner="agent@x.com",
                message_id="MSG-1",
            )

        mock_get_doc.assert_not_called()  # the RMW (get_doc -> save) that raised the 1020 is gone
        self.assertEqual(len(set_value_calls), 1)
        name, vals = set_value_calls[0]
        self.assertEqual(name, "WA-CONTACT-1")
        self.assertEqual(vals["last_message"], "hi")
        self.assertEqual(vals["message_type"], "Incoming")
        self.assertEqual(vals["is_read"], 0, "incoming clears is_read")
        self.assertEqual(vals["contact_name"], "Cust", "profile_name upgrades a bare-number contact_name")
        # Per-room publish + list-channel publish (email present) both fire with the fetched identifiers.
        mock_publish.assert_any_call("WA-CONTACT-1", ANY)
        mock_publish.assert_any_call("latest_chat_updates", ANY, user="a@x.com")

    def test_missing_contact_inserts_new(self):
        # No existing contact -> the insert path (get_doc(dict).insert()) still runs; no set_value.
        fake_new = MagicMock()
        fake_new.name = "WA-CONTACT-NEW"
        fake_new.email = None

        with (
            patch.object(frappe.db, "get_value", return_value=None),
            patch("frappe.get_doc", return_value=fake_new),
            patch.object(frappe.db, "set_value") as mock_set_value,
            patch("frappe.publish_realtime") as mock_publish,
            patch("frappe.utils.now", return_value="2026-07-01 00:00:00"),
        ):
            update_contact_on_message(
                mobile_no="9998887776",
                message="hi",
                message_type="Incoming",
                profile_name="Cust",
                is_outgoing=False,
            )

        fake_new.insert.assert_called_once()
        mock_set_value.assert_not_called()
        mock_publish.assert_any_call("WA-CONTACT-NEW", ANY)
