"""Recording fake provider, wired via the STAPEL_AGENT PROVIDERS override.

``services.get_provider`` instantiates a fresh object per request
(dotted-path + import_string), so calls and canned results live on the
class — reset via ``FakeProvider.reset()`` (the ``fake_provider`` fixture
does this automatically).
"""
from __future__ import annotations

from stapel_agent.providers.base import LlmProvider, ProviderResult


class FakeProvider(LlmProvider):
    name = "fake"

    calls: list[dict] = []
    result = ProviderResult(text='{"answer": 42}')
    error: Exception | None = None

    @classmethod
    def reset(cls):
        cls.calls = []
        cls.result = ProviderResult(
            text='{"answer": 42}',
            input_tokens=10,
            output_tokens=5,
            thinking_tokens=2,
            cache_read_tokens=1,
            cache_write_tokens=3,
        )
        cls.error = None

    def complete(self, *, prompt, model, system_prompt=None):
        cls = type(self)
        cls.calls.append(
            {"prompt": prompt, "model": model, "system_prompt": system_prompt}
        )
        if cls.error is not None:
            raise cls.error
        return cls.result
