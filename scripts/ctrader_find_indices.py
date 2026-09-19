import os

from dotenv import load_dotenv
from twisted.internet import reactor

from ctrader_open_api import Client, Protobuf, TcpProtocol, EndPoints
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAApplicationAuthReq,
    ProtoOAApplicationAuthRes,
    ProtoOAAccountAuthReq,
    ProtoOAAccountAuthRes,
    ProtoOASymbolsListReq,
    ProtoOASymbolsListRes,
)

load_dotenv(".env")

CLIENT_ID = os.getenv("CTRADER_CLIENT_ID")
CLIENT_SECRET = os.getenv("CTRADER_CLIENT_SECRET")
ACCESS_TOKEN = os.getenv("CTRADER_ACCESS_TOKEN")
ACCOUNT_ID = int(os.getenv("CTRADER_ACCOUNT_ID"))

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

    if message.payloadType == ProtoOAApplicationAuthRes().payloadType:

        req = ProtoOAAccountAuthReq()
        req.ctidTraderAccountId = ACCOUNT_ID
        req.accessToken = ACCESS_TOKEN
        client.send(req)

    elif message.payloadType == ProtoOAAccountAuthRes().payloadType:

        req = ProtoOASymbolsListReq()
        req.ctidTraderAccountId = ACCOUNT_ID
        req.includeArchivedSymbols = False
        client.send(req)

    elif message.payloadType == ProtoOASymbolsListRes().payloadType:

        res = Protobuf.extract(message)

        terms = (
            "NAS",
            "USTEC",
            "US100",
            "NASDAQ",
            "NDX",
            "US500",
            "SP500",
            "SPX",
            "S&P",
        )

        print()
        print("FP MARKETS INDEX SYMBOLS")
        print("=" * 80)

        for symbol in res.symbol:
            name = getattr(symbol, "symbolName", "") or ""

            upper_name = name.upper()

            if any(term in upper_name for term in terms):
                print(
                    f"Symbol ID: {symbol.symbolId} | "
                    f"Name: {name}"
                )

        print("=" * 80)
        print("XAUUSD already confirmed: Symbol ID 41")
        print("Execution remains DISABLED.")
        print("NO ORDERS WERE SENT.")

        reactor.stop()

client.setConnectedCallback(connected)
client.setMessageReceivedCallback(on_message)

client.startService()

reactor.callLater(
    30,
    lambda: reactor.stop() if reactor.running else None
)

reactor.run()
