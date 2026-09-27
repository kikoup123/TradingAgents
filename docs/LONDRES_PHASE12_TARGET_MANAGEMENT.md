# Londres Phase 12 — CSD standard-deviation targets and trade management

Phase 12 adds deterministic profit targets and runner management to the existing Londres stack. It does not authorize broker orders.

## CSD standard-deviation range

The target range is tied to the same validated SMT -> CSD event used by the execution gate.

For a bearish setup:

- `C = validated CSD threshold/opening level`
- `P = protected high created by that same CSD sequence`
- `R = P - C`
- `-2 = C - 2R`
- `-2.5 = C - 2.5R`

For a bullish setup the geometry is mirrored:

- `C = validated CSD threshold/opening level`
- `P = protected low created by that same CSD sequence`
- `R = C - P`
- directional `-2 = C + 2R`
- directional `-2.5 = C + 2.5R`

The labels remain `-2` and `-2.5` to preserve the Londres/indicator convention while the price projection is mirrored in trade direction.

If the protected extreme is on the wrong side of the CSD threshold, target generation fails closed.

## Permitted Trader exit modes

For an active Londres trade the Trader may select only one of:

- `FULL_AT_SD_2`
- `FULL_AT_SD_2_5`
- `HOLD_HTF_LIQUIDITY` when a valid HTF runner target exists

The Trader is not allowed to invent another TP price.

## Hold-for-HTF-liquidity management

If the Trader selects `HOLD_HTF_LIQUIDITY`:

1. `60%` of the position is automatically closed at the `-2.5` CSD projection.
2. `40%` remains as the runner.
3. A bearish runner targets active HTF external sell-side liquidity below the `-2.5` level.
4. A bullish runner targets active HTF external buy-side liquidity above the `-2.5` level.
5. The runner target is the nearest eligible active external pool beyond the partial trigger in trade direction.

If no appropriate HTF external liquidity exists beyond `-2.5`, hold mode is unavailable. Standard full exits at `-2` or `-2.5` remain available when the CSD range itself is valid.

## Early runner close

The Trader may close the remaining runner early only through a structured management decision with a recorded reason. Examples can include opposing CSD, loss of directional IOF, or another deterministic deterioration in delivery.

An early close does **not** rewrite the original target. The system preserves:

- original HTF runner target;
- selected SD target;
- `60%` partial trigger and fraction;
- `40%` runner fraction;
- early-exit reason separately.

This allows later performance analysis to compare planned targets with actual management decisions.

## Output contract

Phase 12 records:

- CSD zero reference;
- protected extreme;
- measured range size;
- exact `-2` target price;
- exact `-2.5` target price;
- allowed full-exit modes;
- hold-mode availability;
- automatic partial trigger;
- partial fraction `0.60`;
- runner fraction `0.40`;
- direction-appropriate HTF external liquidity target;
- early-close permission and preserved original target;
- `order_authorized = false`.

## Execution sequence

`SMT -> CSD -> post-CSD IOFC -> MMXM/entry context -> structural SL -> approved risk tier -> CSD SD target policy`

Phase 12 still does not place live orders. Exact entry-trigger selection, executable stop buffer, broker order validation and cTrader/FP Markets execution remain later layers.
