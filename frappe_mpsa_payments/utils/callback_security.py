"""
Authentication for Safaricom callbacks.

1. Per-request HMAC tokens in callback URLs (primary). Only Safaricom sees the URL,
   so a forged callback cannot present a valid token for a given request.
2. Source-IP allowlist, ON by default (Safaricom's published callback IPs).
   site_config: mpesa_callback_ip_allowlist replaces the defaults,
   mpesa_callback_extra_ips adds to them, mpesa_callback_ip_check: 0 disables (emergency only).
Rejections go to the `mpesa_security` log file, not the Error Log, so they cannot
be used to flood the database.
"""
import hashlib
import hmac
import ipaddress
import logging

import frappe


def security_log(message: str) -> None:
    """Write to logs/mpesa_security.log. Frappe sets app loggers to ERROR in production,
    which would drop these, so the level is set explicitly."""
    logger = frappe.logger("mpesa_security")
    logger.setLevel(logging.INFO)
    logger.warning(message)


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
        or req.remote_addr
        or ""
    )


SAFARICOM_CALLBACK_IPS = (
    "196.201.214.200", "196.201.214.206", "196.201.213.114", "196.201.214.207",
    "196.201.214.208", "196.201.213.44", "196.201.212.127", "196.201.212.138",
    "196.201.212.129", "196.201.212.136", "196.201.212.74", "196.201.212.69",
)


def _allowlist() -> list:
    conf = frappe.local.conf
    base = conf.get("mpesa_callback_ip_allowlist") or SAFARICOM_CALLBACK_IPS
    return list(base) + list(conf.get("mpesa_callback_extra_ips") or [])


def guard_callback() -> None:
    if not frappe.local.conf.get("mpesa_callback_ip_check", 1):
        return
    ip = client_ip()
    try:
        addr = ipaddress.ip_address(ip)
        ok = any(addr in ipaddress.ip_network(c, strict=False) for c in _allowlist())
    except ValueError:
        ok = False
    if not ok:
        path = getattr(getattr(frappe.local, "request", None), "path", "")
        security_log(f"Rejected callback from {ip!r} to {path}")
        raise frappe.PermissionError("Not permitted")
