"""Common HMAC webhook verification helper.

A gateway-specific adapter should provide the correct canonical signing rules and
secret. This helper is deliberately generic and is not a substitute for a gateway's
official webhook verification requirements.
"""
from __future__ import annotations
import hashlib, hmac

def verify_hmac_sha256(raw_body: bytes, signature: str, secret: str) -> bool:
    if not signature or not secret:
        return False
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
