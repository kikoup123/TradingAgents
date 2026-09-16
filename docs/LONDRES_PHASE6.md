# Londres Phase 6 — Deterministic CSD + Post-CSD IOFC

Phase 6 removes manual CSD input from the SMT validation path and derives the delivery shift directly from OHLC structure.

## Core execution hierarchy

The deterministic execution sequence is now:

`Liquidity event -> SMT detected -> CSD confirmed -> NEW post-CSD IOFC -> SMT validated`

SMT is never an entry signal by itself.

## Bullish CSD

A bullish CSD requires:

1. A confirmed structural sell-side liquidity reference.
2. A first-time raid through that sell-side liquidity.
3. Identification of the highest down-close candle OPEN participating in the bearish delivery leg.
4. A candle BODY-CLOSE above that opening price.
5. The lowest price made from the raid through the confirmation becomes the protected low.

A wick through the CSD threshold does not confirm CSD.

## Bearish CSD

The bearish model is the inverse:

1. A confirmed structural buy-side liquidity reference.
2. A first-time raid through that buy-side liquidity.
3. Identification of the lowest up-close candle OPEN participating in the bullish delivery leg.
4. A candle BODY-CLOSE below that opening price.
5. The highest price made from the raid through confirmation becomes the protected high.

A wick through the threshold does not confirm CSD.

## Pivot confirmation and no look-ahead

Structural highs and lows are only eligible after the configured pivot span has completed on both sides. CSD cannot use a swing before its confirmation bar exists.

## SMT timing rule

For an SMT event to use a CSD for validation, the relevant CSD must confirm at or after the SMT reference time. A CSD that occurred before the current SMT event cannot validate that later SMT setup.

If a later opposite CSD replaces an earlier same-direction shift, the latest post-SMT CSD is the active delivery state and can produce a direction conflict instead of incorrectly validating the older thesis.

## Post-CSD IOFC

CSD changes delivery; IOFC confirms control.

After a bullish CSD:

- wait for a NEW down-close range formed after the CSD confirmation;
- a later candle must body-close above the full high of that range;
- only then is bullish post-CSD IOFC confirmed.

After a bearish CSD:

- wait for a NEW up-close range formed after the CSD confirmation;
- a later candle must body-close below the full low of that range;
- only then is bearish post-CSD IOFC confirmed.

A pre-CSD IOF range cannot be reused as the post-CSD confirmation range.

## Phase 6 SMT validation gate

The automatic gate is:

`SMT_VALIDATED = SMT_DETECTED AND POST_SMT_CSD_CONFIRMED AND POST_CSD_IOFC_CONFIRMED AND DIRECTIONS_ALIGNED`

The engine reports separately:

- all detected CSD events;
- the CSD selected for the current SMT event;
- post-CSD IOFC state;
- SMT detection state;
- final execution-gate state.

## Output stack

Phase 6 produces:

- Weekly profile context
- Multi-timeframe IOF context
- Daily OLHC/OHLC context
- H4 profile context
- Time & Price / opening levels / ONS
- Liquidity map and active draw
- SMT state with inverse DXY normalization
- CSD state
- selected post-SMT CSD
- post-CSD IOFC
- final SMT/CSD/IOFC execution gate

The next layers remain entry-model refinement, MMXM context, risk sizing, and broker execution. Phase 6 itself does not place orders.
