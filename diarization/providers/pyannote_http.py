"""Self-hosted pyannote diarization HTTP adapter.

One synchronous multipart POST to a self-hosted pyannote wrapper service
(gigaam-style plain HTTP — a thin FastAPI/Flask shim around
``pyannote.audio``'s ``Pipeline.apply()``), NOT the pyannoteAI cloud API
(no media upload, no job polling, no billing).

Wire contract (documented here because the server side is a thin shim the
host deploys; field names deliberately mirror the two primary sources —
``pyannote.audio``'s own ``apply(file, num_speakers=None, min_speakers=None,
max_speakers=None)`` signature for the request knobs, and the pyannoteAI
job-output ``diarization`` segment shape for the response, so a wrapper
just serializes the pipeline output under the documented key)::

    POST {PYANNOTE_BASE_URL}/diarize
        multipart/form-data:
            file          audio bytes (any AudioRef kind — url is
                          downloaded first, path is read, bytes go
                          straight in)
        form fields (all optional):
            num_speakers  exact speaker count (>= 1)
            min_speakers / max_speakers
                          bound hints (>= 1, min <= max; via
                          provider_options — mutually exclusive with
                          num_speakers, validated BEFORE the call)
            ...           any further provider_options keys, as-is
        headers:
            Authorization: Bearer {PYANNOTE_API_KEY}   (only when set —
                          self-hosted services often have no key)

    -> 200 JSON {
        "diarization": [
            {"speaker": "SPEAKER_00", "start": 0.0, "end": 1.5,
             "confidence"?: float},
            ...
        ],                        # seconds-float, chronological (required)
        "duration"?: float        # seconds; inferred from turns if absent
    }

Ported from the iron-benchmark pyannote adapter (invariants, not code):
the seconds-float ``{speaker, start, end}`` segment shape, the
speaker-count knob validation (exact count XOR bounds, all >= 1,
min <= max — fail loudly before the call), inverted-segment clamping
(``end < start`` → clamped, never dropped) and the missing-``diarization``
= loud failure rule. NOT ported: the cloud upload→submit→poll job
machinery, model pinning (the self-hosted model is fixed server-side),
the ``exclusive`` non-overlap layer (cloud-only; a wrapper that offers it
can be reached via ``provider_options`` and the extra layer survives in
``raw``), the EUR pricing card, and the empty-diarization-is-an-error
gate (that is hybrid-merge policy — caller's decision, see
``NormalizedDiarization.turns``).

Settings (all read lazily): ``PYANNOTE_BASE_URL`` (required),
``PYANNOTE_API_KEY`` (optional), ``DIARIZATION_TIMEOUT`` (default cap).
"""
from __future__ import annotations

from typing import Optional

import requests

from ...conf import agent_settings
from ..base import (
    DiarizationError,
    DiarizationProvider,
    NormalizedDiarization,
    RetryableDiarizationError,
    turns_from_segments,
    validate_speaker_counts,
)


class PyannoteHttpProvider(DiarizationProvider):
    name = "pyannote-http"

    def diarize(
        self,
        *,
        audio,
        num_speakers: Optional[int] = None,
        timeout_seconds: Optional[int] = None,
        provider_options: Optional[dict] = None,
    ) -> NormalizedDiarization:
        base_url = (agent_settings.PYANNOTE_BASE_URL or "").rstrip("/")
        if not base_url:
            raise DiarizationError(
                "STAPEL_AGENT['PYANNOTE_BASE_URL'] is not configured",
                provider=self.name,
            )
        timeout = (
            int(agent_settings.DIARIZATION_TIMEOUT)
            if timeout_seconds is None
            else int(timeout_seconds)
        )

        # Bound hints travel via provider_options; validate the knob
        # combination BEFORE any call (ported fail-loud invariant).
        options = dict(provider_options or {})
        min_speakers = options.pop("min_speakers", None)
        max_speakers = options.pop("max_speakers", None)
        validate_speaker_counts(
            num_speakers=num_speakers,
            min_speakers=min_speakers,
            max_speakers=max_speakers,
            provider=self.name,
        )

        data: dict = {}
        if num_speakers is not None:
            data["num_speakers"] = int(num_speakers)
        if min_speakers is not None:
            data["min_speakers"] = int(min_speakers)
        if max_speakers is not None:
            data["max_speakers"] = int(max_speakers)
        if options:
            # The passthrough seam: remaining caller-pinned provider
            # specifics win over (are applied after) the adapter's params.
            data.update(options)

        headers = {}
        api_key = agent_settings.PYANNOTE_API_KEY
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        # AudioRef.read_bytes raises the STT error taxonomy on a bad ref —
        # re-raise it under the diarization taxonomy, fatal/retryable
        # preserved.
        from ...stt.base import RetryableTranscriptionError, TranscriptionError

        try:
            payload = audio.read_bytes(provider=self.name, timeout=min(timeout, 600))
        except RetryableTranscriptionError as exc:
            raise RetryableDiarizationError(
                str(exc), provider=self.name, status_code=exc.status_code
            ) from exc
        except TranscriptionError as exc:
            raise DiarizationError(
                str(exc), provider=self.name, status_code=exc.status_code
            ) from exc

        try:
            resp = requests.post(
                f"{base_url}/diarize",
                headers=headers,
                files={
                    "file": ("audio", payload, audio.mime or "application/octet-stream")
                },
                data=data,
                timeout=timeout,
            )
        except requests.Timeout as exc:
            raise RetryableDiarizationError(
                f"pyannote request timed out: {exc}", provider=self.name
            ) from exc
        except requests.RequestException as exc:
            raise RetryableDiarizationError(
                f"pyannote transport error: {exc}", provider=self.name
            ) from exc

        if resp.status_code == 429:
            raise RetryableDiarizationError(
                "pyannote endpoint rate-limited", provider=self.name, status_code=429
            )
        if resp.status_code >= 500:
            raise RetryableDiarizationError(
                f"pyannote {resp.status_code}: {resp.text[:300]}",
                provider=self.name,
                status_code=resp.status_code,
            )
        if resp.status_code >= 400:
            raise DiarizationError(
                f"pyannote {resp.status_code}: {resp.text[:300]}",
                provider=self.name,
                status_code=resp.status_code,
            )

        try:
            body = resp.json()
        except ValueError as exc:
            raise RetryableDiarizationError(
                f"pyannote returned non-JSON: {resp.text[:300]}", provider=self.name
            ) from exc
        return _normalize(body, provider=self.name)


def _normalize(payload: dict, *, provider: str) -> NormalizedDiarization:
    """Map the documented response body → NormalizedDiarization."""
    segments = payload.get("diarization")
    if not isinstance(segments, list):
        # Contract-required key (ported rule: a malformed success must
        # fail loudly, never produce an empty result).
        raise DiarizationError(
            "pyannote response has no 'diarization' list: "
            f"{str(payload)[:300]}",
            provider=provider,
        )
    try:
        turns = turns_from_segments(segments)
    except (KeyError, TypeError, ValueError) as exc:
        raise DiarizationError(
            f"pyannote response segment malformed: {exc}", provider=provider
        ) from exc

    duration = payload.get("duration")
    try:
        duration = float(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration = None
    if duration is None and turns:
        duration = max(t.end for t in turns)

    return NormalizedDiarization(
        provider=provider,
        duration_seconds=duration,
        turns=turns,
        speakers_detected=sorted({t.speaker for t in turns}),
        raw=payload,
    )


__all__ = ["PyannoteHttpProvider"]
