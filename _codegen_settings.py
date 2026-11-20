"""Single-module Django settings for stapel-agent.

One ``settings.configure(...)`` block serves the test suite and the contract
emission harness (``_codegen.py``), so the two cannot drift apart.
"""
from __future__ import annotations


def settings_kwargs(*, root_urlconf: str = "stapel_agent.tests.urls") -> dict:
    """The ``settings.configure(**kwargs)`` for a single-module instance."""
    # The fleet error envelope for refusals no view code raises (401/403/405);
    # read off core's preset, which reads no settings at import.
    from stapel_core.testing import BASE_REST_FRAMEWORK

    return dict(
        SECRET_KEY="test-secret-key-not-for-production-0123456789abcdef",
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
            "django.contrib.sessions",
            "django.contrib.messages",
            # contrib.admin so the ModelAdmin registrations in admin.py
            # are importable (and covered) in tests.
            "django.contrib.admin",
            # Core's app: it is what registers the GDPR provider bridge
            # and its system checks, so `manage.py check` sees the same
            # wiring here that a host does.
            "stapel_core.django.apps.CommonDjangoConfig",
            "stapel_core.django.users",
            "rest_framework",
            "drf_spectacular",
            "stapel_agent",
        ],
        AUTH_USER_MODEL="users.User",
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": ":memory:",
            }
        },
        DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
        USE_TZ=True,
        ROOT_URLCONF=root_urlconf,
        CACHES={
            "default": {
                "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            }
        },
        # In-memory bus — no Kafka/Redis broker needed
        STAPEL_BUS_BACKEND="stapel_core.bus.backends.memory.MemoryBus",
        # Synchronous in-process comm and schema validation ON so
        # llm.complete / llm.translate payloads are checked against
        # schemas/functions/*.json in tests.
        STAPEL_COMM={
            "OUTBOX_ENABLED": False,
            "ACTION_TRANSPORT": "inprocess",
            "FUNCTION_TRANSPORT": "inprocess",
            "VALIDATE_SCHEMAS": True,
        },
        MIDDLEWARE=[
            "django.middleware.common.CommonMiddleware",
            "stapel_core.django.jwt.middleware.ServiceAPIKeyMiddleware",
        ],
        SERVICE_API_KEY="test-service-key",
        # Skip migrations — create tables directly from models
        MIGRATION_MODULES={
            "users": None,
            "agent": None,
        },
        REST_FRAMEWORK={
            "EXCEPTION_HANDLER": BASE_REST_FRAMEWORK["EXCEPTION_HANDLER"],
        },
    )
