"""Deterministic CSD standard-deviation targets and runner management.

The Londres target model measures the range created by the same validated
SMT -> CSD event used by the execution gate. The CSD threshold/opening level
is the zero reference and the protected extreme is the other end of the range.
Targets are projected away from the protected extreme in trade direction.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class TargetManagementStatus(str, Enum):
    READY = "READY"
    WAIT_FOR_VALIDATED_CSD = "WAIT_FOR_VALIDATED_CSD"
    INVALID_CSD_RANGE = "INVALID_CSD_RANGE"


class TraderExitMode(str, Enum):
    FULL_AT_SD_2 = "FULL_AT_SD_2"
    FULL_AT_SD_2_5 = "FULL_AT_SD_2_5"
    HOLD_HTF_LIQUIDITY = "HOLD_HTF_LIQUIDITY"
    NONE = "NONE"


class RunnerAction(str, Enum):
    HOLD_RUNNER = "HOLD_RUNNER"
    CLOSE_EARLY = "CLOSE_EARLY"


@dataclass(frozen=True)
class SDTarget:
    label: str
    multiplier: float
    price: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TargetManagementContext:
    status: TargetManagementStatus
    direction: str
    csd_level: float | None
    protected_extreme: float | None
    range_size: float | None
    sd_2: SDTarget | None
    sd_2_5: SDTarget | None
    htf_runner_target: dict | None
    htf_liquidity_timeframe: str | None
    hold_available: bool
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        full_exit_options: list[dict[str, Any]] = []
        if self.sd_2 is not None:
            full_exit_options.append(
                {
                    "mode": TraderExitMode.FULL_AT_SD_2.value,
                    "label": self.sd_2.label,
                    "price": self.sd_2.price,
                }
            )
        if self.sd_2_5 is not None:
            full_exit_options.append(
                {
                    "mode": TraderExitMode.FULL_AT_SD_2_5.value,
                    "label": self.sd_2_5.label,
                    "price": self.sd_2_5.price,
                }
            )

        partial = None
        if self.sd_2_5 is not None:
            partial = {
                "trigger_label": self.sd_2_5.label,
                "trigger_price": self.sd_2_5.price,
                "close_fraction": 0.60,
                "runner_fraction": 0.40,
                "automatic_if_hold_mode": True,
            }

        return {
            "status": self.status.value,
            "direction": self.direction,
            "csd_level": self.csd_level,
            "protected_extreme": self.protected_extreme,
            "range_size": self.range_size,
            "sd_targets": {
                "-2": self.sd_2.to_dict() if self.sd_2 else None,
                "-2.5": self.sd_2_5.to_dict() if self.sd_2_5 else None,
            },
            "full_exit_options": full_exit_options,
            "hold_htf_liquidity_available": self.hold_available,
            "htf_runner_target": self.htf_runner_target,
            "htf_liquidity_timeframe": self.htf_liquidity_timeframe,
            "hold_management": {
                "partial": partial,
                "runner_fraction": 0.40 if self.hold_available else 0.0,
                "early_close_allowed": self.hold_available,
                "original_target_must_be_preserved": True,
            },
            "selection_required": self.status == TargetManagementStatus.READY,
            "order_authorized": False,
            "reason_codes": list(self.reason_codes),
        }


class CSDTargetEngine:
    """Build -2 / -2.5 CSD projections and the optional HTF runner target."""

    def analyze(self, context: dict) -> TargetManagementContext:
        validation_csd = context.get("validation_csd") or {}
        direction = str(
            validation_csd.get("direction")
            or (context.get("execution_gate") or {}).get("direction")
            or "UNCONFIRMED"
        )
        threshold = validation_csd.get("threshold_open")
        protected = validation_csd.get("protected_extreme")

        if threshold is None or protected is None or direction not in {"BULLISH", "BEARISH"}:
            return TargetManagementContext(
                status=TargetManagementStatus.WAIT_FOR_VALIDATED_CSD,
                direction=direction,
                csd_level=None if threshold is None else float(threshold),
                protected_extreme=None if protected is None else float(protected),
                range_size=None,
                sd_2=None,
                sd_2_5=None,
                htf_runner_target=None,
                htf_liquidity_timeframe=None,
                hold_available=False,
                reason_codes=("VALIDATED_SMT_CSD_RANGE_REQUIRED",),
            )

        csd_level = float(threshold)
        protected_extreme = float(protected)
        if direction == "BEARISH":
            range_size = protected_extreme - csd_level
        else:
            range_size = csd_level - protected_extreme

        if range_size <= 0:
            return TargetManagementContext(
                status=TargetManagementStatus.INVALID_CSD_RANGE,
                direction=direction,
                csd_level=csd_level,
                protected_extreme=protected_extreme,
                range_size=range_size,
                sd_2=None,
                sd_2_5=None,
                htf_runner_target=None,
                htf_liquidity_timeframe=None,
                hold_available=False,
                reason_codes=("CSD_PROTECTED_EXTREME_DIRECTIONAL_GEOMETRY_INVALID",),
            )

        sign = -1.0 if direction == "BEARISH" else 1.0
        sd_2 = SDTarget(label="-2", multiplier=2.0, price=csd_level + sign * 2.0 * range_size)
        sd_2_5 = SDTarget(
            label="-2.5",
            multiplier=2.5,
            price=csd_level + sign * 2.5 * range_size,
        )

        liquidity = context.get("liquidity") or {}
        runner = self._htf_runner_target(
            liquidity,
            direction=direction,
            beyond_price=sd_2_5.price,
        )
        reasons = [
            "CSD_THRESHOLD_IS_ZERO_REFERENCE",
            "PROTECTED_EXTREME_DEFINES_ONE_RANGE_UNIT",
            "SD_2_AND_SD_2_5_PROJECTED_IN_TRADE_DIRECTION",
        ]
        if runner is None:
            reasons.append("HTF_EXTERNAL_RUNNER_LIQUIDITY_UNAVAILABLE")
        else:
            reasons.append("HTF_EXTERNAL_RUNNER_LIQUIDITY_AVAILABLE_BEYOND_SD_2_5")

        return TargetManagementContext(
            status=TargetManagementStatus.READY,
            direction=direction,
            csd_level=csd_level,
            protected_extreme=protected_extreme,
            range_size=range_size,
            sd_2=sd_2,
            sd_2_5=sd_2_5,
            htf_runner_target=runner,
            htf_liquidity_timeframe=liquidity.get("timeframe"),
            hold_available=runner is not None,
            reason_codes=tuple(reasons),
        )

    @staticmethod
    def _htf_runner_target(
        liquidity: dict,
        *,
        direction: str,
        beyond_price: float,
    ) -> dict | None:
        key = "sell_side" if direction == "BEARISH" else "buy_side"
        pools = []
        for pool in liquidity.get(key, []):
            if pool.get("status") != "ACTIVE":
                continue
            if pool.get("liquidity_class") != "EXTERNAL":
                continue
            price = pool.get("price")
            if price is None:
                continue
            price = float(price)
            if direction == "BEARISH" and price >= beyond_price:
                continue
            if direction == "BULLISH" and price <= beyond_price:
                continue
            pools.append((price, pool))

        if not pools:
            return None

        if direction == "BEARISH":
            price, pool = max(pools, key=lambda item: item[0])
            side = "SELL_SIDE"
        else:
            price, pool = min(pools, key=lambda item: item[0])
            side = "BUY_SIDE"

        return {
            "price": price,
            "side": side,
            "liquidity_class": "EXTERNAL",
            "timeframe": liquidity.get("timeframe"),
            "source_kind": pool.get("source_kind"),
            "source_time": pool.get("source_time"),
            "status": pool.get("status"),
            "source": "HTF_EXTERNAL_LIQUIDITY",
        }


def select_target_management(plan: dict, mode: TraderExitMode | str) -> dict:
    """Hard-validate a Trader exit mode against deterministic target options."""
    selected = mode if isinstance(mode, TraderExitMode) else TraderExitMode(mode)
    if plan.get("status") != TargetManagementStatus.READY.value:
        raise ValueError("target management is not ready")

    targets = plan.get("sd_targets") or {}
    if selected == TraderExitMode.FULL_AT_SD_2:
        target = targets.get("-2")
        if not target:
            raise ValueError("-2 target is unavailable")
        return {
            "selected_exit_mode": selected.value,
            "original_target": target,
            "selected_sd_target": target,
            "hold_for_htf_liquidity": False,
            "partial_trigger": None,
            "partial_fraction": 0.0,
            "runner_fraction": 0.0,
            "runner_target": None,
            "early_exit_reason": None,
            "order_authorized": False,
        }

    if selected == TraderExitMode.FULL_AT_SD_2_5:
        target = targets.get("-2.5")
        if not target:
            raise ValueError("-2.5 target is unavailable")
        return {
            "selected_exit_mode": selected.value,
            "original_target": target,
            "selected_sd_target": target,
            "hold_for_htf_liquidity": False,
            "partial_trigger": None,
            "partial_fraction": 0.0,
            "runner_fraction": 0.0,
            "runner_target": None,
            "early_exit_reason": None,
            "order_authorized": False,
        }

    if selected == TraderExitMode.HOLD_HTF_LIQUIDITY:
        if not plan.get("hold_htf_liquidity_available"):
            raise ValueError("HTF liquidity hold mode is unavailable")
        runner_target = plan.get("htf_runner_target")
        partial = (plan.get("hold_management") or {}).get("partial")
        if runner_target is None or partial is None:
            raise ValueError("hold mode requires both partial and runner target")
        return {
            "selected_exit_mode": selected.value,
            "original_target": runner_target,
            "selected_sd_target": targets.get("-2.5"),
            "hold_for_htf_liquidity": True,
            "partial_trigger": partial,
            "partial_fraction": 0.60,
            "runner_fraction": 0.40,
            "runner_target": runner_target,
            "early_exit_reason": None,
            "order_authorized": False,
        }

    raise ValueError("active trade requires a deterministic exit mode")


def manage_runner(selection: dict, action: RunnerAction | str, *, reason: str | None = None) -> dict:
    """Record runner management without rewriting the original planned target."""
    if not selection.get("hold_for_htf_liquidity"):
        raise ValueError("runner management requires HOLD_HTF_LIQUIDITY mode")

    runner_action = action if isinstance(action, RunnerAction) else RunnerAction(action)
    managed = dict(selection)
    managed["runner_action"] = runner_action.value

    if runner_action == RunnerAction.CLOSE_EARLY:
        clean_reason = (reason or "").strip()
        if not clean_reason:
            raise ValueError("early runner close requires a recorded reason")
        managed["early_exit_reason"] = clean_reason
        managed["runner_status"] = "EARLY_CLOSE_REQUESTED"
    else:
        managed["early_exit_reason"] = None
        managed["runner_status"] = "HOLDING_FOR_ORIGINAL_HTF_TARGET"

    managed["order_authorized"] = False
    return managed
