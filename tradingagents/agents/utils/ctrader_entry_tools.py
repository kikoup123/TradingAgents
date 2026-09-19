import json
from typing import Annotated

from langchain_core.tools import tool

from tradingagents.dataflows.ctrader_entry import (
    evaluate_ltf_continuation,
)


@tool
def get_ctrader_ltf_continuation(
    symbol: Annotated[
        str,
        "XAUUSD, NASDAQ/NQ/US100, or US500/ES/SP500",
    ],
    ltf_timeframe: Annotated[
        str,
        "Lower timeframe: M1, M3, M5 or M15",
    ] = "M5",
    csd_timeframe: Annotated[
        str,
        "Higher CSD/order-flow timeframe, normally M15 or H1",
    ] = "M15",
    count: Annotated[
        int,
        "Number of cTrader candles to inspect",
    ] = 500,
) -> str:
    """
    Validate lower-timeframe continuation after deterministic
    CSD and post-CSD institutional order-flow confirmation.

    This tool checks:
    - higher-timeframe IOF control,
    - lower-timeframe opposing-close ranges,
    - body-close acceptance,
    - protected-swing integrity,
    - subsequent structural failure.

    It does not replace the SMT gate and does not place orders.
    """

    result = evaluate_ltf_continuation(
        symbol=symbol,
        ltf_timeframe=ltf_timeframe,
        csd_timeframe=csd_timeframe,
        count=count,
    )

    return json.dumps(
        result,
        indent=2,
    )
