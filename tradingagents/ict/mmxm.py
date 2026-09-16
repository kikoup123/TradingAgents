"""Deterministic Market Maker Model recognition from Londres component facts.

The MMXM transcript defines three landmarks: current market price/original
consolidation, matrix and terminal.  This module composes already-observed
Londres facts into a conservative recognizer.  It does not turn a random
failure swing, breaker or matrix touch into an MMXM.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

import pandas as pd

from .csd import CSDEngine
from .market_data import closed_bars, comparable_time
from .models import Direction
from .order_flow import OrderFlowEngine

DIRECTIONAL = {Direction.BULLISH.value, Direction.BEARISH.value}


class MMXMType(str, Enum):
    MMBM = "MMBM"
    MMSM = "MMSM"
    UNRESOLVED = "UNRESOLVED"


class MMXMStage(str, Enum):
    NO_MODEL = "NO_MODEL"
    CURVE_TO_MATRIX = "CURVE_TO_MATRIX"
    AT_MATRIX_WAIT_REVERSAL = "AT_MATRIX_WAIT_REVERSAL"
    AT_MATRIX_WAIT_SIGNATURE = "AT_MATRIX_WAIT_SIGNATURE"
    SMART_MONEY_REVERSAL_CONFIRMED = "SMART_MONEY_REVERSAL_CONFIRMED"
    CONTINUATION_PHASE = "CONTINUATION_PHASE"
    TERMINAL_REACHED = "TERMINAL_REACHED"
    INVALIDATED = "INVALIDATED"


class SMRSignature(str, Enum):
    FAILURE_SWING = "FAILURE_SWING"
    BREAKER = "BREAKER"
    NONE = "NONE"


class EntryState(str, Enum):
    WAIT = "WAIT"
    REVERSAL_READY = "REVERSAL_READY"
    CONTINUATION_READY = "CONTINUATION_READY"
    INVALIDATED = "INVALIDATED"


@dataclass(frozen=True)
class MMXMDetection:
    timeframe: str
    model: MMXMType
    stage: MMXMStage
    approach_direction: Direction
    final_direction: Direction
    original_consolidation: dict | None
    dealing_range: dict | None
    matrix: dict | None
    matrix_location: str
    smart_money_reversal: dict
    terminal: dict | None
    parent_relationship: str
    parent_timeframe: str | None
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "timeframe": self.timeframe,
            "model": self.model.value,
            "stage": self.stage.value,
            "approach_direction": self.approach_direction.value,
            "final_direction": self.final_direction.value,
            "original_consolidation": self.original_consolidation,
            "dealing_range": self.dealing_range,
            "matrix": self.matrix,
            "matrix_location": self.matrix_location,
            "smart_money_reversal": self.smart_money_reversal,
            "terminal": self.terminal,
            "parent_relationship": self.parent_relationship,
            "parent_timeframe": self.parent_timeframe,
            "reason_codes": list(self.reason_codes),
        }


class MMXMEngine:
    """Compose OC, matrix, reversal signatures, delivery and terminal context."""

    def __init__(self, *, pivot_span: int = 2) -> None:
        if pivot_span < 1:
            raise ValueError("pivot_span must be >= 1")
        self.pivot_span = pivot_span
        self.structure = CSDEngine(pivot_span=pivot_span)
        self.order_flow = OrderFlowEngine()

    def analyze(
        self,
        bars: pd.DataFrame,
        *,
        timeframe: str,
        narrative: dict,
        as_of=None,
        parent_model: dict | None = None,
    ) -> MMXMDetection:
        data = closed_bars(bars, as_of)
        state = narrative["timeframes"][timeframe]
        delivery = state["price_delivery"]
        oc = delivery.get("original_consolidation")
        approach = self._departure_direction(state, oc)
        final = self._opposite(approach)
        model = self._model_type(final)
        matrix = self._matrix(state, final)
        dealing_range = self._dealing_range(state, matrix, narrative)
        matrix_location = self._matrix_location(matrix, dealing_range)
        location_valid = self._location_valid(model, matrix_location)
        parent_relationship, parent_tf = self._parent_relationship(parent_model, final)
        reasons: list[str] = []

        if oc is None or oc.get("status") != "DISPLACEMENT_CONFIRMED":
            reasons.append("WAIT_FOR_DISPLACEMENT_CONFIRMED_OC")
        if approach == Direction.UNCONFIRMED:
            reasons.append("WAIT_FOR_OC_DEPARTURE_DIRECTION")
        if dealing_range is None:
            reasons.append("WAIT_FOR_DEFINED_DEALING_RANGE")
        if matrix is None:
            reasons.append("WAIT_FOR_PARENT_MATRIX")
        elif not location_valid:
            reasons.append("MATRIX_NOT_IN_REQUIRED_PREMIUM_DISCOUNT_LOCATION")

        reversal = state.get("reversal") or {}
        reversal_aligned = bool(
            reversal.get("confirmed")
            and final.value in DIRECTIONAL
            and reversal.get("csd", {}).get("direction") == final.value
        )
        matrix_reached = bool(matrix and matrix.get("reached")) or reversal_aligned
        matrix_touch_position = self._matrix_touch_position(data, matrix, reversal)
        signature = {"type": SMRSignature.NONE.value, "evidence": None}
        if matrix is not None and reversal_aligned:
            failure = self._failure_swing(
                data,
                matrix=matrix,
                final_direction=final,
                start_position=matrix_touch_position,
            )
            breaker = self._breaker(
                data,
                matrix=matrix,
                final_direction=final,
                start_position=matrix_touch_position,
            )
            if failure is not None:
                signature = {
                    "type": SMRSignature.FAILURE_SWING.value,
                    "evidence": failure,
                }
            elif breaker is not None:
                signature = {"type": SMRSignature.BREAKER.value, "evidence": breaker}

        protected_broken = self._protected_extreme_broken(data, reversal, final)
        continuation = self._continuation_after_reversal(delivery, reversal, final)
        terminal = self._terminal(state, final, reversal)
        terminal_reached = bool(terminal and terminal.get("reached"))

        base_ready = bool(
            oc
            and oc.get("status") == "DISPLACEMENT_CONFIRMED"
            and approach in {Direction.BULLISH, Direction.BEARISH}
            and dealing_range is not None
            and matrix is not None
            and location_valid
        )

        if protected_broken:
            stage = MMXMStage.INVALIDATED
            reasons.append("POST_REVERSAL_PROTECTED_EXTREME_BROKEN")
        elif not base_ready:
            stage = MMXMStage.NO_MODEL
        elif not matrix_reached:
            stage = MMXMStage.CURVE_TO_MATRIX
            reasons.append("DELIVERY_CURVE_HAS_NOT_REACHED_MATRIX")
        elif not reversal_aligned:
            stage = MMXMStage.AT_MATRIX_WAIT_REVERSAL
            reasons.append("MATRIX_TOUCH_IS_NOT_SMART_MONEY_REVERSAL")
        elif signature["type"] == SMRSignature.NONE.value:
            stage = MMXMStage.AT_MATRIX_WAIT_SIGNATURE
            reasons.append("WAIT_FOR_FAILURE_SWING_OR_BREAKER_AT_MATRIX")
        elif terminal_reached:
            stage = MMXMStage.TERMINAL_REACHED
            reasons.append("TERMINAL_LIQUIDITY_REACHED")
        elif continuation:
            stage = MMXMStage.CONTINUATION_PHASE
            reasons.append("FIRST_POST_REVERSAL_REACCUMULATION_REDISTRIBUTION_OBSERVED")
        else:
            stage = MMXMStage.SMART_MONEY_REVERSAL_CONFIRMED
            reasons.append("MATRIX_SIGNATURE_CSD_IOFC_ALIGNED")

        if parent_relationship == "COUNTER_MODEL_WITHIN_PARENT":
            reasons.append("LOCAL_COUNTER_MODEL_DOES_NOT_INVALIDATE_PARENT_MMXM")

        return MMXMDetection(
            timeframe=timeframe,
            model=model if base_ready else MMXMType.UNRESOLVED,
            stage=stage,
            approach_direction=approach,
            final_direction=final,
            original_consolidation=oc,
            dealing_range=dealing_range,
            matrix=matrix,
            matrix_location=matrix_location,
            smart_money_reversal={
                "confirmed": bool(
                    reversal_aligned
                    and signature["type"] != SMRSignature.NONE.value
                    and not protected_broken
                ),
                "signature": signature,
                "csd": reversal.get("csd"),
                "post_csd_iofc": reversal.get("post_csd_iofc"),
            },
            terminal=terminal,
            parent_relationship=parent_relationship,
            parent_timeframe=parent_tf,
            reason_codes=tuple(reasons),
        )

    def analyze_hierarchy(
        self,
        timeframe_bars: dict[str, pd.DataFrame],
        *,
        narrative: dict,
        as_of=None,
    ) -> dict[str, dict]:
        models: dict[str, dict] = {}
        parent: dict | None = None
        for timeframe in narrative["hierarchy"]:
            detection = self.analyze(
                timeframe_bars[timeframe],
                timeframe=timeframe,
                narrative=narrative,
                as_of=as_of,
                parent_model=parent,
            ).to_dict()
            models[timeframe] = detection
            parent = detection
        return models

    @staticmethod
    def entry_contract(
        model: dict,
        *,
        narrative_gate: dict,
        execution_gate: dict,
    ) -> dict:
        reasons: list[str] = []
        stage = model["stage"]
        direction = model["final_direction"]
        parent_relation = model["parent_relationship"]
        aligned_execution = execution_gate.get("direction") == direction

        if stage == MMXMStage.INVALIDATED.value:
            state = EntryState.INVALIDATED
            reasons.append("MMXM_INVALIDATED")
        elif parent_relation == "COUNTER_MODEL_WITHIN_PARENT":
            state = EntryState.WAIT
            reasons.append("HTF_CONTROL_OVERRIDES_COUNTER_MODEL_ENTRY")
        elif not narrative_gate.get("qualified"):
            state = EntryState.WAIT
            reasons.append("WAIT_FOR_PHASE7_NARRATIVE_GATE")
        elif not execution_gate.get("smt_validated") or not aligned_execution:
            state = EntryState.WAIT
            reasons.append("WAIT_FOR_ALIGNED_SMT_CSD_POST_CSD_IOFC")
        elif stage == MMXMStage.SMART_MONEY_REVERSAL_CONFIRMED.value:
            state = EntryState.REVERSAL_READY
            reasons.append("SMART_MONEY_REVERSAL_AND_EXECUTION_GATE_CONFIRMED")
        elif stage == MMXMStage.CONTINUATION_PHASE.value:
            state = EntryState.CONTINUATION_READY
            reasons.append("POST_REVERSAL_CONTINUATION_AND_EXECUTION_GATE_CONFIRMED")
        else:
            state = EntryState.WAIT
            reasons.append("MMXM_STAGE_NOT_ENTRY_READY")

        return {
            "state": state.value,
            "direction": direction,
            "model": model["model"],
            "stage": stage,
            "parent_relationship": parent_relation,
            "reason_codes": reasons,
            "order_authorized": False,
        }

    def _departure_direction(self, state: dict, oc: dict | None) -> Direction:
        if not oc or oc.get("departure_position") is None:
            return Direction.UNCONFIRMED
        departure = int(oc["departure_position"])
        gaps = state["fair_value"].get("gaps", [])
        exact = [gap for gap in gaps if gap.get("formation_position") == departure]
        candidates = exact or [
            gap
            for gap in gaps
            if gap.get("source_position", -1) > oc.get("end_position", -1)
        ]
        if not candidates:
            return Direction.UNCONFIRMED
        try:
            return Direction(candidates[0]["direction"])
        except ValueError:
            return Direction.UNCONFIRMED

    @staticmethod
    def _opposite(direction: Direction) -> Direction:
        if direction == Direction.BULLISH:
            return Direction.BEARISH
        if direction == Direction.BEARISH:
            return Direction.BULLISH
        return Direction.UNCONFIRMED

    @staticmethod
    def _model_type(final: Direction) -> MMXMType:
        if final == Direction.BULLISH:
            return MMXMType.MMBM
        if final == Direction.BEARISH:
            return MMXMType.MMSM
        return MMXMType.UNRESOLVED

    @staticmethod
    def _matrix(state: dict, final: Direction) -> dict | None:
        reversal = state.get("reversal") or {}
        if reversal.get("confirmed") and reversal.get("matrix"):
            return {**reversal["matrix"], "reached": True}
        if final.value not in DIRECTIONAL:
            return None
        candidates = [
            matrix
            for matrix in state.get("parent_matrices", [])
            if matrix.get("direction") == final.value
        ]
        if not candidates:
            return None
        reached = [matrix for matrix in candidates if matrix.get("reached")]
        if reached:
            return max(reached, key=lambda item: item.get("reached_time") or "")
        return min(candidates, key=lambda item: item.get("distance", float("inf")))

    @staticmethod
    def _dealing_range(state: dict, matrix: dict | None, narrative: dict) -> dict | None:
        source = state
        if matrix and matrix.get("timeframe") in narrative["timeframes"]:
            source = narrative["timeframes"][matrix["timeframe"]]
        liquidity = source.get("liquidity", {})
        low = liquidity.get("external_low")
        high = liquidity.get("external_high")
        if low is None or high is None or float(low) >= float(high):
            return None
        low, high = float(low), float(high)
        return {
            "low": low,
            "high": high,
            "equilibrium": (low + high) / 2.0,
            "timeframe": source.get("timeframe"),
        }

    @staticmethod
    def _matrix_location(matrix: dict | None, dealing_range: dict | None) -> str:
        if matrix is None or dealing_range is None:
            return "UNRESOLVED"
        center = (float(matrix["low"]) + float(matrix["high"])) / 2.0
        equilibrium = float(dealing_range["equilibrium"])
        if center > equilibrium:
            return "PREMIUM"
        if center < equilibrium:
            return "DISCOUNT"
        return "EQUILIBRIUM"

    @staticmethod
    def _location_valid(model: MMXMType, location: str) -> bool:
        return bool(
            (model == MMXMType.MMSM and location == "PREMIUM")
            or (model == MMXMType.MMBM and location == "DISCOUNT")
        )

    @staticmethod
    def _matrix_touch_position(data: pd.DataFrame, matrix: dict | None, reversal: dict) -> int:
        csd = reversal.get("csd") or {}
        if csd.get("raid_position") is not None:
            return int(csd["raid_position"])
        if matrix and matrix.get("reached_time"):
            reached = comparable_time(matrix["reached_time"], data.index)
            location = data.index.get_indexer([reached], method="bfill")[0]
            if location >= 0:
                return int(location)
        return 0

    def _failure_swing(
        self,
        data: pd.DataFrame,
        *,
        matrix: dict,
        final_direction: Direction,
        start_position: int,
    ) -> dict | None:
        pivots = self.structure._confirmed_pivots(data)
        matrix_low, matrix_high = float(matrix["low"]), float(matrix["high"])
        if final_direction == Direction.BEARISH:
            extrema = pivots["high"]
            opposite = pivots["low"]
            first_candidates = [
                ref
                for ref in extrema
                if ref.source_position >= start_position
                and data.iloc[ref.source_position].low <= matrix_high
                and ref.price >= matrix_low
            ]
            for first in first_candidates:
                for second in extrema:
                    if second.source_position <= first.source_position or second.price >= first.price:
                        continue
                    intervening = [
                        ref
                        for ref in opposite
                        if first.source_position < ref.source_position < second.source_position
                    ]
                    if not intervening:
                        continue
                    trigger = min(intervening, key=lambda ref: ref.price)
                    after = data.iloc[second.confirmed_position :]
                    broken = after.loc[after.close < trigger.price]
                    if not broken.empty:
                        trigger_time = broken.index[0]
                        return {
                            "direction": final_direction.value,
                            "first_extreme": first.to_dict(),
                            "failed_extreme": second.to_dict(),
                            "intervening_swing": trigger.to_dict(),
                            "trigger_time": trigger_time.isoformat(),
                            "rule": "LOWER_HIGH_THEN_BODY_CLOSE_BELOW_INTERVENING_LOW",
                        }
        elif final_direction == Direction.BULLISH:
            extrema = pivots["low"]
            opposite = pivots["high"]
            first_candidates = [
                ref
                for ref in extrema
                if ref.source_position >= start_position
                and data.iloc[ref.source_position].high >= matrix_low
                and ref.price <= matrix_high
            ]
            for first in first_candidates:
                for second in extrema:
                    if second.source_position <= first.source_position or second.price <= first.price:
                        continue
                    intervening = [
                        ref
                        for ref in opposite
                        if first.source_position < ref.source_position < second.source_position
                    ]
                    if not intervening:
                        continue
                    trigger = max(intervening, key=lambda ref: ref.price)
                    after = data.iloc[second.confirmed_position :]
                    broken = after.loc[after.close > trigger.price]
                    if not broken.empty:
                        trigger_time = broken.index[0]
                        return {
                            "direction": final_direction.value,
                            "first_extreme": first.to_dict(),
                            "failed_extreme": second.to_dict(),
                            "intervening_swing": trigger.to_dict(),
                            "trigger_time": trigger_time.isoformat(),
                            "rule": "HIGHER_LOW_THEN_BODY_CLOSE_ABOVE_INTERVENING_HIGH",
                        }
        return None

    def _breaker(
        self,
        data: pd.DataFrame,
        *,
        matrix: dict,
        final_direction: Direction,
        start_position: int,
    ) -> dict | None:
        if final_direction not in {Direction.BULLISH, Direction.BEARISH}:
            return None
        old_direction = self._opposite(final_direction)
        result = self.order_flow.analyze(data, timeframe="MMXM_BREAKER")
        matrix_low, matrix_high = float(matrix["low"]), float(matrix["high"])
        for event in result.confirmed_events:
            if event.direction != old_direction or event.invalidated_position is None:
                continue
            if event.invalidated_position < start_position:
                continue
            source = data.iloc[event.source_position]
            overlaps_matrix = float(source.low) <= matrix_high and float(source.high) >= matrix_low
            if not overlaps_matrix:
                continue
            subsequent = data.iloc[event.invalidated_position + 1 :]
            for position, (_, row) in enumerate(
                subsequent.iterrows(), start=event.invalidated_position + 1
            ):
                touched = float(row.low) <= event.high and float(row.high) >= event.low
                if not touched:
                    continue
                held = (
                    float(row.close) > event.high
                    if final_direction == Direction.BULLISH
                    else float(row.close) < event.low
                )
                if held:
                    return {
                        "direction": final_direction.value,
                        "failed_order_flow_range": event.to_dict(),
                        "retest_position": position,
                        "retest_time": data.index[position].isoformat(),
                        "rule": "INVALIDATED_OPPOSING_IOF_RANGE_RETESTED_FROM_NEW_SIDE",
                    }
        return None

    @staticmethod
    def _protected_extreme_broken(
        data: pd.DataFrame,
        reversal: dict,
        final_direction: Direction,
    ) -> bool:
        csd = reversal.get("csd") or {}
        position = csd.get("confirmation_position")
        extreme = csd.get("protected_extreme")
        if position is None or extreme is None:
            return False
        after = data.iloc[int(position) + 1 :]
        if final_direction == Direction.BULLISH:
            return bool((after.close < float(extreme)).any())
        if final_direction == Direction.BEARISH:
            return bool((after.close > float(extreme)).any())
        return False

    @staticmethod
    def _continuation_after_reversal(delivery: dict, reversal: dict, final: Direction) -> bool:
        csd = reversal.get("csd") or {}
        position = csd.get("confirmation_position")
        if position is None:
            return False
        expected = "REACCUMULATION" if final == Direction.BULLISH else "REDISTRIBUTION"
        return any(
            event.get("position", -1) > int(position) and event.get("event") == expected
            for event in delivery.get("events", [])
        )

    @staticmethod
    def _terminal(state: dict, final: Direction, reversal: dict) -> dict | None:
        liquidity = state.get("liquidity", {})
        csd = reversal.get("csd") or {}
        confirmation = int(csd.get("confirmation_position", -1))
        target_side = "BUY_SIDE" if final == Direction.BULLISH else "SELL_SIDE"
        pools = liquidity.get("buy_side", []) if target_side == "BUY_SIDE" else liquidity.get("sell_side", [])
        reached = [
            pool
            for pool in pools
            if pool.get("liquidity_class") == "EXTERNAL"
            and pool.get("event_position") is not None
            and int(pool["event_position"]) > confirmation
            and pool.get("status") in {"CONSUMED", "RAIDED_RECLAIMED"}
        ]
        if reached:
            pool = min(reached, key=lambda item: int(item["event_position"]))
            return {**pool, "reached": True, "purpose": "MMXM_TERMINAL"}
        draw = liquidity.get("active_draw")
        if draw and draw.get("side") == target_side:
            return {**draw, "reached": False, "purpose": "MMXM_TERMINAL"}
        return None

    @staticmethod
    def _parent_relationship(parent: dict | None, final: Direction) -> tuple[str, str | None]:
        if parent is None:
            return "ROOT_MODEL", None
        parent_tf = parent.get("timeframe")
        parent_direction = parent.get("final_direction")
        parent_active = parent.get("stage") not in {
            MMXMStage.NO_MODEL.value,
            MMXMStage.INVALIDATED.value,
        }
        if not parent_active or parent_direction not in DIRECTIONAL:
            return "PARENT_UNRESOLVED", parent_tf
        if final.value not in DIRECTIONAL:
            return "LOCAL_UNRESOLVED", parent_tf
        if final.value == parent_direction:
            return "ALIGNED_CHILD_MODEL", parent_tf
        return "COUNTER_MODEL_WITHIN_PARENT", parent_tf
