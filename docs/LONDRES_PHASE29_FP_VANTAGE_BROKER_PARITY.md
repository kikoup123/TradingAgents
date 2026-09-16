# Londres Phase 29 — FP Markets cTrader + Vantage MT5 Broker Parity

Phase 29 adds the second live-broker path beside NinjaTrader without enabling order submission.

## Scope

Phase 29 supports two explicit venue identities:

- `FP_MARKETS_CTRADER`
- `VANTAGE_MT5`

FP Markets uses the existing hardened cTrader Open API read-only adapter. Vantage uses the new MT5 read-only bridge and `MT5UniversalReadOnlyAdapter`.

No broker is selected by guessing a symbol or adapter. The configured venue must match both the adapter type and the broker name reported by the connected account.

## Deterministic preparation path

```text
Validated Londres TradeIntent
    ↓
Explicit venue + adapter type
    ↓
Broker-reported provider identity
    ↓
Explicit canonical → broker symbol map
    ↓
Current broker account snapshot
    ↓
Quote/tick-value supervision
    ↓
Independent Phase 23 equity risk sizing
    ↓
Phase 29 read-only broker-parity envelope
```

This path still consumes the strategy result only after the upstream Londres sequence has established the trade intent. Phase 29 does not weaken the strategy invariant:

```text
SMT_DETECTED + CSD_CONFIRMED + IOF_ALIGNED
```

## FP Markets

FP Markets is connected through `CTraderUniversalReadOnlyAdapter`. The adapter reads the broker identity, masked account, current equity, explicit symbol metadata, live quote and cTrader-derived account-currency tick value.

Phase 29 requires the broker-reported identity to contain `FP Markets` after normalization. A generic cTrader account from another broker is not accepted as `FP_MARKETS_CTRADER`.

## Vantage

Vantage is connected through `MT5UniversalReadOnlyAdapter`.

The local bridge is `scripts/mt5_readonly_bridge.py`. It reads an already-connected MT5 terminal and publishes an atomic JSON snapshot containing only sanitized account state and market metadata required by the Londres risk/supervision layer.

The bridge:

- accepts live/real MT5 brokerage accounts only;
- never stores the full account login;
- hashes the terminal server + login into a private account key;
- exposes only a masked account externally;
- reads current balance/equity/margin state;
- reads explicit symbol geometry and `trade_tick_value_loss`;
- records tick value in the account deposit currency;
- reads current bid/ask with MT5 tick timestamp;
- contains no order submission call.

Phase 29 requires the broker-reported identity to contain `Vantage` after normalization.

## Symbol mapping

Broker symbols are never guessed.

Example mappings might be configured as:

```text
FP Markets cTrader:
NASDAQ -> US100
SP500  -> US500
GOLD   -> XAUUSD

Vantage MT5:
NASDAQ -> NAS100
SP500  -> SP500
DOW    -> DJ30
GOLD   -> XAUUSD
```

The actual symbol names must be verified against the user's own live account because broker/account suffixes can differ.

## Risk invariant

Phase 29 replicates trade intent, not lots.

Each account runs Phase 23 independently:

```text
account risk cash = current broker equity × selected risk fraction
position volume   = risk cash / cash risk per broker volume unit
```

The supported account risk fractions remain exactly 3%, 5% and 10%, with the Londres hard maximum of 10%.

FP Markets and Vantage therefore can receive different prepared lot sizes for the same trade because account equity and broker contract geometry may differ.

## Supervision

Before Phase 23 sizing, Phase 29 requires the universal broker supervisor to confirm:

- connection/account readability;
- complete bid/ask quote;
- quote timestamp within the explicit maximum age;
- verified account-currency tick value;
- tick-value timestamp within policy when the value is market-dependent.

A stale quote, stale tick valuation, disconnected account or unreadable adapter fails closed.

## Orchestration

`BEST_EFFORT` isolates a blocked FP Markets or Vantage account and can keep the other account preparation-ready.

`ALL_OR_NONE` revokes otherwise-ready Phase 29 envelopes if any enabled account fails.

## Execution boundary

Phase 29 always reports:

```text
execution_enabled = false
order_submission_enabled = false
order_authorized = false
broker_order_placed = false
```

Neither the MT5 adapter nor the MT5 bridge exposes `place_order`, `submit_order`, `amend_order`, `cancel_order`, `order_send` or position-flattening methods.

## Files

- `tradingagents/brokers/mt5.py`
- `scripts/mt5_readonly_bridge.py`
- `tradingagents/ict/phase29.py`
- `tests/test_mt5_readonly_adapter.py`
- `tests/test_phase29_broker_parity.py`
- updated broker and ICT package exports

## What Phase 29 does not yet do

Phase 27 and Phase 28 are still NinjaTrader-specific at the final authorization/pre-submit boundary. FP Markets and Vantage now reach the same read-only supervised/risk-prepared boundary, but they still require a universal CFD authorization/revalidation layer before any future live execution adapter can be considered.
