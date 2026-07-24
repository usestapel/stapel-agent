"""Diarization provider seam — normalized speaker turns, ABC, errors.

Diarization is a SEPARATE seam from STT on purpose (the iron-benchmark
lesson): a diarization adapter returns *who spoke when* (speaker turns),
never words. Fusing turns with STT words — nearest-basis assignment,
overlap resolution, turn/word reconciliation — is merge-policy know-how
that belongs to client apps, not to this core.

The audio input reuses ``stt.base.AudioRef`` (exactly one of url/path/
data); errors join the house hierarchy: ``DiarizationError
(ProviderError)`` (fatal — bad input/auth, no fallback) and
``RetryableDiarizationError`` (429/5xx/timeouts), same taxonomy as
STT/images.

This module is deliberately Django-free.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Optional

from ..providers.base import ProviderError


class DiarizationError(ProviderError):
    """Permanent diarization failure (bad audio, auth, bad knobs, ...).

    The service reports ``status: "failure"`` immediately.
    """

    def __init__(self, message: str, *, provider: str = "", status_code: int | None = None):
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code


class RetryableDiarizationError(DiarizationError):
    """Transient diarization failure (network, 429, 5xx, timeout)."""


# ─── Normalized diarization schema ─────────────────────────────────────


@dataclass
class DiarTurn:
    """One speaker turn. Times are seconds from audio start (house canon —
    the same unit as ``NormalizedWord``/``NormalizedUtterance``)."""

    speaker: str
    start: float
    end: float
    confidence: Optional[float] = None


@dataclass
class NormalizedDiarization:
    """Output every diarization provider must return.

    Attributes:
        provider: Adapter id (e.g. ``pyannote-http``). Recorded on the
            PromptLog row for observability.
        duration_seconds: Total audio duration as reported/inferred
            (max turn end when the provider doesn't report one).
        turns: Speaker turns in wire order (providers emit them
            chronologically; the order is preserved, never re-sorted).
            An EMPTY list on a successful call means no speech was
            attributed — that is data, not an error; whether an empty
            diarization blocks a downstream merge is the caller's policy.
        speakers_detected: Sorted distinct speaker labels seen in
            ``turns``.
        raw: Untouched provider response for debugging / re-parsing.
    """

    provider: str
    duration_seconds: Optional[float]
    turns: list[DiarTurn] = field(default_factory=list)
    speakers_detected: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def turns_from_segments(segments: list) -> list[DiarTurn]:
    """Map wire segments ``[{"speaker", "start", "end", "confidence"?}]``
    (seconds-float — the pyannote ``output.diarization`` shape) into
    ``DiarTurn`` rows, order preserved.

    Ported invariant (iron-benchmark pyannote mapper): an INVERTED segment
    (``end < start``) is clamped up to ``start``, never dropped silently —
    the segment still marks WHO was near that moment for any downstream
    nearest-basis merge.
    """
    turns: list[DiarTurn] = []
    for seg in segments or []:
        start = float(seg["start"])
        end = float(seg["end"])
        if end < start:
            end = start
        confidence = seg.get("confidence")
        turns.append(
            DiarTurn(
                speaker=str(seg["speaker"]),
                start=start,
                end=end,
                confidence=float(confidence) if confidence is not None else None,
            )
        )
    return turns


def validate_speaker_counts(
    *,
    num_speakers: Optional[int] = None,
    min_speakers: Optional[int] = None,
    max_speakers: Optional[int] = None,
    provider: str = "",
) -> None:
    """Validate the speaker-count knob combination — fail loudly BEFORE
    any provider call (ported invariant from the iron-benchmark pyannote
    adapter):

    - ``num_speakers`` is an EXACT count, mutually exclusive with the
      ``min_speakers``/``max_speakers`` bounds (an exact count next to
      bounds is contradictory — pyannote treats numSpeakers as
      min==max);
    - every given count must be >= 1;
    - ``min_speakers <= max_speakers`` when both are given.

    Raises fatal ``DiarizationError`` on violation.
    """
    if num_speakers is not None and (
        min_speakers is not None or max_speakers is not None
    ):
        raise DiarizationError(
            "num_speakers is an EXACT count; combining it with "
            "min_speakers/max_speakers bounds is contradictory — pass one "
            "form only",
            provider=provider,
        )
    for name, value in (
        ("num_speakers", num_speakers),
        ("min_speakers", min_speakers),
        ("max_speakers", max_speakers),
    ):
        if value is not None and int(value) < 1:
            raise DiarizationError(
                f"{name} must be >= 1, got {value}", provider=provider
            )
    if (
        min_speakers is not None
        and max_speakers is not None
        and int(min_speakers) > int(max_speakers)
    ):
        raise DiarizationError(
            f"min_speakers {min_speakers} > max_speakers {max_speakers}",
            provider=provider,
        )


# ─── Provider ABC ──────────────────────────────────────────────────────


class DiarizationProvider(ABC):
    """Adapter for a single diarization engine.

    ``name`` is the stable id stored on the PromptLog row;
    ``cost_per_hour`` (USD, optional) lets hosts compute billing debits
    without a separate catalog — same convention as ``SttProvider``.
    """

    name: str = ""
    cost_per_hour: Optional[float] = None

    @abstractmethod
    def diarize(
        self,
        *,
        audio,
        num_speakers: Optional[int] = None,
        timeout_seconds: Optional[int] = None,
        provider_options: Optional[dict] = None,
    ) -> NormalizedDiarization:
        """Run a synchronous diarization of *audio* (an ``AudioRef``).

        ``num_speakers`` is the generic EXACT-count hint (None = let the
        provider decide). Bound hints (``min_speakers``/``max_speakers``)
        are provider-specific knobs and travel via ``provider_options`` —
        adapters that understand them must enforce the
        ``validate_speaker_counts`` invariants.

        ``provider_options`` is the house free-form per-provider
        passthrough, applied AFTER the adapter's own request params —
        a caller can pin provider specifics without a core release.
        Unknown keys go to the provider as-is; the adapter must NEVER
        silently drop them.

        Raises ``RetryableDiarizationError`` on transient failure
        (network, 429, 5xx, timeout) and ``DiarizationError`` on
        permanent failure (bad input, auth, bad knobs).
        """
        raise NotImplementedError


__all__ = [
    "DiarTurn",
    "DiarizationError",
    "DiarizationProvider",
    "NormalizedDiarization",
    "RetryableDiarizationError",
    "turns_from_segments",
    "validate_speaker_counts",
]
