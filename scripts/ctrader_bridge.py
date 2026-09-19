import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


HOST = "127.0.0.1"
PORT = 8765

PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))
)

BARS_SCRIPT = os.path.join(
    PROJECT_ROOT,
    "scripts",
    "ctrader_bars_cli.py",
)

ALLOWED_SYMBOLS = {
    "XAUUSD",
    "GOLD",
    "NASDAQ",
    "USTECH100",
    "US TECH 100",
    "US500",
    "US 500",
}

ALLOWED_TIMEFRAMES = {
    "M1",
    "M3",
    "M5",
    "M15",
    "M30",
    "H1",
    "H4",
    "D1",
    "W1",
}


def send_json(handler, status, payload):
    body = json.dumps(
        payload,
        indent=2,
    ).encode("utf-8")

    handler.send_response(status)
    handler.send_header(
        "Content-Type",
        "application/json; charset=utf-8",
    )
    handler.send_header(
        "Content-Length",
        str(len(body)),
    )
    handler.end_headers()
    handler.wfile.write(body)


class Handler(BaseHTTPRequestHandler):

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/health":
            send_json(
                self,
                200,
                {
                    "service": "londres-ctrader-bridge",
                    "status": "ok",
                    "environment": "demo",
                    "execution_enabled": False,
                    "host": HOST,
                    "port": PORT,
                },
            )
            return

        if parsed.path != "/bars":
            send_json(
                self,
                404,
                {
                    "error": "Not found",
                },
            )
            return

        query = parse_qs(parsed.query)

        symbol = query.get(
            "symbol",
            [""],
        )[0].strip().upper()

        timeframe = query.get(
            "timeframe",
            [""],
        )[0].strip().upper()

        try:
            count = int(
                query.get(
                    "count",
                    ["100"],
                )[0]
            )
        except ValueError:
            send_json(
                self,
                400,
                {
                    "error": "count must be an integer",
                },
            )
            return

        if symbol not in ALLOWED_SYMBOLS:
            send_json(
                self,
                400,
                {
                    "error": f"Unsupported symbol: {symbol}",
                },
            )
            return

        if timeframe not in ALLOWED_TIMEFRAMES:
            send_json(
                self,
                400,
                {
                    "error": f"Unsupported timeframe: {timeframe}",
                },
            )
            return

        if count < 1 or count > 2000:
            send_json(
                self,
                400,
                {
                    "error": "count must be between 1 and 2000",
                },
            )
            return

        try:
            result = subprocess.run(
                [
                    sys.executable,
                    BARS_SCRIPT,
                    symbol,
                    timeframe,
                    str(count),
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                timeout=45,
            )

            if result.returncode != 0:
                send_json(
                    self,
                    502,
                    {
                        "error": "cTrader worker failed",
                        "details": result.stderr[-2000:],
                    },
                )
                return

            payload = json.loads(
                result.stdout
            )

            send_json(
                self,
                200,
                payload,
            )

        except subprocess.TimeoutExpired:
            send_json(
                self,
                504,
                {
                    "error": "cTrader request timed out",
                },
            )

        except Exception as exc:
            send_json(
                self,
                500,
                {
                    "error": str(exc),
                },
            )

    def log_message(self, format, *args):
        return


server = ThreadingHTTPServer(
    (HOST, PORT),
    Handler,
)

print("LONDRES cTrader Bridge")
print(f"Listening on http://{HOST}:{PORT}")
print("Environment: DEMO")
print("Execution: DISABLED")
print("Endpoints:")
print("  GET /health")
print("  GET /bars?symbol=XAUUSD&timeframe=H4&count=100")
print()
print("Press Control+C to stop.")

server.serve_forever()
