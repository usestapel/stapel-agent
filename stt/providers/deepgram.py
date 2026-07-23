"""Deepgram Nova-3 adapter.

Synchronous pre-recorded endpoint — ONE ``POST {base}/v1/listen`` with
the raw audio bytes as the request body (NOT multipart, NOT JSON).
Upload-capable: accepts any AudioRef kind (url is downloaded first, path
is read, bytes go straight in).

Endpoint + params (verified against the primary Deepgram docs via the
iron-benchmark port, 2026-07-04 / 2026-07-18):

- auth header ``Authorization: Token <key>`` (NOT Bearer);
- ``diarize_model=latest`` is a SINGLE query param that BOTH enables
  diarization AND selects the batch diarizer version. The boolean
  ``diarize`` is deprecated (routes to the v1 diarizer) and a request
  setting both is rejected — this adapter never sends ``diarize``, and
  omits ``diarize_model`` entirely when diarization is off;
- ``smart_format=true`` enables formatting + punctuation
  (``punctuated_word`` on every word; no separate ``punctuate`` needed);
- ``utterances=true`` returns ``results.utterances[]`` (semantic turns).

Vocabulary biasing (``keyterms``) — Nova-3 keyterm prompting (verified
2026-07-18, live-proven): ``keyterm`` is a REPEATED query param
(``?keyterm=A&keyterm=B``), never comma-joined; plain terms only — the
``term:weight`` intensifier syntax belongs to the LEGACY ``keywords``
API of nova-2/1/enhanced/base and must never reach nova-3 (it would bias
a literal ":2"); hard limit ~500 TOKENS across all keyterms. Deepgram
does not publish its tokenizer, so the cap is enforced on an estimate —
``max(word_count, ceil(chars / 4))`` per term. Terms over the budget,
duplicates and legacy-syntax terms are TRUNCATED (counted in
``NormalizedTranscript.biasing``), never errors.

Pricing (rate card 2026-07-09, estimates only): Nova-3 mono batch PAYG
$0.0048/min ($0.288/hr); Speaker Diarization add-on $0.0020/min; Keyterm
Prompting add-on $0.0013/min — billed ONLY when the request actually
carries ``keyterm`` params.

Settings: ``DEEPGRAM_API_KEY`` (required), ``DEEPGRAM_BASE_URL``,
``DEEPGRAM_MODEL`` (default ``nova-3``).
"""
from __future__ import annotations

import math
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
)

#: Documented hard limit: ~500 tokens across ALL keyterms in one request.
KEYTERM_TOKEN_CAP = 500

#: Keyterm prompting is a paid add-on, billed only when terms are sent.
KEYTERM_ADDON_USD_PER_MIN = 0.0013


def estimate_keyterm_tokens(term: str) -> int:
    """Per-term token estimate for the 500-token cap: Deepgram's tokenizer
    is unpublished, so ``max(words, ceil(chars/4))`` (English BPE averages
    ~4 chars/token; the word-count floor keeps multi-word phrases honest).
    The provider re-counts server-side either way."""
    stripped = term.strip()
    if not stripped:
        return 0
    return max(len(stripped.split()), math.ceil(len(stripped) / 4))


def _filter_keyterms(keyterms: list[str]) -> tuple[list[str], int]:
    """(accepted, truncated_count) under the Nova-3 keyterm rules: no
    legacy ``term:weight`` intensifiers, case-insensitive dedupe, and the
    estimated 500-token budget (a later, smaller term may still fit)."""
    accepted: list[str] = []
    truncated = 0
    seen: set[str] = set()
    total_tokens = 0
    for term in keyterms:
        stripped = term.strip()
        if not stripped:
            truncated += 1
            continue
        # ``term:2`` / ``term:0.5`` — the nova-2-era keywords syntax.
        tail = stripped.rsplit(":", 1)
        if len(tail) == 2 and tail[1].replace(".", "", 1).isdigit():
            truncated += 1
            continue
        key = stripped.casefold()
        if key in seen:
            truncated += 1
            continue
        tokens = estimate_keyterm_tokens(stripped)
        if total_tokens + tokens > KEYTERM_TOKEN_CAP:
            truncated += 1
            continue
        seen.add(key)
        accepted.append(stripped)
        total_tokens += tokens
    return accepted, truncated


class DeepgramProvider(SttProvider):
    name = "deepgram"
    supports_diarization = True
    supports_keyterms = True
    # Nova-3 mono batch PAYG $0.288/hr; diarization add-on +$0.12/hr and
    # keyterm add-on +$0.078/hr are billed only when requested.
    cost_per_hour = 0.288

    def default_speech_model(self) -> Optional[str]:
        return agent_settings.DEEPGRAM_MODEL

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
        api_key = agent_settings.DEEPGRAM_API_KEY
        if not api_key:
            raise TranscriptionError(
                "STAPEL_AGENT['DEEPGRAM_API_KEY'] is not set", provider=self.name
            )
        timeout = (
            int(agent_settings.STT_TIMEOUT)
            if timeout_seconds is None
            else int(timeout_seconds)
        )
        payload = audio.read_bytes(provider=self.name, timeout=min(timeout, 600))

        params: dict = {
            "model": self.effective_model(),
            "smart_format": "true",
            "utterances": "true",
        }
        if diarization:
            # Enables diarization AND selects the batch diarizer (v2);
            # the deprecated boolean ``diarize`` is never sent.
            params["diarize_model"] = "latest"
        if language:
            # Deepgram takes BCP-47 tags as-is (``en``, ``en-US``, ...).
            params["language"] = language

        biasing = None
        if keyterms:
            accepted, truncated = _filter_keyterms(keyterms)
            if accepted:
                # A list value encodes as the documented REPEATED query
                # param (?keyterm=A&keyterm=B) — never comma-joined.
                params["keyterm"] = accepted
            biasing = biasing_metadata(
                applied=bool(accepted),
                terms_sent=len(accepted),
                terms_truncated=truncated,
            )
        if provider_options:
            # The passthrough seam: applied AFTER the adapter's own params
            # so a caller can pin provider specifics without a core release.
            params.update(provider_options)

        try:
            resp = requests.post(
                f"{self._base_url()}/v1/listen",
                params=params,
                headers={
                    "Authorization": f"Token {api_key}",  # verified: NOT Bearer
                    "Content-Type": audio.mime or "audio/wav",
                },
                data=payload,
                timeout=timeout,
            )
        except requests.Timeout as exc:
            raise RetryableTranscriptionError(
                f"Deepgram request timed out: {exc}", provider=self.name
            ) from exc
        except requests.RequestException as exc:
            raise RetryableTranscriptionError(
                f"Deepgram transport error: {exc}", provider=self.name
            ) from exc

        if resp.status_code == 429:
            raise RetryableTranscriptionError(
                "Deepgram rate-limited", provider=self.name, status_code=429
            )
        if resp.status_code >= 500:
            raise RetryableTranscriptionError(
                f"Deepgram {resp.status_code}: {resp.text[:300]}",
                provider=self.name,
                status_code=resp.status_code,
            )
        if resp.status_code >= 400:
            raise TranscriptionError(
                f"Deepgram {resp.status_code}: {resp.text[:300]}",
                provider=self.name,
                status_code=resp.status_code,
            )

        try:
            body = resp.json()
        except ValueError as exc:
            raise RetryableTranscriptionError(
                f"Deepgram returned non-JSON: {resp.text[:300]}",
                provider=self.name,
            ) from exc
        transcript = _normalize(body, provider=self.name)
        transcript.biasing = biasing
        return transcript

    def _base_url(self) -> str:
        return (agent_settings.DEEPGRAM_BASE_URL or "").rstrip("/")


def _speaker_label(value) -> Optional[str]:
    """Deepgram speakers are INTEGERS starting at 0 (0 is valid and falsy
    — always test ``is not None``) → the label ``speaker_{n}``."""
    if value is None:
        return None
    return f"speaker_{value}"


def _normalize(payload: dict, *, provider: str) -> NormalizedTranscript:
    """Map a ``/v1/listen`` response → NormalizedTranscript (times are
    already seconds). Words prefer ``punctuated_word`` (smart_format)
    over raw ``word``."""
    results = payload.get("results") or {}
    channels = results.get("channels") or []
    channel = channels[0] if channels and isinstance(channels[0], dict) else {}
    alts = channel.get("alternatives") or []
    alt = alts[0] if alts and isinstance(alts[0], dict) else {}

    words: list[NormalizedWord] = []
    speakers: list[str] = []
    for tok in alt.get("words") or []:
        if not isinstance(tok, dict):
            continue
        text = (tok.get("punctuated_word") or tok.get("word") or "").strip()
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
                confidence=tok.get("confidence"),
                speaker=speaker,
            )
        )

    utterances: list[NormalizedUtterance] = []
    for utt in results.get("utterances") or []:
        if not isinstance(utt, dict):
            continue
        text = (utt.get("transcript") or "").strip()
        if not text or utt.get("start") is None or utt.get("end") is None:
            continue
        speaker = _speaker_label(utt.get("speaker"))
        if speaker and speaker not in speakers:
            speakers.append(speaker)
        utterances.append(
            NormalizedUtterance(
                text=text,
                start=float(utt["start"]),
                end=float(utt["end"]),
                speaker=speaker,
                confidence=utt.get("confidence"),
            )
        )

    duration = (payload.get("metadata") or {}).get("duration")
    try:
        duration = float(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration = None
    if duration is None and words:
        duration = max(w.end for w in words)

    return NormalizedTranscript(
        provider=provider,
        language=channel.get("detected_language"),
        duration_seconds=duration,
        words=words,
        utterances=utterances,
        speakers_detected=speakers,
        raw=payload,
    )


__all__ = ["DeepgramProvider"]
