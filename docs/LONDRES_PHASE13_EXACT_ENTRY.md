# Londres Phase 13 — Exact post-CSD IOF entry

Phase 13 makes the entry deterministic. It does not use an LLM-selected entry price.

## Sequence

`SMT -> CSD -> new post-CSD IOF/IOFC range -> confirmation -> retracement -> entry`

The confirmed post-CSD IOF range is the execution zone. Only bars after IOFC confirmation may trigger the entry, so the historical implementation remains causal.

## Proximal-edge trigger

For a bearish setup, price is below the confirmed bearish IOF range after confirmation. The proximal boundary is the **low** of that range. The first return to that low triggers the short entry.

Example:

- IOF low: `21,900`
- IOF high: `21,920`
- price trades below the range after confirmation
- first retracement to `21,900` -> exact short entry `21,900`

For a bullish setup the rule is mirrored: price is above the confirmed bullish IOF range and the first return to the **high** of the range triggers the long entry.

No extra candle-close confirmation is required.

## Gap behavior

Historical OHLC replay must not invent a fill through an unreconstructable gap. If a bar opens beyond the entire entry range, the state becomes `RANGE_SKIPPED_BY_GAP` rather than fabricating a proximal-edge fill. If a bar opens already inside the range, the replay records the first executable bar-open price.

## Hard gate

The Londres Trader cannot replace the deterministic Phase 13 entry with an invented price. If Phase 13 is not `ENTRY_TRIGGERED`, the hard gate forces Hold. When triggered, any LLM-written entry value is replaced by the deterministic entry price.

## Output

Phase 13 records:

- direction;
- confirmed entry-zone low/high;
- source and confirmation positions/times;
- exact entry price;
- entry event position/time;
- fill basis;
- entry status;
- structural stop options measured from the exact entry;
- Phase 12 target-management context.

`order_authorized` remains false. Final executable stop buffering, deterministic risk sizing, broker validation, and broker execution remain separate layers.
