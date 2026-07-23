"""Gladia adapter (solaria-1 by default).

Gladia is ASYNC with a SEPARATE multipart upload step (verified against
the official docs via the iron-benchmark port, 2026-07-09):

1. ``POST /v2/upload`` — multipart field ``audio`` (binary) →
   ``{audio_url, ...}``. Upload-capable: any AudioRef kind is
   materialized via ``read_bytes()`` and uploaded (the verified path —
   the create endpoint is always fed a Gladia-hosted audio_url);
2. ``POST /v2/pre-recorded`` — JSON ``{"audio_url": ..., params}`` →
   ``{id, ...}``. Each successful create is a BILLING event;
3. ``GET /v2/pre-recorded/{id}`` — poll until ``status == "done"``
   (``error`` is terminal-fatal).

Auth: header ``x-gladia-key: <raw key>``. The ``model`` param is always
sent explicitly (omission = solaria-1 TODAY, but an unpinned run can
silently change server-side — the AssemblyAI 'best'-alias lesson).
Diarization is a plain ``diarization`` bool (included in the async
price). Response times are SECONDS; the utterance ``speaker`` is an
INTEGER by order of appearance (0 is valid and falsy); words carry no
speaker of their own and inherit their utterance's.

No vocabulary biasing is wired: no Gladia biasing parameter is covered
by the verified sources this port is pinned to — requested ``keyterms``
are reported as not applied via the ``biasing`` block, and a host that
knows the current Gladia vocabulary config can send it through
``provider_options`` (merged into the create body as-is).

Settings: ``GLADIA_API_KEY`` (required), ``GLADIA_BASE_URL``,
``GLADIA_MODEL`` (default ``solaria-1``).
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
    NormalizedUtterance,
    NormalizedWord,
    RetryableTranscriptionError,
    SttProvider,
    TranscriptionError,
    normalize_language,
    unsupported_biasing,
)

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_S = 120  # per-request cap (the upload can be slow)
INITIAL_POLL_INTERVAL_S = 5.0
MAX_POLL_INTERVAL_S = 30.0
POLL_INTERVAL_GROWTH = 1.5


class GladiaProvider(SttProvider):
    name = "gladia"
    supports_diarization = True
    supports_keyterms = False  # see module docstring
    cost_per_hour = 0.612  # async list price $0.0102/min — verify before billing

    def default_speech_model(self) -> Optional[str]:
        return agent_settings.GLADIA_MODEL

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
        api_key = agent_settings.GLADIA_API_KEY
        if not api_key:
            raise TranscriptionError(
                "STAPEL_AGENT['GLADIA_API_KEY'] is not set", provider=self.name
            )
        timeout = (
            int(agent_settings.STT_TIMEOUT)
            if timeout_seconds is None
            else int(timeout_seconds)
        )
        payload = audio.read_bytes(provider=self.name, timeout=min(timeout, 600))

        body: dict = {
            # Explicit model — omission means solaria-1 TODAY, but an
            # unpinned run can silently change under us.
            "model": self.effective_model(),
            "diarization": bool(diarization),
        }
        if language:
            body["language_config"] = {"languages": [normalize_language(language)]}
        if provider_options:
            # The passthrough seam: applied AFTER the adapter's own params
            # so a caller can pin provider specifics (e.g. a vocabulary
            # config) without a core release.
            body.update(provider_options)

        audio_url = self._upload(payload, mime=audio.mime)
        job_id = self._create({"audio_url": audio_url, **body})
        done = self._poll(job_id, timeout_seconds=timeout)
        transcript = _normalize(done, provider=self.name)
        transcript.biasing = unsupported_biasing(keyterms)
        return transcript

    # ── HTTP helpers ──────────────────────────────────────────

    def _base_url(self) -> str:
        return (agent_settings.GLADIA_BASE_URL or "").rstrip("/")

    def _headers(self) -> dict:
        return {"x-gladia-key": agent_settings.GLADIA_API_KEY}

    def _post(self, path: str, *, op: str, **kwargs) -> dict:
        try:
            resp = requests.post(
                f"{self._base_url()}{path}",
                headers=self._headers(),
                timeout=REQUEST_TIMEOUT_S,
                **kwargs,
            )
        except requests.Timeout as exc:
            raise RetryableTranscriptionError(
                f"Gladia {op} timed out: {exc}", provider=self.name
            ) from exc
        except requests.RequestException as exc:
            raise RetryableTranscriptionError(
                f"Gladia {op} transport error: {exc}", provider=self.name
            ) from exc
        self._raise_for_status(resp, op=op)
        try:
            return resp.json()
        except ValueError as exc:
            raise RetryableTranscriptionError(
                f"Gladia {op} non-JSON: {resp.text[:200]}", provider=self.name
            ) from exc

    def _upload(self, payload: bytes, *, mime: Optional[str]) -> str:
        data = self._post(
            "/v2/upload",
            op="upload",
            files={"audio": ("audio", payload, mime or "application/octet-stream")},
        )
        audio_url = data.get("audio_url")
        if not audio_url:
            raise RetryableTranscriptionError(
                f"Gladia upload returned no audio_url: {data}", provider=self.name
            )
        return audio_url

    def _create(self, body: dict) -> str:
        data = self._post("/v2/pre-recorded", op="create", json=body)
        job_id = data.get("id")
        if not job_id:
            raise RetryableTranscriptionError(
                f"Gladia create returned no job id: {data}", provider=self.name
            )
        return job_id

    def _poll(self, job_id: str, *, timeout_seconds: int) -> dict:
        deadline = time.monotonic() + timeout_seconds
        interval = INITIAL_POLL_INTERVAL_S

        while True:
            if time.monotonic() >= deadline:
                raise RetryableTranscriptionError(
                    f"Gladia polling exceeded {timeout_seconds}s for {job_id}",
                    provider=self.name,
                )

            time.sleep(interval)

            try:
                resp = requests.get(
                    f"{self._base_url()}/v2/pre-recorded/{job_id}",
                    headers=self._headers(),
                    timeout=REQUEST_TIMEOUT_S,
                )
            except requests.RequestException as exc:
                # A single poll hiccup is transient — keep polling.
                logger.debug("Gladia poll error for %s: %s", job_id, exc)
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
            if status == "done":
                return payload
            if status == "error":
                raise TranscriptionError(
                    f"Gladia job error: {payload.get('error_code') or 'unknown'}",
                    provider=self.name,
                )
            # queued / processing — keep polling
            interval = _grow(interval)

    def _raise_for_status(self, resp, *, op: str) -> None:
        if 200 <= resp.status_code < 300:
            return
        if resp.status_code == 429:
            raise RetryableTranscriptionError(
                "Gladia rate-limited", provider=self.name, status_code=429
            )
        if resp.status_code >= 500:
            raise RetryableTranscriptionError(
                f"Gladia {op} {resp.status_code}: {resp.text[:300]}",
                provider=self.name,
                status_code=resp.status_code,
            )
        raise TranscriptionError(
            f"Gladia {op} {resp.status_code}: {resp.text[:300]}",
            provider=self.name,
            status_code=resp.status_code,
        )


def _grow(interval: float) -> float:
    return min(interval * POLL_INTERVAL_GROWTH, MAX_POLL_INTERVAL_S)


def _speaker_label(value) -> Optional[str]:
    """Gladia speakers are INTEGERS by order of appearance (0 is valid
    and falsy — always test ``is not None``) → ``speaker_{n}``."""
    if value is None:
        return None
    return f"speaker_{value}"


def _normalize(payload: dict, *, provider: str) -> NormalizedTranscript:
    """Map a done ``GET /v2/pre-recorded/{id}`` payload (times are
    seconds). Words inherit their utterance's speaker — Gladia words
    carry none of their own."""
    result = payload.get("result") or {}
    tr = result.get("transcription") or {}
    utterances_in = tr.get("utterances") or []

    words: list[NormalizedWord] = []
    utterances: list[NormalizedUtterance] = []
    speakers: list[str] = []

    for utt in utterances_in:
        if not isinstance(utt, dict):
            continue
        speaker = _speaker_label(utt.get("speaker"))
        if speaker and speaker not in speakers:
            speakers.append(speaker)
        word_indexes: list[int] = []
        for tok in utt.get("words") or []:
            if not isinstance(tok, dict):
                continue
            text = (tok.get("word") or "").strip()
            if not text or tok.get("start") is None or tok.get("end") is None:
                continue
            word_indexes.append(len(words))
            words.append(
                NormalizedWord(
                    text=text,
                    start=float(tok["start"]),
                    end=float(tok["end"]),
                    confidence=tok.get("confidence"),
                    speaker=speaker,
                )
            )
        text = (utt.get("text") or "").strip()
        if not text or utt.get("start") is None or utt.get("end") is None:
            continue
        utterances.append(
            NormalizedUtterance(
                text=text,
                start=float(utt["start"]),
                end=float(utt["end"]),
                speaker=speaker,
                confidence=utt.get("confidence"),
                word_indexes=word_indexes,
            )
        )

    duration = (result.get("metadata") or {}).get("audio_duration")
    if duration is None:
        duration = (payload.get("file") or {}).get("audio_duration")
    try:
        duration = float(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration = None
    if duration is None and words:
        duration = max(w.end for w in words)

    languages = tr.get("languages") or []

    return NormalizedTranscript(
        provider=provider,
        language=languages[0] if languages else None,
        duration_seconds=duration,
        words=words,
        utterances=utterances,
        speakers_detected=speakers,
        raw=payload,
    )


__all__ = ["GladiaProvider"]
