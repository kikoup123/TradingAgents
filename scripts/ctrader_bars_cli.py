import argparse
import json
import os
import time
from datetime import datetime, timezone

from dotenv import load_dotenv
from twisted.internet import reactor

from ctrader_open_api import Client, Protobuf, TcpProtocol, EndPoints
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAApplicationAuthReq,
    ProtoOAApplicationAuthRes,
    ProtoOAAccountAuthReq,
    ProtoOAAccountAuthRes,
    ProtoOASymbolByIdReq,
    ProtoOASymbolByIdRes,
    ProtoOAGetTrendbarsReq,
    ProtoOAGetTrendbarsRes,
)
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
    ProtoOATrendbarPeriod,
)

load_dotenv(".env")

SYMBOL_ENV = {
    "XAUUSD": "CTRADER_SYMBOL_XAUUSD",
    "GOLD": "CTRADER_SYMBOL_XAUUSD",
    "NASDAQ": "CTRADER_SYMBOL_NASDAQ",
    "USTECH100": "CTRADER_SYMBOL_NASDAQ",
    "US TECH 100": "CTRADER_SYMBOL_NASDAQ",
    "US500": "CTRADER_SYMBOL_US500",
    "US 500": "CTRADER_SYMBOL_US500",
}

PERIOD_MINUTES = {
    "M1": 1,
    "M3": 3,
    "M5": 5,
    "M15": 15,
    "M30": 30,
    "H1": 60,
    "H4": 240,
    "D1": 1440,
    "W1": 10080,
}

# cTrader applies timeframe-dependent limits to the distance between
# fromTimestamp and toTimestamp for historical trendbar requests. Keep each
# worker request deliberately conservative and let the higher-level history
# loader page backward repeatedly.
MAX_REQUEST_SPAN_MINUTES = {
    "M1": 7 * 24 * 60,
    "M3": 14 * 24 * 60,
    "M5": 30 * 24 * 60,
    "M15": 60 * 24 * 60,
    "M30": 60 * 24 * 60,
    "H1": 30 * 24 * 60,
    "H4": 120 * 24 * 60,
    "D1": 365 * 24 * 60,
    "W1": 5 * 365 * 24 * 60,
}

parser = argparse.ArgumentParser()
parser.add_argument("symbol")
parser.add_argument("timeframe")
parser.add_argument("count", type=int)
parser.add_argument(
    "--to",
    dest="to_time",
    default=None,
    help="Optional UTC ISO-8601 end time for historical paging.",
)
args = parser.parse_args()

symbol_name = args.symbol.strip().upper()
timeframe = args.timeframe.strip().upper()
count = args.count

if symbol_name not in SYMBOL_ENV:
    raise SystemExit(f"Unsupported symbol: {symbol_name}")

if timeframe not in PERIOD_MINUTES:
    raise SystemExit(f"Unsupported timeframe: {timeframe}")

if count < 1 or count > 2000:
    raise SystemExit("Count must be between 1 and 2000")

CLIENT_ID = os.getenv("CTRADER_CLIENT_ID")
CLIENT_SECRET = os.getenv("CTRADER_CLIENT_SECRET")
ACCESS_TOKEN = os.getenv("CTRADER_ACCESS_TOKEN")
ACCOUNT_ID = int(os.getenv("CTRADER_ACCOUNT_ID"))

symbol_env_key = SYMBOL_ENV[symbol_name]
SYMBOL_ID = int(os.getenv(symbol_env_key))

result = None
symbol_digits = 2

client = Client(
    EndPoints.PROTOBUF_DEMO_HOST,
    EndPoints.PROTOBUF_PORT,
    TcpProtocol,
)


def connected(client):
    req = ProtoOAApplicationAuthReq()
    req.clientId = CLIENT_ID
    req.clientSecret = CLIENT_SECRET
    client.send(req)


def on_message(client, message):
    global result, symbol_digits

    if message.payloadType == ProtoOAApplicationAuthRes().payloadType:
        req = ProtoOAAccountAuthReq()
        req.ctidTraderAccountId = ACCOUNT_ID
        req.accessToken = ACCESS_TOKEN
        client.send(req)

    elif message.payloadType == ProtoOAAccountAuthRes().payloadType:
        req = ProtoOASymbolByIdReq()
        req.ctidTraderAccountId = ACCOUNT_ID
        req.symbolId.append(SYMBOL_ID)
        client.send(req)

    elif message.payloadType == ProtoOASymbolByIdRes().payloadType:
        res = Protobuf.extract(message)

        if not res.symbol:
            result = {"error": "Symbol metadata not returned"}
            reactor.stop()
            return

        symbol_digits = int(res.symbol[0].digits)

        now_ms = int(time.time() * 1000)

        if args.to_time:
            end_dt = datetime.fromisoformat(
                args.to_time.replace("Z", "+00:00")
            )
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)
            end_ms = min(
                int(end_dt.timestamp() * 1000),
                now_ms,
            )
        else:
            end_ms = now_ms

        period_minutes = PERIOD_MINUTES[timeframe]

        requested_lookback_minutes = max(
            period_minutes * count * 3,
            period_minutes * count,
        )
        lookback_minutes = min(
            requested_lookback_minutes,
            MAX_REQUEST_SPAN_MINUTES[timeframe],
        )

        from_ms = end_ms - (lookback_minutes * 60 * 1000)

        req = ProtoOAGetTrendbarsReq()
        req.ctidTraderAccountId = ACCOUNT_ID
        req.symbolId = SYMBOL_ID
        req.period = ProtoOATrendbarPeriod.Value(timeframe)
        req.fromTimestamp = from_ms
        req.toTimestamp = end_ms
        req.count = count

        client.send(req)

    elif message.payloadType == ProtoOAGetTrendbarsRes().payloadType:
        res = Protobuf.extract(message)

        bars = []

        for bar in res.trendbar:
            low_raw = int(bar.low)

            open_raw = low_raw + int(bar.deltaOpen)
            high_raw = low_raw + int(bar.deltaHigh)
            close_raw = low_raw + int(bar.deltaClose)

            timestamp = int(bar.utcTimestampInMinutes) * 60

            dt = datetime.fromtimestamp(
                timestamp,
                tz=timezone.utc,
            )

            bars.append(
                {
                    "time": dt.isoformat(),
                    "open": round(open_raw / 100000.0, symbol_digits),
                    "high": round(high_raw / 100000.0, symbol_digits),
                    "low": round(low_raw / 100000.0, symbol_digits),
                    "close": round(close_raw / 100000.0, symbol_digits),
                    "volume": int(bar.volume),
                }
            )

        bars.sort(key=lambda x: x["time"])

        result = {
            "environment": "demo",
            "account_id": ACCOUNT_ID,
            "symbol": symbol_name,
            "symbol_id": SYMBOL_ID,
            "timeframe": timeframe,
            "count": len(bars),
            "requested_to": (
                datetime.fromtimestamp(
                    end_ms / 1000,
                    tz=timezone.utc,
                ).isoformat()
            ),
            "request_span_minutes": lookback_minutes,
            "bars": bars,
        }

        reactor.stop()


client.setConnectedCallback(connected)
client.setMessageReceivedCallback(on_message)

client.startService()

reactor.callLater(
    35,
    lambda: reactor.stop() if reactor.running else None,
)

reactor.run()

if result is None:
    result = {"error": "cTrader request timed out"}

print(json.dumps(result, indent=2))
