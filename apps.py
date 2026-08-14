from django.apps import AppConfig


class AgentConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "stapel_agent"
    label = "agent"
    verbose_name = "Stapel Agent"

    def ready(self):
        # comm Function providers (in-process in a monolith, transport
        # chosen by STAPEL_COMM in microservices — same code).
        from . import functions  # noqa: F401

        # The prompt ledger holds prompts and full responses, so it owes
        # subject requests an answer like every other content store.
        from stapel_core.gdpr import gdpr_registry

        from .gdpr import AgentGDPRProvider

        if "agent" not in gdpr_registry.sections:
            gdpr_registry.register(AgentGDPRProvider())

        # Django system checks (provider registry / DEFAULT_PROVIDER
        # misconfiguration) — registered on import.
        from . import checks  # noqa: F401
