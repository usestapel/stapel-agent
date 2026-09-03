"""Django system checks — catch provider misconfiguration at startup.

Registered from ``AgentConfig.ready()``. IDs:

- ``stapel_agent.E001`` — ``DEFAULT_PROVIDER`` names a provider that is
  not in the effective registry (built-ins ← settings merge ← runtime).
- ``stapel_agent.W001`` — a registry entry's dotted path fails to import
  (typo, or an optional dependency missing in this image).
- ``stapel_agent.W002`` — a registry entry resolves to something that is
  not an ``LlmProvider`` subclass.
- ``stapel_agent.W016`` — the default LLM provider is registered but not
  usable (missing credential/binary/package).
- ``stapel_agent.W014`` — ``PROMPT_LOG_RETENTION_DAYS`` is configured and
  *no scheduler at all* is known to this process.
- ``stapel_agent.W017`` — this process HAS a beat schedule and this
  package's retention task is not in it. The specific half of W014's
  question, with the specific answer (``get_agent_beat_schedule()``);
  the two never fire together.
- ``stapel_agent.W015`` — the STT audio-download allowlist is empty and
  no wildcard is declared, so every URL AudioRef is refused.
- ``stapel_agent.W018`` — a model THIS deployment is configured to call is
  not in ``pricing.PRICES_USD_PER_MTOK``, so every call it makes stores
  ``cost_basis=unpriced`` and metering cannot cost the feature.

Import/subclass problems are warnings, not errors, on purpose: providers
resolve lazily per request and degrade to ``status: "failure"`` — a
broken *unused* entry must not block deploys, but it should be visible.
"""
from __future__ import annotations

import inspect

from django.core import checks
from django.utils.module_loading import import_string


@checks.register("stapel_agent")
def check_providers(app_configs, **kwargs):
    from .conf import agent_settings
    from .providers import registered_providers
    from .providers.base import LlmProvider

    issues = []
    effective = registered_providers()

    default = agent_settings.DEFAULT_PROVIDER
    if default not in effective:
        issues.append(
            checks.Error(
                f"STAPEL_AGENT['DEFAULT_PROVIDER'] is {default!r}, which is "
                "not in the effective provider registry "
                f"({sorted(effective) or 'empty'}).",
                hint=(
                    "Add it via STAPEL_AGENT['PROVIDERS'] or "
                    "stapel_agent.providers.register_provider(), or point "
                    "DEFAULT_PROVIDER at an existing name."
                ),
                id="stapel_agent.E001",
            )
        )

    # Registered is not usable. The check above proves DEFAULT_PROVIDER
    # RESOLVES; it never asked whether it can actually serve a call. The
    # ironmemo stand defaulted to 'anthropic' with an empty key: green
    # checks, and every llm.summarize call failing — invisibly, because
    # the caller treats summarization as best-effort and completed each
    # recording with an empty summary (2026-07-26).
    #
    # Warning, not Error, deliberately: a deployment may install this app
    # for STT/embeddings alone and never make a text call. Blocking those
    # would be the same false positive as warning about a peer nobody
    # talks to. The message says plainly what breaks, so it cannot be
    # read as noise.
    #
    # id was W009 through 0.13.0 — a collision with check_embedding_providers'
    # entry-check id (also W009, introduced two days earlier: 0.4.0 vs 0.6.2).
    # SILENCED_SYSTEM_CHECKS on either check silenced both. Renumbered to
    # W016 (the next free id) in 0.13.1; embeddings kept W009 as the older,
    # already-referenced id (a client fleet deploy, 2026-08-22).
    default_target = effective.get(default)
    if default_target is not None:
        try:
            resolved = (
                import_string(default_target)
                if isinstance(default_target, str)
                else default_target
            )
            reason = resolved.configuration_error()
        except Exception:  # import/interface problems are W001/W002's job
            reason = None
        if reason:
            issues.append(
                checks.Warning(
                    f"Default LLM provider {default!r} is registered but not "
                    f"usable: {reason}. Every llm.complete / llm.summarize "
                    "call will fail with a ProviderError.",
                    hint=(
                        "Configure it, or point "
                        "STAPEL_AGENT['DEFAULT_PROVIDER'] at a backend that "
                        "is. Note a best-effort caller (stapel-recordings' "
                        "summarize step) SWALLOWS this failure — recordings "
                        "complete with empty summaries and no error surfaces "
                        "anywhere."
                    ),
                    id="stapel_agent.W016",
                )
            )

    for name, target in effective.items():
        if isinstance(target, str):
            try:
                target = import_string(target)
            except ImportError as exc:
                issues.append(
                    checks.Warning(
                        f"LLM provider {name!r} cannot be imported: {exc}",
                        hint=(
                            "Fix the dotted path, install the missing "
                            "dependency, or remove the entry (set it to None)."
                        ),
                        id="stapel_agent.W001",
                    )
                )
                continue
        if not (inspect.isclass(target) and issubclass(target, LlmProvider)):
            issues.append(
                checks.Warning(
                    f"LLM provider {name!r} resolves to {target!r}, which is "
                    "not a stapel_agent.LlmProvider subclass.",
                    hint="Implement the LlmProvider ABC (see MODULE.md).",
                    id="stapel_agent.W002",
                )
            )
    return issues


@checks.register("stapel_agent")
def check_stt_providers(app_configs, **kwargs):
    """STT registry checks — all W-level: STT is an optional surface and
    a broken entry degrades to ``status: "failure"`` per request.

    - ``stapel_agent.W003`` — an ``STT_PROVIDERS`` entry cannot be
      imported or is not an ``SttProvider`` subclass;
    - ``stapel_agent.W004`` — ``DEFAULT_STT_PROVIDER`` /
      ``STT_FALLBACK_CHAIN`` / ``STT_LANGUAGE_ROUTES`` reference a name
      missing from the effective registry.
    """
    from .conf import agent_settings
    from .stt import registered_stt_providers
    from .stt.base import SttProvider

    issues = []
    effective = registered_stt_providers()

    for name, target in effective.items():
        if isinstance(target, str):
            try:
                target = import_string(target)
            except ImportError as exc:
                issues.append(
                    checks.Warning(
                        f"STT provider {name!r} cannot be imported: {exc}",
                        hint=(
                            "Fix the dotted path, install the missing "
                            "dependency, or remove the entry (set it to None)."
                        ),
                        id="stapel_agent.W003",
                    )
                )
                continue
        if not (inspect.isclass(target) and issubclass(target, SttProvider)):
            issues.append(
                checks.Warning(
                    f"STT provider {name!r} resolves to {target!r}, which is "
                    "not a stapel_agent.stt.base.SttProvider subclass.",
                    hint="Implement the SttProvider ABC (see MODULE.md).",
                    id="stapel_agent.W003",
                )
            )

    def _unknown(where: str, names) -> None:
        for ref in names:
            if ref and ref not in effective:
                issues.append(
                    checks.Warning(
                        f"{where} references unknown STT provider {ref!r} "
                        f"(effective registry: {sorted(effective)}).",
                        hint=(
                            "Register it via STAPEL_AGENT['STT_PROVIDERS'] / "
                            "register_stt_provider(), or fix the name."
                        ),
                        id="stapel_agent.W004",
                    )
                )

    _unknown("STAPEL_AGENT['DEFAULT_STT_PROVIDER']", [agent_settings.DEFAULT_STT_PROVIDER])
    _unknown("STAPEL_AGENT['STT_FALLBACK_CHAIN']", agent_settings.STT_FALLBACK_CHAIN or [])
    for lang, route in (agent_settings.STT_LANGUAGE_ROUTES or {}).items():
        _unknown(f"STAPEL_AGENT['STT_LANGUAGE_ROUTES'][{lang!r}]", route or [])
    return issues


@checks.register("stapel_agent")
def check_image_providers(app_configs, **kwargs):
    """Image-generation registry checks — W-level like STT's: the image
    surface is optional and a broken entry degrades to
    ``status: "failure"`` per request.

    - ``stapel_agent.W005`` — an ``IMAGE_PROVIDERS`` entry cannot be
      imported or is not an ``ImageGenProvider`` subclass;
    - ``stapel_agent.W006`` — ``DEFAULT_IMAGE_PROVIDER`` references a
      name missing from the effective registry.
    """
    from .conf import agent_settings
    from .images import registered_image_providers
    from .images.base import ImageGenProvider

    issues = []
    effective = registered_image_providers()

    for name, target in effective.items():
        if isinstance(target, str):
            try:
                target = import_string(target)
            except ImportError as exc:
                issues.append(
                    checks.Warning(
                        f"Image provider {name!r} cannot be imported: {exc}",
                        hint=(
                            "Fix the dotted path, install the missing "
                            "dependency, or remove the entry (set it to None)."
                        ),
                        id="stapel_agent.W005",
                    )
                )
                continue
        if not (inspect.isclass(target) and issubclass(target, ImageGenProvider)):
            issues.append(
                checks.Warning(
                    f"Image provider {name!r} resolves to {target!r}, which is "
                    "not a stapel_agent.images.base.ImageGenProvider subclass.",
                    hint="Implement the ImageGenProvider ABC (see MODULE.md).",
                    id="stapel_agent.W005",
                )
            )

    default = agent_settings.DEFAULT_IMAGE_PROVIDER
    if default and default not in effective:
        issues.append(
            checks.Warning(
                f"STAPEL_AGENT['DEFAULT_IMAGE_PROVIDER'] is {default!r}, which "
                "is not in the effective image-provider registry "
                f"({sorted(effective) or 'empty'}).",
                hint=(
                    "Register it via STAPEL_AGENT['IMAGE_PROVIDERS'] / "
                    "register_image_provider(), or fix the name."
                ),
                id="stapel_agent.W006",
            )
        )
    return issues


@checks.register("stapel_agent")
def check_diarization_providers(app_configs, **kwargs):
    """Diarization registry checks — W-level like STT's: the surface is
    optional and a broken entry degrades to ``status: "failure"`` per
    request.

    - ``stapel_agent.W007`` — a ``DIARIZATION_PROVIDERS`` entry cannot
      be imported or is not a ``DiarizationProvider`` subclass;
    - ``stapel_agent.W008`` — ``DEFAULT_DIARIZATION_PROVIDER`` references
      a name missing from the effective registry.
    """
    from .conf import agent_settings
    from .diarization import registered_diarization_providers
    from .diarization.base import DiarizationProvider

    return _registry_issues(
        kind="Diarization",
        effective=registered_diarization_providers(),
        base_cls=DiarizationProvider,
        entry_check_id="stapel_agent.W007",
        default_check_id="stapel_agent.W008",
        default_name=agent_settings.DEFAULT_DIARIZATION_PROVIDER,
        default_setting="DEFAULT_DIARIZATION_PROVIDER",
        register_hint=(
            "STAPEL_AGENT['DIARIZATION_PROVIDERS'] / "
            "register_diarization_provider()"
        ),
    )


@checks.register("stapel_agent")
def check_embedding_providers(app_configs, **kwargs):
    """Embedding registry checks — W-level like STT's: the surface is
    optional and a broken entry degrades to ``status: "failure"`` per
    request.

    - ``stapel_agent.W009`` — an ``EMBEDDING_PROVIDERS`` entry cannot be
      imported or is not an ``EmbeddingProvider`` subclass;
    - ``stapel_agent.W010`` — ``DEFAULT_EMBEDDING_PROVIDER`` references
      a name missing from the effective registry.
    """
    from .conf import agent_settings
    from .embeddings import registered_embedding_providers
    from .embeddings.base import EmbeddingProvider

    return _registry_issues(
        kind="Embedding",
        effective=registered_embedding_providers(),
        base_cls=EmbeddingProvider,
        entry_check_id="stapel_agent.W009",
        default_check_id="stapel_agent.W010",
        default_name=agent_settings.DEFAULT_EMBEDDING_PROVIDER,
        default_setting="DEFAULT_EMBEDDING_PROVIDER",
        register_hint=(
            "STAPEL_AGENT['EMBEDDING_PROVIDERS'] / "
            "register_embedding_provider()"
        ),
    )


@checks.register("stapel_agent")
def check_rerank_providers(app_configs, **kwargs):
    """Rerank registry checks — W-level like STT's: the surface is
    optional and a broken entry degrades to ``status: "failure"`` per
    request.

    - ``stapel_agent.W011`` — a ``RERANK_PROVIDERS`` entry cannot be
      imported or is not a ``RerankProvider`` subclass;
    - ``stapel_agent.W012`` — ``DEFAULT_RERANK_PROVIDER`` references a
      name missing from the effective registry;
    - ``stapel_agent.W013`` — the default is the built-in ``rerank-http``
      but ``RERANK_HTTP_BASE_URL`` is empty (every request would fail).
    """
    from .conf import agent_settings
    from .rerank import registered_rerank_providers
    from .rerank.base import RerankProvider

    effective = registered_rerank_providers()
    issues = _registry_issues(
        kind="Rerank",
        effective=effective,
        base_cls=RerankProvider,
        entry_check_id="stapel_agent.W011",
        default_check_id="stapel_agent.W012",
        default_name=agent_settings.DEFAULT_RERANK_PROVIDER,
        default_setting="DEFAULT_RERANK_PROVIDER",
        register_hint=(
            "STAPEL_AGENT['RERANK_PROVIDERS'] / "
            "register_rerank_provider()"
        ),
    )

    # The self-host adapter has no usable zero-config default (unlike
    # deepinfra-rerank, whose base URL ships filled in) — a resolvable
    # default that cannot serve a single request should be visible.
    from .rerank import BUILTIN_RERANK_PROVIDERS

    default = agent_settings.DEFAULT_RERANK_PROVIDER
    if (
        default == "rerank-http"
        and effective.get(default) == BUILTIN_RERANK_PROVIDERS["rerank-http"]
        and not agent_settings.RERANK_HTTP_BASE_URL
    ):
        issues.append(
            checks.Warning(
                "STAPEL_AGENT['DEFAULT_RERANK_PROVIDER'] is 'rerank-http' "
                "but STAPEL_AGENT['RERANK_HTTP_BASE_URL'] is empty — every "
                "rerank request will fail.",
                hint="Set RERANK_HTTP_BASE_URL to the self-hosted /rerank server.",
                id="stapel_agent.W013",
            )
        )
    return issues


#: Substring that identifies the retention job in a beat schedule — the
#: management command's name, which is also the tail of the shipped task
#: name (``stapel_agent.tasks.purge_prompt_logs``), so both spellings of
#: "this entry runs the purge" are recognised by one test.
PURGE_JOB_NAME = "purge_prompt_logs"


def _beat_schedule():
    """The host's ``CELERY_BEAT_SCHEDULE``, or ``{}`` when it has none."""
    from django.conf import settings

    return getattr(settings, "CELERY_BEAT_SCHEDULE", None) or {}


def _beat_runs_the_purge(schedule) -> bool:
    for entry in schedule.values():
        if PURGE_JOB_NAME in str((entry or {}).get("task", "")):
            return True
        if PURGE_JOB_NAME in str((entry or {}).get("args", "")):
            return True
    return False


@checks.register("stapel_agent")
def check_prompt_log_retention_is_scheduled(app_configs, **kwargs):
    """W014: the retention window is set and nothing is known to enforce it.

    ``PROMPT_LOG_RETENTION_DAYS`` has a default (30 since 0.14.0), but the
    executors this package ships — the ``purge_prompt_logs`` management
    command and, since 0.14.0, ``stapel_agent.tasks.purge_prompt_logs`` —
    are not scheduled by ``AgentConfig.ready()``. Unless the host wired
    one of them the prompts, system prompts and full responses in
    ``PromptLog`` are kept forever while the configuration states a window
    — a retention policy that exists only as a number (audit AGENT-02).

    A process cannot see the host's crontab, so the operator declares it:
    ``PROMPT_LOG_RETENTION_SCHEDULED = True``. A beat schedule that runs
    the job is detected and needs no declaration. Warning rather than
    Error because the gap is a compliance problem, not a broken deploy —
    but silence was the wrong default, since an unenforced retention
    policy looks exactly like an enforced one from the settings file.

    When the process DOES run beat and simply left this package out, the
    finding is W017's — same gap, but a specific hint instead of "wire
    something somewhere", and one warning rather than two.

    ``PROMPT_LOG_RETENTION_DAYS = None`` is not reported here: keeping the
    text forever is then a stated decision, not an accident.
    """
    from .conf import agent_settings, prompt_log_retention_scheduled

    if agent_settings.PROMPT_LOG_RETENTION_DAYS is None:
        return []
    if prompt_log_retention_scheduled():
        return []
    if _beat_schedule():
        # This process runs beat: whether the entry is there or not, the
        # finding belongs to W017, which can name the fix.
        return []

    return [
        checks.Warning(
            "STAPEL_AGENT['PROMPT_LOG_RETENTION_DAYS'] is "
            f"{agent_settings.PROMPT_LOG_RETENTION_DAYS!r}, but nothing in "
            "this process runs the purge: PromptLog keeps prompts, system "
            "prompts and full responses in plaintext for as long as the row "
            "exists.",
            hint=(
                f"Schedule `manage.py {PURGE_JOB_NAME}` (cron, systemd timer, "
                "CronJob) and set "
                "STAPEL_AGENT['PROMPT_LOG_RETENTION_SCHEDULED'] = True to "
                "declare it, or set PROMPT_LOG_RETENTION_DAYS = None to state "
                "that this deployment keeps the text indefinitely."
            ),
            id="stapel_agent.W014",
        )
    ]


@checks.register("stapel_agent")
def check_agent_beat_schedule_is_registered(app_configs, **kwargs):
    """W017: this process runs beat and this package is not in the schedule.

    The ironmemo finding was not a wrong cadence, it was **no entry at
    all**: a service with a working ``CELERY_BEAT_SCHEDULE`` for the other
    libraries, and nothing running the agent retention purge — so the
    prompts and full model responses accumulated behind a setting that
    said otherwise. Nothing could see it, because a beat schedule that
    runs *something* looks exactly like a beat schedule that runs *this*.

    Fires only when a beat schedule exists (a process with none has no
    entry to be missing, and W014 covers "no scheduler is known at all")
    and only when a retention window is configured — the same two escapes
    W014 honours: ``PROMPT_LOG_RETENTION_SCHEDULED = True`` for a host
    that purges from cron, ``PROMPT_LOG_RETENTION_DAYS = None`` for a host
    that states it keeps the text.

    Since 0.14.0 the same beat entry is also the erasure module's only
    scheduled work, so its absence is a retention *and* a hygiene gap.
    """
    from .conf import agent_settings, prompt_log_retention_scheduled
    from .tasks import PURGE_TASK_NAME

    if agent_settings.PROMPT_LOG_RETENTION_DAYS is None:
        return []
    if prompt_log_retention_scheduled():
        return []
    schedule = _beat_schedule()
    if not schedule:
        return []
    if _beat_runs_the_purge(schedule):
        return []

    return [
        checks.Warning(
            "CELERY_BEAT_SCHEDULE has no entry for "
            f"{PURGE_TASK_NAME}: this process runs beat for other work, "
            "but nothing scrubs PromptLog, so prompts, system prompts and "
            "full responses are kept for as long as the row exists "
            "regardless of STAPEL_AGENT['PROMPT_LOG_RETENTION_DAYS'] = "
            f"{agent_settings.PROMPT_LOG_RETENTION_DAYS!r}.",
            hint=(
                "CELERY_BEAT_SCHEDULE = {**get_agent_beat_schedule(), ...} "
                "(stapel_agent.tasks) — or run `manage.py "
                f"{PURGE_JOB_NAME}` from cron and set "
                "STAPEL_AGENT['PROMPT_LOG_RETENTION_SCHEDULED'] = True to "
                "declare it."
            ),
            id="stapel_agent.W017",
        )
    ]


@checks.register("stapel_agent")
def check_stt_download_allowlist(app_configs, **kwargs):
    """W015: the audio-download allowlist is empty and nothing declares a
    wildcard, so every URL-shaped ``AudioRef`` is refused at call time.

    ``STT_DOWNLOAD_ALLOWED_HOSTS`` is an SSRF ceiling: it is in
    ``conf.NO_ENV``, so it resolves from ``settings.STAPEL_AGENT`` only —
    an environment variable cannot fill it, and the shipped default is
    ``[]``. That default fails closed on purpose (``stt.base._download``
    raises ``no_allowed_hosts``), but the refusal happens per request,
    inside a worker, on a path most callers treat as best-effort. The
    iron-agent dev stand ran that way for its whole life: green checks,
    green deploy, and every transcription refused before the first DNS
    lookup (2026-08-21).

    Warning, not Error: a deployment may install this app for text
    completion alone and never transcribe anything, and there is no
    reliable way to ask "is STT in play" — the registry always carries
    the built-in adapters, and their credentials have no common seam. So
    the operator declares the answer, as with W014: list the object-store
    host(s), or set ``STT_DOWNLOAD_ALLOW_ANY_HOST = True``. Either way
    the intent is written down where a reviewer sees it.

    A deployment that masked every STT adapter (each name set to ``None``
    in ``STT_PROVIDERS``) has removed the surface and is not warned.
    """
    from .conf import agent_settings, stt_download_allow_any_host
    from .stt import registered_stt_providers

    if list(agent_settings.STT_DOWNLOAD_ALLOWED_HOSTS or []):
        return []
    if stt_download_allow_any_host():
        return []
    if not registered_stt_providers():
        return []

    return [
        checks.Warning(
            "STAPEL_AGENT['STT_DOWNLOAD_ALLOWED_HOSTS'] is empty and "
            "STT_DOWNLOAD_ALLOW_ANY_HOST is off: every transcription of a "
            "URL AudioRef will fail with "
            "\"audio URL refused (no_allowed_hosts)\" — at call time, in a "
            "worker, not here.",
            hint=(
                "Set STAPEL_AGENT['STT_DOWNLOAD_ALLOWED_HOSTS'] = "
                "['<audio-host>'] in settings.py to the exact host(s) your "
                "presigned audio URLs point at (derive it from the object "
                "store's public URL rather than hardcoding a stand domain), "
                "or set STAPEL_AGENT['STT_DOWNLOAD_ALLOW_ANY_HOST'] = True "
                "to accept any public host. This key is an SSRF ceiling: it "
                "is in stapel_agent.conf.NO_ENV, so an environment variable "
                "is IGNORED for it — it has to be stated in settings.py."
            ),
            id="stapel_agent.W015",
        )
    ]


@checks.register("stapel_agent")
def check_configured_models_are_priced(app_configs, **kwargs):
    """W018: this deployment calls a model the rate card has never heard of.

    ``pricing.cost_fields()`` already records the gap honestly — the row
    lands with ``cost_basis="unpriced"`` rather than pretending the call was
    free — and ``services.complete()`` already logs a warning naming the
    model. Both are per call, inside a worker, on the same line every time.
    A live client fleet ran that way for a fortnight: 352 composer calls in
    one day, each one warning, each warning read by nobody, and metering
    unable to cost the feature at all.

    A test guarded the SHIPPED ladder (``conf.defaults["MODELS"]``) and was
    green the whole time, because the ladder was not what the deployment
    called: every rung was overridden to ``gpt-5.2`` through
    ``OPENAI_COMPAT_MODELS``. So the question this check asks is not "is the
    table complete" but "is the table complete FOR THIS SETTINGS FILE", and
    it asks it once, at ``manage.py check`` time, before the first call.

    Models are resolved through ``backend.resolve_model()`` — the same seam
    ``complete()`` uses at services.py — rather than by re-deriving the
    openai-compat overlay here. A provider that overrides model resolution
    gets checked correctly for free; a copy of the rule would drift.

    Warning, not Error: a deployment may not care what its calls cost, and a
    provider that reports its own charge (``cost_basis="provider_ticks"``)
    never consults the table at all. An unknown ``DEFAULT_PROVIDER`` is left
    to E001, which already names it — two findings for one typo is noise.
    """
    from .conf import agent_settings
    from .pricing import is_priced
    from .services import get_provider

    models = agent_settings.MODELS or {}
    if not models:
        return []

    try:
        backend = get_provider(agent_settings.DEFAULT_PROVIDER)
    except Exception:  # noqa: BLE001
        # E001/W001/W016 own this finding, and without a provider there is no
        # resolve_model to ask. Deliberately every exception, not just
        # ProviderError/ImportError: PROVIDERS is an open extension point, a
        # host-registered class may raise anything at all from its
        # constructor, and a system check that takes `manage.py check` down
        # with it blocks the deploy it was added to inform.
        return []

    unpriced: dict[str, list[str]] = {}
    for size, default in sorted(models.items()):
        try:
            model = backend.resolve_model(size, default)
        except Exception:  # noqa: BLE001 — a check must not break `manage.py`
            continue
        if model and not is_priced(model):
            unpriced.setdefault(model, []).append(size)

    if not unpriced:
        return []

    named = ", ".join(
        f"{model!r} (rung{'s' if len(sizes) > 1 else ''} {', '.join(sizes)})"
        for model, sizes in sorted(unpriced.items())
    )
    return [
        checks.Warning(
            f"This deployment is configured to call {named}, which "
            f"{'are' if len(unpriced) > 1 else 'is'} not in "
            "stapel_agent.pricing.PRICES_USD_PER_MTOK. Every completion from "
            f"{'those models' if len(unpriced) > 1 else 'that model'} will "
            "be stored with cost_basis=unpriced and cost_usd=0.0, so metering "
            "cannot cost the feature — it will read as free rather than as "
            "unknown.",
            hint=(
                "Add the model to stapel_agent.pricing.PRICES_USD_PER_MTOK "
                "with the provider's PUBLISHED price, and the source URL and "
                "fetch date beside it — a price without a provenance line is "
                "a number someone remembered. If the price cannot be "
                "verified, leave it out: cost_basis=unpriced is a true "
                "answer and a guessed rate card is not. If this provider "
                "reports its own charge, no entry is needed — those rows "
                "land as cost_basis=provider_ticks."
            ),
            id="stapel_agent.W018",
        )
    ]


def _registry_issues(
    *,
    kind: str,
    effective: dict,
    base_cls,
    entry_check_id: str,
    default_check_id: str,
    default_name: str,
    default_setting: str,
    register_hint: str,
):
    """The shared entries-importable + default-registered walk the image /
    diarization / embedding checks all perform (STT keeps its own — it
    also validates routes)."""
    issues = []
    for name, target in effective.items():
        if isinstance(target, str):
            try:
                target = import_string(target)
            except ImportError as exc:
                issues.append(
                    checks.Warning(
                        f"{kind} provider {name!r} cannot be imported: {exc}",
                        hint=(
                            "Fix the dotted path, install the missing "
                            "dependency, or remove the entry (set it to None)."
                        ),
                        id=entry_check_id,
                    )
                )
                continue
        if not (inspect.isclass(target) and issubclass(target, base_cls)):
            issues.append(
                checks.Warning(
                    f"{kind} provider {name!r} resolves to {target!r}, which "
                    f"is not a {base_cls.__module__}.{base_cls.__name__} "
                    "subclass.",
                    hint=f"Implement the {base_cls.__name__} ABC (see MODULE.md).",
                    id=entry_check_id,
                )
            )

    if default_name and default_name not in effective:
        issues.append(
            checks.Warning(
                f"STAPEL_AGENT['{default_setting}'] is {default_name!r}, "
                f"which is not in the effective {kind.lower()}-provider "
                f"registry ({sorted(effective) or 'empty'}).",
                hint=f"Register it via {register_hint}, or fix the name.",
                id=default_check_id,
            )
        )
    return issues


__all__ = [
    "PURGE_JOB_NAME",
    "check_agent_beat_schedule_is_registered",
    "check_configured_models_are_priced",
    "check_diarization_providers",
    "check_embedding_providers",
    "check_image_providers",
    "check_prompt_log_retention_is_scheduled",
    "check_providers",
    "check_rerank_providers",
    "check_stt_download_allowlist",
    "check_stt_providers",
]
