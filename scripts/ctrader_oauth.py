import os
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlencode, urlparse, parse_qs

import requests
from dotenv import load_dotenv, set_key


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_FILE = os.path.join(PROJECT_ROOT, ".env")

load_dotenv(ENV_FILE)

CLIENT_ID = os.getenv("CTRADER_CLIENT_ID")
CLIENT_SECRET = os.getenv("CTRADER_CLIENT_SECRET")
REDIRECT_URI = os.getenv(
    "CTRADER_REDIRECT_URI",
    "http://localhost:8080/callback",
)
SCOPE = os.getenv("CTRADER_SCOPE", "trading")

if not CLIENT_ID:
    raise SystemExit("CTRADER_CLIENT_ID is missing from .env")

if not CLIENT_SECRET:
    raise SystemExit("CTRADER_CLIENT_SECRET is missing from .env")


AUTH_URL = (
    "https://id.ctrader.com/my/settings/openapi/grantingaccess/?"
    + urlencode(
        {
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPE,
            "product": "web",
        }
    )
)

TOKEN_URL = "https://openapi.ctrader.com/apps/token"


class OAuthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return

        query = parse_qs(parsed.query)
        code = query.get("code", [None])[0]

        if not code:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Missing cTrader authorization code.")
            return

        try:
            response = requests.get(
                TOKEN_URL,
                params={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": REDIRECT_URI,
                    "client_id": CLIENT_ID,
                    "client_secret": CLIENT_SECRET,
                },
                headers={"Accept": "application/json"},
                timeout=20,
            )

            response.raise_for_status()
            data = response.json()

            if data.get("errorCode"):
                raise RuntimeError(
                    f"{data.get('errorCode')}: "
                    f"{data.get('description')}"
                )

            access_token = data.get("accessToken")
            refresh_token = data.get("refreshToken")

            if not access_token or not refresh_token:
                raise RuntimeError(
                    "cTrader did not return access and refresh tokens."
                )

            set_key(ENV_FILE, "CTRADER_ACCESS_TOKEN", access_token)
            set_key(ENV_FILE, "CTRADER_REFRESH_TOKEN", refresh_token)

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()

            self.wfile.write(
                b"""
                <html>
                  <body style="font-family: sans-serif">
                    <h2>LONDRES TRADING AI connected successfully</h2>
                    <p>The cTrader authorization tokens were saved locally.</p>
                    <p>You can close this browser tab.</p>
                  </body>
                </html>
                """
            )

            print()
            print("==============================================")
            print("SUCCESS: cTrader OAuth authorization completed")
            print("Access token saved locally in .env")
            print("Refresh token saved locally in .env")
            print("NO TRADING ORDERS HAVE BEEN SENT")
            print("==============================================")

        except Exception as exc:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"cTrader OAuth token exchange failed.")
            print()
            print(f"ERROR: {exc}")

    def log_message(self, format, *args):
        return


server = HTTPServer(("127.0.0.1", 8080), OAuthHandler)

print("LONDRES TRADING AI")
print("OAuth callback ready.")
print("Listening on: http://localhost:8080/callback")
print("Environment: DEMO")
print("Execution: DISABLED")
print()
print("Opening cTrader authorization page...")
print("Keep this Terminal window open.")

webbrowser.open(AUTH_URL)

server.handle_request()
server.server_close()
