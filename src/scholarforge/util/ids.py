"""Time-sortable identifiers (ULID layout: 48-bit millisecond time + 80 random bits)."""

from __future__ import annotations

import os
import re
import secrets
import time

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ID_RE = re.compile(r"^[a-z]{2,6}_[0-9A-HJKMNP-TV-Z]{26}$")


def _encode(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        chars.append(_CROCKFORD[value & 31])
        value >>= 5
    return "".join(reversed(chars))


def ulid() -> str:
    ms = int(time.time() * 1000) & ((1 << 48) - 1)
    rand = int.from_bytes(os.urandom(10), "big")
    return _encode(ms, 10) + _encode(rand, 16)


def new_id(prefix: str) -> str:
    """Return ``<prefix>_<ULID>``; prefixes are short lowercase entity tags."""
    if not re.fullmatch(r"[a-z]{2,6}", prefix):
        raise ValueError(f"invalid id prefix: {prefix!r}")
    return f"{prefix}_{ulid()}"


def is_valid_id(value: str, prefix: str | None = None) -> bool:
    if not isinstance(value, str) or not _ID_RE.match(value):
        return False
    return prefix is None or value.startswith(prefix + "_")


def token(nbytes: int = 18) -> str:
    """Opaque URL-safe random token (lease tokens, confirmation tokens)."""
    return secrets.token_urlsafe(nbytes)
