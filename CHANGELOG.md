# Changelog

All notable changes to stapel-agent are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.21.2] — 2026-09-04

### Fixed — a superseded run must not answer for the job that displaced it

One key holds one document, and a refresh starts a second run over it while
the first is still working. `ModelStateStore.save` was a blind upsert — "the
LAST write of a document wins" — so the two runs took turns owning the row.

Measured on a live composer (ruberi.ru, Д380): the job starts at the photo
step over the pictures alone, and again the moment the seller has typed a
title. The first run read a phone's photos as a mirror, descended to
«Зеркала» and answered `mirror_type`, `frame`, `furniture_shape`; the second
read the words and answered `vendor=apple`, `model=iphone-13`,
`memory_size=128-gb`. The stale run finished last, so its document — and its
terminal `status: "done"` — is what every poll of that draft returned. The
composer dropped every answer (no field of the chosen leaf carries those
slugs), stopped polling on the `done` that was not its job's, and the
characteristics step stayed empty while the job read finished.

* **Every write a run makes is FENCED** on the fingerprint it started under.
  A run whose key has since been claimed by a newer question stops where it
  stands: its batches, and its terminal status, are dropped rather than
  written. `runner.SupersededRun` is that signal, handled inside `run()` —
  it is not a stage failure and never reaches a caller.
* **`runner.run(..., fingerprint=...)`** — the question this run was started
  for. A run already superseded when the worker picks it up returns at once
  and buys no provider call. Optional: without it the fence is the document
  as loaded, which still stops a run from overwriting the one that displaced
  it.
* **`StateStore.save_if_current(key, state, fingerprint)`** — the atomic
  fenced write, on `MemoryStateStore` and `ModelStateStore` (one conditional
  `UPDATE`; `updated_at` is set explicitly, because `.update()` is not
  `.save()` and `auto_now` never fires). `FencedStateStore` names the
  protocol. A host store without it falls back to load-then-save, which
  closes the long window and leaves only the instant between two statements.

## [0.21.1] — 2026-09-04

### Fixed — a job asked over nothing must say so, not answer

`sha256("")` is a perfectly valid hash, and that was the trap. A host whose
job started over inputs that never arrived got a fingerprint that looked
exactly as legitimate as any other: every stage ran, each answered about a
subject that was not there, and the non-blocking screening stage published
`allowed: false` with the rationale "the content is empty" — a verdict on the
READER, not on what was being screened. Measured on a live stand: every
analysed listing ended that way, while the row being analysed held two photos
and a title the whole time.

* **`Fingerprint.is_empty`** — the emptiness is now a question a caller can
  ask, instead of one every stage answers separately and none says out loud.
* **`runner.start()` refuses an empty question.** The document comes back
  `status: "failed"` with the new top-level **`error: "empty_input"`**, every
  stage `skipped`, `started` False — so a host's `execute()` is never reached
  and no provider call is bought. A later start with real inputs runs
  normally; a failed job is not a cached answer.
* **`AnalysisState.error`** joins the document (JSON round-trip included,
  `null` when the job did not fail as a whole). The key is always present.
* **`Stage(skip_when=...)`** — a predicate asked before a stage runs. A stage
  that answers "nothing to do" is `skipped`, which is a different statement
  from `done` with an empty result, and the difference is the whole point: a
  screener that never saw content must not be the thing that says the content
  is not allowed. A predicate that raises is logged and the stage runs.

Additive: `skip_when` defaults to `None`, and a fingerprint over any real
input behaves exactly as it did in 0.21.0.

## [0.21.0] — 2026-09-04

### Added — `stapel_agent.analysis`: staged analysis as a job, not a request

A composer that recognises a listing from photos ran the whole chain inside
one blocking POST: seven to nineteen provider calls, median 17 s, worst 33 s,
holding a web worker for all of it. Everything the FIRST call already knew —
the title, the description, what the photograph plainly shows — stayed
invisible until the LAST one returned, and there was nothing to poll, so the
only two outcomes were "wait" and "lose the work".

This is that chain as a **job on a subject**, generic enough for any staged
recognition: a state document, a stage runner, a store, and the ordering that
turns a catalogue's feature definitions into the order a composer asks them.

- **`analysis.state`** — the document (`status`, `fingerprint`, `stages`,
  `updated_at`), JSON by construction so it can live in a row, a cache entry
  or another service's metadata without a second representation.
  `Fingerprint` splits into a photo half and a text half because they
  invalidate different work: new photos invalidate everything, edited text
  only what was derived from it, and a single opaque hash cannot say that.
- **`analysis.runner`** — `start()` is idempotent on the fingerprint (the
  same question is not asked twice), and re-runs on a refresh only the stages
  whose declared half of the fingerprint actually changed. `run()` writes the
  document **after every batch**, so a long stage reports `progress` and a
  growing partial `result` instead of being opaque. A stage may be
  `blocking=False` — a screening pass that fails must not fail the job.
- **`analysis.store`** — `ModelStateStore` (the new `AnalysisJob` row,
  migration `0007`) or any host's own `StateStore`.
- **`analysis.views.AnalysisJobView`** — `POST` starts or refreshes and
  answers `202` with the document; `?wait=1` waits for the FAST stage only;
  `GET` answers the same document, so a poller and a waiter read one shape.
- **`analysis.blocks`** — the ordering, and the four silent drops it closes.
  Features are planned in the composer's order (sections by first appearance,
  required-bearing sections first, required fields first inside one) but
  **resolved** in dependency order over `optionsRef.parentFeature`. Those are
  not the same order, and on a live motoring leaf whose catalogue order is
  alphabetical by slug they are nearly opposite: `generation` sorts before
  `model` before `make`, so a catalogue-ordered cascade asked every child
  before its parent existed and dropped the whole chain without a word.
  Every feature that cannot be asked comes back as an `Ask` with a
  `reason` — `unsupported_type`, `no_options`, `parent_unknown`,
  `parent_cycle` — never as an omission a caller cannot tell from "nobody
  thought to ask". `bounds_for()` narrows numeric questions through
  stapel-attributes' `limit` rule effect where that module is installed, and
  through the config's own `min`/`max` where it is not.

Nothing else in the package changed; `analysis` is additive.

## [0.20.0] — 2026-09-03

### Added — the embeddings surface joins the ledger

`embed()` wrote a PromptLog row with no token counts, no cost, and the
PROVIDER name in the `model` column. Worse than unpriced: outside metering
entirely, on a surface a client fleet's composer runs its vector matching
through. 367 rows on one stand, and the provider had *already reported*
`usage: {"prompt_tokens": 4, "total_tokens": 4}` — the service dropped it into
`metadata` and threw the rest away.

- **`input_tokens`** is recorded from the provider's reported usage
  (`prompt_tokens`, else `total_tokens`, else `input_tokens`). Embeddings have
  no output tokens, so `output_tokens` is 0 rather than NULL — a real zero,
  not an absence. A non-integer usage value is treated as unreported rather
  than coerced: a count guessed from a string is a fabricated quantity.
- **`model` now holds the model**, and `metadata["provider"]` the provider.
  The old arrangement is exactly why no rate card could ever have matched an
  embed row: the fleet stored `openai-embeddings` in the column while the
  model it actually called (`sentence-transformers/LaBSE`) sat in metadata.
  The provider name remains the fallback for a self-hosted server that reports
  no model, so the column is never empty and never a lie. **Breaking** for
  anything reading `PromptLog.model` on `source=embed` rows.
- **`pricing.EMBEDDING_PRICES_USD_PER_MTOK`** — a second table, because an
  embeddings call has a different shape and not just different numbers: it
  bills input tokens and has no output tokens, so a row of
  `{"input", "output"}` and an estimate that multiplies both cannot express
  it. `text-embedding-3-small` $0.02, `-3-large` $0.13, `ada-002` $0.10 per
  MTok of input, verified at developers.openai.com/api/docs/pricing on
  3 Sep 2026.
- **`STAPEL_AGENT["EMBEDDING_PRICES"]`** — the host's own card, merged over
  the shipped one and winning: a negotiated rate is a fact about this
  deployment's invoice, a published list price is only the default.

### Free is not unknown

A miss leaves `cost_usd` NULL, not `0.0` — the audio surfaces' convention, and
the point is that a SUM cannot absorb an unknown as if it were nothing. A
**declared** `0.0` is the opposite: a host that runs its own embedder is not
billed per query, and that is a fact about the endpoint rather than an absence
of information. `EMBEDDING_PRICES = {"<model>": 0.0}` lands the row at `0.00`
with `cost_basis=pricing_estimate`, which is a different row from one nobody
costed. The shipped table carries no zero entries on purpose: a library
asserting a price about somebody else's endpoint is the same fabrication as
guessing a published one, and harder to spot because it looks like good news.

### Changed — W018 watches both surfaces

The check was green about half the spend. It now resolves the configured
`EMBEDDINGS_MODEL` against the embeddings card as well, and reports both
surfaces in one finding. A deployment that masked every embedding adapter has
removed the surface and is not warned, same rule as W015.

### Fixed — `gpt-5.6-luna` was over-priced by 5x

Listed at $1.00/$6.00 from a 2026-07-10 reading; re-verified 3 Sep 2026
against the pricing page and the model's own page, which both publish
$0.20/$1.20 for the short-context standard tier. An over-stated rate silently
inflates every estimate that touches it — the unpriced defect with the sign
flipped. Rows already computed at the old rate keep their stored cost, because
pricing is applied at call time and never recomputed. The "short-context"
qualifier is now confirmed rather than assumed: prompts over 272K input tokens
bill 2x input / 1.5x output for the whole request, which stays unmodelled for
the reason the module docstring gives.

## [0.19.1] — 2026-09-03

### Fixed

- `W018` no longer takes `manage.py check` down with a provider that raises
  from its constructor. `PROVIDERS` is an open extension point, so a
  host-registered class may raise anything at all; the check caught only
  `ProviderError`/`ImportError`. A system check that dies blocks the deploy it
  was added to inform — and a broken provider is already W001/W002/W016's
  finding, not this one's. Every exception is now a clean "not my finding".

## [0.19.0] — 2026-09-03

### Added — the rate card is now checked against the configuration, not only against itself

`stapel_agent.W018`: a Django system check that resolves the models THIS
deployment is configured to call and names any the rate card has never heard
of, at `manage.py check` time.

The defect it closes was live. A client fleet's AI composer pointed all three
ladder rungs at `gpt-5.2` through `OPENAI_COMPAT_MODELS`; the model was not in
`PRICES_USD_PER_MTOK`, so every call — vision draft, category descent,
characteristic filling — stored `cost_basis=unpriced` and `cost_usd=0.0`. That
storage was correct and did exactly its job: it said *unknown*, not *free*.
`services.complete()` even logged a warning naming the model. Both are per
call, inside a worker, on the same line every time — 352 rows in a single day,
and metering that could not cost the feature at all.

`test_the_default_large_model_is_priced` was green throughout, because the
shipped ladder is not what the deployment called. A gate that proves the
library's defaults right proves nothing about a settings file that overrides
them. W018 asks the deployment's own question instead, and asks it once, before
the first call rather than after each one.

Models resolve through `backend.resolve_model()` — the same seam `complete()`
uses — so an `openai-compat` overlay is what gets checked, and a provider that
overrides model resolution is covered without this check knowing about it.
Warning, not Error: a deployment may not care what its calls cost, and a
provider that reports its own charge never consults the table. An unknown
`DEFAULT_PROVIDER` is left to E001, which already names it.

Also: `check_agent_beat_schedule_is_registered` was missing from `__all__`.

### Added — rate cards for the models a live deployment was calling

- `gpt-5.2` — $1.75 / $14.00 per MTok
- `gpt-5.2-pro` — $21.00 / $168.00 per MTok

Verified against https://developers.openai.com/api/docs/pricing and the model's
own page, both fetched 3 Sep 2026, STANDARD tier — which is what this facade
calls. The page lists Batch and Flex separately at exactly half ($0.875/$7.00)
and third-party aggregators were quoting that half as OpenAI's standard price:
the same trap the `grok-4.20` entry already records, and the same answer, the
primary source wins.

### Fixed — snapshot ids in the other provider's spelling were unpriced

`_DATE_SUFFIX` stripped only Anthropic's `-YYYYMMDD`. OpenAI dates its
snapshots `-YYYY-MM-DD` (the published id is `gpt-5.2-2025-12-11`), so an
echoed snapshot id missed the table even once the base alias was in it — a
missing rate card one string away from a present one. Both spellings now
normalize; nothing else does, so `gpt-5.2-turbo` still cannot borrow
`gpt-5.2`'s price.

## [0.18.0] — 2026-09-03

### Added

- `ProseContract.banned_patterns` — regular expressions that must not match
  anywhere in the text, the escalation from `banned_phrases`. A phrase list
  bans what somebody thought of: a live composer routed around a banned
  «на фото» inside a single attempt («по предоставленному фото определить
  невозможно», «по фото не указаны»), and every variant is the same register
  — the text treating the photograph as its source of knowledge instead of
  describing the item for sale. Answering that with more literals is
  whack-a-mole against a model with more spellings than the list has rows; a
  pattern states the shape once. Violations travel as
  `banned_pattern:<pattern>`, so the revision prompt names the rule that
  broke like every other code here.

  Patterns are folded (case, «ё»→«е») and compiled in `__post_init__`, so a
  malformed regex raises where the contract is DECLARED rather than an hour
  later on the first generated text that reached it. `banned_phrases` stays:
  an exact string is easier to read, impossible to get wrong, and right
  wherever it is enough. An undeclared field rejects nothing.

## [Unreleased]

## [0.17.0] — 2026-09-03

### Added — the schema constrains the shape; now something can constrain the register

A constrained decoder guarantees that `description` is a string. It
guarantees nothing about whether that string is the document the product
asked for or a chat turn about it, and from the caller's side the two are
indistinguishable — both are well-formed strings in the right field.

Observed on a live client fleet's listing composer, which asks a vision
model for a sale description: what came back was a caption of the
photograph («on the photo you can see three cameras and a lidar»), an
assessment hedged to the image rather than the item, and a closing offer to
keep the conversation going («if you need any more details from the photo,
I'll tell you»). Every one of those answers validated against its pydantic
model. None of them is a listing. A prompt asking for better is a request,
not a mechanism — the model declines it often enough to matter.

So `complete_json` grows two parameters. `validate` is a
`(result) -> Sequence[str]` returning violation codes, run on the object
the caller will actually receive (the typed instance when a model was
given) after pydantic has passed it. `max_revisions` is how many times a
rejected answer goes BACK to the model with its violations named — telling
it which rule it broke, which is the difference between a revision and a
re-roll of the same distribution. Both default to off, so every existing
call is byte-for-byte the call it was.

When the revisions run out and the answer is still rejected, the call fails
with the new `REASON_OUTPUT_REJECTED` and the violations attached. It is a
distinct reason because it degrades differently from a provider failure:
the transport is fine, the model is answering in a register the caller has
declared unusable, and retrying will not help.

`stapel_agent.safety.prose` ships the contract the composer case needed —
`ProseContract(max_chars, banned_phrases, reject_trailing_question,
banned_endings)` and `check_prose(text, contract) -> tuple[str, ...]`.
Matching folds case and «ё»→«е», because «ё» is optional in written Russian
and a banned phrase that misses half its spellings is a check that reports
clean because it cannot see. `max_chars` counts CHARACTERS: a one-line
title on a storefront is bounded by glyphs, and a Cyrillic title is not two
thirds the length of its Latin equivalent. `banned_endings` is separate
from `banned_phrases` on purpose — an offer to keep talking is a defect
only where it closes the text; the same words mid-sentence are ordinary
prose.

What is deliberately NOT here: any actual phrase list. The contract is a
mechanism and belongs in the library; the phrases are a fact about a
product's language and market and belong in the caller's settings. A
library shipping a banned-phrase list in Russian has guessed at somebody's
product.

## [0.16.2] — 2026-09-02

### Fixed — the LLM's proxy no longer leaks onto a different embeddings endpoint

0.16.1 made `EMBEDDINGS_PROXY` fall back to `OPENAI_COMPAT_PROXY`
unconditionally. The proxy belongs to the ENDPOINT: when
`EMBEDDINGS_BASE_URL` is explicitly set (a local TEI, a self-hosted vLLM),
riding the LLM's tunnel to it is exactly backwards — a deployment whose
OpenAI account needs a proxy found its local embedder unreachable through
that same proxy. The fallback now applies only while the base URL is also
falling back to `OPENAI_COMPAT_BASE_URL`; an explicit `EMBEDDINGS_PROXY`
always wins.

## [0.16.1] — 2026-09-02

### Fixed — the embeddings request now rides the proxy the LLM request rides

`OPENAI_COMPAT_PROXY` was honored by the LLM provider (`proxies=` on the
one request it makes, 0.13.3) and silently ignored by the OpenAI-compatible
EMBEDDINGS provider — a deployment whose OpenAI account is reachable only
through a proxy got working completions and unreachable embeddings from the
same settings block. The embeddings adapter now sends its one request with
`proxies=` too, reading the new `EMBEDDINGS_PROXY` (falling back to
`OPENAI_COMPAT_PROXY`, so a host already configured for the LLM wire
configures nothing extra). Same scope rule as the LLM side: the proxy rides
that request only, never a process-wide `HTTPS_PROXY`; `socks*` URLs need
`stapel-agent[socks]`.

## [0.16.0] — 2026-08-30

### Added — a merged guest keeps their prompt history

This package subscribed `user.deleted` and nothing else, so it had a
silent, wrong answer for the opposite event. `user.merged` (stapel-auth
0.30.0) fires when a guest account is folded into an account that already
exists on sign-in: `from_user_id` stops existing, and every row that named
it belongs to `into_user_id` now. Nothing is erased. Until this release the
guest's `PromptLog` rows kept pointing at an id that can no longer sign in
— invisible to the person who wrote them, and beyond the reach of any
erasure they could later request, because nobody ever requests an erasure
for an id nobody holds.

The failure had no symptom at the seam: nothing raised, nothing retried,
nothing was logged. `stapel_core.lifecycle.E001` (stapel-core 0.52.1)
reports exactly that silence, and `tests/test_user_merged.py` keeps the
check wired to this suite so the answer cannot go missing again.

**Merge policy — re-point, keep everything.** `actions.handle_user_merged`
moves `PromptLog.user_id` from the merged account to the survivor and
changes nothing else. A prompt log row is a metering record as much as a
content record — it carries the tokens and the cost the deployment already
paid for — so the survivor inherits the rows whole rather than a summary of
them. `workspace_id` is deliberately untouched: a merge joins two people,
not two tenants.

Idempotent by construction: the re-point filters on `from_user_id`, which
matches nothing once it has run, so the second delivery of an at-least-once
event moves 0 rows instead of doing the work twice. A payload missing an
id, naming one account twice, or carrying an id no column can parse is
logged and dropped — a raise would make the bus redeliver a message that
can never succeed. `ValidationError` is caught beside `ValueError` and
`TypeError` on purpose: a UUID column rejects a malformed key with
`ValidationError`, which is not a `ValueError`, and a handler that caught
only the latter would raise into the bus.

Contract: `schemas/consumes/user.merged.json`.

## [0.15.0] — 2026-08-28

### Fixed — the Deepgram rate card was seven weeks stale and 58% high

`stt/pricing/deepgram.py` was dated 2026-07-09 and priced a diarized
monolingual batch hour at **$0.408**. Re-read on 2026-08-28,
https://deepgram.com/pricing says **$0.258**:

| | card as shipped | page on 2026-08-28 |
|---|---|---|
| Nova-3 mono, pre-recorded, PAYG | $0.0048 / min | **$0.0043 / min** |
| Nova-3 mono, pre-recorded, Growth | $0.0042 / min | **$0.0036 / min** |
| Nova-3 multi, pre-recorded, PAYG | $0.0058 / min | **$0.0052 / min** |
| Nova-3 multi, pre-recorded, Growth | $0.0050 / min | **$0.0043 / min** |
| Speaker Diarization, pre-recorded | paid add-on $0.0020 / min | **Included** |
| Keyterm Prompting, Growth | not published (PAYG charged) | **$0.0012 / min** |

Two independent things were wrong, and they compounded: the base rate was
high, and a Speaker Diarization add-on was added on top of every batch
estimate. That add-on is a **streaming** line — the Pre-Recorded column reads
"Included" for both tiers — and our adapter always sends `diarize_model`, so
every diarized run this package priced carried it. `hourly_rate` for
`deepgram_nova3_default` goes 0.408 → **0.258**, and for
`deepgram_nova3_multi` 0.468 → **0.312**.

The card now records that this page has moved **twice** in seven weeks, in
opposite directions (2026-07-04 $0.0043 + included → 2026-07-09 $0.0048 +
add-on → 2026-08-28 $0.0043 + included). Each reading matched the page on its
day; the conclusion is that this vendor restates its public card between
quarters and the diarization line moves, so the constants are dated, not
authoritative.

**Breaking (0.x minor).** `NOVA3_DIARIZATION_ADDON_PER_MIN` and
`NOVA3_DIARIZATION_ADDON_PER_HOUR` are renamed
`NOVA3_DIARIZATION_ADDON_STREAMING_PER_MIN` / `_PER_HOUR` — same $0.0020/min
value, a name that says which product it bills. An unqualified constant for an
add-on this workload does not pay is how the overstatement stayed invisible.
`NOVA3_PRICING["nova-3"]` drops `diarization_addon_per_min_usd` /
`diarization_addon_growth_per_min_usd` (the Growth column reads "—" on the
current card, so there is no honest Growth streaming rate to carry) and gains
`diarization_batch_included`, `diarization_addon_streaming_per_min_usd` and
`keyterm_addon_growth_per_min_usd`.

`estimate_cost(..., diarization=...)` is KEPT and is now a no-op on the batch
rates — the caller and `ModelConfig.pricing_kwargs` should not have to change
with the vendor's packaging, and this line has already moved twice. It reads
`diarization_batch_included`, so a card that moves the add-on back changes one
boolean, not the arithmetic. `keyterm=True` on `tier="growth"` now bills the
published Growth rate instead of PAYG.

`RATE_CARD_VERSION` moves `deepgram_2026-07-09` → `deepgram_2026-08-28`, which
is what that string is for: an A/B whose arms were priced on the two cards is
machine-detectable.

### Changed — every other STT rate card re-verified, provenance stated per number

The sibling cards were last checked 2026-07-09..11. Each was re-read against
its vendor page on 2026-08-28:

- **AssemblyAI** — unchanged ($0.15/hr universal-2, $0.21/hr
  universal-3-5-pro, +$0.02/hr diarization). `universal-3-pro` is no longer on
  the page; the key stays priced at its last verified rate and now says so.
- **Soniox** — unchanged ($0.10/hr async, $0.12/hr real-time, diarization and
  LID bundled).
- **xAI** — unchanged ($0.10/hr REST, $0.20/hr streaming), re-read on
  https://docs.x.ai/docs/models rather than the WAF'd pricing page.
- **Xiaomi MiMo** — unchanged ($0.074/hr overseas, ¥0.5/hr domestic).
- **Gladia** — rates unchanged ($0.61/hr async, $0.75/hr real-time,
  diarization included), but the **free allowance changed**: the card said "the
  first 10 h per month are free" and the page now grants a one-time €50 credit
  with no monthly reset. Nothing is planned around it, but a recurring
  allowance that became one-time is exactly the drift that surprises a
  forecast.
- **Speechmatics** — Melia 1 $0.129/hr and Standard $0.24/hr re-confirmed;
  **Enhanced $0.40/hr and the "diarization included on all plans" claim were
  NOT** — that part of the page still renders only interactively and
  docs.speechmatics.com has no pricing chapter. The two rates now carry
  different verification dates in the file rather than one date that would
  imply both were read.
- **ElevenLabs** — `elevenlabs.io/pricing/api` answers **302 to a
  country-restriction help article** from here, so the price table could not be
  read. $0.22/hr is unchanged and its provenance is downgraded in the file to
  "last read on the official page 2026-07-01; not re-confirmable 2026-08-28".
  The official docs confirm only the shape (per audio-hour, Scribe v2);
  secondary write-ups agreeing on $0.22 are corroboration, not verification,
  and the file says so. A reader planning spend needs an invoice or a
  permitted region.

`SttProvider.cost_per_hour` for the Deepgram adapter follows the card
(0.288 → 0.258). It remains a registry ballpark; `stt/pricing/` is the number.

## [0.14.3] — 2026-08-23

### Fixed — an erasure pseudonymizes exactly the ids that NAME the subject

0.14.2 rewrote both id columns on every erasure, whatever the subject. That
made one member's account closure erase a **living tenant's** ability to read
its own ledger: the workspace never asked to be erased, and after the pass its
rows no longer answered to its own id. New `gdpr.PSEUDONYMIZED_COLUMNS` states
the rule per subject type:

| Subject | Rewritten | Left alone |
|---|---|---|
| `account` | `user_id` | `workspace_id` — the tenant is alive and its bill stays queryable by its own id |
| `workspace` | `workspace_id` **and** `user_id` | rows in every other tenant |

A workspace erasure takes the person too, because rows inside an erased tenant
have no tenant left to belong to; the people keep their accounts elsewhere and
those rows are untouched. Everything else is unchanged from 0.14.2 — content
scrubbed, `metadata` cut to the accounting keys, economics columns never read
or written, receipt counts = rows touched, idempotent because the subject's own
id is a pseudonym after the first run.

The metering test that sums a month's spend now takes the sum the way a bill
is taken — `filter(workspace_id="99")` after erasing one of its members — so
the tenant's link is asserted by the query it exists for, not by an equality
on a column.

Patch: no public surface removed, no setting changed. `PSEUDONYMIZED_COLUMNS`
is exported so a host can read the rule instead of inferring it.

## [0.14.2] — 2026-08-23

### Changed — the rule, stated once

**Erasure removes what a person wrote; the bill stays, without the person.**

0.14.0/0.14.1 deleted the `PromptLog` rows of an erased subject, and that was
the wrong half of the trade: deleting them silently restates closed reporting
periods, and "what did March cost" is not a question about whether the account
still exists. `gdpr.erase_subject` now, on every row it selects:

- **scrubs the content** — `prompt`, `system_prompt`, `response`,
  `error_message` (the same operation retention performs);
- **cuts `metadata`** to `gdpr.LEDGER_METADATA_KEYS`, an allowlist of the
  accounting dimensions this package writes (`provider`, `priced_by`, `model`,
  `size`, `n`, `batch_size`, `language`, …). Everything else goes: a caller's
  annotation, a provider's extra dict, and `audio`, which carries
  `AudioRef.describe()` — the URL of the subject's own recording. An allowlist
  and not a denylist, so a key nobody anticipated falls on the erasing side;
- **pseudonymizes `user_id` and `workspace_id`** — new `gdpr.pseudonymize`,
  an HMAC-SHA256 under the deployment's `SECRET_KEY`, prefixed `erased:`. This
  mirrors `stapel_video.presence.pseudonymize_user` exactly (one keyed funnel
  for the fleet, never a plain hash: a bare digest of a user id is a rainbow
  table away from being the id again). Stable, so one subject's rows stay one
  subject and per-subject totals keep their arithmetic; irreversible without
  the key; idempotent, so a redelivery cannot mint a second pseudonym and split
  a subject's history in two;
- **leaves the economics alone** — `cost_usd`, `cost_basis`,
  `audio_duration_ms`, the token counters, the model, the timestamps.

`counts` in the `gdpr.section.erased` receipt is now rows **touched** (it was
rows deleted). Idempotency is unchanged in kind: after the first run the
subject key matches nothing, so a redelivery receipts `0`.

**`AgentGDPRProvider.anonymize` is now `delete`** — the same function object,
not a copy. After the scrub the row is numbers plus a pseudonym, which is what
an anonymisation produces; `delete` is the implementation and `anonymize` the
alias, so nobody goes looking for a second one. (0.14.0 had split them, with
`anonymize` keeping the old scrub-and-unlink behaviour.)

`retention.purge_prompt_logs` is **unchanged**: it still only scrubs text on a
timer, and it does not touch ids — an old row still belongs to a live customer.

Two consequences, stated rather than discovered: rotating `SECRET_KEY` changes
future pseudonyms (a subject erased on both sides of a rotation gets two), and
pseudonymizing *both* ids means a row that named an erased person's workspace
can no longer be looked up by that workspace id either — per-tenant totals
still add up, but the link back to a living tenant is cut with the link to the
person.

Patch, not minor: no public surface is added or removed (`erase_subject`,
`OWNER`, `SUBJECT_TYPES` and the three handlers keep their signatures) and no
setting changes. What changes is what 0.14.1 did to a row, and 0.14.1 is a day
old. `gdpr.pseudonymize`, `gdpr.PSEUDONYM_PREFIX` and
`gdpr.LEDGER_METADATA_KEYS` are exported so a host can read a pseudonymized
ledger without re-deriving the scheme.

## [0.14.1] — 2026-08-23

Re-cut of 0.14.0, which was tagged and never published: its release job
failed on two of the new tests, which called `get_agent_beat_schedule()`
without celery installed. Celery is optional here and CI does not install
it, so the tests `importorskip` it now and the W017 coverage runs from a
literal beat entry on every matrix leg. The 0.14.0 content follows
unchanged.

The prompt ledger becomes an erasure owner that can be *reached*. Pre-1.0,
so a minor is where a changed default and a new public surface live: this
release changes `PROMPT_LOG_RETENTION_DAYS`, changes what `delete()` means,
and adds three comm handlers, a task module and a system check.

### Breaking

- **`PROMPT_LOG_RETENTION_DAYS` default 90 → 30.** The deletion-lifecycle
  spec puts every subject's purge SLA at 30 days; a library default that
  keeps customer content three times longer than the platform's own promise
  is a default that quietly breaks it. A deployment that wants the old
  window states `STAPEL_AGENT["PROMPT_LOG_RETENTION_DAYS"] = 90` — and now
  owns that number where a reviewer can see it. Text older than 30 days is
  scrubbed on the first purge run after the bump.
- **`AgentGDPRProvider.delete()` deletes the rows** instead of scrubbing the
  text and unlinking the subject. Erasure now means one thing on both paths
  (the comm subscriber below and the in-process provider run the same
  `gdpr.erase_subject`), where before an account erasure removed rows over
  comm and kept them in a monolith. **The metering columns of an erased
  subject's calls go with the row** — that cost leaves the ledger, and the
  trade is deliberate: an erasure request is a request to be gone.
  `AgentGDPRProvider.anonymize()` is unchanged and still does exactly what
  `delete()` used to do (scrub the text, drop `user_id`, keep the tenant and
  the counters) — that is the call for a host that wants the numbers without
  the person, and it is no longer a synonym for `delete`.

### Added — the erasure path is consumed, not merely declared

`actions.py` (this package shipped none) subscribes:

- **`gdpr.erasure.requested`** — deletes the `PromptLog` rows of the named
  subject and replies `gdpr.section.erased {owner, subject_type, subject_key,
  correlation_id, receipt_id, counts: {prompt_logs}}` in the same transaction
  as the delete, so a rollback takes the receipt with it. Idempotent: a
  redelivery removes nothing, receipts `0`, and repeats the same derived
  `receipt_id`.
- **`gdpr.owner.probe`** → **`gdpr.owner.alive {owner, subject_types}`**, from
  the same module as the erasure handler. That co-location is the whole
  evidence: an answer proves the erasure subscriber is consumed, not that a
  container is running (`gdpr.W006` and `GET /owners/health` read it).
- **`user.deleted`** — the deprecated account signal stapel-gdpr keeps
  emitting for one minor, routed through the same erase call. When it goes,
  no erasure logic goes with it.

**Subject types claimed: `account`, `workspace`** — declare
`STAPEL_GDPR["DATA_OWNERS"] = {"agent": ["account", "workspace"], ...}`.

**`meeting` is deliberately not claimed**, though the spec's table lists it
for this library. That row assumes the 0.12.0 metering columns carry a
meeting/recording correlation; they carry `user_id`, `workspace_id`,
`cost_usd`, `cost_basis` and `audio_duration_ms`, and no `llm.*` payload
accepts an entity id at all (the schemas are `additionalProperties: false`),
while `metadata` is written by this package, not the caller. There is no key
a meeting erasure could match on here, and an owner that claims a subject
type it cannot erase turns the health table into a false green. A real
meeting key means a nullable column plus a new optional field on every
`llm.*` schema — its own release, and it needs the host side to pass one.

Emits and consumes are declared in `schemas/emits/` and `schemas/consumes/`
(the fleet's comm-contract canon); MODULE.md gains an "Erasure" section.

### Added — the retention job in schedulable form

- **`tasks.py`**: `purge_prompt_logs` (a plain callable, additionally a
  celery `shared_task` under the stable name `stapel_agent.tasks.
  purge_prompt_logs` when celery is installed — it is NOT a dependency) and
  **`get_agent_beat_schedule()`**, the entry a host splices into
  `CELERY_BEAT_SCHEDULE` (canon: `stapel_gdpr.tasks.get_gdpr_beat_schedule`).
  The retention window and the management command have existed since the
  AGENT-02 audit; nothing shipped that a scheduler could reference.
- **`stapel_agent.W017`** — this process runs beat and has no entry for the
  purge. The ironmemo finding was not a wrong cadence but no entry at all,
  and a beat schedule that runs *something* looks exactly like one that runs
  *this*. `W014` narrows to its complement (no scheduler known at all), so
  the two never fire together: one gap, one warning, and W017's hint names
  `get_agent_beat_schedule()` instead of "wire something somewhere". Both
  are still silenced by `PROMPT_LOG_RETENTION_SCHEDULED = True` (an external
  cron) or `PROMPT_LOG_RETENTION_DAYS = None` (a stated decision).

### Fixed

- `tests/test_public_api.py`'s import-lock gate skipped `build/` and `dist/`
  but not dot directories, so a checkout with its own `.venv/` made it
  report every third-party package on disk — a gate that cries wolf gets
  switched off. It now skips them, as `tests/test_packaging.py` already did.

### Docs

- CONFIG.MD: the new `PROMPT_LOG_RETENTION_DAYS` default, plus a row for
  `PROMPT_LOG_RETENTION_SCHEDULED`, which the code has read since 0.9 and
  the registry never listed.

## [0.13.3] — 2026-08-22

### Added

- **`OPENAI_COMPAT_PROXY`** — an outbound proxy for the openai-compat
  provider alone (`socks5h://host:port` or `http://host:port`), passed as
  `proxies=` on that one request rather than as a process-wide
  `HTTPS_PROXY` that would also route every other fetch the service makes.
  SOCKS needs PySocks: new extra `stapel-agent[socks]`; a SOCKS URL without
  it is reported by `W016` at boot instead of failing on the first call.
- **`OPENAI_COMPAT_MAX_TOKENS_PARAM`** — `max_tokens` (default, what most
  compatible hosts accept) or `max_completion_tokens` (OpenAI's
  reasoning-era models — gpt-5.x — reject `max_tokens` with HTTP 400).
  A setting, not model-name sniffing: the dialect is a deployment fact.
  Any other value is a `W016`.

Filed from a client fleet: the owner's provider is OpenAI behind a
mandatory SOCKS5 proxy, and `gpt-5.2` needs the new parameter spelling.

## [0.13.1] — 2026-08-22

### Fixed

- **Check id collision: `stapel_agent.W009` renumbered to `W016`.** Two
  unrelated system checks shared one id — `check_providers`'s "default LLM
  provider is registered but not usable" warning (added in 0.6.2,
  2026-07-26) and `check_embedding_providers`'s "an `EMBEDDING_PROVIDERS`
  entry cannot be imported / is not an `EmbeddingProvider` subclass"
  warning (added in 0.4.0, 2026-07-24, so the older and unchanged holder of
  the id). Found by a client fleet deploy:
  `SILENCED_SYSTEM_CHECKS = ["stapel_agent.W009"]`, meant to quiet one of
  the two, silently silenced BOTH — including the unusable-default-provider
  warning the deploy still needed. The LLM-provider check now reports as
  `stapel_agent.W016`; the embedding-registry check keeps `W009` unchanged.
  A new test (`TestCheckIdsAreUnique`, `tests/test_extension_points.py`)
  statically pins every check id in `checks.py` to exactly one check
  function so this class of collision cannot regress silently again.

## [0.13.0] — 2026-08-22

### Added — a fourth rung on the model-size ladder: `xlarge`

The size vocabulary was `small` / `medium` / `large` — haiku-, sonnet- and
opus-class. Fable-class had nowhere to go. `xlarge` is now accepted
everywhere `large` was (the `llm.complete` / `llm.summarize` comm schemas,
the HTTP serializers, `resolve_model`), and `STAPEL_AGENT["MODELS"]` ships a
default of `claude-fable-5` for it, alongside the existing three. The ladder
is additive — no existing size, model id or default changed — so a caller
that never asks for `xlarge` sees no difference, and a host that only wants
the built-in default gets Fable at the top of the ladder for free.

### Added — an optional model-size entitlement ceiling

A four-rung size ladder is also a monetization axis: a free plan capped at
`small`, a paid one reaching further up. `STAPEL_AGENT["MODEL_SIZE_CEILING_ENTITLEMENT"]`
names an entitlement key (default unset — the seam is closed, and every
existing deployment's behaviour is byte-identical). Configured, `complete()`
asks `billing.check_entitlement` for that key before running a call: an
integer value is read as a 1-based rank into `MODEL_SIZES`, and a request
above the caller's resolved ceiling is **refused** —
`{"status": "failure", "reason": "model_size_ceiling_exceeded", "ceiling",
"requested_size"}` — never silently downgraded to a size nobody asked for.
`llm.complete` and `llm.summarize` (both surfaces — comm and HTTP) carry the
refusal through.

Failure posture mirrors the fleet's own precedent rather than inventing one:
no `user_id` on the call (nothing to ask billing *about* — a system-internal
call has no plan) and an unreachable `billing.check_entitlement` (not
installed, no route, a network failure) both apply **no ceiling** — the same
fail-open choice `ironmemo-backend`'s `recordings_ext.entitlement` gate makes
for its own billing seam, because refusing an otherwise-permitted size over
OUR OWN outage is a worse outcome than the cost of one over-generous call.
Both cases log; the identity case at `warning`, the seam-down case too. A
denial from billing that names no usable cap (a bool entitlement, an unknown
key or plan) is a plan-catalog misconfiguration, not a ceiling — logged at
`error`, still no ceiling.

Two new public functions: `services.resolve_size_ceiling(user_id,
workspace_id=None)` answers "what's the ceiling" without raising, for a
caller (Studio's architect, escalating up the ladder) that wants to clamp
*before* asking; `services.enforce_size_ceiling(...)` raises
`ModelSizeCeilingExceeded` — the specific, catchable class `complete()`
itself catches to build the refusal dict. `workspace_id` is accepted
everywhere the identity pair already travels but not consulted:
`billing.check_entitlement` is user-anchored only, and this package has no
mapping from an opaque `workspace_id` to a billing subject (unlike
stapel-workspaces, which resolves an org to its owner itself).

## [0.12.0] — 2026-08-21

### Added — the prompt ledger becomes a meter

A cost study of a product built on this package found that per-user cost
attribution was **structurally impossible**, and not for want of data. The
token columns had been complete since 7.16. Three separate holes kept the
table from answering the one question a credits system asks — "what did this
customer cost us this month" — and each of them was invisible on its own.

**1. Identity could not cross the bus.** `PromptLog.user_id` existed and the
HTTP views filled it. But product traffic reaches this package only over
comm, the `llm.*` schemas are `additionalProperties: false`, and none of them
carried an id. Every row written by a real pipeline stage read
`user_id = NULL`. A meter with no subject.

Every `llm.*` function that writes a row — `complete`, `translate`,
`transcribe`, `diarize`, `embed`, `rerank`, `summarize`, `generate_image` —
now accepts `user_id` and `workspace_id`. `llm.stt_catalog` does not: it
writes no row, so it has nobody to attribute. `workspace_id` is a new column
alongside `user_id`, because a team wallet and a person's usage are different
questions, and erasure drops the person while accounting keeps the tenant.

Both fields are **optional**, which is a compatibility promise rather than
indecision: a payload that omits them stays valid and no existing host
breaks. Both are **recorded only** — nothing here authorises, entitles,
debits or rate-limits on them. Metering is not billing, and a library that
quietly started enforcing on an id handed to it for accounting would be the
worse surprise.

**2. The cost was computed and thrown away.** `pricing.cost_fields()` already
returned `{cost_usd, cost_basis}` on every completion and attached it to the
response. Nothing stored it. New columns `cost_usd` (Decimal — these get
SUMmed over a billing period) and `cost_basis` now persist it, from the same
single computation that fills the caller's `usage`, so a dashboard and an
invoice cannot disagree about one call.

`cost_basis` is the part that matters: `provider_ticks` when the provider
reported its own charge, `pricing_estimate` from the rate card, `unpriced`
when neither. Without it, 0.0 means "free" and "we have no idea" at once, and
only one of those is good news. The stored figure is **as computed at call
time** and never revisited — re-pricing a year-old row against today's card
would quietly restate history. That was already `pricing.py`'s stated
discipline; now it is the schema's too.

**3. An STT row could not be reconstructed.** A transcribe row held
`prompt = "url:<host>"` and a `duration_ms` measuring how long *we* waited.
STT bills per hour of *audio*. In an audio product that is the largest single
spend path — 82 % of the cost of processing one hour, in the study that
prompted this — and it was unauditable.

A successful transcription now stores `audio_duration_ms`, the provider's own
reported audio length, and prices it: through the run's model config when the
catalog has one (so Deepgram's multilingual variant and hybrid diarization
stages are included, because the invoice will include them), through the
provider's bare rate card otherwise — which is how a host-registered adapter
gets priced at all. `metadata.priced_by` names the card that produced the
number, so it stays falsifiable a quarter later. Diarization records its
billable audio in the same column; pricing that surface is a separate step.

Every road to "we do not know" now logs. The silent zero was the defect.

### Added — rate cards that were missing

`pricing.py` gained `claude-opus-5`, `claude-fable-5`, `claude-mythos-5`,
`claude-opus-4-7`, `claude-opus-4-6` and `claude-opus-4-5`, verified against
the published list on 21 Aug 2026. Each was reachable from a running
deployment while unpriced, and an unpriced model does not stay isolated — it
sums into a total someone acts on. At Fable's $10/$50 one silent month is
real money.

**Claude Sonnet 5 keeps $2/$10, and now says why.** The entry carried a
comment describing that price as introductory "through 31 Aug 2026", with
$3/$15 scheduled for 1 September. That increase was **cancelled**: the
current price list states $2/$10 "is now the standard price" and that the
scheduled increase "will not occur". So this release adds no effective-date
machinery — a dated entry that flipped to $3/$15 next week would make every
row computed after August *wrong*, which is the opposite of what an
effective-date pair was wanted for. The number was already right; the comment
was not. A test now pins both the price and the absence of a clock in the
module.

`stt/pricing/xiaomi_mimo.py` is a new rate card: **$0.074 per hour of audio**
for `mimo-v2.5-asr` (overseas list; ¥0.5/h domestic, carried as a constant
and deliberately never converted — which list an account bills against is a
property of the account, and a pinned FX rate would produce a plausible
number for the wrong customers). This closes a live, key-configured, paid
`zh` route that was priced at nothing. The card is **not** in
`BUILTIN_STT_PRICING_MODULES`, because that map's keys are providers this
package registers and the MiMo adapter is host-side; a host wires the two
together with `register_stt_pricing_module("xiaomi_mimo", ...)`.

### Migration

`0006_promptlog_metering` — four nullable columns (`audio_duration_ms`,
`cost_usd`, `cost_basis`, `workspace_id`) and one index on
`(workspace_id, -created_at)`, the meter's own query. Purely additive; no
backfill, because there is nothing honest to backfill with. Rows written
before this release have no cost and never will: the price in force when they
were made is not recoverable from the row, and inventing one would be the
same fabrication the `unpriced` basis exists to prevent.

## [0.11.0] — 2026-08-21

### Added — an unfillable audio allowlist is loud at boot (`stapel_agent.W015`)

`STT_DOWNLOAD_ALLOWED_HOSTS` defaults to `[]`, and 0.10.0 made that default
fail closed: `stt.base._download` refuses the fetch with
`audio URL refused (no_allowed_hosts)` before any DNS lookup. Correct, and
invisible — the refusal happens per request, inside a worker, on a path most
callers treat as best-effort. The key is also an SSRF ceiling, so it is in
`conf.NO_ENV`: a deployment that tried to set it from the environment set
nothing at all.

The iron-agent dev stand ran that way for its whole life. Green system checks,
green deploy, and every single transcription refused.

A new system check, `stapel_agent.W015`, now says so at startup when the
allowlist is empty and no wildcard is declared. The message names the
setting, the fix, and the reason an environment variable did not work.

Warning, not error, and it silences on any of three explicit states — there is
no reliable way to ask "is STT in play" (the registry always carries the
built-in adapters and their credentials have no common seam), so the operator
declares the answer as with `W014`:

```python
# state the object store your presigned audio URLs point at (derive the host
# from the store's public URL — do not hardcode one stand's domain), or
STAPEL_AGENT = {"STT_DOWNLOAD_ALLOWED_HOSTS": ["files.example.com"]}
# accept any public host, or
STAPEL_AGENT = {"STT_DOWNLOAD_ALLOW_ANY_HOST": True}
# remove the STT surface entirely
STAPEL_AGENT = {"STT_PROVIDERS": {"whisper-http": None, ...}}
```

No runtime behaviour changed: a deployment that was transcribing keeps
transcribing, and one that was silently refusing now hears about it.

## [0.10.0] — 2026-08-14

The 2026-08-11 security audit: AGENT-01, AGENT-02 and AI-01, plus the
settings-hardening follow-up. Minor rather than patch — pre-1.0 semver reads
minor as breaking, and three of the notes below refuse configurations that
used to work.

### Changed — BREAKING: requires stapel-core >= 0.24.0 (was `>=0.15.11`)

`stt/base.py` imports `fetch_bytes` / `SafeFetchError` from
`stapel_core.net` on the `AudioRef.read_bytes` path — the AGENT-01 fix below
— so on an older core this package does not import at all.

**The floor this replaces was knowingly wrong**, and the commit that
introduced the import said so: `stapel_core.net` existed only on core's
`sec/audit-2026-08-11` branch, no released core contained it, and no honest
number could be written at the time. Core **0.24.0 is the release that ships
it** — the first, and so far only, tag containing that module — so this is
the first version of stapel-agent whose declared floor is true.

0.24.0 also covers a second dependency this audit created: `CACHE_POLICY` is
an `import_strings` key, and core 0.24.0 makes such keys implicitly
`no_env`, so an environment variable cannot pick the class mediating the now
tenant-scoped prompt cache. The `NO_ENV` list below closes the same door for
the keys core cannot know about, and core's new `stapel_core.conf.W001`
check names any environment variable a namespace is now refusing to read.

The superseded `>=0.15.11` floor (`stapel_core.schema_strict`, the
strict-subset transform the OpenAI-compatible transport applies before every
constrained call) remains satisfied.

### Security — UPGRADE NOTE: the settings namespace stopped taking orders from the environment

`AppSettings` falls back to `os.environ[KEY]` for every key a namespace does
not exclude, and `STAPEL_AGENT` excluded nothing. The key names are generic —
`CLI_BINARY`, `CACHE_POLICY`, `MAX_TOKENS`, `DEFAULT_PROVIDER` — so in a shared
pod, a compose file or a CI image a same-named variable belonging to something
else lands on them. What it lands on is not cosmetic: `CLI_BINARY` is argv[0] of
a `subprocess.run` (the Claude Code CLI provider), `CACHE_POLICY` is an
`import_strings` key and therefore an `import_string()` target, and the
`STT_DOWNLOAD_*` keys are the SSRF/DoS ceilings on a caller-supplied URL. A
ceiling an outsider can raise is not a ceiling.

`stapel_agent.conf.NO_ENV` now lists the code-selecting keys (`CACHE_POLICY`,
`CLI_BINARY`, every `*_PROVIDERS` overlay, every `DEFAULT_*` name, the STT
routes/pricing/model-config overlays), the ceilings (`MAX_TOKENS`,
`CLI_TIMEOUT`, `STT_TIMEOUT`, all four `STT_DOWNLOAD_*`, `DIARIZATION_TIMEOUT`,
`EMBEDDINGS_TIMEOUT`, `RERANK_TIMEOUT`) and the tenancy/retention gates
(`CACHE_LOOKUP`, `CACHE_TTL`, `CACHE_ALLOW_UNSCOPED`,
`PROMPT_LOG_RETENTION_DAYS`, `PROMPT_LOG_RETENTION_SCHEDULED`).

**If your deployment configures any of those through an environment variable it
will now be ignored** — silently, because the default takes over. Move it into
`settings.STAPEL_AGENT`, where it is reviewable. There is deliberately no
opt-out for this one: the whole point is that the value cannot be set from
outside the settings file. What is *not* closed, and still reads the
environment: `*_API_KEY`, `*_BASE_URL`/`*_URL` and the model names — those are
per-deployment credentials and endpoints and the environment is their canonical
channel.

### Security — UPGRADE NOTE: an empty audio-host allowlist is a refusal, not a wildcard

`STT_DOWNLOAD_ALLOWED_HOSTS = []` meant "any public host", so the deployment
that had configured nothing was the one that would fetch whatever host a
request named. Empty now means the audio download is **refused** — a fatal
`TranscriptionError` (`no_allowed_hosts`), raised before any DNS lookup, naming
both ways out.

**If you transcribe from URLs and have no allowlist, transcription of `url`
refs will start failing on upgrade.** Either list the origins you actually fetch
from:

```python
STAPEL_AGENT = {"STT_DOWNLOAD_ALLOWED_HOSTS": ["files.example.com"]}
```

or take the explicit opt-out, new in this release:

```python
STAPEL_AGENT = {"STT_DOWNLOAD_ALLOW_ANY_HOST": True}
```

The opt-out restores the previous behaviour and nothing more: https-only, the
private/loopback/link-local/CGNAT/metadata refusals, the per-hop re-validation,
the byte cap and the deadline all still apply. `path` and `data` refs are
unaffected. Both keys are in `NO_ENV`, so neither the ceiling nor its opt-out
can be flipped by an environment variable.

### Fixed — booleans no longer read backwards when they arrive as strings

`AppSettings` does no coercion, and `bool("false")` is True. An operator who set
`PYANNOTEAI_EXCLUSIVE=false` in the environment got exclusive diarization
anyway. Boolean keys in this namespace now go through accessors
(`conf.pyannoteai_exclusive`, `conf.stt_download_allow_any_host`,
`conf.prompt_log_retention_scheduled`) that read `1/true/yes/on` and treat
everything else as false — so a mis-typed switch fails closed instead of
silently inverting.

### Security — UPGRADE NOTE: an unscheduled retention policy is now loud (`stapel_agent.W014`)

`PROMPT_LOG_RETENTION_DAYS` has defaulted to 90 since the ledger landed, but the
only executor this package ships is the `purge_prompt_logs` management command,
and nothing registers a periodic task. A host that never wired cron kept every
prompt, system prompt and full response forever while its configuration said 90
days, and nothing anywhere said so (audit AGENT-02 follow-up).

`stapel_agent.checks.check_prompt_log_retention_is_scheduled` now raises
`stapel_agent.W014` at boot when the window is set and nothing is known to
enforce it. **Expect this warning on upgrade** unless you either:

- declare the job you already run —
  `STAPEL_AGENT = {"PROMPT_LOG_RETENTION_SCHEDULED": True}` (a Celery beat entry
  running `purge_prompt_logs` is detected and needs no declaration); or
- state that this deployment keeps the text indefinitely —
  `PROMPT_LOG_RETENTION_DAYS = None`, which this check does not report.

It is a Warning, not an Error: the gap is a compliance problem, not a broken
deploy. Silence was the wrong default, because an unenforced retention policy
looks exactly like an enforced one from the settings file.

### Security — the audio download is no longer a way into the private network

`AudioRef.read_bytes()` handed a caller-supplied URL to
`requests.get(url, timeout=600, stream=True)` and returned `resp.content`: any
scheme, any address, any redirect, any size. A producer that could put a URL in
a transcribe request could therefore reach cloud metadata, an internal admin
port or a link-local service, and could hold a worker for ten minutes while
filling its memory with an unbounded body (audit AGENT-01).

The fetch now goes through `stapel_core.net.fetch_bytes` — the fleet's single
guarded fetcher, https-only, every resolved address validated against the
private/loopback/link-local/CGNAT/metadata ranges, the socket pinned to the
validated IP, every redirect re-validated from scratch, the body capped
mid-stream and the whole operation bounded by a deadline. Writing a second
implementation here is what created the gap in the first place, so this
package consumes core's rather than growing its own.

Four new ceilings (`STT_DOWNLOAD_MAX_BYTES`, `STT_DOWNLOAD_TIMEOUT`,
`STT_DOWNLOAD_TOTAL_DEADLINE`, `STT_DOWNLOAD_ALLOWED_HOSTS`) configure it. The
per-call `timeout=` argument is now a request, not an authority: it may lower
the configured deadline and never raise it. The error taxonomy is unchanged
where it matters — a refusal or a 4xx is fatal (the next provider would fail on
the same ref), transport/deadline/5xx stay retryable.

### Security — a cached answer belongs to whoever asked for it

The prompt cache was keyed on content alone (prompt + system prompt + source),
so two tenants sending the same sensitive prompt shared one stored response and
its metadata (audit AGENT-02). `CachePolicy.lookup`/`store` now take the
caller's `user_id`, the default policy filters ledger rows on it, and a call
that carries no scope does not use the cache at all unless its source is listed
in the new `CACHE_ALLOW_UNSCOPED` — fail closed, because there is no version of
this where the sharing is the safe outcome. A host policy whose signature
predates the argument cannot tell tenants apart, so it is switched off with a
warning instead of trusted.

### Security — the prompt ledger answers subject requests, and its text expires

`PromptLog` holds prompts, system prompts and full responses in plaintext, and
appeared in no export, no erasure path and no retention job (audit AGENT-02).

- `stapel_agent.gdpr.AgentGDPRProvider` (section `agent`) is registered into
  `stapel_core.gdpr.gdpr_registry` from `AgentConfig.ready()`, like every other
  package's provider.
- `stapel_agent.retention.purge_prompt_logs()` and the `purge_prompt_logs`
  management command scrub row text older than the new
  `PROMPT_LOG_RETENTION_DAYS` (default 90).

Both scrub rather than delete: the text is customer content, the token counters
are the finance ledger, and a row without its text holds no personal data.

### Security — a plain summary now carries the structured-output canary

A schema-constrained answer proves its shape by construction; a prose summary
cannot, and the plain path had no equivalent check (audit AI-01). New
`safety.markers.detect_structured_output_leak()` names the scaffolding a model
can spill into prose — tool-call envelopes, parameter tags, chat-template
tokens, schema echoes — and `services.summarize()` runs it on both the
single-shot and the merge path. A hit adds
`safety: {"structured_output_leak": [...], "untrusted": True}` to the envelope
so a consumer can refuse to render it. It is a flag, not a filter: the text is
returned unchanged.


## [0.8.1] — 2026-08-02

### Packaging / contract

- `surface` section of `docs/capabilities.json` now names all four `safety/`
  gate functions (`detect_pwned_markers`, `redact_markers`, `sanitize_for_rag`,
  `redaction_gate`) with a curated "when to reach for this" line, derived by
  AST walk from `surface_roots` and drift-gated by `tests/test_capabilities_surface.py`
  (requires `stapel-tools>=0.22.0`). The rest of `docs/capabilities.json`
  remains hand-written — this module has no gate registry or `docs/schema.json`.
- `docs/llms.txt` — the fifth contract artifact — is now emitted, drift-gated
  by `make contract`/`contract-check`, badged in the README, and listed in
  `package-data` so it ships in the wheel.
- `package-data` also now carries `docs/capabilities.json`, `docs/flows.json`,
  `docs/errors.json` and `CONFIG.MD` so `stapel-catalog --from-installed` can
  see them.
- Badge canon + Python 3.14 classifier + migration-lint enabled in CI.

## [0.8.0] — 2026-07-30

### Fixed
- **The transcript is verbatim again (xAI, Deepgram).** `model_configs`
  advertised `filler_words: True` on the xAI and Deepgram profiles and neither
  adapter ever sent it. Both providers default that knob to *false*, and false
  does not merely tidy the text — it **removes** um/uh from the transcript
  **and from `words[]`**, with nothing in the response to say it happened. So
  0.7.1 promised evidence of what was said and delivered an edited version of
  it, undetectably. Both adapters now send `filler_words=true` on every
  request; a caller who wants the provider's editing asks for it explicitly
  through `provider_options`.
- **ElevenLabs: a word with no timing is dropped, not stamped `t=0`.** The
  mapper turned a null `start` into `0.0`, which invents a measurement, moves
  the word to the opening second of the meeting and folds it into the first
  speaker's turn. The research harness this adapter was ported from drops such
  a token and lets the validator report it — and `validation.elevenlabs` has
  always said `UNTIMED_WORD ... (dropped by mapper)`. Now it is true.
- **Gladia solaria-3 refuses an impossible run before the billable call.** The
  catalog's solaria-3 entry warned that "RU/UK/auto/multi are refused before
  any billable call" while the adapter had no such check: the run was uploaded,
  created (the billing event) and only then rejected by the provider. The
  single-language gate (EN/FR/DE/ES/IT, no auto-detect) is now in the adapter,
  ahead of the upload — ported verbatim from the harness.
- **Soniox reports a language.** `enable_language_identification` was declared
  in the catalog and never sent, so `NormalizedTranscript.language` was
  structurally always `None` for this provider. It is now always requested (it
  is bundled in the async price, and it is the only language signal Soniox
  emits — per token, never globally): the transcript language is the majority
  language of its words, and a token language switch is a word boundary, as
  the adapter's own docstring already claimed.
- **Catalog entries that no adapter could honour.** `adapter_kwargs` said
  "constructor kwargs" and named `model=`, which none of these adapters accept
  — so `gladia_solaria3` and `speechmatics_batch_standard` would have run
  solaria-1 and melia-1. It now names the real seam (`speech_model`, the
  registration pin), and every config that has a model pins it instead of
  inheriting whatever a host's `*_MODEL` setting says. The AssemblyAI profiles
  advertised the plural `speech_models` array of a newer docs generation while
  the adapter sends the singular `speech_model`; the catalog now documents this
  adapter (`punctuate` / `format_text` included). Deepgram's `paragraphs` is
  gone from the catalog rather than added to the request: its only effect is a
  response block this library drops.

### Added
- **A conformance gate for the catalog** (`tests/test_stt_registry_conformance
  .py`). Every defect above is one defect: `provider_params` claims "what the
  adapter actually sends" and nothing checked it, so five entries drifted at
  once and the drift shipped. The gate drives each config's own adapter over a
  stub transport, collects every parameter that reached a request — query,
  JSON body, multipart fields, nested config objects, even Speechmatics'
  JSON-in-a-form-field — and compares it with `provider_params` in both
  directions. A new entry cannot pass quietly: its provider needs a stub, and
  every key it declares has to appear on the wire. Its limit is stated in the
  module docstring: it proves what we ASK for, never what the provider does
  with it — that still needs a live call.

## [0.7.1] — 2026-07-30

### Added
- **`stapel_agent.safety.redaction`** — refuse to persist an artifact that
  contains a secret. The guard was sitting in one product's recordings app,
  where it had nothing to do with recordings: an artifact is assembled from a
  prompt, a model response and the provenance of the call, and every one of
  those has been observed to carry a key at some point — a prompt echoing an
  environment dump, a provider error string quoting the request, a debugging
  field someone added and forgot. Once written the secret is in a database
  row, in every backup of it, and in whatever the staff-facing view renders.
  So it belongs next to `detect_pwned_markers` / `sanitize_for_rag`: any
  library that persists a model's output needs it. The check is deliberately
  dumb — does the serialized text contain the VALUE of an environment variable
  that looks like a credential — because a clever detector that misses is
  worse than a blunt one that does not. `RedactionError` names the offending
  variable and never its value, since an exception message is itself a thing
  that gets logged. The three knobs (`KEY_ENV_SUFFIXES`, `KEY_PREFIXES`,
  `MIN_SECRET_LEN`) are module-level and public: a host whose secrets are not
  named `*_API_KEY` has to be able to say so, and reading a settings block
  would make the cheapest guard in the library depend on Django.
- **`stapel_agent.stt.model_configs`** — a provider name does not identify a
  run and therefore cannot price one. Deepgram bills $0.408/hr monolingual and
  $0.468/hr multilingual over the *same* wire model `nova-3`; AssemblyAI is
  $0.17/hr on Universal-2 and $0.23/hr on Universal-3.5 Pro. A `ModelConfig`
  names the combination that is actually billed and actually reproducible:
  provider + model + the params the adapter really sends + `adapter_kwargs`
  (how two configs of one provider differ by *model*) + `pricing_kwargs` (the
  price variant for configs that share a wire model). `resolve_config()`
  attributes a run that named only a provider — which is what every
  pre-catalog caller does. Eleven shipped configs across the seven priced
  providers, plus three hybrids whose speaker turns come from pyannoteAI
  instead of the STT response. Merge semantics as everywhere else
  (`BUILTIN_STT_MODEL_CONFIGS` ← `STT_MODEL_CONFIGS` ←
  `register_stt_model_config`).
- **A config carries no price.** The obvious shape — a `pricing_per_hour`
  field copied from the rate card — is two copies of one truth, and the copy
  drifts silently: the card moves, the catalog keeps quoting last quarter, and
  nobody finds out until an invoice disagrees with a dashboard.
  `hourly_rate(config)` and `estimate_cost(config, duration_ms)` ask the
  rate-card module instead, so the card and the estimate are one computation
  by construction. Which module prices which provider is its own registry
  (`stt.pricing.pricing_module()`, `BUILTIN_STT_PRICING_MODULES` ←
  `STT_PRICING_MODULES` ← `register_stt_pricing_module`), keyed by the STT
  provider registry name so a config, its adapter and its rate card can never
  name different providers. `whisper-http` is deliberately unpriced and
  resolves to `None`: a self-hosted endpoint costs something, we just do not
  know what, and a $0 stub would report someone's GPU bill as free. A host
  with negotiated rates registers a module rather than editing numbers into
  config objects.
- The pricing tests that could not travel with the 0.6.5 port arrived with the
  registry they were waiting for (`tests/test_stt_model_configs.py`), plus
  direct coverage for two functions the upstream harness never covered: the
  Deepgram `RATE_CARD_VERSION` provenance stamp and pyannote's 20-second
  minimum charge. Three upstream assertions are not reproduced and the module
  docstring says why — measured WER has no home without the corpus it was
  measured on.

### Notes
- `SttProvider.cost_per_hour` still carries the older hand-written
  per-provider ballparks that `llm.stt_catalog` surfaces (ElevenLabs 0.40,
  Soniox 0.36) where the rate-card modules say 0.22 and 0.10. Nothing changed
  there in this release; where the two disagree, the module is the one with a
  dated source line.

## [0.7.0] — 2026-07-29

### Added
- **`stapel_agent.pricing`** — what a completion cost, with the provenance of
  every price. Each entry carries the source it was read from and the date it
  was read, because a price without a provenance line is a number someone
  remembered, and model prices move. An unknown model returns `0.0` with a
  warning and `cost_basis: "unpriced"` — never a guess, and never
  indistinguishable from free, since a fabricated cost does not stay isolated:
  it gets summed into a total someone acts on.
- **The reasoning-token trap, measured.** Providers disagree about whether
  reasoning tokens are already inside the completion count: xAI's *excludes*
  them, OpenAI-style providers *include* them. Estimating from completion
  alone under-counts every xAI run; adding reasoning unconditionally
  over-counts everywhere else by the same amount. `billed_output_tokens()` is
  the single place that knows which, and the finding is carried over verbatim
  from the harness that measured it.
- A charge the provider reported (xAI ships an exact bill in ticks) wins over
  our estimate, and `cost_basis` says which was used. An estimate that silently
  replaces a known figure is how a ledger drifts from an invoice.

### Changed
- **BREAKING** — the `usage` dict returned by `llm.complete` (and by
  `services.complete`) gained fields. It used to carry `input_tokens` and
  `output_tokens` only, while the provider had already measured reasoning and
  cache tokens and the ledger row was storing them. Callers therefore saw a
  smaller number than the invoice, and reasoning tokens are billed. It now
  carries the full breakdown plus `cost_usd`, `billed_output_tokens` and
  `cost_basis`. Code that compared the whole dict for equality needs updating;
  code that reads keys does not.

## [0.6.7] — 2026-07-29

### Added
- **`llm.complete` accepts a `schema`.** Constrained decoding existed in-process
  and stopped at the service boundary: a caller in another service could only
  ask for JSON in prose and hope. That is the one place a malformed answer is
  hardest to recover from — the caller has no access to the provider, no way to
  retry with a tighter constraint, and nothing but text to inspect. Pass the
  schema as pydantic emits it; the transport applies the strict-subset
  transform itself, so callers do not each carry a copy of that rule.

### Fixed
- **The committed function contracts had drifted from the code, and the file
  wins.** `schemas/functions/*.json` are not documentation: `autoload_schemas()`
  registers them at startup and they *override* the in-code schema. So the file
  is what validates a caller's payload while `functions.py` is what a reader
  believes — and when they part, the failure is quiet and actively misleading.
  Adding `schema` to `llm.complete` in Python changed nothing on the wire;
  every call was rejected for a property visible in the source. Four contracts
  were already drifted this way (descriptions on `llm.complete`, `llm.diarize`,
  `llm.generate_image`, `llm.transcribe`), in both directions — some richer in
  the file, some in the code. The richer text won in each case; nobody's
  wording was dropped.
- The files are now generated (`make contract`) and gated
  (`make contract-check`, plus `tests/test_function_contracts.py`, which checks
  both directions: a contract with no function, and a function with no
  contract). Two copies of one truth were the defect; one source and a
  generated copy is the fix.

### Changed
- `to_strict_subset` now lives in `stapel_core.schema_strict` (added in
  `stapel-core` 0.15.11). `stapel_agent.schema_strict` re-exports it, so
  nothing breaks. It moved because it is a pure JSON Schema transform and a
  caller that wants to inspect what will really go on the wire — before paying
  for the call — should not have to import the LLM library to do it.
- Requires `stapel-core>=0.15.11`.

## [0.6.6] — 2026-07-29

### Fixed
- **`strict: true` was going out with a schema strict mode rejects.** The flag
  is what turns structured output into a decoder *constraint* rather than a
  hint, but the endpoints that honour it accept only a narrow subset of JSON
  Schema — and one rule of that subset is that every object lists every
  property in `required`. Pydantic omits any field that has a default, which is
  correct JSON Schema and wrong on this wire. So a single defaulted field meant
  the request was rejected before a token was generated.
- The trap is that the schema *looks* ready. `extra="forbid"` supplies
  `additionalProperties: false`, which is the rule everyone remembers, while
  `required` quietly stays short and nothing local complains. It surfaces only
  as an HTTP error from the provider, on the model that finally has a default.
- `stapel_agent.schema_strict.to_strict_subset()` performs the transform:
  all-required and `additionalProperties: false` on every object node,
  recursing through `$defs`, `items` and the combinators, and dropping the
  constraint keywords the subset does not accept (`minLength`, `pattern`,
  `maxItems`, …). Dropping those loses no safety — the wire schema shapes the
  decoder, the response is re-validated client-side against the real model, and
  only the first has to fit the subset.
- Applied at the OpenAI-compatible transport, not at the caller's model. The
  all-required rule genuinely changes what is asked for — an optional field
  becomes one the model must emit — so it belongs to the transport that demands
  it. The Anthropic path derives its own format from the raw schema and never
  sees the transform. The caller's schema is deep-copied, because the same
  model is reused across calls and transforming in place would hand the second
  call a schema with its constraints already stripped.
- Ported from the harness, where it was written after live calls failed on
  exactly the schema pydantic emits by default, and measured against four
  provider families.

## [0.6.5] — 2026-07-26

### Added
- **Provider pricing** — `stapel_agent.stt.pricing` (assemblyai, deepgram,
  elevenlabs, gladia, soniox, speechmatics, xai_stt) and
  `stapel_agent.diarization.pricing` (pyannote). Published rate cards with the
  date each rate was last checked against the vendor's page, and an
  `estimate_cost()` that returns **None** for an unpriced model rather than a
  fabricated `$0.00` — a made-up zero is worse than an admitted unknown,
  because it adds up silently. Ported byte-for-byte with their tests.
- `complete_json(..., schema=<pydantic model class>)` — the constraint is
  derived from the model and the answer is validated back into it, so
  `result` is a typed instance. A schema hand-written next to a type is two
  statements of one truth; this makes it a projection of the type instead.
  A response that does not fit is a **failure**, and an extra field the model
  forbids is a failure naming the field — not a silent drop.

### Notes
- The library deliberately does not inject `additionalProperties: false` into
  a supplied schema. Strict modes require it and pydantic emits it only for
  models declaring `extra="forbid"` — but quietly tightening a contract the
  caller handed us is how a library starts lying about its inputs. The
  docstring says to declare it; a test proves it survives the derivation.
- Some of the upstream pricing tests could not travel with these modules yet:
  they need a model registry and adapter registry that have not been ported.
  Recorded as debt in the port ledger rather than dropped.

## [0.6.4] — 2026-07-26

### Added
- **Provider-response validation** — `stapel_agent.stt.validation` (assemblyai,
  deepgram, elevenlabs, gladia, soniox, speechmatics, xai_stt) and
  `stapel_agent.diarization.validation` (pyannote). Each checks the structure
  and timestamps of a raw provider payload BEFORE it is mapped into a
  `NormalizedTranscript`, and **returns** issues (`error` | `warning`) instead
  of raising, so a caller can gate on errors while merely surfacing warnings.
  Ported byte-for-byte from the iron-benchmark harness together with the tests
  that pin them: the eight files differ from each other because the providers
  differ, and unifying them would erase measurements, not duplication.
- **`stapel_agent.safety`** — injection-marker detection and the two
  context-specific sanitizers (`redact_markers` for auto-escaping template
  engines, `sanitize_for_rag` for text about to re-enter a prompt). Also
  ported byte-for-byte, including the record of why pre-template HTML
  encoding was removed (it double-escaped).
- `tests/test_packaging.py` — the hand-written `[tool.setuptools] packages`
  list must cover every subpackage in the tree, in both directions. A
  forgotten entry breaks nothing locally and ships a wheel with the module
  absent; the first symptom is an ImportError in someone else's deployment.

### Changed
- **pydantic is now a dependency.** The validators declare their issue types
  as pydantic v2 models, and the contracts arriving next use `extra="forbid"`
  plus field/model validators whose exact behaviour IS the contract — an
  unknown field must fail loudly rather than be dropped. Re-expressing them as
  dataclasses or DRF serializers would be a rewrite that silently changes what
  "valid" means. Internal DTOs stay dataclasses and the HTTP edge stays DRF:
  pydantic is for untrusted structured text (LLM output, provider payloads,
  on-disk artifacts), which is a boundary neither of the other two covered.

## [0.6.3] — 2026-07-26

### Added
- **Schema-constrained output** — `complete(..., schema=<JSON Schema>)` and
  `complete_json(..., schema=...)` constrain the decoder instead of asking for
  JSON in prose. `AnthropicProvider` sends `output_config.format`
  (`{"type": "json_schema", ...}`); `OpenAICompatProvider` sends
  `response_format.json_schema` with `strict: true`. Backends advertise the
  capability with the new `LlmProvider.supports_schema` flag, and the kwarg
  travels only to backends that set it — pre-schema subclasses are untouched.
- `complete_json` drops the injected JSON-API system prompt when a schema is in
  force: that prompt exists to coax an unconstrained model into JSON, and with
  a constraint it only spends tokens restating what the decoder enforces.

### Changed
- A `schema=` request to a provider without `supports_schema` is a **failure**,
  not a warning-and-continue. "Ask nicely for JSON and parse whatever comes
  back" is a different capability and must not stand in for a constraint:
  measured on the iron-benchmark harness (2026-07-03), the prompt-only path
  returned valid JSON whose single `summary` field held the whole answer as
  pseudo-XML while every structured field came back empty. It parsed. It was
  wrong. The caller could not tell — so the call now fails instead.
- Schema calls bypass the prompt cache (lookup **and** store), like image
  calls: the cache key is text, and it can see neither the pixels nor the
  requested shape.

## [0.6.2] — 2026-07-26

### Added
- **`LlmProvider.configuration_error()`** — a backend answers for itself
  whether it can serve a call yet (missing key, missing optional package,
  missing CLI binary), read lazily from settings, never at import. The library
  keeps no table of who needs which credential: that copy would drift the
  moment a provider changes.
- **`stapel_agent.W009`** — the default LLM provider is registered but not
  usable. `check_providers` only ever proved DEFAULT_PROVIDER *resolves*; the
  ironmemo stand defaulted to `anthropic` with an EMPTY `ANTHROPIC_API_KEY`
  and no `claude` binary, so checks were green while every `llm.complete` /
  `llm.summarize` call raised `ProviderError` — invisibly, because the fleet's
  one caller (stapel-recordings' summarize step) is best-effort by design and
  completed each recording with an empty summary. Warning rather than Error on
  purpose: a deployment may install this app for STT/embeddings alone and
  never make a text call — blocking those would be a false alarm about a
  surface nobody uses. The hint names the silent consequence explicitly.

## [0.6.1] — 2026-07-26

### Added — `llm.embed` accepts a per-call `model`

Found on app.ironmemo.com when vector search was switched on:
stapel-recordings puts `model` into the `llm.embed` payload whenever its
embeddings model is configured, and `EMBED_SCHEMA` declared only
`texts`/`provider`/`timeout_seconds`/`provider_options` with
`additionalProperties: false` — so **every** embed call died with
`SchemaValidationError("'model' was unexpected")`. The stand worked
around it by leaving `RECORDINGS_EMBEDDINGS_MODEL` empty; the contract
was the bug.

`model` is now part of the contract (comm schema + the committed
`schemas/functions/llm.embed.json` + `EmbedRequest` + `POST
api/v1/llm/embed`) and travels down to the provider as
`EmbeddingProvider.embed(model=...)`, winning over the registration pin
and the configured default. This is a caller's decision on purpose:
vectors from different models are different spaces, so an indexer that
stamps rows with a model and filters searches by it has to be able to
ask for that exact model. Adapters that cannot select one
(`embeddings-http` — the shim's model is fixed server-side) log the pin
as ignored and never echo it back; `embeddings.model` stays what
ACTUALLY ran. The kwarg travels only when requested, so embedding
adapters written against the previous signature keep working.

### Fixed — startup deadlock in the registry packages (Python 3.14)

iron-agent answered 502 right after `up -d` until someone restarted it:
`runserver` runs Django system checks on `django-main-thread` while the
autoreloader's main thread imports the root URLconf, and both walk
`stapel_agent.*`. The six registry packages had `from .base import X` in
their bodies, so a thread entering through `stapel_agent.<pkg>` held
lock(pkg) while taking lock(pkg.base), and a thread entering through
`stapel_agent.<pkg>.base` took the same two in the opposite order (the
import machinery loads the parent INSIDE the submodule's lock). Python
3.14 raises `_DeadlockError` on that inversion — the server thread died,
the container stayed up, nothing listened on 8000.

The base class is now imported inside `register_*_provider()`, so no
package body holds its own lock while acquiring a submodule's. Two tests
keep it that way: an AST invariant over every `__init__.py` and a
clean-interpreter check that importing a registry does not pull its
`base` module.

### Added — `error-keys/` is finally mounted

`AgentErrorKeysView` has existed since the port but no `urls*.py` ever mounted it — in
*any* stapel library. stapel-translate's `error_collector` polls
`/{prefix}/api/v1/error-keys/` on every service, so the whole endpoint class
answered 404 from Django's URL resolver and the collector harvested nothing
while reporting a plain `HTTP 404`. It is now mounted in `urls_v1.py` at
`error-keys/` (v1 canon), service/staff-gated as the base view declares.

Deliberately **not** in the contract triad: `ErrorKeysView` sets
`schema = None` and `/error-keys` is on the flows allowlist, so `make
contract` is a no-op diff — this is infrastructure, not product surface.

## [0.6.0] — 2026-07-25

Minor (**behaviour change in AssemblyAI biasing**): a second diarization
backend — the pyannoteAI cloud job API — and an end to silent
non-biasing on models that do not honor `keyterms_prompt` for the
requested language.

### Added
- **`pyannote-cloud` diarization adapter**
  (`diarization/providers/pyannote_cloud.py`): the billed
  api.pyannote.ai job API next to the existing self-hosted
  `pyannote-http` shim. Flow: `POST {base}/media/input` → presigned
  `PUT` (skipped entirely when the `AudioRef` already carries an http(s)
  URL — pyannoteAI fetches it server-side) → `POST {base}/diarize`
  (**the billing event**) → poll `GET {base}/jobs/{id}`. Output maps
  through the same `turns_from_segments` contract as the self-hosted
  adapter, so callers see one `NormalizedDiarization` shape.
  Two opinionated, overridable pins: `model` defaults to `precision-2`
  (the flagship — the open-weights ladder 3.1 < community-1 <
  precision-2 is a different quality point, and mixing them invalidates
  any measured comparison) and `exclusive` defaults to `True` (the
  non-overlapping speaker layer; `provider_options={"exclusive": False}`
  returns the raw one, and the untouched response is always in `raw`).
  Speaker-count knobs validate BEFORE the billable call
  (`num_speakers` XOR `min`/`max` bounds); the submit is never
  auto-retried here — transient failures surface as
  `RetryableDiarizationError` and the caller's retry policy (where the
  spend cap lives) decides whether to pay again. `billable_seconds()` is
  the pure per-second-with-20s-floor helper for host cost models.
- Settings `PYANNOTEAI_API_KEY` / `PYANNOTEAI_BASE_URL` (default
  `https://api.pyannote.ai/v1`) / `PYANNOTEAI_MODEL` (`precision-2`) /
  `PYANNOTEAI_EXCLUSIVE` (`True`). The key is deliberately SEPARATE from
  the self-host `PYANNOTE_API_KEY`: same vendor name, different service,
  and one shared setting silently sends a self-host bearer to the cloud
  (or back).
- `AssemblyAIProvider.keyterms_supported_for(language)` +
  `KEYTERMS_LANGUAGES` — the documented per-model coverage map
  (docs survey 2026-07-24).

### Changed
- **AssemblyAI reports biasing honestly outside the model's keyterms
  coverage.** `keyterms_prompt` is honored by universal-3.5-pro (the
  `best` alias) for its own six native languages (en/es/de/fr/pt/it);
  other languages fall back internally to Universal-2, where the
  parameter is Beta and English-only. Sending terms outside that
  coverage produced the worst failure available: a successful request,
  ignored terms, and `biasing.applied: true` — silent non-biasing no
  downstream invariant can catch. Now the parameter is not sent and the
  block reports `applied: false` with every term counted as truncated
  (the `unsupported_biasing` shape). Unknown model names and
  auto-detected language keep the previous send-and-count behaviour;
  hosts with better information override `keyterms_supported_for` per
  registration or force the raw parameter via `provider_options`.

## [0.5.0] — 2026-07-24

Minor: a new generic **rerank** seam — query-vs-documents relevance
scoring — layered end-to-end the way embeddings is (ABC + normalized
dataclass + error taxonomy → provider adapters → registry + settings →
service + PromptLog → comm function + HTTP endpoint + committed
schema). Core stays generic: parameter wiring per API lives here;
retrieval, chunking and final cutoff policies stay app-layer.

### Added
- **Rerank seam** (`rerank/base.py`):
  `RerankProvider.rerank(*, query: str, documents: list[str],
  top_n=None, timeout_seconds=None, provider_options=None) →
  NormalizedRerank` (`provider`, `model`, `results:
  [RerankResult(index, score)]` — **sorted by score descending**,
  `usage`, `raw` — scores stripped from raw, never stored twice).
  `index` = position in the INPUT documents list — the caller joins
  back positionally; **documents never round-trip in the response**.
  Input gate `require_rerank_inputs`: empty/non-string query, empty
  batches, non-string/empty-string documents and non-positive `top_n`
  are fatal BEFORE any provider call. Result gate `rank_results`: an
  out-of-range index, a duplicate index or a count above the input
  size is a loud fatal failure, never a misaligned join; `top_n`
  truncates AFTER the sort, uniformly for every adapter.
  Per-registration `rerank_model` pin mirrors the STT
  `speech_model`/embeddings `embedding_model` canon.
  `RerankError(ProviderError)` fatal vs `RetryableRerankError`
  (429/5xx/timeouts).
- **Rerank adapters**: `deepinfra-rerank`
  (`rerank/providers/deepinfra.py` — the DeepInfra inference dialect,
  `POST {RERANK_BASE_URL}/inference/{model}` with **paired arrays**
  `{"queries": [query]×N, "documents": [...]}` → `{"scores": [...]}`;
  the wire body is built by the pure `build_rerank_request()` so a
  live-verification fix is one edit — the shape is encoded from
  DeepInfra's documented reranker interface for
  `Qwen/Qwen3-Reranker-8B` and marked [НЕ ВЕРИФИЦИРОВАНО live] in the
  module docstring; base URL default `https://api.deepinfra.com/v1`,
  `RERANK_MODEL` default `Qwen/Qwen3-Reranker-8B`, Bearer
  `RERANK_API_KEY` required) and `rerank-http`
  (`rerank/providers/http_server.py` — the TEI `/rerank` dialect:
  `POST {RERANK_HTTP_BASE_URL}/rerank` `{"query", "texts"}` →
  `[{"index", "score"}, ...]`; re-sorted here, model attribution None
  — fixed server-side, never pretended; the keyless self-host
  fallback). Settings: `RERANK_PROVIDERS` / `DEFAULT_RERANK_PROVIDER`
  (merge-registry canon + `register_rerank_provider()`),
  `RERANK_TIMEOUT`, `RERANK_BASE_URL`, `RERANK_API_KEY`,
  `RERANK_MODEL`, `RERANK_HTTP_BASE_URL`.
- **Surfaces**: `llm.rerank` comm function (+ committed
  `schemas/functions/llm.rerank.json`), HTTP endpoint
  `POST api/v1/llm/rerank` (DTO + serializer validation: non-empty
  `query`/`documents`, `top_n ≥ 1`, positive timeout — new error keys
  `error.400.empty_query`, `error.400.empty_documents`,
  `error.400.invalid_top_n`). Envelope mirrors embed's:
  `{"status": "ok", "rerank": {...}, "provider_used": str}` or the
  failure envelope (HTTP 200).
- **Ledger**: new `PromptSource.RERANK` (migration 0005). One row per
  call, `model` = provider name. Privacy canon: the row carries
  `query+docs:<n>` + `{model, document_count, result_count, top_n,
  usage}` — **never the query, never the document texts, never the
  scores** (tested).
- **Checks**: `stapel_agent.W011`/`W012` (rerank registry entry /
  default) — W-level, same degrade-per-request rationale as
  STT/images — plus `W013`: the default is the built-in `rerank-http`
  but `RERANK_HTTP_BASE_URL` is empty (a resolvable default that
  cannot serve a single request should be visible).
- Public API: `RerankProvider`, `NormalizedRerank`, `RerankResult`,
  `register_rerank_provider` / `registered_rerank_providers`;
  `stapel_agent.rerank` resolves to the rerank subpackage. The service
  verb deliberately stays at `stapel_agent.services.rerank` (not a
  package-level function export): the subpackage shares the name, and
  Python binds submodules onto the parent OVER lazy exports — a
  same-named function would be silently shadowed by any
  `stapel_agent.rerank.*` import, so the attribute is pinned to the
  subpackage instead of being left import-order-dependent.

### Packaging
- `tool.setuptools.packages` extended with `stapel_agent.rerank` /
  `stapel_agent.rerank.providers` (the 0.4.0 wheel shipped broken by
  exactly this omission); wheel contents verified as a release gate.

## [0.4.1] — 2026-07-24

### Fixed
- **0.4.0 wheel shipped without the new packages** — the explicit
  `tool.hatch`/setuptools `packages` list in pyproject predates the
  auto-discovery era and did not include `stapel_agent.diarization[.providers]`
  / `stapel_agent.embeddings[.providers]`, so importing the 0.4.0 seams
  raised ModuleNotFoundError. List extended; wheel contents verified.

## [0.4.0] — 2026-07-24

Minor: two new generic seams — speaker **diarization** and text
**embeddings** — layered end-to-end the way transcribe is (ABC +
normalized dataclass + error taxonomy → provider adapters → registry +
settings → service + PromptLog → comm function + HTTP endpoint +
committed schema). Core stays generic: parameter wiring per API lives
here; fusing diarization turns with STT words, chunking policies and
ranking stay app-layer.

### Added
- **Diarization seam** (`diarization/base.py`):
  `DiarizationProvider.diarize(*, audio: AudioRef, num_speakers=None,
  timeout_seconds=None, provider_options=None) → NormalizedDiarization`
  (`provider`, `duration_seconds`, `turns: [DiarTurn(speaker, start,
  end, confidence)]` — seconds-float, wire order preserved,
  `speakers_detected`, `raw`). Errors join the house hierarchy:
  `DiarizationError(ProviderError)` fatal vs
  `RetryableDiarizationError` (429/5xx/timeouts). Ported iron-benchmark
  invariants: speaker-count knob validation
  (`validate_speaker_counts` — exact count XOR min/max bounds, all
  ≥ 1, min ≤ max, fail loudly BEFORE any call), inverted-segment
  clamping (`end < start` → clamped, never dropped), malformed-success
  = loud failure. An EMPTY diarization is data, not an error (the
  empty=error gate is hybrid-merge policy — caller's decision).
- **`pyannote-http` adapter** (`diarization/providers/pyannote_http.py`):
  one synchronous multipart POST to a self-hosted pyannote wrapper
  (gigaam-style plain HTTP, NOT the pyannoteAI cloud jobs API):
  `POST {PYANNOTE_BASE_URL}/diarize` (file + optional
  `num_speakers`/`min_speakers`/`max_speakers` form fields, bounds via
  `provider_options`) → `{"diarization": [{speaker, start, end,
  confidence?}], "duration"?}` — request knobs named after
  `pyannote.audio`'s own `apply()` signature, response segments in the
  pyannoteAI `output.diarization` shape; the full wire contract is
  documented in the module docstring. Upload-capable: any AudioRef
  kind. Settings: `DIARIZATION_PROVIDERS` / `DEFAULT_DIARIZATION_PROVIDER`
  (merge-registry canon + `register_diarization_provider()`),
  `DIARIZATION_TIMEOUT`, `PYANNOTE_BASE_URL`, `PYANNOTE_API_KEY`
  (optional — Bearer only when set).
- **Embeddings seam** (`embeddings/base.py`):
  `EmbeddingProvider.embed(*, texts: list[str], timeout_seconds=None,
  provider_options=None) → NormalizedEmbeddings` (`provider`, `model`,
  `dim`, `vectors` — **input order preserved**, `usage`, `raw` — raw
  kept small, the vectors are never stored twice). Batch gate
  `require_texts`: empty batches / non-string / empty-string entries
  are fatal BEFORE any provider call. A returned count mismatch is a
  loud fatal failure, never a misaligned batch. Per-registration
  `embedding_model` pin mirrors the STT `speech_model` canon.
  `EmbeddingError(ProviderError)` fatal vs `RetryableEmbeddingError`.
- **Embedding adapters**: `openai-embeddings`
  (`embeddings/providers/openai_compat.py` — `POST {base}/embeddings`,
  `{"model", "input": [...]}`, wire entries re-ordered by `index`;
  `EMBEDDINGS_BASE_URL`/`EMBEDDINGS_API_KEY` fall back to the
  `OPENAI_COMPAT_*` pair, `EMBEDDINGS_MODEL` default
  `text-embedding-3-small`) and `embeddings-http`
  (`embeddings/providers/http_server.py` — generic self-host contract
  for local multilingual models class bge-m3/multilingual-e5:
  `POST {EMBEDDINGS_HTTP_BASE_URL}/embed` `{"texts": [...]}` →
  `{"vectors": [[...]], "model"?, "dim"?, "usage"?}`, documented in the
  module docstring; model attribution = server echo, never pretended).
  Settings: `EMBEDDING_PROVIDERS` / `DEFAULT_EMBEDDING_PROVIDER` +
  `register_embedding_provider()`, `EMBEDDINGS_TIMEOUT`,
  `EMBEDDINGS_HTTP_BASE_URL`, `EMBEDDINGS_HTTP_API_KEY`.
- **Surfaces**: `llm.diarize` and `llm.embed` comm functions (+
  committed `schemas/functions/llm.diarize.json` / `llm.embed.json`),
  HTTP endpoints `POST api/v1/llm/diarize` / `POST api/v1/llm/embed`
  (DTO + serializer validation: `num_speakers ≥ 1`, non-empty `texts`,
  positive timeout — new error keys `error.400.invalid_num_speakers`,
  `error.400.empty_texts`). Envelopes mirror transcribe's:
  `{"status": "ok", "diarization"|"embeddings": {...},
  "provider_used": str}` or the failure envelope (HTTP 200).
- **Ledger**: new `PromptSource.DIARIZE` / `PromptSource.EMBED`
  (migration 0004). One row per call, `model` = provider name.
  Privacy canon: the diarize row carries the PII-safe
  `audio.describe()` descriptor + turn/speaker COUNTS; the embed row
  carries `texts:<n>` + `{model, batch_size, dim, usage}` — **never
  the texts, never the vectors** (tested).
- **Checks**: `stapel_agent.W007`/`W008` (diarization registry entry /
  default), `W009`/`W010` (embedding registry entry / default) — all
  W-level, same degrade-per-request rationale as STT/images.
- Public API: `diarize`, `embed`, `DiarizationProvider`,
  `NormalizedDiarization`, `EmbeddingProvider`, `NormalizedEmbeddings`,
  `register_diarization_provider` / `registered_diarization_providers`,
  `register_embedding_provider` / `registered_embedding_providers`.

## [0.3.0] — 2026-07-23

Minor: the generic STT vocabulary-biasing seam + five new provider
adapters ported from the iron-benchmark quads. Core stays generic —
per-provider PARAMETER WIRING (how each API accepts biasing) lives here;
dictionary storage/selection, routing matrices and biasing telemetry
stay app-layer.

### Added
- **Biasing seam** (`stt/base.py`): `SttProvider.transcribe(...)` gains
  `keyterms: list[str] | None` (normalized plain bias terms) and
  `provider_options: dict | None` (free-form per-provider passthrough,
  applied AFTER the adapter's own request params — a caller can pin
  provider specifics without a core release; unknown keys go to the
  provider as-is, never silently dropped). New capability class-attr
  `supports_keyterms: bool = False`. `NormalizedTranscript` gains
  `biasing: dict | None` — `{"applied": bool, "terms_sent": int,
  "terms_truncated": int}`, **counts only, never the term strings**
  (term lists are customer data; the safe thing is the default) —
  threaded through `to_dict()`/`transcript_from_dict()`. Helpers
  `biasing_metadata()` / `unsupported_biasing()`. Adapters without
  keyterm support report requested terms as not applied instead of
  failing; per-provider limits TRUNCATE with counts, never error.
- **Keyterm wiring on existing adapters**: ElevenLabs Scribe `keyterms`
  multipart list (<50 chars / ≤5 words / ≤1000 terms, prohibited chars
  filtered; +20% surcharge noted), AssemblyAI `keyterms_prompt` (≤6
  words per phrase, ≤1000 words total; the legacy `word_boost` pair is
  never sent — gone from current docs).
- **New adapters** (`stt/providers/`, registered as built-ins):
  `deepgram` (Nova-3 `/v1/listen`, raw-bytes body, `Token` auth,
  `diarize_model` — never the deprecated `diarize` boolean; keyterm =
  repeated query param with the ported ~500-token budget estimator,
  legacy `term:weight` syntax and duplicates truncated; keyterm add-on
  $0.0013/min noted), `gladia` (upload→create→poll, solaria-1 pinned
  explicitly), `soniox` (upload→create→poll→fetch with mandatory
  file cleanup every run; sub-word token merge into words), `speechmatics`
  (multipart submit + poll + transcript fetch; melia-1 wire language
  "multi" + hints, `is_eos`-split derived utterances, "UU" → no speaker),
  `xai-stt` (single multipart POST, file field last, `format`+`language`
  pair rules, repeated `keyterm` fields ≤100 × 50 chars; no model
  parameter exists — nothing to pin). Gladia/Soniox/Speechmatics ship
  `supports_keyterms = False` (their vocabulary params are not covered
  by the verified sources); their `provider_options` reach the request
  body for hosts that own that decision. New settings:
  `DEEPGRAM_*`, `GLADIA_*`, `SONIOX_*`, `SPEECHMATICS_*`, `XAI_API_KEY`
  / `XAI_STT_URL`.
- **Surfaces**: `llm.transcribe` schema (+ committed
  `schemas/functions/llm.transcribe.json`), the HTTP transcribe
  serializer/DTO and `services.transcribe()` accept `keyterms` +
  `provider_options` (top-level schema stays `additionalProperties:
  false`; the free-form zone is inside `provider_options` only); the
  result transcript carries the `biasing` block. `llm.stt_catalog` /
  `services.stt_catalog()` entries gain `supports_keyterms`.

### Changed
- `services.transcribe()` threads the seam kwargs to adapters ONLY when
  provided, so out-of-tree adapters written against the pre-seam
  signature keep working until a caller actually uses biasing.

## [0.2.10] — 2026-07-17

Fleet follow-up to stapel-core 0.12.0 (legacy shim sweep). No source
changes needed. Full suite green against core 0.12.0.

### Changed
- `stapel-core` dependency ceiling `<0.12` → `<0.13`.

## [0.2.9] — 2026-07-17

### Changed
- `stapel-core` ceiling raised `>=0.10,<0.11` → `>=0.10,<0.12` (core 0.11
  fleet re-pin: default bus, nav, config-checks, error params/language —
  additive for modules). Suite green against core 0.11.2 (incl. the
  `anthropic` extra), no code changes needed.

## [0.2.8] — 2026-07-16

### Changed
- **v1 canon sweep §60** (api-versioning.md §2, §6): URL set moved to
  `urls_v1.py` (paths now relative: `llm/...`); the new root `urls.py`
  mounts it under `api/v1/`. Host mount `agent/` unchanged: endpoints now
  serve at `/agent/api/v1/llm/...`; bare `/agent/api/llm/...` no longer
  exists (sweep lands before the §3 API00x gates are enabled). Callers
  (stapel-translate AgentProvider) move to `{AGENT_URL}/api/v1/llm/complete`
  in stapel-translate 0.4.8. No contract artifacts in this repo yet.
- Lint hygiene to a clean `stapel-verify`: explicit `# noqa` on pre-existing
  findings.

## [0.2.6] - 2026-07-09

### Added
- Per-registration STT model pin: `SttProvider.speech_model` class-attr
  (the STT mirror of fixing a model on an LLM registration). Setting it on
  a subclass forces one engine/model for that registered name, overriding
  the provider's configured default (`WHISPER_MODEL` /
  `ELEVENLABS_STT_MODEL` / `ASSEMBLYAI_MODEL`); `None` (the default) keeps
  the settings-driven behaviour, so existing registrations are unchanged.
  New `effective_model()` (pin-or-default) and `default_speech_model()`
  (the configured default; providers override it to read their setting)
  helpers on the ABC; the three built-in adapters now send
  `effective_model()`. Two registrations of one adapter class can carry
  different models without a settings change or a fork.
- New comm function `llm.stt_catalog` (committed schema in
  `schemas/functions/llm.stt_catalog.json`, handler in `functions.py`,
  `services.stt_catalog()`): takes no arguments and returns the addressable
  STT surface — `{status, providers: [{name, available, model,
  pinned_model, supports_diarization, supported_languages, cost_per_hour}],
  default_provider, fallback_chain, language_routes}`. Each `model` is the
  registration's effective model (the `speech_model` pin, else the
  configured default); an unresolvable entry is listed `available: false`
  with an `error` rather than silently dropped. Read-only — writes no
  PromptLog row.

### Notes
- **Semver:** strictly this is a MINOR release (a new comm verb + a new
  ABC surface `speech_model`/`effective_model`), but it is held to a PATCH
  (`0.2.6`) by studio's `stapel-agent < 0.3` floor. That is safe here
  because every change is purely additive and backward-compatible: the new
  `speech_model` defaults to `None` (unchanged behaviour), the built-in
  adapters emit the same model as before when unpinned, and `llm.stt_catalog`
  is a brand-new verb touching no existing surface. A dedicated `0.3.0`
  (with a coordinated bump of studio's floor) is deferred until a change
  that actually warrants breaking the floor lands.

- Design note only (no implementation): `docs/streaming-seam.md` sketches
  where a future streaming seam would sit (provider ABC `stream_complete` +
  `supports_streaming`, a `complete_stream()` service generator, an additive
  `emits`-style wire surface) and the invariants it must preserve
  (chunk order, backpressure, wire compatibility, failure parity,
  one-row-per-call ledger). Input for a future design, not a commitment.

## [0.2.5] - 2026-07-09

### Added
- Per-call output-token cap: `llm.complete` payload (comm) and
  `services.complete`/`complete_json` accept an optional `max_tokens`
  integer overriding the configured `STAPEL_AGENT["MAX_TOKENS"]` for that
  call — long structured outputs (file manifests, findings with inline
  tests) raise the ceiling per call instead of a global bump; short calls
  can bound cost. New `LlmProvider.supports_max_tokens` capability flag
  (same discipline as `supports_images`): the kwarg travels only to
  providers that declare it — `anthropic` and `openai-compat` do; a
  requested cap on a non-supporting provider (e.g. `claude-code`) is
  ignored with a logged warning and the configured default stays in
  effect. Pre-existing provider subclasses with older `complete()`
  signatures keep working untouched. The text-keyed prompt cache does not
  see the cap — hosts enabling `CACHE_LOOKUP` for a source should keep
  that source's budget stable (the default policy caches translate only).

## [0.2.4] - 2026-07-09

### Added
- `llm.complete` payload schema admits an optional `role` string — an opaque
  caller tag (e.g. the calling role in a multi-role pipeline) for provider
  routing, override providers and observability. The default completion
  pipeline ignores it. Previously `additionalProperties: false` refused any
  tagged call as soon as schema validation was on (in-process comm callers
  hit `SchemaValidationError`), while stacks that override the provider
  *and* drop the schema masked the mismatch.

## [0.2.3] - 2026-07-08

### Changed
- Admin-suite AS-5: decorated `PromptLog` `@access.ops` (a delivery/audit
  ledger written exclusively by the `services.py` completion pipeline — no
  staff add/change/delete workflow through the admin) and swapped
  `PromptLogAdmin`'s base class to `stapel_core.django.admin.base.StapelModelAdmin`,
  which now enforces the read-only contract instead of the three hand-rolled
  `has_*_permission` overrides. No model in this repo carries credential
  material, so no `@access.secret` classification applies (every provider
  API key is read lazily from settings, never persisted).

## [0.2.2] - 2026-07-06

### Changed
- Pinned `stapel-core` to the `>=0.8,<0.9` window (library-standard §7.1: one
  minor window; floor `0.8.0` is published on PyPI — no pin into the void).
- CI: added the release-track job (library-standard §7.4) — installs the package
  the way an end user does (`pip install .`, dependencies resolved from PyPI
  strictly by the declared pins, no git-main core, no editable siblings), asserts
  `stapel-core` resolves inside the `0.8` window, and runs an import smoke.
  Advisory (continue-on-error) until the whole stapel graph is on PyPI; becomes
  the blocking precondition for a `vX.Y.Z` tag once it is.


## [0.2.1] - 2026-07-06

### Packaging
- Tests excluded from the built wheel/sdist (the `stapel_agent.tests`
  subpackage is no longer listed in `[tool.setuptools] packages`). Added
  `[project.urls]`, completed the trove classifiers (MIT/OSI, Python 3.13,
  `Typing :: Typed`, OS Independent, `3 :: Only`, Development Status) and a
  `[tool.ruff]` lint section (single source shared with the git hooks/CI).


## [0.2.0] - 2026-07-05

### Changed (breaking — custom `CachePolicy` subclasses)
- **The prompt cache key now includes provider + resolved model + model
  size.** `CachePolicy.lookup()` and `.store()` gained three keyword-only
  parameters — `provider`, `model` (the resolved `MODELS[model_size]`
  after `resolve_model`) and `model_size`:

  ```python
  def lookup(self, prompt, system_prompt, source, *,
             provider, model, model_size) -> str | None: ...
  def store(self, prompt, system_prompt, source, response, *,
            provider, model, model_size) -> None: ...
  ```

  Previously the key was prompt + system_prompt + source only, so a
  cached "small" answer could satisfy a "large" request, an explicit
  `provider=` collided with the default, and bumping a model version in
  `MODELS` did not invalidate stale rows (up to `CACHE_TTL`).

  **Migration:** a custom `CachePolicy` must add the three keyword-only
  parameters to its `lookup`/`store` overrides (and fold them into its
  key if it wants correctness across sizes/providers/model versions). A
  policy that ignored them would keep the old collision behaviour, so
  they are required, not defaulted — the mismatch surfaces immediately as
  a `TypeError` at call time rather than as a silent wrong-answer cache
  hit. The default `PromptLogCachePolicy` filters on `model` +
  `model_size` + `metadata.provider`.

### Fixed
- **Unknown STT provider name no longer aborts the fallback chain.** An
  unregistered name in `STT_LANGUAGE_ROUTES` / `STT_FALLBACK_CHAIN` (e.g.
  the docstring's own `"gigaam"` example) is a config error, not bad
  audio — `transcribe()` now skips it and walks to the next provider,
  consistent with the registered-but-unloadable (`ImportError`) branch.
  A fatal `TranscriptionError` raised from *within* a provider's
  `transcribe()` (bad input, auth) still stops the walk. System check
  `W004` still warns about unknown names at startup.
- **`timeout_seconds=0` / negatives are now rejected at the boundary
  instead of silently defaulting or crashing.** The four adapters
  (whisper-http, elevenlabs, assemblyai, openai-images) replaced the
  falsy `int(timeout_seconds or <default>)` with `<default> if
  timeout_seconds is None else int(timeout_seconds)`, so an explicit `0`
  is no longer coerced to the default. `timeout_seconds` now carries a
  `minimum: 1` constraint in the request serializers and the
  `llm.transcribe` / `llm.generate_image` comm schemas — `0` and
  negatives are HTTP 400 / schema errors rather than a silent default or
  an uncaught `urllib3` `ValueError` → HTTP 500.

### Added
- **`timeout_seconds` on the `llm.generate_image` comm surface** — the
  HTTP view already accepted it; the comm schema and function now do too
  (with `minimum: 1`), aligning the two surfaces.

## [0.1.1] - 2026-07-05

### Added
- **Vision input on `llm.complete`** (HTTP + comm, backward-compatible
  additive): optional `images` — each entry `{url}` or `{data_b64,
  mime?}` (raw bytes never travel the wire; in-process callers pass
  `ImageRef(data=...)`). `AnthropicProvider` maps refs to image content
  blocks (url/base64 source), `OpenAICompatProvider` to `image_url`
  parts (url or data URI); `claude-code` has no vision and fails fast
  with `status: "failure"`. New `LlmProvider.supports_images` class
  attribute — the service passes the `images` kwarg only when non-empty
  and only to providers that opt in, so pre-vision provider subclasses
  keep working unchanged.
- **Cache correctness for multimodal requests**: the prompt cache is
  text-keyed, so image requests bypass lookup and store, and the default
  `PromptLogCachePolicy.lookup()` now excludes multimodal ledger rows —
  identical text over different pixels never collides in either
  direction. Vision ledger rows record `metadata.images = {count,
  kinds}`, never bytes.
- **Image generation surface** — `POST api/llm/generate-image` + the
  `llm.generate_image` comm Function (`{prompt, size?, n? (1-10),
  provider?}` → `{status, images: [{url? | data_b64?, mime}],
  provider_used}`); failures stay HTTP 200 with `status: "failure"`.
  Module boundary: the agent returns raw provider results and writes the
  ledger — storing images into stapel-cdn/asset libraries is the
  CALLER's job (system-design §8.8 gateway verb does metering/placement).
- **Image provider registry** — third instance of the house merge
  pattern: `images.BUILTIN_IMAGE_PROVIDERS` +
  `STAPEL_AGENT["IMAGE_PROVIDERS"]` overlay +
  `register_image_provider()` runtime; `DEFAULT_IMAGE_PROVIDER`
  (default `openai-images`); system checks `stapel_agent.W005`
  (unimportable / non-`ImageGenProvider` entry) / `W006` (unknown
  default). `ImageGenProvider` ABC (`generate(*, prompt, size, n,
  timeout_seconds) -> list[GeneratedImage]`, `supported_sizes` gate)
  with the fatal/retryable taxonomy
  (`ImageGenError`/`RetryableImageGenError`).
- **Built-in `openai-images` adapter**: OpenAI-compatible
  `POST {base}/images/generations` (OpenAI, Together, self-hosted
  compatibles) — settings `IMAGES_BASE_URL`/`IMAGES_API_KEY` (both fall
  back to the `OPENAI_COMPAT_*` pair) + optional `IMAGES_MODEL`; maps
  `b64_json`/`url` entries to `GeneratedImage`. Other vendors
  (Stability, ...) are an app-layer recipe in MODULE.md.
- **Ledger coverage for image generation**: `source=generate_image` rows
  (`model` = provider name, prompt logged, response NOT logged raw —
  `{count, mimes, bytes_total}` in metadata, token columns NULL).
  Migration `0003` extends the `source` choices.
- Public API additions (PEP 562, still Django-free at import):
  `generate_image`, `ImageRef`, `ImageGenProvider`, `GeneratedImage`,
  `register_image_provider`, `registered_image_providers`.
- **Transcription surface** — `POST api/llm/transcribe` + the
  `llm.transcribe` comm Function (URLs only over the wire, never raw
  audio bytes), backed by a second open registry with the same merge
  semantics as the LLM one: `STAPEL_AGENT["STT_PROVIDERS"]` overlay over
  `stt.BUILTIN_STT_PROVIDERS`, `None`/`""` removes a name,
  `register_stt_provider()` at runtime. Built-in adapters:
  `whisper-http` (OpenAI Whisper API or self-hosted faster-whisper —
  accepts url/path/bytes refs), `elevenlabs` (Scribe, diarization),
  `assemblyai` (async submit+poll, diarization). App-layer engines
  (GigaAM, ...) subclass `SttProvider` — see the MODULE.md worked
  example.
- **STT routing** (`stt/router.py`): explicit `provider` in the request
  (pinned — no fallback) > `STT_LANGUAGE_ROUTES[lang]` matrix >
  `DEFAULT_STT_PROVIDER` + `STT_FALLBACK_CHAIN`. The chain advances on
  `RetryableTranscriptionError` only (429/5xx/timeouts); fatal
  `TranscriptionError` (bad audio, auth) never falls back.
- **Normalized transcript schema** (`stt/base.py`, Django-free):
  `NormalizedTranscript`/`NormalizedUtterance`/`NormalizedWord` with
  word-level timings, speakers and the untouched provider payload in
  `raw`; `AudioRef` (exactly one of url/path/data, PII-safe
  `describe()`); `transcript_from_dict()` for wire payloads.
- **Summarization surface** — `POST api/llm/summarize` + the
  `llm.summarize` comm Function: exactly one of `text`/`transcript`
  (schema-enforced), single-shot when the input fits one ~15k-token
  chunk, map-reduce (chunk summaries + merge pass) otherwise, optional
  target `language`. `summary.py` renders transcripts as timestamped
  Markdown and chunks with `seg_NNNN` → start-ms anchors
  (click-to-timestamp).
- **Ledger coverage for the new sources**: every transcription writes a
  `PromptLog` row (`source=transcribe`, `model` = STT provider name,
  token columns NULL, fallback walk in `metadata.attempts`); every
  summarize pass logs as `source=summarize` with full token accounting.
  Migration `0002` extends the `source` choices. Cache-by-prompt stays
  off for `summarize` by default (`CACHE_LOOKUP`).
- **System checks** `stapel_agent.W003` (unimportable / non-`SttProvider`
  `STT_PROVIDERS` entry) and `W004` (`DEFAULT_STT_PROVIDER` /
  `STT_FALLBACK_CHAIN` / `STT_LANGUAGE_ROUTES` naming an unknown
  provider) — warnings only: STT is an optional surface and degrades to
  `status: "failure"` per request.
- Public API additions (PEP 562, still Django-free at import):
  `transcribe`, `summarize`, `SttProvider`, `AudioRef`,
  `NormalizedTranscript`, `register_stt_provider`,
  `registered_stt_providers`.

## [0.1.0] - 2026-07-04

Initial release — Python port of a prior NestJS service (the legacy LLM
facade), per the design fixed in the Stapel monorepo's
`docs/agent-service-and-core-ts.md` §2.

### Added
- **Open provider registry with merge semantics** (`providers/__init__.py`).
  `STAPEL_AGENT["PROVIDERS"]` is an overlay merged OVER
  `BUILTIN_PROVIDERS` (same additive style as stapel-notifications
  routing `TYPES`, deliberately not billing's replace-style
  `PAYMENT_PROVIDER`): adding one custom provider never requires
  restating the built-ins, `None`/`""` removes a name. Runtime API for
  app-layer `AppConfig.ready()`: `register_provider(name, cls_or_path)`
  (highest precedence) and `registered_providers()` (the effective
  mapping). `get_provider()` resolves runtime → settings merge →
  built-ins, lazily per request.
- **Django system checks** (`checks.py`, registered from
  `AgentConfig.ready()`): `stapel_agent.E001` when `DEFAULT_PROVIDER` is
  not in the effective registry; `stapel_agent.W001`/`W002` for
  unimportable dotted paths / non-`LlmProvider` entries (warnings — a
  broken unused entry degrades per request, it must not block deploys).
- **Cache-policy seam** (`cache.py`): `STAPEL_AGENT["CACHE_POLICY"]`
  (default `stapel_agent.cache.PromptLogCachePolicy`) points at a
  `CachePolicy` ABC — `should_cache(source)`, `lookup(prompt,
  system_prompt, source) -> str | None`, optional `store()` hook for
  external-storage policies. The default implements the PromptLog+TTL
  behaviour (`CACHE_LOOKUP`/`CACHE_TTL`); hosts swap in Redis/no-op
  without forking. The PromptLog ledger row is written regardless.
- **Serializer seams on both views** (`SerializerSeamMixin`, billing
  pattern): request serializers on both endpoints; typed
  `TranslateResponse` dataclass + serializer on translate.
  `api/llm/complete` deliberately keeps a plain contract dict — its
  `result` is arbitrary JSON (see MODULE.md).
- **HTTP surface**: `POST api/llm/complete` and
  `POST api/llm/translate` (hosts mount under `agent/`), same request/
  response contracts — `stapel-translate`'s `AgentProvider` keeps working
  unchanged. LLM failures stay HTTP 200 with `status: "failure"`; the
  JSON-API system prompt and the JSON/translation response extractors
  (`parsing.py`) are ported verbatim from `llm.controller.ts` /
  `llm.service.ts`. Auth is `IsServiceRequest | IsStaffUser`
  (`SERVICE_API_KEY` via stapel-core), same as stapel-billing's internal
  debit view.
- **comm surface**: `llm.complete` and `llm.translate` Functions
  (`stapel_core.comm`), with JSON Schemas in `schemas/functions/` — in a
  monolith the calls run in-process without HTTP. The comm payload uses
  `from_lang` (a Python-keyword-safe key); the HTTP wire keeps `from`.
- **Provider registry** (`STAPEL_AGENT["PROVIDERS"]`, dotted paths,
  resolved lazily per request): `AnthropicProvider` (SDK, default;
  optional `anthropic` extra), `OpenAICompatProvider` (any
  `/chat/completions` dialect — OpenAI, DeepSeek, MiMo, GLM, Kimi; maps
  `reasoning_tokens` → `thinking_tokens`), `ClaudeCodeCLIProvider`
  (spawns `claude -p`, **opt-in only, never the default**). Custom
  backends subclass `stapel_agent.LlmProvider` — no fork.
- **PromptLog ledger** with the full token accounting from
  system-design 7.16: input/output/**thinking**/cache-read/cache-write
  tokens, `duration_ms`, `model`, `model_size`, `source`, `user_id`,
  `metadata`; read-only admin.
- **Cache-by-prompt**: per-source toggle `CACHE_LOOKUP` (on for
  `translate`, off for `llm_facade` by default) + `CACHE_TTL` (7 days) —
  a repeated identical prompt+system_prompt within the window is served
  from the latest successful row without calling the provider.
- **Settings namespace** `STAPEL_AGENT` (`conf.py`, stapel-core
  `AppSettings`): `MODELS` size map, providers, credentials,
  `MAX_TOKENS`, CLI binary/timeout, cache policy — all read lazily.
- PEP 562 lazy public API (`agent_settings`, `complete`, `translate`,
  `LlmProvider`, `ProviderResult`) — importing the package pulls in no
  Django; `py.typed` marker.

### Deliberately dropped from the source service
- The `claude` module (execute/stream proto-harness) and the `terminal`
  module (node-pty shell) — out of scope for a Django library.
- The ApiKey CRUD/entity — service auth is stapel-core's
  `SERVICE_API_KEY` / `IsServiceRequest`; no module-owned key table.
- OAuth credential reading from `~/.claude/.credentials.json` and the
  background token-refresh hack — the CLI provider owns its auth; the
  facade itself is PAYG-API-key only.
- Any Node/CLI dependency in a default path — `claude-code` is a host
  opt-in.
