"""Deterministic Londres stop-anchor options.

This module does not place a broker stop.  It exposes the two structural
invalidation choices defined by the Londres execution model after a validated
SMT -> CSD -> IOF sequence:

* IOF_RANGE: beyond the latest still-valid directional order-flow range formed
  after the validating CSD.
* SMT_PROTECTED: beyond the protected extreme of the CSD selected for the
  current SMT event.

The Trader may choose between the valid candidates, but may not invent a third
structural anchor.  A later risk/execution layer will apply the instrument's
minimum tick/buffer beyond the selected anchor.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

import pandas as pd

from .market_data import closed_bars


class StopSource(str, Enum):
    IOF_RANGE = "IOF_RANGE"
    SMT_PROTECTED = "SMT_PROTECTED"
    NONE = "NONE"


@dataclass(frozen=True)
class StopCandidate:
    source: StopSource
    anchor_price: float
    placement: str
    direction: str
    valid: bool
    distance_from_current: float
    structural_source: str
    range_low: float | None = None
    range_high: float | None = None
    source_position: int | None = None
    confirmed_position: int | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["source"] = self.source.value
        payload["entry_signal"] = False
        payload["executable_stop_price"] = None
        payload["requires_buffer_beyond_anchor"] = True
        return payload


@dataclass(frozen=True)
class StopSelectionContext:
    direction: str
    current_price: float
    candidates: tuple[StopCandidate, ...]
    selection_required: bool
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "direction": self.direction,
            "current_price": self.current_price,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "allowed_sources": [candidate.source.value for candidate in self.candidates if candidate.valid],
            "selection_required": self.selection_required,
            "selected_source": StopSource.NONE.value,
            "selected_anchor_price": None,
            "order_authorized": False,
            "reason_codes": list(self.reason_codes),
        }


class StopSelectionEngine:
    """Build valid structural stop-anchor choices from Phase 9 context."""

    def analyze(
        self,
        context: dict,
        execution_bars: pd.DataFrame,
        *,
        as_of=None,
    ) -> StopSelectionContext:
        data = closed_bars(execution_bars, as_of)
        current_price = float(data.iloc[-1].close)
        trade_plan = context.get("trade_plan") or {}
        direction = trade_plan.get("direction") or (
            (context.get("mmxm") or {}).get("execution_model") or {}
        ).get("final_direction", "UNCONFIRMED")
        reasons: list[str] = []

        if trade_plan.get("state") != "READY_FOR_ENTRY_SELECTION":
            reasons.append("TRADE_PLAN_NOT_READY_FOR_STOP_SELECTION")
            return StopSelectionContext(
                direction=direction,
                current_price=current_price,
                candidates=(),
                selection_required=False,
                reason_codes=tuple(reasons),
            )

        candidates: list[StopCandidate] = []
        iof_candidate = self._iof_candidate(context, current_price=current_price, direction=direction)
        if iof_candidate is not None:
            candidates.append(iof_candidate)
        else:
            reasons.append("NO_VALID_POST_CSD_DIRECTIONAL_IOF_RANGE")

        smt_candidate = self._smt_protected_candidate(
            context,
            current_price=current_price,
            direction=direction,
        )
        if smt_candidate is not None:
            candidates.append(smt_candidate)
        else:
            reasons.append("NO_VALID_SMT_PROTECTED_EXTREME")

        valid = [candidate for candidate in candidates if candidate.valid]
        if not valid:
            reasons.append("NO_VALID_STRUCTURAL_STOP_OPTION")
        elif len(valid) == 1:
            reasons.append("SINGLE_STRUCTURAL_STOP_OPTION_AVAILABLE")
        else:
            reasons.append("TRADER_MAY_SELECT_IOF_OR_SMT_PROTECTED_STOP")

        return StopSelectionContext(
            direction=direction,
            current_price=current_price,
            candidates=tuple(candidates),
            selection_required=bool(valid),
            reason_codes=tuple(reasons),
        )

    @staticmethod
    def _iof_candidate(
        context: dict,
        *,
        current_price: float,
        direction: str,
    ) -> StopCandidate | None:
        narrative = context.get("bias_narrative") or {}
        execution_tf = narrative.get("execution_timeframe")
        if execution_tf is None:
            return None
        local = (narrative.get("timeframes") or {}).get(execution_tf) or {}
        reversal = local.get("reversal") or {}
        csd = reversal.get("csd") or context.get("validation_csd") or {}
        csd_position = csd.get("confirmation_position")
        flow = local.get("order_flow") or {}

        if direction == "BULLISH":
            ranges = list(flow.get("active_support_ranges") or [])
            placement = "BELOW_RANGE_LOW"
            anchor_key = "low"
        elif direction == "BEARISH":
            ranges = list(flow.get("active_resistance_ranges") or [])
            placement = "ABOVE_RANGE_HIGH"
            anchor_key = "high"
        else:
            return None

        eligible = []
        for item in ranges:
            if item.get("direction") != direction:
                continue
            source_position = item.get("source_position")
            if csd_position is not None and source_position is not None:
                if int(source_position) <= int(csd_position):
                    continue
            eligible.append(item)

        # The latest still-valid range supporting current delivery is the
        # relevant "last order-flow range".  confirmed_position is preferred
        # because a source candle can form before the actual body-acceptance
        # confirmation.
        if eligible:
            selected = max(
                eligible,
                key=lambda item: (
                    int(item.get("confirmed_position", -1) or -1),
                    int(item.get("source_position", -1) or -1),
                ),
            )
        else:
            iofc = reversal.get("post_csd_iofc") or context.get("post_csd_iofc") or {}
            selected = iofc.get("confirmation_range") if iofc.get("confirmed") else None
            if not selected:
                return None

        if selected.get(anchor_key) is None:
            return None
        anchor = float(selected[anchor_key])
        valid = anchor < current_price if direction == "BULLISH" else anchor > current_price
        return StopCandidate(
            source=StopSource.IOF_RANGE,
            anchor_price=anchor,
            placement=placement,
            direction=direction,
            valid=valid,
            distance_from_current=abs(current_price - anchor),
            structural_source="LATEST_VALID_DIRECTIONAL_IOF_RANGE_AFTER_CSD",
            range_low=float(selected["low"]) if selected.get("low") is not None else None,
            range_high=float(selected["high"]) if selected.get("high") is not None else None,
            source_position=selected.get("source_position"),
            confirmed_position=selected.get("confirmed_position"),
        )

    @staticmethod
    def _smt_protected_candidate(
        context: dict,
        *,
        current_price: float,
        direction: str,
    ) -> StopCandidate | None:
        # Phase 6 chooses validation_csd only when it confirms at/after the
        # current SMT event.  Its protected extreme therefore belongs to the
        # same validated SMT -> CSD reversal sequence and is the deterministic
        # protected swing available to the stop selector.
        csd = context.get("validation_csd") or {}
        protected = csd.get("protected_extreme")
        if protected is None:
            narrative = context.get("bias_narrative") or {}
            execution_tf = narrative.get("execution_timeframe")
            local = (narrative.get("timeframes") or {}).get(execution_tf) or {}
            protected = ((local.get("reversal") or {}).get("csd") or {}).get("protected_extreme")
        if protected is None or direction not in {"BULLISH", "BEARISH"}:
            return None

        anchor = float(protected)
        valid = anchor < current_price if direction == "BULLISH" else anchor > current_price
        return StopCandidate(
            source=StopSource.SMT_PROTECTED,
            anchor_price=anchor,
            placement="BELOW_PROTECTED_LOW" if direction == "BULLISH" else "ABOVE_PROTECTED_HIGH",
            direction=direction,
            valid=valid,
            distance_from_current=abs(current_price - anchor),
            structural_source="POST_SMT_CSD_PROTECTED_EXTREME",
        )

    @staticmethod
    def validate_selection(
        stop_context: dict,
        *,
        selected_source: str,
    ) -> dict:
        """Hard-validate a Trader stop-source choice against deterministic options."""
        valid = {
            item["source"]: item
            for item in stop_context.get("candidates", [])
            if item.get("valid")
        }
        if not valid:
            return {
                "valid": False,
                "selected_source": StopSource.NONE.value,
                "selected_anchor_price": None,
                "reason": "NO_VALID_STRUCTURAL_STOP_OPTION",
            }

        if len(valid) == 1:
            # No discretionary choice exists when only one structural anchor is valid.
            source, candidate = next(iter(valid.items()))
            return {
                "valid": True,
                "selected_source": source,
                "selected_anchor_price": float(candidate["anchor_price"]),
                "placement": candidate["placement"],
                "reason": "ONLY_VALID_STRUCTURAL_STOP_SELECTED",
            }

        candidate = valid.get(selected_source)
        if candidate is None:
            return {
                "valid": False,
                "selected_source": StopSource.NONE.value,
                "selected_anchor_price": None,
                "reason": "TRADER_SELECTED_STOP_OUTSIDE_ALLOWED_CANDIDATES",
            }
        return {
            "valid": True,
            "selected_source": selected_source,
            "selected_anchor_price": float(candidate["anchor_price"]),
            "placement": candidate["placement"],
            "reason": "TRADER_STRUCTURAL_STOP_SELECTION_ACCEPTED",
        }
