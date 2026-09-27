# Londres Phase 18 — Secure cTrader / FP Markets read-only connector

Phase 18 introduces broker connectivity without enabling order submission.

## Security boundary

The connector requests cTrader OAuth scope `accounts`, not `trading`. A token that advertises trading permission is rejected by the read-only transport. Demo/live environment classification is required internally to select the correct cTrader endpoint, but it is never included in normal public connector state or AgentState.

The following values stay private and must not be sent to LLM prompts or normal logs:

- client secret
- access token
- refresh token
- full cTrader account id / trader login
- demo/live environment classification
- endpoint host selected from that classification

The public connector state exposes only a masked account identifier, broker name when supplied by cTrader, connection state, and the fact that the connector is read-only.

## Environment variables

Phase 18 expects secrets to be provided outside source control:

```text
CTRADER_CLIENT_ID
CTRADER_CLIENT_SECRET
CTRADER_ACCESS_TOKEN
CTRADER_REFRESH_TOKEN       # optional until refresh is needed
CTRADER_ACCOUNT_ID
CTRADER_ENVIRONMENT         # internal only: demo or live
```

Do not commit real values to the repository.

## OAuth

`CTraderOAuthClient.build_authorization_url()` always requests the view-only `accounts` scope. The token exchange and refresh helpers return token objects whose sensitive token fields are excluded from `repr`.

The cTrader Open API application itself must first be registered and approved in the cTrader Open API portal, with an approved redirect URI.

## Broker data acquired

The read-only JSON transport uses TLS and the cTrader JSON endpoint. It can acquire:

- account balance
- unrealized PnL-derived equity
- used margin and free margin
- account deposit currency
- broker-native symbol id
- symbol digits and pip position
- minimum / maximum / step volume from the broker
- minimum SL / TP distance metadata when provided
- current bid / ask spot quote

All monetary integer fields are converted only when the corresponding cTrader `moneyDigits` scale is present. Missing scales fail closed rather than being guessed.

## cTrader volume units

Open API protocol volume is expressed in 0.01 of a unit. Phase 18 preserves the raw protocol values and also exposes deterministic `*_volume_units` properties by dividing by 100.

This does **not** yet create an `InstrumentRiskSpec` automatically because the Phase 11 risk engine also requires a verified tick value in account currency. Phase 18 must not invent that value.

## Order safety

Phase 18 contains no new-order, amend-order, close-position, or other execution method. Its returned context explicitly carries:

```text
read_only = true
order_submission_enabled = false
order_authorized = false
broker_order_placed = false
```

Phase 17 remains the hard pre-broker order validator. A future execution phase must separately connect broker-native tick value / currency conversion and execution permissions before an order may be sent.

## Public UI contract

Normal UI/report output should look like:

```text
Broker: FP Markets
Account: ••••4821
Status: CONNECTED
```

It must not display `DEMO` or `LIVE`, the full account number, credentials, access tokens, or server details.

## External protocol basis

The implementation follows the current cTrader Open API contracts for OAuth view-only scope, JSON endpoints, application/account authentication, account-list permission scope, trader/account data, symbols and spot quotes. Broker execution remains out of scope for this phase.
