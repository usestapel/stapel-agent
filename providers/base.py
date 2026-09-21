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

    *reason* is the classification from :mod:`stapel_agent.failures` —
    ``quota``, ``auth``, ``server``, ``media`` and the rest. It is what
    the provider chain walks on, what the ledger row records per
    attempt, and what tells "this account is out of credits" from "this
    prompt is malformed" without matching on message text. ``None``
    means an adapter that predates the taxonomy raised it; the service
    treats that as terminal, which is what it did before this field
    existed.
    """

    def __init__(
        self,
        message: str,
        *,
        provider: str = "",
        status_code: int | None = None,
        reason: str | None = None,
    ):
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code
        self.reason = reason


class RetryableProviderError(ProviderError):
    """The provider could not serve this call — the request is fine.

    Quota, auth, throttling, 5xx, an endpoint that is down. The service
    walks the rest of the configured chain instead of failing the
    caller's work, exactly as the STT chain does for the same conditions
    (see :mod:`stapel_agent.failures` for the incident on each side).
    """


class ProviderTimeout(RetryableProviderError):
    """A completion timed out. Logged with status ``timeout``.

    Retryable since 0.28.0: it was a plain ``ProviderError``, which the
    chain would have read as "this prompt cannot be completed by
    anyone". A provider that stalls says nothing about the next one.
    """

    def __init__(self, message: str, *, provider: str = "", **kwargs):
        kwargs.setdefault("reason", "timeout")
        super().__init__(message, provider=provider, **kwargs)


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

    # Backends that can CONSTRAIN the decoder to a JSON Schema set this
    # True AND accept the optional ``schema`` kwarg in complete().
    #
    # "Ask for JSON in the system prompt and parse whatever comes back"
    # is not the same capability and must not stand in for it. Measured
    # on the iron-benchmark harness (2026-07-03): under the prompt-only
    # path a model emitted its entire answer as pseudo-XML inside one
    # string field while every other field came back empty — valid JSON,
    # parsed fine, meant nothing. A caller that asked for a constrained
    # schema and silently got the prompt-only path would never learn
    # that. The service therefore fails such a request outright rather
    # than degrading it.
    supports_schema: bool = False

    @classmethod
    def configuration_error(cls) -> str | None:
        """Why this backend cannot serve a call yet, or None if it can.

        Registered is not the same as usable. `check_providers` only ever
        asked whether DEFAULT_PROVIDER resolves to an LlmProvider subclass
        — so a deployment defaulting to `anthropic` with an EMPTY
        ANTHROPIC_API_KEY passed every check while every text call raised
        ProviderError (a client stand, 2026-07-26). Nothing said so,
        because the one caller in the fleet — stapel-recordings'
        summarize step — is best-effort by design: it swallowed the error
        and each recording completed with an empty summary.

        Each backend answers for ITSELF (the library never keeps a table
        of who needs which credential — that copy would drift the moment
        a provider changes). Read settings lazily, exactly as complete()
        does; never at import.

        Returning None means "configured as far as can be known without
        making a network call" — it is not a promise the credential is
        valid, only that one is present.
        """
        return None

    def resolve_model(self, model_size: str, default: str) -> str:
        """Map a size ("small"/"medium"/"large"/"xlarge") to this backend's model name.

        *default* is the already-resolved ``MODELS[model_size]`` value;
        providers with their own model map (openai-compat) override this.
        """
        return default

    @classmethod
    def base_url(cls) -> str:
        """The HTTP endpoint this backend resolves against, or "" for one
        with none (the Anthropic SDK, the CLI provider — both address a
        fixed vendor endpoint the settings namespace does not name).

        Read lazily, like every other setting here — this is asked once per
        call, from ``pricing.cost_fields``, to tell an aggregator endpoint
        (OpenRouter) from a direct one: the two bill a vendor-prefixed model
        id ("x-ai/grok-4.5") as different products. A provider whose
        endpoint IS configurable (openai-compat and its subclasses) must
        override this; the default is correct only for a fixed endpoint.
        """
        return ""

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


def status_error(
    status_code: int,
    body: str = "",
    *,
    provider: str,
    label: str = "",
) -> ProviderError:
    """The classified exception for a non-2xx answer (raise it).

    The one place an HTTP status becomes a disposition for text calls,
    so an adapter never has to decide — and never again turns "your team
    has used all available credits" into the same object as "this image
    is corrupt" (2026-09-20; see :mod:`stapel_agent.failures`).
    """
    from ..failures import PHRASES, classify_status

    fatal, reason = classify_status(status_code, body)
    phrase = PHRASES.get(reason, reason)
    where = label or provider
    message = (
        f"{where} returned HTTP {status_code} ({phrase}): {(body or '')[:500]}"
    )
    cls = ProviderError if fatal else RetryableProviderError
    return cls(message, provider=provider, status_code=status_code, reason=reason)


__all__ = [
    "LlmProvider",
    "ProviderError",
    "ProviderResult",
    "ProviderTimeout",
    "RetryableProviderError",
    "status_error",
]
