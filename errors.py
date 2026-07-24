"""Custom error keys for the agent service.

Only *request validation* problems are error-key responses (HTTP 400).
LLM/provider failures are NOT errors at the HTTP layer — they return HTTP 200
with ``{"status": "failure", "reason": ...}``.
"""

from stapel_core.django.api.errors import ErrorKeysView, register_service_errors

ERR_400_INVALID_MODEL_SIZE = "error.400.invalid_model_size"
ERR_400_SUMMARIZE_INPUT = "error.400.summarize_input"
ERR_400_INVALID_IMAGE = "error.400.invalid_image"
ERR_400_INVALID_IMAGE_COUNT = "error.400.invalid_image_count"
ERR_400_INVALID_TIMEOUT = "error.400.invalid_timeout"
ERR_400_INVALID_NUM_SPEAKERS = "error.400.invalid_num_speakers"
ERR_400_EMPTY_TEXTS = "error.400.empty_texts"
ERR_400_EMPTY_QUERY = "error.400.empty_query"
ERR_400_EMPTY_DOCUMENTS = "error.400.empty_documents"
ERR_400_INVALID_TOP_N = "error.400.invalid_top_n"

AGENT_ERRORS = {
    ERR_400_INVALID_MODEL_SIZE: "Model must be one of: small, medium, large",
    ERR_400_SUMMARIZE_INPUT: "Provide exactly one of: text, transcript",
    ERR_400_INVALID_IMAGE: (
        "Each image needs exactly one of: url, data_b64 (valid base64)"
    ),
    ERR_400_INVALID_IMAGE_COUNT: "n must be between 1 and 10",
    ERR_400_INVALID_TIMEOUT: "timeout_seconds must be a positive integer (>= 1)",
    ERR_400_INVALID_NUM_SPEAKERS: "num_speakers must be a positive integer (>= 1)",
    ERR_400_EMPTY_TEXTS: "texts must be a non-empty list of non-empty strings",
    ERR_400_EMPTY_QUERY: "query must be a non-empty string",
    ERR_400_EMPTY_DOCUMENTS: (
        "documents must be a non-empty list of non-empty strings"
    ),
    ERR_400_INVALID_TOP_N: "top_n must be a positive integer (>= 1)",
}

register_service_errors(AGENT_ERRORS)


class AgentErrorKeysView(ErrorKeysView):
    def get_service_errors(self):
        return AGENT_ERRORS
