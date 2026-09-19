from __future__ import annotations

from tradingagents.dataflows.ctrader_csd import analyze_csd_orderflow
from tradingagents.dataflows.ctrader_smt import detect_smt
from tradingagents.dataflows.ctrader_entry import evaluate_ltf_continuation


def _direction_from_control(control: str | None) -> str | None:
    if control == "bullish_control":
        return "bullish"
    if control == "bearish_control":
        return "bearish"
    return None


def _htf_gate(
    direction: str,
    h4: dict,
    h1: dict,
) -> dict:

    required = f"{direction}_control"
    opposite = (
        "bearish_control"
        if direction == "bullish"
        else "bullish_control"
    )

    h4_control = h4.get("current_orderflow_control", "none")
    h1_control = h1.get("current_orderflow_control", "none")

    if h4_control == opposite or h1_control == opposite:
        status = "FAIL"
        passed = False
        reason = "At least one HTF is under confirmed opposing control."

    elif h4_control == required and h1_control == required:
        status = "PASS"
        passed = True
        reason = "H4 and H1 confirmed order flow are aligned."

    else:
        status = "PARTIAL"
        passed = False
        reason = (
            "H4/H1 do not both have confirmed order-flow control "
            "in the execution direction."
        )

    return {
        "status": status,
        "passed": passed,
        "direction_required": direction,
        "H4_control": h4_control,
        "H1_control": h1_control,
        "reason": reason,
    }


def evaluate_master_setup(
    symbol: str = "NASDAQ",
    htf_count: int = 300,
    execution_count: int = 500,
    pivot_window: int = 2,
    confirmation_bars: int = 10,
) -> dict:

    symbol = symbol.strip().upper()

    if symbol not in {
        "NASDAQ",
        "NQ",
        "US100",
        "USTECH100",
        "US TECH 100",
    }:
        raise ValueError(
            "Master setup v1 currently supports the NASDAQ/US500 model."
        )

    # --------------------------------------------------
    # 1. Higher-timeframe deterministic context
    # --------------------------------------------------

    h4 = analyze_csd_orderflow(
        symbol="NASDAQ",
        timeframe="H4",
        count=htf_count,
        pivot_window=pivot_window,
        confirmation_bars=confirmation_bars,
    )

    h1 = analyze_csd_orderflow(
        symbol="NASDAQ",
        timeframe="H1",
        count=htf_count,
        pivot_window=pivot_window,
        confirmation_bars=confirmation_bars,
    )

    # --------------------------------------------------
    # 2. Execution-timeframe CSD / IOF
    # --------------------------------------------------

    m15 = analyze_csd_orderflow(
        symbol="NASDAQ",
        timeframe="M15",
        count=execution_count,
        pivot_window=pivot_window,
        confirmation_bars=confirmation_bars,
    )

    execution_control = m15.get(
        "current_orderflow_control",
        "none",
    )

    direction = _direction_from_control(
        execution_control
    )

    if direction is None:
        return {
            "instrument": "NASDAQ",
            "correlated_market": "US500",

            "setup_status": "BLOCKED",
            "entry_allowed": False,

            "reason": (
                "No confirmed NASDAQ M15 post-CSD institutional "
                "order-flow control."
            ),

            "gates": {
                "HTF": "NOT_EVALUATED",
                "LIQUIDITY": "NOT_CONFIRMED",
                "SMT": "NOT_EVALUATED",
                "CSD": "NOT_CONFIRMED",
                "IOF": "NOT_CONFIRMED",
                "LTF": "NOT_EVALUATED",
                "ENTRY": "BLOCKED",
            },

            "m15_control": execution_control,
        }

    # --------------------------------------------------
    # 3. HTF alignment
    # --------------------------------------------------

    htf_gate = _htf_gate(
        direction,
        h4,
        h1,
    )

    # --------------------------------------------------
    # 4. Liquidity + CSD gates
    # --------------------------------------------------

    active_csd = m15.get(
        "active_csd"
    ) or {}

    expected_liquidity = (
        "sell_side_raid"
        if direction == "bullish"
        else "buy_side_raid"
    )

    liquidity_pass = (
        active_csd.get("confirmed") is True
        and active_csd.get("direction") == direction
        and active_csd.get("liquidity") == expected_liquidity
    )

    csd_pass = (
        active_csd.get("confirmed") is True
        and active_csd.get("direction") == direction
    )

    # --------------------------------------------------
    # 5. Post-CSD IOF gate
    # --------------------------------------------------

    post_iof = m15.get(
        "post_csd_iof"
    ) or {}

    iof_pass = (
        execution_control == f"{direction}_control"
        and post_iof.get("confirmed") is True
        and post_iof.get("still_holding") is True
    )

    # --------------------------------------------------
    # 6. Deterministic SMT
    # --------------------------------------------------

    smt = detect_smt(
        timeframe="M15",
        count=execution_count,
        pivot_window=pivot_window,
    )

    if direction == "bullish":
        smt_detail = smt.get(
            "bullish_smt",
            {},
        )
    else:
        smt_detail = smt.get(
            "bearish_smt",
            {},
        )

    smt_pass = (
        smt_detail.get("detected") is True
    )

    # --------------------------------------------------
    # 7. Deterministic LTF continuation
    # --------------------------------------------------

    ltf = evaluate_ltf_continuation(
        symbol="NASDAQ",
        ltf_timeframe="M5",
        csd_timeframe="M15",
        count=execution_count,
        pivot_window=pivot_window,
        confirmation_bars=confirmation_bars,
    )

    ltf_pass = (
        ltf.get("ltf_gate_passed") is True
        and ltf.get("status") == "VALID_CONTINUATION"
        and ltf.get("direction") == direction
        and ltf.get("protected_swing_intact") is True
    )

    # --------------------------------------------------
    # 8. Master pre-entry state
    # --------------------------------------------------

    gates = {
        "HTF": (
            "PASS"
            if htf_gate["passed"]
            else htf_gate["status"]
        ),

        "LIQUIDITY": (
            "PASS"
            if liquidity_pass
            else "FAIL"
        ),

        "SMT": (
            "PASS"
            if smt_pass
            else "FAIL"
        ),

        "CSD": (
            "PASS"
            if csd_pass
            else "FAIL"
        ),

        "IOF": (
            "PASS"
            if iof_pass
            else "FAIL"
        ),

        "LTF": (
            "PASS"
            if ltf_pass
            else "FAIL"
        ),

        # Housing candle / Unicorn entry model
        # has not yet been mechanically encoded.
        "ENTRY": "NOT_EVALUATED",
    }

    mandatory_pre_entry = (
        htf_gate["passed"]
        and liquidity_pass
        and smt_pass
        and csd_pass
        and iof_pass
        and ltf_pass
    )

    if mandatory_pre_entry:
        setup_status = "READY_FOR_ENTRY_MODEL"
    else:
        setup_status = "BLOCKED"

    failed_gates = [
        name
        for name in (
            "HTF",
            "LIQUIDITY",
            "SMT",
            "CSD",
            "IOF",
            "LTF",
        )
        if gates[name] != "PASS"
    ]

    return {
        "instrument": "NASDAQ",
        "correlated_market": "US500",

        "direction": direction,

        "setup_status": setup_status,

        # Remains False until the final entry model
        # is mechanically implemented and confirmed.
        "entry_allowed": False,

        "pre_entry_gates_passed":
            mandatory_pre_entry,

        "failed_gates":
            failed_gates,

        "gates":
            gates,

        "HTF_detail":
            htf_gate,

        "liquidity_detail": {
            "expected": expected_liquidity,
            "passed": liquidity_pass,
            "event": (
                active_csd.get("liquidity")
            ),
            "raid_time": (
                active_csd.get("raid_time")
            ),
        },

        "SMT_detail":
            smt_detail,

        "CSD_detail": {
            "passed": csd_pass,
            "direction": (
                active_csd.get("direction")
            ),
            "threshold": (
                active_csd.get("csd_threshold")
            ),
            "confirmation_time": (
                active_csd.get("confirmation_time")
            ),
        },

        "IOF_detail": {
            "passed": iof_pass,
            "control": execution_control,
            "source_time": (
                post_iof.get("source_time")
            ),
            "confirmation_time": (
                post_iof.get("confirmation_time")
            ),
            "still_holding": (
                post_iof.get("still_holding")
            ),
        },

        "LTF_detail": {
            "passed": ltf_pass,
            "status": ltf.get("status"),
            "protected_swing_intact":
                ltf.get(
                    "protected_swing_intact"
                ),
            "reason":
                ltf.get("reason"),
        },

        "hierarchy": [
            "HTF context",
            "Liquidity raid",
            "SMT",
            "CSD",
            "Post-CSD IOF",
            "LTF continuation",
            "Entry model",
        ],

        "safety": {
            "execution_enabled": False,
            "order_placement": False,
            "reason": (
                "Final entry model has not yet "
                "been deterministically implemented."
            ),
        },
    }
