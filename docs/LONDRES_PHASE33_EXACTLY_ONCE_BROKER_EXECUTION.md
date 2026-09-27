# Londres Phase 33 — Exactly-Once Broker Submission and Reconciliation

## Purpose

Phase 33 is the first Londres phase allowed to cross the broker submission
boundary. It consumes only durable Phase 32 execution commands, atomically claims
each command in the same SQLite ledger, submits through one explicitly bound
broker adapter, persists the broker receipt, and reconciles every uncertain
network outcome before any retry can occur.

Phase 33 does **not** claim that a distributed network can provide mathematical
exactly-once delivery. The guarantee implemented by Londres is narrower and
verifiable:

- one Phase 32 command can be atomically claimed for submission only once;
- a crash after the claim never causes an automatic second submission;
- an uncertain broker result is locked for reconciliation;
- an authorization is consumed only after an exact, protected broker
  acknowledgement is verified.

## Required upstream state

Phase 33 accepts only a valid Phase 32 batch and account command whose durable
ledger reservation is still intact. The command already binds:

- trade id and canonical instrument;
- account alias and venue;
- broker type and exact broker symbol/contract;
- direction and `MARKET_ON_SIGNAL` execution style;
- exact account-specific volume and volume unit;
- selected risk tier;
- intended entry and the Phase 31 executable-price snapshot;
- stop, target and exit mode;
- Phase 30 authorization fingerprint;
- Phase 31 pre-submit fingerprint;
- deterministic command id and client-order label.

Phase 33 never recomputes or resizes the position. If the exact command cannot be
trusted, execution fails closed.

## Explicit execution gate

Live submission requires two independent enablements:

1. `Phase33ExecutionPolicy.execution_enabled = true`;
2. the account's broker execution adapter must report
   `execution_enabled = true`.

Every execution adapter defaults to disabled. The local MT5 and NinjaTrader
hosts also require their own explicit execution enablement. This prevents a
configuration mistake in one layer from silently enabling order placement.

The recommended production policy also requires both stop and target protection.
If the adapter cannot support the configured protection contract, the account is
blocked before submission.

## Durable execution state machine

The Phase 32 SQLite ledger is reused as the execution source of truth.

```text
SHADOW_READY
    -> SUBMITTING
       -> ACKNOWLEDGED
       -> FAILED_SAFE
       -> AMBIGUOUS
       -> RECONCILIATION_REQUIRED
```

`SHADOW_READY -> SUBMITTING` is an atomic SQLite claim. Once the command reaches
`SUBMITTING`, a subsequent process invocation reconciles it instead of submitting
again.

### ACKNOWLEDGED

A command reaches `ACKNOWLEDGED` only when the broker receipt matches the command
identity, the exact authorized volume is accounted for, and the required stop and
target protection are active.

At that point:

```text
authorization_reserved = true
authorization_consumed = true
authorization_locked_against_retry = true
broker_order_placed = true
automatic_retry_allowed = false
```

An identical later invocation returns the stored, hash-verified acknowledgement
without calling the broker again.

### FAILED_SAFE

A broker rejection is terminal only when the adapter can state
`definite_no_fill = true`. The command is locked as `FAILED_SAFE`; a new trade
attempt requires a new upstream authorization rather than reusing the old one.

### AMBIGUOUS / RECONCILIATION_REQUIRED

Timeouts, disconnects, partial evidence, mismatched protection, uncertain fills,
or corrupted local evidence are never interpreted as a safe retry signal.

The command is locked and Phase 33 calls the broker reconciliation path on future
invocations. Blind resubmission is forbidden.

## Multi-account trade replication

The existing Londres replication architecture remains intact.

One validated `TradeIntent` fans out to explicitly enabled brokerage accounts,
but **trade intent is replicated, not raw lot/contract quantity**. Each account
continues to use its own Phase 23/26 risk calculation, current broker equity,
instrument geometry and exact authorized quantity.

Phase 33 therefore preserves:

- common direction, entry model, stop, target, exit mode and risk tier;
- independent NinjaTrader contract sizing;
- independent FP Markets cTrader unit sizing;
- independent Vantage MT5 lot sizing;
- no Standard/Micro futures substitution;
- no silent broker-symbol guessing;
- no silent quantity rounding or resizing.

### BEST_EFFORT

`BEST_EFFORT` is the valid live multi-account policy. One account can be
acknowledged while another is blocked or awaiting reconciliation, without
creating a duplicate order in either account.

### ALL_OR_NONE

A true atomic fill across NinjaTrader, cTrader and MT5 cannot be guaranteed by
independent brokers. Therefore Phase 33 explicitly refuses multi-account live
`ALL_OR_NONE` execution rather than presenting a false distributed-transaction
guarantee.

## FP Markets — cTrader execution path

The cTrader execution connector is separate from the existing read-only
connector.

Requirements:

- an explicitly trading-enabled cTrader Open API token;
- verified `SCOPE_TRADE` permission;
- the exact account configured in the private `CTraderSecretConfig`;
- the exact Phase 32 broker symbol and volume;
- explicit `execution_enabled=True` on `CTraderExecutionAdapter`.

For deployments that also use the read-only connector, keep the trading token
separate. A practical convention is to load it with a separate prefix such as
`CTRADER_TRADE_` using `CTraderSecretConfig.from_env(prefix="CTRADER_TRADE_")`.
Do not replace a view-only token with a trading token in the read-only connector.

The adapter submits the deterministic command label/client order identity with
the exact volume, stop and target. Provider timeout or connection uncertainty
returns `AMBIGUOUS`; it does not trigger a second order request.

Reconciliation searches broker state using the same deterministic command
identity and exact trade geometry.

## Vantage — MetaTrader 5 execution path

MT5 execution is intentionally isolated to a local bridge process running on the
machine/session where the intended Vantage terminal is already authenticated.
The agent does not need the MT5 login password or server credentials.

Components:

- `VantageMT5ExecutionAdapter` in the agent process;
- `scripts/mt5_execution_bridge.py` beside the logged-in MT5 terminal;
- a private local request/response directory;
- a bridge-local SQLite journal that survives restarts.

The bridge validates the private account route and Vantage identity, the exact
current volume grid and the broker's supported filling mode before submission.
It performs `order_check` before `order_send` and includes the command-derived
magic/comment identity plus the exact stop and target.

The bridge is execution-disabled unless it is started with
`--enable-execution`.

Example local host command:

```bash
python scripts/mt5_execution_bridge.py \
  --root ~/.londres/vantage-mt5-execution \
  --enable-execution
```

For initial connectivity/reconciliation validation, omit `--enable-execution`.

After a crash with a local command in `SUBMITTING`, the bridge reconciles current
positions and recent history instead of replaying `order_send`.

## NinjaTrader 8 execution path

NinjaTrader order APIs run inside NinjaTrader on Windows. The Python agent uses a
local file bridge rather than trying to execute NinjaTrader directly from the
Mac process.

Components:

- `NinjaTraderExecutionAdapter` in the agent process;
- `ninjatrader/LondresExecutionBridgeAddOn.cs` in NinjaTrader 8;
- a private local request/response directory shared with the Windows session.

The AddOn binds the exact account alias, contract and integer contract quantity,
creates the entry with NinjaTrader account APIs, observes order/execution update
events, and establishes deterministic OCO stop/target protection. A command that
cannot be reconciled to an exact protected execution remains locked for
reconciliation.

No NinjaTrader account password is serialized into Phase 33 command state.

## Private environment metadata

Demo/live classification remains routing metadata and is never exposed in the
public Phase 33 account or batch state:

```text
account_scope = BROKERAGE_ACCOUNTS
account_environment = HIDDEN_INTERNAL
```

The production architecture targets brokerage accounts. Environment selection is
a private deployment concern, not a strategy input and not an LLM-facing field.

## Deployment checklist

Before enabling order submission on any account:

1. Keep the Phase 32/33 SQLite ledger on durable local storage and back it up
   consistently with the execution host state.
2. Confirm the exact opaque account alias and explicit broker symbol mapping.
3. Confirm current broker equity, tick value, min/max/step and Phase 31 quote
   freshness before producing the Phase 32 command.
4. Start MT5/NinjaTrader bridges with execution disabled first and confirm
   request/response identity plus reconciliation behavior.
5. For cTrader, use a separate trading-scope token and verify the intended
   account before enabling the adapter.
6. Enable exactly one controlled account path first, verify deterministic labels,
   stop/target protection and ledger transitions, then enable additional account
   bindings deliberately.
7. Use `BEST_EFFORT` for independent multi-broker live replication.
8. Never manually delete or rewrite a `SUBMITTING`, `AMBIGUOUS`, or
   `RECONCILIATION_REQUIRED` ledger row to force a retry. Reconcile broker state
   first.

## Regression coverage

Phase 33 tests cover the safety-critical execution semantics, including:

- one broker submit for one durable command;
- idempotent replay of an acknowledged command with no second broker call;
- crash recovery from `SUBMITTING` through reconciliation;
- terminal definite-no-fill rejection;
- uncertain submission lockout;
- exact fill/protection enforcement;
- stale Phase 31 snapshot blocking;
- adapter/account/venue/broker-type binding;
- execution-disabled fail-closed behavior;
- rejection of false multi-broker live `ALL_OR_NONE` semantics;
- provider execution-source hardening for cTrader, MT5 and NinjaTrader.

CI tests use fakes/source validation and do not place real or demo broker orders.

## Operational limitation

Phase 33 makes command submission idempotent from the Londres side and supplies
broker reconciliation paths. It cannot eliminate uncertainty created by an
external broker/network failure. Any provider state that cannot be conclusively
matched to the deterministic command remains `RECONCILIATION_REQUIRED` until
broker state is resolved.
