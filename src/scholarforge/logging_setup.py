"""Structured logging with secret redaction.

Rules enforced here:
* logs go to a rotating JSON-lines file and (optionally) stderr, never stdout,
  because stdout is the MCP stdio transport;
* every record passes through :class:`RedactingFilter`, which masks API keys,
  bearer tokens, webhook secrets, license keys and e-mail addresses;
* book text is never logged at INFO level by ScholarForge code (ids and hashes only).
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import re
import sys
from pathlib import Path

from scholarforge import paths

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"sk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_\-]{16,}"), "sk-***REDACTED***"),
    (re.compile(r"\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{10,}"), "***REDACTED_STRIPE_KEY***"),
    (re.compile(r"\bwhsec_[A-Za-z0-9]{10,}"), "whsec_***REDACTED***"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-~+/=]{8,}"), "Bearer ***REDACTED***"),
    (re.compile(r"(?i)(api[_-]?key|token|secret|password)(\"?\s*[:=]\s*\"?)[^\s\"',;]{6,}"), r"\1\2***REDACTED***"),
    (re.compile(r"\bSF1\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"), "SF1.***REDACTED_LICENSE***"),
    (re.compile(r"\bSFK-[A-Z0-9]{4}(?:-[A-Z0-9]{4}){3,}"), "SFK-***REDACTED***"),
    (re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"), "***@***"),
]


def redact(text: str) -> str:
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - malformed format args
            message = str(record.msg)
        record.msg = redact(message)
        record.args = ()
        if record.exc_info and not record.exc_text:
            record.exc_text = redact(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = redact(record.exc_text)
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in ("project_id", "task_id", "event"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_text:
            payload["exc"] = record.exc_text
        return json.dumps(payload, ensure_ascii=False)


_CONFIGURED = False


def configure_logging(level: str = "INFO", *, stderr: bool = False, log_file: Path | None = None) -> logging.Logger:
    """Idempotently configure the ``scholarforge`` logger tree."""
    global _CONFIGURED
    logger = logging.getLogger("scholarforge")
    logger.setLevel(level.upper())
    logger.propagate = False
    if _CONFIGURED:
        for handler in logger.handlers:
            if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
                handler.setLevel(level.upper())
        return logger
    target = log_file or (paths.logs_dir() / "scholarforge.log")
    target.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(target, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(JsonFormatter())
    file_handler.addFilter(RedactingFilter())
    logger.addHandler(file_handler)
    if stderr:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        stream.addFilter(RedactingFilter())
        stream.setLevel(level.upper())
        logger.addHandler(stream)
    _CONFIGURED = True
    return logger


def reset_logging_for_tests() -> None:
    global _CONFIGURED
    logger = logging.getLogger("scholarforge")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    _CONFIGURED = False


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"scholarforge.{name}" if not name.startswith("scholarforge") else name)
