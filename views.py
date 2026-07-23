"""DRF views for the agent service.

Both endpoints are service-to-service surfaces (``IsServiceRequest |
IsStaffUser``, exactly like stapel-billing's internal debit view). LLM
failures are HTTP 200 with ``status: "failure"``, never 5xx.
"""

import logging

from drf_spectacular.utils import extend_schema
from rest_framework.views import APIView
from stapel_core.django.api.errors import StapelResponse
from stapel_core.django.api.permissions import IsServiceRequest, IsStaffUser

from . import services
from .dto import SummarizeResponse, TranslateResponse
from .images.base import ImageRef
from .serializers import (
    CompleteRequestSerializer,
    GenerateImageRequestSerializer,
    SummarizeRequestSerializer,
    SummarizeResponseSerializer,
    TranscribeRequestSerializer,
    TranslateRequestSerializer,
    TranslateResponseSerializer,
)
from .stt.base import AudioRef

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
    # Deliberately no response serializer: `result` is arbitrary JSON
    # (object or array, whatever the prompt asked for) — a typed dataclass
    # serializer cannot express it. The plain contract dict is the seam
    # here; see MODULE.md.

    @extend_schema(request=CompleteRequestSerializer, responses={200: dict})
    def post(self, request):  # noqa: R007
        ser = self.get_request_serializer_class()(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        payload = services.complete_json(
            data.prompt,
            data.model,
            system_prompt=data.system_prompt,
            provider=data.provider,
            user_id=_request_user_id(request),
            # Entries are pre-validated (exactly one of url/data_b64,
            # base64 decodes) — from_payload cannot raise here.
            images=[ImageRef.from_payload(i) for i in data.images or []] or None,
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
    response_serializer_class = TranslateResponseSerializer

    @extend_schema(
        request=TranslateRequestSerializer,
        responses={200: TranslateResponseSerializer},
    )
    def post(self, request):  # noqa: R007
        ser = self.get_request_serializer_class()(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        payload = services.translate(
            data.from_lang,
            data.to,
            data.entries,
            user_id=_request_user_id(request),
        )
        response_cls = self.get_response_serializer_class()
        dto = TranslateResponse(
            status=payload["status"],
            result=payload.get("result"),
            reason=payload.get("reason"),
        )
        # Absent keys stay absent on the wire: drop nulls after serialization.
        body = {k: v for k, v in dict(response_cls(dto).data).items() if v is not None}
        return StapelResponse(body)


@extend_schema(tags=["LLM"])
class LlmTranscribeView(SerializerSeamMixin, APIView):
    """Speech-to-text — ``POST api/llm/transcribe``.

    Routes through the STT chain (explicit provider > language route >
    default + fallback). STT failures are HTTP 200 with
    ``status: "failure"`` — the house contract.
    """

    permission_classes = [IsServiceRequest | IsStaffUser]
    request_serializer_class = TranscribeRequestSerializer
    # Deliberately no response serializer: `transcript` embeds the raw
    # provider payload (arbitrary JSON) — same rationale as complete's
    # result; see MODULE.md.

    @extend_schema(request=TranscribeRequestSerializer, responses={200: dict})
    def post(self, request):  # noqa: R007
        ser = self.get_request_serializer_class()(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        payload = services.transcribe(
            AudioRef(url=data.audio_url),
            language=data.language,
            diarization=data.diarization,
            provider=data.provider,
            timeout_seconds=data.timeout_seconds,
            keyterms=data.keyterms,
            provider_options=data.provider_options,
            user_id=_request_user_id(request),
        )
        return StapelResponse(payload)


@extend_schema(tags=["LLM"])
class LlmSummarizeView(SerializerSeamMixin, APIView):
    """Summarization — ``POST api/llm/summarize``.

    Exactly one of ``text`` / ``transcript`` (a NormalizedTranscript
    dict). Single-shot when the input fits one chunk, map-reduce
    otherwise. LLM failures are HTTP 200 with ``status: "failure"``.
    """

    permission_classes = [IsServiceRequest | IsStaffUser]
    request_serializer_class = SummarizeRequestSerializer
    response_serializer_class = SummarizeResponseSerializer

    @extend_schema(
        request=SummarizeRequestSerializer,
        responses={200: SummarizeResponseSerializer},
    )
    def post(self, request):  # noqa: R007
        ser = self.get_request_serializer_class()(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        payload = services.summarize(
            data.text if data.text is not None else data.transcript,
            language=data.language,
            model_size=data.model,
            provider=data.provider,
            user_id=_request_user_id(request),
        )
        response_cls = self.get_response_serializer_class()
        dto = SummarizeResponse(
            status=payload["status"],
            summary=payload.get("summary"),
            usage=payload.get("usage"),
            reason=payload.get("reason"),
        )
        body = {k: v for k, v in dict(response_cls(dto).data).items() if v is not None}
        return StapelResponse(body)


@extend_schema(tags=["LLM"])
class LlmGenerateImageView(SerializerSeamMixin, APIView):
    """Image generation — ``POST api/llm/generate-image``.

    Returns the provider's raw results (``url`` and/or ``data_b64`` per
    image) — storage into CDN/asset libraries is the caller's job.
    Generation failures are HTTP 200 with ``status: "failure"``.
    """

    permission_classes = [IsServiceRequest | IsStaffUser]
    request_serializer_class = GenerateImageRequestSerializer
    # Deliberately no response serializer: whether an image arrives as a
    # URL or a base64 blob depends on the backend — same rationale as
    # complete's result; see MODULE.md.

    @extend_schema(request=GenerateImageRequestSerializer, responses={200: dict})
    def post(self, request):  # noqa: R007
        ser = self.get_request_serializer_class()(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        payload = services.generate_image(
            data.prompt,
            size=data.size,
            n=data.n,
            provider=data.provider,
            timeout_seconds=data.timeout_seconds,
            user_id=_request_user_id(request),
        )
        return StapelResponse(payload)
