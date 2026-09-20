"""Install the cTrader bridge runtime without altering TradingAgents requests.

ctrader-open-api 0.9.2 pins requests==2.32.3 while TradingAgents requires a
newer requests release. Install the cTrader client without dependencies and
install only the Twisted/protobuf runtime pieces used by the worker.

service-identity is intentionally not required here. Twisted may emit a warning
about it, but the cTrader worker can run without forcing a local cryptography
build on Intel macOS.
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
    run(
        "install",
        "--upgrade",
        "pip",
        "setuptools",
        "wheel",
    )
    run(
        "install",
        "Twisted>=22.2.0",
        "protobuf>=3.20.1",
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
                "import ctrader_open_api, requests, twisted, google.protobuf; "
                "print('cTrader bridge dependencies OK'); "
                "print('requests', requests.__version__)"
            ),
        ],
        check=True,
    )


if __name__ == "__main__":
    main()
