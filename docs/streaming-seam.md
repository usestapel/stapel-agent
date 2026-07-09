# Streaming seam — design note (not implemented)

Input for a future design; **no commitment**. Today every surface is
request/response: `complete` returns one `ProviderResult`, `transcribe` one
`NormalizedTranscript`. Streaming would add an incremental channel *beside*
these, never replacing them.

## Where the seam is

- **Provider ABC** — optional `stream_complete(...)` yielding text deltas,
  gated by a `supports_streaming` flag (same discipline as `supports_images` /
  `supports_max_tokens`: never driven when False; old subclasses untouched).
- **Service** — a `complete_stream()` generator wrapping the provider stream,
  still writing exactly **one** terminal `PromptLog` row after the last chunk.
- **Wire** — a new comm surface (streaming `emits`, e.g. `llm.complete.delta`
  frames + a terminal frame), **not** a change to the `llm.complete` verb.

## Invariants we must not break

1. **Chunk order** — deltas in generation order; concatenation equals the
   one-shot `result`. Monotonic seq number + a single terminal `done`/`error`.
2. **Backpressure** — pull-based (generator/iterator); a slow consumer must
   not force unbounded buffering in provider or service.
3. **Wire compatibility** — additive: `llm.complete`/`transcribe` contracts,
   the `{status, ...}` envelope, and one-row-per-call logging are unchanged.
4. **Failure parity** — a mid-stream failure ends with a terminal `error`
   frame carrying the same `reason` (fatal-vs-retryable preserved).
5. **No partial ledger** — accounting happens once, at stream end.

STT streaming (partial transcripts) follows the same shape: incremental
utterance frames + a terminal full `NormalizedTranscript`.
