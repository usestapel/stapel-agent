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


__all__ = ["check_providers", "check_stt_providers"]
