"""Task handlers implementing the book-creation workflow.

``HANDLERS`` maps a task kind to its handler. The registry is filled by importing
the handler modules (see :mod:`scholarforge.workflow.handlers`).
"""

from __future__ import annotations

from typing import Any

HANDLERS: dict[str, Any] = {}


def register(handler: Any) -> Any:
    HANDLERS[handler.kind] = handler
    return handler


def _load() -> None:
    try:
        from scholarforge.workflow import handlers  # noqa: F401  (registers on import)
    except ModuleNotFoundError as exc:  # pragma: no cover - only during partial development
        if exc.name != "scholarforge.workflow.handlers":
            raise


_load()
