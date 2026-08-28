"""Named, priced, reproducible ways to call one STT provider.

A provider name alone does not identify a run. "deepgram" bills $0.258/hr
monolingual and $0.312/hr multilingual over the SAME wire model ``nova-3``;
"assemblyai" is $0.17/hr on Universal-2 and $0.23/hr on Universal-3.5 Pro;
"gladia" is one rate over two models that differ by 7 WER points. So the unit a
caller picks, a ledger attributes and a comparison compares is not the provider
— it is the config: provider + model + the params actually sent + the price
variant that combination bills at.

WHY THE CARD IS NOT A FIELD
---------------------------
The obvious shape is a ``pricing_per_hour`` number on each config, copied from
the rate-card module. That is two copies of one truth, and the copy drifts
silently: the rate card moves, the catalog keeps quoting last quarter, and
nobody finds out until an invoice disagrees with a dashboard. The same defect
already exists one layer up in this package — ``SttProvider.cost_per_hour``
carries hand-written ballparks (ElevenLabs 0.40, Soniox 0.36) that the ported
rate cards price at 0.22 and 0.10.

So a config carries no price. :func:`hourly_rate` and :func:`estimate_cost`
ASK the provider's rate-card module, passing ``pricing_kwargs`` — the card and
the estimate are one computation by construction, and a host that negotiated
its own rates registers a pricing module rather than editing numbers into
config objects.

WHAT IS NOT HERE
----------------
No measured quality. The research harness this came from carries per-config
WER measured on a referenced corpus; a framework has no corpus, and a quality
number without the corpus it was measured on is a rumour. Likewise no language
capability matrix and no feature flags: ``SttProvider.supported_languages`` /
``supports_diarization`` already answer those, from the adapter that has to
honour them.

There is also no global "best" default. ``get_default_config()`` in the
harness returned the best-measured config across providers; ranking needs a
measurement, so here the default is per provider and the caller says which.

HYBRIDS
-------
``diarization`` marks a config whose speaker turns come from a SEPARATE
provider (pyannoteAI precision-2) rather than from the STT response: the STT
call is unchanged, an external diarization stage runs after it, and the merge
replaces the speaker labels. ``provider_id`` stays the STT provider — routing
and keys for that stage are untouched — and the estimate is the sum of the two
stages' rate cards, because the invoice will be too.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

#: One audio hour, in milliseconds — the unit every rate card is quoted in.
HOUR_MS = 3_600_000


@dataclass(frozen=True)
class ModelConfig:
    """One reproducible way to call an STT provider.

    Attributes:
        model_config_id: Stable id recorded on the run, e.g.
            ``"deepgram_nova3_multi"``. The same id always means the same
            provider + model + params.
        display_name: Human label for a picker.
        provider_id: Key in the STT provider registry (``BUILTIN_STT_PROVIDERS``
            ← settings ← runtime) — NOT the vendor's own name for itself, so
            the config, its adapter and its rate card are one name.
        model_id: The wire model. Two configs may share it (both Deepgram
            profiles are ``nova-3``); that is what ``pricing_kwargs`` is for.
        default_language: The canonical code this config is tuned for.
            ``resolve_config`` exact-matches on it, which is how a Deepgram
            ``multi`` run reaches the multilingual profile.
        provider_params: What the adapter actually sends, in semantic Python
            types — documentation of the wire form, read from the adapter code,
            never a sketch of what it might send. Not a promise on trust:
            ``tests/test_stt_registry_conformance.py`` drives every config's
            adapter against a stub transport and compares this dict with the
            parameters that actually reached the request, so an entry the
            adapter does not send fails the build.
        adapter_kwargs: The per-registration pin this config needs — class
            attributes for the adapter subclass a host would register
            (``SttProvider.speech_model``), which is how two configs of ONE
            provider differ by MODEL rather than by language. The conformance
            test builds the adapter through exactly these attributes, so a
            name no adapter honours fails there too.
        pricing_kwargs: Extra kwargs for the rate card's ``estimate_cost`` —
            the price-variant channel for configs that share a wire model but
            bill differently.
        diarization: External diarization stage spec, or ``{}`` for none.
            Shape: ``{"provider": "pyannote-cloud", "model": "precision-2",
            "exclusive": True}``.
        warnings: Provider-contract facts a caller must see before choosing —
            things the API will not do, not things it does badly.
        is_default: The provider's default config. Exactly one per provider.
        doc_url: The provider doc these params were read from.
        admin_visible: Whether a picker should list it.
    """

    model_config_id: str
    display_name: str
    provider_id: str
    model_id: str
    default_language: str
    provider_params: dict[str, Any] = field(default_factory=dict)
    adapter_kwargs: dict[str, Any] = field(default_factory=dict)
    pricing_kwargs: dict[str, Any] = field(default_factory=dict)
    diarization: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    is_default: bool = False
    doc_url: str | None = None
    admin_visible: bool = True


# ── Pricing ───────────────────────────────────────────────────────────


def estimate_cost(config: ModelConfig, duration_ms: int) -> float | None:
    """USD estimate for running ``duration_ms`` of audio through ``config``.

    ``None`` when the provider has no published rate card — never 0.0, which
    would be indistinguishable from free.

    A hybrid config adds its diarization stage's own card: two stages, two
    line items on the invoice, one number here.
    """
    from .pricing import pricing_module

    module = pricing_module(config.provider_id)
    if module is None:
        return None
    total = module.estimate_cost(
        duration_ms, model=config.model_id, **config.pricing_kwargs)
    if total is None:
        return None
    if config.diarization:
        from ..diarization.pricing import pyannote

        diar = pyannote.estimate_cost(
            duration_ms, model=config.diarization.get("model"))
        if diar is None:
            return None
        total += diar
    return round(total, 6)


def hourly_rate(config: ModelConfig) -> float | None:
    """The config's USD rate card per audio hour — the same computation the
    estimate uses, asked for one hour. There is no second copy to drift."""
    return estimate_cost(config, HOUR_MS)


# ── The shipped catalog ───────────────────────────────────────────────
#
# Declaration order is load-bearing: ``resolve_config`` scans it, so each
# provider's default profile is declared BEFORE its alternates and new
# entries go last. ``whisper-http`` has no config — a self-hosted endpoint has
# no rate card and no model catalog to pin.

_DIARIZATION_STAGE = {
    "provider": "pyannote-cloud", "model": "precision-2", "exclusive": True,
}
_HYBRID_WARNING = (
    "Hybrid: pyannoteAI precision-2 turns REPLACE the provider's speaker "
    "labels; the diarization stage is billed separately (EUR rate card, 20 s "
    "minimum charge per job) and needs PYANNOTEAI_API_KEY"
)


def _hybrid_of(base: ModelConfig, *, config_id: str,
               display_name: str) -> ModelConfig:
    """A config that keeps ``base``'s STT call and swaps in external speakers.

    The STT wire form is inherited verbatim — a hybrid that quietly changed
    the transcription request would not be comparable with its base.
    """
    return replace(
        base,
        model_config_id=config_id,
        display_name=display_name,
        diarization=dict(_DIARIZATION_STAGE),
        warnings=(_HYBRID_WARNING, *base.warnings),
        is_default=False,
    )


_elevenlabs_scribe_v2 = ModelConfig(
    model_config_id="elevenlabs_scribe_v2_default",
    display_name="ElevenLabs Scribe v2",
    provider_id="elevenlabs",
    model_id="scribe_v2",
    default_language="en",
    provider_params={"model_id": "scribe_v2", "diarize": True,
                     "timestamps_granularity": "word"},
    # Pinned, not inherited: without the pin the config would run whatever
    # ``ELEVENLABS_STT_MODEL`` happens to say, and "the same id always means
    # the same model" would be a host setting away from false.
    adapter_kwargs={"speech_model": "scribe_v2"},
    is_default=True,
    doc_url="https://elevenlabs.io/docs/api-reference/speech-to-text/convert",
)

_deepgram_nova3 = ModelConfig(
    model_config_id="deepgram_nova3_default",
    display_name="Deepgram Nova-3 (monolingual)",
    provider_id="deepgram",
    model_id="nova-3",
    default_language="en",
    # ``paragraphs`` is NOT here: the adapter does not ask for it, because
    # the block it would add is one this library drops (turns come from
    # ``results.utterances``). ``filler_words`` IS here and IS sent — the
    # provider default deletes um/uh from the transcript.
    provider_params={"model": "nova-3", "diarize_model": "latest",
                     "utterances": True, "smart_format": True,
                     "filler_words": True},
    adapter_kwargs={"speech_model": "nova-3"},
    is_default=True,
    doc_url="https://developers.deepgram.com/docs/models-languages-overview",
)

_deepgram_nova3_multi = ModelConfig(
    model_config_id="deepgram_nova3_multi",
    display_name="Deepgram Nova-3 Multilingual (code-switch)",
    provider_id="deepgram",
    model_id="nova-3",
    default_language="multi",
    provider_params={"model": "nova-3", "language": "multi",
                     "diarize_model": "latest", "utterances": True,
                     "smart_format": True, "filler_words": True},
    adapter_kwargs={"speech_model": "nova-3"},
    # Same wire model as the mono default, a different price. Without this the
    # multilingual profile would be estimated at the monolingual rate and the
    # error would be invisible: both numbers are plausible.
    pricing_kwargs={"multilingual": True},
    doc_url="https://developers.deepgram.com/docs/models-languages-overview",
)

_assemblyai_universal2 = ModelConfig(
    model_config_id="assemblyai_universal2_default",
    display_name="AssemblyAI Universal-2",
    provider_id="assemblyai",
    model_id="universal-2",
    default_language="en",
    # ``speech_model`` (SINGULAR) is this adapter's wire field; the
    # research harness this catalog came from used the plural
    # ``speech_models`` array of a later docs generation. The catalog
    # documents THIS adapter, never the harness's.
    provider_params={"speech_model": "universal-2", "speaker_labels": True,
                     "punctuate": True, "format_text": True},
    adapter_kwargs={"speech_model": "universal-2"},
    is_default=True,
    doc_url=("https://www.assemblyai.com/docs/speech-to-text/"
             "pre-recorded-audio/supported-languages"),
)

_assemblyai_u35pro = ModelConfig(
    model_config_id="assemblyai_u35pro",
    display_name="AssemblyAI Universal-3.5 Pro",
    provider_id="assemblyai",
    model_id="universal-3-5-pro",
    default_language="en",
    # ONE model, pinned: a fallback chain over two models is a run that may
    # have used either, and such a run is not attributable.
    provider_params={"speech_model": "universal-3-5-pro",
                     "speaker_labels": True, "punctuate": True,
                     "format_text": True},
    adapter_kwargs={"speech_model": "universal-3-5-pro"},
    warnings=("RU/UK are not Universal-3.5 Pro native languages; the "
              "behaviour of a single-model pin there is unverified",),
    doc_url="https://www.assemblyai.com/docs/api-reference/transcripts/submit",
)

_gladia_solaria1 = ModelConfig(
    model_config_id="gladia_solaria1",
    display_name="Gladia Solaria-1 (multilingual)",
    provider_id="gladia",
    model_id="solaria-1",
    default_language="en",
    provider_params={"model": "solaria-1", "diarization": True},
    # Pinned even though it is also the API default: omitting the parameter
    # would run solaria-1 today and something else after a provider release.
    adapter_kwargs={"speech_model": "solaria-1"},
    is_default=True,
    doc_url="https://docs.gladia.io/chapters/pre-recorded-stt/quickstart",
)

_gladia_solaria3 = ModelConfig(
    model_config_id="gladia_solaria3",
    display_name="Gladia Solaria-3 (EN/FR/DE/ES/IT)",
    provider_id="gladia",
    model_id="solaria-3",
    default_language="en",
    provider_params={"model": "solaria-3", "diarization": True},
    adapter_kwargs={"speech_model": "solaria-3"},
    warnings=("solaria-3 is single-language (EN/FR/DE/ES/IT) and async-only; "
              "RU/UK/auto/multi are refused before any billable call — use "
              "the Solaria-1 config for those",),
    doc_url="https://docs.gladia.io/chapters/pre-recorded-stt/quickstart",
)

_speechmatics_melia1 = ModelConfig(
    model_config_id="speechmatics_melia1",
    display_name="Speechmatics Melia-1 (multilingual)",
    provider_id="speechmatics",
    model_id="melia-1",
    default_language="en",
    provider_params={"type": "transcription", "model": "melia-1",
                     "language": "multi", "diarization": "speaker"},
    adapter_kwargs={"speech_model": "melia-1"},
    warnings=("Early-access model: no confidence scores, custom dictionary or "
              "speaker ID yet — confidence values in the output are "
              "placeholders",
              "Wire language is always 'multi'; a requested language rides as "
              "a hint and language='auto' is rejected"),
    is_default=True,
    doc_url="https://docs.speechmatics.com/speech-to-text/models",
)

_speechmatics_standard = ModelConfig(
    model_config_id="speechmatics_batch_standard",
    display_name="Speechmatics Standard (single language pack)",
    provider_id="speechmatics",
    model_id="standard",
    default_language="en",
    provider_params={"type": "transcription", "model": "standard",
                     "diarization": "speaker"},
    adapter_kwargs={"speech_model": "standard"},
    warnings=("One language pack per run; multilingual audio needs the "
              "melia-1 config",
              "This is the API default when 'model' is omitted — pinned "
              "explicitly so a provider-side default change cannot move it"),
    doc_url="https://docs.speechmatics.com/speech-to-text/models",
)

_xai_stt = ModelConfig(
    model_config_id="xai_stt",
    display_name="xAI Grok STT (REST)",
    provider_id="xai-stt",
    model_id="stt-rest",   # the priced ENDPOINT variant — POST /v1/stt
    default_language="en",
    provider_params={"diarize": True, "filler_words": True, "format": True},
    # EMPTY BY CONTRACT, not by omission: POST /v1/stt takes no model
    # parameter, so there is nothing to pin.
    adapter_kwargs={},
    warnings=("Model is NOT pinnable: the endpoint has no model parameter and "
              "the response carries no model echo — attribution is "
              "endpoint-level only, and the served model can change without "
              "notice",
              "Response 'language' is documented as currently always empty "
              "(detection not enabled); word confidence is entropy-based and "
              "NOT comparable with other providers"),
    is_default=True,
    doc_url=("https://docs.x.ai/developers/model-capabilities/audio/"
             "speech-to-text"),
)

_soniox_async_v5 = ModelConfig(
    model_config_id="soniox_stt_async_v5",
    display_name="Soniox STT Async v5 (multilingual)",
    provider_id="soniox",
    model_id="stt-async-v5",
    default_language="en",
    provider_params={"model": "stt-async-v5",
                     "enable_speaker_diarization": True,
                     "enable_language_identification": True},
    # The wire pin lives in the create body, where model is a REQUIRED field
    # the adapter already sends — pinned here so the id cannot be moved by
    # the ``SONIOX_MODEL`` setting.
    adapter_kwargs={"speech_model": "stt-async-v5"},
    warnings=("Tokens are SUB-WORD and millisecond-timed — the mapper merges "
              "them into words",
              "Uploaded files are NOT auto-deleted by Soniox (fixed quotas: "
              "10 GB / 1,000 files); the adapter deletes the file always and "
              "the transcription after success"),
    is_default=True,
    doc_url="https://soniox.com/docs/stt/async/async-transcription",
)

BUILTIN_STT_MODEL_CONFIGS: dict[str, ModelConfig] = {
    c.model_config_id: c for c in (
        _elevenlabs_scribe_v2,
        _deepgram_nova3,
        _deepgram_nova3_multi,
        _assemblyai_universal2,
        _assemblyai_u35pro,
        _gladia_solaria1,
        _gladia_solaria3,
        _speechmatics_melia1,
        _speechmatics_standard,
        _xai_stt,
        _soniox_async_v5,
        _hybrid_of(_xai_stt, config_id="xai_stt__pyannote_diar",
                   display_name="xAI Grok STT + pyannote precision-2"),
        _hybrid_of(_speechmatics_melia1,
                   config_id="speechmatics_melia1__pyannote_diar",
                   display_name="Speechmatics Melia-1 + pyannote precision-2"),
        _hybrid_of(_elevenlabs_scribe_v2,
                   config_id="elevenlabs_scribe_v2__pyannote_diar",
                   display_name="ElevenLabs Scribe v2 + pyannote precision-2"),
    )
}


# ── Registry ──────────────────────────────────────────────────────────

# config id → ModelConfig | None (None masks a shipped config).
_runtime_stt_model_configs: dict[str, ModelConfig | None] = {}


def register_stt_model_config(config: ModelConfig) -> None:
    """Register *config* at runtime — highest precedence.

    For ``AppConfig.ready()``: a host that ships its own adapter registers the
    profiles it wants addressable. Re-registering an id overrides it.
    """
    if not isinstance(config, ModelConfig):
        raise TypeError(
            f"register_stt_model_config() expects a ModelConfig, "
            f"got {config!r}"
        )
    _runtime_stt_model_configs[config.model_config_id] = config


def registered_stt_model_configs() -> dict[str, ModelConfig]:
    """Effective ``id → ModelConfig`` mapping (built-ins ←
    ``STAPEL_AGENT["STT_MODEL_CONFIGS"]`` ← runtime; falsy entries dropped).

    Insertion order is preserved and it matters: ``resolve_config`` scans it.
    """
    from ..conf import agent_settings

    merged = {
        **BUILTIN_STT_MODEL_CONFIGS,
        **(agent_settings.STT_MODEL_CONFIGS or {}),
        **_runtime_stt_model_configs,
    }
    return {cid: c for cid, c in merged.items() if c}


def _reset_runtime_stt_model_configs() -> None:
    """Tests only."""
    _runtime_stt_model_configs.clear()


def list_configs(admin_visible_only: bool = True) -> list[ModelConfig]:
    """Every effective config, in declaration order (defaults first)."""
    return [c for c in registered_stt_model_configs().values()
            if c.admin_visible or not admin_visible_only]


def get_config(model_config_id: str) -> ModelConfig:
    """The config with this id. ``ValueError`` when there is none."""
    configs = registered_stt_model_configs()
    try:
        return configs[model_config_id]
    except KeyError:
        raise ValueError(
            f"unknown STT model config {model_config_id!r}; known: "
            f"{sorted(configs)}"
        ) from None


def get_default_config(provider_id: str) -> ModelConfig:
    """The provider's default config.

    ``provider_id`` is required: picking a best config across providers needs
    a measurement, and this library has no corpus to measure on.
    """
    for c in registered_stt_model_configs().values():
        if c.provider_id == provider_id and c.is_default:
            return c
    raise ValueError(
        f"no default STT model config for provider {provider_id!r}; known: "
        f"{sorted({c.provider_id for c in registered_stt_model_configs().values()})}"
    )


def resolve_config(provider_id: str, language: str) -> ModelConfig:
    """The config a (provider, language) run belongs to.

    Exact match on ``default_language`` first — that is how a Deepgram
    ``multi`` run is attributed to the multilingual profile it was actually
    billed at — else the provider's default. Used to attribute a run that
    named only a provider, which is what every pre-catalog caller does.
    """
    for c in registered_stt_model_configs().values():
        if c.provider_id == provider_id and c.default_language == language:
            return c
    return get_default_config(provider_id)


__all__ = [
    "BUILTIN_STT_MODEL_CONFIGS",
    "HOUR_MS",
    "ModelConfig",
    "estimate_cost",
    "get_config",
    "get_default_config",
    "hourly_rate",
    "list_configs",
    "register_stt_model_config",
    "registered_stt_model_configs",
    "resolve_config",
]
