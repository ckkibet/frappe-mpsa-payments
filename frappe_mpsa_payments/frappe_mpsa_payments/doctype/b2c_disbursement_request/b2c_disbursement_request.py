# Copyright (c) 2024, Navari Limited and contributors
# For license information, please see license.txt

import uuid

import frappe
from frappe import _
from frappe.utils import nowdate
from frappe.model.document import Document

from ...api.b2c import make_b2c_payment_request


class B2CDisbursementRequest(Document):
    """B2C Payments Transactions"""

    def before_insert(self) -> None:
        """Insert Missing Values"""
        self.originator_conversation_id = self._generate_uuid_v4()
        self._set_payment_details()

    def validate(self) -> None:
        """B2C Payments Transactions validations"""
        if not self.originator_conversation_id:
            self.originator_conversation_id = self._generate_uuid_v4()

        if self.stanbic_settings and not self.dbs_reference_id:
            self.dbs_reference_id = self._generate_uuid_v4()

    def on_submit(self):
        make_b2c_payment_request(self.name)

    def _generate_uuid_v4(self) -> str:
        return str(uuid.uuid4())

    # What the payment is for, as Safaricom records it (max 100 chars each)
    REMARK_LABELS = {
        "Expense Claim": "Expense claim",
        "Employee Advance": "Employee advance",
        "Salary Slip": "Salary",
        "Purchase Invoice": "Supplier invoice",
        "Purchase Order": "Purchase order",
        "Loan": "Loan disbursement",
    }

    def _set_payment_details(self) -> None:
        """Derive Remarks, Occasion and CommandID from the document being paid,
        instead of the DocType defaults ('here are my remarks', SalaryPayment)."""
        label = self.REMARK_LABELS.get(self.reference_doctype, self.reference_doctype or "Payment")
        self.remarks = f"{label} {self.reference_name or ''}".strip()[:100]
        self.occassion = (self.b2c_payment_disbursement or "")[:100]
        self.commandid = (
            "SalaryPayment" if self.reference_doctype == "Salary Slip" else "BusinessPayment"
        )

    @frappe.whitelist()
    def retry_failed_payment(self) -> str:
        """
        Retry a failed request by creating a NEW linked request (fresh
        OriginatorConversationID). The original becomes 'Retried'.
        One level only: a retry cannot itself be retried.
        """
        self.check_permission("submit")
        status, retried_by = frappe.db.get_value(
            self.doctype, self.name, ["status", "retried_by"], for_update=True
        )
        if status != "Failed":
            frappe.throw(_("Only failed requests can be retried ({0} is {1}).").format(self.name, status))
        if self.retry_of:
            frappe.throw(_("{0} is a retry of {1} and cannot be retried again. Create a new disbursement instead.").format(self.name, self.retry_of))
        if retried_by:
            frappe.throw(_("{0} was already retried as {1}.").format(self.name, retried_by))

        new = frappe.copy_doc(self)
        new.docstatus = 0
        new.amended_from = None
        new.retry_of = self.name
        new.retried_by = None
        new.originator_conversation_id = None  # regenerated in before_insert
        new.posting_date = nowdate()
        new.status = "Not Initiated"
        for f in RESULT_FIELDS:
            new.set(f, None)
        new.insert(ignore_permissions=True)

        self.db_set({"status": "Retried", "retried_by": new.name}, notify=True)

        if self.b2c_payment_disbursement_reference:
            frappe.db.set_value(
                "B2C Payment Disbursement Reference",
                self.b2c_payment_disbursement_reference,
                {"b2c_disbursement_request": new.name, "payment_status": _pending_ref_status()},
            )
        if self.b2c_payment_disbursement:
            # let the status scheduler watch the disbursement again
            frappe.db.set_value("B2C Payment Disbursement", self.b2c_payment_disbursement, "retry_count", 0)

        new.submit()  # on_submit dispatches to the provider
        return new.name


RESULT_FIELDS = (
    "transaction_id", "receiver_public_name", "charges_paid_acct_avlbl_funds",
    "utility_acct_avlbl_funds", "recipient_is_registered_customer", "working_acct_avlbl_funds",
    "transaction_completed_datetime", "result_type", "result_code", "response_description",
    "conversation_id", "response_code", "error_code", "error_message", "result_desc", "request_id",
)


def _pending_ref_status() -> str:
    opts = (frappe.get_meta("B2C Payment Disbursement Reference").get_field("payment_status").options or "").split("\n")
    for o in ("Initiated", "Not Initiated", "Pending"):
        if o in opts:
            return o
    return ""
