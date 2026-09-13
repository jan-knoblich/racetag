"""Supervisor for the reader-service child process (plan A1, contract 2.7/4.1).

Responsibilities:

- spawn the child with ``stdin=PIPE`` (the child runs with
  ``--stop-on-stdin-eof``), write the PID file, and on Windows put it into a
  Job Object with ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` so the child dies with
  the shell no matter how the shell ends;
- watch it on a thread and restart it after any exit we did not ask for, with
  back-off (reset once a child stayed up for ``stable_uptime_s``);
- stop it gracefully: close stdin (the child flushes its spool and exits),
  then ``terminate()``, then ``kill()``. On Windows ``terminate()`` is already
  a hard ``TerminateProcess``, which is why the stdin pipe comes first.

The argv builder is called for every spawn, so a restart always picks up the
current persisted configuration.
"""

from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

log = logging.getLogger("racetag.shell.supervisor")

ArgvBuilder = Callable[[], Tuple[List[str], Dict[str, str]]]
EventCallback = Callable[[str, dict], None]

# Grace period for the second escalation step (after terminate()).
_TERMINATE_WAIT_S = 2.0
# The child's stderr capture file is rotated once at spawn time beyond this.
_STDERR_LOG_MAX_BYTES = 1024 * 1024

# ---------------------------------------------------------------------------
# Windows Job Object (ctypes)
#
# Field types are spelled with fixed-size ctypes types instead of
# ctypes.wintypes so the layout is identical (and testable) on every platform:
# DWORD = uint32, LARGE_INTEGER = int64, SIZE_T/ULONG_PTR = size_t,
# HANDLE = void*, BOOL = int.
# ---------------------------------------------------------------------------

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9  # JobObjectExtendedLimitInformation


class IO_COUNTERS(ctypes.Structure):  # noqa: N801 - Win32 name
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):  # noqa: N801
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):  # noqa: N801
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


_kernel32 = None


def _kernel32_dll():
    """kernel32 with typed prototypes. Windows only."""
    global _kernel32
    if _kernel32 is None:
        dll = ctypes.WinDLL("kernel32", use_last_error=True)
        dll.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        dll.CreateJobObjectW.restype = ctypes.c_void_p
        dll.SetInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        dll.SetInformationJobObject.restype = ctypes.c_int
        dll.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        dll.AssignProcessToJobObject.restype = ctypes.c_int
        dll.CloseHandle.argtypes = [ctypes.c_void_p]
        dll.CloseHandle.restype = ctypes.c_int
        _kernel32 = dll
    return _kernel32


def _create_kill_on_close_job() -> Optional[int]:
    """Create an anonymous Job Object that kills its processes when the last
    handle closes (i.e. when this shell process ends for any reason)."""
    k32 = _kernel32_dll()
    job = k32.CreateJobObjectW(None, None)
    if not job:
        log.warning("CreateJobObjectW failed (winerror %s)", ctypes.get_last_error())
        return None
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    ok = k32.SetInformationJobObject(
        job,
        JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
        ctypes.byref(info),
        ctypes.sizeof(info),
    )
    if not ok:
        log.warning("SetInformationJobObject failed (winerror %s)", ctypes.get_last_error())
        k32.CloseHandle(job)
        return None
    return job


def _assign_to_job(job: int, proc: subprocess.Popen) -> bool:
    # Popen._handle is the process handle CreateProcess returned; it carries
    # PROCESS_ALL_ACCESS, which includes the rights AssignProcessToJobObject needs.
    handle = int(proc._handle)  # type: ignore[attr-defined]  # noqa: SLF001
    if not _kernel32_dll().AssignProcessToJobObject(job, handle):
        log.warning(
            "AssignProcessToJobObject failed for pid %s (winerror %s)",
            proc.pid,
            ctypes.get_last_error(),
        )
        return False
    return True


def _close_handle(handle: int) -> None:
    _kernel32_dll().CloseHandle(handle)


# ---------------------------------------------------------------------------
# Supervisor
# ---------------------------------------------------------------------------

class ReaderSupervisor:
    """Keeps exactly one reader-service child alive. Implements ReaderController."""

    def __init__(
        self,
        argv_builder: ArgvBuilder,
        pid_file: Path,
        backoff_s: Sequence[float] = (1, 2, 5, 10, 30),
        on_event: Optional[EventCallback] = None,
        stable_uptime_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
        stderr_log: Optional[Path] = None,
    ) -> None:
        """``stderr_log``: file that receives the child's stderr when the shell
        has no console (windowed exe). The reader-service logs to its own
        file, but an import error or an uncaught traceback before logging is
        set up only ever reaches stderr."""
        if not backoff_s:
            raise ValueError("backoff_s must not be empty")
        self._argv_builder = argv_builder
        self._pid_file = Path(pid_file)
        self._backoff_s = tuple(float(v) for v in backoff_s)
        self._on_event = on_event
        self._stable_uptime_s = stable_uptime_s
        self._clock = clock
        self._stderr_log = Path(stderr_log) if stderr_log is not None else None

        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._proc: Optional[subprocess.Popen] = None
        self._watcher: Optional[threading.Thread] = None
        self._job: Optional[int] = None
        self._stopping = False
        self._restart_requested = False
        self._restart_count = 0
        self._last_exit_code: Optional[int] = None
        self._quick_failures = 0

    # -- public API ---------------------------------------------------------

    def start(self) -> None:
        """Spawn the child and start watching it. No-op if already started."""
        with self._lock:
            if self._watcher is not None and self._watcher.is_alive():
                return
            self._stopping = False
            self._restart_requested = False
            self._quick_failures = 0
            self._watcher = threading.Thread(
                target=self._watch_loop, name="reader-supervisor", daemon=True
            )
            self._watcher.start()

    def stop(self, timeout_s: float = 5.0) -> None:
        """Stop the child gracefully and stop supervising. Idempotent."""
        with self._lock:
            self._stopping = True
            self._cond.notify_all()
            proc = self._proc
            watcher = self._watcher
        if proc is not None:
            self._terminate(proc, timeout_s)
        if watcher is not None and watcher is not threading.current_thread():
            watcher.join(timeout=timeout_s + _TERMINATE_WAIT_S + 1.0)
            if watcher.is_alive():
                log.warning("reader supervisor thread did not finish in time")
        with self._lock:
            self._watcher = None
            job, self._job = self._job, None
        if job is not None:
            # Closing the last handle kills anything still left in the job.
            _close_handle(job)
        self._remove_pid_file()

    def restart(self) -> None:
        """Replace the running child with a fresh one. Returns immediately.

        The new child is built by ``argv_builder``, so it reads the current
        configuration. Ignored when the supervisor is not running.
        """
        with self._lock:
            if self._stopping or self._watcher is None or not self._watcher.is_alive():
                log.info("restart requested while supervisor is not running; ignored")
                return
            if self._restart_requested:
                return  # a restart is already on its way
            self._restart_requested = True
            self._cond.notify_all()  # skip a pending back-off wait
            proc = self._proc
        log.info("reader-service restart requested")
        if proc is not None and proc.poll() is None:
            threading.Thread(
                target=self._terminate,
                args=(proc, 5.0),
                name="reader-supervisor-restart",
                daemon=True,
            ).start()

    def status(self) -> dict:
        """Contract 2.7 keys plus ``restart_pending``.

        ``restart_pending`` is True from an accepted :meth:`restart` until the
        replacement child has been spawned. The old child's final ``stopped``
        heartbeat arrives inside that window, so the UI can show it as
        "wird neu gestartet" instead of an error.
        """
        with self._lock:
            proc = self._proc
            running = proc is not None and proc.poll() is None
            return {
                "running": running,
                "pid": proc.pid if running else None,
                "restart_count": self._restart_count,
                "last_exit_code": self._last_exit_code,
                "restart_pending": self._restart_requested and not self._stopping,
            }

    def is_running(self) -> bool:
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    # -- internals ----------------------------------------------------------

    def _emit(self, name: str, data: dict) -> None:
        if self._on_event is None:
            return
        try:
            self._on_event(name, data)
        except Exception:  # noqa: BLE001 - a broken observer must not stop supervision
            log.exception("on_event callback failed for %s", name)

    def _next_backoff(self, uptime_s: Optional[float]) -> float:
        """Delay before the next spawn after an unexpected exit.

        A child that stayed up for ``stable_uptime_s`` resets the sequence, so a
        single crash after hours of racing restarts after the first step again.
        """
        if uptime_s is not None and uptime_s >= self._stable_uptime_s:
            self._quick_failures = 0
        delay = self._backoff_s[min(self._quick_failures, len(self._backoff_s) - 1)]
        self._quick_failures += 1
        return delay

    def _open_stderr_log(self):
        """Append handle for the child's stderr, rotated to ``.1`` when large."""
        path = self._stderr_log
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size > _STDERR_LOG_MAX_BYTES:
                os.replace(path, path.with_name(path.name + ".1"))
            return open(path, "ab")  # noqa: SIM115 - closed right after Popen
        except OSError:
            log.exception("could not open %s; discarding reader-service stderr", path)
            return None

    def _spawn_locked(self) -> subprocess.Popen:
        """Build argv and start the child. Caller holds ``self._lock``."""
        argv, env = self._argv_builder()
        kwargs: dict = {"stdin": subprocess.PIPE, "env": env}
        stderr_fh = None
        if sys.stdout is None:
            # Windowed shell: there is no console to inherit.
            kwargs["stdout"] = subprocess.DEVNULL
            if self._stderr_log is not None:
                stderr_fh = self._open_stderr_log()
            kwargs["stderr"] = stderr_fh if stderr_fh is not None else subprocess.DEVNULL
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            proc = subprocess.Popen(argv, **kwargs)  # noqa: S603
        finally:
            if stderr_fh is not None:
                stderr_fh.close()  # the child holds its own duplicate
        if sys.platform == "win32":
            if self._job is None:
                self._job = _create_kill_on_close_job()
            if self._job is not None:
                _assign_to_job(self._job, proc)
        return proc

    def _watch_loop(self) -> None:
        first = True
        while True:
            with self._lock:
                if self._stopping:
                    return
                restart_now = self._restart_requested
                self._restart_requested = False
                if not first:
                    self._restart_count += 1
                try:
                    proc = self._spawn_locked()
                except Exception as exc:  # noqa: BLE001 - keep retrying, never die
                    proc = None
                    spawn_error = f"{type(exc).__name__}: {exc}"
                else:
                    spawn_error = None
                    self._proc = proc
            first = False

            if proc is None:
                log.error("could not start reader-service: %s", spawn_error)
                self._emit("spawn_failed", {"error": spawn_error})
                uptime: Optional[float] = None
            else:
                log.info(
                    "reader-service started (pid %s%s): %s",
                    proc.pid,
                    ", after requested restart" if restart_now else "",
                    " ".join(proc.args) if isinstance(proc.args, list) else proc.args,
                )
                self._write_pid_file(proc.pid)
                self._emit("started", {"pid": proc.pid})
                started_at = self._clock()
                code = proc.wait()
                uptime = self._clock() - started_at
                if proc.stdin is not None:
                    try:
                        proc.stdin.close()
                    except (OSError, ValueError):
                        pass
                self._remove_pid_file()
                with self._lock:
                    self._proc = None
                    self._last_exit_code = code
                    requested = self._stopping or self._restart_requested
                log_fn = log.info if requested else log.warning
                log_fn(
                    "reader-service exited with code %s after %.1f s (%s)",
                    code,
                    uptime,
                    "requested" if requested else "unexpected",
                )
                self._emit("exited", {"code": code, "uptime_s": uptime, "requested": requested})

            with self._lock:
                if self._stopping:
                    return
                if self._restart_requested:
                    self._quick_failures = 0
                    continue
                delay = self._next_backoff(uptime)
            log.info("restarting reader-service in %.1f s", delay)
            self._emit("restarting", {"delay_s": delay})
            # Real time on purpose: the injectable clock only measures uptime.
            deadline = time.monotonic() + delay
            with self._lock:
                while not self._stopping and not self._restart_requested:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    self._cond.wait(timeout=remaining)

    def _terminate(self, proc: subprocess.Popen, timeout_s: float) -> None:
        """Close stdin, wait, then terminate(), then kill(). Never raises."""
        if proc.poll() is not None:
            return
        try:
            if proc.stdin is not None and not proc.stdin.closed:
                proc.stdin.close()
        except (OSError, ValueError):
            pass  # child already gone; the wait below returns at once
        try:
            proc.wait(timeout=timeout_s)
            return
        except subprocess.TimeoutExpired:
            log.warning("reader-service pid %s ignored stdin EOF for %.1f s; terminating", proc.pid, timeout_s)
        try:
            proc.terminate()
            proc.wait(timeout=_TERMINATE_WAIT_S)
            return
        except subprocess.TimeoutExpired:
            log.warning("reader-service pid %s did not terminate; killing", proc.pid)
        except OSError:
            return
        try:
            proc.kill()
            proc.wait(timeout=_TERMINATE_WAIT_S)
        except (OSError, subprocess.TimeoutExpired):
            log.exception("could not kill reader-service pid %s", proc.pid)

    def _write_pid_file(self, pid: int) -> None:
        try:
            self._pid_file.parent.mkdir(parents=True, exist_ok=True)
            self._pid_file.write_text(str(pid), encoding="ascii")
        except OSError:
            log.exception("could not write reader-service PID file %s", self._pid_file)

    def _remove_pid_file(self) -> None:
        try:
            self._pid_file.unlink(missing_ok=True)
        except OSError:
            log.exception("could not remove reader-service PID file %s", self._pid_file)
