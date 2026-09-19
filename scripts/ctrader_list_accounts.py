import os

from dotenv import load_dotenv
from twisted.internet import reactor

from ctrader_open_api import Client, Protobuf, TcpProtocol, EndPoints
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAApplicationAuthReq,
    ProtoOAApplicationAuthRes,
    ProtoOAGetAccountListByAccessTokenReq,
    ProtoOAGetAccountListByAccessTokenRes,
)

load_dotenv(".env")

CLIENT_ID = os.getenv("CTRADER_CLIENT_ID")
CLIENT_SECRET = os.getenv("CTRADER_CLIENT_SECRET")
ACCESS_TOKEN = os.getenv("CTRADER_ACCESS_TOKEN")

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
        print("Application authenticated.")

        req = ProtoOAGetAccountListByAccessTokenReq()
        req.accessToken = ACCESS_TOKEN
        client.send(req)

    elif message.payloadType == ProtoOAGetAccountListByAccessTokenRes().payloadType:
        res = Protobuf.extract(message)

        print()
        print("CTRADER ACCOUNTS")
        print("=" * 80)

        for i, account in enumerate(res.ctidTraderAccount):
            environment = "LIVE" if account.isLive else "DEMO"

            broker = getattr(account, "brokerTitleShort", "") or "Unknown broker"
            login = getattr(account, "traderLogin", 0) or "Unknown"

            print(
                f"[{i}] {environment} | "
                f"Broker: {broker} | "
                f"Login: {login} | "
                f"Account ID: {account.ctidTraderAccountId}"
            )

        print("=" * 80)
        reactor.stop()

client.setConnectedCallback(connected)
client.setMessageReceivedCallback(on_message)

client.startService()

reactor.callLater(
    20,
    lambda: reactor.stop() if reactor.running else None
)

reactor.run()
