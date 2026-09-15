# Londres Phase 7 — Bias, narrative and price delivery

Phase 7 composes Phase 6 with transcript-derived context. The Phase 1–6 profile,
clock, Time & Price, liquidity, SMT, CSD and post-CSD IOFC interfaces remain the
foundation. Source rules, timestamps and interpretation limits are in
[LONDRES_TRANSCRIPT_RULES.md](LONDRES_TRANSCRIPT_RULES.md).

## New engines

- `FairValueEngine`: FVG geometry/lifecycle, structural close-through qualification,
  fair-valuation reference and observable order-pairing return/rejection evidence.
- `PriceDeliveryEngine`: engineer/neutralize/distribute/rebalance/redistribute,
  stops/imbalance cycle, efficient-range proxy and displacement departure.
- `NarrativeEngine`: per-timeframe bias, buy-/sell-side curve, prior candle draw,
  OHLC/OLHC expectation, parent control and matrices, retracement versus reversal,
  local versus parent-relative LRLR/HRLR, and bounded narrative target.
- `LondresPhase7Engine`: existing Phase 6 payload plus those outputs and a separate
  `narrative_gate`. `state_update` maps the complete result to `AgentState` keys.

```python
from tradingagents.ict import LondresPhase7Engine

engine = LondresPhase7Engine()
context = engine.analyze(
    timeframe_bars=closed_timeframe_bars,
    hierarchy=("3M", "1M", "1W", "1D", "4H", "1H", "15m", "5m"),
    intraday_bars=intraday_bars,
    minute_bars=minute_bars,
    csd_bars=closed_timeframe_bars["5m"],
    csd_timeframe="5m",
    smt_bars=correlated_bars,  # exact synchronized NQ/ES/YM inputs
    smt_group="US_INDEX",
    smt_timeframe="5m",
    as_of=analysis_time,
)
state_update = engine.state_update(context)
```

The adapter is explicit; the generic stock-analysis graph does not automatically
fetch or aggregate these feeds. A caller must supply the candle data and apply the
state update before LLM narrative generation. There are no broker orders here.

## Causality and candle contract

Supply **completed candles only**. Indexes used by Phase 6/7 must be unique
DatetimeIndexes in ascending order (the boundary sorts inputs), with labels that
make each candle available no earlier than its close. A completed daily candle
labelled by its opening timestamp must be relabelled by the provider; `as_of`
cannot recover unfinished candles from final OHLC. Preserve existing fixed-UTC-4
session semantics when preparing minute/intraday feeds for Time & Price.

Naive input timestamps and naive `as_of` values mean fixed UTC-4. They are
localized before comparing against timezone-aware streams, so a UTC cutoff cannot
shift a naive series by four hours.

`as_of` is applied across every Phase 6 branch before analysis, including profile
inputs and post-CSD IOFC. No FVG forms before candle three closes. Pivots must be
confirmed before a structural impulse/raid can use them. No future touch may
qualify an earlier narrative. Empty cutoffs and invalid/duplicate/non-finite data
fail explicitly. Full replay and manually truncated histories produce the same
results. Event snapshots contain evidence known at that event.

## Interpreting the output

- `bias_narrative.bias_timeframe` is the highest supplied horizon; absent larger
  horizons are listed. Unknown control stays unknown.
- `previous_candle_draw` compares the last completed bar to its predecessor; it
  does not forecast that every bar must take that level.
- `parent_matrices` contains available parent IOF ranges and FVGs opposing local
  delivery, along with touch time and distance. No arbitrary “near” threshold.
- `liquidity_run` describes local flow within parent boundaries;
  `parent_relative_run` describes its relation to the immediate parent.
- `narrative_draw` can be an IRL FVG for rebalancing, a parent matrix boundary, or
  an active Phase 4 liquidity objective. Missing targets remain unresolved.
- `reversal` identifies a CSD + live post-CSD IOFC at a pre-existing parent array.
  It does not promote a lower-timeframe reversal to parent control automatically.
- The Phase 6 `execution_gate` retains its original meaning. The new
  `narrative_gate` additionally requires matching bias/control/validation direction,
  matching LTF candle stream, aligned context and a continuation liquidity target.

## Existing validation fixes

Confirmed IOF ranges remain eligible for later invalidation even after candidate
retention rolls. Post-CSD IOFC returns a still-valid confirmation under current
matching control. Breaking the selected CSD protected extreme by body close blocks
its IOFC validation. Historical Phase 6 analysis truncates all input streams,
including post-CSD scans, at the same cutoff.

## Validation scope

Tests cover mirrored bullish/bearish examples, strict body thresholds, wick-only
fills, unknown parent control, delayed pairing, ordered delivery events, matrix
boundaries, invalidated IOFC, candidate retention, complete stack replay and state
serialization. These test deterministic behavior against the rule register;
they are not backtests, a profitability claim, or live-execution certification.
The PR remains draft. Full MMXM pattern recognition, calibrated thresholds,
entry/risk sizing and broker execution remain future layers.
