"""
Racetag Reader Service

(C) 2025 Pablo Clemente Maseda

"""

from __future__ import annotations

import argparse
import signal
import sys
from typing import List, Optional
import os

from sirit_client import SiritClient
from utils import _ts


def _env_flag(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "y", "on"}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Sirit Infinity 510 minimal client (CONTROL then EVENT)")
    parser.add_argument("--ip", default=os.getenv("READER_IP"), help="Reader IP address (env: READER_IP)")
    parser.add_argument("--event-port", type=int, default=int(os.getenv("EVENT_PORT", "50008")), help="Event channel port (env: EVENT_PORT)")
    parser.add_argument("--control-port", type=int, default=int(os.getenv("CONTROL_PORT", "50007")), help="Control channel port (env: CONTROL_PORT)")
    parser.add_argument("--no-color", action="store_true", default=_env_flag("NO_COLOR", False), help="Disable ANSI colors (env: NO_COLOR=true)")
    parser.add_argument("--interactive", action="store_true", default=_env_flag("INTERACTIVE", False), help="Allow typing CONTROL commands (stdin) (env: INTERACTIVE=true)")
    parser.add_argument("--raw", action="store_true", default=_env_flag("RAW", False), help="Print raw chunks/messages received on sockets (env: RAW=true)")
    parser.add_argument("--init_commands_file", default=os.getenv("INIT_COMMANDS_FILE"), help="Path to file with configuration commands sent AFTER reader.events.bind once session id known (env: INIT_COMMANDS_FILE; defaults to 'init_commands' if not set)")
    parser.add_argument("--backend-url", default=os.getenv("BACKEND_URL"), help="Backend base URL to send events (e.g., http://localhost:8000) (env: BACKEND_URL)")
    parser.add_argument("--backend-token", default=os.getenv("BACKEND_TOKEN"), help="Optional Bearer token for backend auth (env: BACKEND_TOKEN)")
    parser.add_argument(
        "--backend-transport",
        choices=["http", "mock"],
        default=os.getenv("BACKEND_TRANSPORT", "http"),
        help="Type of Backend transport implementation to use; (http) or for testing (mock)",
    )
    args = parser.parse_args(argv)

    # Validate required IP (from CLI or env)
    if not args.ip:
        parser.error("--ip is required (or set env READER_IP)")

    client = SiritClient(
        ip=args.ip,
        control_port=args.control_port,
        event_port=args.event_port,
        init_commands_path=args.init_commands_file,
        colorize=not args.no_color,
        raw=args.raw,
        interactive=args.interactive,
        backend_url=args.backend_url,
        backend_token=args.backend_token,
        backend_transport=args.backend_transport,
    )
    
    # Install signal handlers for graceful shutdown in containers (SIGTERM) and terminals (SIGINT)
    def _on_signal(signum, frame):  # noqa: ARG001
        name = {
            getattr(signal, 'SIGINT', None): 'SIGINT',
            getattr(signal, 'SIGTERM', None): 'SIGTERM',
            getattr(signal, 'SIGQUIT', None): 'SIGQUIT',
        }.get(signum, str(signum))
        print(f"[{_ts()}] Received {name}; stopping...")
        client.request_stop()

    for sig in (getattr(signal, 'SIGINT', None), getattr(signal, 'SIGTERM', None), getattr(signal, 'SIGQUIT', None)):
        if sig is not None:
            try:
                signal.signal(sig, _on_signal)
            except Exception:
                pass


    try:
        client.start()
        client.run_forever()
    except RuntimeError as e:
        print(f"[{_ts()}] Startup failed: {e}")
        client.stop()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())