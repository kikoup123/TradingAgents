"""Phase 12: CSD standard-deviation targets and trade-management context."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import pandas as pd

from .narrative import DEFAULT_HIERARCHY
from .phase10 import LondresPhase10Engine
from .target_management import CSDTargetEngine


class LondresPhase12Engine:
    """Add deterministic -2/-2.5 targets plus optional HTF liquidity runner.

    Phase 12 still does not authorize broker orders. It only defines the
    permitted full-exit targets and the automatic 60/40 hold-management policy.
    """

    def __init__(self) -> None:
        self.phase10 = LondresPhase10Engine()
        self.targets = CSDTargetEngine()

    def analyze(
        self,
        *,
        timeframe_bars: Mapping[str, pd.DataFrame],
        hierarchy: Sequence[str] = DEFAULT_HIERARCHY,
        **phase6_inputs,
    ) -> dict:
        context = self.phase10.analyze(
            timeframe_bars=timeframe_bars,
            hierarchy=hierarchy,
            **phase6_inputs,
        )
        target_management = self.targets.analyze(context)
        return {
            **context,
            "phase": "LONDRES_CSD_SD_TARGET_MANAGEMENT_STACK",
            "target_management": target_management.to_dict(),
        }

    @staticmethod
    def state_update(context: dict) -> dict:
        update = LondresPhase10Engine.state_update(context)
        update["target_management_state"] = context["target_management"]
        return update
