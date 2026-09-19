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

CLIENT_ID = os.getenv("CTRADER_CLIENT_ID")
CLIENT_SECRET = os.getenv("CTRADER_CLIENT_SECRET")
ACCESS_TOKEN = os.getenv("CTRADER_ACCESS_TOKEN")
ACCOUNT_ID = int(os.getenv("CTRADER_ACCOUNT_ID"))

SYMBOL_ID = int(os.getenv("CTRADER_SYMBOL_XAUUSD", "41"))
SYMBOL_NAME = "XAUUSD"

BAR_COUNT = 100
PERIOD_NAME = "H1"

symbol_digits = 2


client = Client(
    EndPoints.PROTOBUF_DEMO_HOST,
    EndPoints.PROTOBUF_PORT,
    TcpProtocol,
)


def connected(client):
    print("Connected to cTrader DEMO.")

    req = ProtoOAApplicationAuthReq()
    req.clientId = CLIENT_ID
    req.clientSecret = CLIENT_SECRET
    client.send(req)


def on_message(client, message):
    global symbol_digits

    if message.payloadType == ProtoOAApplicationAuthRes().payloadType:
        print("Application authenticated.")

        req = ProtoOAAccountAuthReq()
        req.ctidTraderAccountId = ACCOUNT_ID
        req.accessToken = ACCESS_TOKEN
        client.send(req)

    elif message.payloadType == ProtoOAAccountAuthRes().payloadType:
        print("FP Markets demo account authenticated.")

        req = ProtoOASymbolByIdReq()
        req.ctidTraderAccountId = ACCOUNT_ID
        req.symbolId.append(SYMBOL_ID)

        client.send(req)

    elif message.payloadType == ProtoOASymbolByIdRes().payloadType:
        res = Protobuf.extract(message)

        if not res.symbol:
            print("ERROR: Symbol information was not returned.")
            reactor.stop()
            return

        symbol = res.symbol[0]
        symbol_digits = int(symbol.digits)

        print(
            f"{SYMBOL_NAME} loaded | "
            f"Symbol ID: {SYMBOL_ID} | "
            f"Digits: {symbol_digits}"
        )

        now_ms = int(time.time() * 1000)

        # Wide enough window to obtain 100 H1 market bars,
        # including weekends/market closures.
        thirty_days_ago_ms = now_ms - (30 * 24 * 60 * 60 * 1000)

        req = ProtoOAGetTrendbarsReq()
        req.ctidTraderAccountId = ACCOUNT_ID
        req.symbolId = SYMBOL_ID
        req.period = ProtoOATrendbarPeriod.Value(PERIOD_NAME)
        req.fromTimestamp = thirty_days_ago_ms
        req.toTimestamp = now_ms
        req.count = BAR_COUNT

        print()
        print(
            f"Requesting last {BAR_COUNT} "
            f"{PERIOD_NAME} candles for {SYMBOL_NAME}..."
        )

        client.send(req)

    elif message.payloadType == ProtoOAGetTrendbarsRes().payloadType:
        res = Protobuf.extract(message)

        bars = []

        for bar in res.trendbar:
            low_raw = int(bar.low)

            open_raw = low_raw + int(bar.deltaOpen)
            high_raw = low_raw + int(bar.deltaHigh)
            close_raw = low_raw + int(bar.deltaClose)

            timestamp_seconds = int(bar.utcTimestampInMinutes) * 60

            dt = datetime.fromtimestamp(
                timestamp_seconds,
                tz=timezone.utc,
            )

            bars.append(
                {
                    "time": dt,
                    "open": round(open_raw / 100000.0, symbol_digits),
                    "high": round(high_raw / 100000.0, symbol_digits),
                    "low": round(low_raw / 100000.0, symbol_digits),
                    "close": round(close_raw / 100000.0, symbol_digits),
                    "volume": int(bar.volume),
                }
            )

        bars.sort(key=lambda x: x["time"])

        print()
        print("=" * 90)
        print(
            f"{SYMBOL_NAME} | {PERIOD_NAME} | "
            f"Candles received: {len(bars)}"
        )
        print("=" * 90)

        # Print the 15 most recent candles.
        for bar in bars[-15:]:
            print(
                f"{bar['time'].strftime('%Y-%m-%d %H:%M UTC')} | "
                f"O {bar['open']} | "
                f"H {bar['high']} | "
                f"L {bar['low']} | "
                f"C {bar['close']} | "
                f"Ticks {bar['volume']}"
            )

        print("=" * 90)

        if bars:
            print("Oldest returned:", bars[0]["time"])
            print("Newest returned:", bars[-1]["time"])

        print("Execution remains DISABLED.")
        print("NO ORDERS WERE SENT.")

        reactor.stop()


client.setConnectedCallback(connected)
client.setMessageReceivedCallback(on_message)

client.startService()

reactor.callLater(
    30,
    lambda: reactor.stop() if reactor.running else None,
)

reactor.run()
