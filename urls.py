"""URL configuration for the agent app.

Paths are kept 1:1 with the legacy agent service (``api/llm/complete`` /
``api/llm/translate``); host projects mount the app under ``agent/``::

    path("agent/", include("stapel_agent.urls"))

so stapel-translate's AgentProvider keeps POSTing to
``{AGENT_URL}/api/llm/complete`` unchanged.
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
    path("api/llm/complete", LlmCompleteView.as_view(), name="llm-complete"),
    path("api/llm/translate", LlmTranslateView.as_view(), name="llm-translate"),
    path("api/llm/transcribe", LlmTranscribeView.as_view(), name="llm-transcribe"),
    path("api/llm/summarize", LlmSummarizeView.as_view(), name="llm-summarize"),
    path(
        "api/llm/generate-image",
        LlmGenerateImageView.as_view(),
        name="llm-generate-image",
    ),
]
