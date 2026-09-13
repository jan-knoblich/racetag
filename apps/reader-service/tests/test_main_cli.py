"""racetag_reader_service CLI (contract §3.1)."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from fake_sirit import reserve_port

APP_DIR = Path(__file__).resolve().parent.parent


def test_ip_is_optional_and_new_flags_have_contract_defaults(monkeypatch):
    for name in ("READER_IP", "HEARTBEAT_INTERVAL_S", "DISCOVER_AFTER_FAILURES", "DISCOVER_INTERVAL_S",
                 "NO_DISCOVER", "STOP_ON_STDIN_EOF", "CLOCK_RESYNC_INTERVAL_S"):
        monkeypatch.delenv(name, raising=False)
    from racetag_reader_service import build_parser

    args = build_parser().parse_args([])
    assert args.ip is None
    assert args.heartbeat_interval == 2.0
    assert args.discover_after_failures == 6
    assert args.discover_interval == 60.0
    assert args.no_discover is False
    assert args.stop_on_stdin_eof is False
    assert args.clock_resync_interval == 1800.0


def test_flags_read_environment(monkeypatch):
    monkeypatch.setenv("NO_DISCOVER", "true")
    monkeypatch.setenv("STOP_ON_STDIN_EOF", "1")
    monkeypatch.setenv("HEARTBEAT_INTERVAL_S", "0")
    from racetag_reader_service import build_parser

    args = build_parser().parse_args([])
    assert args.no_discover and args.stop_on_stdin_eof and args.heartbeat_interval == 0.0


def test_stdin_eof_is_mutually_exclusive_with_interactive():
    from racetag_reader_service import main

    with pytest.raises(SystemExit) as exc:
        main(["--interactive", "--stop-on-stdin-eof", "--backend-transport", "mock"])
    assert exc.value.code == 2


def test_missing_backend_url_is_a_configuration_error(monkeypatch):
    monkeypatch.delenv("BACKEND_URL", raising=False)
    from racetag_reader_service import main

    assert main(["--backend-transport", "http", "--no-discover"]) == 1


def test_watch_stdin_eof_fires_on_pipe_close():
    from racetag_reader_service import watch_stdin_eof

    read_fd, write_fd = os.pipe()
    stream = os.fdopen(read_fd, "rb")
    fired = threading.Event()
    try:
        thread = watch_stdin_eof(stream, fired.set)
        os.write(write_fd, b"ignored\n")
        assert not fired.wait(0.05), "data before EOF must not stop the service"
        os.close(write_fd)
        assert fired.wait(2.0)
        thread.join(timeout=2.0)
    finally:
        stream.close()


def test_watch_stdin_eof_without_fileno():
    import io
    from racetag_reader_service import watch_stdin_eof

    fired = threading.Event()
    thread = watch_stdin_eof(io.StringIO("line\n"), fired.set)
    assert fired.wait(2.0)
    thread.join(timeout=2.0)


def test_process_stops_with_exit_code_0_when_stdin_closes():
    """End to end: the real entry point with an unreachable reader keeps
    running (no exit 1) and stops cleanly once the shell closes stdin."""
    argv = [
        sys.executable, str(APP_DIR / "src" / "racetag_reader_service.py"),
        "--ip", "127.0.0.1",
        "--control-port", str(reserve_port()),
        "--event-port", str(reserve_port()),
        "--backend-transport", "mock",
        "--no-discover",
        "--stop-on-stdin-eof",
        "--no-color",
    ]
    env = dict(os.environ, RACETAG_FILE_LOG="0", PYTHONUNBUFFERED="1")
    proc = subprocess.Popen(argv, cwd=str(APP_DIR), env=env, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    output = []
    retrying = threading.Event()

    def _read_output():
        for line in proc.stdout:
            output.append(line)
            if b"connection refused" in line:
                retrying.set()

    reader = threading.Thread(target=_read_output, daemon=True)
    reader.start()
    try:
        # Up and retrying the unreachable reader (not exited with 1) ...
        assert retrying.wait(15), b"".join(output).decode("utf-8", errors="replace")
        assert proc.poll() is None
        # ... until the shell closes the pipe.
        proc.stdin.close()
        proc.wait(timeout=15)
        reader.join(timeout=5)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        proc.stdout.close()
    seen = b"".join(output)
    text = seen.decode("utf-8", errors="replace")
    assert proc.returncode == 0, text
    assert "stdin closed; stopping" in text
    assert "reader client stopped" in text
