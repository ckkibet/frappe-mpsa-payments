"""Custom number cards for the MPESA dashboard."""
import frappe


@frappe.whitelist()
def unreconciled_b2c(filters=None):
    """Paid B2C requests with no submitted accounting entry: Payment Entry carrying
    the M-Pesa receipt, or (salary slips) the B2C Journal Entry for that slip."""
    frappe.has_permission("B2C Disbursement Request", "read", throw=True)
    amount, count = frappe.db.sql(
        """
        select coalesce(sum(r.amount), 0), count(*)
        from `tabB2C Disbursement Request` r
        where r.docstatus = 1 and r.status = 'Paid'
          and not exists (
              select 1 from `tabPayment Entry` pe
              where pe.docstatus = 1 and ifnull(r.transaction_id, '') != ''
                and pe.reference_no = r.transaction_id)
          and not (
              r.reference_doctype = 'Salary Slip' and exists (
                  select 1 from `tabJournal Entry` je
                  where je.docstatus = 1
                    and je.user_remark = concat('B2C Payment for Salary Slip ', r.reference_name)))
        """
    )[0]
    return {"value": amount, "fieldtype": "Currency", "route": ["List", "B2C Disbursement Request"],
            "route_options": {"status": "Paid"}}


@frappe.whitelist()
def unreconciled_c2b(filters=None):
    """C2B payments (not cancelled) with no submitted Payment Entry, Sales Invoice or POS Invoice."""
    frappe.has_permission("Mpesa C2B Payment Register", "read", throw=True)
    amount, count = frappe.db.sql(
        """
        select coalesce(sum(c.transamount), 0), count(*)
        from `tabMpesa C2B Payment Register` c
        where c.docstatus < 2
          and not exists (select 1 from `tabPayment Entry` pe where pe.name = c.payment_entry and pe.docstatus = 1)
          and not exists (select 1 from `tabSales Invoice` si where si.name = c.sales_invoice and si.docstatus = 1)
          and not exists (select 1 from `tabPOS Invoice` pi where pi.name = c.pos_invoice and pi.docstatus = 1)
        """
    )[0]
    return {"value": amount, "fieldtype": "Currency", "route": ["List", "Mpesa C2B Payment Register"]}
