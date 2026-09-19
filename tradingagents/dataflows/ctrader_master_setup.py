from __future__ import annotations

from tradingagents.dataflows.ctrader_csd import analyze_csd_orderflow
from tradingagents.dataflows.ctrader_entry import evaluate_ltf_continuation
from tradingagents.dataflows.ctrader_positional import evaluate_positional_entry
from tradingagents.dataflows.ctrader_smt import detect_smt
from tradingagents.dataflows.ctrader_unicorn import evaluate_unicorn_entry


def _direction_from_control(
    control: str | None,
) -> str | None:

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

    required = (
        f"{direction}_control"
    )

    opposite = (
        "bearish_control"
        if direction == "bullish"
        else "bullish_control"
    )

    h4_control = h4.get(
        "current_orderflow_control",
        "none",
    )

    h1_control = h1.get(
        "current_orderflow_control",
        "none",
    )

    if (
        h4_control == opposite
        or h1_control == opposite
    ):
        status = "FAIL"
        passed = False

        reason = (
            "At least one HTF is under "
            "confirmed opposing control."
        )

    elif (
        h4_control == required
        and h1_control == required
    ):
        status = "PASS"
        passed = True

        reason = (
            "H4 and H1 confirmed order "
            "flow are aligned."
        )

    else:
        status = "PARTIAL"
        passed = False

        reason = (
            "H4/H1 do not both have "
            "confirmed order-flow control "
            "in the execution direction."
        )

    return {
        "status": status,
        "passed": passed,

        "direction_required":
            direction,

        "H4_control":
            h4_control,

        "H1_control":
            h1_control,

        "reason":
            reason,
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
            "Master setup currently supports "
            "the NASDAQ/US500 model."
        )

    # ==================================================
    # HTF CONTEXT
    # ==================================================

    h4 = analyze_csd_orderflow(
        symbol="NASDAQ",
        timeframe="H4",
        count=htf_count,
        pivot_window=pivot_window,
        confirmation_bars=
            confirmation_bars,
    )

    h1 = analyze_csd_orderflow(
        symbol="NASDAQ",
        timeframe="H1",
        count=htf_count,
        pivot_window=pivot_window,
        confirmation_bars=
            confirmation_bars,
    )

    # ==================================================
    # FIRST CSD / M15 IOF
    # ==================================================

    m15 = analyze_csd_orderflow(
        symbol="NASDAQ",
        timeframe="M15",
        count=execution_count,
        pivot_window=pivot_window,
        confirmation_bars=
            confirmation_bars,
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
            "instrument":
                "NASDAQ",

            "correlated_market":
                "US500",

            "setup_status":
                "BLOCKED",

            "entry_allowed":
                False,

            "reason": (
                "No confirmed NASDAQ M15 "
                "post-CSD institutional "
                "order-flow control."
            ),

            "gates": {
                "HTF":
                    "NOT_EVALUATED",

                "LIQUIDITY":
                    "NOT_CONFIRMED",

                "SMT":
                    "NOT_EVALUATED",

                "CSD":
                    "NOT_CONFIRMED",

                "IOF":
                    "NOT_CONFIRMED",

                "LTF":
                    "NOT_EVALUATED",

                "ENTRY":
                    "BLOCKED",
            },

            "m15_control":
                execution_control,

            "safety": {
                "execution_enabled":
                    False,

                "order_placement":
                    False,
            },
        }

    # ==================================================
    # HTF GATE
    # ==================================================

    htf_gate = _htf_gate(
        direction,
        h4,
        h1,
    )

    # ==================================================
    # LIQUIDITY + FIRST CSD
    # ==================================================

    active_csd = (
        m15.get("active_csd")
        or {}
    )

    expected_liquidity = (
        "sell_side_raid"
        if direction == "bullish"
        else "buy_side_raid"
    )

    liquidity_pass = (
        active_csd.get("confirmed")
        is True
        and active_csd.get(
            "direction"
        ) == direction
        and active_csd.get(
            "liquidity"
        ) == expected_liquidity
    )

    csd_pass = (
        active_csd.get("confirmed")
        is True
        and active_csd.get(
            "direction"
        ) == direction
    )

    # ==================================================
    # FIRST POST-CSD IOF
    # ==================================================

    post_iof = (
        m15.get("post_csd_iof")
        or {}
    )

    iof_pass = (
        execution_control
        == f"{direction}_control"

        and post_iof.get(
            "confirmed"
        ) is True

        and post_iof.get(
            "still_holding"
        ) is True
    )

    # ==================================================
    # SMT
    # ==================================================

    smt = detect_smt(
        timeframe="M15",
        count=execution_count,
        pivot_window=pivot_window,
    )

    smt_detail = (
        smt.get(
            "bullish_smt",
            {},
        )
        if direction == "bullish"
        else smt.get(
            "bearish_smt",
            {},
        )
    )

    smt_pass = (
        smt_detail.get(
            "detected"
        ) is True
    )

    # ==================================================
    # LTF CONTINUATION
    # ==================================================

    ltf = evaluate_ltf_continuation(
        symbol="NASDAQ",
        ltf_timeframe="M5",
        csd_timeframe="M15",
        count=execution_count,
        pivot_window=pivot_window,
        confirmation_bars=
            confirmation_bars,
    )

    ltf_pass = (
        ltf.get(
            "ltf_gate_passed"
        ) is True

        and ltf.get(
            "status"
        ) == "VALID_CONTINUATION"

        and ltf.get(
            "direction"
        ) == direction

        and ltf.get(
            "protected_swing_intact"
        ) is True
    )

    # ==================================================
    # PRE-ENTRY MASTER GATE
    # ==================================================

    core_pre_entry = (
        htf_gate["passed"]
        and liquidity_pass
        and smt_pass
        and csd_pass
        and iof_pass
    )

    # Unicorn remains the precision fallback and still
    # requires the existing LTF continuation gate.
    unicorn_pre_entry = (
        core_pre_entry
        and ltf_pass
    )

    # ==================================================
    # POSITIONAL ENTRY FIRST, UNICORN AS FALLBACK
    # ==================================================

    positional_result = {
        "status":
            "BLOCKED_BY_PRE_ENTRY",

        "entry_model":
            "POSITIONAL",

        "entry_gate_passed":
            False,

        "fallback_to_unicorn":
            True,

        "execution_allowed":
            False,
    }

    if core_pre_entry:

        # H1 -> M5 is the active positional pair for
        # the current NASDAQ master setup.  The engine
        # itself supports the complete Trading Sand
        # fractal map (W1->H4, D1->H1, H4->M15,
        # H1->M5, M30->M3, M15->M1).
        positional_result = (
            evaluate_positional_entry(
                symbol="NASDAQ",
                direction=direction,
                htf_timeframe="H1",
                count=execution_count,
                pivot_window=
                    pivot_window,
                confirmation_bars=
                    confirmation_bars,
            )
        )

    positional_pass = (
        positional_result.get(
            "entry_gate_passed"
        ) is True
    )

    positional_consumed = (
        positional_result.get(
            "status"
        )
        in {
            "POSITIONAL_TARGET_HIT",
            "POSITIONAL_STOPPED",
            "POSITIONAL_OUTCOME_AMBIGUOUS",
        }
    )

    positional_fallback = (
        positional_result.get(
            "fallback_to_unicorn",
            True,
        ) is True
    )

    entry_result = positional_result
    selected_entry_model = "POSITIONAL"

    if (
        core_pre_entry
        and not positional_pass
        and not positional_consumed
        and positional_fallback
        and unicorn_pre_entry
    ):

        entry_result = (
            evaluate_unicorn_entry(
                symbol="NASDAQ",
                direction=direction,
                count=execution_count,
                pivot_window=
                    pivot_window,
                confirmation_bars=
                    confirmation_bars,
            )
        )

        selected_entry_model = "UNICORN_HOUSING"

    entry_pass = (
        entry_result.get(
            "entry_gate_passed"
        ) is True
    )

    entry_invalidated = (
        entry_result.get(
            "entry_model_invalidated"
        ) is True

        or entry_result.get(
            "status"
        ) == "INVALIDATED"
    )

    if not core_pre_entry:
        entry_gate_status = "BLOCKED"

    elif entry_pass:
        entry_gate_status = "PASS"

    elif positional_consumed:
        entry_gate_status = "COMPLETE"

    elif entry_invalidated:
        entry_gate_status = "FAIL"

    else:
        entry_gate_status = "WAIT"

    # ==================================================
    # FINAL GATES
    # ==================================================

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
            "BYPASSED_POSITIONAL"
            if positional_pass
            else (
                "PASS"
                if ltf_pass
                else "FAIL"
            )
        ),

        "POSITIONAL": (
            "PASS"
            if positional_pass
            else (
                "COMPLETE"
                if positional_consumed
                else (
                    "WAIT"
                    if core_pre_entry
                    else "BLOCKED"
                )
            )
        ),

        "ENTRY":
            entry_gate_status,
    }

    failed_gates = [
        gate
        for gate in (
            "HTF",
            "LIQUIDITY",
            "SMT",
            "CSD",
            "IOF",
            "LTF",
            "POSITIONAL",
            "ENTRY",
        )
        if gates[gate] == "FAIL"
    ]

    waiting_gates = [
        gate
        for gate in (
            "HTF",
            "LIQUIDITY",
            "SMT",
            "CSD",
            "IOF",
            "LTF",
            "POSITIONAL",
            "ENTRY",
        )
        if gates[gate]
        in {
            "WAIT",
            "PARTIAL",
        }
    ]

    if (
        not core_pre_entry
        or positional_result.get(
            "status"
        ) == "POSITIONAL_STOPPED"
    ):

        setup_status = "BLOCKED"

    elif positional_result.get(
        "status"
    ) == "POSITIONAL_TARGET_HIT":

        setup_status = (
            "POSITIONAL_MODEL_COMPLETED"
        )

    elif entry_invalidated:

        setup_status = "BLOCKED"

    elif entry_pass:

        setup_status = (
            "ENTRY_MODEL_CONFIRMED"
        )

    else:

        setup_status = (
            "WAITING_FOR_ENTRY_MODEL"
        )

    return {
        "instrument":
            "NASDAQ",

        "correlated_market":
            "US500",

        "direction":
            direction,

        "setup_status":
            setup_status,

        # Model confirmation and broker
        # order execution remain separate.
        "entry_model_confirmed":
            (
                entry_pass
                or positional_consumed
            ),

        "entry_signal_active":
            entry_pass,

        "selected_entry_model":
            selected_entry_model,

        # Still FALSE:
        # no automatic order placement.
        "entry_allowed":
            False,

        "pre_entry_gates_passed":
            (
                core_pre_entry
                and (
                    positional_pass
                    or ltf_pass
                    or positional_consumed
                )
            ),

        "failed_gates":
            failed_gates,

        "waiting_gates":
            waiting_gates,

        "gates":
            gates,

        "HTF_detail":
            htf_gate,

        "liquidity_detail": {
            "expected":
                expected_liquidity,

            "passed":
                liquidity_pass,

            "event":
                active_csd.get(
                    "liquidity"
                ),

            "raid_time":
                active_csd.get(
                    "raid_time"
                ),
        },

        "SMT_detail":
            smt_detail,

        "CSD_detail": {
            "passed":
                csd_pass,

            "direction":
                active_csd.get(
                    "direction"
                ),

            "threshold":
                active_csd.get(
                    "csd_threshold"
                ),

            "confirmation_time":
                active_csd.get(
                    "confirmation_time"
                ),
        },

        "IOF_detail": {
            "passed":
                iof_pass,

            "control":
                execution_control,

            "source_time":
                post_iof.get(
                    "source_time"
                ),

            "confirmation_time":
                post_iof.get(
                    "confirmation_time"
                ),

            "still_holding":
                post_iof.get(
                    "still_holding"
                ),
        },

        "POSITIONAL_detail":
            positional_result,

        "LTF_detail": {
            "passed":
                ltf_pass,

            "status":
                ltf.get(
                    "status"
                ),

            "protected_swing_intact":
                ltf.get(
                    "protected_swing_intact"
                ),

            "reason":
                ltf.get(
                    "reason"
                ),
        },

        "ENTRY_detail":
            entry_result,

        "hierarchy": [
            "HTF context",
            "Liquidity raid",
            "SMT",
            "First CSD",
            "Post-first-CSD IOF",
            "Positional check: HTF close + mapped LTF CSD + EQ-valid protected swing",
            "C3/C4 open -> protected-swing stop -> HTF STD -2",
            "OR LTF continuation",
            "Breaker + FVG Unicorn",
            "Negated internal FVG",
            "Housing Candle",
            "IFVG body close",
            "IFVG/Housing retest",
            "Second CSD",
            "New IOF",
            "Entry model confirmed",
        ],

        "safety": {
            "execution_enabled":
                False,

            "order_placement":
                False,

            "exact_order_price_defined":
                entry_result.get(
                    "exact_order_price_defined",
                    False,
                ),

            "reason": (
                "Positional and Unicorn/Housing "
                "entry models can now be validated "
                "deterministically, but automatic "
                "broker execution remains disabled."
            ),
        },
    }
