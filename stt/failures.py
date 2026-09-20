"""The STT spelling of :mod:`stapel_agent.failures` — one classification
of a provider's refusal, shared by every adapter.

The table and the classifier itself now live one level up, because the
text surface learned the same lesson the same way a year later (see that
module). This one keeps what is genuinely about transcription: the
exception classes the STT chain raises and the constructors that pick
between them.


The defect this module closes (a client stand in production, 2026-09-09 →
2026-09-12): every adapter classified by STATUS CLASS — 429 retryable,
>=500 retryable, *every other 4xx* fatal. ElevenLabs answers an exhausted
account with ``401 {"detail": {"type": "invalid_request", "code":
"quota_exceeded", "message": "This request exceeds your quota of 23736.
You have 18 credits remaining..."}}``, which fell into the "every other
4xx" branch. The router therefore read an empty wallet as bad audio and
returned failure without trying the configured fallback chain: 41
consecutive recordings failed, and ``fallback_used`` had never once been
true in the ledger's whole history.

The rule: **a provider's account, billing or auth condition is never bad
input.** Only the request's own content — audio the provider cannot
decode, a container it does not accept — is fatal, because only that
fails identically on the next provider. Everything else means "this
provider cannot serve this request right now", and the rest of the chain
gets its turn.

Classification is per RESPONSE, not per status class: the status narrows
it, the body decides. A 401 carrying ``quota_exceeded`` is ``quota``; a
401 carrying nothing recognisable is ``auth`` (a plain bad key — the next
provider may well work); a 400 carrying ``insufficient credits`` is
``quota``, because providers disagree on which status they use for the
same condition; a 400 about the media is ``media``.

``reason`` rides on the exception and is recorded per attempt on the
PromptLog row (``metadata.attempts[].reason``), so an operator can tell
"we are out of money" from "the audio is broken" without reading strings:

===============  =========  ===================================================
reason           fatal?     means
===============  =========  ===================================================
``quota``        no         account out of credits / plan or usage limit hit
``auth``         no         credentials missing, invalid or refused
``rate``         no         throttled right now
``server``       no         provider 5xx
``unavailable``  no         any other provider-side refusal or unusable answer
``unsupported``  no         this provider cannot serve this request (language,
                            model, capability) — another one may
``timeout``      no         our deadline, or the provider stalling
``transport``    no         network/TLS failure below the HTTP answer
``media``        **yes**    the audio or its reference is the problem
``job``          **yes**    the provider ran the job and it failed on the audio
``language``     **yes**    the language code itself is unresolvable (0.25.0)
===============  =========  ===================================================
"""
from __future__ import annotations

from typing import Optional

from ..failures import (  # noqa: F401  (re-exported: this module is the STT spelling)
    AUTH_MARKERS,
    FATAL_REASONS,
    MEDIA_STATUSES,
    QUOTA_MARKERS,
    REASON_AUTH,
    REASON_JOB,
    REASON_LANGUAGE,
    REASON_MEDIA,
    REASON_QUOTA,
    REASON_RATE,
    REASON_SERVER,
    REASON_TIMEOUT,
    REASON_TRANSPORT,
    REASON_UNAVAILABLE,
    REASON_UNSUPPORTED,
    classify_status,
)
from ..failures import PHRASES as _PHRASES  # noqa: F401  (kept: tests read it)
from .base import RetryableTranscriptionError, TranscriptionError


def status_error(
    status_code: int,
    body: str = "",
    *,
    provider: str,
    label: str = "",
    op: str = "",
) -> TranscriptionError:
    """The classified exception for a non-2xx answer (raise it).

    *label* is the provider's human name and *op* an optional stage
    ("submit", "poll"), so the message still reads like the hand-written
    ones it replaces.
    """
    fatal, reason = classify_status(status_code, body)
    where = " ".join(part for part in (label, op) if part)
    phrase = _PHRASES.get(reason, reason)
    message = f"{where} {status_code} ({phrase}): {(body or '')[:300]}".strip()
    cls = TranscriptionError if fatal else RetryableTranscriptionError
    return cls(message, provider=provider, status_code=status_code, reason=reason)


def raise_for_status(
    resp, *, provider: str, label: str = "", op: str = ""
) -> None:
    """No-op on 2xx; otherwise raise the classified error for *resp*."""
    status = int(resp.status_code)
    if 200 <= status < 300:
        return
    raise status_error(
        status,
        _body_of(resp),
        provider=provider,
        label=label,
        op=op,
    )


def _body_of(resp) -> str:
    """The response text, never an exception: a body that cannot be read
    must not turn a classifiable refusal into a crash."""
    try:
        return resp.text or ""
    except Exception:  # pragma: no cover - defensive
        return ""


def missing_credentials(
    setting: str, *, provider: str, hint: str = ""
) -> RetryableTranscriptionError:
    """A provider with no key configured is UNAVAILABLE, not fatal.

    It used to be fatal, which meant one unconfigured name in a chain
    sank every provider behind it — the same defect as the quota 401,
    reached from the other side.
    """
    detail = f" {hint}" if hint else ""
    return RetryableTranscriptionError(
        f"STAPEL_AGENT['{setting}'] is not set{detail}",
        provider=provider,
        reason=REASON_AUTH,
    )


def unavailable(message: str, *, provider: str) -> RetryableTranscriptionError:
    """Provider answered, but not with something usable (no id, non-JSON)."""
    return RetryableTranscriptionError(
        message, provider=provider, reason=REASON_UNAVAILABLE
    )


def unsupported(message: str, *, provider: str) -> RetryableTranscriptionError:
    """This provider cannot serve this request — another one may.

    Language packs, single-language models, capability gaps. Retryable on
    purpose: "gladia cannot do Russian" says nothing about whisper.
    """
    return RetryableTranscriptionError(
        message, provider=provider, reason=REASON_UNSUPPORTED
    )


def timed_out(message: str, *, provider: str) -> RetryableTranscriptionError:
    return RetryableTranscriptionError(
        message, provider=provider, reason=REASON_TIMEOUT
    )


def transport(message: str, *, provider: str) -> RetryableTranscriptionError:
    return RetryableTranscriptionError(
        message, provider=provider, reason=REASON_TRANSPORT
    )


def job_failed(message: str, *, provider: str) -> TranscriptionError:
    """The provider ran the job and it failed on the audio — fatal."""
    return TranscriptionError(message, provider=provider, reason=REASON_JOB)


def bad_media(
    message: str, *, provider: str, status_code: Optional[int] = None
) -> TranscriptionError:
    """The audio, or the reference to it, is the problem — fatal."""
    return TranscriptionError(
        message, provider=provider, status_code=status_code, reason=REASON_MEDIA
    )


__all__ = [
    "AUTH_MARKERS",
    "FATAL_REASONS",
    "MEDIA_STATUSES",
    "QUOTA_MARKERS",
    "REASON_AUTH",
    "REASON_JOB",
    "REASON_LANGUAGE",
    "REASON_MEDIA",
    "REASON_QUOTA",
    "REASON_RATE",
    "REASON_SERVER",
    "REASON_TIMEOUT",
    "REASON_TRANSPORT",
    "REASON_UNAVAILABLE",
    "REASON_UNSUPPORTED",
    "bad_media",
    "classify_status",
    "job_failed",
    "missing_credentials",
    "raise_for_status",
    "status_error",
    "timed_out",
    "transport",
    "unavailable",
    "unsupported",
]
