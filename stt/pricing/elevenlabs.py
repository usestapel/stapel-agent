"""ElevenLabs Scribe STT pricing (estimate — the API returns no cost field).

THIS RATE IS NOT CONFIRMED AGAINST THE VENDOR'S OWN PRICE PAGE
---------------------------------------------------------------
Attempted 2026-08-28: https://elevenlabs.io/pricing/api answers 302 to a
help-centre article about country restrictions ("Do you restrict access to the
service and platform for any specific countries"). The price table is behind a
geo redirect from here and could not be read. So, honestly:

  - $0.22 / hr is what this module has carried since 2026-07-01
    (ARCHITECTURE.md v1.1 §11.2, read when the page was reachable);
  - https://elevenlabs.io/docs/capabilities/speech-to-text WAS readable on
    2026-08-28 and confirms only the SHAPE — "billing is calculated per hour
    of audio, with rates varying by tier and model" — and that the current
    batch model is Scribe v2 (plus a Scribe v2 Realtime, a different product);
  - secondary write-ups dated August 2026 quote $0.22/hr for Scribe v2, which
    agrees with the constant. Secondary sources are corroboration, not
    verification, and they are not what this number rests on.

The value is therefore UNCHANGED and its provenance is "last read on the
official page 2026-07-01; not re-confirmable from this network on 2026-08-28".
Anyone planning spend on ElevenLabs should read the page from a permitted
region, or take the rate off an invoice, before trusting it. The same
secondary sources mention paid add-ons (entity detection, keyterm prompting)
that this module does not model at all — one more reason not to treat the
number below as a full card.

ElevenLabs bills speech-to-text per hour of audio; the exact per-hour rate is
plan-dependent, so this is an ESTIMATE recorded for cost tracking, not a
billed amount (the STT response carries no cost field).

Version: 1.2 · Date: 28 Aug 2026 (re-verification sweep: page unreachable,
provenance downgraded and stated)
Source: https://elevenlabs.io/pricing/api (302 geo redirect on 2026-08-28)
Verified: 2026-07-01 — NOT re-verified since
"""

from __future__ import annotations

SCRIBE_V2_PRICE_PER_HOUR = 0.22  # USD/hr — last read on the official page
#                                  2026-07-01; see the docstring for why it
#                                  could not be re-confirmed on 2026-08-28.

_MODEL_ID = "scribe_v2"


def estimate_cost(duration_ms: int, *, model: str = _MODEL_ID) -> float | None:
    """Estimate the Scribe transcription cost for ``duration_ms`` of audio.

    Returns None for an unpriced model (never a fabricated 0), matching the
    AssemblyAI/Deepgram pricing modules.
    """
    if model != _MODEL_ID:
        return None
    hours = duration_ms / 3_600_000
    return round(hours * SCRIBE_V2_PRICE_PER_HOUR, 6)
