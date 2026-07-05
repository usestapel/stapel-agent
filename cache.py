"""Cache-policy seam — swap the prompt cache without forking.

``STAPEL_AGENT["CACHE_POLICY"]`` is a dotted path to a ``CachePolicy``
subclass (resolved via ``import_strings``, instantiated per call). The
default, ``PromptLogCachePolicy``, implements the stock behaviour: the
latest successful ``PromptLog`` row with an identical
prompt+system_prompt+source within ``CACHE_TTL``, gated per source by
``CACHE_LOOKUP``.

Hosts can point the setting at a Redis-backed policy, a no-op policy,
or anything else::

    # myproject/llm_cache.py
    from stapel_agent.cache import CachePolicy

    class RedisCachePolicy(CachePolicy):
        def should_cache(self, source): ...
        def lookup(self, prompt, system_prompt, source, *,
                   provider, model, model_size): ...
        def store(self, prompt, system_prompt, source, response, *,
                  provider, model, model_size): ...

    # settings.py
    STAPEL_AGENT = {"CACHE_POLICY": "myproject.llm_cache.RedisCachePolicy"}

The PromptLog *ledger* row is always written regardless of the policy —
caching is a read seam, accounting is not optional. ``store()`` exists
for policies with their own storage; the default is a no-op because the
ledger row IS the default policy's storage.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import timedelta


class CachePolicy(ABC):
    """Decides when to consult the prompt cache and answers lookups."""

    @abstractmethod
    def should_cache(self, source: str) -> bool:
        """Whether *source* ("llm_facade"/"translate"/...) uses the cache."""

    @abstractmethod
    def lookup(
        self,
        prompt: str,
        system_prompt: str | None,
        source: str,
        *,
        provider: str,
        model: str,
        model_size: str,
    ) -> str | None:
        """Return the cached raw response text, or None on a miss.

        *provider* (the resolved provider name), *model* (the resolved
        model name — ``MODELS[model_size]`` after ``resolve_model``) and
        *model_size* are part of the cache key: a "small" answer must
        never be served to a "large" request, an explicit ``provider=``
        must not collide with the default, and bumping a model version in
        ``MODELS`` must invalidate its cached rows.
        """

    def store(
        self,
        prompt: str,
        system_prompt: str | None,
        source: str,
        response: str,
        *,
        provider: str,
        model: str,
        model_size: str,
    ) -> None:
        """Persist a successful response for future lookups.

        No-op by default: the default policy reads the PromptLog ledger
        row that ``services.complete`` writes anyway. Policies with
        external storage (Redis, ...) override this. See ``lookup`` for
        why *provider*/*model*/*model_size* belong in the key.
        """


class PromptLogCachePolicy(CachePolicy):
    """Stock policy: PromptLog rows + CACHE_LOOKUP/CACHE_TTL settings."""

    def should_cache(self, source: str) -> bool:
        from .conf import agent_settings

        return bool((agent_settings.CACHE_LOOKUP or {}).get(source, False))

    def lookup(
        self,
        prompt: str,
        system_prompt: str | None,
        source: str,
        *,
        provider: str,
        model: str,
        model_size: str,
    ) -> str | None:
        from django.utils import timezone

        from .conf import agent_settings
        from .models import PromptLog, PromptStatus

        ttl = int(agent_settings.CACHE_TTL)
        qs = PromptLog.objects.filter(
            prompt=prompt,
            source=source,
            status=PromptStatus.SUCCESS,
            response__isnull=False,
            # provider + resolved model + size are part of the key: a
            # "small" answer must never satisfy a "large" request, an
            # explicit provider must not collide with the default, and a
            # model-version bump in MODELS must invalidate old rows.
            model=model,
            model_size=model_size,
            metadata__provider=provider,
            created_at__gte=timezone.now() - timedelta(seconds=ttl),
        ).exclude(
            # The cache key is text-only, but multimodal ledger rows share
            # the prompt text — a text lookup must never serve an answer
            # that was about pixels. (Image requests skip lookup upstream;
            # this guards the other direction.)
            metadata__has_key="images"
        )
        if system_prompt:
            qs = qs.filter(system_prompt=system_prompt)
        else:
            qs = qs.filter(system_prompt__isnull=True)
        row = qs.order_by("-created_at").first()
        return row.response if row is not None else None


__all__ = ["CachePolicy", "PromptLogCachePolicy"]
