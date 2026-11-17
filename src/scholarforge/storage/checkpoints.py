"""Local checkpoints: consistent snapshots of the database plus project files.

Layout (inside ``<project>/.scholarforge/``)::

    objects/ab/abcdef...        content-addressed file store (deduplicated)
    checkpoints/<cp_id>/project.db
    checkpoints/<cp_id>/manifest.json   {id, reason, auto, created_at, files: {relpath: sha256}}

The filesystem is the source of truth; the ``checkpoints`` table is an index that
is re-synchronised from disk after a restore.
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from scholarforge.errors import NotFound
from scholarforge.logging_setup import get_logger
from scholarforge.storage.repo import ProjectDB
from scholarforge.util.atomic import atomic_write_bytes, atomic_write_json, read_json
from scholarforge.util.hashing import sha256_file
from scholarforge.util.ids import new_id
from scholarforge.util.timeutil import now_iso

log = get_logger("checkpoints")

TRACKED_FILES = ("scholarforge.yaml",)
TRACKED_DIRS = ("content", "figures", "research", "assets")


class CheckpointManager:
    def __init__(self, root: Path, pdb: ProjectDB, keep_auto: int = 25):
        self.root = Path(root)
        self.pdb = pdb
        self.keep_auto = keep_auto
        self.state = self.root / ".scholarforge"
        self.objects = self.state / "objects"
        self.cp_dir = self.state / "checkpoints"

    def _tracked(self) -> list[Path]:
        files: list[Path] = []
        for name in TRACKED_FILES:
            p = self.root / name
            if p.is_file():
                files.append(p)
        for d in TRACKED_DIRS:
            base = self.root / d
            if base.is_dir():
                files.extend(p for p in sorted(base.rglob("*")) if p.is_file() and not p.name.startswith(".~"))
        return files

    def _store_object(self, path: Path) -> str:
        digest = sha256_file(path)
        target = self.objects / digest[:2] / digest
        if not target.exists():
            atomic_write_bytes(target, path.read_bytes())
        return digest

    def create(self, reason: str, *, auto: bool = True) -> str:
        cp_id = new_id("cp")
        target_dir = self.cp_dir / cp_id
        target_dir.mkdir(parents=True, exist_ok=True)
        self.pdb.db.backup_to(target_dir / "project.db")
        files = {p.relative_to(self.root).as_posix(): self._store_object(p) for p in self._tracked()}
        manifest = {"id": cp_id, "reason": reason, "auto": auto, "created_at": now_iso(), "files": files}
        atomic_write_json(target_dir / "manifest.json", manifest)
        self.pdb.checkpoints.add(cp_id, reason, auto, target_dir.relative_to(self.root).as_posix(), {"files": len(files)})
        self.pdb.events.log("checkpoint.created", "checkpoint", cp_id, {"reason": reason, "files": len(files)})
        if auto:
            self.prune()
        return cp_id

    def list(self) -> list[dict]:
        out = []
        if not self.cp_dir.exists():
            return out
        for d in self.cp_dir.iterdir():
            mf = d / "manifest.json"
            if mf.is_file():
                m = read_json(mf)
                out.append({k: m[k] for k in ("id", "reason", "auto", "created_at")} | {"files": len(m["files"])})
        out.sort(key=lambda m: (m["created_at"], m["id"]), reverse=True)
        return out

    def prune(self) -> list[str]:
        removed = []
        autos = [c for c in self.list() if c["auto"]]
        for cp in autos[self.keep_auto :]:
            shutil.rmtree(self.cp_dir / cp["id"], ignore_errors=True)
            self.pdb.checkpoints.delete(cp["id"])
            removed.append(cp["id"])
        if removed:
            self._gc_objects()
        return removed

    def _gc_objects(self) -> None:
        live: set[str] = set()
        for cp in self.list():
            live.update(read_json(self.cp_dir / cp["id"] / "manifest.json")["files"].values())
        if not self.objects.exists():
            return
        for sub in self.objects.iterdir():
            for obj in sub.iterdir():
                if obj.name not in live:
                    obj.unlink(missing_ok=True)

    def restore(self, cp_id: str) -> str:
        """Restore database and tracked files; returns the id of the safety checkpoint taken first."""
        cp_path = self.cp_dir / cp_id
        if not (cp_path / "manifest.json").is_file():
            raise NotFound(f"checkpoint not found: {cp_id}")
        safety = self.create(f"before restore of {cp_id}", auto=False)
        manifest = read_json(cp_path / "manifest.json")

        # 1. database: copy snapshot into the live database through the backup API (WAL-safe)
        src = sqlite3.connect(str(cp_path / "project.db"))
        try:
            src.backup(self.pdb.db.conn)
        finally:
            src.close()

        # 2. files: restore manifest entries; move untracked newer files to trash
        wanted: dict[str, str] = manifest["files"]
        trash = self.state / "trash" / now_iso().replace(":", "-")
        for path in self._tracked():
            rel = path.relative_to(self.root).as_posix()
            if rel not in wanted:
                dest = trash / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(path), str(dest))
        for rel, digest in wanted.items():
            obj = self.objects / digest[:2] / digest
            target = self.root / rel
            if target.exists() and sha256_file(target) == digest:
                continue
            if not obj.exists():
                log.error("checkpoint %s is missing object %s for %s", cp_id, digest, rel)
                continue
            atomic_write_bytes(target, obj.read_bytes())

        # 3. re-index checkpoints from disk (the restored DB does not know newer ones)
        self._reindex()
        self.pdb.events.log("checkpoint.restored", "checkpoint", cp_id, {"safety_checkpoint": safety})
        return safety

    def _reindex(self) -> None:
        known = {c["id"] for c in self.pdb.checkpoints.list()}
        on_disk = self.list()
        disk_ids = {c["id"] for c in on_disk}
        for cid in known - disk_ids:
            self.pdb.checkpoints.delete(cid)
        for cp in on_disk:
            if cp["id"] not in known:
                self.pdb.checkpoints.add(
                    cp["id"], cp["reason"], cp["auto"], f".scholarforge/checkpoints/{cp['id']}", {"files": cp["files"]}
                )
