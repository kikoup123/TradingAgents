# Londres Phase 5 — Deterministic SMT Specification

Phase 5 will implement SMT as a deterministic confirmation layer. SMT must never authorize a trade by itself.

## Validation hierarchy

SMT has two different states and they must not be confused:

1. `SMT_DETECTED` — a deterministic relative-strength divergence exists between synchronized correlated instruments.
2. `SMT_VALIDATED` — the detected SMT is confirmed by BOTH CSD and Institutional Order Flow in the same directional thesis.

The hard validation rule is:

`SMT_VALIDATED = SMT_DETECTED AND CSD_CONFIRMED AND IOF_ALIGNED`

If either CSD or IOF is missing, conflicting, or still unconfirmed, the SMT remains contextual information only and cannot authorize an entry.

Directional examples:
- Bullish SMT + bullish CSD + bullish IOF -> validated bullish SMT.
- Bearish SMT + bearish CSD + bearish IOF -> validated bearish SMT.
- Bullish SMT + no CSD -> not validated.
- Bullish SMT + bullish CSD + bearish/unconfirmed IOF -> not validated.
- Bearish SMT + bearish CSD + bullish/unconfirmed IOF -> not validated.

CSD establishes the change in price delivery. IOF confirms which side has control after that delivery shift. SMT is therefore evidence of relative weakness/strength, while CSD + IOF provide the execution-side validation.

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
5. A detected SMT is not a validated SMT until same-direction CSD and IOF confirmation exist.
6. CSD and IOF must agree with the SMT directional thesis; conflicting order flow invalidates execution use of that SMT.
7. SMT is contextual confirmation only and can never bypass liquidity, profile, time/price, or risk requirements.
8. Do not infer missing bars or silently forward-fill structural highs/lows across unavailable market data.

## Planned deterministic output

- group
- timeframe
- reference_time
- direction
- smt_detected
- smt_validated
- divergence_type
- leader_symbol
- nonconfirming_symbols
- compared_levels
- polarity_map
- liquidity_context
- csd_confirmed
- csd_direction
- iof_control
- iof_aligned
- validation_state
- reason_codes

Recommended `validation_state` values:
- `NO_SMT`
- `SMT_DETECTED_WAIT_CSD`
- `SMT_DETECTED_WAIT_IOF`
- `SMT_DIRECTION_CONFLICT`
- `SMT_VALIDATED`

DXY must appear in `polarity_map` as `INVERSE` for the EURUSD/GBPUSD/DXY group.
