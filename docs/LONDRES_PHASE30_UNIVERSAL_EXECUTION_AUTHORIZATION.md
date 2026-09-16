# Londres Phase 30 — Universal Execution Authorization

Phase 30 places NinjaTrader, FP Markets cTrader, and Vantage MT5 behind one deterministic authorization boundary. It still does **not** submit broker orders.

## Required Londres strategy gate

Every enabled account is downstream of the same strategy invariant:

```text
SMT_DETECTED + CSD_CONFIRMED + IOF_ALIGNED
```

Phase 30 also re-verifies the Phase 17 hard pre-broker authorization, exact direction, exact entry, executable stop, target, canonical symbol, and selected exit mode against the `TradeIntent`.

## NinjaTrader input

NinjaTrader enters Phase 30 only through an already-authorized Phase 27 account envelope. Phase 30 requires:

- `AUTHORIZED_FOR_EXECUTION_HANDOFF`;
- `execution_handoff_ready=true`;
- `order_authorized=true`;
- matching trade id, canonical symbol and direction;
- exact entry/stop/target/exit geometry;
- exact active futures contract;
- positive integer contract quantity;
- valid Phase 27 SHA-256 authorization fingerprint.

The Phase 30 universal fingerprint is cryptographically bound to the Phase 27 fingerprint, so a different upstream NinjaTrader authorization produces a different Phase 30 identity.

## FP Markets and Vantage input

FP Markets cTrader and Vantage MT5 enter through Phase 29. Phase 30 re-verifies:

- Phase 29 account is `READY` and preparation-ready;
- configured venue matches broker type;
- broker-reported identity still matches FP Markets or Vantage;
- exact broker symbol is present;
- Phase 29 supervision remained execution-data-ready;
- supervision canonical/broker symbols match the account envelope;
- nested Phase 23 account is `READY_FOR_EXECUTION_ADAPTER`;
- nested Phase 23 trade id, alias, canonical symbol and broker symbol match;
- exact entry/stop/target/exit geometry matches the `TradeIntent`;
- prepared volume is finite and positive;
- CFD volume unit is explicit (`units` or `lots`);
- risk fraction is exactly 3%, 5%, or 10%;
- current account equity is positive;
- projected cash risk is positive;
- projected equity risk does not exceed the selected risk fraction or the Londres 10% hard account limit.

## Universal authorization fingerprint

Each account that passes receives a SHA-256 fingerprint over the exact authorization geometry, including:

- trade id;
- account alias;
- venue and broker type;
- broker identity and exact broker symbol;
- canonical symbol and direction;
- exact account-specific lots/contracts;
- volume unit;
- selected risk fraction and account equity where applicable;
- entry, stop, target and exit mode;
- upstream Phase 27 fingerprint for NinjaTrader.

The fingerprint locks the authorization identity. Phase 30 does not consume it and does not send it to a broker order API.

## Multi-broker orchestration

`BEST_EFFORT` authorizes only accounts that pass while isolating failures.

`ALL_OR_NONE` revokes every otherwise-authorized account if any enabled NinjaTrader, FP Markets, or Vantage account fails Phase 30.

Account aliases must be unique across the combined universal batch.

## Execution boundary

A passing account may expose:

```text
execution_handoff_ready = true
order_authorized = true
```

Phase 30 always preserves:

```text
execution_enabled = false
order_submission_enabled = false
broker_order_placed = false
```

No `place_order`, `submit_order`, `amend_order`, `cancel_order`, `flatten`, cTrader trade execution, MT5 `order_send`, or NinjaTrader submission surface is added.

## Next boundary

Phase 31 should be a universal immediate pre-submit firewall. It must consume only Phase 30-authorized envelopes and immediately re-read current broker/account state, executable bid/ask, spread, tick-value metadata, account equity, lot/contract grid and duplicate-authorization state before any later live submission adapter can act.
