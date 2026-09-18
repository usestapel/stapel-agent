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

        # Erasure over comm (gdpr.erasure.requested / gdpr.owner.probe /
        # the deprecated user.deleted), implemented once in stapel-core:
        # the deterministic receipt id, the receipt inside the erase's
        # transaction, and the probe answered from the same module — which
        # is what makes "alive" evidence. The in-process provider above is
        # only reachable in a monolith; a service that consumes actions
        # participates through this registration. What stays ours is
        # erase_subject (gdpr.py).
        #
        # Registering by name is also what stands core's provider bridge
        # down for this section exactly: until 0.27.0 this module carried
        # its own copy of the protocol, and the bridge could only tell they
        # were the same APP, not the same section (gdpr.W012).
        from stapel_core.gdpr import register_gdpr_owner

        from .gdpr import OWNER, SUBJECT_TYPES, erase_subject

        register_gdpr_owner(OWNER, SUBJECT_TYPES, erase_subject)

        # The rest of the account life cycle (user.merged).
        from . import actions  # noqa: F401

        # Django system checks (provider registry / DEFAULT_PROVIDER
        # misconfiguration) — registered on import.
        from . import checks  # noqa: F401
