# Londres Phase 5 — Deterministic SMT Specification

Phase 5 will implement SMT as a deterministic confirmation layer. SMT must never authorize a trade by itself.

## Correlation groups

### US index triad — same-direction comparison
- NQ / NASDAQ
- ES / S&P 500
- YM / Dow Jones

These are evaluated as positively correlated markets. A structural divergence occurs when one market takes or fails to take a corresponding high/low while the others do not confirm.

### FX / Dollar triad — inverse DXY comparison
- EURUSD
- GBPUSD
- DXY

DXY is explicitly an inverse leg. The engine must normalize DXY polarity before comparing structure.

Examples:
- EURUSD and GBPUSD print a higher high while DXY fails to print the corresponding lower low -> potential bearish SMT in EURUSD/GBPUSD / bullish relative strength in DXY.
- EURUSD and GBPUSD print a lower low while DXY fails to print the corresponding higher high -> potential bullish SMT in EURUSD/GBPUSD / bearish relative weakness in DXY.
- DXY takes a previous high while EURUSD/GBPUSD fail to take corresponding lows -> potential bullish SMT in EURUSD/GBPUSD relative to DXY.
- DXY takes a previous low while EURUSD/GBPUSD fail to take corresponding highs -> potential bearish SMT in EURUSD/GBPUSD relative to DXY.

The engine must not compare DXY as if it were positively correlated with EURUSD/GBPUSD.

### Gold relative-strength group
- XAUUSD
- XAUAUD
- XAUCAD

This group is configurable and should support explicit polarity flags per instrument.

## Core SMT rules

1. Synchronize instruments to the same timestamps and timeframe before comparison.
2. Compare confirmed structural highs/lows and liquidity events, not arbitrary single ticks.
3. Support both same-direction and inverse relationships using explicit polarity configuration.
4. Record which leg created the divergence, which legs confirmed or failed to confirm, and the exact structural levels involved.
5. SMT is contextual confirmation only. A valid trade still requires the rest of the Londres sequence, such as liquidity event, CSD/IOFC, profile alignment, and risk validation.
6. Do not infer missing bars or silently forward-fill structural highs/lows across unavailable market data.

## Planned deterministic output

- group
- timeframe
- reference_time
- direction
- smt_confirmed
- divergence_type
- leader_symbol
- nonconfirming_symbols
- compared_levels
- polarity_map
- liquidity_context
- reason_codes

DXY must appear in `polarity_map` as `INVERSE` for the EURUSD/GBPUSD/DXY group.
