"""AssemblyAI adapter.

Unlike Scribe (synchronous), AssemblyAI is async:

1. ``POST /v2/transcript`` with ``audio_url`` (AssemblyAI fetches it
   server-side — no upload, so a URL ref is REQUIRED) → ``{id, status}``;
2. poll ``GET /v2/transcript/{id}`` with growing intervals until
   ``completed`` / ``error`` / the deadline.

Settings: ``ASSEMBLYAI_API_KEY`` (required), ``ASSEMBLYAI_BASE_URL``,
``ASSEMBLYAI_MODEL`` (``speech_model``; default ``universal`` — 99
languages; set ``best`` for the tier-1 pro model).

Vocabulary biasing (``keyterms``): the CURRENT parameter is
``keyterms_prompt`` (JSON body, list of strings). Documented limits
(official docs survey 2026-07-18, universal-3-5-pro): <=1000
words/phrases per request where EACH WORD of a phrase counts toward the
1000, and <=6 words per phrase. The legacy ``word_boost``/``boost_param``
pair is GONE from current docs (404) — never sent. Out-of-limit terms
are TRUNCATED (counted in ``NormalizedTranscript.biasing``), never
errors.

**Language coverage is NOT uniform** (docs survey 2026-07-24) and this is
the adapter's one opinionated gate: ``keyterms_prompt`` is honored by
universal-3.5-pro (the ``best`` alias) for ITS OWN native languages
(en/es/de/fr/pt/it) — other languages fall back internally to
Universal-2, where the parameter is Beta and **English-only**. Sending
terms outside that coverage is the worst failure mode available here:
the request succeeds, the provider ignores the terms, and the biasing
block would still say ``applied: true`` — silent non-biasing that no
downstream invariant can catch. So when the requested language is
outside the effective model's coverage the parameter is NOT sent and
the block honestly reports ``applied: false`` with every term counted as
truncated (``KEYTERMS_LANGUAGES``; a host that knows better overrides it
per-registration, and ``provider_options`` can still force the raw
parameter). With no language pinned (auto-detect) coverage is unknowable
before the call, so the terms ARE sent — pin the language when you need
the guarantee.
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
    biasing_metadata,
    unsupported_biasing,
)

logger = logging.getLogger(__name__)

SUBMIT_TIMEOUT_S = 60

# Documented keyterms_prompt limits (docs survey 2026-07-18): out-of-limit
# terms are truncated with counts in `biasing`, never raised.
MAX_KEYTERM_PHRASE_WORDS = 6
MAX_KEYTERM_TOTAL_WORDS = 1000  # each word of a phrase counts


#: Documented ``keyterms_prompt`` language coverage per speech model
#: (docs survey 2026-07-24). Keys are AssemblyAI ``speech_model`` values
#: (incl. the ``best`` alias), values are ISO-639-1 primary subtags. A
#: model absent from this map is treated as UNKNOWN coverage — terms are
#: sent (the pre-gate behaviour), because refusing to bias on an
#: unrecognized model name would be its own silent failure.
KEYTERMS_LANGUAGES = {
    # universal-3.5-pro and its 'best' alias: the model's native six.
    "best": frozenset({"en", "es", "de", "fr", "pt", "it"}),
    "universal-3-5-pro": frozenset({"en", "es", "de", "fr", "pt", "it"}),
    "universal-3.5-pro": frozenset({"en", "es", "de", "fr", "pt", "it"}),
    # Universal-2: keyterms_prompt is Beta, English-only.
    "universal": frozenset({"en"}),
    "universal-2": frozenset({"en"}),
}


def _filter_keyterms(keyterms: list[str]) -> tuple[list[str], int]:
    """(accepted, truncated_count) under the documented limits."""
    accepted: list[str] = []
    truncated = 0
    total_words = 0
    for term in keyterms:
        stripped = term.strip()
        words = len(stripped.split())
        if (
            not stripped
            or words > MAX_KEYTERM_PHRASE_WORDS
            or total_words + words > MAX_KEYTERM_TOTAL_WORDS
        ):
            truncated += 1
            continue
        accepted.append(stripped)
        total_words += words
    return accepted, truncated
POLL_TIMEOUT_S = 30
INITIAL_POLL_INTERVAL_S = 5.0
MAX_POLL_INTERVAL_S = 30.0
POLL_INTERVAL_GROWTH = 1.5


class AssemblyAIProvider(SttProvider):
    name = "assemblyai"
    supports_diarization = True
    supports_keyterms = True
    cost_per_hour = 0.37  # 'best' tier list price; 'universal' is cheaper

    def default_speech_model(self) -> Optional[str]:
        return agent_settings.ASSEMBLYAI_MODEL

    def keyterms_supported_for(self, language: Optional[str]) -> bool:
        """Does THIS registration's model honor ``keyterms_prompt`` for
        *language*? Unknown model or no language pinned → True (send;
        see the module docstring). Override per-registration when a host
        has better information than :data:`KEYTERMS_LANGUAGES`."""
        covered = KEYTERMS_LANGUAGES.get(str(self.effective_model() or "").lower())
        if covered is None or not language:
            return True
        primary = language.strip().lower().replace("_", "-").split("-")[0]
        return primary in covered

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
        api_key = agent_settings.ASSEMBLYAI_API_KEY
        if not api_key:
            raise TranscriptionError(
                "STAPEL_AGENT['ASSEMBLYAI_API_KEY'] is not set", provider=self.name
            )
        audio_url = audio.require_url(provider=self.name)
        timeout = (
            int(agent_settings.STT_TIMEOUT)
            if timeout_seconds is None
            else int(timeout_seconds)
        )

        body = {
            "audio_url": audio_url,
            "speech_model": self.effective_model(),
            "speaker_labels": bool(diarization),
            "punctuate": True,
            "format_text": True,
        }
        if language:
            # AssemblyAI accepts ``en`` / ``en_us`` style — normalize separators.
            body["language_code"] = language.lower().replace("-", "_")
        else:
            body["language_detection"] = True

        biasing = None
        if keyterms and not self.keyterms_supported_for(language):
            # Outside the model's documented keyterms coverage: the
            # provider would accept the request and ignore the terms, so
            # report the non-application honestly instead of claiming
            # applied=true (see the module docstring).
            logger.info(
                "assemblyai: keyterms not applied — model %r does not honor "
                "keyterms_prompt for language %r",
                self.effective_model(), language,
            )
            biasing = unsupported_biasing(keyterms)
        elif keyterms:
            accepted, truncated = _filter_keyterms(keyterms)
            if accepted:
                body["keyterms_prompt"] = accepted
            biasing = biasing_metadata(
                applied=bool(accepted),
                terms_sent=len(accepted),
                terms_truncated=truncated,
            )
        if provider_options:
            # The passthrough seam: applied AFTER the adapter's own params
            # so a caller can pin provider specifics without a core release.
            body.update(provider_options)

        transcript_id = self._submit(body)
        payload = self._poll(transcript_id, timeout_seconds=timeout)
        transcript = _normalize(payload, provider=self.name)
        transcript.biasing = biasing
        return transcript

    # ── HTTP helpers ──────────────────────────────────────────

    def _base_url(self) -> str:
        return (agent_settings.ASSEMBLYAI_BASE_URL or "").rstrip("/")

    def _headers(self) -> dict:
        return {
            "Authorization": agent_settings.ASSEMBLYAI_API_KEY,
            "Content-Type": "application/json",
        }

    def _submit(self, body: dict) -> str:
        try:
            resp = requests.post(
                f"{self._base_url()}/v2/transcript",
                json=body,
                headers=self._headers(),
                timeout=SUBMIT_TIMEOUT_S,
            )
        except requests.Timeout as exc:
            raise RetryableTranscriptionError(
                f"AssemblyAI submit timed out: {exc}", provider=self.name
            ) from exc
        except requests.RequestException as exc:
            raise RetryableTranscriptionError(
                f"AssemblyAI submit transport error: {exc}", provider=self.name
            ) from exc

        self._raise_for_status(resp, op="submit")
        try:
            data = resp.json()
        except ValueError as exc:
            raise RetryableTranscriptionError(
                f"AssemblyAI submit non-JSON: {resp.text[:200]}", provider=self.name
            ) from exc
        transcript_id = data.get("id")
        if not transcript_id:
            raise RetryableTranscriptionError(
                f"AssemblyAI submit lacked id: {data}", provider=self.name
            )
        return transcript_id

    def _poll(self, transcript_id: str, *, timeout_seconds: int) -> dict:
        deadline = time.monotonic() + timeout_seconds
        interval = INITIAL_POLL_INTERVAL_S

        while True:
            if time.monotonic() >= deadline:
                raise RetryableTranscriptionError(
                    f"AssemblyAI polling exceeded {timeout_seconds}s "
                    f"for {transcript_id}",
                    provider=self.name,
                )

            time.sleep(interval)

            try:
                resp = requests.get(
                    f"{self._base_url()}/v2/transcript/{transcript_id}",
                    headers=self._headers(),
                    timeout=POLL_TIMEOUT_S,
                )
            except requests.RequestException as exc:
                # A single poll hiccup is transient — keep polling.
                logger.debug("AssemblyAI poll error for %s: %s", transcript_id, exc)
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
            if status == "error":
                raise TranscriptionError(
                    f"AssemblyAI job error: {payload.get('error') or 'unknown'}",
                    provider=self.name,
                )
            # queued / processing — keep polling
            interval = _grow(interval)

    def _raise_for_status(self, resp, *, op: str) -> None:
        if 200 <= resp.status_code < 300:
            return
        if resp.status_code == 429:
            raise RetryableTranscriptionError(
                "AssemblyAI rate-limited", provider=self.name, status_code=429
            )
        if resp.status_code >= 500:
            raise RetryableTranscriptionError(
                f"AssemblyAI {op} {resp.status_code}: {resp.text[:300]}",
                provider=self.name,
                status_code=resp.status_code,
            )
        # 4xx (auth, bad params) — fatal: fall through to the next provider
        # in the CHAIN is wrong here, the request itself is bad.
        raise TranscriptionError(
            f"AssemblyAI {op} {resp.status_code}: {resp.text[:300]}",
            provider=self.name,
            status_code=resp.status_code,
        )


def _grow(interval: float) -> float:
    return min(interval * POLL_INTERVAL_GROWTH, MAX_POLL_INTERVAL_S)


def _normalize(payload: dict, *, provider: str) -> NormalizedTranscript:
    """Map an AssemblyAI response → NormalizedTranscript. Times: ms → s."""
    words: list[NormalizedWord] = []
    speakers: list[str] = []

    for w in payload.get("words") or []:
        text = w.get("text", "")
        if not text:
            continue
        speaker = w.get("speaker")
        if speaker and speaker not in speakers:
            speakers.append(speaker)
        words.append(
            NormalizedWord(
                text=text,
                start=float(w.get("start", 0)) / 1000.0,
                end=float(w.get("end", 0)) / 1000.0,
                confidence=w.get("confidence"),
                speaker=speaker,
            )
        )

    utterances: list[NormalizedUtterance] = []
    for u in payload.get("utterances") or []:
        text = u.get("text", "")
        if not text:
            continue
        speaker = u.get("speaker")
        if speaker and speaker not in speakers:
            speakers.append(speaker)
        utterances.append(
            NormalizedUtterance(
                text=text,
                start=float(u.get("start", 0)) / 1000.0,
                end=float(u.get("end", 0)) / 1000.0,
                speaker=speaker,
                confidence=u.get("confidence"),
            )
        )

    duration_seconds: Optional[float] = None
    if payload.get("audio_duration") is not None:
        # AssemblyAI returns audio_duration as integer seconds.
        try:
            duration_seconds = float(payload["audio_duration"])
        except (TypeError, ValueError):
            duration_seconds = None
    if duration_seconds is None and words:
        duration_seconds = max(w.end for w in words)

    return NormalizedTranscript(
        provider=provider,
        language=payload.get("language_code"),
        duration_seconds=duration_seconds,
        words=words,
        utterances=utterances,
        speakers_detected=speakers,
        raw=payload,
    )


__all__ = ["AssemblyAIProvider"]
