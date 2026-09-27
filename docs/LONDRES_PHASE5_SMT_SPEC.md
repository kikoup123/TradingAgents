# Londres Phase 5 — Deterministic SMT Engine

Phase 5 implements SMT as a deterministic relative-strength layer. SMT never authorizes a trade by itself.

## Validation hierarchy

SMT has two separate states:

1. `detected=true` — a deterministic structural divergence exists between synchronized correlated instruments.
2. `validated=true` — that SMT is confirmed by BOTH CSD and Institutional Order Flow in the same direction.

The hard rule is:

`SMT_VALIDATED = SMT_DETECTED AND CSD_CONFIRMED_AND_ALIGNED AND IOF_CONFIRMED_AND_ALIGNED`

If CSD or IOF is missing, conflicting, transitional, or unconfirmed, SMT remains contextual information only.

CSD establishes the change in price delivery. IOF confirms control after that delivery shift. SMT supplies relative-strength evidence; CSD + IOF validate its execution thesis.

## Correlation groups

### US index triad — same-direction
- NQ / NAS100 / US100 / NASDAQ
- ES / US500 / SPX / SP500
- YM / US30 / DJI / DOW

### FX / Dollar triad — inverse DXY
- EURUSD
- GBPUSD
- DXY

DXY is explicitly configured as `INVERSE`. The engine normalizes its polarity before comparison:
- EURUSD/GBPUSD higher-high event corresponds to a DXY lower-low event.
- EURUSD/GBPUSD lower-low event corresponds to a DXY higher-high event.

DXY is never compared as if it were positively correlated with EURUSD/GBPUSD.

### Gold relative-strength group
- XAUUSD
- XAUAUD
- XAUCAD

The group configuration supports explicit polarity per leg.

## Structural detection rules

1. All legs are intersected to exact common timestamps. Missing bars are never forward-filled.
2. Structural references are confirmed pivots requiring bars on both sides (`pivot_span`).
3. Only pivots confirmed before the event bar may be used; the engine does not use future information.
4. The engine detects the first take of the latest confirmed structural high or low.
5. Same-direction legs compare like-for-like events.
6. Inverse legs swap native high/low events into canonical group polarity before comparison.
7. A high-side nonconfirmation maps to bearish SMT.
8. A low-side nonconfirmation maps to bullish SMT.
9. Simultaneous conflicting high-side and low-side divergence on the same bar is treated as ambiguous rather than validated.

## Validation states

- `NO_SMT`
- `SMT_DETECTED_WAIT_CSD`
- `SMT_DETECTED_WAIT_IOF`
- `SMT_DIRECTION_CONFLICT`
- `SMT_VALIDATED`

Examples:
- Bullish SMT + bullish CSD + bullish IOF -> `SMT_VALIDATED`.
- Bearish SMT + bearish CSD + bearish IOF -> `SMT_VALIDATED`.
- Bullish SMT + no CSD -> `SMT_DETECTED_WAIT_CSD`.
- Bullish SMT + bullish CSD + unconfirmed IOF -> `SMT_DETECTED_WAIT_IOF`.
- SMT direction conflicting with CSD or IOF -> `SMT_DIRECTION_CONFLICT`.

## Deterministic output

The engine returns:
- `group`
- `timeframe`
- `reference_time`
- `direction`
- `detected`
- `validated`
- `validation_state`
- `divergence_type`
- `leader_symbols`
- `nonconfirming_symbols`
- `polarity_map`
- `compared_levels`
- `csd_direction`
- `iof_direction`
- `reason_codes`

## Orchestration

`LondresPhase5Engine` extends the stack:

`Weekly -> IOF -> Daily -> H4 -> Time & Price -> Liquidity -> SMT`

The Phase 5 orchestrator reads IOF control from the requested validation timeframe and accepts a deterministic `csd_direction` input. CSD detection itself remains a separate engine so Phase 5 does not invent CSD logic that has not yet been encoded.
