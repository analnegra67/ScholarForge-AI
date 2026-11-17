from __future__ import annotations

from enum import StrEnum


class NodeKind(StrEnum):
    VOLUME = "volume"
    PART = "part"
    CHAPTER = "chapter"
    SECTION = "section"
    BLOCK = "block"


class NodeStatus(StrEnum):
    PLANNED = "planned"
    DRAFTED = "drafted"
    REVISING = "revising"
    VERIFIED = "verified"  # all automatic checks passed, no open blocking issues
    NEEDS_REVIEW = "needs_review"  # stop criterion hit with unresolved issues -> human/expert review
    FINAL = "final"


class TaskStatus(StrEnum):
    PENDING = "pending"
    LEASED = "leased"
    DONE = "done"
    FAILED = "failed"  # attempts exhausted
    CANCELLED = "cancelled"
    BLOCKED = "blocked"  # waits for a user decision (e.g. paid image generation)


class TaskActor(StrEnum):
    HOST = "host"  # the host AI (Claude Code / Codex) must do it
    ENGINE = "engine"  # ScholarForge does it locally (network checks, rendering...)
    USER = "user"  # a human decision is needed


class ClaimStatus(StrEnum):
    VERIFIED = "verified"
    SUPPORTED = "supported"
    CONTESTED = "contested"
    UNVERIFIED = "unverified"
    REQUIRES_EXPERT_REVIEW = "requires_expert_review"


class SourceStatus(StrEnum):
    PROPOSED = "proposed"
    PENDING = "pending"  # could not be checked yet (offline / service down)
    VERIFIED = "verified"  # exists and metadata matches
    MISMATCH = "mismatch"  # identifier exists but metadata disagrees
    NOT_FOUND = "not_found"  # identifier does not resolve anywhere -> likely fabricated
    UNVERIFIABLE = "unverifiable"  # no identifier and no confident search match
    RETRACTED = "retracted"
    REJECTED = "rejected"  # dropped by the host/user


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class IssueStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    ACCEPTED = "accepted"  # acknowledged, no change (wontfix)
    EXPERT_REVIEW = "expert_review"


class CheckOutcome(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"
    ERROR = "error"
    SKIPPED = "skipped"


class FigureStatus(StrEnum):
    REQUESTED = "requested"
    RENDERED = "rendered"
    FAILED = "failed"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"


SEVERITY_RANK = {Severity.INFO: 0, Severity.WARNING: 1, Severity.ERROR: 2, Severity.CRITICAL: 3}
