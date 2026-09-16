# Londres Phase 19–20 — Universal Broker Contract and Multi-Account Preparation

## Purpose

Phases 19 and 20 decouple the Londres strategy from any single broker and add deterministic multi-account trade-intent replication.

The Londres strategy remains the single source of truth:

`SMT -> CSD -> post-CSD IOF -> exact entry -> executable stop -> risk -> target -> structural break-even -> hard validation`

Broker-specific details are handled only after that strategy state exists.

## Phase 19 — Universal broker contract

The broker layer now exposes a common contract for:

- broker/account identity using aliases and masked account identifiers;
- broker/platform capabilities;
- canonical symbol -> broker symbol translation;
- account balance/equity/margin/currency;
- broker-native instrument tick/volume constraints;
- quotes;
- a canonical `TradeIntent` independent of cTrader, NinjaTrader, MT5 or other future adapters.

The universal contract does **not** contain broker credentials, full account ids or demo/live classification. Demo/live remains adapter-private and appears in public state only as `HIDDEN_INTERNAL`.

### Canonical symbols

The strategy can reason in canonical instruments such as:

- `GOLD`
- `NASDAQ`
- `SP500`
- `DOW`
- `DXY`
- `EURUSD`
- `GBPUSD`
- `BTC`
- `CRUDE`

Aliases such as `XAUUSD`, `US100`, `NAS100`, `NQ`, `US500`, `ES`, `US30` and `YM` normalize to canonical names. Broker symbols are never guessed. Each account must provide an explicit mapping such as:

- canonical `NASDAQ` -> cTrader `US100`
- canonical `NASDAQ` -> MT5 `NAS100`
- canonical `NASDAQ` -> NinjaTrader contract `NQ DEC26`

This is important for futures because the active contract changes over time. Automatic futures rollover remains a future adapter responsibility.

## Phase 20 — Multi-account trade-intent replication

One validated Londres `TradeIntent` can be prepared for multiple enabled accounts at the same time.

The orchestrator does **not** copy a raw lot/contract quantity from a master account. Instead, every account independently receives the same strategy intent and calculates its own safe volume from:

- that account's current equity;
- that account's selected 3%, 5% or 10% risk tier;
- the exact Londres entry and executable stop;
- that broker's tick size and verified tick value in account currency;
- minimum, maximum and step volume constraints.

Therefore two accounts with different equity can participate in the same setup while carrying different lot/contract sizes.

## Per-account risk tiers

Each managed account can independently use exactly one approved risk tier:

- 3%
- 5%
- 10%

The 10% hard ceiling remains unchanged. A manual raw lot/contract quantity is not accepted as the sizing authority.

## Exact Londres management contract

The orchestrator preserves the existing management rules. It does not silently simplify them for a broker.

If `HOLD_HTF_LIQUIDITY` is selected, the account must support partial close and the final broker volume must be exactly splittable into:

- 60% partial at the configured -2.5 management trigger;
- 40% runner to the configured HTF liquidity target.

If exact 60/40 is impossible on an account's volume grid, that account returns `BLOCKED_HOLD_SPLIT` rather than changing the fractions.

Structural break-even remains part of the strategy contract. However, after future broker execution the BE price must use **that account's actual fill price**, not the canonical intended entry. Phase 20 therefore records:

`break_even_price_source = ACTUAL_ACCOUNT_FILL_PRICE`

and requires post-fill risk revalidation before management.

## Broker capability validation

Before an account is marked preparation-ready, its platform capabilities are checked against the Londres intent. Required features can include:

- market order support for `MARKET_ON_SIGNAL`;
- server-side stop loss;
- server-side target;
- stop amendment for structural break-even;
- partial close for the 60/40 hold contract.

Capability mismatch fails closed for that account.

## Partial failure isolation

Two orchestration policies are defined:

### `BEST_EFFORT`

Default policy. A blocked account does not prevent other valid accounts from becoming preparation-ready.

Example:

- Account A: ready
- Account B: ready
- Account C: exact 60/40 impossible -> blocked

Batch status: `PARTIAL_READY`.

### `ALL_OR_NONE`

The batch is preparation-ready only if every enabled account is ready. If one enabled account fails, batch status becomes `BLOCKED`.

No actual order is submitted under either policy in Phase 20.

## cTrader integration

The Phase 18 cTrader read-only connector is wrapped by `CTraderUniversalReadOnlyAdapter`, so cTrader now conforms to the universal broker contract.

The wrapper can expose:

- sanitized account data;
- canonical/broker symbol identity;
- tick size and pip size;
- broker min/max/step volume;
- live quotes.

It deliberately does not yet produce a verified tick value in account currency. Until that is implemented, cTrader multi-account preparation returns `WAIT_FOR_VERIFIED_TICK_VALUE` rather than guessing.

## Security boundary

Phase 19–20 maintains these invariants:

- credentials are not stored in AgentState;
- full account ids are not exposed;
- demo/live is private to adapters;
- account output is masked;
- no broker adapter in this phase exposes `place_order`;
- `order_authorized = false`;
- `broker_order_placed = false`.

`READY_FOR_EXECUTION_ADAPTER` means only that the account-specific package is structurally and mathematically ready for a future execution adapter after the remaining broker-native validation layers are implemented.

## Current implementation files

- `tradingagents/brokers/contracts.py`
- `tradingagents/brokers/symbols.py`
- `tradingagents/brokers/ctrader_adapter.py`
- `tradingagents/ict/multi_account.py`
- `tradingagents/ict/phase19.py`
- `tradingagents/ict/phase20.py`
- `tests/test_multi_account_orchestrator.py`
- `tests/test_ctrader_universal_adapter.py`

## Next work

The next broker hardening work should add verified broker-native tick value/account-currency conversion, then concrete NinjaTrader and MT5/Vantage adapters. Persistent heartbeat/reconnect, quote freshness, post-fill revalidation and actual order lifecycle remain separate phases before any demo/live automated execution is enabled.
