"""ElevenLabs Scribe adapter.

Scribe is a synchronous batch endpoint (``POST /v1/speech-to-text``) —
the adapter downloads the audio from the presigned URL and streams it
into a multipart upload. Requires a URL ref (the service tier that owns
raw bytes should upload them and pass a presigned URL).

Settings: ``ELEVENLABS_API_KEY`` (required), ``ELEVENLABS_STT_URL``,
``ELEVENLABS_STT_MODEL`` (default ``scribe_v2``).

Vocabulary biasing (``keyterms``): Scribe takes a ``keyterms`` list in
the multipart body (sent here as repeated form fields — the standard
multipart list encoding). Documented limits (official docs survey,
2026-07-18): <=1000 terms per request, each term <50 chars and <=5
words, prohibited characters ``< > { } [ ] \\``. Terms outside the
limits are TRUNCATED (counted in ``NormalizedTranscript.biasing``),
never errors. BILLING: keyterms carry a +20% surcharge on the
transcription and >100 terms adds a 20s minimum billable duration —
sending terms is a cost decision the caller owns.

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
    biasing_metadata,
    normalize_language,
)

# Documented Scribe keyterm limits (docs survey 2026-07-18): out-of-limit
# terms are truncated with counts in `biasing`, never raised.
MAX_KEYTERMS = 1000
MAX_KEYTERM_CHARS = 50  # "must be less than 50 characters" → len < 50
MAX_KEYTERM_WORDS = 5
PROHIBITED_KEYTERM_CHARS = set("<>{}[]\\")


def _filter_keyterms(keyterms: list[str]) -> tuple[list[str], int]:
    """(accepted, truncated_count) under the documented Scribe limits."""
    accepted: list[str] = []
    truncated = 0
    for term in keyterms:
        stripped = term.strip()
        if (
            not stripped
            or len(stripped) >= MAX_KEYTERM_CHARS
            or len(stripped.split()) > MAX_KEYTERM_WORDS
            or any(ch in PROHIBITED_KEYTERM_CHARS for ch in stripped)
            or len(accepted) >= MAX_KEYTERMS
        ):
            truncated += 1
            continue
        accepted.append(stripped)
    return accepted, truncated


class ElevenLabsProvider(SttProvider):
    name = "elevenlabs"
    supports_diarization = True
    supports_keyterms = True
    cost_per_hour = 0.40  # public list price — verify before billing off it
    # NB: sending keyterms adds a +20% surcharge (see module docstring).

    def default_speech_model(self) -> Optional[str]:
        return agent_settings.ELEVENLABS_STT_MODEL

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
        api_key = agent_settings.ELEVENLABS_API_KEY
        if not api_key:
            raise TranscriptionError(
                "STAPEL_AGENT['ELEVENLABS_API_KEY'] is not set", provider=self.name
            )
        audio.require_url(provider=self.name)
        timeout = (
            int(agent_settings.STT_TIMEOUT)
            if timeout_seconds is None
            else int(timeout_seconds)
        )
        payload = audio.read_bytes(provider=self.name, timeout=min(timeout, 600))

        data = {
            "model_id": self.effective_model(),
            "timestamps_granularity": "word",
            "diarize": "true" if diarization else "false",
        }
        if language:
            data["language_code"] = normalize_language(language)

        biasing = None
        if keyterms:
            accepted, truncated = _filter_keyterms(keyterms)
            if accepted:
                # A list value becomes repeated multipart fields
                # (keyterms=A, keyterms=B) under `requests`.
                data["keyterms"] = accepted
            biasing = biasing_metadata(
                applied=bool(accepted),
                terms_sent=len(accepted),
                terms_truncated=truncated,
            )
        if provider_options:
            # The passthrough seam: applied AFTER the adapter's own params
            # so a caller can pin provider specifics without a core release.
            data.update(provider_options)

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
        transcript = _normalize(body, provider=self.name)
        transcript.biasing = biasing
        return transcript


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
