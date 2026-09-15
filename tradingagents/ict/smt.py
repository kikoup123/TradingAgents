from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

import pandas as pd

from .models import Direction
from .order_flow import _normalize_ohlc


class SMTPolarity(str, Enum):
    SAME = "SAME"
    INVERSE = "INVERSE"


class SMTValidationState(str, Enum):
    NO_SMT = "NO_SMT"
    SMT_DETECTED_WAIT_CSD = "SMT_DETECTED_WAIT_CSD"
    SMT_DETECTED_WAIT_IOF = "SMT_DETECTED_WAIT_IOF"
    SMT_DIRECTION_CONFLICT = "SMT_DIRECTION_CONFLICT"
    SMT_VALIDATED = "SMT_VALIDATED"


@dataclass(frozen=True)
class SMTLegConfig:
    symbol: str
    aliases: tuple[str, ...]
    polarity: SMTPolarity = SMTPolarity.SAME


@dataclass(frozen=True)
class SMTGroupConfig:
    key: str
    legs: tuple[SMTLegConfig, ...]


@dataclass(frozen=True)
class SMTReference:
    side: str
    price: float
    source_position: int
    source_time: str
    confirmed_position: int
    confirmed_time: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SMTResult:
    group: str
    timeframe: str
    reference_time: str | None
    direction: Direction
    detected: bool
    validated: bool
    validation_state: SMTValidationState
    divergence_type: str | None
    leader_symbols: list[str]
    nonconfirming_symbols: list[str]
    polarity_map: dict[str, str]
    compared_levels: dict[str, dict[str, Any]]
    csd_direction: Direction
    iof_direction: Direction
    reason_codes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "group": self.group,
            "timeframe": self.timeframe,
            "reference_time": self.reference_time,
            "direction": self.direction.value,
            "detected": self.detected,
            "validated": self.validated,
            "validation_state": self.validation_state.value,
            "divergence_type": self.divergence_type,
            "leader_symbols": list(self.leader_symbols),
            "nonconfirming_symbols": list(self.nonconfirming_symbols),
            "polarity_map": dict(self.polarity_map),
            "compared_levels": dict(self.compared_levels),
            "csd_direction": self.csd_direction.value,
            "iof_direction": self.iof_direction.value,
            "reason_codes": list(self.reason_codes),
        }


DEFAULT_SMT_GROUPS: dict[str, SMTGroupConfig] = {
    "US_INDEX": SMTGroupConfig(
        key="US_INDEX",
        legs=(
            SMTLegConfig("NQ", ("NQ", "NAS100", "US100", "NASDAQ")),
            SMTLegConfig("ES", ("ES", "US500", "SPX", "SP500")),
            SMTLegConfig("YM", ("YM", "US30", "DJI", "DOW")),
        ),
    ),
    "FX_DXY": SMTGroupConfig(
        key="FX_DXY",
        legs=(
            SMTLegConfig("EURUSD", ("EURUSD", "EUR/USD")),
            SMTLegConfig("GBPUSD", ("GBPUSD", "GBP/USD")),
            SMTLegConfig(
                "DXY",
                ("DXY", "DX", "DX1!", "USDOLLAR"),
                polarity=SMTPolarity.INVERSE,
            ),
        ),
    ),
    "GOLD_RELATIVE": SMTGroupConfig(
        key="GOLD_RELATIVE",
        legs=(
            SMTLegConfig("XAUUSD", ("XAUUSD", "XAU/USD", "GOLD")),
            SMTLegConfig("XAUAUD", ("XAUAUD", "XAU/AUD")),
            SMTLegConfig("XAUCAD", ("XAUCAD", "XAU/CAD")),
        ),
    ),
}


class SMTEngine:
    """Deterministic structural SMT divergence engine.

    Each leg is synchronized to exact common timestamps. The engine compares
    first-time raids of each leg's latest *confirmed* structural pivot rather
    than arbitrary tick differences. Inverse legs are normalized before the
    comparison, so DXY lower-low events correspond to EURUSD/GBPUSD higher-high
    events and vice versa.

    SMT detection and SMT validation are intentionally separate. A divergence
    is only validated when a same-direction CSD has already been confirmed and
    institutional order flow (IOF) is also confirmed in that direction.
    """

    def __init__(self, *, pivot_span: int = 2) -> None:
        if pivot_span < 1:
            raise ValueError("pivot_span must be >= 1")
        self.pivot_span = pivot_span

    def analyze(
        self,
        instrument_bars: dict[str, pd.DataFrame],
        *,
        group: str | SMTGroupConfig,
        timeframe: str,
        csd_direction: Direction = Direction.UNCONFIRMED,
        iof_direction: Direction = Direction.UNCONFIRMED,
        as_of: pd.Timestamp | str | None = None,
    ) -> SMTResult:
        config = self._resolve_group(group)
        resolved = self._resolve_legs(instrument_bars, config)
        synchronized = self._synchronize(resolved, as_of=as_of)
        polarity_map = {leg.symbol: leg.polarity.value for leg in config.legs}

        if not synchronized or len(next(iter(synchronized.values()))) < 2 * self.pivot_span + 2:
            return self._empty_result(
                config=config,
                timeframe=timeframe,
                polarity_map=polarity_map,
                csd_direction=csd_direction,
                iof_direction=iof_direction,
                reason="INSUFFICIENT_SYNCHRONIZED_STRUCTURAL_DATA",
            )

        pivots = {
            symbol: self._confirmed_pivots(data)
            for symbol, data in synchronized.items()
        }
        common_index = next(iter(synchronized.values())).index
        latest_event: dict[str, Any] | None = None
        ambiguous_seen = False

        for position in range(2 * self.pivot_span + 1, len(common_index)):
            canonical_up: dict[str, bool] = {}
            canonical_down: dict[str, bool] = {}
            compared: dict[str, dict[str, Any]] = {}
            complete = True

            for leg in config.legs:
                data = synchronized[leg.symbol]
                high_ref = self._latest_reference(
                    pivots[leg.symbol]["high"], before_position=position
                )
                low_ref = self._latest_reference(
                    pivots[leg.symbol]["low"], before_position=position
                )
                if high_ref is None or low_ref is None:
                    complete = False
                    break

                native_up = self._first_high_take(data, position, high_ref)
                native_down = self._first_low_take(data, position, low_ref)
                if leg.polarity == SMTPolarity.INVERSE:
                    canonical_up[leg.symbol] = native_down
                    canonical_down[leg.symbol] = native_up
                else:
                    canonical_up[leg.symbol] = native_up
                    canonical_down[leg.symbol] = native_down

                compared[leg.symbol] = {
                    "polarity": leg.polarity.value,
                    "high_reference": high_ref.to_dict(),
                    "low_reference": low_ref.to_dict(),
                    "native_high_take": native_up,
                    "native_low_take": native_down,
                }

            if not complete:
                continue

            up_divergence = any(canonical_up.values()) and not all(canonical_up.values())
            down_divergence = any(canonical_down.values()) and not all(canonical_down.values())
            if up_divergence and down_divergence:
                ambiguous_seen = True
                continue
            if not up_divergence and not down_divergence:
                continue

            if up_divergence:
                direction = Direction.BEARISH
                flags = canonical_up
                divergence_type = "HIGH_SIDE_NONCONFIRMATION"
            else:
                direction = Direction.BULLISH
                flags = canonical_down
                divergence_type = "LOW_SIDE_NONCONFIRMATION"

            latest_event = {
                "reference_time": self._time_label(common_index[position], position),
                "direction": direction,
                "divergence_type": divergence_type,
                "leader_symbols": [symbol for symbol, value in flags.items() if value],
                "nonconfirming_symbols": [
                    symbol for symbol, value in flags.items() if not value
                ],
                "compared_levels": compared,
            }

        if latest_event is None:
            reason = "NO_STRUCTURAL_SMT_DIVERGENCE"
            if ambiguous_seen:
                reason = "ONLY_AMBIGUOUS_TWO_SIDED_DIVERGENCE_FOUND"
            return self._empty_result(
                config=config,
                timeframe=timeframe,
                polarity_map=polarity_map,
                csd_direction=csd_direction,
                iof_direction=iof_direction,
                reason=reason,
            )

        direction = latest_event["direction"]
        validation_state = self._validation_state(
            smt_direction=direction,
            csd_direction=csd_direction,
            iof_direction=iof_direction,
        )
        validated = validation_state == SMTValidationState.SMT_VALIDATED
        reason_codes = [
            "STRUCTURAL_SMT_DETECTED",
            f"SMT_DIRECTION_{direction.value}",
            f"CSD_{csd_direction.value}",
            f"IOF_{iof_direction.value}",
            validation_state.value,
        ]
        if any(leg.polarity == SMTPolarity.INVERSE for leg in config.legs):
            reason_codes.append("INVERSE_POLARITY_NORMALIZED")

        return SMTResult(
            group=config.key,
            timeframe=timeframe,
            reference_time=latest_event["reference_time"],
            direction=direction,
            detected=True,
            validated=validated,
            validation_state=validation_state,
            divergence_type=latest_event["divergence_type"],
            leader_symbols=latest_event["leader_symbols"],
            nonconfirming_symbols=latest_event["nonconfirming_symbols"],
            polarity_map=polarity_map,
            compared_levels=latest_event["compared_levels"],
            csd_direction=csd_direction,
            iof_direction=iof_direction,
            reason_codes=reason_codes,
        )

    @staticmethod
    def _resolve_group(group: str | SMTGroupConfig) -> SMTGroupConfig:
        if isinstance(group, SMTGroupConfig):
            return group
        key = group.upper()
        if key not in DEFAULT_SMT_GROUPS:
            raise ValueError(f"Unknown SMT group: {group}")
        return DEFAULT_SMT_GROUPS[key]

    @staticmethod
    def _resolve_legs(
        instrument_bars: dict[str, pd.DataFrame], config: SMTGroupConfig
    ) -> dict[str, pd.DataFrame]:
        normalized_keys = {str(key).upper(): key for key in instrument_bars}
        resolved: dict[str, pd.DataFrame] = {}
        missing: list[str] = []
        for leg in config.legs:
            matched_key = next(
                (
                    normalized_keys[alias.upper()]
                    for alias in leg.aliases
                    if alias.upper() in normalized_keys
                ),
                None,
            )
            if matched_key is None:
                missing.append(leg.symbol)
                continue
            resolved[leg.symbol] = _normalize_ohlc(instrument_bars[matched_key]).sort_index()
        if missing:
            raise ValueError(
                f"Missing SMT legs for {config.key}: {', '.join(missing)}"
            )
        return resolved

    @staticmethod
    def _synchronize(
        resolved: dict[str, pd.DataFrame], *, as_of: pd.Timestamp | str | None
    ) -> dict[str, pd.DataFrame]:
        if not resolved:
            return {}
        common_index = None
        for data in resolved.values():
            if not isinstance(data.index, pd.DatetimeIndex):
                raise ValueError("SMT bars must use a DatetimeIndex")
            common_index = data.index if common_index is None else common_index.intersection(data.index)
        assert common_index is not None
        common_index = common_index.sort_values()
        if as_of is not None and len(common_index):
            cutoff = pd.Timestamp(as_of)
            if common_index.tz is not None and cutoff.tzinfo is None:
                cutoff = cutoff.tz_localize(common_index.tz)
            elif common_index.tz is None and cutoff.tzinfo is not None:
                cutoff = cutoff.tz_localize(None)
            elif common_index.tz is not None and cutoff.tzinfo is not None:
                cutoff = cutoff.tz_convert(common_index.tz)
            common_index = common_index[common_index <= cutoff]
        return {symbol: data.loc[common_index].copy() for symbol, data in resolved.items()}

    def _confirmed_pivots(
        self, data: pd.DataFrame
    ) -> dict[str, list[SMTReference]]:
        span = self.pivot_span
        highs: list[SMTReference] = []
        lows: list[SMTReference] = []
        for position in range(span, len(data) - span):
            row = data.iloc[position]
            left = data.iloc[position - span : position]
            right = data.iloc[position + 1 : position + span + 1]
            confirmation_position = position + span
            if float(row["high"]) > float(left["high"].max()) and float(
                row["high"]
            ) > float(right["high"].max()):
                highs.append(
                    SMTReference(
                        side="HIGH",
                        price=float(row["high"]),
                        source_position=position,
                        source_time=self._time_label(data.index[position], position),
                        confirmed_position=confirmation_position,
                        confirmed_time=self._time_label(
                            data.index[confirmation_position], confirmation_position
                        ),
                    )
                )
            if float(row["low"]) < float(left["low"].min()) and float(
                row["low"]
            ) < float(right["low"].min()):
                lows.append(
                    SMTReference(
                        side="LOW",
                        price=float(row["low"]),
                        source_position=position,
                        source_time=self._time_label(data.index[position], position),
                        confirmed_position=confirmation_position,
                        confirmed_time=self._time_label(
                            data.index[confirmation_position], confirmation_position
                        ),
                    )
                )
        return {"high": highs, "low": lows}

    @staticmethod
    def _latest_reference(
        references: list[SMTReference], *, before_position: int
    ) -> SMTReference | None:
        eligible = [
            reference
            for reference in references
            if reference.confirmed_position < before_position
        ]
        return eligible[-1] if eligible else None

    @staticmethod
    def _first_high_take(
        data: pd.DataFrame, position: int, reference: SMTReference
    ) -> bool:
        current = float(data.iloc[position]["high"])
        if current <= reference.price:
            return False
        previous = data.iloc[reference.confirmed_position : position]
        return previous.empty or float(previous["high"].max()) <= reference.price

    @staticmethod
    def _first_low_take(
        data: pd.DataFrame, position: int, reference: SMTReference
    ) -> bool:
        current = float(data.iloc[position]["low"])
        if current >= reference.price:
            return False
        previous = data.iloc[reference.confirmed_position : position]
        return previous.empty or float(previous["low"].min()) >= reference.price

    @staticmethod
    def _validation_state(
        *,
        smt_direction: Direction,
        csd_direction: Direction,
        iof_direction: Direction,
    ) -> SMTValidationState:
        if csd_direction not in {Direction.BULLISH, Direction.BEARISH}:
            return SMTValidationState.SMT_DETECTED_WAIT_CSD
        if csd_direction != smt_direction:
            return SMTValidationState.SMT_DIRECTION_CONFLICT
        if iof_direction not in {Direction.BULLISH, Direction.BEARISH}:
            return SMTValidationState.SMT_DETECTED_WAIT_IOF
        if iof_direction != smt_direction:
            return SMTValidationState.SMT_DIRECTION_CONFLICT
        return SMTValidationState.SMT_VALIDATED

    @staticmethod
    def _empty_result(
        *,
        config: SMTGroupConfig,
        timeframe: str,
        polarity_map: dict[str, str],
        csd_direction: Direction,
        iof_direction: Direction,
        reason: str,
    ) -> SMTResult:
        return SMTResult(
            group=config.key,
            timeframe=timeframe,
            reference_time=None,
            direction=Direction.UNCONFIRMED,
            detected=False,
            validated=False,
            validation_state=SMTValidationState.NO_SMT,
            divergence_type=None,
            leader_symbols=[],
            nonconfirming_symbols=[],
            polarity_map=polarity_map,
            compared_levels={},
            csd_direction=csd_direction,
            iof_direction=iof_direction,
            reason_codes=[reason],
        )

    @staticmethod
    def _time_label(index_value: object, position: int) -> str:
        if hasattr(index_value, "isoformat"):
            try:
                return index_value.isoformat()
            except TypeError:
                pass
        return str(index_value) if index_value is not None else str(position)
