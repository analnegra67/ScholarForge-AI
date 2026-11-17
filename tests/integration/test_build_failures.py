"""A failing renderer must never damage the book sources, and other formats must still be attempted."""

from pathlib import Path

from scholarforge.models.enums import NodeKind
from scholarforge.publishing import builder as builder_mod
from scholarforge.publishing.quarto import RenderRun
from scholarforge.util.hashing import sha256_file


def test_failed_render_keeps_sources_and_reports(project, monkeypatch, tmp_path):
    db = project.db
    ch = db.nodes.create(kind=NodeKind.CHAPTER, parent_id=None, position=0, title="Chapter", slug="chapter")
    sec = db.nodes.create(kind=NodeKind.SECTION, parent_id=ch.id, position=0, title="Section", slug="section")
    blk = db.nodes.create(kind=NodeKind.BLOCK, parent_id=sec.id, position=0, title="Block", slug="block")
    node = project.content.write_block(blk.id, "Some text with $x^2$.", origin="host")
    before = {p: sha256_file(p) for p in (project.root / "content").rglob("*") if p.is_file()}
    calls = []

    def fake_render(project_dir: Path, fmt: str, logs_dir: Path, **kw):
        calls.append(fmt)
        logs_dir.mkdir(parents=True, exist_ok=True)
        log = logs_dir / f"render-{fmt}.log"
        log.write_text("! Undefined control sequence.", encoding="utf-8")
        ok = fmt == "html"
        if ok:
            out = project_dir / "_output"
            out.mkdir(exist_ok=True)
            (out / "index.html").write_text("<html><body>ok</body></html>", encoding="utf-8")
        return RenderRun(fmt, ok, 0 if ok else 1, 0.1, log, "" if ok else "! Undefined control sequence.")

    monkeypatch.setattr(builder_mod, "render", fake_render)
    manifest = builder_mod.BookBuilder(project).build(formats=["html", "pdf", "epub"])
    assert calls == ["html", "epub", "pdf"]
    assert manifest["status"] == "partial"
    assert manifest["results"]["html"]["ok"] and not manifest["results"]["pdf"]["ok"]
    assert "Undefined control sequence" in manifest["results"]["pdf"]["error"]
    after = {p: sha256_file(p) for p in (project.root / "content").rglob("*") if p.is_file()}
    assert before == after
    assert db.nodes.get(node.id).version == 1
    assert (project.exports_dir / "manifest.json").exists()
    assert (project.reports_dir / "build-report.md").exists()
    # the lock is released even after failures
    assert not (project.state_dir / "locks" / "build.lock").exists()
