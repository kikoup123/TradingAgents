# Londres Trading Agent — Phase 2

Phase 2 adds deterministic Daily and H4 profile logic on top of the Phase 1
Weekly Profile + IOF/IOFC engine.

## Canonical clock

The Londres profile clock is **fixed UTC-4 all year**. It intentionally does
not use `America/New_York` DST transitions.

The trading day rolls at **18:00 UTC-4**. Its H4 candles are:

- 18:00
- 22:00
- 02:00
- 06:00
- 10:00
- 14:00

## Weekly -> Daily inheritance

The Weekly Profile engine supplies the current day type.

- bullish continuation/reversal candidate -> Daily `OLHC` hypothesis
- bearish continuation/reversal candidate -> Daily `OHLC` hypothesis
- bullish-week retracement / return-to-range candidate -> bearish `OHLC` hypothesis
- bearish-week retracement / return-to-range candidate -> bullish `OLHC` hypothesis
- range/unresolved weekly context -> Daily profile remains unresolved

The Daily engine does not confirm a profile from weekday or candle colour alone.
Confirmation requires the observed OHLC path, matching deterministic order-flow
control, and a protected Daily extreme.

## H4 driver logic

Phase 2 treats the **06:00 H4** as the driver defined by the supplied H4 profile
model:

1. Reversal established before the driver -> 06:00 continuation expected.
2. No reversal established before the driver -> 06:00 reversal expected.
3. If a completed 06:00 reversal driver fails to reverse -> H4 profile invalidated.

A reversal during the 02:00-06:00 H4 is classified as `LONDON_REVERSAL`.
After a confirmed 06:00 driver, the profile transitions to `NY_CONTINUATION` or
`NY_REVERSAL` while the Daily profile remains valid.

## Location context

The H4 result can carry these location labels:

- `IRL`
- `ERL_TO_IRL`
- `OPR`
- `OB_CONTINUATION`

Phase 2 does **not** guess these locations. They are explicit inputs until the
Liquidity / PD Array engine is implemented. This prevents the agent from
inventing structural context before those rules are encoded.

## New modules

- `tradingagents/ict/daily_profile.py`
- `tradingagents/ict/h4_profile.py`
- `tradingagents/ict/phase2.py`

New LangGraph state slots:

- `daily_profile_state`
- `h4_profile_state`
- `profile_stack_state`

## Next phase

Phase 3 will port the user's `LONDRES ICT OPENING PRICES + ONS` Pine logic into a
deterministic Time & Price engine: Asian Open, Midnight Open, London, 02:00,
07:30, 08:30, 09:30, 10:00, 13:30, 14:00, settlement, ONS ranges, EQ, and
standard-deviation projections.
