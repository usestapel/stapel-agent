"""Custom error keys for the agent service.

Only *request validation* problems are error-key responses (HTTP 400).
LLM/provider failures are NOT errors at the HTTP layer — the the legacy agent service
contract returns HTTP 200 with ``{"status": "failure", "reason": ...}``.
"""

from stapel_core.django.api.errors import ErrorKeysView, register_service_errors

ERR_400_INVALID_MODEL_SIZE = "error.400.invalid_model_size"

AGENT_ERRORS = {
    ERR_400_INVALID_MODEL_SIZE: "Model must be one of: small, medium, large",
}

register_service_errors(AGENT_ERRORS)


class AgentErrorKeysView(ErrorKeysView):
    def get_service_errors(self):
        return AGENT_ERRORS
