# Londres Phase 27 — Live-Account Execution Authorization

Phase 27 is the deterministic handoff boundary between the validated Londres trading setup and a future NinjaTrader execution adapter.

It does **not** submit, modify, cancel, or close orders. It only decides whether an exact account-specific execution envelope is authorized to be handed to a later execution layer.

## Required strategy sequence

Phase 27 explicitly re-verifies the Londres execution sequence before any live account can be authorized:

```text
SMT_DETECTED
    ↓
CSD_CONFIRMED_AFTER_SMT
    ↓
POST_CSD_IOFC_CONFIRMED_AND_DIRECTION_ALIGNED
    ↓
SMT_VALIDATED
    ↓
PHASE17_HARD_PRE_BROKER_AUTHORIZED
    ↓
PHASE26_LIVE_ACCOUNT_PREPARATION_READY
    ↓
PHASE27_EXECUTION_HANDOFF_AUTHORIZED
```

SMT alone is never sufficient.

The public Phase 27 strategy gate exposes the same invariant as:

```text
SMT_DETECTED + CSD_CONFIRMED + IOF_ALIGNED
```

## Strategy geometry must match the trade intent

Phase 27 fails closed unless the validated strategy context matches the exact `TradeIntent`:

- direction;
- canonical symbol;
- exact entry price;
- executable stop price;
- selected target price;
- selected exit mode.

A strategy context from one setup therefore cannot be combined with a Phase 26 account plan from another setup.

Phase 26 `trade_id` and canonical symbol must also match the same intent.

## Multi-account quantity rule

Phase 17 contains a deterministic trade calculation, but that calculation may have been produced using one account's equity. It is **not** the quantity source for multi-account execution.

Phase 27 deliberately ignores the Phase 17 position volume.

For every NinjaTrader live account:

```text
contract_quantity = Phase 26 prepared_contracts
```

Phase 26 already obtained that value by independently sizing the account from current broker-reported equity and its explicit 3% / 5% / 10% risk policy.

Phase 27 accepts only a positive integer futures quantity. It never rounds a fractional quantity into a tradable contract count.

## Exact contract requirement

An authorized account envelope requires:

- account alias;
- selected futures root;
- active resolved NinjaTrader contract;
- positive integer contract quantity;
- exact entry, stop, target, direction and exit mode.

The active contract must belong to the selected Phase 26 root. For example, a Phase 26 `NQ` plan cannot authorize an `MNQ` active contract.

There is no silent Standard/Micro substitution.

## Authorization fingerprint

Every authorized account receives a deterministic SHA-256 fingerprint derived from:

- trade id;
- account alias;
- canonical symbol;
- direction;
- active contract;
- contract quantity;
- entry;
- stop;
- target;
- exit mode.

This fingerprint gives a future execution layer a stable identity for the exact authorized account/trade envelope and can later support idempotency and duplicate-order protection.

A blocked account receives no authorization fingerprint.

## BEST_EFFORT and ALL_OR_NONE

### BEST_EFFORT

Ready accounts may be authorized even when another live account is blocked by Phase 26.

```text
Account A -> AUTHORIZED_FOR_EXECUTION_HANDOFF
Account B -> BLOCKED_PHASE26
Batch     -> PARTIAL_READY
```

### ALL_OR_NONE

If any enabled live account fails, otherwise-ready accounts have their Phase 27 authorization revoked for that batch.

```text
Account A -> BLOCKED_BATCH_POLICY
Account B -> BLOCKED_PHASE26
Batch     -> BLOCKED
```

This prevents an individually ready account from escaping an explicit all-or-none orchestration rule.

## Safety boundary

Phase 27 may set:

```text
order_authorized = true
execution_handoff_ready = true
```

only when the deterministic strategy and account gates pass.

That authorization is **not broker execution**. Phase 27 always preserves:

```text
execution_enabled = false
order_submission_enabled = false
broker_order_placed = false
```

No broker credential is stored in the authorization envelope.

## Files

- `tradingagents/ict/phase27.py`
- `tests/test_phase27_execution_authorization.py`
- `docs/LONDRES_PHASE27_EXECUTION_AUTHORIZATION.md`

## Next boundary

A later phase can consume only Phase 27-authorized envelopes and implement the actual NinjaTrader order-submission adapter. That future layer should use the Phase 27 authorization fingerprint as part of an idempotency/duplicate-submission guard and must revalidate live quote/account state immediately before submission.
