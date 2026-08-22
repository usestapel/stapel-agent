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


# The token ceiling is spelled two ways across OpenAI-dialect endpoints:
# `max_tokens` (the original, still what most compatible hosts accept) and
# `max_completion_tokens` (OpenAI's own reasoning-era models reject the
# former with HTTP 400). A setting, not sniffing: the endpoint's dialect is
# a deployment fact, and guessing it from the model name is how a renamed
# model breaks production.
_MAX_TOKENS_PARAMS = frozenset({"max_tokens", "max_completion_tokens"})


class OpenAICompatProvider(LlmProvider):
    name = "openai-compat"
    supports_images = True
    supports_max_tokens = True
    supports_schema = True

    @classmethod
    def configuration_error(cls) -> str | None:
        # Only the base URL is mandatory: a self-hosted endpoint (vLLM,
        # Ollama, TEI) legitimately needs no key, so a missing API key is
        # not reported — the endpoint itself will 401 if it wanted one.
        if not (agent_settings.OPENAI_COMPAT_BASE_URL or "").strip():
            return (
                "OPENAI_COMPAT_BASE_URL is empty — set "
                "STAPEL_AGENT['OPENAI_COMPAT_BASE_URL'] to a "
                "/chat/completions-compatible endpoint"
            )
        proxy = (agent_settings.OPENAI_COMPAT_PROXY or "").strip()
        if proxy.startswith("socks"):
            # requests only learns SOCKS from PySocks; without it every call
            # dies with InvalidSchema at request time. Say so at boot instead.
            try:
                import socks  # noqa: F401  (PySocks)
            except ImportError:
                return (
                    "OPENAI_COMPAT_PROXY is a SOCKS URL but PySocks is not "
                    "installed — install stapel-agent[socks]"
                )
        param = agent_settings.OPENAI_COMPAT_MAX_TOKENS_PARAM
        if param not in _MAX_TOKENS_PARAMS:
            return (
                f"OPENAI_COMPAT_MAX_TOKENS_PARAM={param!r} is not one of "
                f"{sorted(_MAX_TOKENS_PARAMS)}"
            )
        return None

    @staticmethod
    def _proxies() -> dict | None:
        proxy = (agent_settings.OPENAI_COMPAT_PROXY or "").strip()
        return {"http": proxy, "https": proxy} if proxy else None

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
        max_tokens: int | None = None,
        schema: dict | None = None,
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

        payload = {
            "model": model,
            "messages": messages,
            agent_settings.OPENAI_COMPAT_MAX_TOKENS_PARAM: int(
                max_tokens or agent_settings.MAX_TOKENS
            ),
        }
        if schema:
            # The OpenAI-flavoured spelling of constrained decoding.
            # `strict` is what makes it a decoder constraint rather than
            # a hint — without it the endpoint is free to return prose.
            #
            # And `strict` is exactly why the schema cannot go out as
            # pydantic emits it: the strict subset requires every property
            # to be listed in `required`, while pydantic omits anything
            # that has a default. One defaulted field and the endpoint
            # rejects the request before generating a token. The transform
            # lives here rather than at the caller because it is a demand
            # of this transport — the Anthropic path derives its own format
            # from the raw schema and must not see it.
            from stapel_core.schema_strict import to_strict_subset

            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "result",
                    "schema": to_strict_subset(schema),
                    "strict": True,
                },
            }

        try:
            response = requests.post(
                f"{base_url}/chat/completions",
                json=payload,
                headers=headers,
                timeout=int(agent_settings.CLI_TIMEOUT),
                proxies=self._proxies(),
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
