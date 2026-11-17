"""Signed license tokens: ``SF1.<base64url(payload JSON)>.<base64url(Ed25519 signature)>``."""

from __future__ import annotations

import base64
import json
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from scholarforge.errors import LicenseError

PREFIX = "SF1"


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def canonical(claims: dict[str, Any]) -> bytes:
    return json.dumps(claims, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def sign_token(private_key: Ed25519PrivateKey, claims: dict[str, Any]) -> str:
    payload = canonical(claims)
    return f"{PREFIX}.{_b64e(payload)}.{_b64e(private_key.sign(payload))}"


def verify_token(token: str, public_keys: list[Ed25519PublicKey]) -> dict[str, Any]:
    try:
        prefix, payload_b64, sig_b64 = token.strip().split(".")
    except ValueError as exc:
        raise LicenseError("malformed license token") from exc
    if prefix != PREFIX:
        raise LicenseError("unsupported license token version")
    payload, signature = _b64d(payload_b64), _b64d(sig_b64)
    for key in public_keys:
        try:
            key.verify(signature, payload)
            break
        except InvalidSignature:
            continue
    else:
        raise LicenseError("license token signature is invalid", hint="re-activate the license")
    try:
        claims = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise LicenseError("license token payload is not valid JSON") from exc
    for field in ("lid", "plan", "status", "exp", "grace_until", "dev", "iat"):
        if field not in claims:
            raise LicenseError(f"license token lacks '{field}'")
    return claims


def generate_keypair() -> tuple[str, str]:
    """Return (private_pem, public_b64) for a new signing key (license server setup)."""
    priv = Ed25519PrivateKey.generate()
    pem = priv.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()).decode("ascii")
    pub = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return pem, base64.b64encode(pub).decode("ascii")


def load_private_key(pem: str) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(pem.encode("ascii"), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise LicenseError("the license signing key must be an Ed25519 key")
    return key


def load_public_key(b64: str) -> Ed25519PublicKey:
    return Ed25519PublicKey.from_public_bytes(base64.b64decode(b64))
