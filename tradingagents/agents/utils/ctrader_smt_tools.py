import json
from typing import Annotated

from langchain_core.tools import tool

from tradingagents.dataflows.ctrader_smt import detect_smt


@tool
def get_ctrader_smt(
    timeframe: Annotated[
        str,
        "SMT timeframe: M1, M3, M5, M15, M30, H1 or H4",
    ] = "M15",
    count: Annotated[
        int,
        "Number of synchronized candles to inspect",
    ] = 300,
    pivot_window: Annotated[
        int,
        "Fractal pivot strength; normally 2 or 3",
    ] = 2,
) -> str:
    """
    Detect deterministic SMT divergence between FP Markets
    NASDAQ (US TECH 100) and US500.

    Bearish SMT:
    one market makes a higher swing high while the other fails.

    Bullish SMT:
    one market makes a lower swing low while the other fails.

    SMT alone is NOT considered an entry signal.
    """

    result = detect_smt(
        timeframe=timeframe,
        count=count,
        pivot_window=pivot_window,
    )

    return json.dumps(
        result,
        indent=2,
    )
