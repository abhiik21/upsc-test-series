"""Provider-neutral payment interface.

Production gateways should implement this interface. The current prototype keeps
payment records in SQLite and does not make external gateway calls.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol

@dataclass(frozen=True)
class PaymentOrder:
    provider: str
    order_id: str
    amount_paise: int
    currency: str = "INR"

class PaymentProvider(Protocol):
    name: str
    def create_order(self, amount_paise: int, receipt: str, notes: dict[str, str] | None = None) -> PaymentOrder: ...
    def verify_webhook(self, raw_body: bytes, signature: str) -> bool: ...
    def request_refund(self, payment_reference: str, amount_paise: int | None = None) -> str: ...

class DevelopmentPaymentProvider:
    name = "development"

    def create_order(self, amount_paise: int, receipt: str, notes: dict[str, str] | None = None) -> PaymentOrder:
        raise RuntimeError("Development payment provider does not create real orders. Configure a production gateway before enabling checkout.")

    def verify_webhook(self, raw_body: bytes, signature: str) -> bool:
        return False

    def request_refund(self, payment_reference: str, amount_paise: int | None = None) -> str:
        raise RuntimeError("Development payment provider does not issue real refunds.")
