"""Authoring loop with the scripted host and recorded API responses (no rendering)."""

import pytest

from scholarforge.core.intake import params_from_request
from scholarforge.core.workflow import Coordinator
from scholarforge.e2e.scenario import PROMPT, drive
from scholarforge.e2e.scripted_host import ScriptedHost, fixture_dir
from scholarforge.science.sources import manager as source_manager
from scholarforge.science.sources.http import FixtureTransport


@pytest.fixture
def replay(monkeypatch):
    monkeypatch.setattr(source_manager, "DEFAULT_TRANSPORT", FixtureTransport(fixture_dir() / "http"))


@pytest.fixture
def dl_project(service, settings, replay):
    settings.network.enabled = True  # served from recorded responses
    params = params_from_request(PROMPT)
    params.target_pages = 5
    p = service.create(params)
    yield p
    p.close()


def test_full_authoring_loop(dl_project):
    wf = Coordinator(dl_project)
    host = ScriptedHost()
    log = []
    assert drive(wf, host, log=log) == "ready_to_build"
    kinds = [e["kind"] for e in log]
    assert kinds[0] == "research.plan" and "sources.review" in kinds and "architecture.outline" in kinds
    assert kinds.count("draft.block") == 6 and "revise.block" in kinds and "review.chapter" in kinds
    db = dl_project.db
    assert all(b.status.value in ("verified", "needs_review") for b in db.nodes.blocks())
    statuses = {s.citekey: s.status.value for s in db.sources.list()}
    assert statuses["doe2019"] == "rejected" and statuses["silver2016"] == "verified"
    claims = db.claims.status_counts()
    assert claims.get("verified", 0) >= 5 and claims.get("contested", 0) >= 1
    assert wf.phase() == "ready_to_build"
    # the checkpoint trail exists for every phase change
    reasons = [c["reason"] for c in dl_project.checkpoints.list()]
    assert any("architecture" in r for r in reasons) and any("->" in r for r in reasons)


def test_invalid_submissions_get_actionable_feedback(dl_project):
    wf = Coordinator(dl_project)
    task = wf.next_task()["task"]
    assert task["kind"] == "research.plan" and task["output_schema"]["type"] == "object"
    out = wf.submit_task(task["task_id"], task["lease_token"], {"field_name": "x"})
    assert not out["accepted"] and any(f["code"] == "SCHEMA" for f in out["feedback"])
    assert out["attempts_left"] == 2
    # stale lease token is refused
    from scholarforge.errors import LeaseConflict

    with pytest.raises(LeaseConflict):
        wf.submit_task(task["task_id"], "bogus", {})


def test_block_validation_rejects_fabricated_citation_and_bad_math(dl_project):
    wf = Coordinator(dl_project)
    host = ScriptedHost()
    # run until the first draft task, then submit a broken block
    while True:
        r = wf.next_task()
        if r["status"] == "engine_busy":
            continue
        t = r["task"]
        if t["kind"] == "draft.block":
            break
        out = wf.submit_task(t["task_id"], t["lease_token"], host.answer(t))
        assert out["accepted"], out
    good = host.answer(t)
    broken = dict(good)
    broken["markdown"] = good["markdown"] + "\n\nAs shown by [@doe2019] and [@nobody2020].\n\n## Bad heading\n"
    broken["math_checks"] = good["math_checks"] + [
        {"id": "wrong", "kind": "derivative", "expr": "x**3", "var": "x", "expected": "3*x"}]
    out = wf.submit_task(t["task_id"], t["lease_token"], broken)
    codes = {f["code"] for f in out["feedback"] if f["severity"] == "error"}
    assert not out["accepted"]
    assert {"CITATION_NOT_USABLE", "UNKNOWN_CITATION", "RESERVED_HEADING_LEVEL", "MATH_CHECK_FAILED"} <= codes
    out = wf.submit_task(t["task_id"], t["lease_token"], good)
    assert out["accepted"], out["feedback"]


def test_resume_releases_leases_and_detects_manual_edits(dl_project, service):
    wf = Coordinator(dl_project)
    host = ScriptedHost()
    drive(wf, host, stop_after_drafts=1)
    leased = wf.queue.current()
    block = next(b for b in dl_project.db.nodes.blocks() if b.content_path)
    path = dl_project.root / block.content_path
    path.write_text(path.read_text(encoding="utf-8") + "\nA manual note added by the author.\n", encoding="utf-8")
    pid = dl_project.id
    dl_project.close()
    p2 = service.open(pid)
    try:
        info = Coordinator(p2).resume()
        assert info["released_leases"] == 1 and info["external_edits"] == [block.id]
        assert Coordinator(p2).next_task()["task"]["task_id"] == leased.id
        versions = p2.db.nodes.versions(block.id)
        assert versions[-1]["origin"] == "external_edit"
    finally:
        p2.close()
