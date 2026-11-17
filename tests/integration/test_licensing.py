"""Licensing end to end: test payment provider -> license server -> client, plus Stripe signature handling."""

import hashlib
import hmac
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "license_server" / "src"))

from starlette.testclient import TestClient  # noqa: E402

from scholarforge.errors import EntitlementRequired, LicenseError, PaymentError  # noqa: E402
from scholarforge.licensing.client import LicenseClient  # noqa: E402
from scholarforge.licensing.providers.stripe import StripeProvider, verify_stripe_signature  # noqa: E402
from scholarforge.licensing.providers.test_provider import TestPaymentProvider  # noqa: E402
from scholarforge.licensing.tokens import generate_keypair, load_public_key, sign_token, verify_token  # noqa: E402
from scholarforge_license_server.app import create_app  # noqa: E402
from scholarforge_license_server.config import ServerConfig  # noqa: E402


@pytest.fixture
def keys():
    pem, pub = generate_keypair()
    return pem, pub


@pytest.fixture
def server(tmp_path, keys):
    pem, _ = keys
    provider = TestPaymentProvider("whsec_unit")
    cfg = ServerConfig(db_path=tmp_path / "ls.sqlite", signing_key_pem=pem, pepper="p" * 32, provider="test")
    app = create_app(cfg, provider=provider)
    return TestClient(app), provider


def _subscribe(client, provider, period_days=30):
    checkout = client.post("/v1/checkout", json={"email": "buyer@example.org"}).json()
    session = checkout["session"]
    headers, body = provider.make_event("checkout.completed", customer="cus_1", subscription="sub_1",
                                        checkout=session, email="buyer@example.org",
                                        current_period_end=int(time.time()) + period_days * 86400)
    assert client.post("/v1/webhooks/test", content=body, headers=headers).status_code == 200
    # replaying the same event is idempotent
    again = client.post("/v1/webhooks/test", content=body, headers=headers).json()
    assert again["duplicate"] is True
    key = client.get(f"/v1/checkout/result?session={session}").json()["license_key"]
    assert client.get(f"/v1/checkout/result?session={session}").status_code == 404  # shown once
    return key


def test_tokens_sign_and_verify(keys):
    pem, pub = keys
    from scholarforge.licensing.tokens import load_private_key

    claims = {"lid": "l", "plan": "pro", "status": "active", "exp": 1, "grace_until": 2, "dev": "d", "iat": 0}
    tok = sign_token(load_private_key(pem), claims)
    assert verify_token(tok, [load_public_key(pub)])["lid"] == "l"
    tampered = tok[:-4] + ("AAAA" if not tok.endswith("AAAA") else "BBBB")
    with pytest.raises(LicenseError):
        verify_token(tampered, [load_public_key(pub)])
    _, other = generate_keypair()
    with pytest.raises(LicenseError):
        verify_token(tok, [load_public_key(other)])


def test_subscription_lifecycle(server, keys, monkeypatch):
    client, provider = server
    _, pub = keys
    key = _subscribe(client, provider)
    lc = LicenseClient(http=client, public_keys=[load_public_key(pub)])
    st = lc.activate(key)
    assert st.state == "active" and st.plan == "pro"
    # offline: token still valid locally without any network
    lc_offline = LicenseClient(http=None, public_keys=[load_public_key(pub)])
    assert lc_offline.status().state == "active"
    # grace period after expiry, then expired
    exp = st.expires_at
    assert lc.status(now=exp + 86400).state == "grace"
    assert lc.status(now=exp + 60 * 86400).state == "expired"
    # cancellation: access continues until the period end, then ends
    headers, body = provider.make_event("subscription.canceled", customer="cus_1", subscription="sub_1",
                                        cancel_at_period_end=True, current_period_end=int(time.time()) + 86400)
    client.post("/v1/webhooks/test", content=body, headers=headers)
    st = lc.refresh()
    assert st.state == "active"
    headers, body = provider.make_event("subscription.ended", customer="cus_1", subscription="sub_1",
                                        current_period_end=int(time.time()) - 10)
    client.post("/v1/webhooks/test", content=body, headers=headers)
    with pytest.raises(LicenseError):
        lc.refresh()
    # the local file survives deletion of the primary copy (backup)
    lc.file.unlink()
    assert lc.status().state in ("active", "grace", "canceled", "expired")
    # deactivation removes local state
    assert lc.deactivate()["deactivated_locally"] is True
    assert lc.status().state == "missing"


def test_device_limit_and_bad_signatures(server):
    client, provider = server
    key = _subscribe(client, provider)
    for i in range(3):
        assert client.post("/v1/licenses/activate", json={"license_key": key, "device_id": f"dev{i}"}).status_code == 200
    r = client.post("/v1/licenses/activate", json={"license_key": key, "device_id": "dev3"})
    assert r.status_code == 409
    assert client.post("/v1/licenses/activate", json={"license_key": "SFK-NOPE", "device_id": "x"}).status_code == 403
    headers, body = provider.make_event("subscription.ended", customer="cus_1", subscription="sub_1")
    headers["X-SF-Test-Signature"] = headers["X-SF-Test-Signature"].replace("v1=", "v1=00")
    assert client.post("/v1/webhooks/test", content=body, headers=headers).status_code == 400


def test_server_never_stores_plain_email_or_key(server, tmp_path):
    client, provider = server
    key = _subscribe(client, provider)
    raw = (tmp_path / "ls.sqlite").read_bytes()
    assert b"buyer@example.org" not in raw
    assert key.encode() not in raw  # the pending plain key was deleted when shown


def test_gate_enforcement(monkeypatch, sf_home):
    from scholarforge.licensing.gate import require_entitlement

    require_entitlement()  # enforcement off by default (source installs)
    monkeypatch.setenv("SCHOLARFORGE_LICENSE_ENFORCEMENT", "on")
    with pytest.raises(EntitlementRequired):
        require_entitlement()


def _stripe_header(secret: str, body: bytes, t: int) -> str:
    sig = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={t},v1={sig}"


def test_stripe_webhook_verification_and_mapping():
    secret = "whsec_stripe_unit"
    prov = StripeProvider("sk_test_dummy", secret, "price_123")
    evt = {"id": "evt_1", "type": "customer.subscription.updated",
           "data": {"object": {"id": "sub_9", "customer": "cus_9", "status": "active", "cancel_at_period_end": False,
                               "items": {"data": [{"current_period_end": 1900000000}]}}}}
    body = json.dumps(evt).encode()
    now = int(time.time())
    parsed = prov.parse_webhook({"Stripe-Signature": _stripe_header(secret, body, now)}, body)
    assert parsed.type == "subscription.active" and parsed.current_period_end == 1900000000
    with pytest.raises(PaymentError):
        prov.parse_webhook({"Stripe-Signature": _stripe_header("wrong", body, now)}, body)
    with pytest.raises(PaymentError):  # replay outside tolerance
        verify_stripe_signature(_stripe_header(secret, body, now - 3600), body, secret, now=now)
    deleted = {"id": "evt_2", "type": "customer.subscription.deleted", "data": {"object": {"id": "sub_9",
                                                                                         "customer": "cus_9"}}}
    b2 = json.dumps(deleted).encode()
    assert prov.parse_webhook({"stripe-signature": _stripe_header(secret, b2, now)}, b2).type == "subscription.ended"


def test_stripe_checkout_request_shape():
    import httpx

    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["Authorization"]
        seen["form"] = dict(httpx.QueryParams(request.content.decode()))
        return httpx.Response(200, json={"id": "cs_1", "url": "https://checkout.stripe.com/c/cs_1"})

    prov = StripeProvider("sk_test_dummy", "whsec_x", "price_123", transport=httpx.MockTransport(handler))
    out = prov.create_checkout(success_url="https://s/ok", cancel_url="https://s/no", client_reference="ref1")
    assert out["url"].startswith("https://checkout.stripe.com")
    assert seen["url"].endswith("/v1/checkout/sessions") and seen["auth"] == "Bearer sk_test_dummy"
    assert seen["form"]["mode"] == "subscription" and seen["form"]["line_items[0][price]"] == "price_123"
