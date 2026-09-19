import json
from typing import Annotated

from langchain_core.tools import tool

from tradingagents.dataflows.ctrader_csd import (
    analyze_csd_orderflow,
)


@tool
def get_ctrader_csd(
    symbol: Annotated[
        str,
        "XAUUSD, NASDAQ/NQ/US100, or US500/ES/SP500",
    ],
    timeframe: Annotated[
        str,
        "M1, M3, M5, M15, M30, H1, H4, D1 or W1",
    ] = "M15",
    count: Annotated[
        int,
        "Number of cTrader candles to inspect",
    ] = 300,
    pivot_window: Annotated[
        int,
        "Liquidity swing pivot strength; usually 2 or 3",
    ] = 2,
    confirmation_bars: Annotated[
        int,
        "Maximum bars allowed after raid for CSD confirmation",
    ] = 10,
) -> str:
    """
    Deterministically detect liquidity raid, CSD/CISD and
    post-CSD institutional order flow from FP Markets cTrader candles.

    Wick-only threshold breaks do not confirm CSD.

    CSD alone does not establish continuation control.
    A new post-CSD opposing-close source range must be traded through
    and accepted with candle bodies to confirm IOF/IOFC.
    """

    result = analyze_csd_orderflow(
        symbol=symbol,
        timeframe=timeframe,
        count=count,
        pivot_window=pivot_window,
        confirmation_bars=confirmation_bars,
    )

    return json.dumps(
        result,
        indent=2,
    )
