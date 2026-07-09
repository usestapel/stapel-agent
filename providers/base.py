"""Provider seam — the ABC every LLM backend implements.

Providers are addressed by name through ``STAPEL_AGENT["PROVIDERS"]``
(a dotted-path registry) and instantiated lazily per request by
``services.get_provider``. Implement this ABC in the app layer and point
the setting at it to add a backend without forking.

This module is deliberately Django-free so ``from stapel_agent import
LlmProvider, ProviderResult`` works without configured settings.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


class ProviderError(Exception):
    """Raised by a provider when a completion cannot be produced.

    The service layer converts it into a ``status: "failure"`` response
    (HTTP 200) and an ``error`` PromptLog row.
    """


class ProviderTimeout(ProviderError):
    """A completion timed out. Logged with status ``timeout``."""


@dataclass
class ProviderResult:
    """Raw completion text plus the token accounting the ledger needs."""

    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


class LlmProvider(ABC):
    """One LLM backend (Anthropic SDK, OpenAI-compatible HTTP, CLI, ...)."""

    name = "base"

    # Vision-capable backends set this True AND accept the optional
    # ``images`` kwarg in complete(). The service never forwards images
    # to a provider that leaves it False — such a request degrades to a
    # clear ``status: "failure"`` ("does not support image input"), and
    # pre-vision subclasses with the old three-argument signature keep
    # working untouched.
    supports_images: bool = False

    # Backends that honour a per-call output-token cap set this True AND
    # accept the optional ``max_tokens`` kwarg in complete(). The service
    # never forwards the kwarg to a provider that leaves it False — a
    # requested cap is then ignored with a logged warning (the provider
    # keeps its configured default), and pre-existing subclasses with the
    # old signature keep working untouched.
    supports_max_tokens: bool = False

    def resolve_model(self, model_size: str, default: str) -> str:
        """Map a size ("small"/"medium"/"large") to this backend's model name.

        *default* is the already-resolved ``MODELS[model_size]`` value;
        providers with their own model map (openai-compat) override this.
        """
        return default

    @abstractmethod
    def complete(
        self,
        *,
        prompt: str,
        model: str,
        system_prompt: str | None = None,
        images: list | None = None,
    ) -> ProviderResult:
        """Return the completion for *prompt*. Raise ProviderError on failure.

        *images* is a list of ``stapel_agent.images.base.ImageRef`` —
        only ever passed when ``supports_images`` is True (and only as a
        keyword, only when non-empty), so subclasses that predate vision
        support need no signature change.

        ``max_tokens`` (an int, the per-call output-token cap overriding
        the configured ``MAX_TOKENS``) follows the same discipline: only
        ever passed when ``supports_max_tokens`` is True, only as a
        keyword, only when the caller requested a cap.
        """


__all__ = ["LlmProvider", "ProviderError", "ProviderResult", "ProviderTimeout"]
