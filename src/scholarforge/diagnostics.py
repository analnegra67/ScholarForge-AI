"""Environment diagnostics (``scholarforge doctor`` / MCP ``diagnostics`` tool).

Reports facts only: what is installed, what works, what is missing and how to fix
it. Large external dependencies (Quarto, a TeX distribution) are named honestly.
"""

from __future__ import annotations

import os
import platform
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any

from scholarforge import paths
from scholarforge.errors import ScholarForgeError
from scholarforge.version import __version__


def _check(name: str, ok: bool, detail: str = "", fix: str | None = None, required: bool = True) -> dict[str, Any]:
    item: dict[str, Any] = {"name": name, "ok": bool(ok), "required": required, "detail": detail}
    if not ok and fix:
        item["fix"] = fix
    return item


def _writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path, delete=True):
            pass
        return True
    except OSError:
        return False


def appdata_virtualization() -> str | None:
    """On Windows, apps packaged as MSIX (e.g. some desktop AI clients) silently redirect writes to
    %LOCALAPPDATA% into their own package folder. Detect it so users understand where tools went."""
    if os.name != "nt" or not os.environ.get("LOCALAPPDATA"):
        return None
    real = paths.package_redirect(paths.data_dir())
    return str(real) if real is not None else None


def run_doctor(deep: bool = False) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    checks.append(_check("python", sys.version_info >= (3, 12), sys.version.split()[0], "install Python 3.12+"))
    checks.append(_check("sqlite", sqlite3.sqlite_version_info >= (3, 35), sqlite3.sqlite_version,
                         "upgrade Python (bundled SQLite too old)"))

    try:
        from scholarforge.settings import load_settings

        settings = load_settings()
        checks.append(_check("config", True, str(paths.config_file())))
    except ScholarForgeError as exc:
        settings = None
        checks.append(_check("config", False, exc.message, exc.hint))

    for label, getter in (("data_dir", paths.data_dir), ("cache_dir", paths.cache_dir), ("logs_dir", paths.logs_dir)):
        p = getter()
        checks.append(_check(label, _writable(p), str(p), f"make {p} writable"))
    if settings is not None:
        root = Path(settings.projects_root)
        ok = _writable(root)
        detail = str(root)
        if paths.is_cloud_synced(root):
            detail += " (cloud-synced folder: works, but sync clients can briefly lock files)"
        checks.append(_check("projects_root", ok, detail, "set projects_root to a writable folder"))

    for mod in ("pydantic", "yaml", "httpx", "sympy", "numpy", "scipy", "matplotlib", "pikepdf", "PIL", "mcp",
                "keyring", "cryptography", "pypdfium2"):
        try:
            module = __import__(mod)
            version = getattr(module, "__version__", "")
            if not version:
                from importlib import metadata

                dist = {"yaml": "PyYAML", "PIL": "Pillow"}.get(mod, mod)
                try:
                    version = metadata.version(dist)
                except metadata.PackageNotFoundError:
                    version = "installed"
            checks.append(_check(f"python:{mod}", True, str(version)))
        except Exception as exc:  # pragma: no cover - depends on installation
            checks.append(_check(f"python:{mod}", False, str(exc), "reinstall ScholarForge dependencies"))

    from scholarforge.secrets import _keyring

    kr = _keyring()
    checks.append(_check("credential_store", kr is not None,
                         type(kr.get_keyring()).__name__ if kr else "unavailable",
                         "API keys can still be provided through environment variables", required=False))

    tools: dict[str, Any] = {}
    try:
        from scholarforge.tools.detect import detect_all

        tools = detect_all()
        for name, info in tools.items():
            checks.append(_check(f"tool:{name}", info.get("available", False), info.get("detail", ""),
                                 info.get("fix"), required=info.get("required", False)))
    except ImportError:
        pass

    virtual = appdata_virtualization()
    if virtual:
        checks.append(_check(
            "appdata_virtualized", False,
            f"user data is redirected to {virtual} by a packaged (MSIX) host app",
            "tools and settings installed from this app are visible only to programs started from it; "
            "install them again from a normal terminal if you also use ScholarForge outside the app",
            required=False))

    if settings is not None:
        checks.append(_check("network", settings.network.enabled,
                             "enabled" if settings.network.enabled else "disabled (offline mode)", required=False))

    smoke: dict[str, Any] = {}
    if deep:
        try:
            from scholarforge.tools.smoke import run_smoke_tests

            smoke = run_smoke_tests()
        except ImportError:
            smoke = {"skipped": "smoke tests unavailable"}

    required_failures = [c["name"] for c in checks if c["required"] and not c["ok"]]
    return {
        "scholarforge": __version__,
        "platform": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "pid": os.getpid(),
        "ok": not required_failures,
        "required_failures": required_failures,
        "checks": checks,
        "tools": tools,
        "smoke": smoke,
    }
