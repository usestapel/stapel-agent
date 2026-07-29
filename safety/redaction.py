"""Refuse to persist an artifact that contains a secret.

Ported from the recordings product (``recordings_ext/mic/redaction.py``),
which had it from the harness's ``summarize/mic_artifacts.py``. Its write path
is file-based and does not come across; this guard does, because what it
protects against has nothing to do with where the bytes land — and nothing to
do with meetings either. Any library that persists a model's output or the
payload of a request it made needs it, which is why it belongs here next to
``detect_pwned_markers`` / ``sanitize_for_rag`` rather than in one product.

An extraction artifact is assembled from a prompt, a model response and the
provenance of the call. Every one of those has been observed to carry a key at
some point — a prompt echoing an environment dump, a provider error string
quoting the request, a debugging field someone added and forgot. Once written
the secret is in a database row, in every backup of it, and in whatever the
staff-facing view renders.

The check is deliberately dumb: does the serialized text contain the VALUE of
an environment variable that looks like a credential. No pattern matching on
key shapes beyond the one prefix we know, because a clever detector that misses
is worse than a blunt one that does not — this runs on our own process
environment, where the true values are simply available.

The error names the offending variable and never its value. An exception
message is itself a thing that gets logged.

The three knobs are module-level and public on purpose. A host whose secrets
are not named ``*_API_KEY`` has to be able to say so, and the alternative —
a settings block read at call time — would make the cheapest guard in the
library depend on Django being configured. Extend them in ``AppConfig.ready()``
(``KEY_ENV_SUFFIXES += ("_CREDENTIAL",)``); do not shrink them.
"""

from __future__ import annotations

import os

#: Environment variables whose values must never appear in a stored artifact.
KEY_ENV_SUFFIXES: tuple[str, ...] = (
    "_API_KEY", "_API_TOKEN", "_SECRET", "_PASSWORD",
)

#: Below this length a value is not a credential, it is a flag or a short
#: config string, and matching on it would refuse writes at random.
MIN_SECRET_LEN = 12

#: Literal prefixes worth checking for on their own: a provider key can reach a
#: payload from somewhere other than our own environment (an operator pasting
#: one into a prompt, a provider echoing the Authorization header back in an
#: error body).
KEY_PREFIXES: tuple[str, ...] = ("sk-ant-",)


class RedactionError(RuntimeError):
    """A payload contained a credential; the write was refused.

    The message names the offending environment variable only — never the
    value, since an exception message is itself a thing that gets logged.
    """


def _secret_values() -> list[tuple[str, str]]:
    """``(env_var_name, value)`` pairs that must never appear in an artifact."""
    return [
        (name, value)
        for name, value in os.environ.items()
        if name.endswith(KEY_ENV_SUFFIXES) and len(value) >= MIN_SECRET_LEN
    ]


def redaction_gate(text: str) -> None:
    """Raise :class:`RedactionError` when ``text`` leaks a secret."""
    for prefix in KEY_PREFIXES:
        if prefix in text:
            raise RedactionError(
                f"payload contains a {prefix!r}-prefixed token; write refused"
            )
    for name, value in _secret_values():
        if value in text:
            raise RedactionError(
                f"payload contains the value of {name}; write refused"
            )


__all__ = [
    "KEY_ENV_SUFFIXES",
    "KEY_PREFIXES",
    "MIN_SECRET_LEN",
    "RedactionError",
    "redaction_gate",
]
