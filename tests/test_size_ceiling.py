"""The model-size ceiling seam — an OPTIONAL, closed-by-default entitlement
gate on ``complete()`` (services.resolve_size_ceiling / enforce_size_ceiling).

The switch is ``STAPEL_AGENT['MODEL_SIZE_CEILING_ENTITLEMENT']``: empty (the
default) means the seam never asks billing anything and every call behaves
exactly as it did before this feature existed. Configuring it to an
entitlement key name turns on a per-call ``billing.check_entitlement`` lookup
that can refuse a request whose ``model_size`` sits above what the caller's
plan resolves to — an upsell surface, never a silent downgrade.

Fail-open precedent: mirrors ironmemo-backend's ``recordings_ext.entitlement``
gate — an unreachable/misconfigured billing call must not turn into a denial
of an otherwise-permitted size, because the caller did nothing wrong; only a
billing verdict that is both present AND names a usable ceiling narrows
anything.
"""
import pytest

from stapel_agent import services
from stapel_agent.models import PromptSource


ENTITLEMENT_KEY = "llm.model_size_ceiling"


def _configure(settings, **extra):
    settings.STAPEL_AGENT = {
        "PROVIDERS": {"fake": "stapel_agent.tests.fakes.FakeProvider"},
        "DEFAULT_PROVIDER": "fake",
        **extra,
    }


@pytest.mark.django_db
class TestSeamClosedByDefault:
    """The switch is unset — byte-identical to pre-0.13 behaviour."""

    def test_resolve_size_ceiling_is_none_without_configuring_the_switch(
        self, fake_provider
    ):
        assert services.resolve_size_ceiling("u-1") is None

    def test_complete_never_asks_billing_when_the_switch_is_unset(
        self, fake_provider, monkeypatch
    ):
        def _boom(*a, **k):
            raise AssertionError("billing.check_entitlement must not be called")

        monkeypatch.setattr("stapel_core.comm.call", _boom)
        result = services.complete(
            "hello", "xlarge", source=PromptSource.OTHER, user_id="u-1"
        )
        assert result["status"] == "ok"

    def test_every_size_is_still_servable(self, fake_provider):
        for size in services.MODEL_SIZES:
            result = services.complete(
                "hello", size, source=PromptSource.OTHER, user_id="u-1"
            )
            assert result["status"] == "ok", size


@pytest.mark.django_db
class TestNoIdentity:
    """Configured, but the call carries no user_id — nothing to ask billing
    ABOUT. Fails open (no ceiling) with a logged warning, not a denial."""

    def test_no_user_id_applies_no_ceiling(self, fake_provider, settings, monkeypatch):
        _configure(settings, MODEL_SIZE_CEILING_ENTITLEMENT=ENTITLEMENT_KEY)

        def _boom(*a, **k):
            raise AssertionError("billing must not be asked with no identity")

        monkeypatch.setattr("stapel_core.comm.call", _boom)
        assert services.resolve_size_ceiling(None) is None
        assert services.resolve_size_ceiling("") is None

    def test_no_user_id_logs_a_warning(self, fake_provider, settings, caplog):
        _configure(settings, MODEL_SIZE_CEILING_ENTITLEMENT=ENTITLEMENT_KEY)
        with caplog.at_level("WARNING"):
            services.resolve_size_ceiling(None)
        assert "no user_id" in caplog.text or "no ceiling" in caplog.text

    def test_call_without_identity_still_completes_at_any_size(
        self, fake_provider, settings
    ):
        _configure(settings, MODEL_SIZE_CEILING_ENTITLEMENT=ENTITLEMENT_KEY)
        result = services.complete("hello", "xlarge", source=PromptSource.OTHER)
        assert result["status"] == "ok"


@pytest.mark.django_db
class TestBillingUnavailable:
    """billing.check_entitlement is unreachable — fails open, mirroring
    ironmemo's recordings_ext.entitlement gate exactly."""

    def test_unregistered_function_fails_open(self, fake_provider, settings):
        # Nothing registers "billing.check_entitlement" in this process —
        # stapel-billing isn't installed here, so this IS the "unreachable"
        # case, with no monkeypatching needed to produce it.
        _configure(settings, MODEL_SIZE_CEILING_ENTITLEMENT=ENTITLEMENT_KEY)
        assert services.resolve_size_ceiling("u-1") is None

    def test_unregistered_function_logs_a_warning_not_an_error(
        self, fake_provider, settings, caplog
    ):
        _configure(settings, MODEL_SIZE_CEILING_ENTITLEMENT=ENTITLEMENT_KEY)
        with caplog.at_level("WARNING"):
            services.resolve_size_ceiling("u-1")
        assert "unreachable" in caplog.text

    def test_call_completes_in_full_when_billing_is_unreachable(
        self, fake_provider, settings
    ):
        _configure(settings, MODEL_SIZE_CEILING_ENTITLEMENT=ENTITLEMENT_KEY)
        result = services.complete(
            "hello", "xlarge", source=PromptSource.OTHER, user_id="u-1"
        )
        assert result["status"] == "ok"
        assert fake_provider.calls[0]["model"] == "claude-fable-5"

    def test_unexpected_billing_exception_also_fails_open(
        self, fake_provider, settings, monkeypatch, caplog
    ):
        _configure(settings, MODEL_SIZE_CEILING_ENTITLEMENT=ENTITLEMENT_KEY)

        def _kaboom(*a, **k):
            raise RuntimeError("something the comm layer never defined")

        monkeypatch.setattr("stapel_core.comm.call", _kaboom)
        with caplog.at_level("WARNING"):
            assert services.resolve_size_ceiling("u-1") is None
        assert "unexpected" in caplog.text.lower()


@pytest.mark.django_db
class TestEntitlementAllows:
    def test_allowed_true_applies_no_ceiling(self, fake_provider, settings, monkeypatch):
        _configure(settings, MODEL_SIZE_CEILING_ENTITLEMENT=ENTITLEMENT_KEY)
        monkeypatch.setattr(
            "stapel_core.comm.call", lambda *a, **k: {"allowed": True, "limit": None}
        )
        assert services.resolve_size_ceiling("u-1") is None

    def test_limit_at_the_top_of_the_ladder_is_unrestricted(
        self, fake_provider, settings, monkeypatch
    ):
        _configure(settings, MODEL_SIZE_CEILING_ENTITLEMENT=ENTITLEMENT_KEY)
        monkeypatch.setattr(
            "stapel_core.comm.call",
            lambda *a, **k: {"allowed": False, "limit": len(services.MODEL_SIZES)},
        )
        assert services.resolve_size_ceiling("u-1") is None

    def test_within_the_resolved_ceiling_passes(
        self, fake_provider, settings, monkeypatch
    ):
        _configure(settings, MODEL_SIZE_CEILING_ENTITLEMENT=ENTITLEMENT_KEY)
        monkeypatch.setattr(
            "stapel_core.comm.call",
            lambda *a, **k: {"allowed": False, "limit": 3},  # caps at "large"
        )
        result = services.complete(
            "hello", "large", source=PromptSource.OTHER, user_id="u-1"
        )
        assert result["status"] == "ok"
        assert fake_provider.calls[0]["model"] == "claude-opus-4-8"


@pytest.mark.django_db
class TestOverCeilingIsRefused:
    def test_resolve_size_ceiling_reports_the_rank(
        self, fake_provider, settings, monkeypatch
    ):
        _configure(settings, MODEL_SIZE_CEILING_ENTITLEMENT=ENTITLEMENT_KEY)
        monkeypatch.setattr(
            "stapel_core.comm.call", lambda *a, **k: {"allowed": False, "limit": 2}
        )
        assert services.resolve_size_ceiling("u-1") == "medium"

    def test_enforce_size_ceiling_raises(self, fake_provider, settings, monkeypatch):
        _configure(settings, MODEL_SIZE_CEILING_ENTITLEMENT=ENTITLEMENT_KEY)
        monkeypatch.setattr(
            "stapel_core.comm.call", lambda *a, **k: {"allowed": False, "limit": 1}
        )
        with pytest.raises(services.ModelSizeCeilingExceeded) as exc_info:
            services.enforce_size_ceiling("large", "u-1")
        assert exc_info.value.requested_size == "large"
        assert exc_info.value.ceiling == "small"

    def test_a_size_at_the_ceiling_is_not_refused(
        self, fake_provider, settings, monkeypatch
    ):
        _configure(settings, MODEL_SIZE_CEILING_ENTITLEMENT=ENTITLEMENT_KEY)
        monkeypatch.setattr(
            "stapel_core.comm.call", lambda *a, **k: {"allowed": False, "limit": 2}
        )
        # "medium" IS the ceiling here — allowed, not just "below" it.
        services.enforce_size_ceiling("medium", "u-1")

    def test_complete_refuses_an_over_ceiling_request(
        self, fake_provider, settings, monkeypatch
    ):
        _configure(settings, MODEL_SIZE_CEILING_ENTITLEMENT=ENTITLEMENT_KEY)
        monkeypatch.setattr(
            "stapel_core.comm.call", lambda *a, **k: {"allowed": False, "limit": 1}
        )
        result = services.complete(
            "hello", "xlarge", source=PromptSource.OTHER, user_id="u-1"
        )
        assert result["status"] == "failure"
        assert result["reason"] == services.REASON_MODEL_SIZE_CEILING
        assert result["ceiling"] == "small"
        assert result["requested_size"] == "xlarge"
        # No provider call and no spend for a refused request.
        assert fake_provider.calls == []

    def test_complete_json_threads_the_ceiling_fields_through(
        self, fake_provider, settings, monkeypatch
    ):
        _configure(settings, MODEL_SIZE_CEILING_ENTITLEMENT=ENTITLEMENT_KEY)
        monkeypatch.setattr(
            "stapel_core.comm.call", lambda *a, **k: {"allowed": False, "limit": 1}
        )
        result = services.complete_json("hello", "large", user_id="u-1")
        assert result["status"] == "failure"
        assert result["ceiling"] == "small"
        assert result["requested_size"] == "large"

    def test_summarize_threads_the_ceiling_fields_through(
        self, fake_provider, settings, monkeypatch
    ):
        _configure(settings, MODEL_SIZE_CEILING_ENTITLEMENT=ENTITLEMENT_KEY)
        monkeypatch.setattr(
            "stapel_core.comm.call", lambda *a, **k: {"allowed": False, "limit": 1}
        )
        result = services.summarize("some text", model_size="large", user_id="u-1")
        assert result["status"] == "failure"
        assert result["ceiling"] == "small"
        assert result["requested_size"] == "large"


@pytest.mark.django_db
class TestDenialWithoutUsableCap:
    """billing answered but gave nothing to cap TO — a bool-only entitlement
    or a denial from an unknown key/plan. Catalog misconfiguration: logged
    as an error, and still no ceiling (never a refusal for OUR bug)."""

    def test_denied_with_no_limit_applies_no_ceiling(
        self, fake_provider, settings, monkeypatch
    ):
        _configure(settings, MODEL_SIZE_CEILING_ENTITLEMENT=ENTITLEMENT_KEY)
        monkeypatch.setattr(
            "stapel_core.comm.call",
            lambda *a, **k: {"allowed": False, "limit": None, "reason": "not_in_plan"},
        )
        assert services.resolve_size_ceiling("u-1") is None

    def test_denied_with_no_limit_logs_an_error(
        self, fake_provider, settings, monkeypatch, caplog
    ):
        _configure(settings, MODEL_SIZE_CEILING_ENTITLEMENT=ENTITLEMENT_KEY)
        monkeypatch.setattr(
            "stapel_core.comm.call", lambda *a, **k: {"allowed": False, "limit": None}
        )
        with caplog.at_level("ERROR"):
            services.resolve_size_ceiling("u-1")
        assert "billing denied" in caplog.text

    def test_non_positive_limit_applies_no_ceiling_and_logs_an_error(
        self, fake_provider, settings, monkeypatch, caplog
    ):
        _configure(settings, MODEL_SIZE_CEILING_ENTITLEMENT=ENTITLEMENT_KEY)
        monkeypatch.setattr(
            "stapel_core.comm.call", lambda *a, **k: {"allowed": False, "limit": 0}
        )
        with caplog.at_level("ERROR"):
            assert services.resolve_size_ceiling("u-1") is None
        assert "non-positive" in caplog.text


@pytest.mark.django_db
class TestWorkspaceIdAccepted:
    """workspace_id is accepted at every call site (same signature shape as
    user_id/workspace_id elsewhere) but not consulted — no comm call is made
    for it, and it never changes the verdict."""

    def test_workspace_id_alone_is_still_no_identity(
        self, fake_provider, settings, monkeypatch
    ):
        _configure(settings, MODEL_SIZE_CEILING_ENTITLEMENT=ENTITLEMENT_KEY)

        def _boom(*a, **k):
            raise AssertionError("billing must not be asked with no user_id")

        monkeypatch.setattr("stapel_core.comm.call", _boom)
        assert services.resolve_size_ceiling(None, workspace_id="w-1") is None

    def test_workspace_id_does_not_change_the_billing_payload(
        self, fake_provider, settings, monkeypatch
    ):
        _configure(settings, MODEL_SIZE_CEILING_ENTITLEMENT=ENTITLEMENT_KEY)
        seen = {}

        def _record(name, payload):
            seen["name"], seen["payload"] = name, payload
            return {"allowed": True, "limit": None}

        monkeypatch.setattr("stapel_core.comm.call", _record)
        services.resolve_size_ceiling("u-1", workspace_id="w-1")
        assert seen["name"] == "billing.check_entitlement"
        assert seen["payload"] == {
            "user_id": "u-1", "key": ENTITLEMENT_KEY, "quantity": 1,
        }
