import time

import pytest

from scholarforge.core.intake import params_from_request
from scholarforge.core.tasks import TaskQueue
from scholarforge.errors import InvalidInput, LeaseConflict, ProjectExists, ProjectNotFound
from scholarforge.models.enums import NodeKind, TaskActor, TaskStatus
from scholarforge.models.project import PrintSettings, ProjectParams
from scholarforge.util.atomic import atomic_write_text, read_text

E2E_PROMPT = (
    "Create a rigorous scientific textbook about the Mathematical Foundations of Deep Learning. "
    "Include calculus, linear algebra, optimization, automatic differentiation, neural networks, proofs, "
    "exercises, figures, references and a print-ready edition."
)


def test_create_project_layout_registry_and_initial_task(project, service):
    root = project.root
    for sub in ("content", "figures", "research", "build", "exports", "reports", ".scholarforge"):
        assert (root / sub).is_dir()
    assert (root / "scholarforge.yaml").is_file()
    assert (root / ".scholarforge" / "project.db").is_file()
    tasks = project.db.tasks.list()
    assert [t.kind for t in tasks] == ["research.plan"]
    assert project.checkpoints.list()[0]["reason"] == "project created"
    listed = service.list()
    assert any(p["id"] == project.id for p in listed)


def test_open_by_id_path_slug_and_unknown(project, service):
    for ref in (project.id, str(project.root), project.root.name):
        p = service.open(ref)
        assert p.id == project.id
        p.close()
    with pytest.raises(ProjectNotFound):
        service.open("prj_01ZZZZZZZZZZZZZZZZZZZZZZZZ")


def test_registry_rebuilt_after_loss(project, service, sf_home):
    service.registry.path.unlink()
    p = service.open(project.id)  # rediscovered by scanning projects_root
    assert p.root == project.root
    p.close()


def test_project_in_explicit_location(service, tmp_path):
    target = tmp_path / "mybook"
    p = service.create(ProjectParams(topic="Topology basics"), location=target)
    assert p.root == target.resolve()
    p.close()
    with pytest.raises(ProjectExists):
        service.create(ProjectParams(topic="Topology basics"), location=target)
    nonempty = tmp_path / "busy"
    nonempty.mkdir()
    (nonempty / "file.txt").write_text("x")
    with pytest.raises(InvalidInput):
        service.create(ProjectParams(topic="Topology basics"), location=nonempty)


def test_duplicate_titles_get_unique_folders(service):
    a = service.create(ProjectParams(topic="Quantum Gravity"))
    b = service.create(ProjectParams(topic="Quantum Gravity"))
    assert a.root != b.root and b.root.name.endswith("-2")
    a.close()
    b.close()


def test_print_settings_validation():
    ps = PrintSettings(trim="170x240mm", paper="custom", ppi=444)
    w, h = ps.trim_mm()
    assert (round(w), round(h)) == (170, 240)
    assert abs(ps.per_page_thickness_mm() - 25.4 / 444) < 1e-9
    with pytest.raises(ValueError):
        PrintSettings(trim="huge")
    with pytest.raises(ValueError):
        PrintSettings(paper="custom")


def test_task_queue_lease_complete_reject_conflict(project):
    q = TaskQueue(project.db, lease_minutes=1, default_max_attempts=2)
    project.db.tasks.cancel_open()
    q.enqueue("custom.kind", title="t")
    t = q.lease_next(TaskActor.HOST)
    assert t.kind == "custom.kind" and t.status == TaskStatus.LEASED and t.max_attempts == 2
    assert q.lease_next(TaskActor.HOST) is None  # only one task, already leased
    with pytest.raises(LeaseConflict):
        q.verify_lease(t.id, "wrong-token")
    q.verify_lease(t.id, t.lease_token)
    r = q.reject(t.id, [{"code": "X", "message": "fix it"}])
    # a rejected submission keeps the lease, so the host can fix and resubmit with the same token
    assert r.status == TaskStatus.LEASED and r.attempts == 1 and r.feedback[0]["code"] == "X"
    assert r.lease_token == t.lease_token
    q.verify_lease(t.id, t.lease_token)
    r2 = q.reject(t.id, [{"code": "X", "message": "still wrong"}])
    assert r2.status == TaskStatus.FAILED
    with pytest.raises(LeaseConflict):
        q.verify_lease(t.id, t.lease_token)  # failed tasks accept no further submissions


def test_lease_expiry_and_release_on_resume(project):
    q = TaskQueue(project.db, lease_minutes=1)
    t = q.lease_next(TaskActor.HOST)
    # force expiry
    project.db.db.execute("UPDATE tasks SET lease_expires_at = '2000-01-01T00:00:00.000Z' WHERE id = ?", (t.id,))
    again = q.lease_next(TaskActor.HOST)
    assert again.id == t.id and again.lease_token != t.lease_token
    assert q.release_host_leases() == 1
    assert project.db.tasks.get(t.id).status == TaskStatus.PENDING


def test_dependencies_respected(project):
    q = TaskQueue(project.db)
    project.db.tasks.cancel_open()
    first = q.enqueue("a", title="first")
    second = q.enqueue("b", title="second", depends_on=[first.id], priority=1)
    leased = q.lease_next(TaskActor.HOST)
    assert leased.id == first.id  # 'second' has higher priority but its dependency is open
    q.complete(first.id, {"ok": True})
    assert q.lease_next(TaskActor.HOST).id == second.id


def test_enqueue_is_idempotent_per_kind_and_target(project):
    q = TaskQueue(project.db)
    a = q.enqueue("draft.block", target_id="nd_x")
    b = q.enqueue("draft.block", target_id="nd_x")
    assert a.id == b.id


def test_content_versions_and_external_edit(project):
    ch = project.db.nodes.create(kind=NodeKind.CHAPTER, parent_id=None, position=0, title="Calculus", slug="calculus")
    sec = project.db.nodes.create(kind=NodeKind.SECTION, parent_id=ch.id, position=0, title="Limits", slug="limits")
    blk = project.db.nodes.create(kind=NodeKind.BLOCK, parent_id=sec.id, position=0, title="Definition", slug="definition")
    n1 = project.content.write_block(blk.id, "First version of the text.", origin="host")
    assert n1.version == 1 and n1.content_path == "content/01-calculus/01-limits/01-definition.md"
    same = project.content.write_block(blk.id, "First version of the text.", origin="host")
    assert same.version == 1  # identical content does not create a version
    # a human edits the file directly
    atomic_write_text(project.root / n1.content_path, "Edited by a human.")
    edits = project.content.detect_external_edits()
    assert [e.node_id for e in edits] == [blk.id]
    n3 = project.content.write_block(blk.id, "Third version.", origin="host")
    assert n3.version == 3
    origins = [v["origin"] for v in project.db.nodes.versions(blk.id)]
    assert origins == ["host", "external_edit", "host"]
    restored = project.content.restore_version(blk.id, 2)
    assert read_text(project.root / restored.content_path) == "Edited by a human.\n"


def test_checkpoint_restore_db_and_files(project):
    ch = project.db.nodes.create(kind=NodeKind.CHAPTER, parent_id=None, position=0, title="A", slug="a")
    sec = project.db.nodes.create(kind=NodeKind.SECTION, parent_id=ch.id, position=0, title="S", slug="s")
    blk = project.db.nodes.create(kind=NodeKind.BLOCK, parent_id=sec.id, position=0, title="B", slug="b")
    project.content.write_block(blk.id, "Good text.", origin="host")
    cp = project.checkpoints.create("before risky change", auto=False)
    time.sleep(0.01)
    # damage: overwrite content, add a stray file, change db
    project.content.write_block(blk.id, "Broken text.", origin="host")
    (project.root / "content" / "stray.md").write_text("stray", encoding="utf-8")
    project.db.meta.set("phase", "garbage")
    safety = project.checkpoints.restore(cp)
    node = project.db.nodes.get(blk.id)
    assert read_text(project.root / node.content_path) == "Good text.\n"
    assert node.version == 1
    assert not (project.root / "content" / "stray.md").exists()
    assert project.db.meta.get("phase") != "garbage"
    ids = [c["id"] for c in project.checkpoints.list()]
    assert cp in ids and safety in ids  # index rebuilt from disk


def test_checkpoint_pruning_keeps_manual(project):
    project.checkpoints.keep_auto = 2
    manual = project.checkpoints.create("manual", auto=False)
    for i in range(4):
        project.checkpoints.create(f"auto {i}")
    cps = project.checkpoints.list()
    assert sum(1 for c in cps if c["auto"]) == 2
    assert any(c["id"] == manual for c in cps)


def test_intake_parses_the_e2e_prompt():
    p = params_from_request(E2E_PROMPT)
    assert p.topic == "the Mathematical Foundations of Deep Learning" or p.topic == "Mathematical Foundations of Deep Learning"
    assert p.book_type == "textbook" and p.depth == "rigorous" and p.language == "en"
    assert "print" in p.formats
    assert {"proofs", "exercises", "figures", "references"} <= set(p.elements)
    lowered = [t.lower() for t in p.required_topics]
    for topic in ("calculus", "linear algebra", "optimization", "automatic differentiation", "neural networks"):
        assert topic in lowered


def test_intake_russian_request():
    p = params_from_request("Создай максимально глубокую монографию по квантовой гравитации")
    assert p.language == "ru" and p.book_type == "monograph" and p.depth == "exhaustive"
    assert "квантовой гравитации" in p.topic
