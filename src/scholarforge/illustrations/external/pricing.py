"""Cost estimates for paid image generation.

Prices change; this table records where and when they were read. Estimates are
always labelled approximate; the actual charge is computed from the ``usage``
object returned by the API after generation, and billed by the provider.
"""

from __future__ import annotations

from typing import Any

PRICING_SOURCE = "https://developers.openai.com/api/docs/models/gpt-image-2.6-sunburst"
PRICING_VERIFIED_AT = "2026-11-17"

# USD per 1M tokens (official model pages, read on PRICING_VERIFIED_AT)
MODELS: dict[str, dict[str, float]] = {
    "gpt-image-2.6-sunburst": {"text_input": 5.0, "image_input": 8.0, "image_output": 30.0},
    "gpt-image-2.5-sunburst": {"text_input": 5.0, "image_input": 8.0, "image_output": 30.0},
}

# Image output tokens for a 1024x1024 image by quality, as documented for GPT Image models
# (low 272, medium 1056, high 4160). xhigh/max are not documented per token: extrapolated, flagged.
OUTPUT_TOKENS_1024 = {"low": 272, "medium": 1056, "high": 4160, "auto": 4160}
EXTRAPOLATED = {"xhigh": 2.0, "max": 4.0}


def _pixels(size: str) -> int:
    if size == "auto":
        return 1536 * 1024
    w, h = (int(x) for x in size.lower().split("x"))
    return w * h


def estimate(model: str, size: str, quality: str, prompt: str) -> dict[str, Any]:
    price = MODELS.get(model)
    if price is None:
        return {"available": False, "model": model,
                "reason": "no verified price for this model in ScholarForge's table; the provider bills actual usage"}
    scale = _pixels(size) / (1024 * 1024)
    if quality in OUTPUT_TOKENS_1024:
        out_tokens = OUTPUT_TOKENS_1024[quality] * scale
        extrapolated = False
    else:
        out_tokens = OUTPUT_TOKENS_1024["high"] * EXTRAPOLATED.get(quality, 4.0) * scale
        extrapolated = True
    in_tokens = max(1, len(prompt) // 4)
    usd = out_tokens * price["image_output"] / 1e6 + in_tokens * price["text_input"] / 1e6
    return {"available": True, "model": model, "size": size, "quality": quality, "estimated_usd": round(usd, 4),
            "estimated_output_tokens": int(out_tokens), "estimated_input_tokens": in_tokens, "approximate": True,
            "extrapolated_quality": extrapolated, "pricing_source": PRICING_SOURCE,
            "pricing_verified_at": PRICING_VERIFIED_AT}


def actual_cost(model: str, usage: dict[str, Any] | None) -> float | None:
    price = MODELS.get(model)
    if not price or not usage:
        return None
    details = usage.get("input_tokens_details") or {}
    text_in = details.get("text_tokens", usage.get("input_tokens", 0))
    image_in = details.get("image_tokens", 0)
    out = usage.get("output_tokens", 0)
    return round(text_in * price["text_input"] / 1e6 + image_in * price["image_input"] / 1e6
                 + out * price["image_output"] / 1e6, 4)
