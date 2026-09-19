from typing import Annotated

from langchain_core.tools import tool

from tradingagents.dataflows.ctrader import get_ctrader_bars as _get_ctrader_bars


@tool
def get_ctrader_bars(
    symbol: Annotated[
        str,
        "Instrument: XAUUSD, NASDAQ/NQ/US100, or US500/ES/SPX500",
    ],
    timeframe: Annotated[
        str,
        "Timeframe: M1, M3, M5, M15, M30, H1, H4, D1, or W1",
    ],
    count: Annotated[
        int,
        "Number of candles to retrieve, between 1 and 2000",
    ] = 200,
) -> str:
    """
    Retrieve broker-native FP Markets OHLCV candles from the local
    cTrader Open API bridge.

    Use this tool for ICT/SMC price-action analysis on XAUUSD,
    NASDAQ and US500, including HTF/LTF structure, liquidity,
    SMT, CSD, displacement, MMXM and session analysis.

    Data comes from the configured FP Markets cTrader account.
    This tool is market-data only and does not execute orders.
    """
    return _get_ctrader_bars(
        symbol=symbol,
        timeframe=timeframe,
        count=count,
    )
