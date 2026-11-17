"""License client: activation, offline validation with grace period, refresh, deactivation.

Local state lives in ``<data_dir>/license/license.json`` (+ ``.bak`` copy written
atomically, so a crash or an accidental deletion of one file does not lose the
license). The license key itself is kept in the OS credential store.
"""

from __future__ import annotations

import os
import time
from typing import Any, Literal

import httpx
from pydantic import BaseModel

from scholarforge import paths, secrets
from scholarforge.errors import LicenseError, NetworkUnavailable
from scholarforge.licensing.device import device_id
from scholarforge.licensing.tokens import load_public_key, verify_token
from scholarforge.logging_setup import get_logger
from scholarforge.util.atomic import atomic_write_json, read_json
from scholarforge.version import __version__

log = get_logger("license")

State = Literal["active", "grace", "expired", "canceled", "missing", "invalid", "wrong_device"]


class LicenseStatus(BaseModel):
    state: State
    plan: str | None = None
    license_id: str | None = None
    expires_at: int | None = None
    grace_until: int | None = None
    last_refresh: int | None = None
    message: str = ""

    @property
    def usable(self) -> bool:
        return self.state in ("active", "grace")


def configured_public_keys() -> list:
    keys = []
    env = os.environ.get("SCHOLARFORGE_LICENSE_PUBKEYS", "")
    for item in [k.strip() for k in env.split(",") if k.strip()]:
        keys.append(load_public_key(item))
    try:
        from scholarforge.licensing import keys as builtin

        for item in builtin.PUBLIC_KEYS:
            keys.append(load_public_key(item))
    except ImportError:
        pass
    return keys


class LicenseClient:
    def __init__(self, server_url: str | None = None, http: httpx.Client | None = None,
                 public_keys: list | None = None):
        from scholarforge.settings import load_settings

        settings = load_settings()
        self.settings = settings.license
        self.server_url = (server_url or self.settings.server_url or "").rstrip("/")
        self._http = http
        self.public_keys = public_keys if public_keys is not None else configured_public_keys()
        self.dir = paths.data_dir() / "license"
        self.file = self.dir / "license.json"

    # ---------------------------------------------------------------------------------- storage
    def _load(self) -> dict[str, Any] | None:
        for f in (self.file, self.file.with_suffix(".json.bak")):
            if f.exists():
                try:
                    return read_json(f)
                except Exception:
                    continue
        return None

    def _store(self, data: dict[str, Any]) -> None:
        atomic_write_json(self.file, data)
        atomic_write_json(self.file.with_suffix(".json.bak"), data)

    def _http_client(self) -> httpx.Client:
        if self._http is not None:
            return self._http
        if not self.server_url:
            raise LicenseError("no license server configured",
                               hint="set license.server_url (scholarforge config set license.server_url URL)")
        return httpx.Client(base_url=self.server_url, timeout=20, headers={"User-Agent": f"ScholarForge/{__version__}"})

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        client = self._http_client()
        try:
            resp = client.post(path, json=body)
        except httpx.HTTPError as exc:
            raise NetworkUnavailable(f"license server unreachable: {type(exc).__name__}") from exc
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("error", resp.text[:200])
            except Exception:
                detail = resp.text[:200]
            raise LicenseError(f"license server refused the request ({resp.status_code}): {detail}")
        return resp.json()

    # -------------------------------------------------------------------------------------- api
    def status(self, now: float | None = None) -> LicenseStatus:
        now = now or time.time()
        data = self._load()
        if not data or "token" not in data:
            return LicenseStatus(state="missing", message="no license activated on this device")
        if not self.public_keys:
            return LicenseStatus(state="invalid", message="no license verification key configured in this build")
        try:
            claims = verify_token(data["token"], self.public_keys)
        except LicenseError as exc:
            return LicenseStatus(state="invalid", message=exc.message)
        base = {"plan": claims["plan"], "license_id": claims["lid"], "expires_at": claims["exp"],
                "grace_until": claims["grace_until"], "last_refresh": data.get("refreshed_at")}
        if claims["dev"] != device_id():
            return LicenseStatus(state="wrong_device", message="the license was activated on another device", **base)
        if claims["status"] == "canceled" and now > claims["exp"]:
            return LicenseStatus(state="canceled", message="the subscription has ended", **base)
        if now <= claims["exp"]:
            return LicenseStatus(state="active", message="subscription active", **base)
        local_grace = claims["exp"] + self.settings.offline_grace_days * 86400
        if now <= min(claims["grace_until"], local_grace):
            return LicenseStatus(state="grace", message="offline grace period: refresh when online", **base)
        return LicenseStatus(state="expired", message="license expired; refresh or renew the subscription", **base)

    def activate(self, license_key: str) -> LicenseStatus:
        key = license_key.strip()
        if not key:
            raise LicenseError("empty license key")
        data = self._post("/v1/licenses/activate", {"license_key": key, "device_id": device_id(),
                                                     "app_version": __version__})
        verify_token(data["token"], self.public_keys)
        record: dict[str, Any] = {"token": data["token"], "refreshed_at": int(time.time())}
        try:
            secrets.set_secret("license_key", key)
        except Exception:
            # No OS credential store (e.g. headless Linux): keep the key next to the token so that a refresh
            # does not lock the user out. A license key only allows activations within the device limit.
            record["license_key"] = key
            log.warning("credential store unavailable: license key kept in the local license file")
        self._store(record)
        return self.status()

    def _license_key(self) -> str | None:
        return secrets.get_secret("license_key") or (self._load() or {}).get("license_key")

    def refresh(self) -> LicenseStatus:
        key = self._license_key()
        if not key:
            raise LicenseError("no license key stored on this device", hint="scholarforge license activate <KEY>")
        data = self._post("/v1/licenses/refresh", {"license_key": key, "device_id": device_id(),
                                                    "app_version": __version__})
        verify_token(data["token"], self.public_keys)
        previous = self._load() or {}
        record = {"token": data["token"], "refreshed_at": int(time.time())}
        if previous.get("license_key"):
            record["license_key"] = previous["license_key"]
        self._store(record)
        return self.status()

    def deactivate(self) -> dict[str, Any]:
        key = self._license_key()
        result: dict[str, Any] = {"deactivated_locally": True}
        if key:
            try:
                self._post("/v1/licenses/deactivate", {"license_key": key, "device_id": device_id()})
                result["server"] = "device released"
            except (LicenseError, NetworkUnavailable) as exc:
                result["server"] = f"not reached: {exc.message}"
        for f in (self.file, self.file.with_suffix(".json.bak")):
            f.unlink(missing_ok=True)
        secrets.delete_secret("license_key")
        return result

    def maybe_refresh(self, max_age_s: int = 86400) -> LicenseStatus:
        """Refresh at most once a day when online; never fail hard on network problems."""
        st = self.status()
        data = self._load() or {}
        if st.state in ("missing", "invalid"):
            return st
        if time.time() - int(data.get("refreshed_at") or 0) < max_age_s and st.state == "active":
            return st
        try:
            return self.refresh()
        except (NetworkUnavailable, LicenseError) as exc:
            log.info("license refresh skipped: %s", exc.message)
            return st
