"""OpenAI-compatible Whisper HTTP adapter.

One adapter covers the OpenAI Whisper API and self-hosted
faster-whisper/whisper.cpp servers speaking the same dialect
(``POST {base}/audio/transcriptions``, multipart, ``verbose_json``).

Upload-capable: accepts any AudioRef kind (url is downloaded first,
path is read, bytes go straight in) — this is the reason a local
faster-whisper works through the same seam.

Settings (all read lazily): ``WHISPER_BASE_URL`` (required),
``WHISPER_API_KEY`` (optional — self-hosted servers often have none),
``WHISPER_MODEL`` (default ``whisper-1``).
"""
from __future__ import annotations

from typing import Optional

import requests

from ...conf import agent_settings
from .. import failures, segmentation
from ..base import (
    AudioRef,
    NormalizedTranscript,
    NormalizedUtterance,
    NormalizedWord,
    SttProvider,
    normalize_language,
    unsupported_biasing,
)


class WhisperHttpProvider(SttProvider):
    name = "whisper-http"
    supports_diarization = False
    # The OpenAI dialect has no keyterm/vocabulary parameter — requested
    # keyterms are reported as not applied (biasing block), never dropped
    # silently. (A host that wants Whisper's ``prompt`` biasing can send
    # it via provider_options={"prompt": ...} — a caller-owned decision.)
    supports_keyterms = False

    def default_speech_model(self) -> Optional[str]:
        return agent_settings.WHISPER_MODEL

    def transcribe(
        self,
        *,
        audio: AudioRef,
        language: Optional[str] = None,
        diarization: bool = False,
        timeout_seconds: Optional[int] = None,
        keyterms: Optional[list[str]] = None,
        provider_options: Optional[dict] = None,
    ) -> NormalizedTranscript:
        base_url = (agent_settings.WHISPER_BASE_URL or "").rstrip("/")
        if not base_url:
            # Unconfigured endpoint = this provider is unavailable in this
            # deployment; the chain must walk past it, not die on it.
            raise failures.missing_credentials(
                "WHISPER_BASE_URL", provider=self.name
            )
        timeout = (
            int(agent_settings.STT_TIMEOUT)
            if timeout_seconds is None
            else int(timeout_seconds)
        )
        payload = audio.read_bytes(provider=self.name, timeout=min(timeout, 600))

        data = {
            "model": self.effective_model(),
            "response_format": "verbose_json",
            "timestamp_granularities[]": "word",
        }
        if language:
            data["language"] = normalize_language(language)
        if provider_options:
            # The passthrough seam: caller-pinned provider specifics win
            # over (are applied after) the adapter's own params.
            data.update(provider_options)
        headers = {}
        api_key = agent_settings.WHISPER_API_KEY
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        try:
            resp = requests.post(
                f"{base_url}/audio/transcriptions",
                headers=headers,
                files={"file": ("audio", payload, audio.mime or "application/octet-stream")},
                data=data,
                timeout=timeout,
            )
        except requests.Timeout as exc:
            raise failures.timed_out(
                f"whisper request timed out: {exc}", provider=self.name
            ) from exc
        except requests.RequestException as exc:
            raise failures.transport(
                f"whisper transport error: {exc}", provider=self.name
            ) from exc

        # Per-RESPONSE classification — see stt/failures.py.
        failures.raise_for_status(resp, provider=self.name, label="whisper")

        try:
            body = resp.json()
        except ValueError as exc:
            raise failures.unavailable(
                f"whisper returned non-JSON: {resp.text[:300]}", provider=self.name
            ) from exc
        transcript = _normalize(body, provider=self.name)
        transcript.biasing = unsupported_biasing(keyterms)
        return transcript


def _normalize(payload: dict, *, provider: str) -> NormalizedTranscript:
    """Map an OpenAI ``verbose_json`` body → NormalizedTranscript."""
    words = [
        NormalizedWord(
            text=w.get("word", "").strip(),
            start=float(w.get("start") or 0.0),
            end=float(w.get("end") or w.get("start") or 0.0),
        )
        for w in payload.get("words") or []
        if w.get("word")
    ]
    utterances = [
        NormalizedUtterance(
            text=(s.get("text") or "").strip(),
            start=float(s.get("start") or 0.0),
            end=float(s.get("end") or 0.0),
        )
        for s in payload.get("segments") or []
        if (s.get("text") or "").strip()
    ]
    # Word timings first: the whole-text fallback below is ONE utterance
    # covering the file, which is the wall of text this release is about.
    # It is kept only for a response that carries no words to cut with.
    utterances = segmentation.finalize(utterances, words)
    if not utterances and payload.get("text"):
        end = max((w.end for w in words), default=0.0)
        utterances = [
            NormalizedUtterance(text=payload["text"].strip(), start=0.0, end=end)
        ]

    duration = payload.get("duration")
    try:
        duration = float(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration = None
    if duration is None and words:
        duration = max(w.end for w in words)

    return NormalizedTranscript(
        provider=provider,
        language=payload.get("language"),
        duration_seconds=duration,
        words=words,
        utterances=utterances,
        speakers_detected=[],
        raw=payload,
    )


__all__ = ["WhisperHttpProvider"]
