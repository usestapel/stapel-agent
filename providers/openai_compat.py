"""OpenAI-compatible ``/chat/completions`` provider.

One provider covers the whole OpenAI-flavoured portfolio: OpenAI itself,
DeepSeek, MiMo, GLM, Kimi — anything speaking the chat-completions dialect.
Configure ``OPENAI_COMPAT_BASE_URL`` / ``OPENAI_COMPAT_API_KEY`` and
(optionally) a per-size model map ``OPENAI_COMPAT_MODELS``.
"""
from __future__ import annotations

import requests

from ..conf import agent_settings
from .base import LlmProvider, ProviderError, ProviderResult, ProviderTimeout


class OpenAICompatProvider(LlmProvider):
    name = "openai-compat"
    supports_images = True

    def resolve_model(self, model_size: str, default: str) -> str:
        models = agent_settings.OPENAI_COMPAT_MODELS or {}
        return models.get(model_size) or default

    def complete(
        self,
        *,
        prompt: str,
        model: str,
        system_prompt: str | None = None,
        images: list | None = None,
    ) -> ProviderResult:
        base_url = (agent_settings.OPENAI_COMPAT_BASE_URL or "").rstrip("/")
        if not base_url:
            raise ProviderError(
                "OpenAI-compatible endpoint not configured — set "
                "STAPEL_AGENT['OPENAI_COMPAT_BASE_URL']"
            )
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        if images:
            # Vision: multimodal content array — image parts (URL or
            # data URI) followed by the text part.
            parts = [_image_part(img) for img in images]
            parts.append({"type": "text", "text": prompt})
            messages.append({"role": "user", "content": parts})
        else:
            messages.append({"role": "user", "content": prompt})

        headers = {"Content-Type": "application/json"}
        api_key = agent_settings.OPENAI_COMPAT_API_KEY
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        try:
            response = requests.post(
                f"{base_url}/chat/completions",
                json={
                    "model": model,
                    "messages": messages,
                    "max_tokens": int(agent_settings.MAX_TOKENS),
                },
                headers=headers,
                timeout=int(agent_settings.CLI_TIMEOUT),
            )
        except requests.Timeout as exc:
            raise ProviderTimeout("Execution timed out") from exc
        except requests.RequestException as exc:
            raise ProviderError(f"OpenAI-compatible endpoint unreachable: {exc}") from exc

        if response.status_code >= 400:
            raise ProviderError(
                f"OpenAI-compatible endpoint returned HTTP "
                f"{response.status_code}: {response.text[:500]}"
            )
        try:
            data = response.json()
            text = data["choices"][0]["message"]["content"] or ""
        except (ValueError, LookupError, TypeError) as exc:
            raise ProviderError(
                f"Unexpected response from OpenAI-compatible endpoint: {exc}"
            ) from exc

        usage = data.get("usage") or {}
        details = usage.get("completion_tokens_details") or {}
        return ProviderResult(
            text=text,
            input_tokens=usage.get("prompt_tokens", 0) or 0,
            output_tokens=usage.get("completion_tokens", 0) or 0,
            thinking_tokens=details.get("reasoning_tokens", 0) or 0,
        )


def _image_part(img) -> dict:
    """Map an ``ImageRef`` to an OpenAI ``image_url`` content part
    (URL refs pass through; byte refs become data URIs)."""
    url = img.url or f"data:{img.mime};base64,{img.as_base64()}"
    return {"type": "image_url", "image_url": {"url": url}}


__all__ = ["OpenAICompatProvider"]
