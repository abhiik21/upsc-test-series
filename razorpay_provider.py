"""Razorpay adapter: Orders API, Checkout signature check, webhook check and refunds.

Only the standard library is used (no extra dependency). Reference: https://razorpay.com/docs/api/
  * Create order   POST /v1/orders            (HTTP Basic auth: key id + key secret; amount in paise)
  * Checkout       returns razorpay_payment_id, razorpay_order_id, razorpay_signature
                   signature = HMAC-SHA256(order_id + "|" + payment_id, key_secret)
  * Webhook        header X-Razorpay-Signature = HMAC-SHA256(raw request body, webhook secret)
  * Refund         POST /v1/payments/{payment_id}/refund
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

from .provider import PaymentOrder
from .webhook_security import verify_hmac_sha256

API_BASE = "https://api.razorpay.com/v1"


class PaymentGatewayError(RuntimeError):
    """The gateway refused a request or could not be reached."""


@dataclass(frozen=True)
class RazorpayConfig:
    key_id: str
    key_secret: str
    webhook_secret: str

    @classmethod
    def from_env(cls) -> "RazorpayConfig":
        return cls(
            key_id=os.getenv("RAZORPAY_KEY_ID", "").strip(),
            key_secret=os.getenv("RAZORPAY_KEY_SECRET", "").strip(),
            webhook_secret=os.getenv("RAZORPAY_WEBHOOK_SECRET", "").strip(),
        )


class RazorpayProvider:
    name = "razorpay"

    def __init__(self, config: RazorpayConfig, timeout: float = 15.0):
        self.config = config
        self.timeout = timeout

    @property
    def key_id(self) -> str:
        return self.config.key_id

    # -- HTTP ---------------------------------------------------------------------------------
    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        token = base64.b64encode(f"{self.config.key_id}:{self.config.key_secret}".encode()).decode()
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(API_BASE + path, data=data, method=method, headers={
            "Authorization": f"Basic {token}", "Content-Type": "application/json", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = (json.loads(exc.read().decode()).get("error") or {}).get("description", "")
            except Exception:
                pass
            raise PaymentGatewayError(f"Razorpay rejected the request ({exc.code}): {detail or exc.reason}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise PaymentGatewayError(f"Could not reach Razorpay: {exc}") from exc

    # -- operations ---------------------------------------------------------------------------
    def create_order(self, amount_paise: int, receipt: str, notes: dict[str, str] | None = None) -> PaymentOrder:
        body = {"amount": int(amount_paise), "currency": "INR", "receipt": receipt[:40], "notes": notes or {}}
        data = self._request("POST", "/orders", body)
        if not data.get("id"):
            raise PaymentGatewayError("Razorpay did not return an order id")
        return PaymentOrder(provider=self.name, order_id=data["id"], amount_paise=int(data.get("amount", amount_paise)), currency=data.get("currency", "INR"))

    def verify_payment_signature(self, order_id: str, payment_id: str, signature: str) -> bool:
        """The check Razorpay documents for the Checkout success callback."""
        if not (order_id and payment_id and signature and self.config.key_secret):
            return False
        expected = hmac.new(self.config.key_secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    def verify_webhook(self, raw_body: bytes, signature: str) -> bool:
        return verify_hmac_sha256(raw_body, signature, self.config.webhook_secret)

    def request_refund(self, payment_reference: str, amount_paise: int | None = None) -> str:
        body = {"amount": int(amount_paise)} if amount_paise else {}
        data = self._request("POST", f"/payments/{payment_reference}/refund", body)
        return str(data.get("id", ""))
