"""xAI Grok STT adapter — ONE synchronous multipart POST.

The only adapter with this shape: a single ``POST {XAI_STT_URL}``
(multipart/form-data) returns the transcript directly — no upload step,
no polling. Upload-capable: any AudioRef kind via ``read_bytes()``.

Facts verified against the official docs via the iron-benchmark port
(2026-07-10):

- auth ``Authorization: Bearer <key>``;
- there is NO ``model`` parameter — the served model cannot be pinned
  and the response carries no model echo (``default_speech_model`` is
  therefore None; a ``speech_model`` pin would fake a knob that does not
  exist);
- ``file`` must be the LAST multipart field — ``requests`` renders
  ``data=`` fields before ``files=`` parts, satisfying it structurally;
- ``diarize`` "true"/"false": each word gains an integer 0-based
  ``speaker``;
- ``format=true`` (Inverse Text Normalization) REQUIRES ``language`` (a
  documented 400 otherwise) and is defined for a 25-language list —
  outside it only ``language`` is sent;
- response: ``{text, language (currently always "" — detection not yet
  enabled; mapped to None), duration (seconds), words[]: {text, start,
  end (seconds), confidence (entropy-based; omitted when 0), speaker}}``.

Vocabulary biasing (``keyterms``): ``keyterm`` is a REPEATED multipart
field, documented limits max 100 terms x 50 chars. Out-of-limit terms
are TRUNCATED (counted in ``NormalizedTranscript.biasing``), never
errors.

Settings: ``XAI_API_KEY`` (required), ``XAI_STT_URL``.
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
    utterances_from_words,
)

# Documented keyterm limits (docs verified 2026-07-10): out-of-limit
# terms are truncated with counts in `biasing`, never raised.
MAX_KEYTERMS = 100
MAX_KEYTERM_CHARS = 50

#: The 25 languages the ``language``+``format=true`` pair documents (ITN
#: formatting only — transcription runs regardless of this set).
FORMATTING_LANGUAGES = frozenset({
    "ar", "cs", "da", "nl", "en", "fil", "fr", "de", "hi", "id", "it", "ja",
    "ko", "mk", "ms", "fa", "pl", "pt", "ro", "ru", "es", "sv", "th", "tr",
    "vi",
})


def _filter_keyterms(keyterms: list[str]) -> tuple[list[str], int]:
    """(accepted, truncated_count) under the documented xAI limits."""
    accepted: list[str] = []
    truncated = 0
    for term in keyterms:
        stripped = term.strip()
        if (
            not stripped
            or len(stripped) > MAX_KEYTERM_CHARS
            or len(accepted) >= MAX_KEYTERMS
        ):
            truncated += 1
            continue
        accepted.append(stripped)
    return accepted, truncated


class XaiSttProvider(SttProvider):
    name = "xai-stt"
    supports_diarization = True
    supports_keyterms = True
    # No public per-hour rate card verified for this endpoint yet.
    cost_per_hour = None

    # NB: no default_speech_model override — the endpoint has no model
    # parameter, so there is nothing to configure or pin.

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
        api_key = agent_settings.XAI_API_KEY
        if not api_key:
            raise TranscriptionError(
                "STAPEL_AGENT['XAI_API_KEY'] is not set", provider=self.name
            )
        timeout = (
            int(agent_settings.STT_TIMEOUT)
            if timeout_seconds is None
            else int(timeout_seconds)
        )
        payload = audio.read_bytes(provider=self.name, timeout=min(timeout, 600))

        data: dict = {"diarize": "true" if diarization else "false"}
        lang = normalize_language(language)
        if lang:
            data["language"] = lang
            if lang in FORMATTING_LANGUAGES:
                # format=true without language is a documented 400 — the
                # pair is sent together or not at all.
                data["format"] = "true"

        biasing = None
        if keyterms:
            accepted, truncated = _filter_keyterms(keyterms)
            if accepted:
                # A list value becomes the documented REPEATED multipart
                # field (keyterm=A, keyterm=B) under `requests`.
                data["keyterm"] = accepted
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
                agent_settings.XAI_STT_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                data=data,
                # files render AFTER data fields — the documented
                # "file field last" requirement.
                files={"file": ("audio", payload, audio.mime or "application/octet-stream")},
                timeout=timeout,
            )
        except requests.Timeout as exc:
            raise RetryableTranscriptionError(
                f"xAI STT request timed out: {exc}", provider=self.name
            ) from exc
        except requests.RequestException as exc:
            raise RetryableTranscriptionError(
                f"xAI STT transport error: {exc}", provider=self.name
            ) from exc

        if resp.status_code == 429:
            raise RetryableTranscriptionError(
                "xAI STT rate-limited", provider=self.name, status_code=429
            )
        if resp.status_code >= 500:
            # Includes the documented 502 (audio fetch) / 503.
            raise RetryableTranscriptionError(
                f"xAI STT {resp.status_code}: {resp.text[:300]}",
                provider=self.name,
                status_code=resp.status_code,
            )
        if resp.status_code >= 400:
            raise TranscriptionError(
                f"xAI STT {resp.status_code}: {resp.text[:300]}",
                provider=self.name,
                status_code=resp.status_code,
            )

        try:
            body = resp.json()
        except ValueError as exc:
            raise RetryableTranscriptionError(
                f"xAI STT returned non-JSON: {resp.text[:300]}",
                provider=self.name,
            ) from exc
        transcript = _normalize(body, provider=self.name)
        transcript.biasing = biasing
        return transcript


def _speaker_label(value) -> Optional[str]:
    """xAI speakers are INTEGERS starting at 0 (0 is valid and falsy —
    always test ``is not None``) → the label ``speaker_{n}``."""
    if value is None:
        return None
    return f"speaker_{value}"


def _normalize(payload: dict, *, provider: str) -> NormalizedTranscript:
    """Map a ``POST /v1/stt`` response (flat ``words[]``, seconds).
    Utterances are derived same-speaker word runs; with no word timing at
    all, the plain ``text`` becomes one unattributed utterance."""
    words: list[NormalizedWord] = []
    speakers: list[str] = []
    for tok in payload.get("words") or []:
        if not isinstance(tok, dict):
            continue
        text = (tok.get("text") or "").strip()
        if not text or tok.get("start") is None or tok.get("end") is None:
            continue
        speaker = _speaker_label(tok.get("speaker"))
        if speaker and speaker not in speakers:
            speakers.append(speaker)
        words.append(
            NormalizedWord(
                text=text,
                start=float(tok["start"]),
                end=float(tok["end"]),
                confidence=tok.get("confidence"),  # omitted-when-0 upstream
                speaker=speaker,
            )
        )

    duration = payload.get("duration")
    try:
        duration = float(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration = None
    if duration is None and words:
        duration = max(w.end for w in words)

    utterances = utterances_from_words(words)
    full_text = (payload.get("text") or "").strip()
    if not utterances and full_text:
        # Documented as possible: words[] omitted when empty.
        utterances = [
            NormalizedUtterance(text=full_text, start=0.0, end=duration or 0.0)
        ]

    return NormalizedTranscript(
        provider=provider,
        # Documented quirk: language is CURRENTLY always "" (detection
        # not yet enabled) — mapped to None, never stored verbatim.
        language=payload.get("language") or None,
        duration_seconds=duration,
        words=words,
        utterances=utterances,
        speakers_detected=speakers,
        raw=payload,
    )


__all__ = ["XaiSttProvider"]
