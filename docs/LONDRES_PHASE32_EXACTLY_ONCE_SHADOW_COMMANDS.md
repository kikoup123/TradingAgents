# Londres Phase 32 — Exactly-Once Shadow Execution Commands

## Purpose

Phase 32 is the durable boundary between the Phase 31 universal pre-submit
firewall and any future broker-specific execution adapter.

It does **not** submit an order. Instead, it converts each Phase 31 account that
passed immediate pre-submit revalidation into one immutable deterministic
`ExecutionCommand` and reserves the underlying authorization in a file-backed
SQLite ledger.

The objective is to make retries safe and to ensure one Phase 30 authorization
cannot be silently reused for a different account route, broker symbol, volume,
price snapshot or trade geometry.

## Inputs

Phase 32 accepts only a Phase 31 batch with:

- a valid Phase 31 phase marker;
- matching trade id, canonical symbol and direction;
- `MARKET_ON_SIGNAL` execution style;
- `pre_submit_ready = true`;
- `order_authorized = true`;
- `order_submission_enabled = false`;
- account envelopes in `READY_FOR_EXECUTION_ADAPTER_HANDOFF` state.

Each account must still provide the exact values approved by Phase 31:

- opaque account alias;
- supported venue and broker type;
- exact broker symbol or futures contract;
- exact account-specific volume and unit;
- exact selected 3%, 5% or 10% risk tier;
- current executable price;
- Phase 30 authorization fingerprint;
- Phase 31 pre-submit snapshot fingerprint.

Phase 32 never resizes a position.

## Deterministic command identity

Each account-specific command binds:

- trade id;
- opaque account alias;
- venue and broker type;
- exact broker symbol/contract;
- canonical symbol;
- direction;
- execution style;
- exact account-specific volume and unit;
- selected risk tier;
- intended entry;
- Phase 31 current executable price;
- stop;
- target;
- selected exit mode;
- Phase 30 authorization fingerprint;
- Phase 31 pre-submit fingerprint;
- quote timestamp.

The `command_id` is SHA-256 over canonical JSON of these immutable identity
fields. A deterministic broker-safe client label is derived from the command id.
The current timestamp is deliberately **not** part of command identity, so an
identical retry produces the same command id.

## Durable SQLite ledger

`SQLiteExecutionAuthorizationLedger` uses a file-backed SQLite database. In-memory
SQLite is rejected because crash/restart persistence is a Phase 32 requirement.

The ledger has unique constraints on:

1. `command_id`;
2. Phase 30 authorization fingerprint;
3. Phase 31 pre-submit fingerprint.

This means a Phase 30 authorization can have at most one durable command
reservation.

Stored canonical command JSON also has a SHA-256 payload hash. `inspect()`
recomputes both the stored payload hash and deterministic command identity. A
mismatch is surfaced as `RECONCILIATION_REQUIRED`; the corrupted command is not
returned as valid.

## Idempotent retry semantics

If the exact same command is presented again while the ledger state remains
`SHADOW_READY`, Phase 32 returns `IDEMPOTENT_SHADOW_READY` and does not create a
new row.

This is a successful idempotent retry, not a second authorization.

If the same Phase 30 authorization or Phase 31 snapshot is presented with a
different command, Phase 32 fails closed with a ledger conflict.

If an identical command has already advanced beyond the Phase 32 shadow state,
Phase 32 does not re-arm it. A later execution/reconciliation phase must decide
what happens next.

## BEST_EFFORT and ALL_OR_NONE

### BEST_EFFORT

Each valid account command is reserved in its own immediate SQLite transaction.
A replay/conflict in one account does not prevent another valid account from
being reserved.

### ALL_OR_NONE

All commands are classified and reserved inside one SQLite `BEGIN IMMEDIATE`
transaction. If any command conflicts, all new reservations in that batch are
rolled back.

Pre-existing durable reservations are never deleted or rewritten merely to make
a new ALL_OR_NONE request appear atomic.

## Reservation vs consumption

Phase 32 makes a deliberate distinction:

- `authorization_reserved = true` means the authorization is durably bound to one
  command and cannot be used for another command;
- `authorization_consumed = false` remains true throughout Phase 32.

The authorization should only be **consumed** as part of a later atomic broker
submission state transition. This avoids falsely claiming exactly-once broker
execution before network submission, acknowledgement and reconciliation logic
exist.

The ledger already defines future-facing states including:

- `SHADOW_READY`;
- `SUBMITTING`;
- `ACKNOWLEDGED`;
- `AMBIGUOUS`;
- `FAILED_SAFE`;
- `RECONCILIATION_REQUIRED`.

Phase 32 itself writes only `SHADOW_READY`.

## Demo/live privacy

Demo/live classification remains private broker-routing metadata. It is not
serialized into Phase 32 commands or public batch/account output.

Public output remains:

```text
account_scope = BROKERAGE_ACCOUNTS
account_environment = HIDDEN_INTERNAL
```

The command is still route-bound because the Phase 31 fingerprint already binds
the exact adapter/account route that passed immediate revalidation, and Phase 32
also binds the opaque account alias, venue, broker type and broker symbol.

## Execution boundary

Phase 32 exposes no order-placement surface.

```text
execution_enabled = false
order_submission_enabled = false
broker_order_placed = false
authorization_consumed = false
execution_handoff_ready = false
```

No `place_order`, `submit_order`, MT5 `order_send`, amend, cancel, close or
flatten method is introduced.

## Regression coverage

The Phase 32 regression suite verifies:

- three-venue command construction and exact volume preservation;
- deterministic command ids;
- identical retry idempotency;
- SQLite persistence across ledger re-open;
- Phase 30 authorization replay rejection;
- BEST_EFFORT conflict isolation;
- ALL_OR_NONE transaction rollback;
- invalid Phase 31 volume rejection before ledger mutation;
- demo/live privacy in public command state;
- stored payload corruption detection;
- exposure of reserved Phase 30 fingerprints for Phase 31 replay defense;
- absence of any live order-submission surface.

## Next boundary

The next phase should implement broker-specific submission as an explicit ledger
state transition, not as a new independent command generator.

A safe future flow is:

```text
SHADOW_READY
  -> atomic claim / SUBMITTING
  -> broker-specific submit with deterministic client label
  -> ACKNOWLEDGED
```

If network outcome is uncertain, the state must become `AMBIGUOUS` or
`RECONCILIATION_REQUIRED` rather than blindly retrying and risking a duplicate
order.
