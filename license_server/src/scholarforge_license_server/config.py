from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ServerConfig:
    db_path: Path
    signing_key_pem: str
    pepper: str
    provider: str = "test"  # "test" | "stripe"
    public_url: str = "http://127.0.0.1:8787"
    max_devices: int = 3
    token_buffer_days: int = 3  # token validity beyond the paid period (covers webhook delays)
    offline_grace_days: int = 14
    stripe_secret_key: str | None = None
    stripe_webhook_secret: str | None = None
    stripe_price_id: str | None = None
    test_webhook_secret: str = "whsec_test_local_only"
    allowed_origins: list[str] = field(default_factory=list)

    @classmethod
    def from_env(cls) -> "ServerConfig":
        key_file = os.environ.get("SF_LS_SIGNING_KEY_FILE")
        if not key_file or not Path(key_file).exists():
            raise RuntimeError("SF_LS_SIGNING_KEY_FILE must point to the Ed25519 private key (PEM)")
        pepper = os.environ.get("SF_LS_PEPPER")
        if not pepper or len(pepper) < 16:
            raise RuntimeError("SF_LS_PEPPER (>= 16 random characters) is required to hash license keys")
        return cls(
            db_path=Path(os.environ.get("SF_LS_DB", "license-server.sqlite")),
            signing_key_pem=Path(key_file).read_text(encoding="ascii"),
            pepper=pepper,
            provider=os.environ.get("SF_LS_PROVIDER", "test"),
            public_url=os.environ.get("SF_LS_PUBLIC_URL", "http://127.0.0.1:8787"),
            max_devices=int(os.environ.get("SF_LS_MAX_DEVICES", "3")),
            stripe_secret_key=os.environ.get("STRIPE_SECRET_KEY"),
            stripe_webhook_secret=os.environ.get("STRIPE_WEBHOOK_SECRET"),
            stripe_price_id=os.environ.get("STRIPE_PRICE_ID"),
            test_webhook_secret=os.environ.get("SF_LS_TEST_WEBHOOK_SECRET", "whsec_test_local_only"),
        )
