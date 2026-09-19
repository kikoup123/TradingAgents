import json
from typing import Annotated

from langchain_core.tools import tool

from tradingagents.dataflows.ctrader_master_setup import (
    evaluate_master_setup,
)


@tool
def get_ctrader_master_setup(
    symbol: Annotated[
        str,
        "Currently NASDAQ/NQ/US100 for the NASDAQ-US500 model",
    ] = "NASDAQ",
    htf_count: Annotated[
        int,
        "Number of H4/H1 broker candles",
    ] = 300,
    execution_count: Annotated[
        int,
        "Number of M15/M5 broker candles",
    ] = 500,
    pivot_window: Annotated[
        int,
        "Pivot strength used by liquidity, SMT and CSD detection",
    ] = 2,
    confirmation_bars: Annotated[
        int,
        "Maximum bars allowed for deterministic CSD confirmation",
    ] = 10,
) -> str:
    """
    Run the complete deterministic Trading Sand pre-entry workflow.

    Gates:
    HTF context
    -> liquidity raid
    -> SMT
    -> CSD
    -> post-CSD IOF
    -> LTF continuation
    -> entry model

    Any failed mandatory gate blocks the setup.

    This tool does not place orders.
    """

    result = evaluate_master_setup(
        symbol=symbol,
        htf_count=htf_count,
        execution_count=execution_count,
        pivot_window=pivot_window,
        confirmation_bars=confirmation_bars,
    )

    return json.dumps(result, indent=2)
