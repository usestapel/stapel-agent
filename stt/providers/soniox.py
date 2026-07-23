"""Soniox async STT adapter (stt-async-v5).

Five-step flow (verified against the official OpenAPI schema via the
iron-benchmark port, 2026-07-10):

1. ``POST /v1/files`` — multipart field ``file`` → ``{id}``.
   Upload-capable: any AudioRef kind via ``read_bytes()``;
2. ``POST /v1/transcriptions`` — JSON ``{model, file_id,
   enable_speaker_diarization, language_hints?}`` → ``{id}``.
   Each successful create is a BILLING event;
3. ``GET /v1/transcriptions/{id}`` — poll until ``completed``
   (``error`` OR ``failed`` is terminal-fatal — the docs use both);
4. ``GET /v1/transcriptions/{id}/transcript`` → ``{id, text, tokens[]}``;
5. cleanup: DELETE the uploaded FILE in every outcome (Soniox does NOT
   auto-delete uploads and they count against fixed quotas — 10 GB /
   1000 files) and the TRANSCRIPTION after a successful fetch. Cleanup
   failures never mask a successful transcription.

Auth: ``Authorization: Bearer <key>``. ``language_hints`` BIAS language
detection, they do not restrict it — omitted entirely with no language
(the model's native full-auto multilingual mode). Tokens are SUB-WORD
("Beau"/"ti"/"ful") with native MILLISECOND times and a STRING speaker
("1".."15"); this adapter merges them into words (a token starts a new
word when it is first, its text begins with whitespace, or its
speaker/language changed — punctuation glues onto the word it follows)
and converts ms → s.

No vocabulary biasing is wired: the OpenAPI documents an optional
``context`` (general/text/terms) but its terms shape is not covered by
the sources this port is pinned to — requested ``keyterms`` are reported
as not applied via the ``biasing`` block; a host can send ``context``
through ``provider_options`` (merged into the create body as-is).

Settings: ``SONIOX_API_KEY`` (required), ``SONIOX_BASE_URL``,
``SONIOX_MODEL`` (default ``stt-async-v5``).
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import requests

from ...conf import agent_settings
from ..base import (
    AudioRef,
    NormalizedTranscript,
    NormalizedWord,
    RetryableTranscriptionError,
    SttProvider,
    TranscriptionError,
    normalize_language,
    unsupported_biasing,
    utterances_from_words,
)

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_S = 120  # per-request cap (the upload can be slow)
INITIAL_POLL_INTERVAL_S = 5.0
MAX_POLL_INTERVAL_S = 30.0
POLL_INTERVAL_GROWTH = 1.5


class SonioxProvider(SttProvider):
    name = "soniox"
    supports_diarization = True
    supports_keyterms = False  # see module docstring
    cost_per_hour = 0.36  # async list price $0.006/min — verify before billing

    def default_speech_model(self) -> Optional[str]:
        return agent_settings.SONIOX_MODEL

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
        api_key = agent_settings.SONIOX_API_KEY
        if not api_key:
            raise TranscriptionError(
                "STAPEL_AGENT['SONIOX_API_KEY'] is not set", provider=self.name
            )
        timeout = (
            int(agent_settings.STT_TIMEOUT)
            if timeout_seconds is None
            else int(timeout_seconds)
        )
        payload = audio.read_bytes(provider=self.name, timeout=min(timeout, 600))

        body: dict = {
            # Explicit model — REQUIRED by the schema ("stt-async-v4"
            # silently routes to v5 server-side; pin the real id).
            "model": self.effective_model(),
            "enable_speaker_diarization": bool(diarization),
        }
        if language:
            # One hint biases detection without restricting it; no
            # language = the native full-auto multilingual mode.
            body["language_hints"] = [normalize_language(language)]
        if provider_options:
            # The passthrough seam: applied AFTER the adapter's own params
            # so a caller can pin provider specifics (e.g. ``context``)
            # without a core release.
            body.update(provider_options)

        file_id = self._upload(payload, mime=audio.mime)
        try:
            transcription_id = self._create({**body, "file_id": file_id})
            job = self._poll(transcription_id, timeout_seconds=timeout)
            transcript_payload = self._fetch_transcript(transcription_id)
            # Success — the transcription object is no longer needed.
            self._delete(f"/v1/transcriptions/{transcription_id}")
        finally:
            # The FILE is ours and reproducible — delete it in EVERY
            # outcome; a failed transcription object is kept for
            # forensics (error_type/error_message live on it).
            self._delete(f"/v1/files/{file_id}")

        transcript = _normalize(transcript_payload, provider=self.name, job=job)
        transcript.biasing = unsupported_biasing(keyterms)
        return transcript

    # ── HTTP helpers ──────────────────────────────────────────

    def _base_url(self) -> str:
        return (agent_settings.SONIOX_BASE_URL or "").rstrip("/")

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {agent_settings.SONIOX_API_KEY}"}

    def _request(self, method: str, path: str, *, op: str, **kwargs) -> dict:
        try:
            resp = requests.request(
                method,
                f"{self._base_url()}{path}",
                headers=self._headers(),
                timeout=REQUEST_TIMEOUT_S,
                **kwargs,
            )
        except requests.Timeout as exc:
            raise RetryableTranscriptionError(
                f"Soniox {op} timed out: {exc}", provider=self.name
            ) from exc
        except requests.RequestException as exc:
            raise RetryableTranscriptionError(
                f"Soniox {op} transport error: {exc}", provider=self.name
            ) from exc
        self._raise_for_status(resp, op=op)
        try:
            return resp.json()
        except ValueError as exc:
            raise RetryableTranscriptionError(
                f"Soniox {op} non-JSON: {resp.text[:200]}", provider=self.name
            ) from exc

    def _upload(self, payload: bytes, *, mime: Optional[str]) -> str:
        data = self._request(
            "POST",
            "/v1/files",
            op="upload",
            files={"file": ("audio", payload, mime or "application/octet-stream")},
        )
        file_id = data.get("id")
        if not file_id:
            raise RetryableTranscriptionError(
                f"Soniox upload returned no file id: {data}", provider=self.name
            )
        return file_id

    def _create(self, body: dict) -> str:
        data = self._request("POST", "/v1/transcriptions", op="create", json=body)
        transcription_id = data.get("id")
        if not transcription_id:
            raise RetryableTranscriptionError(
                f"Soniox create returned no transcription id: {data}",
                provider=self.name,
            )
        return transcription_id

    def _poll(self, transcription_id: str, *, timeout_seconds: int) -> dict:
        deadline = time.monotonic() + timeout_seconds
        interval = INITIAL_POLL_INTERVAL_S

        while True:
            if time.monotonic() >= deadline:
                raise RetryableTranscriptionError(
                    f"Soniox polling exceeded {timeout_seconds}s "
                    f"for {transcription_id}",
                    provider=self.name,
                )

            time.sleep(interval)

            try:
                resp = requests.get(
                    f"{self._base_url()}/v1/transcriptions/{transcription_id}",
                    headers=self._headers(),
                    timeout=REQUEST_TIMEOUT_S,
                )
            except requests.RequestException as exc:
                # A single poll hiccup is transient — keep polling.
                logger.debug("Soniox poll error for %s: %s", transcription_id, exc)
                interval = _grow(interval)
                continue

            if resp.status_code >= 500:
                interval = _grow(interval)
                continue

            self._raise_for_status(resp, op="poll")

            try:
                payload = resp.json()
            except ValueError:
                interval = _grow(interval)
                continue

            status = payload.get("status")
            if status == "completed":
                return payload
            if status in ("error", "failed"):
                raise TranscriptionError(
                    "Soniox job failed: "
                    f"{payload.get('error_message') or payload.get('error_type') or 'unknown'}",
                    provider=self.name,
                )
            # queued / processing — keep polling
            interval = _grow(interval)

    def _fetch_transcript(self, transcription_id: str) -> dict:
        return self._request(
            "GET",
            f"/v1/transcriptions/{transcription_id}/transcript",
            op="transcript fetch",
        )

    def _delete(self, path: str) -> bool:
        """Best-effort cleanup DELETE — never raises, never masks the
        transcription outcome."""
        try:
            resp = requests.delete(
                f"{self._base_url()}{path}",
                headers=self._headers(),
                timeout=REQUEST_TIMEOUT_S,
            )
            return 200 <= resp.status_code < 300
        except requests.RequestException as exc:
            logger.debug("Soniox cleanup DELETE %s failed: %s", path, exc)
            return False

    def _raise_for_status(self, resp, *, op: str) -> None:
        if 200 <= resp.status_code < 300:
            return
        if resp.status_code == 429:
            raise RetryableTranscriptionError(
                "Soniox rate-limited", provider=self.name, status_code=429
            )
        if resp.status_code >= 500:
            raise RetryableTranscriptionError(
                f"Soniox {op} {resp.status_code}: {resp.text[:300]}",
                provider=self.name,
                status_code=resp.status_code,
            )
        raise TranscriptionError(
            f"Soniox {op} {resp.status_code}: {resp.text[:300]}",
            provider=self.name,
            status_code=resp.status_code,
        )


def _grow(interval: float) -> float:
    return min(interval * POLL_INTERVAL_GROWTH, MAX_POLL_INTERVAL_S)


class _WordAccumulator:
    """A word being assembled from consecutive sub-word tokens."""

    __slots__ = ("text", "start_ms", "end_ms", "confidences", "speaker")

    def __init__(self, tok: dict):
        self.text = (tok.get("text") or "").lstrip()
        self.start_ms = tok.get("start_ms")
        self.end_ms = tok.get("end_ms")
        conf = tok.get("confidence")
        self.confidences = [conf] if conf is not None else []
        self.speaker = tok.get("speaker")

    def absorb(self, tok: dict) -> None:
        self.text += tok.get("text") or ""
        end = tok.get("end_ms")
        if end is not None:
            self.end_ms = end if self.end_ms is None else max(self.end_ms, end)
        conf = tok.get("confidence")
        if conf is not None:
            self.confidences.append(conf)


def _merge_tokens(tokens: list) -> list[_WordAccumulator]:
    """Merge Soniox sub-word tokens into words. Documented behaviour:
    "Beau|ti|ful" carries no leading spaces, " are" does, "?" attaches
    space-less — so a token starts a NEW word when it is the first one,
    its text begins with whitespace, or its speaker changed; otherwise it
    glues onto the current word."""
    words: list[_WordAccumulator] = []
    for tok in tokens:
        if not isinstance(tok, dict):
            continue
        text = tok.get("text") or ""
        if not text.strip():
            continue  # a pure-whitespace token carries nothing
        starts_new = (
            not words
            or text[:1].isspace()
            or tok.get("speaker") != words[-1].speaker
        )
        if starts_new:
            words.append(_WordAccumulator(tok))
        else:
            words[-1].absorb(tok)
    return [
        w
        for w in words
        if w.text and w.start_ms is not None and w.end_ms is not None
    ]


def _normalize(
    payload: dict, *, provider: str, job: Optional[dict] = None
) -> NormalizedTranscript:
    """Map a ``GET .../transcript`` body → NormalizedTranscript.
    Times: native ms → s; STRING speaker "1" → ``speaker_1``. The final
    job object contributes ``audio_duration_ms`` (the provider-billed
    duration) when available."""
    merged = _merge_tokens(payload.get("tokens") or [])

    words: list[NormalizedWord] = []
    speakers: list[str] = []
    for w in merged:
        speaker = f"speaker_{w.speaker}" if w.speaker not in (None, "") else None
        if speaker and speaker not in speakers:
            speakers.append(speaker)
        confidence = (
            round(sum(w.confidences) / len(w.confidences), 6)
            if w.confidences
            else None
        )
        words.append(
            NormalizedWord(
                text=w.text.strip(),
                start=float(w.start_ms) / 1000.0,
                end=float(w.end_ms) / 1000.0,
                confidence=confidence,
                speaker=speaker,
            )
        )

    duration = None
    duration_ms = (job or {}).get("audio_duration_ms")
    if isinstance(duration_ms, (int, float)) and duration_ms > 0:
        duration = float(duration_ms) / 1000.0
    elif words:
        duration = max(w.end for w in words)

    return NormalizedTranscript(
        provider=provider,
        language=None,  # Soniox reports language per token, not globally
        duration_seconds=duration,
        words=words,
        utterances=utterances_from_words(words),
        speakers_detected=speakers,
        raw=payload,
    )


__all__ = ["SonioxProvider"]
