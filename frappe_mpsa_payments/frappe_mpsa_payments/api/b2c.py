import frappe

from frappe_mpsa_payments.services.b2c_request_service import create_b2c_request
from frappe_mpsa_payments.services.b2c_response_service import process_b2c_result


@frappe.whitelist()
def make_b2c_payment_request(doc_name: str) -> dict:
    """
    Queues and initiates a B2C payment request by creating the
    B2C Request document and delegating to the appropriate connector.
    """
    return create_b2c_request(doc_name)


@frappe.whitelist(allow_guest=True)
def b2c_results_callback(**kwargs):
    """
    Handle the B2C callback from Daraja.
    """

    from frappe_mpsa_payments.utils.callback_security import guard_callback, security_log, verify_token
    guard_callback()
    _occ = (kwargs.get("Result") or {}).get("OriginatorConversationID")
    if not verify_token(_occ, kwargs.get("t"), "b2c"):
        security_log(f"Rejected B2C callback: bad/missing token for {_occ!r}")
        return "OK"
    try:
        result_payload = kwargs.get("Result") or {}
        process_b2c_result(None, result_payload)
        return "OK"

    except Exception:
        frappe.log_error(frappe.get_traceback(), "B2C Callback Error")
        raise


@frappe.whitelist(allow_guest=True)
def b2c_status_result_callback(**kwargs):
    """Result of a TransactionStatusQuery sent by the stale-request job."""
    from frappe_mpsa_payments.utils.callback_security import guard_callback, security_log, verify_token
    guard_callback()
    if not verify_token(kwargs.get("r"), kwargs.get("t"), "b2c-status"):
        security_log(f"Rejected B2C status callback: bad/missing token for {kwargs.get('r')!r}")
        return "OK"
    from frappe_mpsa_payments.services.b2c_status_query_service import handle_status_result

    try:
        handle_status_result(kwargs.get("Result") or {}, expected_name=kwargs.get("r"))
    except Exception:
        frappe.log_error(frappe.get_traceback(), "B2C Status Result Error")
    return "OK"
