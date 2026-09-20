"""CLI for Trading Sand positional-entry historical signal validation.

Example:
    python scripts/ctrader_positional_validation.py \
        --symbol NASDAQ --htf H1 --htf-count 160 --ltf-count 2000 \
        --csv positional_validation.csv
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from tradingagents.dataflows.ctrader_positional_validation import (
    validate_positional_history,
    validate_positional_history_days,
)


def _write_csv(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not records:
        path.write_text("", encoding="utf-8")
        return

    fieldnames = list(records[0].keys())
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            row = dict(record)
            if isinstance(row.get("trail_stop_candidate"), dict):
                row["trail_stop_candidate"] = json.dumps(
                    row["trail_stop_candidate"],
                    sort_keys=True,
                )
            writer.writerow(row)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Validate Trading Sand positional entries against recent cTrader "
            "history. This is signal validation only; execution remains disabled."
        )
    )
    parser.add_argument("--symbol", default="NASDAQ")
    parser.add_argument("--htf", default="H1")
    parser.add_argument("--htf-count", type=int, default=160)
    parser.add_argument("--ltf-count", type=int, default=2000)
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help=(
            "Paginated calendar-day lookback. When supplied, this replaces "
            "--htf-count/--ltf-count and can exceed the 2,000-bar limit."
        ),
    )
    parser.add_argument(
        "--end",
        default=None,
        help="Optional UTC ISO-8601 end time for --days mode.",
    )
    parser.add_argument(
        "--warmup-days",
        type=int,
        default=None,
        help="Optional warmup days before the validation window.",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=2000,
        help="Bars per cTrader page in --days mode (maximum 2000).",
    )
    parser.add_argument("--pivot-window", type=int, default=2)
    parser.add_argument("--confirmation-bars", type=int, default=10)
    parser.add_argument("--horizon", type=int, default=1)
    parser.add_argument("--min-tick", type=float, default=1e-12)
    parser.add_argument(
        "--csv",
        type=Path,
        default=None,
        help="Optional CSV path for one row per historical fractal candidate.",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Print coverage and summary without the full records array.",
    )
    args = parser.parse_args()

    if args.days is not None:
        result = validate_positional_history_days(
            symbol=args.symbol,
            htf_timeframe=args.htf,
            days=args.days,
            end_time=args.end,
            warmup_days=args.warmup_days,
            page_size=args.page_size,
            pivot_window=args.pivot_window,
            confirmation_bars=args.confirmation_bars,
            min_tick=args.min_tick,
            outcome_horizon_htf_bars=args.horizon,
        )
    else:
        result = validate_positional_history(
            symbol=args.symbol,
            htf_timeframe=args.htf,
            htf_count=args.htf_count,
            ltf_count=args.ltf_count,
            pivot_window=args.pivot_window,
            confirmation_bars=args.confirmation_bars,
            min_tick=args.min_tick,
            outcome_horizon_htf_bars=args.horizon,
        )

    if args.csv is not None:
        _write_csv(args.csv, result["records"])

    payload = (
        {
            "symbol": result["symbol"],
            "htf_timeframe": result["htf_timeframe"],
            "ltf_timeframe": result["ltf_timeframe"],
            "coverage": result["coverage"],
            "summary": result["summary"],
            "historical_fetch": result.get("historical_fetch"),
            "execution_allowed": result["execution_allowed"],
            "csv": str(args.csv) if args.csv is not None else None,
        }
        if args.summary_only
        else result
    )
    print(json.dumps(payload, indent=2, default=str))


if __name__ == "__main__":
    main()
