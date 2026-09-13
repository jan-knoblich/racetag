"""
Racetag Reader Service

(C) 2025 Pablo Clemente Maseda

"""

from __future__ import annotations

import argparse
import os
import platform
import signal
import sys
import threading
from typing import Callable, List, Optional

from sirit_client import SiritClient
from status_reporter import reader_service_version
from utils import configure_logging, env_flag, get_logger, resolve_log_dir


def watch_stdin_eof(stream, on_eof: Callable[[], None]) -> threading.Thread:
    """Call ``on_eof`` once ``stream`` reaches end-of-file.

    The desktop shell spawns the service with a stdin pipe and closes it to
    request a graceful stop; when the shell dies the OS closes the pipe too.
    This is the only portable stop signal on Windows, where terminate() is a
    hard kill that would skip the spool flush. Whatever arrives on the pipe
    before EOF is ignored.
    """
    def _watch() -> None:
        try:
            fileno = stream.fileno()
        except (AttributeError, OSError, ValueError):
            fileno = None
        try:
            if fileno is not None:
                while os.read(fileno, 4096):
                    pass
            else:
                while stream.readline():
                    pass
        except (OSError, ValueError):
            pass  # a broken or closed stdin is an EOF as well
        on_eof()

    thread = threading.Thread(target=_watch, name="reader-stdin-eof", daemon=True)
    thread.start()
    return thread


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sirit Infinity 510 minimal client (CONTROL then EVENT)")
    parser.add_argument("--ip", default=os.getenv("READER_IP"), help="Reader IP address; optional, without it the reader is searched on the network (env: READER_IP)")
    parser.add_argument("--event-port", type=int, default=int(os.getenv("EVENT_PORT", "50008")), help="Event channel port (env: EVENT_PORT)")
    parser.add_argument("--control-port", type=int, default=int(os.getenv("CONTROL_PORT", "50007")), help="Control channel port (env: CONTROL_PORT)")
    parser.add_argument("--no-color", action="store_true", default=env_flag("NO_COLOR", False), help="Disable ANSI colors (env: NO_COLOR=true)")
    parser.add_argument("--interactive", action="store_true", default=env_flag("INTERACTIVE", False), help="Allow typing CONTROL commands (stdin) (env: INTERACTIVE=true)")
    parser.add_argument("--raw", action="store_true", default=env_flag("RAW", False), help="Print raw chunks/messages received on sockets (env: RAW=true)")
    parser.add_argument("--init_commands_file", default=os.getenv("INIT_COMMANDS_FILE"), help="Path to file with configuration commands sent AFTER reader.events.bind once session id known (env: INIT_COMMANDS_FILE; defaults to 'init_commands' if not set)")
    parser.add_argument("--backend-url", default=os.getenv("BACKEND_URL"), help="Backend base URL to send events (e.g., http://localhost:8000) (env: BACKEND_URL)")
    parser.add_argument("--backend-token", default=os.getenv("BACKEND_TOKEN"), help="Optional token sent as X-API-Key header (env: BACKEND_TOKEN)")
    parser.add_argument(
        "--backend-transport",
        choices=["http", "mock"],
        default=os.getenv("BACKEND_TRANSPORT", "http"),
        help="Type of Backend transport implementation to use; (http) or for testing (mock)",
    )
    parser.add_argument(
        "--min-lap-interval",
        type=float,
        default=float(os.getenv("MIN_LAP_INTERVAL_S", "0")),
        help=(
            "Reader-side seconds between two forwarded arrive events for the "
            "same tag. Default 0 = presence-union only; the backend is the "
            "single lap-cooldown authority (AUDIT-2026-07 M6). "
            "(env: MIN_LAP_INTERVAL_S)"
        ),
    )
    parser.add_argument(
        "--antenna-power",
        type=int,
        default=int(os.getenv("ANTENNA_POWER", "300")),
        help=(
            "Conducted power for connected antenna ports, in 0.1 dBm units "
            "(300 = 30 dBm max). Applied automatically on every reader connect. "
            "(env: ANTENNA_POWER)"
        ),
    )
    parser.add_argument(
        "--antenna-ports",
        type=str,
        default=os.getenv("ANTENNA_PORTS", "1 2"),
        help=(
            "Fallback antenna ports (space/comma separated, e.g. '1 2') used "
            "only when the reader's antennas.detected auto-detection returns "
            "nothing. (env: ANTENNA_PORTS)"
        ),
    )
    parser.add_argument(
        "--heartbeat-interval",
        type=float,
        default=float(os.getenv("HEARTBEAT_INTERVAL_S", "2.0")),
        help="Seconds between reader status POSTs to {backend-url}/reader/status; 0 disables (env: HEARTBEAT_INTERVAL_S)",
    )
    parser.add_argument(
        "--discover-after-failures",
        type=int,
        default=int(os.getenv("DISCOVER_AFTER_FAILURES", "6")),
        help="Consecutive connect failures before the reader is searched on the network (env: DISCOVER_AFTER_FAILURES)",
    )
    parser.add_argument(
        "--discover-interval",
        type=float,
        default=float(os.getenv("DISCOVER_INTERVAL_S", "60")),
        help="Minimum seconds between two automatic discovery runs (env: DISCOVER_INTERVAL_S)",
    )
    parser.add_argument(
        "--no-discover",
        action="store_true",
        default=env_flag("NO_DISCOVER", False),
        help="Disable automatic discovery; backend discover commands still work (env: NO_DISCOVER=true)",
    )
    parser.add_argument(
        "--stop-on-stdin-eof",
        action="store_true",
        default=env_flag("STOP_ON_STDIN_EOF", False),
        help="Stop gracefully when stdin reaches EOF (used by the desktop shell) (env: STOP_ON_STDIN_EOF=true)",
    )
    parser.add_argument(
        "--clock-resync-interval",
        type=float,
        default=float(os.getenv("CLOCK_RESYNC_INTERVAL_S", "1800")),
        help="Seconds between re-pushing the host UTC clock to the connected reader; 0 disables (env: CLOCK_RESYNC_INTERVAL_S)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=env_flag("RACETAG_DEBUG", False),
        help="Enable debug logging (env: RACETAG_DEBUG=true)",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Run the reader-service. Returns 0 after a requested stop, 1 on a
    configuration error (an unreachable reader is not an error: it is retried)."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.interactive and args.stop_on_stdin_eof:
        parser.error("--stop-on-stdin-eof cannot be combined with --interactive (both read stdin)")

    # Loggers are created at import time; reconfiguring the shared parent
    # applies --debug to all of them.
    if args.debug:
        os.environ["RACETAG_DEBUG"] = "1"
    configure_logging(debug=args.debug)
    _log = get_logger("reader.main")
    _log.info(
        "racetag reader-service %s starting (python %s, %s, pid %d, log dir %s, reader ip %s, backend %s)",
        reader_service_version(), platform.python_version(), sys.platform, os.getpid(),
        resolve_log_dir(), args.ip or "auto", args.backend_url if args.backend_transport == "http" else "mock",
    )

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
        min_lap_interval_s=args.min_lap_interval,
        antenna_power=args.antenna_power,
        antenna_ports_fallback=args.antenna_ports,
        heartbeat_interval_s=args.heartbeat_interval,
        discover_after_failures=args.discover_after_failures,
        discover_interval_s=args.discover_interval,
        discovery_enabled=not args.no_discover,
        clock_resync_interval_s=args.clock_resync_interval,
    )

    # Install signal handlers for graceful shutdown in containers (SIGTERM) and terminals (SIGINT)
    def _on_signal(signum, frame):  # noqa: ARG001
        sig_name = {
            getattr(signal, 'SIGINT', None): 'SIGINT',
            getattr(signal, 'SIGTERM', None): 'SIGTERM',
            getattr(signal, 'SIGQUIT', None): 'SIGQUIT',
        }.get(signum, str(signum))
        _log.info("Received %s; stopping...", sig_name)
        client.request_stop()

    for sig in (getattr(signal, 'SIGINT', None), getattr(signal, 'SIGTERM', None), getattr(signal, 'SIGQUIT', None)):
        if sig is not None:
            try:
                signal.signal(sig, _on_signal)
            except Exception:
                pass

    try:
        client.start()
    except RuntimeError as e:
        _log.error("Startup failed: %s", e)
        client.stop()
        return 1

    if args.stop_on_stdin_eof:
        if sys.stdin is None:
            _log.warning("--stop-on-stdin-eof given but the process has no stdin; ignoring")
        else:
            def _on_stdin_eof() -> None:
                _log.info("stdin closed; stopping...")
                client.request_stop()

            watch_stdin_eof(sys.stdin, _on_stdin_eof)

    client.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
