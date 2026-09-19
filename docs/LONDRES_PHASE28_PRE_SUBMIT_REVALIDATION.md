# Londres Phase 28 — Pre-Submit Revalidation Firewall

Phase 28 is the final **read-only** safety firewall before any future NinjaTrader order-submission adapter could receive an authorized trade envelope.

It does **not** submit, modify, cancel, flatten, close or manage a broker order.

## Input boundary

Phase 28 consumes only a Phase 27 account envelope that already passed:

```text
SMT_DETECTED
    ↓
CSD_CONFIRMED_AFTER_SMT
    ↓
POST_CSD_IOFC_CONFIRMED_AND_DIRECTION_ALIGNED
    ↓
PHASE17_HARD_PRE_BROKER_AUTHORIZED
    ↓
PHASE26_ACCOUNT_POLICY_READY
    ↓
PHASE27_EXECUTION_HANDOFF_AUTHORIZED
```

SMT is never sufficient by itself.

Phase 28 additionally requires the Phase 27 batch to remain authorization-ready while still preserving:

```text
order_submission_enabled = false
```

The current Phase 28 contract supports only `MARKET_ON_SIGNAL` execution style. Other execution styles fail closed until their exact pre-submit semantics are explicitly implemented.

## Duplicate-authorization protection

Every Phase 27-authorized account carries a SHA-256 authorization fingerprint.

Phase 28 accepts an external set of already-used fingerprints and blocks an account when its Phase 27 fingerprint:

- is malformed;
- was already used by a prior submission path; or
- appears more than once in the current Phase 28 batch.

Phase 28 itself does not mutate or persist that registry and does not consume the fingerprint. A future order-submission layer must atomically consume/register the authorization at submission time.

## Immediate active-contract revalidation

The Phase 27 selected futures root and active contract are not trusted indefinitely.

Immediately before handoff, Phase 28 asks the NinjaTrader read-only adapter to resolve the currently active verified contract again.

The account is blocked if:

- the root is no longer supported for the canonical instrument;
- rollover verification fails; or
- the active contract has changed since Phase 27 authorization.

This prevents a stale `NQ 12-26` authorization from being submitted after the platform has rolled to a different verified contract.

## Immediate broker supervision

Phase 28 re-runs the existing fail-closed broker supervisor using explicit environment policy inputs.

It requires:

- broker/bridge connectivity;
- connected account state;
- current readable account snapshot;
- verified instrument metadata;
- complete timestamped bid/ask quote;
- quote age within the explicit maximum;
- valid quote timestamp;
- verified tick-value data.

A stale or missing quote blocks the account before any future execution handoff.

## Market-quality revalidation

`Phase28PreSubmitPolicy` contains explicit limits for:

- maximum spread in ticks;
- maximum adverse entry deviation in ticks.

No default slippage or spread allowance is invented by the engine.

For `MARKET_ON_SIGNAL`:

```text
bullish current executable price = current ask
bearish current executable price = current bid
```

The current executable quote must still lie inside the original stop/target trade geometry.

For example, a bullish trade is blocked if the ask has already traded at or through the target, or at/below the stop geometry.

## Current-equity risk revalidation

Phase 28 does not assume that account equity or executable price stayed unchanged after Phase 26/27.

Risk is recomputed from:

```text
current executable bid/ask
current broker-reported equity
current verified tick size
current verified tick value
Phase 27 contract quantity
original executable stop
```

For futures:

```text
stop_distance = abs(current_executable_price - stop_price)
risk_per_contract = (stop_distance / tick_size) * tick_value
projected_cash_risk = risk_per_contract * contract_quantity
projected_equity_risk_fraction = projected_cash_risk / current_equity
```

The account is blocked if the current projected risk exceeds:

- the Phase 26 account risk fraction;
- the Londres hard maximum risk fraction;
- an explicit Phase 26 cash-risk cap, when supplied;
- the Phase 26 maximum-contract cap; or
- the current broker instrument maximum quantity.

This means an adverse price move or equity drawdown between authorization and submission can invalidate an otherwise valid Phase 27 envelope.

## Pre-submit snapshot fingerprint

A successful Phase 28 account receives a second deterministic SHA-256 fingerprint derived from:

- Phase 27 authorization fingerprint;
- account alias;
- active contract;
- contract quantity;
- current equity;
- current bid;
- current ask;
- quote timestamp;
- Phase 28 revalidation timestamp.

This fingerprint identifies the **exact live broker/market snapshot** that passed Phase 28.

A future execution adapter should require this exact Phase 28 snapshot identity and should re-check that it has not expired before attempting submission.

## BEST_EFFORT and ALL_OR_NONE

### BEST_EFFORT

An account that fails Phase 28 can be isolated while another valid account remains ready.

### ALL_OR_NONE

If any enabled account fails Phase 28, every otherwise-ready account has its pre-submit handoff revoked for that batch.

## Safety boundary

Phase 28 can expose:

```text
pre_submit_ready = true
order_authorized = true
```

only after all revalidation gates pass.

It still always exposes:

```text
execution_enabled = false
order_submission_enabled = false
broker_order_placed = false
fingerprint_consumed = false
```

There is no `place_order`, `submit_order`, `cancel_order`, `flatten`, ATM or close-position API in Phase 28.

## Regression coverage

`tests/test_phase28_pre_submit_revalidation.py` covers:

- valid ready path;
- stale quotes;
- rollover/active-contract changes;
- reused authorization fingerprints;
- excessive spread;
- excessive adverse entry deviation;
- current-equity/current-quote risk expansion;
- `ALL_OR_NONE` revocation;
- absence of an order-submission surface.

## Files

- `tradingagents/ict/phase28.py`
- `tests/test_phase28_pre_submit_revalidation.py`
- `docs/LONDRES_PHASE28_PRE_SUBMIT_REVALIDATION.md`
- `tradingagents/ict/__init__.py`

## Next boundary

Phase 28 is intentionally the end of the read-only authorization stack.

Any future phase that actually submits a NinjaTrader order must be a separately reviewed execution adapter and must atomically enforce idempotency, re-check Phase 28 freshness, submit the exact authorized account/contract/quantity geometry, and preserve hard stop/risk constraints without silent resizing or substitution.
