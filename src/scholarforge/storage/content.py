"""Block sources as plain Markdown files, with version history.

The files under ``content/`` are the authoritative, human-editable text of the
book. The database stores their hash; when a file changes outside ScholarForge
(a human edit) the change is detected, recorded as a new version and the block
is scheduled for re-verification — nothing is silently overwritten.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from scholarforge.errors import NotFound
from scholarforge.models.entities import Node
from scholarforge.models.enums import NodeKind
from scholarforge.storage.repo import ProjectDB
from scholarforge.util.atomic import atomic_write_text, read_text
from scholarforge.util.hashing import sha256_text
from scholarforge.util.text import word_count


@dataclass
class ExternalEdit:
    node_id: str
    path: str
    old_hash: str | None
    new_hash: str


class ContentStore:
    def __init__(self, root: Path, pdb: ProjectDB):
        self.root = Path(root)
        self.pdb = pdb
        self.content_dir = self.root / "content"
        self.history_dir = self.root / ".scholarforge" / "history"

    # -- paths ---------------------------------------------------------------------
    SLUG_IN_PATH = 24  # keeps nested paths well below the 260-character Windows limit

    def block_relpath(self, node: Node) -> str:
        if node.content_path:
            return node.content_path

        def short(slug: str) -> str:
            return slug[: self.SLUG_IN_PATH].rstrip("-") or "x"

        parts = [f"{node.position + 1:02d}-{short(node.slug)}.md"]
        for anc in self.pdb.nodes.ancestors(node.id):
            parts.append(f"{anc.position + 1:02d}-{short(anc.slug)}")
        return "/".join(["content", *reversed(parts)])

    def abspath(self, relpath: str) -> Path:
        return self.root / Path(relpath)

    # -- read / write -------------------------------------------------------------------
    def read_block(self, node_id: str) -> str | None:
        node = self.pdb.nodes.get(node_id)
        if not node.content_path:
            return None
        path = self.abspath(node.content_path)
        if not path.exists():
            return None
        return read_text(path)

    def write_block(self, node_id: str, markdown: str, *, origin: str, note: str | None = None) -> Node:
        node = self.pdb.nodes.get(node_id)
        if node.kind != NodeKind.BLOCK:
            raise NotFound(f"node {node_id} is a {node.kind.value}, not a block")
        self.sync_external_edit(node)
        node = self.pdb.nodes.get(node_id)
        text = markdown.replace("\r\n", "\n").rstrip() + "\n"
        new_hash = sha256_text(text)
        if node.content_hash == new_hash and node.content_path and self.abspath(node.content_path).exists():
            return node  # identical content: no new version
        relpath = self.block_relpath(node)
        version = node.version + 1
        atomic_write_text(self.abspath(relpath), text)
        hist_rel = f".scholarforge/history/{node.id}/v{version:04d}.md"
        atomic_write_text(self.root / hist_rel, text)
        self.pdb.nodes.add_version(node.id, version, new_hash, hist_rel, origin, note)
        return self.pdb.nodes.update(
            node.id, content_path=relpath, content_hash=new_hash, version=version, word_count=word_count(text)
        )

    def sync_external_edit(self, node: Node) -> ExternalEdit | None:
        """Record a manual edit of the block file as a version (origin='external_edit')."""
        if not node.content_path:
            return None
        path = self.abspath(node.content_path)
        if not path.exists():
            return None
        text = read_text(path)
        current = sha256_text(text)
        if current == node.content_hash:
            return None
        version = node.version + 1
        hist_rel = f".scholarforge/history/{node.id}/v{version:04d}.md"
        atomic_write_text(self.root / hist_rel, text)
        self.pdb.nodes.add_version(node.id, version, current, hist_rel, "external_edit", "detected change on disk")
        self.pdb.nodes.update(node.id, content_hash=current, version=version, word_count=word_count(text))
        self.pdb.events.log("content.external_edit", "node", node.id, {"old": node.content_hash, "new": current})
        return ExternalEdit(node.id, node.content_path, node.content_hash, current)

    def detect_external_edits(self) -> list[ExternalEdit]:
        edits = []
        for node in self.pdb.nodes.blocks():
            edit = self.sync_external_edit(node)
            if edit:
                edits.append(edit)
        return edits

    def restore_version(self, node_id: str, version: int) -> Node:
        for v in self.pdb.nodes.versions(node_id):
            if v["version"] == version:
                text = read_text(self.root / v["path"])
                return self.write_block(node_id, text, origin="restore", note=f"restored v{version}")
        raise NotFound(f"version {version} of {node_id} not found")
