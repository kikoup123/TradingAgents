import os

from dotenv import load_dotenv, set_key
from twisted.internet import reactor

from ctrader_open_api import Client, Protobuf, TcpProtocol, EndPoints
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAApplicationAuthReq,
    ProtoOAApplicationAuthRes,
    ProtoOAGetAccountListByAccessTokenReq,
    ProtoOAGetAccountListByAccessTokenRes,
    ProtoOAAccountAuthReq,
    ProtoOAAccountAuthRes,
)


load_dotenv(".env")

CLIENT_ID = os.getenv("CTRADER_CLIENT_ID")
CLIENT_SECRET = os.getenv("CTRADER_CLIENT_SECRET")
ACCESS_TOKEN = os.getenv("CTRADER_ACCESS_TOKEN")

if not CLIENT_ID or not CLIENT_SECRET or not ACCESS_TOKEN:
    raise SystemExit("Missing cTrader credentials/tokens in .env")


client = Client(
    EndPoints.PROTOBUF_DEMO_HOST,
    EndPoints.PROTOBUF_PORT,
    TcpProtocol,
)


def connected(client):
    print("Connected to cTrader DEMO endpoint.")

    req = ProtoOAApplicationAuthReq()
    req.clientId = CLIENT_ID
    req.clientSecret = CLIENT_SECRET

    client.send(req)


def disconnected(client, reason):
    print("Disconnected:", reason)


def on_error(failure):
    print("ERROR:", failure)
    if reactor.running:
        reactor.stop()


def on_message(client, message):
    if message.payloadType == ProtoOAApplicationAuthRes().payloadType:
        print("Application authenticated.")

        req = ProtoOAGetAccountListByAccessTokenReq()
        req.accessToken = ACCESS_TOKEN
        client.send(req)

    elif message.payloadType == ProtoOAGetAccountListByAccessTokenRes().payloadType:
        response = Protobuf.extract(message)

        accounts = list(response.ctidTraderAccount)

        if not accounts:
            print("No cTrader accounts were returned for this token.")
            reactor.stop()
            return

        print()
        print("Accounts returned by cTrader:")

        for index, account in enumerate(accounts):
            print(
                f"[{index}] "
                f"ctidTraderAccountId={account.ctidTraderAccountId} "
                f"isLive={account.isLive}"
            )

        demo_accounts = [a for a in accounts if not a.isLive]

        if not demo_accounts:
            print("No DEMO account found.")
            reactor.stop()
            return

        selected = demo_accounts[0]
        account_id = int(selected.ctidTraderAccountId)

        set_key(".env", "CTRADER_ACCOUNT_ID", str(account_id))

        print()
        print("Selected first DEMO account:", account_id)

        req = ProtoOAAccountAuthReq()
        req.ctidTraderAccountId = account_id
        req.accessToken = ACCESS_TOKEN

        client.send(req)

    elif message.payloadType == ProtoOAAccountAuthRes().payloadType:
        response = Protobuf.extract(message)

        print()
        print("========================================")
        print("CTRADER DEMO ACCOUNT AUTHENTICATED")
        print("Account ID:", response.ctidTraderAccountId)
        print("Execution remains DISABLED")
        print("NO ORDERS WERE SENT")
        print("========================================")

        reactor.stop()


client.setConnectedCallback(connected)
client.setDisconnectedCallback(disconnected)
client.setMessageReceivedCallback(on_message)

client.startService()

reactor.callLater(20, lambda: reactor.stop() if reactor.running else None)
reactor.run()
