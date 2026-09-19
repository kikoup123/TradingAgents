import os

from dotenv import load_dotenv
from twisted.internet import reactor

from ctrader_open_api import Client, Protobuf, TcpProtocol, EndPoints
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAApplicationAuthReq,
    ProtoOAApplicationAuthRes,
    ProtoOAAccountAuthReq,
    ProtoOAAccountAuthRes,
    ProtoOASymbolCategoryListReq,
    ProtoOASymbolCategoryListRes,
    ProtoOASymbolsListReq,
    ProtoOASymbolsListRes,
)

load_dotenv(".env")

CLIENT_ID = os.getenv("CTRADER_CLIENT_ID")
CLIENT_SECRET = os.getenv("CTRADER_CLIENT_SECRET")
ACCESS_TOKEN = os.getenv("CTRADER_ACCESS_TOKEN")
ACCOUNT_ID = int(os.getenv("CTRADER_ACCOUNT_ID"))

categories = {}

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
    global categories

    if message.payloadType == ProtoOAApplicationAuthRes().payloadType:
        req = ProtoOAAccountAuthReq()
        req.ctidTraderAccountId = ACCOUNT_ID
        req.accessToken = ACCESS_TOKEN
        client.send(req)

    elif message.payloadType == ProtoOAAccountAuthRes().payloadType:
        print("FP Markets demo account authenticated.")

        req = ProtoOASymbolCategoryListReq()
        req.ctidTraderAccountId = ACCOUNT_ID
        client.send(req)

    elif message.payloadType == ProtoOASymbolCategoryListRes().payloadType:
        res = Protobuf.extract(message)

        print()
        print("SYMBOL CATEGORIES")
        print("=" * 80)

        for c in res.symbolCategory:
            categories[c.id] = c.name
            print(f"Category ID: {c.id} | Name: {c.name}")

        print("=" * 80)

        req = ProtoOASymbolsListReq()
        req.ctidTraderAccountId = ACCOUNT_ID
        req.includeArchivedSymbols = False
        client.send(req)

    elif message.payloadType == ProtoOASymbolsListRes().payloadType:
        res = Protobuf.extract(message)

        print()
        print("INDEX / INDICES CATEGORY SYMBOLS")
        print("=" * 100)

        found = 0

        for symbol in res.symbol:
            category_id = getattr(symbol, "symbolCategoryId", 0)
            category_name = categories.get(category_id, "")

            category_upper = category_name.upper()

            if (
                "INDEX" in category_upper
                or "INDICES" in category_upper
            ):
                found += 1

                name = getattr(symbol, "symbolName", "") or ""
                description = getattr(symbol, "description", "") or ""

                print(
                    f"ID: {symbol.symbolId} | "
                    f"Name: {name} | "
                    f"Category: {category_name} | "
                    f"Description: {description}"
                )

        print("=" * 100)
        print("Index-category symbols found:", found)
        print("XAUUSD confirmed separately: ID 41")
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
