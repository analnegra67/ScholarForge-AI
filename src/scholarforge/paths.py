"""Platform-specific locations.

``SCHOLARFORGE_HOME`` relocates *all* machine-level state (config, data, cache,
logs, tools) under one directory, which is what tests and portable installs use.
Book projects live under ``projects_root`` (configurable, default
``<Documents>/ScholarForge``) and are fully self-contained folders.
"""

from __future__ import annotations

import os
from pathlib import Path

import platformdirs

APP_NAME = "ScholarForge"


def _home_override() -> Path | None:
    value = os.environ.get("SCHOLARFORGE_HOME")
    return Path(value).expanduser().resolve() if value else None


def data_dir() -> Path:
    home = _home_override()
    path = home / "data" if home else Path(platformdirs.user_data_dir(APP_NAME, appauthor=False))
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_dir() -> Path:
    home = _home_override()
    path = home / "config" if home else Path(platformdirs.user_config_dir(APP_NAME, appauthor=False))
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_dir() -> Path:
    home = _home_override()
    path = home / "cache" if home else Path(platformdirs.user_cache_dir(APP_NAME, appauthor=False))
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir() -> Path:
    home = _home_override()
    path = home / "logs" if home else Path(platformdirs.user_log_dir(APP_NAME, appauthor=False))
    path.mkdir(parents=True, exist_ok=True)
    return path


def tools_dir() -> Path:
    override = os.environ.get("SCHOLARFORGE_TOOLS_DIR")
    path = Path(override).expanduser().resolve() if override else data_dir() / "tools"
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_projects_root() -> Path:
    override = os.environ.get("SCHOLARFORGE_PROJECTS")
    if override:
        return Path(override).expanduser().resolve()
    home = _home_override()
    if home:
        return home / "projects"
    return Path(platformdirs.user_documents_dir()) / APP_NAME


def config_file() -> Path:
    override = os.environ.get("SCHOLARFORGE_CONFIG")
    return Path(override).expanduser().resolve() if override else config_dir() / "config.yaml"


def ensure_within(base: Path, candidate: Path) -> Path:
    """Resolve ``candidate`` and guarantee it stays inside ``base`` (path traversal guard)."""
    from scholarforge.errors import PathSecurityError

    base_r = base.resolve()
    cand_r = (base_r / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
    try:
        cand_r.relative_to(base_r)
    except ValueError as exc:
        raise PathSecurityError(
            f"path escapes the allowed directory: {candidate}", details={"base": str(base_r)}
        ) from exc
    return cand_r


def is_cloud_synced(path: Path) -> bool:
    """Best-effort detection of OneDrive/Dropbox/iCloud folders (SQLite + sync clients = risk)."""
    markers = ("onedrive", "dropbox", "icloud", "google drive", "googledrive", "mobile documents")
    return any(m in part.lower() for part in path.resolve().parts for m in markers)
