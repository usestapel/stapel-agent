"""Serializers for the agent API."""

import base64

from stapel_core.django.api.errors import StapelValidationError
from stapel_core.django.api.serializers import StapelDataclassSerializer

from .dto import (
    CompleteRequest,
    GenerateImageRequest,
    SummarizeRequest,
    SummarizeResponse,
    TranscribeRequest,
    TranslateRequest,
    TranslateResponse,
)
from .errors import (
    ERR_400_INVALID_IMAGE,
    ERR_400_INVALID_IMAGE_COUNT,
    ERR_400_INVALID_MODEL_SIZE,
    ERR_400_INVALID_TIMEOUT,
    ERR_400_SUMMARIZE_INPUT,
)
from .services import MODEL_SIZES


def _validate_timeout_seconds(value):
    # `requests` rejects a timeout of 0, and a negative timeout raises deep
    # inside urllib3 (uncaught → HTTP 500) — reject non-positive at the
    # boundary so it stays a 400, never a 500. None means "use the default".
    if value is not None and int(value) < 1:
        raise StapelValidationError(ERR_400_INVALID_TIMEOUT)
    return value


class CompleteRequestSerializer(StapelDataclassSerializer):
    class Meta:
        dataclass = CompleteRequest

    def validate_model(self, value):
        if value not in MODEL_SIZES:
            raise StapelValidationError(ERR_400_INVALID_MODEL_SIZE)
        return value

    def validate_images(self, value):
        for entry in value or []:
            if not isinstance(entry, dict):
                raise StapelValidationError(ERR_400_INVALID_IMAGE)
            has_url = bool(entry.get("url"))
            has_b64 = bool(entry.get("data_b64"))
            if has_url == has_b64:  # neither, or both
                raise StapelValidationError(ERR_400_INVALID_IMAGE)
            if has_b64:
                try:
                    base64.b64decode(entry["data_b64"], validate=True)
                except (ValueError, TypeError):
                    raise StapelValidationError(ERR_400_INVALID_IMAGE)
        return value


class TranslateRequestSerializer(StapelDataclassSerializer):
    """Accepts the wire format where the source language key is ``from``
    — a Python keyword, mapped explicitly onto ``from_lang``."""

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

    def validate_timeout_seconds(self, value):
        return _validate_timeout_seconds(value)


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


class GenerateImageRequestSerializer(StapelDataclassSerializer):
    class Meta:
        dataclass = GenerateImageRequest

    def validate_n(self, value):
        if not 1 <= int(value) <= 10:
            raise StapelValidationError(ERR_400_INVALID_IMAGE_COUNT)
        return value

    def validate_timeout_seconds(self, value):
        return _validate_timeout_seconds(value)
