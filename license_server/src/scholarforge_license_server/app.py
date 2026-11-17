"""HTTP API (Starlette).

POST /v1/checkout                 -> {url}            start a subscription checkout
GET  /v1/checkout/result?session= -> {license_key}    shown once on the success page
POST /v1/webhooks/{provider}                          payment provider events (signature-verified, idempotent)
POST /v1/licenses/activate        -> {token}          {license_key, device_id, app_version}
POST /v1/licenses/refresh         -> {token}
POST /v1/licenses/deactivate      -> {ok}
GET  /healthz
"""

from __future__ import annotations

import logging
import time
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from scholarforge.errors import PaymentError
from scholarforge.licensing.providers.test_provider import TestPaymentProvider
from scholarforge.licensing.tokens import load_private_key, sign_token
from scholarforge.logging_setup import redact

from scholarforge_license_server.config import ServerConfig
from scholarforge_license_server.db import LicenseDB

log = logging.getLogger("scholarforge.license_server")

ACTIVE = ("active", "canceled_pending")  # canceled_pending: canceled but paid until period end


def _json_error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def create_app(config: ServerConfig, provider=None) -> Starlette:
    db = LicenseDB(config.db_path, config.pepper)
    signer = load_private_key(config.signing_key_pem)
    if provider is None:
        if config.provider == "stripe":
            from scholarforge.licensing.providers.stripe import StripeProvider

            provider = StripeProvider(config.stripe_secret_key or "", config.stripe_webhook_secret or "",
                                      config.stripe_price_id or "")
        else:
            provider = TestPaymentProvider(config.test_webhook_secret)

    def token_for(lic, sub, device_id: str) -> str:
        now = int(time.time())
        status = sub["status"] if sub else "none"
        end = int(sub["current_period_end"] or now) if sub else now
        exp = end + config.token_buffer_days * 86400
        claims = {"v": 1, "lid": lic["id"], "plan": "pro", "status": "canceled" if sub and sub["cancel_at_period_end"]
                  else status, "features": ["*"], "iat": now, "exp": exp,
                  "grace_until": exp + config.offline_grace_days * 86400, "dev": device_id}
        return sign_token(signer, claims)

    def entitled(sub) -> bool:
        return bool(sub) and sub["status"] in ACTIVE and int(sub["current_period_end"] or 0) > time.time()

    async def body_json(request: Request) -> dict[str, Any]:
        try:
            data = await request.json()
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    async def checkout(request: Request) -> JSONResponse:
        data = await body_json(request)
        ref = "ref_" + str(int(time.time() * 1000))
        try:
            session = provider.create_checkout(
                success_url=f"{config.public_url}/v1/checkout/result?session={{CHECKOUT_SESSION_ID}}",
                cancel_url=f"{config.public_url}/canceled", client_reference=ref,
                customer_email=data.get("email"))
        except PaymentError as exc:
            return _json_error(502, exc.message)
        return JSONResponse({"url": session["url"], "session": session["id"]})

    async def checkout_result(request: Request) -> JSONResponse:
        session = request.query_params.get("session", "")
        key = db.take_pending_key(session) if session else None
        if not key:
            return _json_error(404, "no license key available for this session (already shown or unknown)")
        return JSONResponse({"license_key": key, "note": "store this key safely; it is shown only once"})

    async def webhook(request: Request) -> JSONResponse:
        name = request.path_params["provider"]
        if name != provider.name:
            return _json_error(404, "unknown provider")
        body = await request.body()
        try:
            event = provider.parse_webhook(dict(request.headers), body)
        except PaymentError as exc:
            log.warning("rejected webhook: %s", exc.message)
            return _json_error(400, exc.message)
        if db.seen_event(event.event_id, event.provider, event.raw_type):
            return JSONResponse({"ok": True, "duplicate": True})
        if event.type == "ignored":
            return JSONResponse({"ok": True, "ignored": event.raw_type})
        customer = db.upsert_customer(provider.name, event.customer_ref, event.email)
        if event.type == "checkout.completed":
            db.upsert_subscription(customer, event.subscription_ref, "active",
                                   event.current_period_end or int(time.time()) + 31 * 86400)
            lic_id, _ = db.issue_license(customer, event.checkout_ref)
            log.info("checkout completed: license %s issued", lic_id)
        elif event.type == "subscription.active":
            db.upsert_subscription(customer, event.subscription_ref, "active", event.current_period_end)
        elif event.type == "subscription.past_due":
            db.upsert_subscription(customer, event.subscription_ref, "past_due", event.current_period_end)
        elif event.type == "subscription.canceled":
            db.upsert_subscription(customer, event.subscription_ref, "canceled_pending", event.current_period_end,
                                   cancel_at_period_end=True)
        elif event.type == "subscription.ended":
            db.upsert_subscription(customer, event.subscription_ref, "ended", event.current_period_end or int(time.time()))
        log.info("webhook %s processed (%s)", event.event_id, event.type)
        return JSONResponse({"ok": True})

    def _license_and_sub(data: dict[str, Any]):
        key = str(data.get("license_key") or "")
        device = str(data.get("device_id") or "")
        if not key or not device or len(device) > 128:
            return None, None, _json_error(400, "license_key and device_id are required")
        lic = db.license_by_key(key)
        if lic is None or lic["revoked"]:
            log.info("unknown or revoked license key %s", redact(key[:8]))
            return None, None, _json_error(403, "unknown or revoked license key")
        sub = db.subscription_for_customer(lic["customer_id"])
        return lic, sub, None

    async def activate(request: Request) -> JSONResponse:
        data = await body_json(request)
        lic, sub, err = _license_and_sub(data)
        if err:
            return err
        if not entitled(sub):
            return _json_error(402, "subscription is not active")
        if not db.activate(lic["id"], data["device_id"], data.get("app_version"), config.max_devices):
            return _json_error(409, f"device limit reached ({config.max_devices}); deactivate another device first")
        return JSONResponse({"token": token_for(lic, sub, data["device_id"])})

    async def refresh(request: Request) -> JSONResponse:
        data = await body_json(request)
        lic, sub, err = _license_and_sub(data)
        if err:
            return err
        known = {a["device_id"] for a in db.activations(lic["id"])}
        if data["device_id"] not in known:
            return _json_error(409, "device is not activated")
        if not entitled(sub):
            return _json_error(402, "subscription is not active")
        db.activate(lic["id"], data["device_id"], data.get("app_version"), config.max_devices)
        return JSONResponse({"token": token_for(lic, sub, data["device_id"])})

    async def deactivate(request: Request) -> JSONResponse:
        data = await body_json(request)
        lic, _sub, err = _license_and_sub(data)
        if err:
            return err
        db.deactivate(lic["id"], data["device_id"])
        return JSONResponse({"ok": True})

    async def healthz(_: Request) -> JSONResponse:
        return JSONResponse({"ok": True, "provider": provider.name})

    app = Starlette(routes=[
        Route("/v1/checkout", checkout, methods=["POST"]),
        Route("/v1/checkout/result", checkout_result, methods=["GET"]),
        Route("/v1/webhooks/{provider}", webhook, methods=["POST"]),
        Route("/v1/licenses/activate", activate, methods=["POST"]),
        Route("/v1/licenses/refresh", refresh, methods=["POST"]),
        Route("/v1/licenses/deactivate", deactivate, methods=["POST"]),
        Route("/healthz", healthz, methods=["GET"]),
    ])
    app.state.db = db
    app.state.provider = provider
    return app
