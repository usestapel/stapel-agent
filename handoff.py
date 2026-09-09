"""Hand a bulk result to the caller's storage instead of the caller's wire.

The comm Functions in this module were written by reusing the HTTP views'
response shapes verbatim. Over HTTP a multi-megabyte JSON body is
unremarkable; over a message broker it is a hard ceiling, and the contract
that was correct in one medium became an outage in the other. Measured on a
client stand (2026-09-09): ``llm.transcribe`` over a 2h28m meeting answered
8 647 617 bytes against an 8 MiB NATS cap and the recording was dropped —
twice, because the user tried again.

The input side of the same call already had this right. ``audio_url`` is a
presigned GET the caller minted; ``TRANSCRIBE_SCHEMA`` says it outright —
"comm carries URLs only, never raw bytes". So the output side is given the
mirror image: the caller mints a presigned PUT, the agent writes the result
there, and the reply carries the key and the counts.

WHY THE CALLER OWNS THE DESTINATION
    The agent has no object storage and should not grow one. The caller
    does — the transcript's home is a field on the caller's own row
    (``Recording.transcript_storage_key`` and its siblings), under the
    caller's key layout, in the caller's bucket, with the caller's
    retention. Handing the agent a bucket would put a second copy in a
    second place and make the agent responsible for cleaning it up.

WHY NOT ONLY WHEN IT IS BIG
    Because then the size is part of the contract, and a contract that
    holds up to 8 MiB is one config change or one chattier provider away
    from not holding. The caller asks for a reference or it does not, and
    that decision is made once at the call site, never per payload.
"""
from __future__ import annotations

import hashlib
import json
import logging

logger = logging.getLogger(__name__)

#: Only these schemes are ever written to. A presigned PUT is a URL from
#: the caller, and a Function payload is data from another service — so it
#: gets the same treatment as any other URL this module is handed.
ALLOWED_SCHEMES = ("http", "https")

DEFAULT_TIMEOUT_SECONDS = 120


class HandoffError(Exception):
    """The artifact could not be written where the caller asked."""


def put_json(url: str, body: dict, *, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> dict:
    """PUT *body* as JSON to *url*; return ``{"bytes", "sha256"}``.

    Raises :class:`HandoffError` on anything that is not a 2xx. It must
    never degrade to "return the bulk inline instead": that branch is the
    outage this module exists to remove, and it would fire exactly when the
    payload is large enough to matter.
    """
    from urllib.parse import urlsplit

    scheme = urlsplit(url).scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise HandoffError(f"refusing to write an artifact to a {scheme or 'schemeless'} URL")

    data = json.dumps(body, default=str).encode("utf-8")
    digest = hashlib.sha256(data).hexdigest()

    import requests

    try:
        resp = requests.put(
            url, data=data, headers={"Content-Type": "application/json"}, timeout=timeout
        )
    except requests.RequestException as exc:
        raise HandoffError(f"artifact PUT failed: {exc!r}") from exc
    if resp.status_code >= 300:
        # The body of a presigned-PUT rejection is the operator's only clue
        # (an expired signature and a wrong content type look identical
        # from here), so a bounded slice of it travels with the error.
        raise HandoffError(
            f"artifact PUT returned HTTP {resp.status_code}: {resp.text[:300]}"
        )
    logger.info("stapel-agent: artifact of %d bytes handed off (sha256 %s)", len(data), digest[:12])
    return {"bytes": len(data), "sha256": digest}


__all__ = ["HandoffError", "put_json", "put_json_or_raise", "ALLOWED_SCHEMES"]


def put_json_or_raise(url: str, body: dict, *, key: str = "", timeout: int = DEFAULT_TIMEOUT_SECONDS) -> dict:
    """:func:`put_json` plus the ``key`` echo the caller gets back.

    The key is echoed rather than parsed out of the URL: a presigned URL's
    path is the storage backend's business, and a caller that has to
    reverse-engineer its own key from a signature is a caller with a
    parser it never asked for.
    """
    ref = put_json(url, body, timeout=timeout)
    return {"key": key, "content_type": "application/json", **ref}
