from __future__ import annotations

import copy
from importlib import resources
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from scholarforge import paths
from scholarforge.errors import ConfigError, NotFound
from scholarforge.util.text import normalize_for_match

Level = Literal["off", "optional", "recommended", "required"]


class VerificationPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    math_symbolic: Level = "optional"
    numeric_checks: Level = "optional"
    dimensional_analysis: Level = "off"
    formal_proof: Level = "off"
    claim_support: Level = "recommended"


class SourcePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    databases: list[str] = Field(default_factory=lambda: ["crossref", "openalex", "arxiv", "semantic_scholar",
                                                         "openlibrary"])
    arxiv_categories: list[str] = Field(default_factory=list)
    min_sources: int = 3
    prefer_primary: bool = True


class ClaimPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    require_citation_kinds: list[str] = Field(default_factory=lambda: ["empirical", "historical", "statistical"])
    theorem_needs_proof_or_citation: bool = True
    speculative_policy: Literal["label", "strict"] = "label"
    high_stakes_kinds: list[str] = Field(default_factory=list)


class Presentation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    environments: list[str] = Field(default_factory=lambda: ["definition", "example", "remark"])
    conventions: list[str] = Field(default_factory=list)
    exercises: bool = True


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    extends: str | None = None
    description: str = ""
    keywords: list[str] = Field(default_factory=list)
    evidence_types: list[str] = Field(default_factory=list)
    verification: VerificationPolicy = Field(default_factory=VerificationPolicy)
    sources: SourcePolicy = Field(default_factory=SourcePolicy)
    claims: ClaimPolicy = Field(default_factory=ClaimPolicy)
    presentation: Presentation = Field(default_factory=Presentation)
    writing_guidance: str = ""
    safety_notes: list[str] = Field(default_factory=list)
    lineage: list[str] = Field(default_factory=list, description="resolved inheritance chain (root first)")

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "lineage": self.lineage,
            "verification": self.verification.model_dump(), "claims": self.claims.model_dump(),
            "environments": self.presentation.environments, "conventions": self.presentation.conventions,
            "writing_guidance": self.writing_guidance, "safety_notes": self.safety_notes,
            "evidence_types": self.evidence_types,
        }


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


class ProfileRegistry:
    def __init__(self, extra_dirs: list[Path] | None = None):
        self._raw: dict[str, dict] = {}
        self._origin: dict[str, str] = {}
        for entry in resources.files("scholarforge.profiles.builtin").iterdir():
            if entry.name.endswith(".yaml"):
                self._add_raw(yaml.safe_load(entry.read_text(encoding="utf-8")), f"builtin:{entry.name}")
        dirs = list(extra_dirs or [])
        user_dir = paths.config_dir() / "profiles"
        if user_dir.is_dir():
            dirs.append(user_dir)
        for d in dirs:
            for file in sorted(Path(d).glob("*.yaml")):
                try:
                    self._add_raw(yaml.safe_load(file.read_text(encoding="utf-8")), str(file))
                except yaml.YAMLError as exc:
                    raise ConfigError(f"invalid profile file {file}: {exc}") from exc
        self._resolved: dict[str, Profile] = {}
        for pid in self._raw:
            self.get(pid)  # validate everything eagerly

    def _add_raw(self, data: Any, origin: str) -> None:
        if not isinstance(data, dict) or "id" not in data:
            raise ConfigError(f"profile without id in {origin}")
        self._raw[data["id"]] = data
        self._origin[data["id"]] = origin

    def ids(self) -> list[str]:
        return sorted(self._raw)

    def get(self, profile_id: str) -> Profile:
        if profile_id in self._resolved:
            return self._resolved[profile_id]
        if profile_id not in self._raw:
            raise NotFound(f"unknown domain profile '{profile_id}'", hint=f"known profiles: {', '.join(self.ids())}")
        chain: list[str] = []
        current: str | None = profile_id
        while current:
            if current in chain:
                raise ConfigError(f"profile inheritance cycle: {' -> '.join(chain + [current])}")
            if current not in self._raw:
                raise ConfigError(f"profile '{chain[-1]}' extends unknown profile '{current}'")
            chain.append(current)
            current = self._raw[current].get("extends")
        merged: dict = {}
        for pid in reversed(chain):
            layer = {k: v for k, v in self._raw[pid].items() if k not in ("id", "name", "extends", "keywords")}
            merged = _deep_merge(merged, layer)
        raw = self._raw[profile_id]
        merged.update({"id": profile_id, "name": raw.get("name", profile_id), "extends": raw.get("extends"),
                       "keywords": raw.get("keywords", []), "lineage": list(reversed(chain))})
        try:
            profile = Profile.model_validate(merged)
        except Exception as exc:
            raise ConfigError(f"invalid profile '{profile_id}' ({self._origin[profile_id]}): {exc}") from exc
        self._resolved[profile_id] = profile
        return profile

    def with_overrides(self, profile_id: str, overrides: dict | None) -> Profile:
        base = self.get(profile_id)
        if not overrides:
            return base
        data = _deep_merge(base.model_dump(), overrides)
        data["id"], data["lineage"] = base.id, base.lineage
        return Profile.model_validate(data)

    def match(self, text: str) -> tuple[str, list[dict[str, Any]]]:
        """Choose the best profile for a topic; returns (profile_id, ranked scores)."""
        norm = " " + normalize_for_match(text) + " "
        scores = []
        for pid, raw in self._raw.items():
            hits = []
            for kw in raw.get("keywords", []):
                nkw = normalize_for_match(kw)
                if nkw and (" " + nkw) in norm:
                    hits.append(kw)
            depth = len(self.get(pid).lineage)
            score = sum(len(normalize_for_match(h).split()) for h in hits)
            scores.append({"id": pid, "score": score, "depth": depth, "hits": hits})
        scores.sort(key=lambda s: (s["score"], s["depth"]), reverse=True)
        best = scores[0] if scores and scores[0]["score"] > 0 else None
        return (best["id"] if best else "generic-science"), scores[:5]


_REGISTRY: ProfileRegistry | None = None


def get_registry(refresh: bool = False) -> ProfileRegistry:
    global _REGISTRY
    if _REGISTRY is None or refresh:
        _REGISTRY = ProfileRegistry()
    return _REGISTRY
