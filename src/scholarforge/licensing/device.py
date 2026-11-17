"""Privacy-preserving device identifier: a salted hash, never the raw machine id."""

from __future__ import annotations

import getpass
import hashlib
import os
import platform
import subprocess
import uuid
from pathlib import Path

from scholarforge import paths

SALT = b"scholarforge-device-v1"


def _machine_id() -> str | None:
    system = platform.system()
    try:
        if system == "Windows":
            import winreg  # type: ignore[import-not-found]

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography",
                                0, winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)) as k:
                return str(winreg.QueryValueEx(k, "MachineGuid")[0])
        if system == "Linux":
            for p in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
                if Path(p).exists():
                    return Path(p).read_text().strip()
        if system == "Darwin":
            out = subprocess.run(["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"], capture_output=True, text=True,
                                 timeout=10).stdout
            for line in out.splitlines():
                if "IOPlatformUUID" in line:
                    return line.split("=")[-1].strip().strip('"')
    except Exception:
        return None
    return None


def device_id() -> str:
    raw = _machine_id()
    if not raw:
        f = paths.data_dir() / "license" / "device-id"
        f.parent.mkdir(parents=True, exist_ok=True)
        if not f.exists():
            f.write_text(str(uuid.uuid4()))
        raw = f.read_text().strip()
    try:
        user = getpass.getuser()
    except Exception:
        user = os.environ.get("USERNAME", "")
    return hashlib.sha256(SALT + raw.encode() + b"|" + user.encode()).hexdigest()[:32]
