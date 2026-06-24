"""Carry the chat-list window from the legacy leanerp Single to whatsapp_chat's own.

`chat_list_window_days` used to live on `Leanerp WhatsApp Settings` (leanerp_whatsapp).
It now lives on `WhatsApp Chat Settings` so whatsapp_chat needs only frappe_whatsapp.
Copy the configured value across so an already-tuned value isn't reset to the default
on upgrade. No-op on a leanerp-free site (the row simply won't exist).

Read straight from `tabSingles` rather than `get_single_value`: the field has been
removed from the Leanerp WhatsApp Settings doctype, and model-sync strips it from the
meta *before* this post-model-sync patch runs — so `get_single_value` would raise
"field does not exist". The raw single value survives that removal and stays readable.
"""

import frappe
from frappe.utils import cint


def execute():
    row = frappe.db.sql(
        """SELECT value FROM tabSingles WHERE doctype = %s AND field = %s""",
        ("Leanerp WhatsApp Settings", "chat_list_window_days"),
    )
    old_value = cint(row[0][0]) if row and row[0][0] is not None else 0
    if old_value > 0:
        frappe.db.set_single_value("WhatsApp Chat Settings", "chat_list_window_days", old_value)
