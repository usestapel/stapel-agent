"""Provider pricing tests — ported verbatim from the iron-benchmark research
harness.

Only the pricing-focused tests (plus the fixtures/constants they need) were
brought over; the mapper / adapter / validator / model-registry / runner
tests that live alongside them in the harness stay there — this module is
scoped to ``stapel_agent.stt.pricing`` / ``stapel_agent.diarization.pricing``.

Several upstream test modules reused test names like
``test_pricing_contract`` (each qualified by provider, e.g.
``test_gladia_pricing_contract``) independently of one another. Grouping one
class per provider here avoids any cross-provider name collisions while
keeping every method name, docstring, assertion and inline sample value
exactly as written in the source. The only changes from the originals are:
the import paths (now pointing at ``stapel_agent.stt.pricing.*`` /
``stapel_agent.diarization.pricing.*`` instead of
``pipeline.adapters.*_pricing``), the ``self`` parameter and indentation
needed to nest each function as a class method, and a local alias line at the
top of each method (e.g. ``estimate_cost = _assemblyai_estimate_cost``) so the
assertion bodies below it can call the bare name exactly as the source did.

A number of upstream pricing-adjacent tests were LEFT BEHIND rather than
ported, because they exercise machinery this port does not carry over
(the model config registry ``pipeline.models.registry``, the adapter
registry, ``run_pipeline``, or a ``docs/provider_catalog.yaml`` fixture) —
see the module docstring notes in the calling agent's report for the full
list; none of those upstream tests were dropped as "redundant", they are
simply out of scope for a pricing-tables-only port.

Sources (ironmemo-backend, origin/feature/benchmark-harness):
  iron-benchmark/pipeline/tests/test_assemblyai.py
  iron-benchmark/pipeline/tests/test_deepgram.py
  iron-benchmark/pipeline/tests/test_elevenlabs.py
  iron-benchmark/pipeline/tests/test_gladia_p66.py
  iron-benchmark/pipeline/tests/test_soniox_p76.py
  iron-benchmark/pipeline/tests/test_speechmatics_p73.py
  iron-benchmark/pipeline/tests/test_xai_stt_p76.py
  iron-benchmark/pipeline/tests/test_pyannote_hybrid_p84.py
  iron-benchmark/pipeline/tests/test_aai_u35pro_p65.py
  iron-benchmark/pipeline/tests/test_p116_deepgram_keyterm.py
"""

import pytest

from stapel_agent.stt.pricing.assemblyai import (
    estimate_cost as _assemblyai_estimate_cost,
)
from stapel_agent.stt.pricing.deepgram import (
    NOVA3_BATCH_PRICE_PER_HOUR,
    NOVA3_KEYTERM_ADDON_PER_MIN,
    NOVA3_PRICING,
)
from stapel_agent.stt.pricing.deepgram import estimate_cost as _deepgram_estimate_cost
from stapel_agent.stt.pricing.elevenlabs import SCRIBE_V2_PRICE_PER_HOUR
from stapel_agent.stt.pricing.elevenlabs import (
    estimate_cost as _elevenlabs_estimate_cost,
)
from stapel_agent.stt.pricing.gladia import estimate_cost as _gladia_estimate_cost
from stapel_agent.stt.pricing.soniox import estimate_cost as _soniox_estimate_cost
from stapel_agent.stt.pricing.speechmatics import ENHANCED_BATCH_PRICE_PER_HOUR
from stapel_agent.stt.pricing.speechmatics import (
    estimate_cost as _speechmatics_estimate_cost,
)
from stapel_agent.stt.pricing.xai_stt import STT_STREAMING_PRICE_PER_HOUR
from stapel_agent.stt.pricing.xai_stt import estimate_cost as _xai_stt_estimate_cost
from stapel_agent.diarization.pricing import pyannote as _pyannote_pricing

_HOUR_MS = 3_600_000


# =============================================================================
# AssemblyAI — source: iron-benchmark/pipeline/tests/test_assemblyai.py,
#                       iron-benchmark/pipeline/tests/test_aai_u35pro_p65.py
# =============================================================================

class TestAssemblyAIPricing:
    """Mirrors ``test_assemblyai.py``'s / ``test_aai_u35pro_p65.py``'s pricing
    coverage."""

    # --- test 5: pricing -------------------------------------------------

    def test_pricing(self):
        estimate_cost = _assemblyai_estimate_cost
        # 77.5 s at ($0.15 + $0.02)/hr, pro-rated per second:
        # 77.5/3600 * 0.17 = 0.00365972... -> 0.00366 (rounded to 6 dp)
        assert estimate_cost(77_500, model="universal-2", diarization=True) == \
            pytest.approx(77.5 / 3600 * 0.17, abs=1e-6)
        # without diarization only the base rate applies
        assert estimate_cost(3_600_000, model="universal-2", diarization=False) == 0.15
        # universal-3-5-pro rate verified 2026-07-09 (P65) -> priced, no longer None
        assert estimate_cost(3_600_000, model="universal-3-5-pro") == 0.23
        # unknown model -> None, never a fabricated 0
        assert estimate_cost(60_000, model="made-up-model") is None

    # --- unified pricing contract (test_aai_u35pro_p65.py) ----------------

    def test_aai_pricing_u35pro(self):
        aai_cost = _assemblyai_estimate_cost
        assert aai_cost(_HOUR_MS, model="universal-3-5-pro") == 0.23
        assert aai_cost(_HOUR_MS, model="universal-3-5-pro", diarization=False) == 0.21
        assert aai_cost(_HOUR_MS, model="universal-2") == 0.17    # unchanged
        assert aai_cost(_HOUR_MS, model="made-up-model") is None  # never a fake $0


# =============================================================================
# Deepgram — source: iron-benchmark/pipeline/tests/test_deepgram.py,
#                     iron-benchmark/pipeline/tests/test_p116_deepgram_keyterm.py
# =============================================================================

class TestDeepgramPricing:
    """Mirrors ``test_deepgram.py``'s / ``test_p116_deepgram_keyterm.py``'s
    pricing coverage."""

    # --- test 8: pricing is source-dated and priced from the verified rate --

    def test_pricing_source_dated(self):
        estimate_cost = _deepgram_estimate_cost
        row = NOVA3_PRICING["nova-3"]
        assert row["source"] == "https://deepgram.com/pricing"
        assert row["verified_date"] == "2026-08-28"
        # 2026-08-28 card: mono $0.0043/min, Speaker Diarization INCLUDED on
        # pre-recorded (the $0.0020/min add-on is charged on streaming). Our
        # adapter always sends diarize_model, so the default estimate is the
        # base rate: 1 minute -> $0.0043.
        assert estimate_cost(60_000) == pytest.approx(0.0043, abs=1e-9)
        # and asking for it without diarization costs exactly the same
        assert estimate_cost(60_000, diarization=False) == pytest.approx(0.0043, abs=1e-9)
        # pro-rating: half a minute is exactly half the effective rate
        assert estimate_cost(30_000) == pytest.approx(0.00215, abs=1e-9)
        # multilingual variant: $0.0052/min ($0.312/hr)
        assert estimate_cost(60_000, multilingual=True) == pytest.approx(0.0052, abs=1e-9)
        assert estimate_cost(3_600_000, multilingual=True) == pytest.approx(0.312, abs=1e-6)
        # Growth tier prices the base at the Growth column
        assert estimate_cost(60_000, tier="growth", diarization=False) == \
            pytest.approx(0.0036, abs=1e-9)
        assert estimate_cost(60_000, tier="growth") == pytest.approx(0.0036, abs=1e-9)
        # hourly convenience constants
        assert NOVA3_BATCH_PRICE_PER_HOUR == pytest.approx(0.258, abs=1e-6)
        # effective default (mono, diarization included) over one hour
        assert estimate_cost(3_600_000) == pytest.approx(0.258, abs=1e-6)
        # unknown model -> None, never a fabricated 0
        assert estimate_cost(60_000, model="nova-9") is None

    def test_the_streaming_diarization_addon_never_reaches_a_batch_estimate(self):
        """The defect this file now guards. Between 2026-07-09 and 2026-08-28
        this module added a $0.0020/min diarization add-on to every batch
        estimate; the add-on is a STREAMING line, and a diarized monolingual
        hour was overstated by 58% ($0.408 against $0.258)."""
        from stapel_agent.stt.pricing.deepgram import (
            NOVA3_DIARIZATION_ADDON_STREAMING_PER_MIN,
        )

        estimate_cost = _deepgram_estimate_cost
        assert NOVA3_DIARIZATION_ADDON_STREAMING_PER_MIN == pytest.approx(0.0020)
        assert NOVA3_PRICING["nova-3"]["diarization_batch_included"] is True
        assert estimate_cost(3_600_000, diarization=True) == \
            estimate_cost(3_600_000, diarization=False)

    # -- pricing (test_p116_deepgram_keyterm.py) ---------------------------

    def test_pricing_keyterm_addon_opt_in(self):
        estimate_cost = _deepgram_estimate_cost
        assert NOVA3_KEYTERM_ADDON_PER_MIN == pytest.approx(0.0013)
        base = estimate_cost(60_000)
        with_kt = estimate_cost(60_000, keyterm=True)
        assert with_kt == pytest.approx(base + 0.0013)
        assert estimate_cost(60_000) == pytest.approx(base)   # default unchanged
        # The Growth keyterm rate IS published on the 2026-08-28 card
        # ($0.0012/min); before it was, this module charged PAYG on Growth
        # rather than invent a discount.
        growth_base = estimate_cost(60_000, tier="growth")
        assert estimate_cost(60_000, tier="growth", keyterm=True) == \
            pytest.approx(growth_base + 0.0012)


# =============================================================================
# ElevenLabs — source: iron-benchmark/pipeline/tests/test_elevenlabs.py,
#                       iron-benchmark/pipeline/tests/test_aai_u35pro_p65.py
# =============================================================================

class TestElevenLabsPricing:
    """Mirrors ``test_elevenlabs.py``'s / ``test_aai_u35pro_p65.py``'s pricing
    coverage."""

    # --- pricing (test_elevenlabs.py) -------------------------------------

    def test_pricing_five_minutes(self):
        estimate_cost = _elevenlabs_estimate_cost
        # 5 min audio = 5/60 hr * $0.22 = $0.018333...
        assert estimate_cost(5 * 60 * 1000) == 0.018333
        assert SCRIBE_V2_PRICE_PER_HOUR == 0.22

    def test_pricing_zero(self):
        estimate_cost = _elevenlabs_estimate_cost
        assert estimate_cost(0) == 0.0

    # --- unified pricing contract (test_aai_u35pro_p65.py) ----------------

    def test_el_pricing_accepts_model_kwarg(self):
        el_cost = _elevenlabs_estimate_cost
        assert el_cost(_HOUR_MS) == 0.22                 # pre-P65 call shape intact
        assert el_cost(_HOUR_MS, model="scribe_v2") == 0.22
        assert el_cost(_HOUR_MS, model="made-up-model") is None


# =============================================================================
# Gladia — source: iron-benchmark/pipeline/tests/test_gladia_p66.py
# =============================================================================

class TestGladiaPricing:
    """Mirrors ``test_gladia_p66.py``'s pricing-contract coverage."""

    # --- provider registry + pricing contract -----------------------------

    def test_gladia_pricing_contract(self):
        gladia_cost = _gladia_estimate_cost
        assert gladia_cost(_HOUR_MS, model="solaria-1") == 0.61
        assert gladia_cost(_HOUR_MS, model="solaria-3") == 0.61
        assert gladia_cost(_HOUR_MS, model="made-up-model") is None  # never fake $0
        # billing_time = duration x channels (schema fact): stereo doubles it.
        assert gladia_cost(_HOUR_MS, model="solaria-1", channels=2) == 1.22
        assert gladia_cost(1_800_000, model="solaria-1") == 0.305


# =============================================================================
# Soniox — source: iron-benchmark/pipeline/tests/test_soniox_p76.py
# =============================================================================

class TestSonioxPricing:
    """Mirrors ``test_soniox_p76.py``'s pricing-contract coverage."""

    # --- provider registry + pricing contract -----------------------------

    def test_soniox_pricing_contract(self):
        soniox_cost = _soniox_estimate_cost
        assert soniox_cost(_HOUR_MS) == 0.10
        assert soniox_cost(_HOUR_MS, model="stt-async-v5") == 0.10
        # the v4 alias routes to v5 server-side — same rate
        assert soniox_cost(_HOUR_MS, model="stt-async-v4") == 0.10
        assert soniox_cost(_HOUR_MS, model="made-up") is None   # never fake $0
        assert soniox_cost(1_800_000) == 0.05                   # pro-rated


# =============================================================================
# Speechmatics — source: iron-benchmark/pipeline/tests/test_speechmatics_p73.py
# =============================================================================

class TestSpeechmaticsPricing:
    """Mirrors ``test_speechmatics_p73.py``'s pricing-contract coverage."""

    # --- provider registry + pricing contract -----------------------------

    def test_speechmatics_pricing_contract(self):
        sm_cost = _speechmatics_estimate_cost
        assert sm_cost(_HOUR_MS, model="melia-1") == 0.129
        assert sm_cost(_HOUR_MS, model="standard") == 0.24
        assert sm_cost(_HOUR_MS, model="enhanced") == ENHANCED_BATCH_PRICE_PER_HOUR
        assert sm_cost(_HOUR_MS, model="made-up-model") is None  # never fake $0
        assert sm_cost(1_800_000, model="melia-1") == 0.0645     # pro-rated


# =============================================================================
# xAI STT — source: iron-benchmark/pipeline/tests/test_xai_stt_p76.py
# =============================================================================

class TestXaiSttPricing:
    """Mirrors ``test_xai_stt_p76.py``'s pricing-contract coverage."""

    # --- provider registry + pricing contract -----------------------------

    def test_xai_pricing_contract(self):
        xai_cost = _xai_stt_estimate_cost
        assert xai_cost(_HOUR_MS) == 0.10
        assert xai_cost(_HOUR_MS, model="stt-rest") == 0.10
        assert (xai_cost(_HOUR_MS, model="stt-streaming")
                == STT_STREAMING_PRICE_PER_HOUR)
        assert xai_cost(_HOUR_MS, model="made-up") is None   # never fake $0
        assert xai_cost(1_800_000) == 0.05                   # pro-rated


# =============================================================================
# pyannote diarization — source: iron-benchmark/pipeline/tests/test_pyannote_hybrid_p84.py
# =============================================================================

class TestPyannoteDiarPricing:
    """Mirrors ``test_pyannote_hybrid_p84.py``'s pricing coverage."""

    # --- pricing -----------------------------------------------------------

    def test_pricing_rate_card_and_min_charge(self):
        pricing = _pyannote_pricing
        # 300 s at EUR 0.112/hr
        assert pricing.estimate_cost_eur(300_000) == round(300 / 3600 * 0.112, 6)
        # 8 s job bills as the 20 s minimum (billing.md)
        assert pricing.estimate_cost_eur(8_000) == round(20 / 3600 * 0.112, 6)
        # USD equivalent uses the pinned rate
        eur = pricing.estimate_cost_eur(300_000)
        assert pricing.estimate_cost(300_000) == round(
            eur * pricing.EUR_USD_RATE, 6)
        # unknown model -> None, never a fabricated 0
        assert pricing.estimate_cost_eur(300_000, model="precision-9") is None


# =============================================================================
# Cross-provider pricing contract — source:
#   iron-benchmark/pipeline/tests/test_aai_u35pro_p65.py
# =============================================================================

class TestCrossProviderPricingContract:
    """Mirrors ``test_aai_u35pro_p65.py``'s uniform-call-shape coverage."""

    def test_pricing_contract_uniform_across_providers(self):
        el_cost = _elevenlabs_estimate_cost
        dg_cost = _deepgram_estimate_cost
        aai_cost = _assemblyai_estimate_cost
        # The runner calls estimate_cost(duration_ms, model=config.model_id) for
        # EVERY provider — each module must accept the keyword.
        assert el_cost(_HOUR_MS, model="scribe_v2") is not None
        assert dg_cost(_HOUR_MS, model="nova-3") is not None
        assert aai_cost(_HOUR_MS, model="universal-3-5-pro") is not None
