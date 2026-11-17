"""Secret storage in the operating-system credential store (via ``keyring``).

* Windows: Credential Manager; macOS: Keychain; Linux: Secret Service/KWallet.
* Environment variables (e.g. ``OPENAI_API_KEY``) are honoured read-only and are
  never written to disk by ScholarForge.
* No plaintext fallback file is used: if no secure backend exists, storing fails
  loudly and the user is told to use an environment variable instead.
"""

from __future__ import annotations

import os

from scholarforge.errors import SecretError

SERVICE = "ScholarForge"

ENV_FALLBACKS = {
    "openai_api_key": ("SCHOLARFORGE_OPENAI_API_KEY", "OPENAI_API_KEY"),
    "openalex_api_key": ("SCHOLARFORGE_OPENALEX_API_KEY", "OPENALEX_API_KEY"),
    "semantic_scholar_api_key": ("SCHOLARFORGE_S2_API_KEY", "S2_API_KEY"),
    "license_key": ("SCHOLARFORGE_LICENSE_KEY",),
}


def _keyring():
    if os.environ.get("SCHOLARFORGE_DISABLE_KEYRING") == "1":
        return None
    try:
        import keyring
        from keyring.backends import fail

        backend = keyring.get_keyring()
        if isinstance(backend, fail.Keyring):
            return None
        return keyring
    except Exception:  # pragma: no cover - backend import problems
        return None


def get_secret(name: str) -> str | None:
    for env_name in ENV_FALLBACKS.get(name, ()):
        value = os.environ.get(env_name)
        if value:
            return value.strip()
    kr = _keyring()
    if kr is None:
        return None
    try:
        value = kr.get_password(SERVICE, name)
    except Exception:
        return None
    return value.strip() if value else None


def set_secret(name: str, value: str) -> None:
    if not value or not value.strip():
        raise SecretError("refusing to store an empty secret")
    kr = _keyring()
    if kr is None:
        raise SecretError(
            "no secure credential store is available on this system",
            hint=f"set the environment variable {ENV_FALLBACKS.get(name, ('<NAME>',))[0]} instead",
        )
    try:
        kr.set_password(SERVICE, name, value.strip())
    except Exception as exc:
        raise SecretError(f"could not store secret '{name}': {type(exc).__name__}") from exc


def delete_secret(name: str) -> bool:
    kr = _keyring()
    if kr is None:
        return False
    try:
        kr.delete_password(SERVICE, name)
        return True
    except Exception:
        return False


def secret_source(name: str) -> str:
    """Where a secret would come from: 'env', 'keyring' or 'missing' (never the value)."""
    for env_name in ENV_FALLBACKS.get(name, ()):
        if os.environ.get(env_name):
            return "env"
    kr = _keyring()
    if kr is not None:
        try:
            if kr.get_password(SERVICE, name):
                return "keyring"
        except Exception:
            pass
    return "missing"


def mask(value: str | None) -> str:
    if not value:
        return "<none>"
    if len(value) <= 8:
        return "***"
    return value[:3] + "…" + value[-4:]
