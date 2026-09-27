# Londres — ICT transcript rule register

This register records a full reading of all six available SRT uploads. Timestamps
refer to the supplied Spanish subtitles, whose automatic translations sometimes
confuse *up-close/down-close*, *imbalance/efficiency*, and *breaker*. The existing
Londres definitions resolve those ambiguities; terminology has not been renamed.
The MP4 was not needed for this implementation and was not inspected. Chart-only
coordinates and promises of material in another lecture are not invented.

## Source inventory

| ID | Uploaded transcript | Coverage read |
| --- | --- | --- |
| IOF | Institutional Orderflow — The One Thing in Price That Doesn’t Lie | 00:11–13:14, all 437 subtitle cues |
| BIAS | Framing Bias & Building Narrative | 00:02–14:47, all 500 cues |
| RUN | Framing High and Low Resistant Liquidity Runs | 00:02–08:12, all 259 cues |
| MEP | Market Efficiency Paradigm Mental Frameworks | 00:02–16:16, all 583 cues |
| FVG | The Fair Value Gap Lecture: Understanding Fair Value Gap, Fair Valuation and Order Pairing and Its Significance Pt 1 | 00:02–11:02, all 362 cues |
| MMXM | MMXM | 00:02–13:05, all 441 cues |

## Institutional order flow

- **IOF-01** (00:17–00:59, 02:14–03:50): observe which opposing-close candles
  and PD Arrays support delivery. Down-close ranges supporting price describe
  the buy-side curve; up-close ranges resisting price describe the sell-side
  curve. No tick/order-book feed is required for these price observations.
- **IOF-02** (00:54–02:11, 06:21–07:24): each timeframe has its own control.
  Lower-timeframe bullish flow can deliver into a parent premium array inside a
  larger bearish program; the inverse applies. Counter-flow alone does not
  invalidate parent control. Wait for the LTF to realign at the parent array.
- **IOF-03** (05:20–07:00, 10:39–11:19): array negation and subsequent support
  by arrays in the opposite direction are evidence of a shift, rather than a
  prediction from reaching the location alone.
- **IOF-04** (07:37–10:38): identify the parent objective and distinguish stops
  to imbalance from imbalance to stops. A weekly sell-side curve can be only a
  retracement into a six-month discount array.
- **IOF-05** (11:31–12:34): with bearish control, lows are LRLR objectives and
  highs are protected/HRLR; inverse for bullish. Counter-flow objectives are
  bounded by internal arrays instead of assuming opposing ERL will break.
- **IOF-06** (12:42–13:14): failure swing/breaker are reversal signatures, and
  the reasoning is fractal. This lecture does not give a complete numerical
  failure-swing/breaker detector.

**Implementation:** retain the Phase 1 full-range body-close IOFC and invalidation
rules, Phase 6 CSD opening-price thresholds and NEW post-CSD range requirement.
`OrderFlowEngine` now keeps confirmed ranges live after candidate retention rolls
and excludes invalidated/opposed post-CSD confirmations. `NarrativeEngine` keeps
local control, parent control, curve, profile and matrix evidence separate.

## Bias and narrative

- **BIAS-01** (00:20–01:42, 02:11–03:12): start with parent IOF and candle
  anatomy; monthly/weekly/daily charts are nested. The prior candle high/low is
  a directional draw hypothesis, not a guaranteed outcome.
- **BIAS-02** (03:13–08:21): child candles construct the parent candle. OHLC is
  the bearish expectation, OLHC the bullish expectation. Illustrations of four
  weeks per month or five days per week are examples, not calendar constants.
- **BIAS-03** (08:21–09:26): locate price within the dealing range, identify
  PD Arrays and proximity. Continue toward the parent matrix, then wait for a
  shift instead of extrapolating the old draw indefinitely.
- **BIAS-04** (09:33–12:17): answer, separately: prior highs or lows; buy-side
  or sell-side curve; reversal/retracement/continuation; stops/imbalance or
  ERL/IRL; PD Array position and hierarchy. The same movement can have different
  labels on different timeframes.
- **BIAS-05** (12:39–14:47): profiles describe expected delivery, not every
  candle's final color. Opposing days can occur within a directional program.
  Price can return to imbalance after a stop objective has been taken.

**Implementation:** highest supplied timeframe owns the reported bias, with
missing higher horizons reported explicitly. Every timeframe reports control,
curve, expected OHLC/OLHC, the previous completed candle's high/low and whether
the last candle reached it, dealing-range position, parent matrices and their
price distance. An unconfirmed parent is not silently replaced by its child.
The existing weekly/daily/H4 profile outputs remain intact.

## High/low resistance liquidity runs

- **RUN-01** (00:28–02:21): LRLR follows established delivery; HRLR opposes it.
  Protected premium ranges/highs impede bullish runs in a sell program; protected
  discount ranges/lows impede bearish runs in a buy program.
- **RUN-02** (02:23–04:48): an LTF run may remain LRLR only until it reaches
  parent order flow. A daily bullish range can bound a locally bearish run.
- **RUN-03** (04:49–06:44): after control changes, the liquidity run designation
  changes with it; reaccumulation/redistribution can contain smaller programs.
- **RUN-04** (06:46–08:12): always state the timeframe and measuring point.
  Approaching parent structure limits the case for further LRLR continuation.
  One late subtitle calls the newly realigned run “high resistance,” inconsistent
  with the repeated definition. The implementation uses the repeated rule of
  alignment with control and preserves separate local/parent-relative labels.

**Implementation:** `classify_liquidity_run` requires directional control; neutral,
transition and unknown are unresolved. Parent matrix reached, or an objective
that crosses an opposing parent matrix, changes the bounded local classification
to HRLR. Parent-relative HRLR does not erase a local LRLR that ends before the
parent matrix. Targets and reason codes are explicit; no probability is assigned.

## Market efficiency and price-delivery cycles

- **MEP-01** (00:31–01:27): consolidation → expansion → retracement or reversal.
  Which interpretation applies depends on order-flow context, not just candle
  size or color.
- **MEP-02** (01:28–02:21): buy stops ↔ sell stops, with displacement leaving
  imbalance and returns rebalancing it; this structure nests fractally.
- **MEP-03** (02:22–03:28): engineer → neutralize → distribute → rebalance →
  redistribute. These are ordered observations, not interchangeable labels.
- **MEP-04** (03:29–06:06): stops → imbalance → stops in directional delivery.
  At a parent array, negation of the previously respected array is an early
  transition clue; newly respected arrays establish new control.
- **MEP-05** (06:08–07:21): ERL → IRL → ERL and fair value → discount/premium
  are alternate views of this cycle. Internal FVG targets are kept distinct
  from Phase 4 internal structural liquidity pools.
- **MEP-06** (07:22–11:33): interpret a return after directional expansion as a
  retracement while parent control holds; a return alone is not a reversal.
  Smaller consolidations/expansions can exist within the return.
- **MEP-07** (11:34–16:16): the worked examples repeat the nested sequence;
  there are no additional universal numeric thresholds in the examples.

**Implementation:** `PriceDeliveryEngine` replays confirmed pivots/raids and
FVG formation/return chronologically. An observed one-sided stop raid must precede
an opposite-direction displacement source; a later gap return is REBALANCE;
a still later body close beyond the displacement candle is REDISTRIBUTE, labeled
reaccumulation for bullish delivery and redistribution for bearish. A two-sided
raid has unknown intrabar order and clears the pending raid chain. Negating the
delivery gap causes TRANSITION/INVALIDATED, never automatic reversal. Fresh stops
can switch the cycle back to STOPS_TO_IMBALANCE even on a continuation candle.

## Fair value, FVG and order pairing

- **FVG-01** (00:42–01:20): a gap is not guaranteed to hold. Fair value as range
  midpoint is a different concept from a structural fair-valuation point.
- **FVG-02** (01:20–03:14): structural fair valuation refers to a prior
  buying/selling boundary crossed with velocity; a return is expected into the
  candle delivering that move. Wicks can represent two-way delivery.
- **FVG-03** (03:17–03:48, 05:05–05:22, 07:33–07:45, 08:52–09:28): prefer the
  FVG offered by the candle that trades and **closes** through a prior high/low.
  Preserve that structural reference instead of choosing a random nearer gap.
  Return to the reference can be delayed.
- **FVG-04** (03:49–04:43): after return to fair valuation, look for a CSD/OB
  formation and delivery toward liquidity. Array touch itself is not an entry.
- **FVG-05** (05:29–09:49): apply the idea symmetrically and on any timeframe;
  several gaps can coexist. Track failures as well as returns/rejections.
- **FVG-06** (10:02–11:02): the next lecture promises handling of price breaking
  the source candle and smaller reaccumulation. That full procedure is not in
  this upload, so no inversion-FVG/breaker rule is inferred from this teaser.

**Implementation:** three-candle non-overlap with a directional middle candle;
confirmation only at the third close. Record gap boundaries/CE, full source-candle
range, prior confirmed pivot and structural valuation point separately. A strict
middle-candle close through a pivot known before that candle earns structural
qualification; earlier closes through the reference exclude that qualification.
First touch, partial/full rebalance, body-close invalidation, structural return
and rejection have distinct positions/times. Wick-only full fill is not body
invalidation. Actual order pairing cannot be observed in OHLC: outputs are
explicitly price-action proxies, not claims about institutional transactions.

## MMXM context and limits

- **MMXM-01** (00:21–02:25): the program forms within a defined dealing range;
  movement premium ↔ discount expresses accumulation/manipulation/distribution.
- **MMXM-02** (02:57–04:06): a whole smaller MMXM can be one curve of a larger
  model. Local reversal need not invalidate the larger model.
- **MMXM-03** (04:14–05:31): current market price/original consolidation →
  matrix → terminal are the three landmarks. Smart Money Reversal belongs at
  the matrix, with failure swing or breaker, followed by changed order flow.
- **MMXM-04** (05:33–08:42): curves contain stops/imbalances and repeated
  reaccumulation/redistribution. Reclaimed order/mitigation areas are locations
  of interest, not fully specified new algorithms in this transcript.
- **MMXM-05** (08:48–09:29): the first redistribution/reaccumulation is an
  alternative to catching the Smart Money Reversal itself.
- **MMXM-06** (09:31–13:05): efficient OC followed by velocity, matrix touch,
  control shift and terminal delivery recur in the chart examples.

**Implementation boundary:** OC candidate/departure, parent matrix, directional
curve, continuation sequence and liquidity objective are available as component
facts. CSD + post-CSD IOFC can confirm a local reversal at an existing parent
array; they do not create an entire MMXM. This change does **not** claim a complete
failure-swing, breaker, reclaimed-order-block or automatic MMXM recognizer.

## Explicit engineering conventions (not verbatim lecture rules)

1. Existing pivot span defaults to 2 (strict extrema; no equal-high/low pivot).
2. “Velocity” is represented by a directional FVG-producing candle; there is no
   invented ATR multiplier or profitability estimate.
3. Consolidation candidate = configurable 3-bar window with common traded overlap
   and both up/down closes. It becomes displacement-confirmed only on subsequent
   FVG departure outside its bounds. This is a documented proxy, not exact OC truth.
4. Strict closes beyond a boundary confirm/negate it. Equality is not a break.
5. Gap touch is inclusive; full wick fill and body invalidation are separate.
6. Structural reference selection uses the most recently formed eligible pivot.
7. Narrative reversal confirmation requires the CSD raid to overlap a previously
   available parent array and then a still-valid new post-CSD IOFC. More permissive
   proximity definitions await a specified numeric rule.
8. The narrative qualification gate requires aligned available timeframes, a
   bounded LRLR liquidity objective, and the existing Phase 6 validation on the
   same candle stream. It remains context qualification, not an executable order.

## Source integrity

SHA-256 of the original uploaded SRT bytes (source files are not republished):

- `FRAMING BIAS & BUILDING NARRATIVE.srt`: `d94c58495756b07a35e2146bf3ce354d2bc2f62c84621cd761143b8c879723de`
- `FRAMING HIGH AND LOW RESISTANT LIQUIDITY RUNS.srt`: `b055eb88988b46a02930849c05caacb6a5fe510a71348e939876f5930609b3a6`
- `INSTITUTIONAL ORDERFLOW- THE ONE THING IN PRICE THAT DOESN’T LIE.srt`: `bd6480f252811471870823fdb884432ab072c594d16e9ee95319f7d6e1fb185d`
- `MARKET EFFICIENCY PARADIGM MENTAL FRAMEWORKS.srt`: `cb21d9cf2015cc4c80d1c7cdee32751fe112ebf9de67455d8714bc1aec98d596`
- `MMXM.srt`: `cc172127c57f17d3fae833f73ca64fff9ae7fa6e18eae8c85ad8742d54b6ba97`
- `THE FAIR VALUE GAP LECTURE UNDERSTANDING FAIR VALUE GAP FAIR VALUATION and ORDER PAIRING and ITS SIGNIFICANCE Pt 1.srt`: `c7aba7e74e83f450468ce5d2cdd963748bb18047410b570f0b44080496634591`
