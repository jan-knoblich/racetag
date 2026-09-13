"""Native OS helpers for the desktop shell (plan A2, A5, D6, E2).

Everything an operator can see here is German; log lines stay English.

Windows calls go through ``ctypes`` with explicit ``argtypes``/``restype`` so
64-bit handles are never truncated to C ``int``. The DLL bindings are created
lazily (``ctypes.WinDLL`` does not exist on other platforms), which keeps the
module importable everywhere and in tests.

Set ``RACETAG_NO_DIALOGS=1`` to log dialogs instead of showing them (CI and the
test-suite; a modal ``MessageBoxW`` on a headless runner would block forever).
"""

from __future__ import annotations

import ctypes
import datetime as _dt
import logging
import os
import shutil
import subprocess
import sys
import threading
import traceback
from pathlib import Path
from typing import Optional, Tuple

log = logging.getLogger("racetag.shell.native")

WEBVIEW2_CLIENT_ID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
WEBVIEW2_DOWNLOAD_URL = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
# pywebview's edgechromium backend silently falls back to the legacy MSHTML
# (IE) renderer below this runtime build, which renders the frontend broken.
WEBVIEW2_MIN_VERSION: Tuple[int, ...] = (86, 0, 622, 0)

# MessageBoxW flags / return codes (winuser.h).
MB_OK = 0x00000000
MB_YESNO = 0x00000004
MB_ICONERROR = 0x00000010
MB_ICONQUESTION = 0x00000020
MB_ICONWARNING = 0x00000030
MB_ICONINFORMATION = 0x00000040
MB_SETFOREGROUND = 0x00010000
MB_TOPMOST = 0x00040000
IDYES = 6
SW_RESTORE = 9

# SetThreadExecutionState flags (winbase.h).
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002

# FOLDERID_Desktop (knownfolders.h): {B4BFCC3A-DB2C-424C-B029-7FE99A87C641}.
_FOLDERID_DESKTOP = (0xB4BFCC3A, 0xDB2C, 0x424C, (0xB0, 0x29, 0x7F, 0xE9, 0x9A, 0x87, 0xC6, 0x41))
_USER_SHELL_FOLDERS_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"

_KIND_TO_ICON = {
    "error": MB_ICONERROR,
    "warning": MB_ICONWARNING,
    "info": MB_ICONINFORMATION,
}
_KIND_TO_OSASCRIPT = {
    "error": "critical",
    "warning": "warning",
    "info": "informational",
}

# osascript blocks until the operator clicks. The cap only protects against a
# hung AppleScript host, it is not meant to auto-dismiss a dialog.
_OSASCRIPT_TIMEOUT_S = 600


def _dialogs_disabled() -> bool:
    return os.environ.get("RACETAG_NO_DIALOGS", "").strip() in {"1", "true", "yes"}


# ---------------------------------------------------------------------------
# Windows bindings (lazy)
# ---------------------------------------------------------------------------

_user32 = None


def _user32_dll():
    """Return user32 with typed prototypes. Windows only."""
    global _user32
    if _user32 is None:
        from ctypes import wintypes  # noqa: PLC0415

        dll = ctypes.WinDLL("user32", use_last_error=True)
        dll.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
        dll.MessageBoxW.restype = ctypes.c_int
        dll.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        dll.FindWindowW.restype = wintypes.HWND
        dll.IsIconic.argtypes = [wintypes.HWND]
        dll.IsIconic.restype = wintypes.BOOL
        dll.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        dll.ShowWindow.restype = wintypes.BOOL
        dll.SetForegroundWindow.argtypes = [wintypes.HWND]
        dll.SetForegroundWindow.restype = wintypes.BOOL
        dll.IsWindowVisible.argtypes = [wintypes.HWND]
        dll.IsWindowVisible.restype = wintypes.BOOL
        _user32 = dll
    return _user32


_kernel32 = None


def _kernel32_dll():
    """Return kernel32 with typed prototypes. Windows only."""
    global _kernel32
    if _kernel32 is None:
        dll = ctypes.WinDLL("kernel32", use_last_error=True)
        dll.SetThreadExecutionState.argtypes = [ctypes.c_uint32]
        dll.SetThreadExecutionState.restype = ctypes.c_uint32
        _kernel32 = dll
    return _kernel32


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def _guid(parts) -> GUID:
    d1, d2, d3, d4 = parts
    return GUID(d1, d2, d3, (ctypes.c_ubyte * 8)(*d4))


# ---------------------------------------------------------------------------
# Dialogs
# ---------------------------------------------------------------------------

def _osascript(lines: Tuple[str, ...], args: Tuple[str, ...]) -> subprocess.CompletedProcess:
    """Run AppleScript with the texts passed as argv, so no quoting is needed."""
    cmd = ["osascript"]
    for line in ("on run argv",) + lines + ("end run",):
        cmd += ["-e", line]
    return subprocess.run(
        cmd + list(args),
        capture_output=True,
        text=True,
        timeout=_OSASCRIPT_TIMEOUT_S,
        check=False,
    )


def message_box(title: str, text: str, kind: str = "error") -> None:
    """Show a modal message box. Never raises."""
    log_fn = log.error if kind == "error" else log.warning if kind == "warning" else log.info
    log_fn("dialog [%s]: %s", title, text.replace("\n", " "))
    if _dialogs_disabled():
        return
    try:
        if sys.platform == "win32":
            flags = MB_OK | _KIND_TO_ICON.get(kind, MB_ICONERROR) | MB_TOPMOST | MB_SETFOREGROUND
            _user32_dll().MessageBoxW(None, text, title, flags)
        elif sys.platform == "darwin":
            style = _KIND_TO_OSASCRIPT.get(kind, "critical")
            _osascript(
                (f"display alert (item 1 of argv) message (item 2 of argv) as {style}",),
                (title, text),
            )
    except Exception:  # noqa: BLE001 - a failed dialog must never take the app down
        log.exception("message_box failed")


def ask_yes_no(title: str, text: str) -> bool:
    """Ask a Ja/Nein question. Returns False when no dialog can be shown."""
    log.warning("question [%s]: %s", title, text.replace("\n", " "))
    if _dialogs_disabled():
        return False
    try:
        if sys.platform == "win32":
            flags = MB_YESNO | MB_ICONQUESTION | MB_TOPMOST | MB_SETFOREGROUND
            answer = _user32_dll().MessageBoxW(None, text, title, flags) == IDYES
        elif sys.platform == "darwin":
            result = _osascript(
                (
                    'display dialog (item 2 of argv) with title (item 1 of argv) '
                    'buttons {"Nein", "Ja"} default button "Ja" with icon caution',
                ),
                (title, text),
            )
            # "Nein" is a normal button (returncode 0); only a cancelled or
            # failed dialog returns non-zero.
            answer = result.returncode == 0 and "button returned:Ja" in result.stdout
        else:
            return False
    except Exception:  # noqa: BLE001
        log.exception("ask_yes_no failed")
        return False
    log.info("answer [%s]: %s", title, "yes" if answer else "no")
    return answer


# ---------------------------------------------------------------------------
# Opening files, folders and URLs
# ---------------------------------------------------------------------------

def _launch(target: str) -> bool:
    try:
        if sys.platform == "win32":
            os.startfile(target)  # type: ignore[attr-defined]  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", target])  # noqa: S603, S607
        else:
            subprocess.Popen(["xdg-open", target])  # noqa: S603, S607
    except Exception:  # noqa: BLE001
        log.exception("could not open %s", target)
        return False
    return True


def open_path(path) -> bool:
    """Open a file or folder with the OS default handler (Explorer, Finder)."""
    return _launch(str(path))


def open_url(url: str) -> bool:
    """Open a URL in the default browser."""
    return _launch(url)


# ---------------------------------------------------------------------------
# Crash handling
# ---------------------------------------------------------------------------

_crash_lock = threading.Lock()
_crash_dialog_shown = False


def _crash_text(crash_log: Path) -> str:
    return (
        "Racetag ist auf einen unerwarteten Fehler gestoßen.\n\n"
        f"Die Details wurden gespeichert in:\n{crash_log}\n\n"
        "Bitte Racetag neu starten. Tritt der Fehler wieder auf, in den "
        "Einstellungen „Support-Paket erstellen“ wählen und das Paket an den "
        "Support schicken."
    )


def report_crash(exc_type, exc, tb, log_dir: Path, where: str = "main thread") -> Path:
    """Append the traceback to ``crash.log`` and show one German dialog.

    Only the first crash of a process opens a dialog; later ones are still
    written to the log, so a crash loop cannot bury the operator in dialogs.
    """
    global _crash_dialog_shown
    crash_log = Path(log_dir) / "crash.log"
    formatted = "".join(traceback.format_exception(exc_type, exc, tb))
    stamp = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        crash_log.parent.mkdir(parents=True, exist_ok=True)
        with open(crash_log, "a", encoding="utf-8") as fh:
            fh.write(f"===== {stamp} unhandled exception in {where} =====\n{formatted}\n")
    except OSError:
        log.exception("could not write %s", crash_log)
    log.critical("unhandled exception in %s:\n%s", where, formatted)

    with _crash_lock:
        show = not _crash_dialog_shown
        _crash_dialog_shown = True
    if show:
        message_box("Racetag – Unerwarteter Fehler", _crash_text(crash_log), "error")
    return crash_log


def install_excepthook(log_dir: Path) -> None:
    """Route uncaught exceptions (main and worker threads) to :func:`report_crash`."""
    log_dir = Path(log_dir)

    def _hook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        report_crash(exc_type, exc, tb, log_dir, "main thread")

    def _thread_hook(args):
        if args.exc_type is SystemExit:
            return
        name = args.thread.name if args.thread is not None else "unknown thread"
        report_crash(args.exc_type, args.exc_value, args.exc_traceback, log_dir, f"thread {name}")

    sys.excepthook = _hook
    threading.excepthook = _thread_hook


# ---------------------------------------------------------------------------
# Environment checks
# ---------------------------------------------------------------------------

def _parse_version(raw: str) -> Optional[Tuple[int, ...]]:
    try:
        return tuple(int(p) for p in raw.strip().split("."))
    except ValueError:
        return None


def webview2_version() -> Optional[str]:
    """Installed Evergreen WebView2 runtime version.

    Returns ``"n/a"`` off Windows, ``None`` when the runtime is missing or too
    old for pywebview's edgechromium backend.
    """
    if sys.platform != "win32":
        return "n/a"
    import winreg  # noqa: PLC0415

    locations = (
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT_ID}"),
        (winreg.HKEY_CURRENT_USER, rf"Software\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT_ID}"),
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT_ID}"),
    )
    for hive, key_path in locations:
        try:
            with winreg.OpenKey(hive, key_path) as key:
                value, _ = winreg.QueryValueEx(key, "pv")
        except OSError:
            continue
        parsed = _parse_version(str(value))
        if parsed is None or not any(parsed):
            continue  # "0.0.0.0" marks an uninstalled runtime
        if parsed >= WEBVIEW2_MIN_VERSION:
            return str(value).strip()
        log.warning("WebView2 runtime %s is older than %s", value, WEBVIEW2_MIN_VERSION)
    return None


def free_disk_bytes(path) -> int:
    """Free bytes on the volume holding ``path`` (nearest existing parent)."""
    p = Path(path).resolve()
    while not p.exists() and p.parent != p:
        p = p.parent
    return shutil.disk_usage(p).free


def focus_existing_window(title: str = "Racetag") -> bool:
    """Bring an already open Racetag window to the front. Windows only.

    Returns False when no *visible* window with that title exists.
    """
    if sys.platform != "win32":
        return False
    try:
        user32 = _user32_dll()
        hwnd = user32.FindWindowW(None, title)
        if not hwnd:
            return False
        if not user32.IsWindowVisible(hwnd):
            # A closing pywebview window is hidden first and then lingers
            # while WebView2 cleans up: there is nothing to show the operator.
            log.info("found a hidden %r window; treating it as not open", title)
            return False
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        return bool(user32.SetForegroundWindow(hwnd))
    except Exception:  # noqa: BLE001
        log.exception("focus_existing_window failed")
        return False


# ---------------------------------------------------------------------------
# Desktop folder
# ---------------------------------------------------------------------------

def _known_folder_desktop() -> Optional[Path]:
    """The shell's Desktop via SHGetKnownFolderPath (follows OneDrive redirection)."""
    shell32 = ctypes.WinDLL("shell32")
    ole32 = ctypes.WinDLL("ole32")
    fn = shell32.SHGetKnownFolderPath
    fn.argtypes = [ctypes.POINTER(GUID), ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    fn.restype = ctypes.c_long  # HRESULT
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole32.CoTaskMemFree.restype = None
    folder_id = _guid(_FOLDERID_DESKTOP)
    buf = ctypes.c_void_p()
    hr = fn(ctypes.byref(folder_id), 0, None, ctypes.byref(buf))
    try:
        if hr != 0 or not buf.value:
            log.warning("SHGetKnownFolderPath(Desktop) failed (hresult 0x%08X)", hr & 0xFFFFFFFF)
            return None
        return Path(ctypes.wstring_at(buf.value))
    finally:
        if buf.value:
            ole32.CoTaskMemFree(buf.value)


def _registry_desktop() -> Optional[Path]:
    """The Desktop entry of HKCU ... Explorer\\User Shell Folders, env vars expanded."""
    import winreg  # noqa: PLC0415

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _USER_SHELL_FOLDERS_KEY) as key:
            value, _ = winreg.QueryValueEx(key, "Desktop")
    except OSError:
        return None
    expanded = os.path.expandvars(str(value)).strip()
    return Path(expanded) if expanded else None


def desktop_dir() -> Path:
    """The folder Explorer / Finder shows as the Desktop. Never raises.

    On Windows the Desktop may be redirected (OneDrive Known Folder Move puts
    it under ``%USERPROFILE%\\OneDrive\\Desktop``), so ``~/Desktop`` is only a
    fallback. Always returns an existing directory when one can be found and
    never an empty path: pywebview replaces an empty dialog directory with
    the drive-less ``%HOMEPATH%``.
    """
    def candidates():
        if sys.platform == "win32":
            for lookup in (_known_folder_desktop, _registry_desktop):
                try:
                    found = lookup()
                except Exception:  # noqa: BLE001 - fall through to the next source
                    log.exception("desktop lookup %s failed", getattr(lookup, "__name__", lookup))
                    found = None
                if found is not None:
                    yield found
        yield Path.home() / "Desktop"
        profile = os.environ.get("USERPROFILE")
        if sys.platform == "win32" and profile:
            yield Path(profile)

    try:
        for candidate in candidates():
            try:
                if candidate.is_dir():
                    return candidate
            except OSError:
                continue
        return Path.home()
    except RuntimeError:  # Path.home() without any home directory
        log.exception("no home directory for the desktop lookup")
        return Path(os.environ.get("USERPROFILE") or os.getcwd())


# ---------------------------------------------------------------------------
# Keeping the computer awake during a race
# ---------------------------------------------------------------------------

_caffeinate_proc: Optional[subprocess.Popen] = None


def keep_system_awake(enabled: bool) -> bool:
    """Stop (or allow again) idle sleep and display timeout. Never raises.

    Windows: ``SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED |
    ES_DISPLAY_REQUIRED)``. The request belongs to the calling thread, so
    call it from a thread that lives as long as the app (the main thread)
    and reset it from the same thread. The display is kept on as well: on
    Modern Standby laptops a display timeout alone sends the PC into standby.

    macOS: ``caffeinate -d -i -w <pid>``, which also ends with this process.

    A closed laptop lid still sleeps; no API prevents that.
    """
    global _caffeinate_proc
    try:
        if sys.platform == "win32":
            flags = ES_CONTINUOUS | ((ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED) if enabled else 0)
            if not _kernel32_dll().SetThreadExecutionState(flags):
                log.warning("SetThreadExecutionState(0x%08X) failed", flags)
                return False
        elif sys.platform == "darwin":
            if enabled:
                if _caffeinate_proc is None or _caffeinate_proc.poll() is not None:
                    _caffeinate_proc = subprocess.Popen(  # noqa: S603
                        ["/usr/bin/caffeinate", "-d", "-i", "-w", str(os.getpid())],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
            else:
                proc, _caffeinate_proc = _caffeinate_proc, None
                if proc is not None and proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        proc.kill()
        else:
            return False
    except Exception:  # noqa: BLE001 - sleep prevention must never break startup or shutdown
        log.exception("keep_system_awake(%s) failed", enabled)
        return False
    log.info("sleep prevention %s", "on" if enabled else "off")
    return True
