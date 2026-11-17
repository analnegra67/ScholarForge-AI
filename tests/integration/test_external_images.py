"""Mode B: paid external image generation (OpenAI Images API) with estimate + explicit confirmation.

The HTTP layer is a mock transport: no request leaves the machine and no money is spent.
"""

import base64
import io
import json

import httpx
import pytest
from PIL import Image

from scholarforge.core.workflow import Coordinator
from scholarforge.errors import ConfirmationRequired
from scholarforge.illustrations.external import pricing, service
from scholarforge.models.enums import FigureStatus, NodeKind, TaskStatus
from scholarforge.models.results import FigureRequest
from scholarforge.workflow.figures import request_figure

KEY = "sk-proj-unit-test-key-1234567890"


def _png() -> str:
    buf = io.BytesIO()
    Image.new("RGB", (1536, 1024), "white").save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


@pytest.fixture
def block(project):
    db = project.db
    ch = db.nodes.create(kind=NodeKind.CHAPTER, parent_id=None, position=0, title="C", slug="c")
    sec = db.nodes.create(kind=NodeKind.SECTION, parent_id=ch.id, position=0, title="S", slug="s")
    return db.nodes.create(kind=NodeKind.BLOCK, parent_id=sec.id, position=0, title="B", slug="b")


REQ = FigureRequest(label="fig-lab", renderer="external", prompt="A clean schematic of a laboratory optical bench",
                    caption="Schematic of the optical bench.",
                    alt_text="A long table with a laser on the left, two lenses and a screen on the right.")


def test_disabled_provider_falls_back_to_native(project, block):
    wf = Coordinator(project)
    request_figure(wf, block, REQ)
    fix = project.db.tasks.open_for("figure.fix", project.db.figures.by_label("fig-lab").id)
    assert fix is not None and "not available" in fix.payload["error"]


def test_estimate_confirm_generate(project, block, monkeypatch):
    monkeypatch.setenv("SCHOLARFORGE_OPENAI_API_KEY", KEY)
    project.settings.images.enabled = True
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"created": 1, "data": [{"b64_json": _png()}],
                                         "usage": {"input_tokens": 50, "output_tokens": 6000,
                                                   "input_tokens_details": {"text_tokens": 50, "image_tokens": 0}}})

    monkeypatch.setattr(service, "TRANSPORT", httpx.MockTransport(handler))
    wf = Coordinator(project)
    request_figure(wf, block, REQ)
    fig = project.db.figures.by_label("fig-lab")
    assert fig.status == FigureStatus.AWAITING_CONFIRMATION
    resp = wf.next_task(run_engine=False)
    assert resp["status"] in ("waiting_for_user", "task")
    blocked = project.db.tasks.list(status=TaskStatus.BLOCKED)
    task = blocked[0]
    est = task.payload["estimate"]
    assert est["available"] and est["approximate"] and est["estimated_usd"] > 0
    assert est["pricing_source"].startswith("https://developers.openai.com")
    with pytest.raises(ConfirmationRequired):
        service.confirm_external_generation(project, task.id, "wrong-token", True)
    assert "url" not in seen  # nothing was sent without a valid confirmation
    out = service.confirm_external_generation(project, task.id, task.payload["confirmation_token"], True)
    assert out["generated"] and out["cost_usd"] == pricing.actual_cost("gpt-image-2.6-sunburst", out["usage"])
    assert seen["auth"] == f"Bearer {KEY}" and seen["url"].endswith("/v1/images/generations")
    assert seen["body"]["model"] == "gpt-image-2.6-sunburst" and seen["body"]["n"] == 1
    fig = project.db.figures.by_label("fig-lab")
    assert fig.status == FigureStatus.RENDERED and "AI-generated" in fig.caption
    assert fig.provenance["ai_generated"] and fig.provenance["approved_by_user"]
    assert (project.root / fig.outputs["png"]).exists() and (project.root / fig.outputs["pdf"]).exists()
    done = project.db.tasks.get(task.id)
    assert done.status == TaskStatus.DONE and "confirmation_token" not in done.payload
    assert project.db.tasks.open_for("figure.review", fig.id) is not None
    # the key never reaches the project files or logs
    for path in project.root.rglob("*"):
        if path.is_file() and path.suffix in (".json", ".yaml", ".md", ".db"):
            assert KEY.encode() not in path.read_bytes()


def test_declined_generation_requests_native_figure(project, block, monkeypatch):
    monkeypatch.setenv("SCHOLARFORGE_OPENAI_API_KEY", KEY)
    project.settings.images.enabled = True
    wf = Coordinator(project)
    request_figure(wf, block, REQ)
    task = project.db.tasks.list(status=TaskStatus.BLOCKED)[0]
    out = service.confirm_external_generation(project, task.id, task.payload["confirmation_token"], False)
    assert out["declined"] and project.db.tasks.get(task.id).status == TaskStatus.CANCELLED
    assert project.db.tasks.open_for("figure.fix", project.db.figures.by_label("fig-lab").id) is not None


def test_cost_limit_blocks_generation(project, block, monkeypatch):
    monkeypatch.setenv("SCHOLARFORGE_OPENAI_API_KEY", KEY)
    project.settings.images.enabled = True
    project.settings.images.max_cost_per_image_usd = 0.001
    request_figure(Coordinator(project), block, REQ)
    assert not project.db.tasks.list(status=TaskStatus.BLOCKED)
    fix = project.db.tasks.open_for("figure.fix", project.db.figures.by_label("fig-lab").id)
    assert "exceeds" in fix.payload["error"]
