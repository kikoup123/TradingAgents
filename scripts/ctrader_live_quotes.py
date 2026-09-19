import os

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
    ProtoOASubscribeSpotsReq,
    ProtoOASubscribeSpotsRes,
    ProtoOASpotEvent,
)

load_dotenv(".env")

CLIENT_ID = os.getenv("CTRADER_CLIENT_ID")
CLIENT_SECRET = os.getenv("CTRADER_CLIENT_SECRET")
ACCESS_TOKEN = os.getenv("CTRADER_ACCESS_TOKEN")
ACCOUNT_ID = int(os.getenv("CTRADER_ACCOUNT_ID"))

SYMBOLS = {
    int(os.getenv("CTRADER_SYMBOL_XAUUSD")): "XAUUSD",
    int(os.getenv("CTRADER_SYMBOL_NASDAQ")): "US TECH 100",
    int(os.getenv("CTRADER_SYMBOL_US500")): "US 500",
}

digits = {}
quotes = {}
event_count = 0

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
    global event_count

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

        for symbol_id in SYMBOLS:
            req.symbolId.append(symbol_id)

        client.send(req)

    elif message.payloadType == ProtoOASymbolByIdRes().payloadType:
        res = Protobuf.extract(message)

        for symbol in res.symbol:
            digits[int(symbol.symbolId)] = int(symbol.digits)

        print()
        print("Symbol precision loaded.")

        req = ProtoOASubscribeSpotsReq()
        req.ctidTraderAccountId = ACCOUNT_ID
        req.subscribeToSpotTimestamp = True

        for symbol_id in SYMBOLS:
            req.symbolId.append(symbol_id)

        client.send(req)

    elif message.payloadType == ProtoOASubscribeSpotsRes().payloadType:
        print("Subscribed to live quotes.")
        print("Waiting for XAUUSD, US TECH 100 and US 500...")
        print()

    elif message.payloadType == ProtoOASpotEvent().payloadType:
        spot = Protobuf.extract(message)

        symbol_id = int(spot.symbolId)

        if symbol_id not in SYMBOLS:
            return

        if symbol_id not in quotes:
            quotes[symbol_id] = {
                "bid": None,
                "ask": None,
            }

        symbol_digits = digits.get(symbol_id, 5)

        if spot.HasField("bid"):
            quotes[symbol_id]["bid"] = round(
                spot.bid / 100000.0,
                symbol_digits,
            )

        if spot.HasField("ask"):
            quotes[symbol_id]["ask"] = round(
                spot.ask / 100000.0,
                symbol_digits,
            )

        bid = quotes[symbol_id]["bid"]
        ask = quotes[symbol_id]["ask"]

        print(
            f"{SYMBOLS[symbol_id]:12} | "
            f"BID: {bid} | "
            f"ASK: {ask}"
        )

        event_count += 1

        if event_count >= 20:
            print()
            print("Market-data test complete.")
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
