"""Stripe Billing integration (REST, no SDK dependency).

Implements what Stripe documents for subscriptions with Checkout:

* ``POST /v1/checkout/sessions`` with ``mode=subscription`` and a recurring Price
  (configure a $20/month price in the Stripe dashboard and pass its id);
* webhook verification of the ``Stripe-Signature`` header: HMAC-SHA256 of
  ``"{t}.{raw_body}"`` with the endpoint's signing secret, compared against every
  ``v1`` signature, with a timestamp tolerance (default 300 s) against replay;
* events: ``checkout.session.completed``, ``customer.subscription.created|updated|deleted``,
  ``invoice.paid``, ``invoice.payment_failed``.

Nothing here runs unless the license server is configured with real Stripe secrets.
Consider a merchant-of-record setup (Stripe Managed Payments, Paddle) for VAT/sales tax.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Mapping
from typing import Any

import httpx

from scholarforge.errors import PaymentError
from scholarforge.licensing.providers.base import SubscriptionEvent

API = "https://api.stripe.com/v1"


def verify_stripe_signature(header: str, body: bytes, secret: str, tolerance_s: int = 300,
                            now: float | None = None) -> None:
    items = [p.split("=", 1) for p in header.split(",") if "=" in p]
    timestamps = [v for k, v in items if k == "t"]
    sigs = [v for k, v in items if k == "v1"]
    if not timestamps or not sigs:
        raise PaymentError("malformed Stripe-Signature header")
    try:
        t = int(timestamps[0])
    except ValueError as exc:
        raise PaymentError("malformed Stripe-Signature timestamp") from exc
    signed = f"{t}.".encode() + body
    expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, s) for s in sigs):
        raise PaymentError("Stripe signature verification failed")
    if abs((now or time.time()) - t) > tolerance_s:
        raise PaymentError("Stripe webhook timestamp outside the tolerance window (possible replay)")


def _period_end(sub: dict[str, Any]) -> int | None:
    if sub.get("current_period_end"):
        return int(sub["current_period_end"])
    items = ((sub.get("items") or {}).get("data")) or []
    ends = [int(i["current_period_end"]) for i in items if i.get("current_period_end")]
    return max(ends) if ends else None


class StripeProvider:
    name = "stripe"

    def __init__(self, secret_key: str, webhook_secret: str, price_id: str, *,
                 transport: httpx.BaseTransport | None = None, tolerance_s: int = 300):
        if not (secret_key and webhook_secret and price_id):
            raise PaymentError("Stripe is not configured (secret key, webhook secret and price id are required)")
        self._client = httpx.Client(base_url=API, timeout=30, transport=transport,
                                    headers={"Authorization": f"Bearer {secret_key}"})
        self.webhook_secret = webhook_secret
        self.price_id = price_id
        self.tolerance = tolerance_s

    def create_checkout(self, *, success_url: str, cancel_url: str, client_reference: str,
                        customer_email: str | None = None) -> dict[str, Any]:
        form = {"mode": "subscription", "line_items[0][price]": self.price_id, "line_items[0][quantity]": "1",
                "success_url": success_url, "cancel_url": cancel_url, "client_reference_id": client_reference,
                "allow_promotion_codes": "true"}
        if customer_email:
            form["customer_email"] = customer_email
        resp = self._client.post("/checkout/sessions", data=form)
        if resp.status_code >= 400:
            raise PaymentError(f"Stripe checkout creation failed ({resp.status_code})")
        data = resp.json()
        return {"id": data["id"], "url": data["url"]}

    def cancel_subscription(self, subscription_ref: str) -> None:
        resp = self._client.post(f"/subscriptions/{subscription_ref}", data={"cancel_at_period_end": "true"})
        if resp.status_code >= 400:
            raise PaymentError(f"Stripe cancellation failed ({resp.status_code})")

    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> SubscriptionEvent:
        header = next((v for k, v in headers.items() if k.lower() == "stripe-signature"), None)
        if not header:
            raise PaymentError("missing Stripe-Signature header")
        verify_stripe_signature(header, body, self.webhook_secret, self.tolerance)
        evt = json.loads(body)
        obj = (evt.get("data") or {}).get("object") or {}
        etype = evt.get("type", "")
        base = {"provider": self.name, "event_id": evt["id"], "raw_type": etype}
        if etype == "checkout.session.completed":
            return SubscriptionEvent(**base, type="checkout.completed", customer_ref=obj.get("customer"),
                                     subscription_ref=obj.get("subscription"), checkout_ref=obj.get("id"),
                                     client_reference=obj.get("client_reference_id"),
                                     email=(obj.get("customer_details") or {}).get("email") or obj.get("customer_email"))
        if etype in ("customer.subscription.created", "customer.subscription.updated"):
            status = obj.get("status")
            mapped = {"active": "subscription.active", "trialing": "subscription.active",
                      "past_due": "subscription.past_due", "unpaid": "subscription.past_due",
                      "canceled": "subscription.ended", "incomplete_expired": "subscription.ended"}.get(status, "ignored")
            if mapped == "subscription.active" and obj.get("cancel_at_period_end"):
                mapped = "subscription.canceled"
            return SubscriptionEvent(**base, type=mapped, customer_ref=obj.get("customer"),
                                     subscription_ref=obj.get("id"), current_period_end=_period_end(obj),
                                     cancel_at_period_end=bool(obj.get("cancel_at_period_end")))
        if etype == "customer.subscription.deleted":
            return SubscriptionEvent(**base, type="subscription.ended", customer_ref=obj.get("customer"),
                                     subscription_ref=obj.get("id"), current_period_end=_period_end(obj))
        if etype == "invoice.paid":
            lines = (obj.get("lines") or {}).get("data") or []
            end = max((int((ln.get("period") or {}).get("end", 0)) for ln in lines), default=0) or None
            sub = obj.get("subscription") or ((obj.get("parent") or {}).get("subscription_details") or {}).get("subscription")
            return SubscriptionEvent(**base, type="subscription.active", customer_ref=obj.get("customer"),
                                     subscription_ref=sub, current_period_end=end)
        if etype == "invoice.payment_failed":
            sub = obj.get("subscription") or ((obj.get("parent") or {}).get("subscription_details") or {}).get("subscription")
            return SubscriptionEvent(**base, type="subscription.past_due", customer_ref=obj.get("customer"),
                                     subscription_ref=sub)
        return SubscriptionEvent(**base, type="ignored")
