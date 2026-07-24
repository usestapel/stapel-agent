"""Django system checks — catch provider misconfiguration at startup.

Registered from ``AgentConfig.ready()``. IDs:

- ``stapel_agent.E001`` — ``DEFAULT_PROVIDER`` names a provider that is
  not in the effective registry (built-ins ← settings merge ← runtime).
- ``stapel_agent.W001`` — a registry entry's dotted path fails to import
  (typo, or an optional dependency missing in this image).
- ``stapel_agent.W002`` — a registry entry resolves to something that is
  not an ``LlmProvider`` subclass.

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
    "check_diarization_providers",
    "check_embedding_providers",
    "check_image_providers",
    "check_providers",
    "check_stt_providers",
]
