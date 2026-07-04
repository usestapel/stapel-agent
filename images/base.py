"""Image seam — vision-input refs, generation ABC, errors.

Two independent contracts share this module:

- **``ImageRef``** — vision *input* for ``llm.complete``: exactly one of
  ``url`` / ``data`` (bytes). Over comm/HTTP only ``url`` or base64
  ``data_b64`` travel (the JSON schema rejects anything else); raw bytes
  exist for in-process callers only. LLM providers map refs into their
  dialect's content blocks.
- **``ImageGenProvider``/``GeneratedImage``** — image *generation* for
  ``llm.generate_image``. Errors join the house hierarchy:
  ``ImageGenError(ProviderError)`` (fatal — bad prompt/size/auth) and
  ``RetryableImageGenError`` (429/5xx/timeouts), same taxonomy as STT.

This module is deliberately Django-free.
"""
from __future__ import annotations

import base64
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from ..providers.base import ProviderError


class ImageGenError(ProviderError):
    """Permanent image-generation failure (bad prompt/size, auth, ...)."""

    def __init__(self, message: str, *, provider: str = "", status_code: int | None = None):
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code


class RetryableImageGenError(ImageGenError):
    """Transient image-generation failure (network, 429, 5xx, timeout)."""


# ─── Vision input ──────────────────────────────────────────────────────


@dataclass
class ImageRef:
    """Reference to one input image — exactly one of url/data.

    ``url`` is a fetchable HTTP(S) URL the model vendor pulls itself;
    ``data`` is raw bytes (in-process callers only — the wire carries
    base64 ``data_b64`` instead). ``mime`` defaults to ``image/png``
    for byte refs; for URL refs the vendor sniffs the content type.
    """

    url: Optional[str] = None
    data: Optional[bytes] = None
    mime: Optional[str] = None

    def __post_init__(self):
        provided = [k for k in ("url", "data") if getattr(self, k)]
        if len(provided) != 1:
            raise ValueError(
                "ImageRef needs exactly one of url/data, got "
                + (", ".join(provided) or "none")
            )
        if self.data is not None and not self.mime:
            self.mime = "image/png"

    @classmethod
    def from_payload(cls, payload: dict) -> "ImageRef":
        """Build from a wire payload: ``{"url": ...}`` or
        ``{"data_b64": ..., "mime"?: ...}``. Raises ``ValueError`` on
        invalid base64 (``binascii.Error`` is a ``ValueError``)."""
        data = payload.get("data")
        if data is None and payload.get("data_b64"):
            data = base64.b64decode(payload["data_b64"], validate=True)
        return cls(url=payload.get("url"), data=data, mime=payload.get("mime"))

    @property
    def kind(self) -> str:
        return "url" if self.url else "data"

    def as_base64(self) -> str:
        """Base64 form of the byte payload (base64/data-URI content blocks)."""
        return base64.b64encode(self.data or b"").decode("ascii")


# ─── Generation ────────────────────────────────────────────────────────


@dataclass
class GeneratedImage:
    """One generated image — the provider returns ``url`` and/or
    ``data_b64`` (never raw bytes; results go straight onto the wire).

    Storage into CDN/asset libraries is the CALLER's job — this module
    returns the raw result and writes the ledger, nothing else.
    """

    mime: str = "image/png"
    url: Optional[str] = None
    data_b64: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            k: v
            for k, v in (("url", self.url), ("data_b64", self.data_b64), ("mime", self.mime))
            if v is not None
        }


def b64_decoded_size(data_b64: Optional[str]) -> int:
    """Decoded byte length of a base64 string without decoding it
    (ledger metadata: ``bytes_total`` — never the bytes themselves)."""
    if not data_b64:
        return 0
    return len(data_b64) * 3 // 4 - data_b64[-2:].count("=")


class ImageGenProvider(ABC):
    """Adapter for one image-generation backend.

    ``name`` is the stable id stored on the PromptLog row;
    ``supported_sizes`` is a set of ``"WxH"`` strings or None for "any"
    (the service rejects unsupported sizes before calling out).
    """

    name: str = ""
    supported_sizes: Optional[frozenset[str]] = None

    @abstractmethod
    def generate(
        self,
        *,
        prompt: str,
        size: str = "1024x1024",
        n: int = 1,
        timeout_seconds: Optional[int] = None,
    ) -> list[GeneratedImage]:
        """Generate *n* images for *prompt*.

        Raise ``RetryableImageGenError`` on transient failure (network,
        429, 5xx, timeout) and ``ImageGenError`` on permanent failure
        (bad prompt/size, auth).
        """
        raise NotImplementedError


__all__ = [
    "GeneratedImage",
    "ImageGenError",
    "ImageGenProvider",
    "ImageRef",
    "RetryableImageGenError",
    "b64_decoded_size",
]
