"""The STT model-config registry and the pricing-module registry it asks.

These are the tests that could not travel with the 0.6.5 pricing port: they
exercise a catalog of model configs and a registry mapping a provider to its
rate card, neither of which existed here yet. Sources (ironmemo-backend,
origin/feature/benchmark-harness):

  iron-benchmark/pipeline/tests/test_model_registry.py
      test_pricing_from_pricing_modules
  iron-benchmark/pipeline/tests/test_dg_multi_pricing_p70.py   (whole module)
  iron-benchmark/pipeline/tests/test_aai_u35pro_p65.py
      test_u35pro_pricing_from_rate_card
  iron-benchmark/pipeline/tests/test_gladia_p66.py
      test_gladia_pricing_one_shared_async_rate
  iron-benchmark/pipeline/tests/test_speechmatics_p73.py
      test_speechmatics_pricing_per_model
  iron-benchmark/pipeline/tests/test_soniox_p76.py
      test_soniox_pricing_and_quality_are_honest
  iron-benchmark/pipeline/tests/test_xai_stt_p76.py
      test_xai_pricing_and_quality_are_honest
  iron-benchmark/pipeline/tests/test_pyannote_hybrid_p84.py
      test_catalog_pyannote_rate_matches_pricing_module

Three upstream assertions are deliberately NOT reproduced, because what they
assert does not exist in a framework:

- measured WER (``_MEASURED_WER_BY_CONFIG``). A quality figure without the
  corpus it was measured on is a rumour; the harness keeps its corpus and its
  numbers. The two upstream tests named ``..._pricing_and_quality_are_honest``
  therefore keep their pricing and their WARNING assertions — the warnings are
  provider-contract facts, which do travel — and drop the WER equality.
- ``test_catalog_pyannote_rate_matches_pricing_module`` cross-checked a
  ``docs/provider_catalog.yaml`` against the pricing module. That YAML was the
  second copy of the rate card; here there is no second copy, so the test
  becomes what it was really guarding: the hybrid's declared diarization model
  must be one the rate card actually prices.
- the two ``run_pipeline`` tests in test_dg_multi_pricing_p70.py asserted that
  the RUNNER prices dg-multi at its own variant. There is no runner here; the
  seam those tests guard is ``estimate_cost(config, duration_ms)``, which is
  the one computation a runner would call.
"""

from __future__ import annotations

import pytest

from stapel_agent.diarization.pricing import pyannote as _pyannote_pricing
from stapel_agent.stt import BUILTIN_STT_PROVIDERS
from stapel_agent.stt.model_configs import (
    HOUR_MS,
    BUILTIN_STT_MODEL_CONFIGS,
    ModelConfig,
    estimate_cost,
    get_config,
    get_default_config,
    hourly_rate,
    list_configs,
    register_stt_model_config,
    registered_stt_model_configs,
    resolve_config,
)
from stapel_agent.stt.model_configs import (
    _reset_runtime_stt_model_configs,
)
from stapel_agent.stt.pricing import (
    BUILTIN_STT_PRICING_MODULES,
    pricing_module,
    register_stt_pricing_module,
    registered_stt_pricing_modules,
)
from stapel_agent.stt.pricing import (
    _reset_runtime_stt_pricing_modules,
)
from stapel_agent.stt.pricing.assemblyai import (
    DIARIZATION_ADDON_PER_HOUR_USD,
    RATES_PER_HOUR_USD,
)
from stapel_agent.stt.pricing.deepgram import (
    NOVA3_BATCH_PRICE_PER_HOUR,
    NOVA3_MULTI_PRICE_PER_HOUR,
    RATE_CARD_VERSION,
)
from stapel_agent.stt.pricing.elevenlabs import SCRIBE_V2_PRICE_PER_HOUR
from stapel_agent.stt.pricing.gladia import GLADIA_ASYNC_PRICE_PER_HOUR
from stapel_agent.stt.pricing.soniox import STT_ASYNC_V5_PRICE_PER_HOUR
from stapel_agent.stt.pricing.speechmatics import (
    MELIA1_BATCH_PRICE_PER_HOUR,
    STANDARD_BATCH_PRICE_PER_HOUR,
)
from stapel_agent.stt.pricing.xai_stt import STT_REST_PRICE_PER_HOUR


@pytest.fixture(autouse=True)
def clean_runtime_registries():
    _reset_runtime_stt_model_configs()
    _reset_runtime_stt_pricing_modules()
    yield
    _reset_runtime_stt_model_configs()
    _reset_runtime_stt_pricing_modules()


# =============================================================================
# The pricing-module registry
# =============================================================================

class TestPricingModuleRegistry:
    def test_every_priced_provider_resolves_to_its_module(self):
        for name in BUILTIN_STT_PRICING_MODULES:
            module = pricing_module(name)
            assert module is not None, name
            assert callable(module.estimate_cost), name

    def test_keys_are_stt_provider_registry_names(self):
        """One name for the adapter and its rate card. The vendor's own id
        ("xai") is NOT the key — ``xai-stt`` is, because that is what the
        provider registry answers to."""
        assert set(BUILTIN_STT_PRICING_MODULES) <= set(BUILTIN_STT_PROVIDERS)
        assert "xai-stt" in BUILTIN_STT_PRICING_MODULES

    def test_unpriced_provider_is_none_not_zero(self):
        """A self-hosted endpoint costs something; we just do not know what.
        Returning a $0 stub would report someone's GPU bill as free."""
        assert "whisper-http" in BUILTIN_STT_PROVIDERS
        assert pricing_module("whisper-http") is None
        assert pricing_module("no-such-provider") is None

    def test_host_can_register_its_own_rate_card(self):
        from stapel_agent.stt.pricing import elevenlabs

        register_stt_pricing_module("house-stt", elevenlabs)
        assert pricing_module("house-stt") is elevenlabs

    def test_dotted_path_registration_is_resolved_lazily(self):
        register_stt_pricing_module(
            "house-stt", "stapel_agent.stt.pricing.gladia")
        assert pricing_module("house-stt").GLADIA_ASYNC_PRICE_PER_HOUR == 0.61

    def test_a_provider_can_be_declared_unpriced(self):
        register_stt_pricing_module("deepgram", None)
        assert pricing_module("deepgram") is None
        assert "deepgram" not in registered_stt_pricing_modules()

    def test_a_non_module_is_refused(self):
        with pytest.raises(TypeError, match="estimate_cost"):
            register_stt_pricing_module("house-stt", object())

    def test_settings_overlay_merges_over_builtins(self, settings):
        settings.STAPEL_AGENT = {
            "STT_PRICING_MODULES": {
                "house-stt": "stapel_agent.stt.pricing.soniox"}}
        effective = registered_stt_pricing_modules()
        # added without restating the built-ins
        assert "house-stt" in effective
        assert set(BUILTIN_STT_PRICING_MODULES) <= set(effective)


# =============================================================================
# The catalog itself
# =============================================================================

class TestCatalogWiring:
    def test_every_config_names_a_registered_provider(self):
        """The seam this catalog exists to close: a config that names a
        provider nobody registered is a run that cannot happen."""
        for c in list_configs(admin_visible_only=False):
            assert c.provider_id in BUILTIN_STT_PROVIDERS, c.model_config_id

    def test_every_config_is_priced(self):
        """Every shipped config names a provider with a rate card — a shipped
        profile whose cost is unknowable would be a trap."""
        for c in list_configs(admin_visible_only=False):
            assert hourly_rate(c) is not None, c.model_config_id

    def test_exactly_one_default_per_provider(self):
        defaults: dict[str, list[str]] = {}
        for c in list_configs(admin_visible_only=False):
            if c.is_default:
                defaults.setdefault(c.provider_id, []).append(c.model_config_id)
        for provider, ids in defaults.items():
            assert len(ids) == 1, f"{provider}: {ids}"
        # every provider that has a config has a default
        providers = {c.provider_id for c in list_configs(admin_visible_only=False)}
        assert set(defaults) == providers

    def test_config_ids_are_unique_and_self_describing(self):
        for cid, c in BUILTIN_STT_MODEL_CONFIGS.items():
            assert cid == c.model_config_id

    def test_unknown_id_raises_value_error_listing_the_known_ones(self):
        with pytest.raises(ValueError, match="unknown STT model config"):
            get_config("nope")


class TestAttribution:
    def test_provider_only_run_resolves_to_the_default(self):
        assert (resolve_config("gladia", "en").model_config_id
                == "gladia_solaria1")
        assert (resolve_config("speechmatics", "en").model_config_id
                == "speechmatics_melia1")
        assert resolve_config("xai-stt", "en").model_config_id == "xai_stt"
        assert (resolve_config("soniox", "en").model_config_id
                == "soniox_stt_async_v5")
        assert (resolve_config("assemblyai", "en").model_config_id
                == "assemblyai_universal2_default")

    def test_language_specific_profile_wins_the_exact_match(self):
        """A Deepgram ``multi`` run is billed at the multilingual rate, so it
        must be attributed to the multilingual profile."""
        assert (resolve_config("deepgram", "multi").model_config_id
                == "deepgram_nova3_multi")
        assert (resolve_config("deepgram", "en").model_config_id
                == "deepgram_nova3_default")

    def test_unknown_language_falls_back_to_the_provider_default(self):
        assert (resolve_config("deepgram", "sw").model_config_id
                == "deepgram_nova3_default")

    def test_default_config_needs_a_provider(self):
        """There is no global "best": ranking configs needs a measurement and
        this library has no corpus."""
        with pytest.raises(TypeError):
            get_default_config()          # type: ignore[call-arg]
        with pytest.raises(ValueError, match="no default STT model config"):
            get_default_config("no-such-provider")


class TestPricingFromPricingModules:
    """Mirrors ``test_model_registry.py::test_pricing_from_pricing_modules``."""

    def test_elevenlabs(self):
        assert (hourly_rate(get_config("elevenlabs_scribe_v2_default"))
                == SCRIBE_V2_PRICE_PER_HOUR)

    def test_deepgram_mono_prices_diarization_as_included(self):
        # Our adapter always sends diarize_model, and on the 2026-08-28 card
        # pre-recorded diarization is Included — so the diarized rate a run
        # actually incurs IS the base batch rate. The literal is here because
        # the line above it would also pass against a card that quietly
        # reintroduced an add-on.
        assert (hourly_rate(get_config("deepgram_nova3_default"))
                == NOVA3_BATCH_PRICE_PER_HOUR)
        assert hourly_rate(get_config("deepgram_nova3_default")) == 0.258

    def test_deepgram_multi_is_its_own_variant(self):
        assert (hourly_rate(get_config("deepgram_nova3_multi"))
                == NOVA3_MULTI_PRICE_PER_HOUR)
        assert hourly_rate(get_config("deepgram_nova3_multi")) == 0.312

    def test_assemblyai_universal2(self):
        assert hourly_rate(get_config("assemblyai_universal2_default")) == round(
            RATES_PER_HOUR_USD["universal-2"] + DIARIZATION_ADDON_PER_HOUR_USD, 6)

    def test_u35pro_pricing_from_rate_card(self):
        """Mirrors ``test_aai_u35pro_p65.py::test_u35pro_pricing_from_rate_card``."""
        c = get_config("assemblyai_u35pro")
        assert hourly_rate(c) == round(
            RATES_PER_HOUR_USD["universal-3-5-pro"]
            + DIARIZATION_ADDON_PER_HOUR_USD, 6)
        assert hourly_rate(c) == 0.23      # $0.21 base + $0.02 diarization

    def test_gladia_pricing_one_shared_async_rate(self):
        """Mirrors ``test_gladia_p66.py::test_gladia_pricing_one_shared_async_rate``."""
        for cid in ("gladia_solaria1", "gladia_solaria3"):
            assert hourly_rate(get_config(cid)) == GLADIA_ASYNC_PRICE_PER_HOUR
        assert GLADIA_ASYNC_PRICE_PER_HOUR == 0.61   # verified 2026-07-09

    def test_speechmatics_pricing_per_model(self):
        """Mirrors ``test_speechmatics_p73.py::test_speechmatics_pricing_per_model``."""
        assert (hourly_rate(get_config("speechmatics_melia1"))
                == MELIA1_BATCH_PRICE_PER_HOUR == 0.129)
        assert (hourly_rate(get_config("speechmatics_batch_standard"))
                == STANDARD_BATCH_PRICE_PER_HOUR == 0.24)

    def test_soniox_pricing_and_warnings_are_honest(self):
        """Mirrors ``test_soniox_p76.py::test_soniox_pricing_and_quality_are_honest``
        minus the measured-WER assertion (no corpus here)."""
        c = get_config("soniox_stt_async_v5")
        assert hourly_rate(c) == STT_ASYNC_V5_PRICE_PER_HOUR == 0.10
        assert any("NOT auto-deleted" in w for w in c.warnings)
        assert any("SUB-WORD" in w for w in c.warnings)

    def test_xai_pricing_and_warnings_are_honest(self):
        """Mirrors ``test_xai_stt_p76.py::test_xai_pricing_and_quality_are_honest``
        minus the measured-WER assertion (no corpus here)."""
        c = get_config("xai_stt")
        assert hourly_rate(c) == STT_REST_PRICE_PER_HOUR == 0.10
        assert any("not pinnable" in w.lower() for w in c.warnings)
        assert any("empty" in w for w in c.warnings)   # language-detection note


class TestPriceVariantChannel:
    """Mirrors ``test_dg_multi_pricing_p70.py``'s catalog-wiring coverage."""

    def test_dg_multi_is_the_only_config_with_pricing_kwargs(self):
        assert (get_config("deepgram_nova3_multi").pricing_kwargs
                == {"multilingual": True})
        for c in list_configs(admin_visible_only=False):
            if c.model_config_id != "deepgram_nova3_multi":
                assert c.pricing_kwargs == {}, \
                    f"{c.model_config_id}: unexpected pricing_kwargs"

    def test_construction_without_pricing_kwargs_stays_valid(self):
        assert ModelConfig(
            model_config_id="x", display_name="X", provider_id="deepgram",
            model_id="nova-3", default_language="en",
        ).pricing_kwargs == {}

    def test_both_deepgram_configs_share_a_wire_model(self):
        """Which is the whole reason the price variant needs its own channel:
        ``model=`` alone cannot tell the two rates apart."""
        assert (get_config("deepgram_nova3_default").model_id
                == get_config("deepgram_nova3_multi").model_id == "nova-3")

    def test_estimate_prices_dg_multi_by_its_variant(self):
        """The runner-wiring test, at the seam a runner would call."""
        assert (estimate_cost(get_config("deepgram_nova3_multi"), 5000)
                == round(5000 / HOUR_MS * 0.312, 6))

    def test_estimate_prices_dg_mono_unchanged(self):
        assert (estimate_cost(get_config("deepgram_nova3_default"), 5000)
                == round(5000 / HOUR_MS * 0.258, 6))

    def test_rate_and_estimate_are_one_computation(self):
        """Mirrors ``test_every_card_rate_equals_its_module_hour_estimate``.
        Upstream this compared a stored card against the module; here the card
        IS the module call, so the gate is over host-registered configs too."""
        from stapel_agent.stt.pricing import pricing_module as _module

        for c in list_configs(admin_visible_only=False):
            module = _module(c.provider_id)
            expected = module.estimate_cost(
                HOUR_MS, model=c.model_id, **c.pricing_kwargs)
            if c.diarization:
                expected += _pyannote_pricing.estimate_cost(
                    HOUR_MS, model=c.diarization["model"])
            assert hourly_rate(c) == round(expected, 6), c.model_config_id


class TestHybridConfigs:
    def test_hybrid_inherits_the_stt_call_verbatim(self):
        """A hybrid that quietly changed the transcription request would not
        be comparable with its base."""
        base = get_config("xai_stt")
        hybrid = get_config("xai_stt__pyannote_diar")
        assert hybrid.provider_id == base.provider_id
        assert hybrid.model_id == base.model_id
        assert hybrid.provider_params == base.provider_params
        assert hybrid.adapter_kwargs == base.adapter_kwargs
        assert hybrid.pricing_kwargs == base.pricing_kwargs

    def test_hybrid_never_becomes_a_provider_default(self):
        for c in list_configs(admin_visible_only=False):
            if c.diarization:
                assert c.is_default is False, c.model_config_id
        # ...so resolving the provider still lands on the plain profile
        assert resolve_config("xai-stt", "en").model_config_id == "xai_stt"

    def test_hybrid_declares_a_registered_diarization_provider(self):
        from stapel_agent.diarization import BUILTIN_DIARIZATION_PROVIDERS

        for c in list_configs(admin_visible_only=False):
            if c.diarization:
                assert c.diarization["provider"] in BUILTIN_DIARIZATION_PROVIDERS

    def test_hybrid_diar_model_is_one_the_rate_card_prices(self):
        """What ``test_catalog_pyannote_rate_matches_pricing_module`` was
        really guarding, now that there is no second copy of the card."""
        for c in list_configs(admin_visible_only=False):
            if c.diarization:
                assert (c.diarization["model"]
                        in _pyannote_pricing.RATES_EUR_PER_HOUR)

    def test_hybrid_rate_is_the_sum_of_both_stages(self):
        stt = hourly_rate(get_config("xai_stt"))
        diar = _pyannote_pricing.estimate_cost(HOUR_MS, model="precision-2")
        assert hourly_rate(get_config("xai_stt__pyannote_diar")) == round(
            stt + diar, 6)

    def test_hybrid_warns_that_diarization_is_billed_separately(self):
        c = get_config("speechmatics_melia1__pyannote_diar")
        assert any("billed separately" in w for w in c.warnings)
        # the base's own warnings are kept, not replaced
        base = get_config("speechmatics_melia1")
        assert set(base.warnings) <= set(c.warnings)


class TestUncoveredPricingFunctions:
    """Two functions the upstream harness never covered directly."""

    def test_deepgram_rate_card_version_is_dated_provenance(self):
        """Runners stamp this so an A/B whose arms were priced on different
        cards is machine-detectable. A version string with no date could not
        do that job."""
        from stapel_agent.stt.pricing.deepgram import (
            NOVA3_PRICING,
            NOVA3_RATE_CARD_VERSION,
        )

        assert RATE_CARD_VERSION == NOVA3_RATE_CARD_VERSION
        assert RATE_CARD_VERSION == (
            f"deepgram_{NOVA3_PRICING['nova-3']['verified_date']}")
        assert RATE_CARD_VERSION.startswith("deepgram_20")

    def test_pyannote_billable_seconds_applies_the_minimum_charge(self):
        billable = _pyannote_pricing.billable_seconds
        minimum = _pyannote_pricing.MIN_BILLABLE_SECONDS
        # a job shorter than the minimum still bills the minimum
        assert billable(8_000) == minimum
        assert billable(0) == minimum
        # at and above it, the real duration bills
        assert billable(int(minimum * 1000)) == minimum
        assert billable(300_000) == 300.0

    def test_pyannote_minimum_charge_reaches_the_estimate(self):
        """The minimum is the reason a 5-second clip is not almost free."""
        five_seconds = _pyannote_pricing.estimate_cost_eur(5_000)
        assert five_seconds == _pyannote_pricing.estimate_cost_eur(20_000)


class TestHostRegistration:
    def test_registered_config_is_addressable_and_priced(self):
        register_stt_model_config(ModelConfig(
            model_config_id="house_dg_growth",
            display_name="Deepgram Nova-3 (Growth tier)",
            provider_id="deepgram",
            model_id="nova-3",
            default_language="en",
            pricing_kwargs={"tier": "growth"},
        ))
        c = get_config("house_dg_growth")
        assert c.display_name == "Deepgram Nova-3 (Growth tier)"
        # the Growth tier really is cheaper than PAYG — priced, not copied
        assert hourly_rate(c) < hourly_rate(get_config("deepgram_nova3_default"))

    def test_registration_overrides_a_shipped_id(self):
        register_stt_model_config(ModelConfig(
            model_config_id="xai_stt", display_name="ours",
            provider_id="xai-stt", model_id="stt-streaming",
            default_language="en", is_default=True,
        ))
        assert get_config("xai_stt").model_id == "stt-streaming"
        assert hourly_rate(get_config("xai_stt")) == 0.20

    def test_a_non_config_is_refused(self):
        with pytest.raises(TypeError, match="ModelConfig"):
            register_stt_model_config({"model_config_id": "x"})

    def test_settings_overlay_merges_over_builtins(self, settings):
        extra = ModelConfig(
            model_config_id="house_el", display_name="House EL",
            provider_id="elevenlabs", model_id="scribe_v2",
            default_language="en",
        )
        settings.STAPEL_AGENT = {"STT_MODEL_CONFIGS": {"house_el": extra}}
        effective = registered_stt_model_configs()
        assert effective["house_el"] is extra
        assert set(BUILTIN_STT_MODEL_CONFIGS) <= set(effective)

    def test_settings_can_mask_a_shipped_config(self, settings):
        settings.STAPEL_AGENT = {"STT_MODEL_CONFIGS": {"gladia_solaria3": None}}
        assert "gladia_solaria3" not in registered_stt_model_configs()
        assert "gladia_solaria1" in registered_stt_model_configs()

    def test_admin_visible_false_is_hidden_from_the_default_listing(self):
        register_stt_model_config(ModelConfig(
            model_config_id="house_hidden", display_name="hidden",
            provider_id="elevenlabs", model_id="scribe_v2",
            default_language="en", admin_visible=False,
        ))
        visible = {c.model_config_id for c in list_configs()}
        every = {c.model_config_id for c in list_configs(admin_visible_only=False)}
        assert "house_hidden" not in visible
        assert "house_hidden" in every

    def test_unpriced_provider_yields_no_estimate(self):
        """A config on a provider with no rate card estimates to None, not 0."""
        register_stt_model_config(ModelConfig(
            model_config_id="house_whisper", display_name="Self-hosted",
            provider_id="whisper-http", model_id="whisper-1",
            default_language="en",
        ))
        assert hourly_rate(get_config("house_whisper")) is None
        assert estimate_cost(get_config("house_whisper"), 5000) is None

    def test_unpriced_model_on_a_priced_provider_yields_none(self):
        register_stt_model_config(ModelConfig(
            model_config_id="house_future", display_name="Nova-9",
            provider_id="deepgram", model_id="nova-9",
            default_language="en",
        ))
        assert hourly_rate(get_config("house_future")) is None
