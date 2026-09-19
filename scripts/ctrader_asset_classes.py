import os

from dotenv import load_dotenv
from twisted.internet import reactor

from ctrader_open_api import Client, Protobuf, TcpProtocol, EndPoints
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAApplicationAuthReq,
    ProtoOAApplicationAuthRes,
    ProtoOAAccountAuthReq,
    ProtoOAAccountAuthRes,
    ProtoOAAssetClassListReq,
    ProtoOAAssetClassListRes,
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

asset_classes = {}
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

    if message.payloadType == ProtoOAApplicationAuthRes().payloadType:
        req = ProtoOAAccountAuthReq()
        req.ctidTraderAccountId = ACCOUNT_ID
        req.accessToken = ACCESS_TOKEN
        client.send(req)

    elif message.payloadType == ProtoOAAccountAuthRes().payloadType:
        print("FP Markets demo account authenticated.")

        req = ProtoOAAssetClassListReq()
        req.ctidTraderAccountId = ACCOUNT_ID
        client.send(req)

    elif message.payloadType == ProtoOAAssetClassListRes().payloadType:
        res = Protobuf.extract(message)

        print()
        print("ASSET CLASSES")
        print("=" * 70)

        for item in res.assetClass:
            asset_classes[item.id] = item.name
            print(
                f"Asset Class ID: {item.id} | "
                f"Name: {item.name}"
            )

        print("=" * 70)

        req = ProtoOASymbolCategoryListReq()
        req.ctidTraderAccountId = ACCOUNT_ID
        client.send(req)

    elif message.payloadType == ProtoOASymbolCategoryListRes().payloadType:
        res = Protobuf.extract(message)

        print()
        print("CATEGORIES WITH ASSET CLASS")
        print("=" * 90)

        for item in res.symbolCategory:
            categories[item.id] = item.assetClassId

            asset_name = asset_classes.get(
                item.assetClassId,
                "Unknown asset class"
            )

            print(
                f"Category ID: {item.id} | "
                f"Category: {item.name} | "
                f"Asset Class: {asset_name} "
                f"(ID {item.assetClassId})"
            )

        print("=" * 90)

        req = ProtoOASymbolsListReq()
        req.ctidTraderAccountId = ACCOUNT_ID
        req.includeArchivedSymbols = False
        client.send(req)

    elif message.payloadType == ProtoOASymbolsListRes().payloadType:
        res = Protobuf.extract(message)

        index_asset_ids = {
            asset_id
            for asset_id, name in asset_classes.items()
            if "INDEX" in name.upper()
            or "INDICE" in name.upper()
        }

        index_category_ids = {
            category_id
            for category_id, asset_id in categories.items()
            if asset_id in index_asset_ids
        }

        print()
        print("FP MARKETS INDEX CFD SYMBOLS")
        print("=" * 100)

        found = 0

        for symbol in res.symbol:
            category_id = getattr(
                symbol,
                "symbolCategoryId",
                0,
            )

            if category_id in index_category_ids:
                found += 1

                name = getattr(
                    symbol,
                    "symbolName",
                    "",
                ) or ""

                description = getattr(
                    symbol,
                    "description",
                    "",
                ) or ""

                print(
                    f"ID: {symbol.symbolId} | "
                    f"Name: {name} | "
                    f"Description: {description}"
                )

        print("=" * 100)
        print("Index CFD symbols found:", found)
        print("XAUUSD already confirmed: ID 41")
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
