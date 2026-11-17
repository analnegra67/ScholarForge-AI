"""Payment providers behind one interface. The scientific engine knows nothing about them."""

from scholarforge.licensing.providers.base import PaymentProvider, SubscriptionEvent

__all__ = ["PaymentProvider", "SubscriptionEvent"]
