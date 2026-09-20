"""Install the cTrader bridge dependency without downgrading requests.

ctrader-open-api 0.9.2 hard-pins requests==2.32.3, while TradingAgents requires
requests>=2.32.4. The Open API client does not need requests for the Twisted
socket transport used by our worker, so install it with --no-deps and keep the
project's newer requests package intact.
"""

from __future__ import annotations

import subprocess
import sys


def run(*args: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "pip", *args],
        check=True,
    )


def main() -> None:
    run("install", "-e", ".[ctrader]")
    run(
        "install",
        "Twisted>=22.2.0",
        "protobuf>=3.20.1",
        "service-identity>=24.1.0",
    )
    run(
        "install",
        "--no-deps",
        "ctrader-open-api==0.9.2",
    )

    subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import ctrader_open_api, requests, service_identity; "
                "print('cTrader bridge dependencies OK'); "
                "print('requests', requests.__version__)"
            ),
        ],
        check=True,
    )


if __name__ == "__main__":
    main()
