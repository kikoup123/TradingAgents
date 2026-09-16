# Londres Phase 16 — Structural Break-Even Management

Phase 16 protects capital with a deterministic structural break-even rule. It does not move the stop because price has travelled an arbitrary number of points or reached a fixed R multiple.

## Sequence

The break-even transition requires the complete sequence:

`ENTRY -> IOF RANGE EXIT -> NEW CONFIRMED FRACTAL -> BOS IN IOF DIRECTION -> STOP TO ENTRY`

Leaving the IOF entry range alone is not sufficient.

### Bearish

1. The deterministic Phase 13 short entry is active from the post-CSD bearish IOF range.
2. Price body-closes below the IOF entry range.
3. A new confirmed execution-timeframe swing low/fractal forms after that range exit.
4. A later candle body-closes below the new fractal low, confirming continuation/BOS in the bearish IOF direction.
5. The current stop moves to the exact original entry price.

### Bullish

The logic is mirrored:

1. Price body-closes above the bullish IOF entry range.
2. A new confirmed execution-timeframe swing high/fractal forms after the exit.
3. A later candle body-closes above that fractal high.
4. The current stop moves to the exact original entry price.

## Fractal confirmation

Phase 16 reuses the project's established confirmed-pivot convention. The default `pivot_span=2` requires two completed bars on each side of the pivot, preventing a still-forming high/low from being treated as a confirmed fractal.

The fractal source must occur after the IOF-range exit. The BOS scan begins only after that fractal itself has become confirmed.

## Break-even price

The break-even stop is exactly:

`break_even_price = exact_entry_price`

No spread, commission, fee, slippage or extra-tick compensation is added.

That means "break even" is structural price protection, not a guarantee of net-zero P/L after transaction costs. Spread and commissions remain separate accounting items.

## Preserved analytics

Moving the current stop does not rewrite the original trade plan. Phase 16 preserves:

- original entry;
- original executable stop;
- original projected cash risk;
- original projected equity-risk fraction;
- original R:R;
- IOF-range exit time and close;
- fractal source and confirmation time;
- fractal/BOS level;
- BOS trigger time and close;
- current stop after the transition.

This allows later replay/backtesting to compare the original trade geometry with the actual managed result.

## State machine

- `WAIT_FOR_ACTIVE_TRADE`
- `WAIT_FOR_IOF_EXIT`
- `WAIT_FOR_NEW_FRACTAL`
- `WAIT_FOR_BOS`
- `BREAK_EVEN_TRIGGERED`

The original stop remains active through every waiting state. Only `BREAK_EVEN_TRIGGERED` changes `current_stop_price` to the exact entry.

## Safety boundary

Phase 16 remains pre-broker. It never authorizes or places an order, and every serialized state keeps `order_authorized = false`.

The hard pre-broker order validator remains the next execution-safety phase.
