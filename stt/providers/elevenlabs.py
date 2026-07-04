"""ElevenLabs Scribe adapter (ported from the legacy recordings service).

Scribe is a synchronous batch endpoint (``POST /v1/speech-to-text``) —
the adapter downloads the audio from the presigned URL and streams it
into a multipart upload. Requires a URL ref (the service tier that owns
raw bytes should upload them and pass a presigned URL).

Settings: ``ELEVENLABS_API_KEY`` (required), ``ELEVENLABS_STT_URL``,
``ELEVENLABS_STT_MODEL`` (default ``scribe_v2``).

Response shape relied upon::

    {"language_code": "en", "text": "...",
     "words": [{"text": "Hello", "start": 0.0, "end": 0.4,
                "type": "word", "speaker_id": "speaker_0"}, ...]}
"""
from __future__ import annotations

from typing import Optional

import requests

from ...conf import agent_settings
from ..base import (
    AudioRef,
    NormalizedTranscript,
    NormalizedUtterance,
    NormalizedWord,
    RetryableTranscriptionError,
    SttProvider,
    TranscriptionError,
    normalize_language,
)


class ElevenLabsProvider(SttProvider):
    name = "elevenlabs"
    supports_diarization = True
    cost_per_hour = 0.40  # public list price — verify before billing off it

    def transcribe(
        self,
        *,
        audio: AudioRef,
        language: Optional[str] = None,
        diarization: bool = False,
        timeout_seconds: Optional[int] = None,
    ) -> NormalizedTranscript:
        api_key = agent_settings.ELEVENLABS_API_KEY
        if not api_key:
            raise TranscriptionError(
                "STAPEL_AGENT['ELEVENLABS_API_KEY'] is not set", provider=self.name
            )
        audio.require_url(provider=self.name)
        timeout = int(timeout_seconds or agent_settings.STT_TIMEOUT)
        payload = audio.read_bytes(provider=self.name, timeout=min(timeout, 600))

        data = {
            "model_id": agent_settings.ELEVENLABS_STT_MODEL,
            "timestamps_granularity": "word",
            "diarize": "true" if diarization else "false",
        }
        if language:
            data["language_code"] = normalize_language(language)

        try:
            resp = requests.post(
                agent_settings.ELEVENLABS_STT_URL,
                headers={"xi-api-key": api_key},
                files={"file": ("audio", payload, "application/octet-stream")},
                data=data,
                timeout=timeout,
            )
        except requests.Timeout as exc:
            raise RetryableTranscriptionError(
                f"ElevenLabs request timed out: {exc}", provider=self.name
            ) from exc
        except requests.RequestException as exc:
            raise RetryableTranscriptionError(
                f"ElevenLabs transport error: {exc}", provider=self.name
            ) from exc

        if resp.status_code == 429:
            raise RetryableTranscriptionError(
                "ElevenLabs rate-limited", provider=self.name, status_code=429
            )
        if resp.status_code >= 500:
            raise RetryableTranscriptionError(
                f"ElevenLabs {resp.status_code}: {resp.text[:300]}",
                provider=self.name,
                status_code=resp.status_code,
            )
        if resp.status_code >= 400:
            raise TranscriptionError(
                f"ElevenLabs {resp.status_code}: {resp.text[:300]}",
                provider=self.name,
                status_code=resp.status_code,
            )

        try:
            body = resp.json()
        except ValueError as exc:
            raise RetryableTranscriptionError(
                f"ElevenLabs returned non-JSON: {resp.text[:300]}",
                provider=self.name,
            ) from exc
        return _normalize(body, provider=self.name)


def _normalize(payload: dict, *, provider: str) -> NormalizedTranscript:
    """Ported verbatim in behaviour: skip non-word tokens, group
    consecutive same-speaker words into utterances."""
    words_in = payload.get("words") or []
    words: list[NormalizedWord] = []
    speakers: list[str] = []
    utterances: list[NormalizedUtterance] = []

    current_speaker: object = object()  # sentinel — first word always flushes
    current_words: list[int] = []
    current_text: list[str] = []
    current_start: Optional[float] = None
    current_end: Optional[float] = None

    def flush():
        if not current_words or current_start is None or current_end is None:
            return
        utterances.append(
            NormalizedUtterance(
                text=" ".join(current_text).strip(),
                start=current_start,
                end=current_end,
                speaker=current_speaker if isinstance(current_speaker, str) else None,
                word_indexes=list(current_words),
            )
        )

    for raw in words_in:
        if raw.get("type") and raw["type"] != "word":
            # spacing/audio_event tokens carry timing only — skip
            continue
        text = raw.get("text", "")
        if not text:
            continue
        start = float(raw.get("start") or 0.0)
        end = float(raw.get("end") or start)
        speaker = raw.get("speaker_id")
        if speaker and speaker not in speakers:
            speakers.append(speaker)

        words.append(NormalizedWord(text=text, start=start, end=end, speaker=speaker))

        if speaker != current_speaker:
            flush()
            current_speaker = speaker
            current_words = [len(words) - 1]
            current_text = [text]
            current_start = start
            current_end = end
        else:
            current_words.append(len(words) - 1)
            current_text.append(text)
            current_end = end

    flush()

    return NormalizedTranscript(
        provider=provider,
        language=payload.get("language_code"),
        duration_seconds=max((w.end for w in words), default=None),
        words=words,
        utterances=utterances,
        speakers_detected=speakers,
        raw=payload,
    )


__all__ = ["ElevenLabsProvider"]
