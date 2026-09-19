# Londres Phase 22 — Broker Connection Supervision

Phase 22 adds a broker-agnostic, fail-closed supervision layer between broker-native risk normalization and any future execution adapter.

It does **not** submit, authorize, amend, partially close, or close broker orders.

## Purpose

A validated Londres setup is not enough to safely hand a package to a broker. The execution environment must also prove that the broker data path is alive and that the market-dependent values used by sizing are fresh.

Phase 22 therefore performs an application-level heartbeat over the universal broker adapter and verifies:

- public connection state;
- account readability;
- masked account identity only;
- current timestamped bid/ask availability;
- quote freshness against an explicit maximum age;
- verified account-currency tick value availability;
- tick-value/conversion freshness when the valuation has a market timestamp;
- optional deterministic reconnect attempts;
- future timestamps only within an explicit clock-skew tolerance.

## Explicit policy — no silent timing assumptions

The caller supplies `BrokerSupervisionPolicy` with:

- `quote_max_age_ms`;
- `tick_value_max_age_ms`;
- `max_reconnect_attempts`;
- `future_timestamp_tolerance_ms`.

The strategy engine does not invent quote age, valuation age, clock skew, or reconnect-count thresholds.

## Status model

Phase 22 can return:

- `HEALTHY`;
- `RECONNECTED`;
- `DISCONNECTED`;
- `ADAPTER_ERROR`;
- `WAIT_FOR_QUOTE`;
- `STALE_QUOTE`;
- `WAIT_FOR_VERIFIED_TICK_VALUE`;
- `STALE_TICK_VALUE`;
- `INVALID_TIMESTAMP`.

Only `HEALTHY` and `RECONNECTED` produce `execution_data_ready=true`.

## Tick-value freshness

A broker-native tick value may be either static or market-dependent.

If `tick_value_timestamp_ms` exists, the value is treated as market-dependent and must not exceed the explicit `tick_value_max_age_ms` threshold.

If a verified tick value has no market timestamp, Phase 22 treats it as non-market-dependent metadata. This supports cases such as exchange-defined futures tick values or a cTrader symbol whose P/L quote asset already equals the account deposit asset and therefore needs no live FX conversion leg.

An unresolved tick value always fails closed.

## Reconnect behavior

Adapters may expose an optional `reconnect()` method. The supervisor calls it only up to the explicit `max_reconnect_attempts` limit.

`CTraderUniversalReadOnlyAdapter` now exposes a read-only reconnect hook that:

1. closes the previous connector session;
2. reconnects using the existing view-only OAuth configuration;
3. does not change account environment, permissions, or execution capability.

Reconnect does not authorize trading.

## Privacy and execution boundary

Normal supervision output exposes only masked/public account information. Demo/live classification stays internal to the adapter.

The Phase 22 payload always preserves:

```text
read_only = true
order_submission_enabled = false
order_authorized = false
broker_order_placed = false
```

`execution_data_ready=true` means only that the read-only account/quote/valuation data path passed the current supervision heartbeat.

## Integration order

```text
Phase 18 read-only broker connection
→ Phase 19 universal broker contracts
→ Phase 20 multi-account preparation
→ Phase 21 broker-native account-currency risk normalization
→ Phase 22 connection / quote / valuation supervision
→ future broker execution adapter
```

Before live or demo order submission is introduced, the execution path must require a fresh Phase 22 result in addition to the existing deterministic Londres validation stack.
