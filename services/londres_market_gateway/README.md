# Londres Market Data Gateway

Broker-independent HTTPS market-data middleware for the Londres Trading AI iOS app.

The gateway keeps vendor credentials on the server and exposes only the canonical app contract:

- `GET /health`
- `GET /v1/market/candles?symbol=NQ&timeframe=M5&limit=2000`
- `GET /v1/market/quote?symbol=NQ`

The iOS app never receives the Databento API key and this service contains no broker account, broker authentication, order submission, or order-routing capability.

## Data source

The first provider is Databento CME Globex (`GLBX.MDP3`). The default roots are `NQ`, `ES`, and `YM`, requested through Databento continuous volume-front symbology (`NQ.v.0`, `ES.v.0`, `YM.v.0`).

Historical OHLCV seeds the cache once. A single live session then subscribes to:

- `ohlcv-1m` for closed one-minute bars
- `bbo-1s` for current bid/ask

The gateway derives the app timeframes locally:

- `M1` — vendor one-minute bars
- `M5` and `M15` — resampled from M1
- `H1` — vendor hourly history plus recent M1 aggregation
- `H4` — fixed UTC-4 Londres four-hour buckets
- `D` — 18:00 fixed UTC-4 trading-day buckets
- `W` — Monday 18:00 fixed UTC-4 weekly buckets

Only closed candles are returned.

## Installation

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r services/londres_market_gateway/requirements.txt
```

Set credentials locally or in the hosting provider's secret manager. Never commit them:

```bash
export DATABENTO_API_KEY='...'
export LONDRES_MARKET_LIVE=true
export LONDRES_MARKET_SYMBOLS='NQ,ES,YM'
export LONDRES_GATEWAY_BEARER_TOKEN='development-gateway-token'
```

Run locally:

```bash
uvicorn services.londres_market_gateway.app:app --host 0.0.0.0 --port 8080
```

For the iOS simulator or app runtime, configure:

```text
LONDRES_MARKET_DATA_BASE_URL=https://your-gateway.example.com
LONDRES_MARKET_DATA_BEARER_TOKEN=development-gateway-token
```

The gateway URL must use HTTPS in the iOS client.

## Container

Build from the repository root:

```bash
docker build -f services/londres_market_gateway/Dockerfile -t londres-market-gateway .
```

Run with secrets injected at runtime:

```bash
docker run --rm -p 8080:8080 \
  -e DATABENTO_API_KEY \
  -e LONDRES_GATEWAY_BEARER_TOKEN \
  londres-market-gateway
```

## Market-data licensing

Code integration and exchange redistribution rights are separate concerns. CME live data supplied to external paying app users may require commercial/external distribution licensing. Do not expose live data to production subscribers until the applicable Databento/CME licensing and subscriber requirements are confirmed. The gateway supports development/internal testing without changing the iOS architecture.

## Security boundary

`DATABENTO_API_KEY` belongs only on the gateway server. Do not put it in Xcode settings, `Info.plist`, source code, screenshots, GitHub, TestFlight notes, or customer devices.

`LONDRES_GATEWAY_BEARER_TOKEN` is an optional development/private-deployment control. A single shared token embedded in a public App Store binary is not a production subscriber-authentication solution. Production should replace it with short-lived per-user authorization tied to the app subscription/account service.
