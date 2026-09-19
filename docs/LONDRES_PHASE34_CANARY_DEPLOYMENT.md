# Londres Phase 34 — Single-Account Canary Deployment Gate

## Purpose

Phase 34 is the deployment safety boundary between a validated Phase 32 command and Phase 33 broker execution. It is intentionally narrower than normal multi-account replication.

The purpose is to prove one exact account route first, with a short-lived durable attestation, before allowing one Phase 33 canary submission. A successful canary does **not** automatically enable the remaining accounts.

Phase 34 is split into two explicit stages:

```text
Phase 32 SHADOW_READY
        |
        v
OBSERVATION (execution adapter disabled)
        |
        v
short-lived durable canary attestation
        |
        v
ACTIVATION (explicit execution enablement)
        |
        v
reconciliation-only provider probe
        |
        +-- anything except NOT_FOUND --> BLOCK
        |
        v
atomic one-use token claim
        |
        v
single-account Phase 33 handoff
```

## Stage 1 — observation

Observation requires an execution adapter whose `execution_enabled` capability is **false**.

It does not call `submit_market()` and does not call `reconcile()`. No broker mutation can occur from the observation path.

Before issuing an attestation, Phase 34 verifies:

- the input is a Phase 32 shadow-only plan;
- exactly one configured target account exists;
- that account is `SHADOW_READY` or `IDEMPOTENT_SHADOW_READY`;
- its Phase 30 authorization is durably reserved;
- the immutable `ExecutionCommand` passes deterministic integrity checks;
- the binding alias, venue and broker type match exactly;
- the execution adapter reports market-order and reconciliation capability;
- required server-side stop and target capability is present;
- the durable Phase 32 SQLite row is hash-valid and still exactly `SHADOW_READY`;
- the durable command exactly matches the command in the Phase 32 plan;
- the Phase 31 quote/snapshot timestamp is still fresh under the explicit canary policy;
- the selected risk tier does not exceed the canary ceiling.

The default canary ceiling is **3%**. The ceiling itself must be one of the existing Londres risk tiers: 3%, 5% or 10%.

Phase 34 never resizes a trade to force it under the canary ceiling. If an upstream command was authorized at 5% and the canary ceiling is 3%, observation blocks. A new upstream plan is required.

## Durable canary attestation

A successful observation writes a record into the same file-backed SQLite database already used by Phases 32 and 33.

The attestation binds:

- exact command id;
- opaque account alias;
- exact execution adapter id;
- venue and broker type;
- exact broker symbol;
- exact account-specific volume and unit;
- selected risk tier;
- Phase 30 authorization fingerprint;
- Phase 31 pre-submit fingerprint;
- Phase 31 quote timestamp;
- observation timestamp and expiry timestamp.

Canonical JSON is SHA-256 hashed. The hash is the canary token. The stored payload is independently hash-verified before activation.

The token is short-lived and one-use.

## Stage 2 — activation

Activation requires a separate explicit policy with:

```text
execution_enabled = true
```

The execution adapter must now report execution enabled, but it must retain the **same adapter id, account alias, venue and broker type** that were observed.

Phase 34 then revalidates the command and Phase 32 ledger state. The command must still be `SHADOW_READY`; an authorization already advanced by another process cannot be armed through the canary path.

### Reconciliation-only provider probe

Before claiming the canary token and before Phase 33 is allowed to submit, Phase 34 calls only:

```text
adapter.reconcile(command)
```

This is the non-trading provider-route handshake. It exercises the authenticated/local execution route and the exact account/symbol/command identity without creating a new order.

Activation proceeds only when the provider returns:

```text
NOT_FOUND
```

That means no existing evidence for the command was found on that broker route.

If reconciliation returns `ACKNOWLEDGED`, `REJECTED`, `AMBIGUOUS`, `RECONCILIATION_REQUIRED`, or any other result, Phase 34 blocks submission. Existing or uncertain broker evidence must be resolved rather than overwritten with a new order.

## Atomic one-use activation

After a clean `NOT_FOUND` probe, Phase 34 atomically changes the canary attestation from:

```text
OBSERVED -> ACTIVATING
```

Only one process can win that transition.

The token cannot be reused to arm another submission.

Phase 34 then builds a **single-account** `BEST_EFFORT` Phase 32 handoff and invokes Phase 33 for only the canary account.

The rest of the replication set remains untouched.

## Canary terminal states

The attestation is finalized according to the Phase 33 result:

```text
ACKNOWLEDGED / IDEMPOTENT_ACKNOWLEDGED / RECOVERED_ACKNOWLEDGED
    -> COMPLETED

FAILED_SAFE
    -> FAILED_SAFE

AMBIGUOUS / RECONCILIATION_REQUIRED / other uncertain state
    -> RECONCILIATION_REQUIRED
```

An ambiguous canary is never automatically resent.

## Replication expansion

Phase 34 intentionally outputs:

```text
single_account_canary = true
replication_expansion_enabled = false
```

A successful canary proves that one exact account route completed the gated execution path. It is a prerequisite for a later rollout gate, not permission to silently activate every account.

The future rollout layer should require explicit operator policy for which additional aliases may be enabled and should preserve independent per-account sizing.

## Broker-specific operating flow

### FP Markets cTrader

1. Keep the existing view-only connector separate.
2. Configure the Phase 33 cTrader execution adapter with the intended opaque account alias and a token verified for `SCOPE_TRADE`.
3. Run Phase 34 observation with the execution adapter disabled.
4. Confirm the returned canary token and exact command/account details.
5. Recreate/arm the same adapter id with execution explicitly enabled.
6. Phase 34 activation authenticates and performs reconciliation only.
7. Only a clean `NOT_FOUND` result permits the single Phase 33 market submission.

### Vantage MT5

1. Start from the intended already-authenticated MT5 terminal session.
2. Keep the local execution host without `--enable-execution` while preparing and reviewing the route.
3. Phase 34 observation validates the Python adapter and durable command without IPC submission.
4. Deliberately start/arm the local host for execution and instantiate the same adapter identity as enabled.
5. Activation first sends a `RECONCILE` request through the local IPC route.
6. Only `NOT_FOUND` permits the one-account Phase 33 submission.

The MT5 login, server and demo/live classification stay local to the execution host.

### NinjaTrader 8

1. Install/run `ninjatrader/LondresExecutionBridge.cs` in the intended Windows NinjaTrader session.
2. Keep its local execution configuration disabled during preparation.
3. Phase 34 observation validates the exact opaque account alias, futures contract command and adapter identity.
4. Explicitly enable the local NinjaTrader execution configuration and instantiate the same enabled adapter identity.
5. Activation sends reconciliation first through file IPC.
6. Only `NOT_FOUND` permits the single Phase 33 entry submission and OCO protection flow.

No full NinjaTrader account identifier or demo/live label is placed in public Londres state.

## Demo/live privacy

Phase 34 preserves the existing privacy contract:

```text
account_scope = BROKERAGE_ACCOUNTS
account_environment = HIDDEN_INTERNAL
```

Demo/live classification is private routing metadata. It is not part of the canary token's public output.

## Validation boundary

The automated test suite uses fake execution adapters. It verifies the control plane and exactly-once canary semantics without connecting to a broker and without placing demo or live orders.

Automated regression coverage includes:

- observation cannot run with an already-enabled execution adapter;
- observation performs zero reconcile and zero submit calls;
- durable attestation creation and integrity verification;
- stale Phase 31 snapshot rejection;
- explicit 3/5/10 canary risk ceiling enforcement without resizing;
- exact adapter identity binding;
- explicit activation enablement;
- reconciliation-before-submit ordering;
- existing broker evidence blocks submission;
- exactly one canary account is handed to Phase 33;
- successful acknowledgement consumes the one-use canary;
- repeated activation cannot submit twice;
- ambiguous execution becomes reconciliation-required and is not resent;
- demo/live labels are absent from public canary output.

A **real broker-connected canary** is an operator deployment action. It requires the intended broker session/token and explicit execution enablement; CI does not and must not perform it.
