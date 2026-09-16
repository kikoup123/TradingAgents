#!/usr/bin/env python3
"""Local Vantage MetaTrader 5 execution host for Londres Phase 33.

Run this only on the machine/session where the intended Vantage MT5 account is
already logged in. The bridge reads immutable command files from an inbox, uses
a local SQLite journal to prevent duplicate submission across restarts, and
publishes sanitized receipts. Demo/live classification, server and full login
remain local and never appear in response files.

Execution is OFF unless ``--enable-execution`` is supplied explicitly.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping


ACKNOWLEDGED = "ACKNOWLEDGED"
REJECTED = "REJECTED"
AMBIGUOUS = "AMBIGUOUS"
NOT_FOUND = "NOT_FOUND"
RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _account_key(server: str, login: Any) -> str:
    return hashlib.sha256(f"{server}:{login}".encode()).hexdigest()


def _public_alias(account_key: str) -> str:
    digest = hashlib.sha256(account_key.encode()).hexdigest()[:8].upper()
    return f"MT5-{digest}"


def _magic(command_id: str) -> int:
    digest = hashlib.sha256(command_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") & 0x7FFFFFFF


def _normalized_provider(value: Any) -> str:
    return "".join(character for character in str(value or "").upper() if character.isalnum())


def _same_price(left: Any, right: Any) -> bool:
    try:
        return math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=1e-9)
    except (TypeError, ValueError):
        return False


def _exact_volume(value: Any, info: Any) -> float:
    try:
        volume = float(value)
        minimum = float(info.volume_min)
        maximum = float(info.volume_max)
        step = float(info.volume_step)
    except (TypeError, ValueError, AttributeError) as exc:
        raise RuntimeError("MT5 volume metadata is invalid") from exc
    if not all(math.isfinite(item) and item > 0 for item in (volume, minimum, maximum, step)):
        raise RuntimeError("MT5 exact volume and grid must be finite and > 0")
    if volume < minimum - 1e-12 or volume > maximum + 1e-12:
        raise RuntimeError("MT5 exact volume is outside current broker min/max")
    steps = (volume - minimum) / step
    if not math.isclose(steps, round(steps), abs_tol=1e-9):
        raise RuntimeError("MT5 exact volume no longer aligns to broker step; resizing forbidden")
    return volume


class LocalExecutionJournal:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS local_commands (
                    command_id TEXT PRIMARY KEY,
                    request_sha256 TEXT NOT NULL,
                    state TEXT NOT NULL,
                    receipt_json TEXT,
                    receipt_sha256 TEXT,
                    updated_at_ms INTEGER NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=5.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def begin_submit(self, *, command_id: str, request_sha: str, now_ms: int) -> str:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM local_commands WHERE command_id = ?",
                (command_id,),
            ).fetchone()
            if row is None:
                connection.execute(
                    """
                    INSERT INTO local_commands (
                        command_id, request_sha256, state,
                        receipt_json, receipt_sha256, updated_at_ms
                    ) VALUES (?, ?, 'SUBMITTING', NULL, NULL, ?)
                    """,
                    (command_id, request_sha, now_ms),
                )
                connection.commit()
                return "SUBMIT"
            if str(row["request_sha256"]) != request_sha:
                connection.rollback()
                return "CONFLICT"
            state = str(row["state"])
            connection.commit()
            if state == "SUBMITTING":
                return "RECONCILE"
            return "RETURN_STORED"
        finally:
            connection.close()

    def store(self, *, command_id: str, state: str, receipt: dict[str, Any], now_ms: int) -> None:
        raw = _canonical_json(receipt)
        digest = _sha256_text(raw)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE local_commands
                SET state = ?, receipt_json = ?, receipt_sha256 = ?, updated_at_ms = ?
                WHERE command_id = ?
                """,
                (state, raw, digest, now_ms, command_id),
            )
            connection.commit()

    def stored(self, command_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM local_commands WHERE command_id = ?",
                (command_id,),
            ).fetchone()
        if row is None or row["receipt_json"] is None:
            return None
        raw = str(row["receipt_json"])
        if _sha256_text(raw) != str(row["receipt_sha256"]):
            return None
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None


class MT5ExecutionHost:
    def __init__(self, mt5: Any, root: Path, *, execution_enabled: bool) -> None:
        self.mt5 = mt5
        self.root = root
        self.requests = root / "requests"
        self.responses = root / "responses"
        self.requests.mkdir(parents=True, exist_ok=True)
        self.responses.mkdir(parents=True, exist_ok=True)
        self.execution_enabled = execution_enabled
        self.journal = LocalExecutionJournal(root / "bridge_execution.sqlite")

    def process_once(self) -> int:
        count = 0
        for request_path in sorted(self.requests.glob("*.json")):
            response_path = self.responses / request_path.name
            if response_path.exists():
                continue
            try:
                envelope = self._read_request(request_path)
                receipt = self._handle(envelope)
            except Exception as exc:
                command_id, operation = self._identity_from_filename(request_path.name)
                receipt = {
                    "outcome": RECONCILIATION_REQUIRED,
                    "provider_code": "MT5_BRIDGE_EXCEPTION",
                    "provider_message": type(exc).__name__,
                    "definite_no_fill": False,
                }
                envelope = {
                    "command_id": command_id,
                    "operation": operation,
                }
            self._write_response(response_path, envelope=envelope, receipt=receipt)
            count += 1
        return count

    def _read_request(self, path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("MT5 execution request is unreadable") from exc
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise RuntimeError("Unsupported MT5 execution request schema")
        operation = str(payload.get("operation") or "")
        if operation not in {"SUBMIT_MARKET", "RECONCILE"}:
            raise RuntimeError("Unsupported MT5 execution operation")
        command = payload.get("command")
        if not isinstance(command, dict):
            raise RuntimeError("MT5 execution command is missing")
        if payload.get("command_id") != command.get("command_id"):
            raise RuntimeError("MT5 execution request command identity mismatch")
        return payload

    def _handle(self, envelope: dict[str, Any]) -> dict[str, Any]:
        command = envelope["command"]
        operation = str(envelope["operation"])
        self._validate_account(command)
        if operation == "RECONCILE":
            return self._reconcile(command)
        if not self.execution_enabled:
            return {
                "outcome": REJECTED,
                "provider_code": "MT5_LOCAL_EXECUTION_NOT_ENABLED",
                "provider_message": "Start bridge with --enable-execution",
                "definite_no_fill": True,
            }

        command_id = str(command["command_id"])
        request_sha = _sha256_text(_canonical_json(command))
        action = self.journal.begin_submit(
            command_id=command_id,
            request_sha=request_sha,
            now_ms=_now_ms(),
        )
        if action == "CONFLICT":
            return {
                "outcome": RECONCILIATION_REQUIRED,
                "provider_code": "MT5_LOCAL_COMMAND_ID_PAYLOAD_CONFLICT",
                "definite_no_fill": False,
            }
        if action == "RETURN_STORED":
            stored = self.journal.stored(command_id)
            if stored is not None:
                return stored
            return {
                "outcome": RECONCILIATION_REQUIRED,
                "provider_code": "MT5_LOCAL_STORED_RECEIPT_INTEGRITY_FAILURE",
                "definite_no_fill": False,
            }
        if action == "RECONCILE":
            receipt = self._reconcile(command)
            self.journal.store(
                command_id=command_id,
                state="RECONCILED",
                receipt=receipt,
                now_ms=_now_ms(),
            )
            return receipt

        receipt = self._submit_exact_market(command)
        self.journal.store(
            command_id=command_id,
            state=str(receipt["outcome"]),
            receipt=receipt,
            now_ms=_now_ms(),
        )
        return receipt

    def _validate_account(self, command: Mapping[str, Any]) -> None:
        account = self.mt5.account_info()
        terminal = self.mt5.terminal_info()
        if account is None or terminal is None:
            raise RuntimeError("MT5 account/terminal unavailable")
        data = account._asdict()
        company = str(data.get("company") or "")
        server = str(data.get("server") or "")
        login = data.get("login")
        if "VANTAGE" not in _normalized_provider(company):
            raise RuntimeError("Phase 33 MT5 execution host requires Vantage provider identity")
        if not server or login is None:
            raise RuntimeError("MT5 private route metadata unavailable")
        raw_mode = int(data.get("trade_mode", -1))
        allowed = {
            int(getattr(self.mt5, "ACCOUNT_TRADE_MODE_DEMO", 0)),
            int(getattr(self.mt5, "ACCOUNT_TRADE_MODE_REAL", 2)),
        }
        if raw_mode not in allowed:
            raise RuntimeError("MT5 execution host accepts demo or live brokerage accounts only")
        alias = _public_alias(_account_key(server, login))
        if alias != str(command.get("account_alias") or ""):
            raise RuntimeError("MT5 command is bound to a different private account route")
        if str(command.get("venue") or "") != "VANTAGE_MT5":
            raise RuntimeError("MT5 command venue mismatch")
        if str(command.get("broker_type") or "") != "MT5":
            raise RuntimeError("MT5 command broker type mismatch")
        if str(command.get("execution_style") or "") != "MARKET_ON_SIGNAL":
            raise RuntimeError("MT5 execution host supports MARKET_ON_SIGNAL only")

    def _submit_exact_market(self, command: Mapping[str, Any]) -> dict[str, Any]:
        symbol_name = str(command["broker_symbol"])
        if not self.mt5.symbol_select(symbol_name, True):
            return self._reject("MT5_SYMBOL_SELECT_FAILED")
        info = self.mt5.symbol_info(symbol_name)
        tick = self.mt5.symbol_info_tick(symbol_name)
        if info is None or tick is None:
            return self._reject("MT5_SYMBOL_OR_TICK_UNAVAILABLE")
        try:
            volume = _exact_volume(command["exact_volume"], info)
            filling = self._fill_policy(info)
        except RuntimeError as exc:
            return self._reject("MT5_CURRENT_VOLUME_OR_FILL_POLICY_INVALID", str(exc))

        bullish = command["direction"] == "BULLISH"
        order_type = self.mt5.ORDER_TYPE_BUY if bullish else self.mt5.ORDER_TYPE_SELL
        market_execution = int(info.trade_exemode) == int(self.mt5.SYMBOL_TRADE_EXECUTION_MARKET)
        request = {
            "action": self.mt5.TRADE_ACTION_DEAL,
            "magic": _magic(str(command["command_id"])),
            "symbol": symbol_name,
            "volume": volume,
            "type": order_type,
            "sl": float(command["stop_price"]),
            "tp": float(command["target_price"]),
            "deviation": 0,
            "type_time": self.mt5.ORDER_TIME_GTC,
            "type_filling": filling,
            "comment": f"L33-{str(command['command_id'])[:20]}",
        }
        if not market_execution:
            request["price"] = float(tick.ask if bullish else tick.bid)

        check = self.mt5.order_check(request)
        if check is None:
            return self._reject("MT5_ORDER_CHECK_UNAVAILABLE")
        if int(getattr(check, "retcode", -1)) != 0:
            return self._reject(
                f"MT5_ORDER_CHECK_{getattr(check, 'retcode', 'UNKNOWN')}",
                str(getattr(check, "comment", "")),
            )

        result = self.mt5.order_send(request)
        if result is None:
            return {
                "outcome": AMBIGUOUS,
                "provider_code": "MT5_ORDER_SEND_RETURNED_NONE",
                "provider_message": str(self.mt5.last_error()),
                "definite_no_fill": False,
            }
        code = int(result.retcode)
        if code == int(self.mt5.TRADE_RETCODE_DONE):
            reconciled = self._reconcile(command)
            if reconciled["outcome"] == ACKNOWLEDGED:
                reconciled["provider_code"] = "MT5_TRADE_RETCODE_DONE_RECONCILED"
                return reconciled
            return {
                **reconciled,
                "outcome": RECONCILIATION_REQUIRED,
                "provider_code": "MT5_DONE_BUT_EXACT_PROTECTED_POSITION_NOT_YET_RECONCILED",
                "broker_order_id": str(getattr(result, "order", 0) or "") or None,
                "provider_message": str(getattr(result, "comment", "")),
                "definite_no_fill": False,
            }
        if code in {
            int(getattr(self.mt5, "TRADE_RETCODE_PLACED", 10008)),
            int(getattr(self.mt5, "TRADE_RETCODE_DONE_PARTIAL", 10010)),
            int(getattr(self.mt5, "TRADE_RETCODE_TIMEOUT", 10012)),
            int(getattr(self.mt5, "TRADE_RETCODE_CONNECTION", 10031)),
        }:
            return {
                "outcome": AMBIGUOUS,
                "broker_order_id": str(getattr(result, "order", 0) or "") or None,
                "broker_position_id": None,
                "filled_volume": float(getattr(result, "volume", 0.0) or 0.0),
                "average_fill_price": float(getattr(result, "price", 0.0) or 0.0) or None,
                "stop_protection_active": False,
                "target_protection_active": False,
                "provider_code": f"MT5_RETCODE_{code}",
                "provider_message": str(getattr(result, "comment", "")),
                "definite_no_fill": False,
            }
        return {
            "outcome": REJECTED,
            "broker_order_id": str(getattr(result, "order", 0) or "") or None,
            "broker_position_id": None,
            "filled_volume": 0.0,
            "average_fill_price": None,
            "stop_protection_active": False,
            "target_protection_active": False,
            "provider_code": f"MT5_RETCODE_{code}",
            "provider_message": str(getattr(result, "comment", "")),
            "definite_no_fill": True,
        }

    def _fill_policy(self, info: Any) -> Any:
        flags = int(info.filling_mode)
        fok_flag = int(getattr(self.mt5, "SYMBOL_FILLING_FOK", 1))
        ioc_flag = int(getattr(self.mt5, "SYMBOL_FILLING_IOC", 2))
        if flags & fok_flag:
            return self.mt5.ORDER_FILLING_FOK
        if flags & ioc_flag:
            return self.mt5.ORDER_FILLING_IOC
        if int(info.trade_exemode) != int(self.mt5.SYMBOL_TRADE_EXECUTION_MARKET):
            return self.mt5.ORDER_FILLING_RETURN
        raise RuntimeError("No explicit safe MT5 market-execution filling mode is available")

    def _reconcile(self, command: Mapping[str, Any]) -> dict[str, Any]:
        symbol = str(command["broker_symbol"])
        magic = _magic(str(command["command_id"]))
        label = f"L33-{str(command['command_id'])[:20]}"
        expected_volume = float(command["exact_volume"])
        positions = self.mt5.positions_get(symbol=symbol)
        if positions is None:
            return {
                "outcome": RECONCILIATION_REQUIRED,
                "provider_code": "MT5_POSITIONS_QUERY_FAILED",
                "provider_message": str(self.mt5.last_error()),
                "definite_no_fill": False,
            }
        for position in positions:
            row = position._asdict()
            if int(row.get("magic", -1)) != magic and str(row.get("comment") or "") != label:
                continue
            volume = float(row.get("volume") or 0.0)
            if not math.isclose(volume, expected_volume, abs_tol=1e-12):
                return {
                    "outcome": RECONCILIATION_REQUIRED,
                    "broker_position_id": str(row.get("ticket") or "") or None,
                    "filled_volume": volume,
                    "average_fill_price": float(row.get("price_open") or 0.0) or None,
                    "stop_protection_active": _same_price(row.get("sl"), command["stop_price"]),
                    "target_protection_active": _same_price(row.get("tp"), command["target_price"]),
                    "provider_code": "MT5_MATCHED_POSITION_VOLUME_DIFFERS_FROM_COMMAND",
                    "definite_no_fill": False,
                }
            stop_ok = _same_price(row.get("sl"), command["stop_price"])
            target_ok = _same_price(row.get("tp"), command["target_price"])
            return {
                "outcome": ACKNOWLEDGED if stop_ok and target_ok else RECONCILIATION_REQUIRED,
                "broker_order_id": None,
                "broker_position_id": str(row.get("ticket") or "") or None,
                "filled_volume": volume,
                "average_fill_price": float(row.get("price_open") or 0.0) or None,
                "stop_protection_active": stop_ok,
                "target_protection_active": target_ok,
                "provider_code": "MT5_EXACT_POSITION_RECONCILED",
                "definite_no_fill": False,
            }

        now = datetime.now(timezone.utc)
        start = now - timedelta(days=1)
        orders = self.mt5.history_orders_get(start, now)
        deals = self.mt5.history_deals_get(start, now)
        evidence = []
        for collection in (orders, deals):
            for item in collection or ():
                row = item._asdict()
                if int(row.get("magic", -1)) == magic or str(row.get("comment") or "") == label:
                    evidence.append(row)
        if evidence:
            return {
                "outcome": RECONCILIATION_REQUIRED,
                "provider_code": "MT5_HISTORY_MATCH_WITHOUT_CURRENT_EXACT_POSITION",
                "provider_message": f"matching_history_records={len(evidence)}",
                "definite_no_fill": False,
            }
        return {
            "outcome": NOT_FOUND,
            "provider_code": "MT5_NO_CURRENT_OR_RECENT_COMMAND_EVIDENCE",
            "definite_no_fill": False,
        }

    @staticmethod
    def _reject(code: str, message: str | None = None) -> dict[str, Any]:
        return {
            "outcome": REJECTED,
            "broker_order_id": None,
            "broker_position_id": None,
            "filled_volume": 0.0,
            "average_fill_price": None,
            "stop_protection_active": False,
            "target_protection_active": False,
            "provider_code": code,
            "provider_message": message,
            "definite_no_fill": True,
        }

    @staticmethod
    def _identity_from_filename(name: str) -> tuple[str, str]:
        stem = name[:-5] if name.endswith(".json") else name
        command_id, separator, operation = stem.partition(".")
        return command_id, operation if separator else "UNKNOWN"

    @staticmethod
    def _write_response(
        path: Path,
        *,
        envelope: Mapping[str, Any],
        receipt: dict[str, Any],
    ) -> None:
        payload = {
            "schema_version": 1,
            "command_id": envelope.get("command_id"),
            "operation": envelope.get("operation"),
            "generated_at_ms": _now_ms(),
            "account_environment": "HIDDEN_INTERNAL",
            "receipt": receipt,
        }
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(_canonical_json(payload), encoding="utf-8")
        os.replace(temporary, path)


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Londres Vantage MT5 Phase 33 execution bridge")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--poll-ms", type=int, default=100)
    parser.add_argument("--enable-execution", action="store_true")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.poll_ms <= 0:
        parser.error("--poll-ms must be > 0")

    try:
        import MetaTrader5 as mt5
    except ImportError as exc:
        raise SystemExit("MetaTrader5 Python package is required on the MT5 execution host") from exc
    if not mt5.initialize():
        raise SystemExit(f"MT5 initialize failed: {mt5.last_error()}")
    host = MT5ExecutionHost(mt5, args.root.expanduser(), execution_enabled=args.enable_execution)
    try:
        while True:
            host.process_once()
            if args.once:
                break
            time.sleep(args.poll_ms / 1000.0)
    except KeyboardInterrupt:
        return 0
    finally:
        mt5.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
