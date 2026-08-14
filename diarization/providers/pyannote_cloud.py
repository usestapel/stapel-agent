"""pyannoteAI **cloud** diarization adapter (api.pyannote.ai).

The sibling of ``pyannote_http`` (a self-hosted ``pyannote.audio`` shim):
same normalized output, completely different wire flow — an async,
BILLED job API with its own media store.

Wire flow (three steps, verified contract of the iron-benchmark
``pyannote_diar_adapter`` port):

1. **Media** — only when the audio is not already a fetchable URL:
   ``POST {base}/media/input {"url": "media://<key>"}`` returns a
   presigned ``url``; the bytes go there with a plain ``PUT``. An
   ``AudioRef`` that already carries an http(s) URL skips this entirely
   (pyannoteAI fetches it server-side — no upload, no media object).
2. **Submit** — ``POST {base}/diarize`` with
   ``{"url", "model", "exclusive", "numSpeakers"?}`` → ``{"jobId"}``.
   **This call is the billing event.**
3. **Poll** — ``GET {base}/jobs/{jobId}`` until
   ``status in {"succeeded", "failed", "canceled"}``; the turns live in
   ``output.diarization`` as ``{speaker, start, end}`` seconds-float —
   the same shape ``turns_from_segments`` maps for the self-hosted shim.

Two pins this adapter is opinionated about (both overridable):

- ``model`` defaults to **precision-2** — the pyannoteAI flagship, and
  the model every measured hybrid-fusion number we have was produced on
  (the open-weights ladder 3.1 < community-1 < precision-2 is a
  different quality point; mixing them silently invalidates the
  comparison);
- ``exclusive`` defaults to **True** — pyannoteAI's non-overlapping
  layer. Overlap-aware merge policy belongs to the caller, and a caller
  that wants the raw overlapping layer sets
  ``provider_options={"exclusive": False}``; the untouched response is
  always in ``NormalizedDiarization.raw``.

**Billing invariant (ported):** jobs are billed per second of audio with
a 20-second minimum, and ONLY when the job succeeds — so this adapter
never retries a submit on its own. Transient failures surface as
``RetryableDiarizationError`` and the caller's retry policy (where the
spend cap lives) decides whether to pay again; :func:`billable_seconds`
is the pure helper for the host's cost model.

Settings: ``PYANNOTEAI_API_KEY`` (required — deliberately NOT the
self-host ``PYANNOTE_API_KEY``: the two are different credentials for
different services and a shared name silently sends one to the other),
``PYANNOTEAI_BASE_URL`` (default ``https://api.pyannote.ai/v1``),
``PYANNOTEAI_MODEL`` (default ``precision-2``),
``PYANNOTEAI_EXCLUSIVE`` (default True), ``DIARIZATION_TIMEOUT``.
"""
from __future__ import annotations

import logging
import math
import time
import uuid
from typing import Optional

import requests

from ...conf import agent_settings, pyannoteai_exclusive
from ..base import (
    DiarizationError,
    DiarizationProvider,
    NormalizedDiarization,
    RetryableDiarizationError,
    turns_from_segments,
    validate_speaker_counts,
)

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_S = 120  # per-request cap (the media PUT can be slow)
INITIAL_POLL_INTERVAL_S = 5.0
MAX_POLL_INTERVAL_S = 30.0
POLL_INTERVAL_GROWTH = 1.5

#: pyannoteAI bills per second of audio with this floor per job.
MIN_BILLED_SECONDS = 20


def billable_seconds(duration_seconds: Optional[float]) -> int:
    """Seconds a SUCCEEDED job is billed for: the audio duration rounded
    up, floored at :data:`MIN_BILLED_SECONDS`. Failed/canceled jobs are
    not billed at all — hosts call this only on success."""
    if not duration_seconds or duration_seconds <= 0:
        return MIN_BILLED_SECONDS
    return max(MIN_BILLED_SECONDS, int(math.ceil(float(duration_seconds))))


def _grow(interval: float) -> float:
    return min(interval * POLL_INTERVAL_GROWTH, MAX_POLL_INTERVAL_S)


class PyannoteCloudProvider(DiarizationProvider):
    name = "pyannote-cloud"

    def diarize(
        self,
        *,
        audio,
        num_speakers: Optional[int] = None,
        timeout_seconds: Optional[int] = None,
        provider_options: Optional[dict] = None,
    ) -> NormalizedDiarization:
        if not agent_settings.PYANNOTEAI_API_KEY:
            raise DiarizationError(
                "STAPEL_AGENT['PYANNOTEAI_API_KEY'] is not set", provider=self.name
            )
        timeout = (
            int(agent_settings.DIARIZATION_TIMEOUT)
            if timeout_seconds is None
            else int(timeout_seconds)
        )

        # Bound hints travel via provider_options; validate the knob
        # combination BEFORE any billable call (ported fail-loud rule).
        options = dict(provider_options or {})
        min_speakers = options.pop("min_speakers", None)
        max_speakers = options.pop("max_speakers", None)
        validate_speaker_counts(
            num_speakers=num_speakers,
            min_speakers=min_speakers,
            max_speakers=max_speakers,
            provider=self.name,
        )

        body: dict = {
            "url": self._media_url(audio, timeout=timeout),
            "model": agent_settings.PYANNOTEAI_MODEL,
            # Via the accessor, not bool(): the setting is env-readable and
            # bool("false") is True — see conf.pyannoteai_exclusive.
            "exclusive": pyannoteai_exclusive(),
        }
        if num_speakers is not None:
            body["numSpeakers"] = int(num_speakers)
        if min_speakers is not None:
            body["minSpeakers"] = int(min_speakers)
        if max_speakers is not None:
            body["maxSpeakers"] = int(max_speakers)
        if options:
            # The passthrough seam: remaining caller-pinned specifics win
            # over (are applied after) the adapter's own params.
            body.update(options)

        job_id = self._submit(body)
        payload = self._poll(job_id, timeout_seconds=timeout)
        return _normalize(payload, provider=self.name)

    # ── HTTP helpers ──────────────────────────────────────────

    def _base_url(self) -> str:
        return (agent_settings.PYANNOTEAI_BASE_URL or "").rstrip("/")

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {agent_settings.PYANNOTEAI_API_KEY}"}

    def _media_url(self, audio, *, timeout: int) -> str:
        """The URL the diarize job will read from: the caller's own http(s)
        URL when it has one, else a freshly uploaded pyannoteAI media
        object."""
        url = getattr(audio, "url", None)
        if url and str(url).lower().startswith(("http://", "https://")):
            return str(url)

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

        media_url = f"media://stapel-agent/{uuid.uuid4().hex}"
        created = self._request(
            "post", "/media/input", op="media", json={"url": media_url}
        )
        presigned = created.get("url")
        if not presigned:
            raise RetryableDiarizationError(
                f"pyannoteAI media/input returned no url: {str(created)[:200]}",
                provider=self.name,
            )
        try:
            resp = requests.put(
                presigned,
                data=payload,
                headers={"Content-Type": audio.mime or "application/octet-stream"},
                timeout=REQUEST_TIMEOUT_S,
            )
        except requests.RequestException as exc:
            raise RetryableDiarizationError(
                f"pyannoteAI media upload transport error: {exc}", provider=self.name
            ) from exc
        if resp.status_code >= 400:
            raise RetryableDiarizationError(
                f"pyannoteAI media upload {resp.status_code}: {resp.text[:200]}",
                provider=self.name,
                status_code=resp.status_code,
            )
        return media_url

    def _submit(self, body: dict) -> str:
        """The billing event — one call, never auto-retried here."""
        data = self._request("post", "/diarize", op="submit", json=body)
        job_id = data.get("jobId") or data.get("id")
        if not job_id:
            raise RetryableDiarizationError(
                f"pyannoteAI diarize returned no jobId: {str(data)[:200]}",
                provider=self.name,
            )
        return str(job_id)

    def _poll(self, job_id: str, *, timeout_seconds: int) -> dict:
        deadline = time.monotonic() + timeout_seconds
        interval = INITIAL_POLL_INTERVAL_S
        while True:
            if time.monotonic() >= deadline:
                # The job may still be running (and billed) server-side —
                # a caller re-driving the stage submits a NEW job, so
                # timeouts are a cost knob, not just a latency one.
                raise RetryableDiarizationError(
                    f"pyannoteAI polling exceeded {timeout_seconds}s for {job_id}",
                    provider=self.name,
                )
            time.sleep(interval)
            try:
                resp = requests.get(
                    f"{self._base_url()}/jobs/{job_id}",
                    headers=self._headers(),
                    timeout=REQUEST_TIMEOUT_S,
                )
            except requests.RequestException as exc:
                logger.debug("pyannoteAI poll error for %s: %s", job_id, exc)
                interval = _grow(interval)
                continue
            if resp.status_code >= 500 or resp.status_code == 429:
                interval = _grow(interval)
                continue
            if resp.status_code >= 400:
                raise DiarizationError(
                    f"pyannoteAI poll {resp.status_code}: {resp.text[:200]}",
                    provider=self.name,
                    status_code=resp.status_code,
                )
            try:
                payload = resp.json()
            except ValueError:
                interval = _grow(interval)
                continue

            status = str(payload.get("status") or "").lower()
            if status in ("succeeded", "success", "done", "completed"):
                return payload
            if status in ("failed", "error", "canceled", "cancelled"):
                raise DiarizationError(
                    f"pyannoteAI job {job_id} {status}: "
                    f"{str(payload.get('error') or payload.get('message') or '')[:200]}",
                    provider=self.name,
                )
            interval = _grow(interval)  # created / running

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
            raise RetryableDiarizationError(
                f"pyannoteAI {op} timed out: {exc}", provider=self.name
            ) from exc
        except requests.RequestException as exc:
            raise RetryableDiarizationError(
                f"pyannoteAI {op} transport error: {exc}", provider=self.name
            ) from exc
        if resp.status_code == 429:
            raise RetryableDiarizationError(
                f"pyannoteAI {op} rate-limited", provider=self.name, status_code=429
            )
        if resp.status_code >= 500:
            raise RetryableDiarizationError(
                f"pyannoteAI {op} {resp.status_code}: {resp.text[:200]}",
                provider=self.name,
                status_code=resp.status_code,
            )
        if resp.status_code >= 400:
            raise DiarizationError(
                f"pyannoteAI {op} {resp.status_code}: {resp.text[:200]}",
                provider=self.name,
                status_code=resp.status_code,
            )
        try:
            return resp.json()
        except ValueError as exc:
            raise RetryableDiarizationError(
                f"pyannoteAI {op} non-JSON: {resp.text[:200]}", provider=self.name
            ) from exc


def _normalize(payload: dict, *, provider: str) -> NormalizedDiarization:
    """Map a succeeded job body → NormalizedDiarization."""
    output = payload.get("output") or {}
    segments = output.get("diarization")
    if not isinstance(segments, list):
        # Ported rule: a malformed success fails loudly, never silently
        # becomes an empty diarization.
        raise DiarizationError(
            f"pyannoteAI job output has no 'diarization' list: {str(payload)[:300]}",
            provider=provider,
        )
    try:
        turns = turns_from_segments(segments)
    except (KeyError, TypeError, ValueError) as exc:
        raise DiarizationError(
            f"pyannoteAI job segment malformed: {exc}", provider=provider
        ) from exc

    duration = output.get("duration") or payload.get("duration")
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


__all__ = ["PyannoteCloudProvider", "billable_seconds", "MIN_BILLED_SECONDS"]
