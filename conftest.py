def pytest_configure(config):
    from django.conf import settings
    if not settings.configured:
        from stapel_agent._codegen_settings import settings_kwargs

        settings.configure(**settings_kwargs())
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
    with a second/retryable/quota/fatal sibling registered for chain tests.

    Same merge semantics as ``fake_provider``: the STT_PROVIDERS overlay
    adds names without restating the built-ins.
    """
    from stapel_agent.tests.fakes import (
        FakeSttProvider,
        FatalSttProvider,
        QuotaSttProvider,
        RetryableSttProvider,
        SecondSttProvider,
    )

    fakes = (
        FakeSttProvider,
        SecondSttProvider,
        RetryableSttProvider,
        FatalSttProvider,
        QuotaSttProvider,
    )
    settings.STAPEL_AGENT = {
        **getattr(settings, "STAPEL_AGENT", {}),
        "STT_PROVIDERS": {
            "fake-stt": "stapel_agent.tests.fakes.FakeSttProvider",
            "fake-stt-2": "stapel_agent.tests.fakes.SecondSttProvider",
            "retry-stt": "stapel_agent.tests.fakes.RetryableSttProvider",
            "fatal-stt": "stapel_agent.tests.fakes.FatalSttProvider",
            "quota-stt": "stapel_agent.tests.fakes.QuotaSttProvider",
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
def fake_rerank(settings):
    """Route reranking to the recording FakeRerankProvider (default),
    with a fatal sibling registered for the failure path.

    Same merge semantics as the other fixtures: the RERANK_PROVIDERS
    overlay adds names without restating the built-ins.
    """
    from stapel_agent.tests.fakes import FakeRerankProvider, FatalRerankProvider

    fakes = (FakeRerankProvider, FatalRerankProvider)
    settings.STAPEL_AGENT = {
        **getattr(settings, "STAPEL_AGENT", {}),
        "RERANK_PROVIDERS": {
            "fake-rerank": "stapel_agent.tests.fakes.FakeRerankProvider",
            "fatal-rerank": "stapel_agent.tests.fakes.FatalRerankProvider",
        },
        "DEFAULT_RERANK_PROVIDER": "fake-rerank",
    }
    for cls in fakes:
        cls.reset()
    yield FakeRerankProvider
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
