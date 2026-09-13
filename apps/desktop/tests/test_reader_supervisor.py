"""Tests for reader_supervisor.ReaderSupervisor (plan A1, contract 2.7/4.1).

Children are tiny Python scripts, back-off values are injected, so the whole
module runs in well under two seconds of real waiting.
"""
import ctypes
import os
import subprocess
import sys
import threading
import time
from unittest.mock import MagicMock

import pytest

import reader_supervisor
from reader_supervisor import ReaderSupervisor

CRASH_CHILD = "import sys; sys.exit(3)"
# Behaves like the reader-service with --stop-on-stdin-eof.
STDIN_CHILD = "import sys; sys.stdin.read(); sys.exit(0)"
# Ignores stdin EOF, so only terminate()/kill() stop it.
STUBBORN_CHILD = "import time\nwhile True:\n    time.sleep(0.05)"


def _builder(code, calls=None):
    def build():
        if calls is not None:
            calls.append(time.monotonic())
        return [sys.executable, "-c", code], dict(os.environ)

    return build


def _wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def test_status_before_start(tmp_path):
    sup = ReaderSupervisor(_builder(STDIN_CHILD), tmp_path / "r.pid")
    assert sup.status() == {
        "running": False, "pid": None, "restart_count": 0, "last_exit_code": None, "restart_pending": False,
    }
    assert sup.is_running() is False


def test_crash_is_restarted_with_backoff(tmp_path):
    calls = []
    events = []
    sup = ReaderSupervisor(
        _builder(CRASH_CHILD, calls),
        tmp_path / "r.pid",
        backoff_s=(0.05,),
        on_event=lambda name, data: events.append((name, data)),
    )
    try:
        sup.start()
        assert _wait_for(lambda: sup.status()["restart_count"] >= 2), sup.status()
    finally:
        sup.stop(timeout_s=1.0)
    status = sup.status()
    assert status["last_exit_code"] == 3
    assert status["running"] is False
    assert len(calls) >= 3, "argv builder must run for every spawn"
    names = [name for name, _ in events]
    assert "started" in names and "exited" in names and "restarting" in names
    exited = [data for name, data in events if name == "exited"]
    assert exited[0]["requested"] is False


def test_requested_stop_closes_stdin_and_does_not_restart(tmp_path):
    pid_file = tmp_path / "r.pid"
    events = []
    sup = ReaderSupervisor(
        _builder(STDIN_CHILD), pid_file, backoff_s=(0.05,),
        on_event=lambda name, data: events.append(name),
    )
    try:
        sup.start()
        assert _wait_for(sup.is_running)
        pid = sup.status()["pid"]
        assert _wait_for(lambda: pid_file.exists() and pid_file.read_text() == str(pid))
        t0 = time.monotonic()
        sup.stop(timeout_s=3.0)
        elapsed = time.monotonic() - t0
    finally:
        sup.stop(timeout_s=1.0)

    status = sup.status()
    # Exit code 0 (not a signal) proves the child left because stdin closed,
    # before any terminate() escalation.
    assert status == {
        "running": False, "pid": None, "restart_count": 0, "last_exit_code": 0, "restart_pending": False,
    }
    assert elapsed < 2.0
    assert not pid_file.exists()
    assert "restarting" not in events
    time.sleep(0.1)
    assert sup.status()["restart_count"] == 0


def test_stop_escalates_to_terminate_when_stdin_is_ignored(tmp_path):
    sup = ReaderSupervisor(_builder(STUBBORN_CHILD), tmp_path / "r.pid", backoff_s=(0.05,))
    try:
        sup.start()
        assert _wait_for(sup.is_running)
        sup.stop(timeout_s=0.2)
    finally:
        sup.stop(timeout_s=0.2)
    status = sup.status()
    assert status["running"] is False
    assert status["last_exit_code"] not in (None, 0)
    assert status["restart_count"] == 0


def test_restart_spawns_fresh_child_and_rereads_config(tmp_path):
    calls = []
    sup = ReaderSupervisor(_builder(STDIN_CHILD, calls), tmp_path / "r.pid", backoff_s=(5.0,))
    try:
        sup.start()
        assert _wait_for(sup.is_running)
        first_pid = sup.status()["pid"]

        t0 = time.monotonic()
        sup.restart()
        assert time.monotonic() - t0 < 0.5, "restart() must not block"

        assert _wait_for(
            lambda: sup.status()["running"] and sup.status()["pid"] not in (None, first_pid)
        ), sup.status()
        status = sup.status()
        assert status["restart_count"] == 1
        assert status["last_exit_code"] == 0
        assert len(calls) == 2
    finally:
        sup.stop(timeout_s=1.0)


def test_restart_pending_until_replacement_is_spawned(tmp_path):
    """The old child's final 'stopped' heartbeat is sent while restart_pending
    is True, so the UI can tell a requested restart from a real stop."""
    # Like the reader-service: on stdin EOF it needs a moment (standby, final
    # heartbeat) before it exits.
    slow_exit_child = "import sys, time; sys.stdin.read(); time.sleep(0.5); sys.exit(0)"
    calls = []
    sup = ReaderSupervisor(_builder(slow_exit_child, calls), tmp_path / "r.pid", backoff_s=(5.0,))
    try:
        sup.start()
        assert _wait_for(sup.is_running)
        first_pid = sup.status()["pid"]
        assert sup.status()["restart_pending"] is False

        sup.restart()
        status = sup.status()
        assert status["restart_pending"] is True
        assert status["pid"] == first_pid, "old child still shutting down"

        assert _wait_for(lambda: len(calls) == 2 and sup.status()["pid"] not in (None, first_pid)), sup.status()
        assert sup.status()["restart_pending"] is False
    finally:
        sup.stop(timeout_s=1.0)
    assert sup.status()["restart_pending"] is False


def test_restart_skips_pending_backoff(tmp_path):
    calls = []
    sup = ReaderSupervisor(_builder(CRASH_CHILD, calls), tmp_path / "r.pid", backoff_s=(30.0,))
    try:
        sup.start()
        assert _wait_for(lambda: sup.status()["last_exit_code"] == 3)
        # The supervisor now waits 30 s; restart() must cut that short.
        sup.restart()
        assert _wait_for(lambda: len(calls) >= 2, timeout=2.0)
    finally:
        sup.stop(timeout_s=1.0)


def test_restart_when_not_started_is_ignored(tmp_path):
    calls = []
    sup = ReaderSupervisor(_builder(STDIN_CHILD, calls), tmp_path / "r.pid")
    sup.restart()
    time.sleep(0.05)
    assert calls == []
    assert sup.is_running() is False


def test_backoff_sequence_and_reset_after_stable_uptime(tmp_path):
    sup = ReaderSupervisor(_builder(STDIN_CHILD), tmp_path / "r.pid", backoff_s=(1, 2, 5, 10, 30))
    assert [sup._next_backoff(0.5) for _ in range(7)] == [1, 2, 5, 10, 30, 30, 30]
    assert sup._next_backoff(60.0) == 1, "60 s of uptime resets the back-off"
    assert sup._next_backoff(0.1) == 2
    assert sup._next_backoff(None) == 5, "a failed spawn counts as a quick failure"


def test_backoff_reset_with_injected_clock(tmp_path):
    """Every child 'runs' 100 s according to the clock, so the back-off never
    grows beyond its first step even though the child crashes at once."""
    ticks = iter(range(0, 10_000_000, 100))
    lock = threading.Lock()

    def clock():
        with lock:
            return float(next(ticks))

    sup = ReaderSupervisor(
        _builder(CRASH_CHILD), tmp_path / "r.pid", backoff_s=(0.05, 30.0), clock=clock
    )
    try:
        sup.start()
        assert _wait_for(lambda: sup.status()["restart_count"] >= 2, timeout=3.0), sup.status()
    finally:
        sup.stop(timeout_s=1.0)


def test_spawn_failure_is_retried(tmp_path):
    events = []

    def broken_builder():
        return [str(tmp_path / "no-such-binary")], dict(os.environ)

    sup = ReaderSupervisor(
        broken_builder, tmp_path / "r.pid", backoff_s=(0.02,),
        on_event=lambda name, data: events.append(name),
    )
    try:
        sup.start()
        assert _wait_for(lambda: sup.status()["restart_count"] >= 2)
        assert sup.is_running() is False
    finally:
        sup.stop(timeout_s=1.0)
    assert "spawn_failed" in events


def test_failing_event_callback_does_not_stop_supervision(tmp_path):
    def bad_callback(name, data):
        raise RuntimeError("observer bug")

    sup = ReaderSupervisor(_builder(CRASH_CHILD), tmp_path / "r.pid", backoff_s=(0.02,), on_event=bad_callback)
    try:
        sup.start()
        assert _wait_for(lambda: sup.status()["restart_count"] >= 1)
    finally:
        sup.stop(timeout_s=1.0)


def test_stop_is_idempotent_and_start_after_stop_works(tmp_path):
    sup = ReaderSupervisor(_builder(STDIN_CHILD), tmp_path / "r.pid")
    sup.stop()
    try:
        sup.start()
        assert _wait_for(sup.is_running)
        sup.stop(timeout_s=2.0)
        sup.stop(timeout_s=2.0)
        assert sup.is_running() is False
        sup.start()
        assert _wait_for(sup.is_running)
    finally:
        sup.stop(timeout_s=2.0)
    assert sup.is_running() is False


def test_child_stderr_goes_to_file_without_console(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "stdout", None)
    stderr_log = tmp_path / "logs" / "reader-stderr.log"
    stderr_log.parent.mkdir()
    stderr_log.write_bytes(b"x" * (reader_supervisor._STDERR_LOG_MAX_BYTES + 1))
    code = "import sys; sys.stderr.write('ImportError: boom\\n'); sys.exit(1)"
    sup = ReaderSupervisor(_builder(code), tmp_path / "r.pid", backoff_s=(30.0,), stderr_log=stderr_log)
    try:
        sup.start()
        assert _wait_for(lambda: sup.status()["last_exit_code"] == 1)
    finally:
        sup.stop(timeout_s=1.0)
    assert "ImportError: boom" in stderr_log.read_text(encoding="utf-8")
    rotated = stderr_log.with_name("reader-stderr.log.1")
    assert rotated.stat().st_size == reader_supervisor._STDERR_LOG_MAX_BYTES + 1


# ---------------------------------------------------------------------------
# Windows specifics
# ---------------------------------------------------------------------------

def test_job_object_structure_layout():
    expected = 144 if ctypes.sizeof(ctypes.c_void_p) == 8 else 112
    assert ctypes.sizeof(reader_supervisor.JOBOBJECT_EXTENDED_LIMIT_INFORMATION) == expected
    assert reader_supervisor.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE == 0x2000


def test_windows_spawn_path_uses_no_window_pipe_and_job(tmp_path, monkeypatch):
    """Exercise the win32 branch of _spawn_locked with Popen and the Job Object mocked."""
    fake_proc = MagicMock(pid=4242, _handle=99)
    popen = MagicMock(return_value=fake_proc)
    assign = MagicMock(return_value=True)
    monkeypatch.setattr(reader_supervisor.subprocess, "Popen", popen)
    monkeypatch.setattr(reader_supervisor.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(reader_supervisor, "_create_kill_on_close_job", MagicMock(return_value=1234))
    monkeypatch.setattr(reader_supervisor, "_assign_to_job", assign)
    monkeypatch.setattr(reader_supervisor.sys, "stdout", None)

    sup = ReaderSupervisor(_builder(STDIN_CHILD), tmp_path / "r.pid")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(reader_supervisor.sys, "platform", "win32")
        proc = sup._spawn_locked()

    assert proc is fake_proc
    kwargs = popen.call_args.kwargs
    assert kwargs["stdin"] is subprocess.PIPE
    assert kwargs["creationflags"] == 0x08000000
    assert kwargs["stdout"] is subprocess.DEVNULL and kwargs["stderr"] is subprocess.DEVNULL
    assign.assert_called_once_with(1234, fake_proc)
    assert sup._job == 1234


@pytest.mark.skipif(sys.platform != "win32", reason="Job Objects are Windows-only")
def test_real_job_object_assignment(tmp_path):
    job = reader_supervisor._create_kill_on_close_job()
    assert job is not None
    proc = subprocess.Popen(
        [sys.executable, "-c", STDIN_CHILD],
        stdin=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        assert reader_supervisor._assign_to_job(job, proc) is True
    finally:
        reader_supervisor._close_handle(job)  # KILL_ON_JOB_CLOSE ends the child
        proc.wait(timeout=5)
    assert proc.returncode is not None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only end-to-end")
def test_windows_supervisor_stop_via_stdin(tmp_path):
    sup = ReaderSupervisor(_builder(STDIN_CHILD), tmp_path / "r.pid")
    try:
        sup.start()
        assert _wait_for(sup.is_running)
        sup.stop(timeout_s=3.0)
    finally:
        sup.stop(timeout_s=1.0)
    assert sup.status()["last_exit_code"] == 0
