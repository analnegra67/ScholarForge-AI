"""OpenAI Images API client (``POST /v1/images/generations``) using the user's own key.

Requests go directly from the user's machine to the official API. The key is
read from the OS credential store or an environment variable and is never
logged, cached, written to disk or included in exports.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Any

import httpx

from scholarforge.errors import ExternalServiceError


@dataclass
class GeneratedImage:
    data: bytes
    mime: str
    usage: dict[str, Any] = field(default_factory=dict)
    revised_prompt: str | None = None
    response_meta: dict[str, Any] = field(default_factory=dict)


class OpenAIImagesProvider:
    name = "openai"

    def __init__(self, api_key: str, *, base_url: str = "https://api.openai.com/v1", model: str,
                 transport: httpx.BaseTransport | None = None, timeout_s: float = 300.0):
        if not api_key:
            raise ExternalServiceError("no OpenAI API key configured")
        self._client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout_s, transport=transport,
                                    headers={"Authorization": f"Bearer {api_key}",
                                             "User-Agent": "ScholarForge (user-supplied key)"})
        self.model = model

    def close(self) -> None:
        self._client.close()

    @staticmethod
    def _error(resp: httpx.Response) -> ExternalServiceError:
        try:
            err = resp.json().get("error", {})
            message = err.get("message") or resp.text[:300]
            code = err.get("code") or err.get("type")
        except Exception:
            message, code = resp.text[:300], None
        hints = {401: "check the API key (scholarforge secret set openai_api_key)",
                 403: "the key's project may not have access to this model",
                 404: "the model is not available to this API key",
                 429: "rate limit or quota exceeded on your OpenAI account"}
        return ExternalServiceError(f"OpenAI API error {resp.status_code}: {message}",
                                    details={"status": resp.status_code, "code": code}, hint=hints.get(resp.status_code))

    def model_available(self) -> bool:
        resp = self._client.get(f"/models/{self.model}")
        if resp.status_code == 200:
            return True
        if resp.status_code == 404:
            return False
        raise self._error(resp)

    def generate(self, prompt: str, *, size: str, quality: str, output_format: str = "png",
                 background: str = "opaque", safety_identifier: str | None = None) -> GeneratedImage:
        body: dict[str, Any] = {"model": self.model, "prompt": prompt, "n": 1, "size": size, "quality": quality,
                                "output_format": output_format, "background": background}
        if safety_identifier:
            body["safety_identifier"] = safety_identifier
        resp = self._client.post("/images/generations", json=body)
        if resp.status_code != 200:
            raise self._error(resp)
        payload = resp.json()
        items = payload.get("data") or []
        if not items or not items[0].get("b64_json"):
            raise ExternalServiceError("the API response contains no image data")
        data = base64.b64decode(items[0]["b64_json"])
        mime = {"png": "image/png", "jpeg": "image/jpeg", "webp": "image/webp", "svg": "image/svg+xml"}.get(
            output_format, "application/octet-stream")
        meta = {k: v for k, v in payload.items() if k not in ("data", "usage")}
        return GeneratedImage(data=data, mime=mime, usage=payload.get("usage") or {},
                              revised_prompt=items[0].get("revised_prompt"), response_meta=meta)
