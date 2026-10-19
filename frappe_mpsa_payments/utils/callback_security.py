"""
Authentication for Safaricom callbacks.

1. Per-request HMAC tokens in callback URLs (primary). Only Safaricom sees the URL,
   so a forged callback cannot present a valid token for a given request.
2. Optional source-IP allowlist: site_config `mpesa_callback_ip_allowlist` (list of
   CIDRs). Disabled when unset, so sandbox/local testing keeps working.
Rejections go to the `mpesa_security` log file, not the Error Log, so they cannot
be used to flood the database.
"""
import hashlib
import hmac
import ipaddress

import frappe


def _key() -> bytes:
    key = frappe.local.conf.get("encryption_key")
    if not key:
        frappe.throw("encryption_key missing from site_config")
    return key.encode()


def callback_token(value: str, purpose: str) -> str:
    return hmac.new(_key(), f"{purpose}:{value}".encode(), hashlib.sha256).hexdigest()[:40]


def verify_token(value, token, purpose: str) -> bool:
    if not value or not token:
        return False
    return hmac.compare_digest(callback_token(str(value), purpose), str(token))


def client_ip() -> str:
    req = getattr(frappe.local, "request", None)
    if not req:
        return ""
    h = req.headers
    return (
        h.get("CF-Connecting-IP")
        or (h.get("X-Forwarded-For") or "").split(",")[0].strip()
        or req.remote_addr
        or ""
    )


def guard_callback() -> None:
    allow = frappe.local.conf.get("mpesa_callback_ip_allowlist")
    if not allow:
        return
    ip = client_ip()
    try:
        addr = ipaddress.ip_address(ip)
        ok = any(addr in ipaddress.ip_network(c, strict=False) for c in allow)
    except ValueError:
        ok = False
    if not ok:
        path = getattr(getattr(frappe.local, "request", None), "path", "")
        frappe.logger("mpesa_security").warning(f"Rejected callback from {ip!r} to {path}")
        raise frappe.PermissionError("Not permitted")
