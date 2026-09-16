# Londres Phase 8 — Deterministic MMXM Recognition + Entry-State Contract

Phase 8 composes the existing deterministic Londres facts into a conservative
Market Maker Model recognizer. It does **not** place orders, calculate position
size, or connect to a broker.

## MMXM landmarks

The transcript-derived model is represented as:

`Original Consolidation -> Curve -> Matrix -> Smart Money Reversal -> Continuation -> Terminal`

The engine does not call every consolidation or every reversal an MMXM. A model
requires all of the following component facts:

1. An Original Consolidation candidate already classified by `PriceDeliveryEngine`
   as `DISPLACEMENT_CONFIRMED`.
2. The directional FVG departure from that OC, used as the curve-to-matrix
   direction. No ATR/velocity threshold is invented.
3. A defined structural dealing range with external high, external low and EQ.
4. A parent matrix in the correct half of that range:
   - bullish departure -> premium matrix -> potential **MMSM**;
   - bearish departure -> discount matrix -> potential **MMBM**.
5. Matrix touch does not equal reversal.
6. The local reversal must already have a same-direction CSD and a still-valid
   NEW post-CSD IOFC at the parent matrix.
7. In addition, the matrix must show a deterministic failure-swing or breaker
   signature before Phase 8 calls the Smart Money Reversal complete.

## Failure-swing engineering rule

The transcript identifies failure swing as an MMXM reversal signature but does
not provide a universal numerical detector. Phase 8 therefore uses an explicit,
symmetric structural convention rather than pretending the lecture supplied one.

Bearish failure swing:

- a confirmed structural high trades at/through the premium matrix;
- a later confirmed high fails below that first high;
- an intervening confirmed low exists;
- a later candle body-closes below that intervening low.

Bullish failure swing is the exact inverse: matrix low -> higher low -> body-close
above the intervening high.

The signature only counts at the MMXM matrix. A matching pattern elsewhere is not
an MMXM Smart Money Reversal.

## Breaker engineering rule

A breaker signature requires an old-direction institutional order-flow range to:

1. overlap the MMXM matrix;
2. have previously been confirmed;
3. be body-close invalidated after the matrix is reached;
4. later be retested from the new side and hold with a body close in the new
   direction.

This deliberately reuses the deterministic full-range IOF rules already present
in the project. It is a conservative price-action proxy for the transcript's
breaker signature.

## Smart Money Reversal gate

A completed Phase 8 Smart Money Reversal therefore requires:

`CORRECT MATRIX LOCATION + (FAILURE SWING OR BREAKER) + CSD + POST-CSD IOFC`

SMT remains upstream in the existing Phase 6 execution gate. Phase 8 does not
replace or weaken that gate.

## MMXM stages

- `NO_MODEL`
- `CURVE_TO_MATRIX`
- `AT_MATRIX_WAIT_REVERSAL`
- `AT_MATRIX_WAIT_SIGNATURE`
- `SMART_MONEY_REVERSAL_CONFIRMED`
- `CONTINUATION_PHASE`
- `TERMINAL_REACHED`
- `INVALIDATED`

For an MMBM, the first post-reversal continuation observation is a
`REACCUMULATION`. For an MMSM it is `REDISTRIBUTION`.

## Terminal

The preferred terminal is external liquidity in the final MMXM direction:

- MMBM -> external buy-side liquidity;
- MMSM -> external sell-side liquidity.

If an external target has already been consumed or raided after the reversal,
the model reports `TERMINAL_REACHED`. Otherwise the active directional liquidity
draw is reported as the developing terminal when available.

## Fractal inheritance

MMXM is fractal. Phase 8 reports the relationship of every child model to the
nearest higher-timeframe model:

- `ROOT_MODEL`
- `ALIGNED_CHILD_MODEL`
- `COUNTER_MODEL_WITHIN_PARENT`
- `PARENT_UNRESOLVED`
- `LOCAL_UNRESOLVED`

A lower-timeframe counter-model does **not** invalidate an intact higher-timeframe
MMXM. The default entry contract waits rather than using that counter-model as a
final execution direction.

## Initial entry-state contract

Phase 8 adds a deliberately narrow state contract:

- `WAIT`
- `REVERSAL_READY`
- `CONTINUATION_READY`
- `INVALIDATED`

`REVERSAL_READY` requires a completed MMXM Smart Money Reversal plus the existing
Phase 7 narrative gate and aligned Phase 6 SMT/CSD/post-CSD-IOFC execution gate.

`CONTINUATION_READY` requires the same gates after the first observed post-reversal
reaccumulation/redistribution phase.

The returned field `order_authorized` is always `false`. Risk sizing, stop/target
construction, broker checks and order placement belong to later phases.

## Non-claims

Phase 8 does not claim:

- profitability or probability estimates;
- that every valid discretionary ICT failure swing is captured by the strict
  engineering detector;
- reclaimed-order-block or Unicorn entry selection;
- risk sizing;
- cTrader/FP Markets execution.

Those remain separate later layers so that deterministic market understanding is
not mixed with order authorization.
