"""Phase 13: exact entry from the first return into the post-CSD IOF range."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import pandas as pd

from .entry_execution import PostCSDIOFEntryEngine
from .narrative import DEFAULT_HIERARCHY
from .phase12 import LondresPhase12Engine


class LondresPhase13Engine:
    """Add a deterministic executable entry event to the Phase 12 stack.

    The entry zone is the confirmed IOF range formed after the validated CSD.
    Entry is triggered only when price returns to that range after IOFC. This
    phase still does not authorize a broker order because the final executable
    stop buffer and broker validation remain separate layers.
    """

    def __init__(self) -> None:
        self.phase12 = LondresPhase12Engine()
        self.entry = PostCSDIOFEntryEngine()

    def analyze(
        self,
        *,
        timeframe_bars: Mapping[str, pd.DataFrame],
        hierarchy: Sequence[str] = DEFAULT_HIERARCHY,
        **phase6_inputs,
    ) -> dict:
        context = self.phase12.analyze(
            timeframe_bars=timeframe_bars,
            hierarchy=hierarchy,
            **phase6_inputs,
        )
        execution_timeframe = context["bias_narrative"]["execution_timeframe"]
        entry_execution = self.entry.analyze(
            context,
            timeframe_bars[execution_timeframe],
            as_of=phase6_inputs.get("as_of"),
        )
        entry_dict = entry_execution.to_dict()
        exact_entry = entry_dict.get("exact_entry_price")

        stop_options = []
        for candidate in (context.get("stop_options") or {}).get("candidates", []):
            enriched = dict(candidate)
            anchor = candidate.get("anchor_price")
            if exact_entry is not None and anchor is not None:
                enriched["distance_from_entry"] = abs(float(anchor) - float(exact_entry))
            else:
                enriched["distance_from_entry"] = None
            stop_options.append(enriched)

        execution_package = {
            "entry_status": entry_dict["status"],
            "exact_entry_price": exact_entry,
            "entry_event": entry_dict.get("entry_event"),
            "entry_zone": entry_dict.get("entry_zone"),
            "structural_stop_options": stop_options,
            "target_management": context.get("target_management"),
            "risk_sizing_ready": False,
            "risk_sizing_blocker": (
                "FINAL_EXECUTABLE_STOP_BUFFER_REQUIRED"
                if exact_entry is not None
                else "WAIT_FOR_EXACT_IOF_RETRACE_ENTRY"
            ),
            "order_authorized": False,
        }

        return {
            **context,
            "phase": "LONDRES_EXACT_POST_CSD_IOF_ENTRY_STACK",
            "entry_execution": entry_dict,
            "execution_package": execution_package,
        }

    @staticmethod
    def state_update(context: dict) -> dict:
        update = LondresPhase12Engine.state_update(context)
        update["entry_execution_state"] = context["entry_execution"]
        update["execution_package_state"] = context["execution_package"]
        return update
