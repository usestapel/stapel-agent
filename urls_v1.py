"""v1 URL set for stapel-agent (api-versioning.md §2, §6).

Paths (relative to the ``api/v1/`` mount contributed by the root
``urls.py``): ``llm/complete`` / ``llm/translate`` / ...; host projects
mount the app under ``agent/``::

    path("agent/", include("stapel_agent.urls"))   # -> /agent/api/v1/llm/...

so stapel-translate's AgentProvider POSTs to
``{AGENT_URL}/api/v1/llm/complete`` (v1 canon).
"""

from django.urls import path

from .views import (
    LlmCompleteView,
    LlmGenerateImageView,
    LlmSummarizeView,
    LlmTranscribeView,
    LlmTranslateView,
)

urlpatterns = [
    path("llm/complete", LlmCompleteView.as_view(), name="llm-complete"),
    path("llm/translate", LlmTranslateView.as_view(), name="llm-translate"),
    path("llm/transcribe", LlmTranscribeView.as_view(), name="llm-transcribe"),
    path("llm/summarize", LlmSummarizeView.as_view(), name="llm-summarize"),
    path(
        "llm/generate-image",
        LlmGenerateImageView.as_view(),
        name="llm-generate-image",
    ),
]
