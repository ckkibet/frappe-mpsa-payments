"""
Backstop for lost B2C callbacks: ask Daraja for the outcome of requests stuck in Initiated.
Never marks a request Failed on its own - unclear outcomes stay Initiated for manual review.
"""
import json
from datetime import datetime

import frappe
import requests
from frappe.utils import add_to_date, get_url, now_datetime

from frappe_mpsa_payments.connectors.mpesa.b2c_connector import MpesaB2CConnector
from frappe_mpsa_payments.utils.doctype_names import B2C_REQUEST_DOCTYPE

QUERY_AFTER_MINUTES = 10
MAX_QUERIES = 6
RESULT_METHOD = "frappe_mpsa_payments.frappe_mpsa_payments.api.b2c.b2c_status_result_callback"


def query_stale_b2c_requests():
    """Scheduler entry point."""
    cutoff = add_to_date(now_datetime(), minutes=-QUERY_AFTER_MINUTES)
    names = frappe.get_all(
        B2C_REQUEST_DOCTYPE,
        filters={
            "docstatus": 1,
            "status": "Initiated",
            "mpesa_settings": ["is", "set"],
            "modified": ["<", cutoff],  # each query bumps modified, spacing queries out
            "status_query_count": ["<", MAX_QUERIES],
        },
        pluck="name",
    )
    for name in names:
        try:
            send_status_query(name)
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"B2C status query failed: {name}")
        frappe.db.commit()


def send_status_query(name: str) -> dict:
    req = frappe.get_doc(B2C_REQUEST_DOCTYPE, name)
    c = MpesaB2CConnector(req.mpesa_settings)
    token = c._get_valid_token()
    url = f"{get_url()}/api/method/{RESULT_METHOD}"
    payload = {
        "Initiator": c.initiator_name,
        "SecurityCredential": c.security_credential,
        "CommandID": "TransactionStatusQuery",
        "OriginalConversationID": req.originator_conversation_id,
        "PartyA": c.business_shortcode,
        "IdentifierType": "4",
        "ResultURL": url,
        "QueueTimeOutURL": url,
        "Remarks": f"Status check {req.name}"[:100],
        "Occasion": "",
    }
    if req.transaction_id:
        payload["TransactionID"] = req.transaction_id

    r = requests.post(
        f"{c._base_url}/mpesa/transactionstatus/v1/query",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    try:
        data = r.json()
    except ValueError:
        data = {"raw": r.text}

    count = (req.status_query_count or 0) + 1
    req.db_set(
        {
            "status_query_count": count,
            "last_status_query": now_datetime(),
            "status_query_id": data.get("ConversationID") or "",
        }
    )
    if str(data.get("ResponseCode")) != "0":
        frappe.log_error(
            json.dumps({"request": name, "http": r.status_code, "response": data}, default=str),
            f"B2C status query rejected: {name}",
        )
    if count >= MAX_QUERIES:
        frappe.log_error(
            f"{name} is still Initiated after {count} status queries. Reconcile manually against the M-Pesa statement.",
            f"B2C needs manual reconciliation: {name}",
        )
    return data


def handle_status_result(result: dict) -> None:
    result = frappe._dict(result)
    conv = result.get("ConversationID")
    name = conv and frappe.db.get_value(B2C_REQUEST_DOCTYPE, {"status_query_id": conv}, "name")
    if not name:
        frappe.log_error(frappe.as_json(result), "B2C status result: unknown ConversationID")
        return

    req = frappe.get_doc(B2C_REQUEST_DOCTYPE, name)
    if req.status != "Initiated":
        return  # already settled (normal callback won the race)

    if str(result.get("ResultCode")) != "0":
        req.add_comment("Comment", f"Status query did not resolve: {result.get('ResultDesc')}")
        return

    p = {x.get("Key"): x.get("Value") for x in (result.get("ResultParameters") or {}).get("ResultParameter", [])}
    status = str(p.get("TransactionStatus") or "")

    if status != "Completed":
        req.add_comment("Comment", f"Status query returned TransactionStatus={status or 'blank'}; left Initiated")
        return

    amount = p.get("Amount")
    if amount is None or abs(float(amount) - float(req.amount)) > 0.01:
        frappe.log_error(frappe.as_json(result), f"B2C status result amount mismatch: {name}")
        return

    req.status = "Paid"
    req.transaction_id = p.get("ReceiptNo") or req.transaction_id
    req.result_desc = "Confirmed by Transaction Status query"
    req.error_message = ""
    finalised = str(p.get("FinalisedTime") or "")
    try:
        req.transaction_completed_datetime = datetime.strptime(finalised, "%Y%m%d%H%M%S")
    except ValueError:
        pass
    req.save(ignore_permissions=True)

    frappe.enqueue(
        "frappe_mpsa_payments.services.b2c_response_service.update_b2c_reference_status",
        queue="short",
        timeout=300,
        b2c_request_name=req.name,
        enqueue_next=True,
    )
