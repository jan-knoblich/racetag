"""
Racetag Ingestion Service

(C) 2025 Pablo Clemente Maseda

"""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from sirit_client import SiritClient
from utils import _ts


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Sirit Infinity 510 minimal client (CONTROL then EVENT)")
    parser.add_argument("--ip", required=True, help="Reader IP address")
    parser.add_argument("--event-port", type=int, default=50008, help="Event channel port")
    parser.add_argument("--control-port", type=int, default=50007, help="Control channel port")
    parser.add_argument("--no-color", action="store_true", help="Disable ANSI colors")
    parser.add_argument("--interactive", action="store_true", help="Allow typing CONTROL commands (stdin)")
    parser.add_argument("--raw", action="store_true", help="Print raw chunks/messages received on sockets")
    parser.add_argument("--init_commands_file", help="Path to file with configuration commands sent AFTER reader.events.bind once session id known (defaults to 'init_commands')")
    args = parser.parse_args(argv)

    client = SiritClient(
        ip=args.ip,
        control_port=args.control_port,
        event_port=args.event_port,
        init_commands_path=args.init_commands_file,
        colorize=not args.no_color,
        raw=args.raw,
        interactive=args.interactive,
    )
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