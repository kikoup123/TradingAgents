# Londres Trading Agent — Phase 1

Base target: `TauricResearch/TradingAgents` main at commit `be952b8eccb49720509af544c6675233bc1f10d0`.

## Implemented

- Deterministic institutional order-flow range detection on any timeframe.
- Bullish rule: down-close range -> later candle body close above range -> support / bullish IOFC.
- Bearish rule: up-close range -> later candle body close below range -> resistance / bearish IOFC.
- Wick-only penetration does not confirm IOFC.
- Confirmed range invalidation and `TRANSITION` state.
- Post-CSD IOFC gate that only considers a NEW opposite-close range formed after the CSD anchor.
- HTF order-flow control analysis for multiple timeframes.
- Weekly profile classifier:
  - Classic Expansion: weekly extreme Monday/Tuesday.
  - Midweek Reversal: weekly extreme Wednesday.
  - Thursday Reversal: weekly extreme Thursday.
  - Optional delayed Friday reversal branch after Thursday external manipulation.
- Weekly profile status is `DEVELOPING`, `CONFIRMED`, `INVALIDATED`, or `UNRESOLVED`.
- Daily expectation inheritance:
  - bullish continuation -> OLHC
  - bearish continuation -> OHLC
  - Classic Expansion Friday -> retracement candidate
  - Thursday reversal Friday -> continuation if opposing draw remains; return-to-range if draw is completed.

## Important design rule

The engine does **not** call Monday/Tuesday/Wednesday/Thursday the weekly high/low merely because that day is currently the observed extreme. A weekly profile is only `CONFIRMED` after the caller supplies `protected_weekly_extreme=True` and HTF order flow is directional.

## LangGraph state fields

Phase 1 adds these fields to `AgentState`:

```python
analysis_mode: "SWING" | "DAILY_SWING"
weekly_profile_state: dict
htf_order_flow_state: dict
```

The deterministic engine should execute before LLM narrative generation. Later phases will provide the missing deterministic inputs from Liquidity, CSD, and Time & Price engines.
