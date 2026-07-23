"""Speechmatics batch adapter (melia-1 / standard / enhanced).

Three-step flow (verified against the official docs via the
iron-benchmark port, 2026-07-10):

1. ``POST /v2/jobs/`` — ONE multipart submit: ``data_file`` (binary) +
   ``config`` (a JSON string form field) → ``{id}``. Upload-capable:
   any AudioRef kind via ``read_bytes()``;
2. ``GET /v2/jobs/{id}`` — poll ``job.status`` until ``done``
   (``rejected`` / ``deleted`` / ``expired`` are terminal-fatal);
3. ``GET /v2/jobs/{id}/transcript`` — the JSON transcript (a FLAT
   ``results[]`` stream of word/punctuation items; no provider
   utterances).

Auth: ``Authorization: Bearer <key>``. The ``model`` field is always
sent explicitly (omission = "standard" TODAY; ``operating_point`` is
deprecated and never sent). Language rules (ported invariants):

- melia-1 accepts ONLY ``language="multi"`` (``auto`` is an API error) —
  a concrete requested language becomes a ``language_hints`` entry;
- standard/enhanced need ONE explicit language pack code; no language is
  rejected here as fatal BEFORE any billable call (the auto-LID path is
  documented but deliberately not wired).

Speakers come back as STRING labels "S1", "S2", ... or "UU"
(unidentified → None here). Utterances are DERIVED: maximal same-speaker
word runs, additionally split after an end-of-sentence punctuation mark
(``is_eos`` — provider-tagged, not a heuristic). Punctuation is glued
into utterance text but never becomes a word cell.

No vocabulary biasing is wired: Speechmatics' ``additional_vocab`` is
not covered by the verified sources this port is pinned to — requested
``keyterms`` are reported as not applied via the ``biasing`` block; a
host can send it through ``provider_options`` (merged into the
``transcription_config`` as-is).

Settings: ``SPEECHMATICS_API_KEY`` (required), ``SPEECHMATICS_BASE_URL``
(region-pinned: melia-1 exists in EU1/US1 only), ``SPEECHMATICS_MODEL``
(default ``melia-1``).
"""
from __future__ import annotations

import json
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

#: Provider label meaning "speaker could not be identified" → None here.
_UNIDENTIFIED = "UU"

_TERMINAL_FAILURES = frozenset({"rejected", "deleted", "expired"})


class SpeechmaticsProvider(SttProvider):
    name = "speechmatics"
    supports_diarization = True
    supports_keyterms = False  # see module docstring
    cost_per_hour = 0.30  # enhanced-tier ballpark — verify before billing

    def default_speech_model(self) -> Optional[str]:
        return agent_settings.SPEECHMATICS_MODEL

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
        api_key = agent_settings.SPEECHMATICS_API_KEY
        if not api_key:
            raise TranscriptionError(
                "STAPEL_AGENT['SPEECHMATICS_API_KEY'] is not set",
                provider=self.name,
            )
        timeout = (
            int(agent_settings.STT_TIMEOUT)
            if timeout_seconds is None
            else int(timeout_seconds)
        )
        payload = audio.read_bytes(provider=self.name, timeout=min(timeout, 600))

        model = self.effective_model()
        lang = normalize_language(language)
        tc: dict = {
            # Explicit model — omission means "standard" TODAY, but an
            # unpinned run can silently change under us.
            "model": model,
            "diarization": "speaker" if diarization else "none",
        }
        if model == "melia-1":
            # melia-1 is always multilingual: the wire language is
            # "multi"; a concrete requested language becomes a hint.
            tc["language"] = "multi"
            if lang and lang != "multi":
                tc["language_hints"] = [lang]
        else:
            if not lang or lang in ("auto", "multi"):
                raise TranscriptionError(
                    f"Speechmatics model {model!r} needs one explicit "
                    "language pack code (auto-LID is not wired — use the "
                    "melia-1 model for multilingual audio)",
                    provider=self.name,
                )
            tc["language"] = lang
        if provider_options:
            # The passthrough seam: applied AFTER the adapter's own params
            # so a caller can pin provider specifics (e.g.
            # ``additional_vocab``) without a core release.
            tc.update(provider_options)

        config = {"type": "transcription", "transcription_config": tc}
        job_id = self._submit(payload, config, mime=audio.mime)
        self._poll(job_id, timeout_seconds=timeout)
        transcript_payload = self._fetch_transcript(job_id)
        transcript = _normalize(transcript_payload, provider=self.name)
        transcript.biasing = unsupported_biasing(keyterms)
        return transcript

    # ── HTTP helpers ──────────────────────────────────────────

    def _base_url(self) -> str:
        return (agent_settings.SPEECHMATICS_BASE_URL or "").rstrip("/")

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {agent_settings.SPEECHMATICS_API_KEY}"}

    def _submit(self, payload: bytes, config: dict, *, mime: Optional[str]) -> str:
        try:
            resp = requests.post(
                f"{self._base_url()}/v2/jobs/",
                headers=self._headers(),
                files={
                    "data_file": ("audio", payload, mime or "application/octet-stream")
                },
                data={"config": json.dumps(config)},
                timeout=REQUEST_TIMEOUT_S,
            )
        except requests.Timeout as exc:
            raise RetryableTranscriptionError(
                f"Speechmatics submit timed out: {exc}", provider=self.name
            ) from exc
        except requests.RequestException as exc:
            raise RetryableTranscriptionError(
                f"Speechmatics submit transport error: {exc}", provider=self.name
            ) from exc
        self._raise_for_status(resp, op="submit")
        try:
            data = resp.json()
        except ValueError as exc:
            raise RetryableTranscriptionError(
                f"Speechmatics submit non-JSON: {resp.text[:200]}",
                provider=self.name,
            ) from exc
        job_id = data.get("id")
        if not job_id:
            raise RetryableTranscriptionError(
                f"Speechmatics submit lacked id: {data}", provider=self.name
            )
        return job_id

    def _poll(self, job_id: str, *, timeout_seconds: int) -> dict:
        deadline = time.monotonic() + timeout_seconds
        interval = INITIAL_POLL_INTERVAL_S

        while True:
            if time.monotonic() >= deadline:
                raise RetryableTranscriptionError(
                    f"Speechmatics polling exceeded {timeout_seconds}s "
                    f"for {job_id}",
                    provider=self.name,
                )

            time.sleep(interval)

            try:
                resp = requests.get(
                    f"{self._base_url()}/v2/jobs/{job_id}",
                    headers=self._headers(),
                    timeout=REQUEST_TIMEOUT_S,
                )
            except requests.RequestException as exc:
                # A single poll hiccup is transient — keep polling.
                logger.debug("Speechmatics poll error for %s: %s", job_id, exc)
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

            job = payload.get("job") or {}
            status = job.get("status")
            if status == "done":
                return job
            if status in _TERMINAL_FAILURES:
                errors = job.get("errors") or []
                detail = (
                    "; ".join(
                        str(e.get("message", e))
                        for e in errors[:3]
                        if isinstance(e, (dict, str))
                    )
                    or "no error detail"
                )
                raise TranscriptionError(
                    f"Speechmatics job ended as {status!r}: {detail}",
                    provider=self.name,
                )
            # running / accepted — keep polling
            interval = _grow(interval)

    def _fetch_transcript(self, job_id: str) -> dict:
        try:
            resp = requests.get(
                f"{self._base_url()}/v2/jobs/{job_id}/transcript",
                headers=self._headers(),
                timeout=REQUEST_TIMEOUT_S,
            )
        except requests.Timeout as exc:
            raise RetryableTranscriptionError(
                f"Speechmatics transcript fetch timed out: {exc}",
                provider=self.name,
            ) from exc
        except requests.RequestException as exc:
            raise RetryableTranscriptionError(
                f"Speechmatics transcript fetch transport error: {exc}",
                provider=self.name,
            ) from exc
        self._raise_for_status(resp, op="transcript fetch")
        try:
            return resp.json()
        except ValueError as exc:
            raise RetryableTranscriptionError(
                f"Speechmatics transcript non-JSON: {resp.text[:200]}",
                provider=self.name,
            ) from exc

    def _raise_for_status(self, resp, *, op: str) -> None:
        if 200 <= resp.status_code < 300:
            return
        if resp.status_code == 429:
            raise RetryableTranscriptionError(
                "Speechmatics rate-limited", provider=self.name, status_code=429
            )
        if resp.status_code >= 500:
            raise RetryableTranscriptionError(
                f"Speechmatics {op} {resp.status_code}: {resp.text[:300]}",
                provider=self.name,
                status_code=resp.status_code,
            )
        raise TranscriptionError(
            f"Speechmatics {op} {resp.status_code}: {resp.text[:300]}",
            provider=self.name,
            status_code=resp.status_code,
        )


def _grow(interval: float) -> float:
    return min(interval * POLL_INTERVAL_GROWTH, MAX_POLL_INTERVAL_S)


def _speaker_of(alt: dict) -> Optional[str]:
    """The attributed speaker of one alternative ("UU" → None)."""
    speaker = alt.get("speaker")
    if not speaker or speaker == _UNIDENTIFIED:
        return None
    return str(speaker)


def _normalize(payload: dict, *, provider: str) -> NormalizedTranscript:
    """Map a flat ``results[]`` transcript → NormalizedTranscript (times
    are seconds). Utterances = same-speaker runs split at ``is_eos``."""
    results = payload.get("results") or []

    words: list[NormalizedWord] = []
    utterances: list[NormalizedUtterance] = []
    speakers: list[str] = []

    cur_parts: list[str] = []
    cur_indexes: list[int] = []
    cur_speaker: Optional[str] = None
    cur_start: Optional[float] = None
    cur_end: Optional[float] = None
    close_after_eos = False

    def flush():
        nonlocal cur_parts, cur_indexes, cur_start, cur_end
        if cur_parts and cur_start is not None and cur_end is not None:
            utterances.append(
                NormalizedUtterance(
                    text=" ".join(cur_parts).strip(),
                    start=cur_start,
                    end=cur_end,
                    speaker=cur_speaker,
                    word_indexes=list(cur_indexes),
                )
            )
        cur_parts, cur_indexes, cur_start, cur_end = [], [], None, None

    for item in results if isinstance(results, list) else []:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        alts = item.get("alternatives") or []
        alt = alts[0] if alts and isinstance(alts[0], dict) else None
        if alt is None:
            continue
        content = (alt.get("content") or "").strip()

        if item_type == "punctuation":
            if cur_parts and content:
                if item.get("attaches_to") in ("previous", "both", None):
                    # ". , ?" glue onto the preceding word, never float
                    cur_parts[-1] = cur_parts[-1] + content
                else:
                    cur_parts.append(content)
                end = item.get("end_time")
                if end is not None and cur_end is not None and float(end) > cur_end:
                    cur_end = float(end)
                if item.get("is_eos"):
                    close_after_eos = True  # split at the sentence boundary
            continue

        if item_type not in ("word", "entity"):
            continue  # unrecognized types may appear — ignored per schema
        start = item.get("start_time")
        end = item.get("end_time")
        if start is None or end is None or not content:
            continue
        start, end = float(start), float(end)

        speaker = _speaker_of(alt)
        if speaker and speaker not in speakers:
            speakers.append(speaker)

        if not cur_parts or speaker != cur_speaker or close_after_eos:
            flush()
            cur_speaker = speaker
            cur_start = start
            close_after_eos = False
        cur_parts.append(content)
        cur_indexes.append(len(words))
        cur_end = end if cur_end is None else max(cur_end, end)

        words.append(
            NormalizedWord(
                text=content,
                start=start,
                end=end,
                confidence=alt.get("confidence"),
                speaker=speaker,
            )
        )
    flush()

    # job.duration is whole seconds; fall back to the furthest word end.
    duration = (payload.get("job") or {}).get("duration")
    try:
        duration = float(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration = None
    if duration is None and words:
        duration = max(w.end for w in words)

    # metadata.transcription_config is the provider's ECHO of the config;
    # "multi" (melia-1) carries no single detected language → None.
    config_echo = (payload.get("metadata") or {}).get("transcription_config") or {}
    language = config_echo.get("language")
    if language == "multi":
        language = None

    return NormalizedTranscript(
        provider=provider,
        language=language,
        duration_seconds=duration,
        words=words,
        utterances=utterances,
        speakers_detected=speakers,
        raw=payload,
    )


__all__ = ["SpeechmaticsProvider"]
