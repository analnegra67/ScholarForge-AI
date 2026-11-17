from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, Protocol

from pydantic import BaseModel

EventType = Literal["checkout.completed", "subscription.active", "subscription.past_due",
                    "subscription.canceled", "subscription.ended", "ignored"]


class SubscriptionEvent(BaseModel):
    provider: str
    event_id: str
    type: EventType
    raw_type: str
    customer_ref: str | None = None
    subscription_ref: str | None = None
    checkout_ref: str | None = None
    client_reference: str | None = None
    email: str | None = None  # hashed before storage by the server
    current_period_end: int | None = None
    cancel_at_period_end: bool = False


class PaymentProvider(Protocol):
    name: str

    def create_checkout(self, *, success_url: str, cancel_url: str, client_reference: str,
                        customer_email: str | None = None) -> dict[str, Any]: ...

    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> SubscriptionEvent: ...

    def cancel_subscription(self, subscription_ref: str) -> None: ...
