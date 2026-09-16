"""Deterministic post-MMXM trade-plan context.

This layer does not select a proprietary entry model and never authorizes a
broker order. It converts already-confirmed Londres context into the structural
facts an entry engine will later need: direction, protected invalidation,
terminal objective and eligible post-confirmation price locations.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

import pandas as pd

from .market_data import closed_bars


class TradePlanState(str, Enum):
    NOT_READY = "NOT_READY"
    READY_FOR_ENTRY_SELECTION = "READY_FOR_ENTRY_SELECTION"
    INVALIDATED = "INVALIDATED"
    OBJECTIVE_REACHED = "OBJECTIVE_REACHED"


@dataclass(frozen=True)
class TradePlanContext:
    state: TradePlanState
    direction: str
    model: str
    mmxm_stage: str
    current_price: float
    invalidation: dict | None
    primary_target: dict | None
    liquidity_run: dict | None
    execution_locations: tuple[dict, ...]
    entry_selection_required: bool
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "direction": self.direction,
            "model": self.model,
            "mmxm_stage": self.mmxm_stage,
            "current_price": self.current_price,
            "invalidation": self.invalidation,
            "primary_target": self.primary_target,
            "liquidity_run": self.liquidity_run,
            "execution_locations": list(self.execution_locations),
            "entry_selection_required": self.entry_selection_required,
            "risk_reward_status": "WAIT_FOR_ENTRY_PRICE",
            "order_authorized": False,
            "reason_codes": list(self.reason_codes),
        }


class TradePlanEngine:
    """Prepare structural trade facts without choosing an entry or risk size."""

    READY_ENTRY_STATES = {"REVERSAL_READY", "CONTINUATION_READY"}

    def analyze(
        self,
        context: dict,
        execution_bars: pd.DataFrame,
        *,
        as_of=None,
    ) -> TradePlanContext:
        data = closed_bars(execution_bars, as_of)
        current_price = float(data.iloc[-1].close)
        mmxm = context["mmxm"]["execution_model"]
        entry = context["entry_model"]
        narrative = context["bias_narrative"]
        timeframe = narrative["execution_timeframe"]
        local = narrative["timeframes"][timeframe]
        direction = mmxm["final_direction"]
        reasons: list[str] = []

        reversal = local.get("reversal") or {}
        csd = reversal.get("csd") or {}
        protected = csd.get("protected_extreme")
        confirmation_position = csd.get("confirmation_position")
        invalidation = None
        if protected is not None and direction in {"BULLISH", "BEARISH"}:
            invalidation = {
                "price": float(protected),
                "source": "CSD_PROTECTED_EXTREME",
                "kind": "PROTECTED_LOW" if direction == "BULLISH" else "PROTECTED_HIGH",
                "confirmation_position": confirmation_position,
            }
        else:
            reasons.append("WAIT_FOR_CSD_PROTECTED_EXTREME")

        target = self._primary_target(mmxm, local)
        if target is None:
            reasons.append("WAIT_FOR_DIRECTIONAL_OBJECTIVE")

        locations = self._execution_locations(
            local,
            mmxm,
            direction=direction,
            confirmation_position=confirmation_position,
        )
        if not locations:
            reasons.append("WAIT_FOR_POST_CONFIRMATION_EXECUTION_LOCATION")

        liquidity_run = local.get("parent_relative_run") or local.get("liquidity_run")
        if liquidity_run and liquidity_run.get("classification") == "HRLR":
            reasons.append("EXECUTION_PATH_IS_PARENT_RELATIVE_HRLR")

        invalidated = self._invalidation_breached(current_price, direction, invalidation)
        objective_reached = bool(
            mmxm.get("stage") == "TERMINAL_REACHED"
            or (target and target.get("reached"))
        )
        geometry_valid = self._geometry_valid(
            current_price,
            direction=direction,
            invalidation=invalidation,
            target=target,
        )

        if invalidated or mmxm.get("stage") == "INVALIDATED" or entry.get("state") == "INVALIDATED":
            state = TradePlanState.INVALIDATED
            reasons.append("STRUCTURAL_INVALIDATION_REACHED")
        elif objective_reached:
            state = TradePlanState.OBJECTIVE_REACHED
            reasons.append("MMXM_OBJECTIVE_ALREADY_REACHED")
        elif entry.get("state") not in self.READY_ENTRY_STATES:
            state = TradePlanState.NOT_READY
            reasons.append("MMXM_ENTRY_GATE_NOT_READY")
        elif not geometry_valid:
            state = TradePlanState.NOT_READY
            reasons.append("TARGET_INVALIDATION_GEOMETRY_UNRESOLVED")
        elif not locations:
            state = TradePlanState.NOT_READY
        else:
            state = TradePlanState.READY_FOR_ENTRY_SELECTION
            reasons.append("STRUCTURAL_PLAN_READY_WAIT_EXACT_ENTRY_MODEL")

        return TradePlanContext(
            state=state,
            direction=direction,
            model=mmxm.get("model", "UNRESOLVED"),
            mmxm_stage=mmxm.get("stage", "NO_MODEL"),
            current_price=current_price,
            invalidation=invalidation,
            primary_target=target,
            liquidity_run=liquidity_run,
            execution_locations=tuple(locations),
            entry_selection_required=state == TradePlanState.READY_FOR_ENTRY_SELECTION,
            reason_codes=tuple(reasons),
        )

    @staticmethod
    def _primary_target(mmxm: dict, local: dict) -> dict | None:
        terminal = mmxm.get("terminal")
        if terminal and terminal.get("price") is not None:
            return {
                "price": float(terminal["price"]),
                "kind": terminal.get("purpose", "MMXM_TERMINAL"),
                "side": terminal.get("side"),
                "liquidity_class": terminal.get("liquidity_class"),
                "reached": bool(terminal.get("reached")),
                "source": "MMXM_TERMINAL",
            }
        draw = local.get("narrative_draw")
        if draw and draw.get("price") is not None:
            return {
                "price": float(draw["price"]),
                "kind": draw.get("kind", "NARRATIVE_DRAW"),
                "side": draw.get("side"),
                "reached": bool(draw.get("reached", False)),
                "source": "NARRATIVE_DRAW",
            }
        return None

    @staticmethod
    def _execution_locations(
        local: dict,
        mmxm: dict,
        *,
        direction: str,
        confirmation_position: int | None,
    ) -> list[dict]:
        locations: list[dict] = []
        reversal = local.get("reversal") or {}
        iofc = reversal.get("post_csd_iofc") or {}
        confirmation_range = iofc.get("confirmation_range")
        if confirmation_range and iofc.get("confirmed"):
            locations.append(
                {
                    "kind": "POST_CSD_IOFC_RANGE",
                    "low": float(confirmation_range["low"]),
                    "high": float(confirmation_range["high"]),
                    "source_position": confirmation_range.get("source_position"),
                    "confirmed_position": confirmation_range.get("confirmed_position"),
                    "status": confirmation_range.get("status"),
                    "entry_signal": False,
                }
            )

        for gap in local.get("fair_value", {}).get("structural_fvg_candidates", []):
            if gap.get("direction") != direction:
                continue
            if confirmation_position is not None and int(gap.get("formation_position", -1)) <= int(
                confirmation_position
            ):
                continue
            locations.append(
                {
                    "kind": "POST_CSD_STRUCTURAL_FVG",
                    "low": float(gap["low"]),
                    "high": float(gap["high"]),
                    "consequent_encroachment": float(gap["consequent_encroachment"]),
                    "fair_valuation_point": gap.get("fair_valuation_point"),
                    "formation_position": gap.get("formation_position"),
                    "status": gap.get("status"),
                    "entry_signal": False,
                }
            )

        signature = (mmxm.get("smart_money_reversal") or {}).get("signature") or {}
        if signature.get("type") == "BREAKER" and signature.get("evidence"):
            failed = signature["evidence"].get("failed_order_flow_range") or {}
            if failed.get("low") is not None and failed.get("high") is not None:
                locations.append(
                    {
                        "kind": "MMXM_BREAKER_RETEST_RANGE",
                        "low": float(failed["low"]),
                        "high": float(failed["high"]),
                        "source_position": failed.get("source_position"),
                        "entry_signal": False,
                    }
                )
        return locations

    @staticmethod
    def _invalidation_breached(current_price: float, direction: str, invalidation: dict | None) -> bool:
        if invalidation is None:
            return False
        price = float(invalidation["price"])
        if direction == "BULLISH":
            return current_price < price
        if direction == "BEARISH":
            return current_price > price
        return False

    @staticmethod
    def _geometry_valid(
        current_price: float,
        *,
        direction: str,
        invalidation: dict | None,
        target: dict | None,
    ) -> bool:
        if invalidation is None or target is None or direction not in {"BULLISH", "BEARISH"}:
            return False
        stop = float(invalidation["price"])
        objective = float(target["price"])
        if direction == "BULLISH":
            return stop < current_price < objective
        return objective < current_price < stop
