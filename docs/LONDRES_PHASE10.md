# Londres Phase 10 — Trader Structural Stop Selection

Phase 10 implements the stop-selection rule defined for the Londres execution sequence:

`SMT -> CSD -> IOF -> entry context -> structural stop choice`

The Trader is allowed to choose between the two valid structural invalidation anchors. It is not allowed to manufacture a third stop concept.

## Stop option A — IOF_RANGE

For a bearish setup:

- use the latest still-valid bearish order-flow range formed after the validating CSD;
- the structural stop anchor is that range high;
- the executable stop must ultimately be placed **above** that high.

For a bullish setup:

- use the latest still-valid bullish order-flow range formed after the validating CSD;
- the structural stop anchor is that range low;
- the executable stop must ultimately be placed **below** that low.

A pre-CSD order-flow range cannot be promoted as the current execution stop. If no later active range is available, the post-CSD IOFC confirmation range is the deterministic fallback.

## Stop option B — SMT_PROTECTED

The SMT-protected option uses the protected extreme of the CSD that validated the current SMT event.

Because Phase 6 only selects a validation CSD at or after the active SMT reference time, this protected high/low belongs to the same validated SMT -> CSD reversal sequence.

For bearish delivery:

- protected high is the anchor;
- executable stop belongs above the protected high.

For bullish delivery:

- protected low is the anchor;
- executable stop belongs below the protected low.

## Trader discretion

When both candidates are valid, the Trader receives both and may choose either:

- `IOF_RANGE`
- `SMT_PROTECTED`

The Trader must explain the choice using the current MMXM, order-flow structure and target geometry.

When only one structural candidate is valid, there is no discretion: the hard validator uses that candidate.

When neither candidate is valid, the system must wait/hold.

## Hard validation

The LLM may choose a source, but it may not set the structural anchor price itself.

After the LLM responds, the hard validator:

1. checks that the proposed trade direction agrees with the Londres deterministic direction;
2. checks the selected source against the currently valid candidates;
3. replaces any LLM-written anchor with the exact deterministic anchor price;
4. clears any LLM-invented executable `stop_loss` value;
5. fails closed to `HOLD` if the selection is not valid.

This means the Trader has discretion between approved structural concepts, but not discretion to invent price structure.

## Buffer / executable stop

Phase 10 deliberately does **not** convert the anchor into a broker stop price.

The requested rule is "above" or "below" the structural level. The project does not yet have an approved instrument-specific tick/buffer convention, so Phase 10 records:

- structural anchor price;
- placement rule (`ABOVE_RANGE_HIGH`, `BELOW_RANGE_LOW`, `ABOVE_PROTECTED_HIGH`, or `BELOW_PROTECTED_LOW`);
- distance from current price;
- structural source.

`executable_stop_price` remains `null` until the risk/execution layer defines the valid tick/buffer beyond the anchor.

## Safety state

Phase 10 still has:

- `order_authorized: false`
- no broker order placement;
- no live execution;
- no invented stop buffer;
- no position sizing yet.
