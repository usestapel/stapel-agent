"""One reading of "the provider said no", shared by every provider call.

WHY THIS IS NOT PER-SURFACE
    ``stt/failures.py`` was written for the transcription chain after a
    client stand read an exhausted ElevenLabs account (``401 {"code":
    "quota_exceeded"}``) as bad audio and failed 41 recordings without
    ever trying the configured fallback. The rule it established — *a
    provider's account, billing or auth condition is never bad input* —
    has nothing to do with audio, and the next surface to learn it the
    expensive way was the text one: on 2026-09-20 a client's summariser
    endpoint answered ``403 "your team has either used all available
    credits or reached its monthly spending limit"`` and every one of
    that day's recordings lost its summary, because
    ``providers/openai_compat.py`` turned every status >= 400 into one
    flat ``ProviderError`` that the service could only report.

    Two surfaces, one defect, so one classifier. A second copy of the
    quota markers is a copy that goes stale the first time a vendor
    invents a new phrase for "pay us".

THE THREE OUTCOMES A CALL SITE HAS TO TELL APART
    ``retryable_same_provider``   the provider is fine and this moment is
                                  not — a timeout, a 5xx, a throttling
                                  429 with a Retry-After. Trying the same
                                  provider later is the right move.
    ``fallback_next_provider``    this provider cannot serve this request
                                  at all right now — quota exhausted, a
                                  plan or auth refusal, a model or
                                  capability it does not have, a payload
                                  limit that is its own. Another provider
                                  may answer it unchanged.
    ``terminal_input``            the REQUEST is the problem — media the
                                  decoder rejects, an unsupported
                                  container, a language nothing resolves.
                                  Every provider would fail identically,
                                  so walking the chain only spends time.

    :func:`disposition` names which of the three a ``reason`` belongs to.
    It is the answer a chain walker needs and the one an operator reads;
    ``fatal`` (the boolean the older call sites use) is exactly
    ``disposition(...) == TERMINAL_INPUT``.

CLASSIFICATION IS PER RESPONSE, NOT PER STATUS CLASS
    The status narrows it, the body decides. A 401 carrying
    ``quota_exceeded`` is ``quota``; a 401 carrying nothing recognisable
    is ``auth`` (a plain bad key — the next provider may well work); a
    400 carrying ``insufficient credits`` is ``quota``, because providers
    disagree about which status they use for the same condition; a 400
    about the media is ``media``.

====================  ===========================  ======================
reason                disposition                  means
====================  ===========================  ======================
``quota``             fallback_next_provider       out of credits / plan
                                                   or spending limit hit
``auth``              fallback_next_provider       credentials missing,
                                                   invalid or refused
``rate``              retryable_same_provider      throttled right now
``server``            retryable_same_provider      provider 5xx
``timeout``           retryable_same_provider      our deadline, or the
                                                   provider stalling
``transport``         retryable_same_provider      network/TLS failure
``unavailable``       fallback_next_provider       any other provider-side
                                                   refusal or unusable answer
``unsupported``       fallback_next_provider       this provider cannot
                                                   serve this request
``media``             terminal_input               the input is the problem
``job``               terminal_input               the provider ran it and
                                                   it failed on the input
``language``          terminal_input               the language code itself
                                                   is unresolvable
====================  ===========================  ======================
"""
from __future__ import annotations

REASON_QUOTA = "quota"
REASON_AUTH = "auth"
REASON_RATE = "rate"
REASON_SERVER = "server"
REASON_UNAVAILABLE = "unavailable"
REASON_UNSUPPORTED = "unsupported"
REASON_TIMEOUT = "timeout"
REASON_TRANSPORT = "transport"
REASON_MEDIA = "media"
REASON_JOB = "job"
REASON_LANGUAGE = "language"

#: The three dispositions, spelled as the task that named them does.
RETRYABLE_SAME_PROVIDER = "retryable_same_provider"
FALLBACK_NEXT_PROVIDER = "fallback_next_provider"
TERMINAL_INPUT = "terminal_input"

#: The reasons that stop a fallback chain. Everything else walks it.
FATAL_REASONS = frozenset({REASON_MEDIA, REASON_JOB, REASON_LANGUAGE})

#: Reasons where the SAME provider is worth another attempt later. They
#: still walk the chain when there is one — a second provider now beats a
#: first provider in thirty seconds — but they are the ones a caller may
#: legitimately re-drive, and the ones that must never be read as "this
#: account is out of money".
RETRYABLE_REASONS = frozenset(
    {REASON_RATE, REASON_SERVER, REASON_TIMEOUT, REASON_TRANSPORT}
)

#: Statuses providers use for "the request/media itself is wrong". They are
#: terminal ONLY when the body does not say quota/auth — see
#: :func:`classify_status`.
MEDIA_STATUSES = frozenset({400, 413, 415, 422})

#: Substrings that mean "the account cannot pay for this call". Matched
#: case-insensitively anywhere in the body, so they hit both a JSON error
#: code (``"code": "quota_exceeded"``) and prose ("You have 18 credits
#: remaining", "used all available credits or reached its monthly
#: spending limit").
QUOTA_MARKERS = (
    "quota",
    "credit",
    "billing",
    "insufficient_funds",
    "insufficient funds",
    "insufficient balance",
    "payment",
    "past_due",
    "unpaid",
    "subscription",
    "free tier",
    "usage limit",
    "limit_exceeded",
    "plan_limit",
    "spending limit",
    "spend limit",
    "monthly limit",
    "out of funds",
    "top up",
    "top-up",
    "upgrade your",
)

#: Substrings that mean "these credentials are not usable" — needed because
#: some providers answer a bad key with 400/422 rather than 401.
AUTH_MARKERS = (
    "api key",
    "api_key",
    "apikey",
    "unauthorized",
    "unauthenticated",
    "authentication",
    "not authorized",
    "invalid_token",
    "invalid token",
    "forbidden",
    "permission",
)

#: Human half of the message, so a log line reads without a lookup table.
PHRASES = {
    REASON_QUOTA: "quota/billing refused",
    REASON_AUTH: "auth refused",
    REASON_RATE: "rate-limited",
    REASON_SERVER: "server error",
    REASON_UNAVAILABLE: "provider unavailable",
    REASON_UNSUPPORTED: "unsupported request",
    REASON_MEDIA: "rejected the media",
}


def has_marker(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def classify_status(status_code: int, body: str = "") -> tuple[bool, str]:
    """``(fatal, reason)`` for one non-2xx provider answer.

    The body is advisory — an empty one still classifies, it just cannot
    upgrade a status to ``quota``/``auth``.
    """
    text = (body or "").lower()

    if status_code >= 500:
        return False, REASON_SERVER
    if status_code == 402:  # Payment Required — never ambiguous
        return False, REASON_QUOTA
    if status_code == 429:
        # A 429 can be throttling OR the monthly allowance; the body is
        # what tells them apart, and an operator needs the difference
        # (one clears by itself, the other needs a card).
        return False, REASON_QUOTA if has_marker(text, QUOTA_MARKERS) else REASON_RATE
    if status_code in (401, 403, 407):
        # 403 is the one the text surface was losing recordings to: xAI
        # answers an exhausted team with it, and "forbidden" alone would
        # have read as a bad key. Either way it is not the customer's
        # input and never terminal.
        return False, REASON_QUOTA if has_marker(text, QUOTA_MARKERS) else REASON_AUTH
    if status_code in MEDIA_STATUSES:
        if has_marker(text, QUOTA_MARKERS):
            return False, REASON_QUOTA
        if has_marker(text, AUTH_MARKERS):
            return False, REASON_AUTH
        # Unsupported format, too long, corrupt, malformed params: the
        # only family the next provider would fail on identically.
        return True, REASON_MEDIA
    if status_code >= 400:
        # 404/405/409/451/... — the endpoint, the deployment's config or
        # the provider's own state. Never the input.
        return False, REASON_UNAVAILABLE
    # A non-2xx below 400 is a redirect the client did not follow: the
    # provider did not answer the call, so the next one gets a turn.
    return False, REASON_UNAVAILABLE


def disposition(reason: str | None) -> str:
    """Which of the three outcomes *reason* belongs to."""
    if reason in FATAL_REASONS:
        return TERMINAL_INPUT
    if reason in RETRYABLE_REASONS:
        return RETRYABLE_SAME_PROVIDER
    return FALLBACK_NEXT_PROVIDER


def is_out_of_credits(reason: str | None) -> bool:
    """Whether *reason* means the account, not the request, is the problem."""
    return reason == REASON_QUOTA


__all__ = [
    "AUTH_MARKERS",
    "FALLBACK_NEXT_PROVIDER",
    "FATAL_REASONS",
    "MEDIA_STATUSES",
    "PHRASES",
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
    "RETRYABLE_REASONS",
    "RETRYABLE_SAME_PROVIDER",
    "TERMINAL_INPUT",
    "classify_status",
    "disposition",
    "has_marker",
    "is_out_of_credits",
]
