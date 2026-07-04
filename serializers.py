"""Serializers for the agent API."""

from stapel_core.django.api.errors import StapelValidationError
from stapel_core.django.api.serializers import StapelDataclassSerializer

from .dto import (
    CompleteRequest,
    SummarizeRequest,
    SummarizeResponse,
    TranscribeRequest,
    TranslateRequest,
    TranslateResponse,
)
from .errors import ERR_400_INVALID_MODEL_SIZE, ERR_400_SUMMARIZE_INPUT
from .services import MODEL_SIZES


class CompleteRequestSerializer(StapelDataclassSerializer):
    class Meta:
        dataclass = CompleteRequest

    def validate_model(self, value):
        if value not in MODEL_SIZES:
            raise StapelValidationError(ERR_400_INVALID_MODEL_SIZE)
        return value


class TranslateRequestSerializer(StapelDataclassSerializer):
    """Accepts the the legacy agent service wire format where the source language key is
    ``from`` — a Python keyword, mapped explicitly onto ``from_lang``."""

    class Meta:
        dataclass = TranslateRequest

    def to_internal_value(self, data):
        if isinstance(data, dict) and "from" in data and "from_lang" not in data:
            data = dict(data)
            data["from_lang"] = data.pop("from")
        return super().to_internal_value(data)


class TranslateResponseSerializer(StapelDataclassSerializer):
    class Meta:
        dataclass = TranslateResponse


class TranscribeRequestSerializer(StapelDataclassSerializer):
    class Meta:
        dataclass = TranscribeRequest


class SummarizeRequestSerializer(StapelDataclassSerializer):
    class Meta:
        dataclass = SummarizeRequest

    def validate_model(self, value):
        if value not in MODEL_SIZES:
            raise StapelValidationError(ERR_400_INVALID_MODEL_SIZE)
        return value

    def validate(self, data):
        text = getattr(data, "text", None)
        transcript = getattr(data, "transcript", None)
        if (text is None) == (transcript is None):
            raise StapelValidationError(ERR_400_SUMMARIZE_INPUT)
        return data


class SummarizeResponseSerializer(StapelDataclassSerializer):
    class Meta:
        dataclass = SummarizeResponse
