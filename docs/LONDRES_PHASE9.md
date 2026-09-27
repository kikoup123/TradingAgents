# Londres Phase 9 — Structural Trade-Plan Context

Phase 9 sits between a validated MMXM/entry gate and the still-unimplemented
proprietary entry selector. Its job is to prepare the structural facts required
to construct a trade without inventing an entry trigger or authorizing an order.

## Sequence

The stack is now:

`HTF narrative -> Liquidity -> SMT -> CSD -> post-CSD IOFC -> MMXM -> structural trade plan -> exact entry selector (future)`

Phase 9 only activates after Phase 8 reports either `REVERSAL_READY` or
`CONTINUATION_READY`.

## Structural invalidation

The trade-plan invalidation is the protected extreme established by the CSD
selected for the current reversal:

- bullish plan -> protected CSD low;
- bearish plan -> protected CSD high.

If current closed-bar price has already crossed that protected extreme, the
trade plan becomes `INVALIDATED`.

This is an execution-context invalidation level, not yet a broker stop order.
Actual stop placement, spread/slippage buffers and broker normalization belong to
the later risk/order layer.

## Primary objective

The preferred target is the Phase 8 MMXM terminal:

- MMBM -> external buy-side terminal;
- MMSM -> external sell-side terminal.

If an MMXM terminal is unavailable, a deterministic narrative draw may be used
as context when it has a concrete price. If the terminal has already been
reached, Phase 9 reports `OBJECTIVE_REACHED` instead of preparing a new entry.

## Eligible execution locations

Phase 9 records locations but explicitly marks every location
`entry_signal: false`.

Currently eligible context locations are:

1. the NEW post-CSD IOFC confirmation range;
2. active structural FVGs in the final MMXM direction that formed **after** the
   CSD confirmation;
3. the failed order-flow range used by a confirmed MMXM breaker signature.

A pre-CSD FVG is not promoted to a Phase 9 execution location for the current
validated reversal.

These locations are ingredients for the future entry selector, not standalone
signals.

## LRLR / HRLR

The trade plan carries the parent-relative liquidity-run classification already
produced by Phase 7. HRLR does not automatically become an entry merely because
an LTF trigger exists. The state records that the path is parent-relative HRLR
so the later entry/risk layer can refuse or constrain it.

## Trade-plan states

- `NOT_READY`
- `READY_FOR_ENTRY_SELECTION`
- `INVALIDATED`
- `OBJECTIVE_REACHED`

`READY_FOR_ENTRY_SELECTION` requires:

- Phase 8 entry state is `REVERSAL_READY` or `CONTINUATION_READY`;
- valid CSD protected invalidation;
- a concrete directional target;
- coherent geometry (stop/current price/target ordered in the intended direction);
- at least one post-confirmation execution location.

## Hard safety boundary

Phase 9 always returns:

- `risk_reward_status: WAIT_FOR_ENTRY_PRICE`
- `order_authorized: false`

Without an exact entry price, R:R cannot be calculated honestly. Phase 9 does
not guess one.

## Still not implemented

The following remain separate work:

- exact Unicorn definition and selection;
- housing-candle entry rule;
- OTE entry policy;
- mitigation/reclaimed-order-block selection beyond existing context;
- final entry price;
- stop buffer and broker-normalized SL;
- target scaling/partials;
- position sizing and account-risk limits;
- hard order validator;
- cTrader/FP Markets execution.

Those rules should be encoded from the user's exact execution methodology rather
than inferred from generic ICT material.
