"""Machine-level index of known projects (``<data_dir>/registry.json``).

The registry is only a convenience index: every project folder is self-describing
(``scholarforge.yaml``), so a lost or stale registry is rebuilt by scanning.
"""

from __future__ import annotations

from pathlib import Path

from scholarforge import paths
from scholarforge.util.atomic import atomic_write_json, read_json, read_yaml
from scholarforge.util.timeutil import now_iso


class ProjectRegistry:
    def __init__(self, path: Path | None = None):
        self.path = path or (paths.data_dir() / "registry.json")

    def _load(self) -> dict:
        if not self.path.exists():
            return {"projects": {}}
        try:
            data = read_json(self.path)
        except Exception:
            # Corrupt registry: keep a copy for forensics and start over (projects are rediscoverable).
            self.path.replace(self.path.with_suffix(".corrupt.json"))
            return {"projects": {}}
        data.setdefault("projects", {})
        return data

    def _save(self, data: dict) -> None:
        atomic_write_json(self.path, data)

    def add(self, project_id: str, root: Path, title: str) -> None:
        data = self._load()
        data["projects"][project_id] = {"path": str(Path(root).resolve()), "title": title, "registered_at": now_iso()}
        self._save(data)

    def touch(self, project_id: str) -> None:
        data = self._load()
        if project_id in data["projects"]:
            data["projects"][project_id]["last_opened"] = now_iso()
            self._save(data)

    def remove(self, project_id: str) -> bool:
        data = self._load()
        removed = data["projects"].pop(project_id, None) is not None
        if removed:
            self._save(data)
        return removed

    def get(self, project_id: str) -> Path | None:
        entry = self._load()["projects"].get(project_id)
        return Path(entry["path"]) if entry else None

    def entries(self) -> dict[str, dict]:
        return self._load()["projects"]

    def scan(self, root: Path) -> int:
        """Register every project folder found directly under ``root``."""
        found = 0
        if not root.exists():
            return 0
        data = self._load()
        for cfg in root.glob("*/scholarforge.yaml"):
            try:
                raw = read_yaml(cfg)
                pid, title = raw["id"], raw["params"].get("title") or raw["params"]["topic"]
            except Exception:
                continue
            if pid not in data["projects"]:
                data["projects"][pid] = {"path": str(cfg.parent.resolve()), "title": title, "registered_at": now_iso()}
                found += 1
        if found:
            self._save(data)
        return found
