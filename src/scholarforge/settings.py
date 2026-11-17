"""Machine-level settings (``config.yaml``) with validation and env overrides.

Resolution order: built-in defaults < config file < environment variables.
Secrets are never stored here: API keys live in the OS credential store
(see :mod:`scholarforge.secrets`).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from scholarforge import paths
from scholarforge.errors import ConfigError
from scholarforge.util.atomic import atomic_write_yaml, read_yaml
from scholarforge.version import __version__


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class NetworkSettings(_Model):
    enabled: bool = True
    timeout_s: float = Field(20.0, gt=0, le=300)
    max_retries: int = Field(3, ge=0, le=10)
    # Opt-in only: Crossref asks polite clients for a contact address. Never filled automatically.
    contact_email: str | None = None

    def user_agent(self) -> str:
        ua = f"ScholarForge/{__version__} (local scientific publishing engine)"
        if self.contact_email:
            ua += f" mailto:{self.contact_email}"
        return ua


class SourceSettings(_Model):
    crossref: bool = True
    openalex: bool = True
    arxiv: bool = True
    semantic_scholar: bool = True
    openlibrary: bool = True
    cache_ttl_days: int = Field(30, ge=0, le=3650)
    min_title_similarity: float = Field(0.86, ge=0.5, le=1.0)


class SandboxSettings(_Model):
    timeout_s: int = Field(90, ge=5, le=3600)
    memory_mb: int = Field(2048, ge=128, le=65536)
    # strict: AST allow-list + audit hook; permissive: audit hook only (user must opt in explicitly)
    policy: Literal["strict", "permissive"] = "strict"


class MathSettings(_Model):
    timeout_s: int = Field(30, ge=2, le=600)
    numeric_samples: int = Field(24, ge=4, le=1000)
    rel_tolerance: float = Field(1e-8, gt=0, lt=1)


class WorkflowSettings(_Model):
    lease_minutes: int = Field(60, ge=1, le=24 * 60)
    max_attempts: int = Field(3, ge=1, le=10)
    max_revision_rounds: int = Field(2, ge=0, le=10)
    chapter_review: bool = True
    words_per_page: int = Field(380, ge=150, le=900)
    engine_time_budget_s: float = Field(25.0, ge=1, le=600)
    auto_checkpoint_keep: int = Field(25, ge=1, le=1000)


class ToolSettings(_Model):
    quarto: str | None = None
    lualatex: str | None = None
    java: str | None = None
    epubcheck_jar: str | None = None
    dot: str | None = None
    lean: str | None = None
    lake: str | None = None
    mathlib_project: str | None = None
    render_timeout_s: int = Field(1800, ge=30, le=6 * 3600)


class ImageProviderSettings(_Model):
    enabled: bool = False
    provider: Literal["openai"] = "openai"
    model: str = "gpt-image-2.6-sunburst"
    quality: Literal["low", "medium", "high", "xhigh", "max", "auto"] = "high"
    size: str = "1536x1024"
    max_cost_per_image_usd: float = Field(1.0, gt=0, le=100)
    require_confirmation: bool = True
    base_url: str = "https://api.openai.com/v1"

    @field_validator("size")
    @classmethod
    def _size(cls, value: str) -> str:
        if value == "auto":
            return value
        try:
            w, h = (int(x) for x in value.lower().split("x"))
        except ValueError as exc:
            raise ValueError("size must be 'auto' or WIDTHxHEIGHT") from exc
        if w % 16 or h % 16:
            raise ValueError("width and height must be multiples of 16")
        return f"{w}x{h}"


class LicenseSettings(_Model):
    server_url: str | None = None
    # "off": source/dev installs; "on": packaged releases enforce an active subscription for
    # write operations. Reading and exporting existing projects is never blocked.
    enforcement: Literal["off", "on"] = "off"
    offline_grace_days: int = Field(14, ge=0, le=60)


class Settings(_Model):
    projects_root: Path = Field(default_factory=paths.default_projects_root)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    network: NetworkSettings = Field(default_factory=NetworkSettings)
    sources: SourceSettings = Field(default_factory=SourceSettings)
    sandbox: SandboxSettings = Field(default_factory=SandboxSettings)
    math: MathSettings = Field(default_factory=MathSettings)
    workflow: WorkflowSettings = Field(default_factory=WorkflowSettings)
    tools: ToolSettings = Field(default_factory=ToolSettings)
    images: ImageProviderSettings = Field(default_factory=ImageProviderSettings)
    license: LicenseSettings = Field(default_factory=LicenseSettings)

    def to_yaml_dict(self) -> dict:
        return self.model_dump(mode="json")


_ENV_BOOL = {"1": True, "true": True, "yes": True, "on": True, "0": False, "false": False, "no": False, "off": False}


def _apply_env(data: dict) -> dict:
    env = os.environ
    if "SCHOLARFORGE_PROJECTS" in env:
        data["projects_root"] = env["SCHOLARFORGE_PROJECTS"]
    if "SCHOLARFORGE_LOG_LEVEL" in env:
        data["log_level"] = env["SCHOLARFORGE_LOG_LEVEL"].upper()
    if "SCHOLARFORGE_OFFLINE" in env:
        flag = _ENV_BOOL.get(env["SCHOLARFORGE_OFFLINE"].lower(), True)
        data.setdefault("network", {})["enabled"] = not flag
    for key, tool in (
        ("SCHOLARFORGE_QUARTO", "quarto"),
        ("SCHOLARFORGE_LUALATEX", "lualatex"),
        ("SCHOLARFORGE_EPUBCHECK_JAR", "epubcheck_jar"),
        ("SCHOLARFORGE_JAVA", "java"),
    ):
        if key in env:
            data.setdefault("tools", {})[tool] = env[key]
    if "SCHOLARFORGE_LICENSE_ENFORCEMENT" in env:
        data.setdefault("license", {})["enforcement"] = env["SCHOLARFORGE_LICENSE_ENFORCEMENT"]
    if "SCHOLARFORGE_LICENSE_SERVER" in env:
        data.setdefault("license", {})["server_url"] = env["SCHOLARFORGE_LICENSE_SERVER"]
    return data


def load_settings(path: Path | None = None) -> Settings:
    cfg_path = path or paths.config_file()
    data: dict = {}
    if cfg_path.exists():
        try:
            loaded = read_yaml(cfg_path)
        except Exception as exc:  # yaml.YAMLError, UnicodeDecodeError
            raise ConfigError(f"cannot parse {cfg_path}: {exc}", hint="fix or delete the config file") from exc
        if loaded is not None and not isinstance(loaded, dict):
            raise ConfigError(f"{cfg_path} must contain a mapping")
        data = loaded or {}
    data = _apply_env(data)
    try:
        return Settings.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(
            f"invalid configuration in {cfg_path}",
            details={"errors": exc.errors(include_url=False, include_context=False)},
            hint="run `scholarforge config show` to compare with defaults",
        ) from exc


def save_settings(settings: Settings, path: Path | None = None) -> Path:
    cfg_path = path or paths.config_file()
    return atomic_write_yaml(cfg_path, settings.to_yaml_dict())


def set_setting(settings: Settings, dotted_key: str, raw_value: str) -> Settings:
    """Set ``a.b.c`` from a string value with type coercion through validation."""
    import yaml

    data = settings.model_dump(mode="json")
    node = data
    parts = dotted_key.split(".")
    for part in parts[:-1]:
        if part not in node or not isinstance(node[part], dict):
            raise ConfigError(f"unknown setting: {dotted_key}")
        node = node[part]
    if parts[-1] not in node:
        raise ConfigError(f"unknown setting: {dotted_key}")
    node[parts[-1]] = yaml.safe_load(raw_value) if raw_value != "" else None
    try:
        return Settings.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"invalid value for {dotted_key}: {raw_value!r}",
                          details={"errors": exc.errors(include_url=False, include_context=False)}) from exc
