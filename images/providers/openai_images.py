"""OpenAI-compatible image generation adapter.

One adapter covers everything speaking the OpenAI images dialect
(``POST {base}/images/generations``): OpenAI itself, Together, and
self-hosted compatibles. Vendors with their own protocols (Stability,
Ideogram, ...) stay app-layer — implement the ``ImageGenProvider`` ABC
and register it (see MODULE.md).

Settings (all read lazily): ``IMAGES_BASE_URL`` (falls back to
``OPENAI_COMPAT_BASE_URL``), ``IMAGES_API_KEY`` (falls back to
``OPENAI_COMPAT_API_KEY``), ``IMAGES_MODEL`` (optional — omitted from
the request when empty, for servers with a single baked-in model).
"""
from __future__ import annotations

from typing import Optional

import requests

from ...conf import agent_settings
from ..base import (
    GeneratedImage,
    ImageGenError,
    ImageGenProvider,
    RetryableImageGenError,
)

DEFAULT_TIMEOUT_S = 120


class OpenAIImagesProvider(ImageGenProvider):
    name = "openai-images"

    def generate(
        self,
        *,
        prompt: str,
        size: str = "1024x1024",
        n: int = 1,
        timeout_seconds: Optional[int] = None,
    ) -> list[GeneratedImage]:
        base_url = (
            agent_settings.IMAGES_BASE_URL or agent_settings.OPENAI_COMPAT_BASE_URL or ""
        ).rstrip("/")
        if not base_url:
            raise ImageGenError(
                "Image endpoint not configured — set STAPEL_AGENT['IMAGES_BASE_URL'] "
                "(or OPENAI_COMPAT_BASE_URL)",
                provider=self.name,
            )
        api_key = agent_settings.IMAGES_API_KEY or agent_settings.OPENAI_COMPAT_API_KEY

        body = {"prompt": prompt, "n": int(n), "size": size}
        if agent_settings.IMAGES_MODEL:
            body["model"] = agent_settings.IMAGES_MODEL
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        try:
            resp = requests.post(
                f"{base_url}/images/generations",
                json=body,
                headers=headers,
                timeout=int(timeout_seconds or DEFAULT_TIMEOUT_S),
            )
        except requests.Timeout as exc:
            raise RetryableImageGenError(
                f"image generation timed out: {exc}", provider=self.name
            ) from exc
        except requests.RequestException as exc:
            raise RetryableImageGenError(
                f"image endpoint transport error: {exc}", provider=self.name
            ) from exc

        if resp.status_code == 429:
            raise RetryableImageGenError(
                "image endpoint rate-limited", provider=self.name, status_code=429
            )
        if resp.status_code >= 500:
            raise RetryableImageGenError(
                f"image endpoint {resp.status_code}: {resp.text[:300]}",
                provider=self.name,
                status_code=resp.status_code,
            )
        if resp.status_code >= 400:
            raise ImageGenError(
                f"image endpoint {resp.status_code}: {resp.text[:300]}",
                provider=self.name,
                status_code=resp.status_code,
            )

        try:
            payload = resp.json()
        except ValueError as exc:
            raise RetryableImageGenError(
                f"image endpoint returned non-JSON: {resp.text[:300]}",
                provider=self.name,
            ) from exc

        images = [
            GeneratedImage(
                mime=entry.get("mime_type") or "image/png",
                url=entry.get("url"),
                data_b64=entry.get("b64_json"),
            )
            for entry in payload.get("data") or []
            if entry.get("url") or entry.get("b64_json")
        ]
        if not images:
            raise ImageGenError(
                f"image endpoint response contained no images: {str(payload)[:300]}",
                provider=self.name,
            )
        return images


__all__ = ["OpenAIImagesProvider"]
