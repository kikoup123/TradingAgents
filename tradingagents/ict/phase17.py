"""Phase 17: hard deterministic pre-broker order validation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import pandas as pd

from .narrative import DEFAULT_HIERARCHY
from .order_validator import HardPreBrokerOrderValidator
from .phase16 import LondresPhase16Engine
from .risk_sizing import InstrumentRiskSpec


class LondresPhase17Engine:
    """Validate the complete Londres order contract before any broker adapter.

    This phase may set ``order_authorized`` true only when every deterministic
    layer agrees. It still does not connect to or place an order with a broker.
    """

    def __init__(self, *, break_even_pivot_span: int = 2) -> None:
        self.phase16 = LondresPhase16Engine(break_even_pivot_span=break_even_pivot_span)
        self.validator = HardPreBrokerOrderValidator()

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
        context = self.phase16.analyze(
            timeframe_bars=timeframe_bars,
            trader_stop_selection=trader_stop_selection,
            instrument_risk_spec=instrument_risk_spec,
            account_equity=account_equity,
            stop_buffer_ticks=stop_buffer_ticks,
            max_risk_cash=max_risk_cash,
            hierarchy=hierarchy,
            **phase6_inputs,
        )

        validation = self.validator.validate(
            entry_execution=context.get("entry_execution"),
            executable_stop=context.get("executable_stop"),
            trade_calculation=context.get("trade_calculation"),
            break_even_management=context.get("break_even_management"),
            trader_selection=trader_stop_selection,
            instrument=instrument_risk_spec,
            account_equity=account_equity,
            max_risk_cash=max_risk_cash,
        ).to_dict()

        package = dict(context.get("execution_package") or {})
        package["pre_broker_validation"] = validation
        package["order_authorized"] = bool(validation.get("order_authorized"))
        package["broker_order_placed"] = False

        return {
            **context,
            "phase": "LONDRES_HARD_PRE_BROKER_VALIDATION_STACK",
            "pre_broker_validation": validation,
            "execution_package": package,
        }

    @staticmethod
    def state_update(context: dict) -> dict:
        update = LondresPhase16Engine.state_update(context)
        update["pre_broker_validation_state"] = context["pre_broker_validation"]
        update["execution_package_state"] = context["execution_package"]
        return update
