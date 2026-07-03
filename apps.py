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

        # Django system checks (provider registry / DEFAULT_PROVIDER
        # misconfiguration) — registered on import.
        from . import checks  # noqa: F401
