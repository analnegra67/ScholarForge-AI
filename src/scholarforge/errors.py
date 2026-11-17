"""Exception hierarchy.

Every error raised deliberately by ScholarForge carries a stable machine-readable
``code`` so that the CLI, the MCP server and the host AI can react to it without
parsing messages. ``hint`` tells a human (or the host AI) what to do next.
"""

from __future__ import annotations

from typing import Any


class ScholarForgeError(Exception):
    code = "SF_ERROR"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None, hint: str | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}
        self.hint = hint

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            data["details"] = self.details
        if self.hint:
            data["hint"] = self.hint
        return data

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"[{self.code}] {self.message}" + (f" (hint: {self.hint})" if self.hint else "")


class ConfigError(ScholarForgeError):
    code = "CONFIG_ERROR"


class StorageError(ScholarForgeError):
    code = "STORAGE_ERROR"


class MigrationError(StorageError):
    code = "MIGRATION_ERROR"


class ProjectNotFound(ScholarForgeError):
    code = "PROJECT_NOT_FOUND"


class ProjectExists(ScholarForgeError):
    code = "PROJECT_EXISTS"


class NotFound(ScholarForgeError):
    code = "NOT_FOUND"


class TaskNotFound(NotFound):
    code = "TASK_NOT_FOUND"


class LeaseConflict(ScholarForgeError):
    """A task result was submitted with a stale or foreign lease token."""

    code = "LEASE_CONFLICT"


class InvalidInput(ScholarForgeError):
    code = "INVALID_INPUT"


class WorkflowError(ScholarForgeError):
    code = "WORKFLOW_ERROR"


class NetworkUnavailable(ScholarForgeError):
    code = "NETWORK_UNAVAILABLE"


class ExternalServiceError(ScholarForgeError):
    code = "EXTERNAL_SERVICE_ERROR"


class ToolMissing(ScholarForgeError):
    """An external program (Quarto, LuaLaTeX, epubcheck, Lean...) is not installed."""

    code = "TOOL_MISSING"


class SandboxViolation(ScholarForgeError):
    code = "SANDBOX_VIOLATION"


class SandboxTimeout(ScholarForgeError):
    code = "SANDBOX_TIMEOUT"


class RenderError(ScholarForgeError):
    code = "RENDER_ERROR"


class PathSecurityError(ScholarForgeError):
    code = "PATH_SECURITY"


class SecretError(ScholarForgeError):
    code = "SECRET_ERROR"


class ConfirmationRequired(ScholarForgeError):
    """An operation (paid API call, destructive change) needs explicit user confirmation."""

    code = "CONFIRMATION_REQUIRED"


class LicenseError(ScholarForgeError):
    code = "LICENSE_ERROR"


class EntitlementRequired(LicenseError):
    code = "ENTITLEMENT_REQUIRED"


class PaymentError(ScholarForgeError):
    code = "PAYMENT_ERROR"
