"""Tests for AUDIT-2026-07 H6/H7: process-lifecycle guards in the desktop shell.

H7: single-instance flock — a second acquisition on the same lock file must
    fail while the first handle is alive.
H6: reader-service PID file stale-kill logic must never touch a PID that is
    no longer a reader-service. (Writing/removing the PID file is covered by
    test_reader_supervisor.py.)
"""
import os
import subprocess
import sys
import time
import types
from pathlib import Path
from unittest.mock import patch

import pytest


def _import_desktop_app():
    """Import apps/desktop/app.py — robust against sys.path pollution.

    Other tests call _build_combined_app(), which inserts the BACKEND dir
    (also containing an app.py) at sys.path[0]; a naive `import app` would
    then resolve to the wrong module. Force the desktop dir to the front and
    verify what we got.
    """
    desktop_dir = Path(__file__).resolve().parent.parent
    existing = sys.modules.get("app")
    if existing is not None and Path(
        getattr(existing, "__file__", "")
    ).parent == desktop_dir:
        return existing
    sys.modules.pop("app", None)
    if str(desktop_dir) in sys.path:
        sys.path.remove(str(desktop_dir))
    sys.path.insert(0, str(desktop_dir))
    import app as desktop_app  # noqa: PLC0415
    assert Path(desktop_app.__file__).parent == desktop_dir, (
        f"imported wrong app module: {desktop_app.__file__}"
    )
    return desktop_app


# ---------------------------------------------------------------------------
# H7 — single-instance lock
# ---------------------------------------------------------------------------

class TestSingleInstanceLock:

    def test_lock_acquired_then_second_acquisition_fails(self, tmp_path):
        app = _import_desktop_app()
        lock_path = tmp_path / "racetag.lock"

        fh1 = app._try_lock_file(lock_path)
        assert fh1 is not None, "first acquisition must succeed"
        try:
            fh2 = app._try_lock_file(lock_path)
            assert fh2 is None, "second acquisition must fail while first is held"
        finally:
            fh1.close()

    def test_lock_released_on_close_allows_reacquisition(self, tmp_path):
        app = _import_desktop_app()
        lock_path = tmp_path / "racetag.lock"

        fh1 = app._try_lock_file(lock_path)
        assert fh1 is not None
        fh1.close()  # simulates process exit — flock released with the fd

        fh2 = app._try_lock_file(lock_path)
        assert fh2 is not None, "lock must be reacquirable after release"
        fh2.close()

    def test_lock_holds_after_pid_is_written(self, tmp_path):
        """The first instance writes its PID into the lock file; a second
        attempt must still fail (regression for the Windows seek(0) fix)."""
        app = _import_desktop_app()
        lock_path = tmp_path / "racetag.lock"

        fh1 = app._try_lock_file(lock_path)
        assert fh1 is not None
        try:
            fh1.truncate(0)
            fh1.write("123456")
            fh1.flush()
            assert app._try_lock_file(lock_path) is None
        finally:
            fh1.close()

    def test_windows_lock_seeks_to_start_before_locking(self, tmp_path, monkeypatch):
        """msvcrt.locking locks from the current position and "a+" opens at
        EOF, so the lock must be taken at offset 0 regardless of file size."""
        app = _import_desktop_app()
        lock_path = tmp_path / "racetag.lock"
        lock_path.write_text("98765")  # PID left by a previous instance
        positions = []

        def fake_locking(fd, mode, nbytes):
            positions.append(os.lseek(fd, 0, os.SEEK_CUR))

        fake_msvcrt = types.ModuleType("msvcrt")
        fake_msvcrt.LK_NBLCK = 2
        fake_msvcrt.locking = fake_locking
        monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(sys, "platform", "win32")
            fh = app._try_lock_file(lock_path)
        try:
            assert fh is not None
            assert positions == [0]
        finally:
            fh.close()

    def test_lock_survives_across_subprocess(self, tmp_path):
        """Real cross-process check: hold the lock here, try from a child."""
        app = _import_desktop_app()
        lock_path = tmp_path / "racetag.lock"

        fh = app._try_lock_file(lock_path)
        assert fh is not None
        try:
            if sys.platform == "win32":
                acquire = (
                    "import msvcrt\n"
                    "fh.seek(0)\n"
                    "msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)\n"
                )
            else:
                acquire = (
                    "import fcntl\n"
                    "fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
                )
            probe = (
                f"fh = open({str(lock_path)!r}, 'a+')\n"
                "try:\n"
                + "".join(f"    {line}\n" for line in acquire.splitlines())
                + "    print('ACQUIRED')\n"
                "except OSError:\n"
                "    print('LOCKED')\n"
            )
            out = subprocess.run(
                [sys.executable, "-c", probe],
                capture_output=True, text=True, timeout=10,
            ).stdout.strip()
            assert out == "LOCKED", f"child process saw: {out!r}"
        finally:
            fh.close()


# ---------------------------------------------------------------------------
# H6 — reader-service PID file + stale-kill
# ---------------------------------------------------------------------------

class TestReaderPidFile:

    def test_kill_stale_ignores_missing_pid_file(self, tmp_path, monkeypatch):
        app = _import_desktop_app()
        monkeypatch.setattr(app, "_READER_PID_FILE", tmp_path / "nope.pid")
        app._kill_stale_reader_service()  # must not raise

    def test_kill_stale_removes_file_for_dead_pid(self, tmp_path, monkeypatch):
        app = _import_desktop_app()
        pid_file = tmp_path / "reader-service.pid"
        monkeypatch.setattr(app, "_READER_PID_FILE", pid_file)

        # Spawn a real short-lived process and let it exit → dead PID
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait()
        pid_file.write_text(str(p.pid))

        app._kill_stale_reader_service()
        assert not pid_file.exists()

    def test_kill_stale_does_not_kill_reused_pid(self, tmp_path, monkeypatch):
        """A live process whose command is NOT a reader-service must survive
        and the stale file must be dropped."""
        app = _import_desktop_app()
        pid_file = tmp_path / "reader-service.pid"
        monkeypatch.setattr(app, "_READER_PID_FILE", pid_file)

        # A live innocent process (sleep)
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"])
        try:
            pid_file.write_text(str(p.pid))
            app._kill_stale_reader_service()
            assert p.poll() is None, "innocent process was killed!"
            assert not pid_file.exists(), "stale file should be dropped"
        finally:
            p.kill()
            p.wait()

    @pytest.mark.skipif(sys.platform == "win32", reason="stale-kill is POSIX-only; Windows relies on the Job Object")
    def test_kill_stale_terminates_real_reader_service_lookalike(self, tmp_path, monkeypatch, caplog):
        """A live process whose command matches a reader-service gets SIGTERM."""
        app = _import_desktop_app()
        pid_file = tmp_path / "reader-service.pid"
        monkeypatch.setattr(app, "_READER_PID_FILE", pid_file)

        # Launch a sleeper whose argv contains the marker string the check
        # greps for. Python's argv shows up in `ps -o command`.
        p = subprocess.Popen([
            sys.executable, "-c",
            "import sys, time; time.sleep(30)", "--reader-service",
        ])
        try:
            pid_file.write_text(str(p.pid))
            with caplog.at_level("INFO", logger="racetag.shell"):
                app._kill_stale_reader_service()
            # Give the SIGTERM a moment
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and p.poll() is None:
                time.sleep(0.1)
            assert p.poll() is not None, (
                "stale reader-service was not terminated; command line seen: "
                f"{app._process_command_line(p.pid)!r}; log: {caplog.text!r}"
            )
            assert not pid_file.exists()
        finally:
            if p.poll() is None:
                p.kill()
                p.wait()


# ---------------------------------------------------------------------------
# H6 — parent-liveness self-termination in the reader-service
# ---------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform == "win32", reason="parent-liveness check is a no-op on Windows")
class TestParentLiveness:

    def test_run_forever_exits_when_reparented(self):
        """Simulate parent death by making os.getppid return a new value —
        run_forever must exit (and call stop) within a couple of ticks."""
        reader_src = str(
            Path(__file__).resolve().parents[2] / "reader-service" / "src"
        )
        if reader_src not in sys.path:
            sys.path.insert(0, reader_src)
        import sirit_client as sc  # noqa: PLC0415
        from backend_client.mock import MockBackendClient  # noqa: PLC0415

        client = sc.SiritClient(
            ip="127.0.0.1", control_port=50007, event_port=50008,
            init_commands_path=None, colorize=False, raw=False,
            interactive=False, backend_transport="mock",
        )
        client._backend = MockBackendClient()
        client.control_sock = None

        ppids = iter([1111, 1111, 1])  # third tick: reparented to launchd
        with patch.object(sc.os, "getppid", side_effect=lambda: next(ppids, 1)):
            import threading
            t = threading.Thread(target=client.run_forever, daemon=True)
            t.start()
            t.join(timeout=5)
            assert not t.is_alive(), "run_forever did not exit after reparenting"
        assert client._stopping.is_set(), "stop() was not invoked"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only helper")
def test_process_command_line_keeps_marker_after_long_path():
    """The marker must survive long command lines (procps truncates `ps` output
    to 80 columns when it is not writing to a terminal)."""
    app = _import_desktop_app()
    long_arg = "x" * 200
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)", long_arg, "--reader-service"])
    try:
        cmd = app._process_command_line(p.pid)
        assert cmd is not None
        assert "--reader-service" in cmd
    finally:
        p.kill()
        p.wait()
