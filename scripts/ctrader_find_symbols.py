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
    print("Connected to cTrader DEMO.")

    req = ProtoOAApplicationAuthReq()
    req.clientId = CLIENT_ID
    req.clientSecret = CLIENT_SECRET
    client.send(req)


def on_message(client, message):

    if message.payloadType == ProtoOAApplicationAuthRes().payloadType:
        print("Application authenticated.")

        req = ProtoOAAccountAuthReq()
        req.ctidTraderAccountId = ACCOUNT_ID
        req.accessToken = ACCESS_TOKEN
        client.send(req)

    elif message.payloadType == ProtoOAAccountAuthRes().payloadType:
        print("FP Markets demo account authenticated:", ACCOUNT_ID)

        req = ProtoOASymbolsListReq()
        req.ctidTraderAccountId = ACCOUNT_ID
        req.includeArchivedSymbols = False
        client.send(req)

    elif message.payloadType == ProtoOASymbolsListRes().payloadType:
        res = Protobuf.extract(message)

        keywords = (
            "xau",
            "gold",
            "nas",
            "us100",
            "ustec",
            "ndx",
            "us500",
            "sp500",
            "spx",
            "s&p",
        )

        print()
        print("MATCHING FP MARKETS SYMBOLS")
        print("=" * 90)

        matches = []

        for symbol in res.symbol:
            name = getattr(symbol, "symbolName", "") or ""
            description = getattr(symbol, "description", "") or ""

            searchable = f"{name} {description}".lower()

            if any(word in searchable for word in keywords):
                matches.append(symbol)

                print(
                    f"Symbol ID: {symbol.symbolId} | "
                    f"Name: {name} | "
                    f"Description: {description}"
                )

        print("=" * 90)
        print("Matches found:", len(matches))
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
