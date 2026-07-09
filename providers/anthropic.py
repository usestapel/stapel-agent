"""Anthropic SDK provider (the default).

Requires the optional ``anthropic`` extra (``pip install
stapel-agent[anthropic]``) and ``STAPEL_AGENT["ANTHROPIC_API_KEY"]``.
Both are read lazily at call time — an unconfigured host fails with a
clear ProviderError on first use, never at import.
"""
from __future__ import annotations

from ..conf import agent_settings
from .base import LlmProvider, ProviderError, ProviderResult


class AnthropicProvider(LlmProvider):
    name = "anthropic"
    supports_images = True
    supports_max_tokens = True

    def complete(
        self,
        *,
        prompt: str,
        model: str,
        system_prompt: str | None = None,
        images: list | None = None,
        max_tokens: int | None = None,
    ) -> ProviderResult:
        api_key = agent_settings.ANTHROPIC_API_KEY
        if not api_key:
            raise ProviderError(
                "Anthropic API key not configured — set "
                "STAPEL_AGENT['ANTHROPIC_API_KEY'] (or the ANTHROPIC_API_KEY "
                "env var) or pick another provider"
            )
        try:
            import anthropic
        except ImportError as exc:
            raise ProviderError(
                "the 'anthropic' package is not installed — "
                "pip install stapel-agent[anthropic]"
            ) from exc

        client = anthropic.Anthropic(api_key=api_key)
        if images:
            # Vision: image content blocks first, the text prompt last —
            # the Anthropic-recommended ordering.
            content: list | str = [_image_block(img) for img in images]
            content.append({"type": "text", "text": prompt})
        else:
            content = prompt
        kwargs = {
            "model": model,
            "max_tokens": int(max_tokens or agent_settings.MAX_TOKENS),
            "messages": [{"role": "user", "content": content}],
        }
        if system_prompt:
            kwargs["system"] = system_prompt
        try:
            message = client.messages.create(**kwargs)
        except Exception as exc:
            raise ProviderError(str(exc)) from exc

        text = "".join(
            block.text
            for block in message.content
            if getattr(block, "type", "") == "text"
        )
        usage = getattr(message, "usage", None)
        return ProviderResult(
            text=text,
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
        )


def _image_block(img) -> dict:
    """Map an ``ImageRef`` to an Anthropic image content block."""
    if img.url:
        return {"type": "image", "source": {"type": "url", "url": img.url}}
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": img.mime,
            "data": img.as_base64(),
        },
    }


__all__ = ["AnthropicProvider"]
