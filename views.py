"""DRF views for the agent service — path-compatible with the legacy agent service.

Both endpoints are service-to-service surfaces (``IsServiceRequest |
IsStaffUser``, exactly like stapel-billing's internal debit view) and
keep the iron contract: LLM failures are HTTP 200 with
``status: "failure"``, never 5xx.
"""

import logging

from drf_spectacular.utils import extend_schema
from rest_framework.views import APIView
from stapel_core.django.api.errors import StapelResponse
from stapel_core.django.api.permissions import IsServiceRequest, IsStaffUser

from . import services
from .serializers import CompleteRequestSerializer, TranslateRequestSerializer

logger = logging.getLogger(__name__)


class SerializerSeamMixin:
    """Overridable serializer seam (same pattern as stapel-billing)."""

    request_serializer_class = None
    response_serializer_class = None

    def get_request_serializer_class(self):
        return self.request_serializer_class

    def get_response_serializer_class(self):
        return self.response_serializer_class


def _request_user_id(request):
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        return str(user.pk)
    return None


@extend_schema(tags=["LLM"])
class LlmCompleteView(SerializerSeamMixin, APIView):
    """JSON LLM completion — ``POST api/llm/complete``.

    Prepends the JSON-API system prompt (unless the request brings its
    own), calls the configured provider and parses JSON out of the raw
    LLM text; prose around the JSON is returned as ``comment``.
    """

    permission_classes = [IsServiceRequest | IsStaffUser]
    request_serializer_class = CompleteRequestSerializer

    @extend_schema(request=CompleteRequestSerializer, responses={200: dict})
    def post(self, request):
        ser = self.get_request_serializer_class()(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        payload = services.complete_json(
            data.prompt,
            data.model,
            system_prompt=data.system_prompt,
            provider=data.provider,
            user_id=_request_user_id(request),
        )
        return StapelResponse(payload)


@extend_schema(tags=["LLM"])
class LlmTranslateView(SerializerSeamMixin, APIView):
    """Key-value translation — ``POST api/llm/translate``.

    Wire request: ``{"from": str ("auto" allowed), "to": str,
    "entries": {key: text}}``. Empty entries short-circuit to
    ``{"status": "ok", "result": {}}`` without calling the provider.
    """

    permission_classes = [IsServiceRequest | IsStaffUser]
    request_serializer_class = TranslateRequestSerializer

    @extend_schema(request=TranslateRequestSerializer, responses={200: dict})
    def post(self, request):
        ser = self.get_request_serializer_class()(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        payload = services.translate(
            data.from_lang,
            data.to,
            data.entries,
            user_id=_request_user_id(request),
        )
        return StapelResponse(payload)
