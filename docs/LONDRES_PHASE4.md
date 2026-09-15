# Londres Phase 4 — Deterministic Liquidity Engine

Phase 4 adds a deterministic liquidity map on top of the validated Weekly -> Daily -> H4 -> Time & Price stack.

## Purpose

The Liquidity Engine converts ICT liquidity concepts into explicit machine-readable state before SMT or CSD are evaluated. It does not ask an LLM to guess where liquidity is.

## Structural liquidity

Completed swing highs are classified as buy-side liquidity (BSL). Completed swing lows are classified as sell-side liquidity (SSL).

The highest confirmed swing high is classified as external range liquidity (ERL). The lowest confirmed swing low is also ERL. Confirmed swing points inside those extremes are internal range liquidity (IRL).

Only completed pivots are used. A pivot requires bars on both sides, so the most recent unfinished structure is not retrospectively labelled.

## Liquidity state

Each pool can be:

- `ACTIVE` — liquidity remains available.
- `RAIDED_RECLAIMED` — price wicked through the pool and body-closed back through the level.
- `CONSUMED` — price traded through the pool and body-closed beyond it.

For BSL, a wick above the level followed by a close below it is a raid/reclaim. A close at or above the breached level is treated as consumption.

For SSL, a wick below the level followed by a close above it is a raid/reclaim. A close at or below the breached level is treated as consumption.

## Protected liquidity

When order flow is bullish, the latest active structural SSL is marked as protected liquidity. When order flow is bearish, the latest active structural BSL is marked as protected liquidity.

This gives the later CSD/IOFC engine an explicit structural invalidation reference instead of deriving it from an LLM narrative.

## Active draw on liquidity

With bullish order flow, the engine searches for active BSL above current price. With bearish order flow, it searches for active SSL below current price.

External liquidity is prioritized over internal liquidity. If multiple external pools remain, the nearest one in the active direction becomes the current draw.

If no qualifying pool exists, the active draw is unresolved rather than fabricated.

## ONS integration

Completed or active New York, London and Asia ONS highs/lows from Phase 3 are imported as named external liquidity pools. Their source names remain explicit, such as `NY_ONS_HIGH` and `NY_ONS_LOW`.

## Dealing range location

When both external BSL and external SSL are available, the engine calculates the dealing-range equilibrium and classifies current price as:

- `PREMIUM`
- `DISCOUNT`
- `EQUILIBRIUM`

This is descriptive context only. It is not an entry signal.

## Phase 4 output

The orchestration chain is now:

`Weekly Profile -> HTF IOF -> Daily OLHC/OHLC -> H4 Profile -> Time & Price -> Liquidity`

The next layer is SMT. SMT must be calculated from synchronized correlated instruments and must not be accepted as a standalone trade trigger.
