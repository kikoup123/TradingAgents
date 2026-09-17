# Londres Trading AI — iOS cTrader Read-Only Connection

## Purpose

This milestone adds an optional cTrader Open API account connection to the Londres iOS app without enabling broker execution.

The deterministic Londres strategy, signal engine, journal and paper accounts remain independent from broker execution. cTrader is used only to authenticate the user and read authorized account information.

## Hard safety boundary

The integration requests OAuth scope `accounts` only.

It does not expose any endpoint or Swift method for:

- new orders;
- pending orders;
- order modification;
- stop-loss or take-profit modification;
- position closing;
- broker-side risk changes.

The existing hardened Python cTrader connector verifies view-only permission and fails closed if the access token has trading permission.

## Architecture

```text
Londres Trading AI (SwiftUI)
        |
        | HTTPS
        v
Londres Gateway
        |
        | OAuth + cTrader Open API JSON/TLS
        v
cTrader / authorized broker accounts
```

The iOS application never contains `CTRADER_CLIENT_SECRET`.

After OAuth, the gateway encrypts the cTrader access token, refresh token, full account IDs and demo/live routing metadata into an opaque broker-session token. The iOS app stores only that opaque value in iOS Keychain. Account numbers returned to normal UI are masked.

## OAuth flow

1. The user opens **Settings → Broker Connections → cTrader**.
2. The app opens `GET /v1/brokers/ctrader/start` on the Londres gateway.
3. The gateway redirects to the cTrader authorization page with `scope=accounts` and `product=web`.
4. The user signs in to cTrader and selects the accounts the app may view.
5. cTrader redirects to the HTTPS callback registered for the Open API application.
6. The gateway exchanges the short-lived authorization code for access/refresh tokens immediately.
7. The gateway authenticates the cTrader Open API application and discovers the authorized cTrader account IDs.
8. The gateway creates an encrypted broker session and returns a one-time handoff code to the iOS custom URL scheme.
9. The iOS app exchanges the one-time handoff for the opaque broker-session token and stores it in Keychain.
10. The app can request masked account status and read-only balance/equity/margin snapshots through the gateway.

## Gateway routes

```text
GET  /v1/brokers/ctrader/availability
GET  /v1/brokers/ctrader/start
GET  /v1/brokers/ctrader/callback
POST /v1/brokers/ctrader/complete
GET  /v1/brokers/ctrader/status
POST /v1/brokers/ctrader/refresh
GET  /v1/brokers/ctrader/account?account_key=...
```

There are deliberately no broker execution routes in this milestone.

## Server configuration

After Spotware approves the cTrader Open API application, configure the gateway with:

```text
CTRADER_CLIENT_ID=<approved client id>
CTRADER_CLIENT_SECRET=<approved client secret>
CTRADER_REDIRECT_URI=https://<gateway-host>/v1/brokers/ctrader/callback
CTRADER_SESSION_SECRET=<at least 32 random characters>
CTRADER_IOS_CALLBACK_SCHEME=londrestradingai
```

Generate `CTRADER_SESSION_SECRET` outside source control. For example:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Never commit real credential values.

The exact `CTRADER_REDIRECT_URI` must also be added to the approved cTrader Open API application's Redirect URIs list.

## iOS configuration

The iOS app reads the gateway URL from:

```text
LONDRES_BROKER_GATEWAY_BASE_URL
```

If that value is not present, it falls back to `LONDRES_MARKET_DATA_BASE_URL`, allowing market data and cTrader OAuth to use the same deployed Londres gateway.

The registered iOS callback scheme is:

```text
londrestradingai://ctrader/complete
```

## Account data exposed to UI

Normal UI receives only:

- broker name when cTrader supplies one;
- masked account number;
- read-only connection state;
- balance;
- equity;
- used margin;
- free margin;
- deposit currency.

Normal UI does not receive:

- `client_secret`;
- raw cTrader access token;
- raw refresh token;
- full `ctidTraderAccountId`;
- full trader login;
- demo/live routing classification;
- cTrader proxy hostname.

## Current approval dependency

The code can be built and tested before Spotware approval. Real cTrader OAuth cannot complete until the Open API application is approved and its production redirect URI and server credentials are configured.

Until those values exist, the broker screen reports cTrader as not configured and the rest of the Londres iOS app continues to operate normally.
