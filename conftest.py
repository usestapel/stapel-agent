def pytest_configure(config):
    from django.conf import settings
    if not settings.configured:
        settings.configure(
            SECRET_KEY="test-secret-key-not-for-production",
            INSTALLED_APPS=[
                "django.contrib.contenttypes",
                "django.contrib.auth",
                "django.contrib.sessions",
                "django.contrib.messages",
                # contrib.admin so the ModelAdmin registrations in admin.py
                # are importable (and covered) in tests.
                "django.contrib.admin",
                "stapel_core.django.users",
                "rest_framework",
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
            ROOT_URLCONF="stapel_agent.tests.urls",
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
        )
        import django
        django.setup()

        # Register schemas/functions/*.json with the comm registries so
        # call() payloads are validated against the committed contracts.
        from stapel_core.comm.schemas import autoload_schemas
        autoload_schemas()


import pytest  # noqa: E402


@pytest.fixture
def user(db):
    from django.contrib.auth import get_user_model
    User = get_user_model()
    return User.objects.create_user(
        username="testuser",
        email="testuser@example.com",
        password="testpass123",
    )


@pytest.fixture
def staff_user(db):
    from django.contrib.auth import get_user_model
    User = get_user_model()
    return User.objects.create_user(
        username="staffuser",
        email="staff@example.com",
        password="testpass123",
        is_staff=True,
    )


@pytest.fixture
def api_client():
    from rest_framework.test import APIClient
    return APIClient()


@pytest.fixture
def staff_client(staff_user):
    from rest_framework.test import APIClient
    client = APIClient()
    client.force_authenticate(user=staff_user)
    return client


@pytest.fixture
def fake_provider(settings):
    """Route completions to the recording FakeProvider (default provider).

    Keys are read lazily through stapel_agent.conf.agent_settings, so the
    settings override takes effect at call time. Class-level state is
    reset around each test — get_provider() instantiates a fresh object
    per request, so recordings must live on the class.
    """
    from stapel_agent.tests.fakes import FakeProvider

    # Merge semantics: adding "fake" does not restate the built-ins —
    # they stay resolvable alongside it.
    settings.STAPEL_AGENT = {
        "PROVIDERS": {"fake": "stapel_agent.tests.fakes.FakeProvider"},
        "DEFAULT_PROVIDER": "fake",
    }
    FakeProvider.reset()
    yield FakeProvider
    FakeProvider.reset()


@pytest.fixture
def fake_stt(settings):
    """Route transcriptions to the recording FakeSttProvider (default),
    with a second/retryable/fatal sibling registered for chain tests.

    Same merge semantics as ``fake_provider``: the STT_PROVIDERS overlay
    adds names without restating the built-ins.
    """
    from stapel_agent.tests.fakes import (
        FakeSttProvider,
        FatalSttProvider,
        RetryableSttProvider,
        SecondSttProvider,
    )

    fakes = (FakeSttProvider, SecondSttProvider, RetryableSttProvider, FatalSttProvider)
    settings.STAPEL_AGENT = {
        **getattr(settings, "STAPEL_AGENT", {}),
        "STT_PROVIDERS": {
            "fake-stt": "stapel_agent.tests.fakes.FakeSttProvider",
            "fake-stt-2": "stapel_agent.tests.fakes.SecondSttProvider",
            "retry-stt": "stapel_agent.tests.fakes.RetryableSttProvider",
            "fatal-stt": "stapel_agent.tests.fakes.FatalSttProvider",
        },
        "DEFAULT_STT_PROVIDER": "fake-stt",
    }
    for cls in fakes:
        cls.reset()
    yield FakeSttProvider
    for cls in fakes:
        cls.reset()


@pytest.fixture
def fake_diarization(settings):
    """Route diarization to the recording FakeDiarizationProvider
    (default), with a fatal sibling registered for the failure path.

    Same merge semantics as the other fixtures: the
    DIARIZATION_PROVIDERS overlay adds names without restating the
    built-in pyannote-http.
    """
    from stapel_agent.tests.fakes import (
        FakeDiarizationProvider,
        FatalDiarizationProvider,
    )

    fakes = (FakeDiarizationProvider, FatalDiarizationProvider)
    settings.STAPEL_AGENT = {
        **getattr(settings, "STAPEL_AGENT", {}),
        "DIARIZATION_PROVIDERS": {
            "fake-diar": "stapel_agent.tests.fakes.FakeDiarizationProvider",
            "fatal-diar": "stapel_agent.tests.fakes.FatalDiarizationProvider",
        },
        "DEFAULT_DIARIZATION_PROVIDER": "fake-diar",
    }
    for cls in fakes:
        cls.reset()
    yield FakeDiarizationProvider
    for cls in fakes:
        cls.reset()


@pytest.fixture
def fake_embeddings(settings):
    """Route embeddings to the recording FakeEmbeddingProvider (default),
    with a fatal sibling registered for the failure path.

    Same merge semantics as the other fixtures: the EMBEDDING_PROVIDERS
    overlay adds names without restating the built-ins.
    """
    from stapel_agent.tests.fakes import FakeEmbeddingProvider, FatalEmbeddingProvider

    fakes = (FakeEmbeddingProvider, FatalEmbeddingProvider)
    settings.STAPEL_AGENT = {
        **getattr(settings, "STAPEL_AGENT", {}),
        "EMBEDDING_PROVIDERS": {
            "fake-embed": "stapel_agent.tests.fakes.FakeEmbeddingProvider",
            "fatal-embed": "stapel_agent.tests.fakes.FatalEmbeddingProvider",
        },
        "DEFAULT_EMBEDDING_PROVIDER": "fake-embed",
    }
    for cls in fakes:
        cls.reset()
    yield FakeEmbeddingProvider
    for cls in fakes:
        cls.reset()


@pytest.fixture
def fake_images(settings):
    """Route image generation to the recording FakeImageProvider (default),
    with a size-restricted sibling registered for the validation path.

    Same merge semantics as the other fixtures: the IMAGE_PROVIDERS
    overlay adds names without restating the built-in openai-images.
    """
    from stapel_agent.tests.fakes import FakeImageProvider, SquareOnlyImageProvider

    fakes = (FakeImageProvider, SquareOnlyImageProvider)
    settings.STAPEL_AGENT = {
        **getattr(settings, "STAPEL_AGENT", {}),
        "IMAGE_PROVIDERS": {
            "fake-images": "stapel_agent.tests.fakes.FakeImageProvider",
            "square-images": "stapel_agent.tests.fakes.SquareOnlyImageProvider",
        },
        "DEFAULT_IMAGE_PROVIDER": "fake-images",
    }
    for cls in fakes:
        cls.reset()
    yield FakeImageProvider
    for cls in fakes:
        cls.reset()
