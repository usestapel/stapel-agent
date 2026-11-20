"""Canonical-prefix URLconf for contract emission.

The host mounts the agent at ``path("agent/", include("stapel_agent.urls"))``,
which yields ``/agent/api/v1/...``; this URLconf reproduces that mount so
drf-spectacular emits the same paths and operationIds.
"""
from django.urls import include, path

urlpatterns = [
    path("agent/", include("stapel_agent.urls")),
]
