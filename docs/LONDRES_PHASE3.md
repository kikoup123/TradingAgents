# Londres Trading Agent — Phase 3

Phase 3 adds the deterministic **Time & Price Engine** derived from the user's
`LONDRES ICT OPENING PRICES + ONS` Pine indicator.

## Canonical vs source clocks

The Weekly / Daily / H4 profile stack remains on **fixed UTC-4 all year**.
That convention determines the Londres trading-day boundary and H4 profile.

The Time & Price engine intentionally preserves the source timezones used by the
Pine script so its levels can be compared against TradingView:

- Opening prices: `America/New_York`
- New York ONS: `America/Chicago`, 04:00-08:00 local
- London ONS: `Europe/London`, 05:00-07:00 local
- Asia ONS: `Asia/Tokyo`, 07:00-09:00 local

All resulting timestamps are normalized back to fixed UTC-4 in the agent output.
This preserves Pine parity without changing the Phase 2 profile clock.

## Opening-price map

The engine calculates these exact opening prices when the configured minute bar
exists:

- 19:30 Asian Open
- 00:00 Midnight Open
- 01:30 London / LD Open
- 02:00 processing open
- 07:30 NY Premarket
- 08:30 open
- 09:30 Equities Open
- 10:00 open
- 13:30 Afternoon Open
- 14:00 open
- 18:00 Settlement

Missing source bars are returned as `UNAVAILABLE`; the engine does not interpolate
or guess an opening price.

For every available opening level the engine also reports:

- current relation: `ABOVE`, `BELOW`, or `AT`
- whether price revisited the level after creation
- whether candle-body closes crossed from one side to the other
- timestamp of the latest body-close cross

Support/resistance, raid/reclaim, and draw classification remain reserved for the
later Liquidity engine.

## ONS engine

For New York, London, and Asia ONS the engine calculates:

- session high
- session low
- equilibrium
- total range
- upper standard-deviation projections
- lower standard-deviation projections
- active / complete session state

The default Pine behavior is preserved:

- range source: `Wicks`
- half-deviation mode enabled
- projection count: `2`

Therefore the default projection ladder is:

- `+0.5`
- `+1.0`
- `-0.5`
- `-1.0`

`Bodies` mode and full-deviation mode are also supported through `ONSConfig`.

## New modules

- `tradingagents/ict/time_price.py`
- `tradingagents/ict/phase3.py`

New LangGraph state slots:

- `time_price_state`
- `londres_context_state`

## Profile stack after Phase 3

```text
Weekly Profile
    -> HTF Order Flow
    -> Day Type
    -> Daily OLHC / OHLC
    -> H4 Profile
    -> Opening Prices / ONS / EQ / Deviations
```

The next phase should build the **Liquidity Engine** so these deterministic price
levels can be classified as internal/external liquidity, swept/untouched,
reclaimed, protected, or active draw targets before SMT and CSD are evaluated.
