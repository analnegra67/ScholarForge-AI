"""Persistent entities (rows of the project database) as typed models."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from scholarforge.models.enums import (
    CheckOutcome,
    ClaimStatus,
    FigureStatus,
    IssueStatus,
    NodeKind,
    NodeStatus,
    Severity,
    SourceStatus,
    TaskActor,
    TaskStatus,
)

_JSON_FIELDS = {
    "plan", "payload", "depends_on", "result", "feedback", "authors", "verification", "csl",
    "citations", "details", "aliases", "spec", "outputs", "provenance", "qc", "data", "formats",
    "manifest", "check_ids", "report",
}


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    for key, value in list(data.items()):
        if key in _JSON_FIELDS and isinstance(value, str):
            try:
                data[key] = json.loads(value)
            except json.JSONDecodeError:
                pass
    return data


class _Entity(BaseModel):
    model_config = ConfigDict(extra="ignore", use_enum_values=False)

    @classmethod
    def from_row(cls, row: sqlite3.Row | None):
        data = row_to_dict(row)
        return None if data is None else cls.model_validate(data)


class Node(_Entity):
    id: str
    parent_id: str | None = None
    kind: NodeKind
    position: int
    title: str
    slug: str
    summary: str | None = None
    plan: dict[str, Any] = Field(default_factory=dict)
    status: NodeStatus = NodeStatus.PLANNED
    content_path: str | None = None
    content_hash: str | None = None
    version: int = 0
    word_count: int = 0
    revision_rounds: int = 0
    written_summary: str | None = None
    created_at: str
    updated_at: str


class Task(_Entity):
    id: str
    kind: str
    actor: TaskActor
    status: TaskStatus
    title: str = ""
    priority: int = 100
    target_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    attempts: int = 0
    max_attempts: int = 3
    lease_token: str | None = None
    lease_expires_at: str | None = None
    result: dict[str, Any] | None = None
    feedback: list[dict[str, Any]] | None = None
    seq: int = 0
    created_at: str
    updated_at: str
    completed_at: str | None = None


class Source(_Entity):
    id: str
    citekey: str
    kind: str = "article"
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None
    publisher: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    isbn: str | None = None
    url: str | None = None
    openalex_id: str | None = None
    status: SourceStatus = SourceStatus.PROPOSED
    verification: dict[str, Any] = Field(default_factory=dict)
    abstract: str | None = None
    relevance: str | None = None
    proposed_by: str = "host"
    created_at: str
    updated_at: str


class Claim(_Entity):
    id: str
    node_id: str
    label: str | None = None
    kind: str
    text: str
    citations: list[str] = Field(default_factory=list)
    check_ids: list[str] = Field(default_factory=list)
    speculative: bool = False
    status: ClaimStatus = ClaimStatus.UNVERIFIED
    status_reason: str | None = None
    created_at: str
    updated_at: str


class Term(_Entity):
    id: str
    term: str
    normalized: str
    definition: str
    defined_in: str | None = None
    aliases: list[str] = Field(default_factory=list)
    version: int = 1
    created_at: str
    updated_at: str


class Notation(_Entity):
    id: str
    symbol: str
    normalized: str
    meaning: str
    scope: str = "global"
    defined_in: str | None = None
    created_at: str


class Issue(_Entity):
    id: str
    node_id: str | None = None
    severity: Severity
    category: str
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    status: IssueStatus = IssueStatus.OPEN
    origin: str = "engine"
    created_at: str
    resolved_at: str | None = None


class CheckRecord(_Entity):
    id: str
    subject_type: str
    subject_id: str
    check_type: str
    method: str
    independent: bool
    outcome: CheckOutcome
    details: dict[str, Any] = Field(default_factory=dict)
    engine_version: str
    created_at: str


class Figure(_Entity):
    id: str
    node_id: str | None = None
    label: str
    mode: str
    renderer: str
    spec: dict[str, Any] = Field(default_factory=dict)
    caption: str
    alt_text: str
    status: FigureStatus = FigureStatus.REQUESTED
    outputs: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)
    license: str | None = None
    qc: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str
