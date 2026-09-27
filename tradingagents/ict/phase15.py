"""Phase 15: complete deterministic trade calculation after entry and stop."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import pandas as pd

from .narrative import DEFAULT_HIERARCHY
from .phase14 import LondresPhase14Engine
from .risk_sizing import InstrumentRiskSpec
from .trade_calculator import TradeCalculatorEngine


class LondresPhase15Engine:
    """Join exact entry, executable stop, risk tier and deterministic targets.

    This phase calculates broker-grid position size and R:R but still does not
    authorize or place an order.
    """

    def __init__(self) -> None:
        self.phase14 = LondresPhase14Engine()
        self.trade_calculator = TradeCalculatorEngine()

    def analyze(
        self,
        *,
        timeframe_bars: Mapping[str, pd.DataFrame],
        trader_stop_selection: dict | None = None,
        instrument_risk_spec: InstrumentRiskSpec | None = None,
        account_equity: float | None = None,
        stop_buffer_ticks: int | None = None,
        max_risk_cash: float | None = None,
        hierarchy: Sequence[str] = DEFAULT_HIERARCHY,
        **phase6_inputs,
    ) -> dict:
        tick_size = instrument_risk_spec.tick_size if instrument_risk_spec is not None else None
        context = self.phase14.analyze(
            timeframe_bars=timeframe_bars,
            trader_stop_selection=trader_stop_selection,
            instrument_tick_size=tick_size,
            stop_buffer_ticks=stop_buffer_ticks,
            hierarchy=hierarchy,
            **phase6_inputs,
        )

        calculation = self.trade_calculator.calculate(
            entry_execution=context.get("entry_execution"),
            executable_stop=context.get("executable_stop"),
            trader_selection=trader_stop_selection,
            instrument=instrument_risk_spec,
            account_equity=account_equity,
            max_risk_cash=max_risk_cash,
        )
        calculation_dict = calculation.to_dict()

        package = dict(context.get("execution_package") or {})
        package["trade_calculation"] = calculation_dict
        package["risk_sizing_ready"] = calculation_dict.get("status") == "READY"
        package["order_authorized"] = False

        return {
            **context,
            "phase": "LONDRES_COMPLETE_TRADE_CALCULATION_STACK",
            "trade_calculation": calculation_dict,
            "risk_sizing": calculation_dict.get("risk_sizing"),
            "execution_package": package,
        }

    @staticmethod
    def state_update(context: dict) -> dict:
        update = LondresPhase14Engine.state_update(context)
        update["trade_calculation_state"] = context["trade_calculation"]
        update["risk_sizing_state"] = context.get("risk_sizing") or {}
        update["execution_package_state"] = context["execution_package"]
        return update
