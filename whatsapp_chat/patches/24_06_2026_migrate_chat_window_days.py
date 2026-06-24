"""Carry the chat-list window from the legacy leanerp Single to whatsapp_chat's own.

`chat_list_window_days` used to live on `Leanerp WhatsApp Settings` (leanerp_whatsapp).
It now lives on `WhatsApp Chat Settings` so whatsapp_chat needs only frappe_whatsapp.
Copy the configured value across if the old Single exists, so an already-tuned
value (e.g. 100) isn't reset to the default on upgrade. No-op on a leanerp-free site.
"""

import frappe
from frappe.utils import cint


def execute():
    if not frappe.db.exists("DocType", "Leanerp WhatsApp Settings"):
        return

    try:
        old_value = cint(
            frappe.db.get_single_value("Leanerp WhatsApp Settings", "chat_list_window_days")
        )
    except Exception:
        return

    if old_value > 0:
        frappe.db.set_single_value("WhatsApp Chat Settings", "chat_list_window_days", old_value)
