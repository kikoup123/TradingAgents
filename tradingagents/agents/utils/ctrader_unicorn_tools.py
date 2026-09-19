import json
from typing import Annotated

from langchain_core.tools import tool

from tradingagents.dataflows.ctrader_unicorn import (
    evaluate_unicorn_entry,
)


@tool
def get_ctrader_unicorn_entry(
    symbol: Annotated[
        str,
        "NASDAQ, US500 or XAUUSD",
    ],
    direction: Annotated[
        str,
        "bullish or bearish",
    ],
    count: Annotated[
        int,
        "Candles to inspect per timeframe",
    ] = 500,
    pivot_window: Annotated[
        int,
        "Swing pivot strength",
    ] = 2,
    confirmation_bars: Annotated[
        int,
        "Maximum confirmation window",
    ] = 10,
) -> str:
    """
    Deterministically inspect:
    actual Breaker -> FVG overlap -> Unicorn ->
    nested FVG negation -> Housing Candle ->
    IFVG -> retest -> second CSD -> new IOF.

    This tool never places orders.
    """

    result = evaluate_unicorn_entry(
        symbol=symbol,
        direction=direction,
        count=count,
        pivot_window=pivot_window,
        confirmation_bars=
            confirmation_bars,
    )

    return json.dumps(
        result,
        indent=2,
    )
