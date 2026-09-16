# Londres Phase 17 — Hard Pre-Broker Order Validator

Phase 17 is the final deterministic fail-closed firewall before a future broker adapter may receive an order request. It does **not** connect to cTrader, place an order, modify an order, or expose broker credentials.

## Authorization contract

`order_authorized` may become `true` only when all deterministic layers agree:

1. Phase 13 exact post-CSD IOF retracement entry is `ENTRY_TRIGGERED`.
2. Phase 14 executable stop is `READY` and still references the Trader-selected structural stop source and anchor.
3. Phase 15 trade calculation is `READY`.
4. Phase 16 has a recognized management state.
5. Entry, direction and original executable stop are identical across phases.
6. Broker instrument symbol matches the calculated symbol and risk-sizing symbol.
7. Trader risk is exactly 3%, 5%, or 10%; 10% remains the absolute hard ceiling.
8. Projected cash/equity risk does not exceed the selected tier or optional lower cash cap.
9. Position volume equals the deterministic risk-engine output and is on the broker volume grid.
10. Target mode and deterministic CSD target agree with the hard-validated Trader selection.
11. HOLD mode preserves the exact 60% / 40% contract and both child quantities are broker-executable.
12. Current stop state agrees with structural break-even management.

Any failed invariant produces a typed failure status and `order_authorized=false`. There is no manual override in this layer.

## Original stop versus current stop

Phase 17 deliberately distinguishes risk-at-entry geometry from later management.

Before structural break-even:

- `original_stop_price = Phase 14 executable stop`
- `current_stop_price = original_stop_price`

After Phase 16 reports `BREAK_EVEN_TRIGGERED`:

- `original_stop_price` remains unchanged for analytics;
- `current_stop_price = exact_entry_price`;
- `break_even_price = exact_entry_price`;
- no spread, commission, fee, slippage or extra-tick compensation is added.

Original projected cash risk, equity-risk fraction and R:R must remain immutable after the stop moves to break-even.

## Explicit statuses

The validator exposes:

- `AUTHORIZED`
- `WAIT_FOR_ENTRY`
- `WAIT_FOR_EXECUTABLE_STOP`
- `WAIT_FOR_TRADE_CALCULATION`
- `WAIT_FOR_MANAGEMENT_STATE`
- `INVALID_TRADER_SELECTION`
- `DIRECTION_MISMATCH`
- `ENTRY_GEOMETRY_MISMATCH`
- `STOP_GEOMETRY_MISMATCH`
- `SYMBOL_MISMATCH`
- `ACCOUNT_POLICY_VIOLATION`
- `RISK_POLICY_VIOLATION`
- `VOLUME_POLICY_VIOLATION`
- `TARGET_CONTRACT_VIOLATION`
- `BREAK_EVEN_POLICY_VIOLATION`

## HOLD contract

`HOLD_HTF_LIQUIDITY` is accepted only when the deterministic Phase 12/15 contract remains intact:

- 60% partial at the deterministic `-2.5` trigger;
- 40% runner;
- partial + runner volume equals the original deterministic position size;
- both quantities are on the broker volume step and satisfy broker minimum size;
- runner target remains beyond the partial target in trade direction.

The validator never approximates 60/40.

## Broker boundary

Phase 17 authorization means only that the deterministic order package is eligible for a future broker adapter. `broker_order_placed` remains `false` in Phase 17.

The next broker phase must still provide authenticated account connectivity, market/symbol metadata, execution permissions, environment privacy, broker-side rejection handling and order lifecycle reconciliation.
