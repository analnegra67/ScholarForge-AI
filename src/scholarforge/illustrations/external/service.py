"""External generation workflow: estimate -> explicit user confirmation -> generate -> QC.

The confirmation token is shown once to the host, which must ask the user; only a
call to ``confirm_external_image`` with that token (and approve=true) spends money.
"""

from __future__ import annotations

import hashlib
import hmac
from pathlib import Path
from typing import Any

import httpx
from PIL import Image

from scholarforge import secrets
from scholarforge.errors import ConfirmationRequired, ExternalServiceError, InvalidInput
from scholarforge.illustrations.external import pricing
from scholarforge.illustrations.external.openai_images import OpenAIImagesProvider
from scholarforge.illustrations.qc import check_figure
from scholarforge.models.entities import Figure
from scholarforge.models.enums import FigureStatus, TaskActor, TaskStatus
from scholarforge.util.atomic import atomic_write_bytes, atomic_write_json
from scholarforge.util.ids import token
from scholarforge.util.timeutil import now_iso

# Tests inject a mock transport here; production uses the real network.
TRANSPORT: httpx.BaseTransport | None = None


def _hash(tok: str) -> str:
    return hashlib.sha256(tok.encode("utf-8")).hexdigest()


def provider_status(settings) -> dict[str, Any]:
    key_source = secrets.secret_source("openai_api_key")
    return {"enabled": settings.images.enabled, "model": settings.images.model, "key": key_source,
            "usable": settings.images.enabled and key_source != "missing"}


def make_provider(settings) -> OpenAIImagesProvider:
    key = secrets.get_secret("openai_api_key")
    if not settings.images.enabled:
        raise ExternalServiceError("external image generation is disabled",
                                   hint="scholarforge config set images.enabled true")
    if not key:
        raise ExternalServiceError("no OpenAI API key", hint="scholarforge secret set openai_api_key")
    return OpenAIImagesProvider(key, base_url=settings.images.base_url, model=settings.images.model,
                                transport=TRANSPORT)


def plan_external_generation(wf, fig: Figure) -> dict[str, Any]:
    settings = wf.project.settings
    status = provider_status(settings)
    prompt = fig.spec.get("prompt") or ""
    if not status["usable"]:
        wf.queue.enqueue("figure.fix", title=f"Provide a native version of {fig.label}", target_id=fig.id,
                         priority=45, payload={"error": "external image generation is not available "
                                                         f"(enabled={status['enabled']}, key={status['key']}); "
                                                         "draw this figure natively (matplotlib/TikZ/Graphviz)"})
        return {"planned": False, "reason": "provider unavailable"}
    est = pricing.estimate(settings.images.model, settings.images.size, settings.images.quality, prompt)
    if est.get("available") and est["estimated_usd"] > settings.images.max_cost_per_image_usd:
        wf.queue.enqueue("figure.fix", title=f"Provide a native version of {fig.label}", target_id=fig.id,
                         priority=45, payload={"error": f"estimated cost ${est['estimated_usd']} exceeds the per-image "
                                                         f"limit ${settings.images.max_cost_per_image_usd}"})
        return {"planned": False, "reason": "over cost limit", "estimate": est}
    tok = token(12)
    wf.queue.enqueue(
        "figure.external", actor=TaskActor.USER, title=f"Approve paid image generation for {fig.label}",
        target_id=fig.id, status=TaskStatus.BLOCKED, priority=45,
        payload={"blocked_reason": "paid external image generation needs the user's approval",
                 "label": fig.label, "prompt": prompt, "model": settings.images.model, "size": settings.images.size,
                 "quality": settings.images.quality, "estimate": est, "token_sha256": _hash(tok),
                 "confirmation_token": tok,
                 "how_to_confirm": "ask the user; if they approve call confirm_external_image(project_id, task_id, "
                                   "confirmation_token, approve=true)"},
    )
    wf.pdb.figures.update(fig.id, status=FigureStatus.AWAITING_CONFIRMATION)
    return {"planned": True, "estimate": est}


def confirm_external_generation(project, task_id: str, confirmation_token: str, approve: bool) -> dict[str, Any]:
    from scholarforge.core.workflow import Coordinator

    wf = Coordinator(project)
    task = project.db.tasks.get(task_id)
    if task.kind != "figure.external" or task.status != TaskStatus.BLOCKED:
        raise InvalidInput(f"task {task_id} is not a pending external-image approval")
    if not hmac.compare_digest(_hash(confirmation_token or ""), task.payload.get("token_sha256", "")):
        raise ConfirmationRequired("invalid confirmation token", hint="use the token shown with the approval request")
    fig = project.db.figures.get(task.target_id)
    if not approve:
        wf.queue.cancel(task.id)
        wf.queue.enqueue("figure.fix", title=f"Provide a native version of {fig.label}", target_id=fig.id,
                         priority=45, payload={"error": "the user declined paid image generation; draw it natively"})
        return {"generated": False, "declined": True}
    provider = make_provider(project.settings)
    try:
        img = provider.generate(task.payload["prompt"], size=task.payload["size"], quality=task.payload["quality"])
    finally:
        provider.close()
    out_dir = project.figures_dir / fig.label
    png = out_dir / "figure.png"
    atomic_write_bytes(png, img.data)
    pdf = out_dir / "figure.pdf"
    with Image.open(png) as im:
        rgb = im.convert("RGB")
        rgb.save(pdf, "PDF", resolution=300.0)
    cost = pricing.actual_cost(task.payload["model"], img.usage)
    caption = fig.caption if "AI-generated" in fig.caption else f"{fig.caption} (AI-generated illustration.)"
    provenance = {"mode": "external", "provider": "openai", "model": task.payload["model"],
                  "prompt": task.payload["prompt"], "revised_prompt": img.revised_prompt,
                  "size": task.payload["size"], "quality": task.payload["quality"], "usage": img.usage,
                  "cost_usd_from_usage": cost, "estimate": task.payload.get("estimate"), "generated_at": now_iso(),
                  "ai_generated": True, "approved_by_user": True,
                  "rights_note": "Generated through the OpenAI API with the user's key; check the provider's current "
                                 "terms for ownership and permitted use."}
    atomic_write_json(out_dir / "provenance.json", provenance)
    files = {"png": png, "pdf": pdf}
    qc, problems = check_figure(files, caption=caption, alt_text=fig.alt_text,
                                width_fraction=float(fig.spec.get("width", 0.85)), text_width_in=5.0,
                                min_ppi=project.config.print.min_image_ppi, print_color=project.config.print.color)
    project.db.figures.update(fig.id, status=FigureStatus.RENDERED, caption=caption,
                              outputs={k: Path(v).relative_to(project.root).as_posix() for k, v in files.items()},
                              provenance=provenance, qc=qc, license="AI-generated (OpenAI API, user's account)")
    payload = {k: v for k, v in task.payload.items() if k != "confirmation_token"}
    project.db.tasks.update_payload(task.id, payload)
    project.db.tasks.set_status(task.id, TaskStatus.DONE, result={"cost_usd": cost})
    wf.queue.enqueue("figure.review", title=f"Check generated figure {fig.label}", target_id=fig.id, priority=45)
    return {"generated": True, "label": fig.label, "cost_usd": cost, "usage": img.usage, "qc_problems": problems}
