"""ScholarForge: an AI-native scientific publishing engine.

The host AI (Claude Code, Codex) is the reasoning engine. ScholarForge is the
orchestration, verification and publishing engine: it plans work as tasks,
stores everything locally, verifies what can be verified, and typesets books.
ScholarForge never calls a language model itself.
"""

from scholarforge.version import __version__

__all__ = ["__version__"]
