"""Deterministic payment provider for development and tests (no money, no network).

Webhooks are signed exactly like a real provider would (HMAC-SHA256 over
``timestamp.body``), so the server's verification path is exercised for real.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from collections.abc import Mapping
from typing import Any

from scholarforge.errors import PaymentError
from scholarforge.licensing.providers.base import SubscriptionEvent

HEADER = "X-SF-Test-Signature"


class TestPaymentProvider:
    __test__ = False  # not a pytest test class
    name = "test"

    def __init__(self, webhook_secret: str, tolerance_s: int = 300):
        self.secret = webhook_secret.encode()
        self.tolerance = tolerance_s
        self.checkouts: dict[str, dict[str, Any]] = {}
        self.canceled: list[str] = []

    def create_checkout(self, *, success_url: str, cancel_url: str, client_reference: str,
                        customer_email: str | None = None) -> dict[str, Any]:
        sid = f"cs_test_{uuid.uuid4().hex[:16]}"
        self.checkouts[sid] = {"client_reference": client_reference, "email": customer_email}
        return {"id": sid, "url": success_url.replace("{CHECKOUT_SESSION_ID}", sid)}

    def sign(self, body: bytes, timestamp: int | None = None) -> str:
        t = int(timestamp or time.time())
        sig = hmac.new(self.secret, f"{t}.".encode() + body, hashlib.sha256).hexdigest()
        return f"t={t},v1={sig}"

    def make_event(self, event_type: str, **fields: Any) -> tuple[dict[str, str], bytes]:
        payload = {"id": f"evt_test_{uuid.uuid4().hex[:16]}", "type": event_type, "data": fields,
                   "created": int(time.time())}
        body = json.dumps(payload).encode()
        return {HEADER: self.sign(body)}, body

    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> SubscriptionEvent:
        header = next((v for k, v in headers.items() if k.lower() == HEADER.lower()), None)
        if not header:
            raise PaymentError("missing webhook signature")
        parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
        try:
            t = int(parts["t"])
        except (KeyError, ValueError) as exc:
            raise PaymentError("malformed webhook signature") from exc
        expected = hmac.new(self.secret, f"{t}.".encode() + body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, parts.get("v1", "")):
            raise PaymentError("invalid webhook signature")
        if abs(time.time() - t) > self.tolerance:
            raise PaymentError("webhook timestamp outside the tolerance window")
        evt = json.loads(body)
        d = evt.get("data", {})
        mapping = {"checkout.completed": "checkout.completed", "subscription.renewed": "subscription.active",
                   "subscription.active": "subscription.active", "payment.failed": "subscription.past_due",
                   "subscription.canceled": "subscription.canceled", "subscription.ended": "subscription.ended"}
        return SubscriptionEvent(
            provider=self.name, event_id=evt["id"], type=mapping.get(evt["type"], "ignored"), raw_type=evt["type"],
            customer_ref=d.get("customer"), subscription_ref=d.get("subscription"), checkout_ref=d.get("checkout"),
            client_reference=d.get("client_reference"), email=d.get("email"),
            current_period_end=d.get("current_period_end"), cancel_at_period_end=bool(d.get("cancel_at_period_end")),
        )

    def cancel_subscription(self, subscription_ref: str) -> None:
        self.canceled.append(subscription_ref)
