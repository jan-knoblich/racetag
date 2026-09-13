"""Tests for native.py: the cross-platform testable parts (plan A2, A5, D6, E2)."""
import ctypes
import sys
import threading
from unittest.mock import MagicMock

import pytest

import native


# ---------------------------------------------------------------------------
# open_path / open_url dispatch
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "platform_name, expected_cmd",
    [("darwin", "open"), ("linux", "xdg-open")],
)
def test_open_path_dispatch_posix(monkeypatch, tmp_path, platform_name, expected_cmd):
    popen = MagicMock()
    monkeypatch.setattr(native.sys, "platform", platform_name)
    monkeypatch.setattr(native.subprocess, "Popen", popen)

    assert native.open_path(tmp_path) is True
    popen.assert_called_once_with([expected_cmd, str(tmp_path)])


def test_open_path_dispatch_windows(monkeypatch, tmp_path):
    startfile = MagicMock()
    monkeypatch.setattr(native.sys, "platform", "win32")
    monkeypatch.setattr(native.os, "startfile", startfile, raising=False)

    assert native.open_path(tmp_path) is True
    startfile.assert_called_once_with(str(tmp_path))


def test_open_url_uses_same_dispatch(monkeypatch):
    popen = MagicMock()
    monkeypatch.setattr(native.sys, "platform", "darwin")
    monkeypatch.setattr(native.subprocess, "Popen", popen)

    assert native.open_url("https://example.org") is True
    popen.assert_called_once_with(["open", "https://example.org"])


def test_open_path_returns_false_on_error(monkeypatch, tmp_path):
    monkeypatch.setattr(native.sys, "platform", "linux")
    monkeypatch.setattr(native.subprocess, "Popen", MagicMock(side_effect=FileNotFoundError("xdg-open")))
    assert native.open_path(tmp_path) is False


# ---------------------------------------------------------------------------
# Dialogs
# ---------------------------------------------------------------------------

def test_message_box_disabled_does_nothing(monkeypatch):
    run = MagicMock()
    monkeypatch.setattr(native.subprocess, "run", run)
    native.message_box("Titel", "Text", "error")  # RACETAG_NO_DIALOGS=1 from conftest
    run.assert_not_called()


def test_message_box_darwin_passes_texts_as_argv(monkeypatch):
    monkeypatch.delenv("RACETAG_NO_DIALOGS")
    monkeypatch.setattr(native.sys, "platform", "darwin")
    run = MagicMock(return_value=MagicMock(returncode=0, stdout=""))
    monkeypatch.setattr(native.subprocess, "run", run)

    native.message_box("Racetag läuft bereits", 'Text mit "Anführungszeichen"', "warning")

    cmd = run.call_args.args[0]
    assert cmd[0] == "osascript"
    assert cmd[-2:] == ["Racetag läuft bereits", 'Text mit "Anführungszeichen"']
    assert any("as warning" in part for part in cmd)


def test_message_box_never_raises(monkeypatch):
    monkeypatch.delenv("RACETAG_NO_DIALOGS")
    monkeypatch.setattr(native.sys, "platform", "darwin")
    monkeypatch.setattr(native.subprocess, "run", MagicMock(side_effect=OSError("no osascript")))
    native.message_box("Titel", "Text")


def test_message_box_without_gui_platform_only_logs(monkeypatch):
    monkeypatch.delenv("RACETAG_NO_DIALOGS")
    monkeypatch.setattr(native.sys, "platform", "linux")
    run = MagicMock()
    monkeypatch.setattr(native.subprocess, "run", run)
    native.message_box("Titel", "Text", "info")
    run.assert_not_called()


@pytest.mark.parametrize("stdout, expected", [("button returned:Ja\n", True), ("button returned:Nein\n", False)])
def test_ask_yes_no_darwin(monkeypatch, stdout, expected):
    monkeypatch.delenv("RACETAG_NO_DIALOGS")
    monkeypatch.setattr(native.sys, "platform", "darwin")
    monkeypatch.setattr(
        native.subprocess, "run", MagicMock(return_value=MagicMock(returncode=0, stdout=stdout))
    )
    assert native.ask_yes_no("Frage", "Neu starten?") is expected


def test_ask_yes_no_disabled_returns_false():
    assert native.ask_yes_no("Frage", "Neu starten?") is False


# ---------------------------------------------------------------------------
# Crash hook
# ---------------------------------------------------------------------------

def test_install_excepthook_writes_crash_log_and_shows_dialog_once(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)
    monkeypatch.setattr(native, "_crash_dialog_shown", False)
    box = MagicMock()
    monkeypatch.setattr(native, "message_box", box)

    native.install_excepthook(tmp_path)
    for message in ("first boom", "second boom"):
        try:
            raise RuntimeError(message)
        except RuntimeError:
            sys.excepthook(*sys.exc_info())

    crash = (tmp_path / "crash.log").read_text(encoding="utf-8")
    assert "first boom" in crash and "second boom" in crash
    box.assert_called_once()
    title, text, kind = box.call_args.args
    assert kind == "error"
    assert str(tmp_path / "crash.log") in text
    assert "Unerwarteter Fehler" in title


def test_thread_excepthook_reports_and_ignores_system_exit(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)
    monkeypatch.setattr(native, "message_box", MagicMock())
    monkeypatch.setattr(native, "_crash_dialog_shown", False)
    native.install_excepthook(tmp_path)

    def boom():
        raise ValueError("thread boom")

    def quiet_exit():
        raise SystemExit(0)

    for target in (boom, quiet_exit):
        t = threading.Thread(target=target, name=f"t-{target.__name__}")
        t.start()
        t.join(timeout=2)

    crash = (tmp_path / "crash.log").read_text(encoding="utf-8")
    assert "thread boom" in crash and "t-boom" in crash
    assert "t-quiet_exit" not in crash


# ---------------------------------------------------------------------------
# Environment checks
# ---------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform == "win32", reason="non-Windows behaviour")
def test_webview2_version_off_windows():
    assert native.webview2_version() == "n/a"


@pytest.mark.skipif(sys.platform != "win32", reason="reads the Windows registry")
def test_webview2_version_on_windows_returns_version_or_none():
    value = native.webview2_version()
    assert value is None or value[0].isdigit()


def test_parse_version_and_minimum():
    assert native._parse_version("120.0.2210.91") == (120, 0, 2210, 91)
    assert native._parse_version("garbage") is None
    assert native._parse_version("86.0.600.0") < native.WEBVIEW2_MIN_VERSION
    assert native._parse_version("86.0.622.0") >= native.WEBVIEW2_MIN_VERSION


def test_free_disk_bytes_uses_nearest_existing_parent(tmp_path):
    assert native.free_disk_bytes(tmp_path / "does" / "not" / "exist") > 0


@pytest.mark.skipif(sys.platform == "win32", reason="non-Windows behaviour")
def test_focus_existing_window_off_windows():
    assert native.focus_existing_window() is False


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 API")
def test_focus_existing_window_without_window_returns_false():
    assert native.focus_existing_window("Racetag-no-such-window-7f3a") is False


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 API")
def test_user32_prototypes_are_typed():
    user32 = native._user32_dll()
    for name in ("MessageBoxW", "FindWindowW", "IsIconic", "ShowWindow", "SetForegroundWindow", "IsWindowVisible"):
        fn = getattr(user32, name)
        assert fn.argtypes is not None and fn.restype is not None


def _fake_user32(monkeypatch, hwnd, visible):
    user32 = MagicMock()
    user32.FindWindowW.return_value = hwnd
    user32.IsWindowVisible.return_value = visible
    user32.IsIconic.return_value = 0
    user32.SetForegroundWindow.return_value = 1
    monkeypatch.setattr(native, "_user32", user32)
    monkeypatch.setattr(native.sys, "platform", "win32")
    return user32


def test_focus_existing_window_ignores_hidden_closing_window(monkeypatch):
    """pywebview hides the window first and cleans up WebView2 afterwards:
    that window is not something the operator can use."""
    user32 = _fake_user32(monkeypatch, hwnd=0x1234, visible=0)
    assert native.focus_existing_window("Racetag") is False
    user32.SetForegroundWindow.assert_not_called()


def test_focus_existing_window_focuses_visible_window(monkeypatch):
    user32 = _fake_user32(monkeypatch, hwnd=0x1234, visible=1)
    assert native.focus_existing_window("Racetag") is True
    user32.SetForegroundWindow.assert_called_once_with(0x1234)


def test_focus_existing_window_without_match(monkeypatch):
    _fake_user32(monkeypatch, hwnd=None, visible=1)
    assert native.focus_existing_window("Racetag") is False


# ---------------------------------------------------------------------------
# Desktop folder
# ---------------------------------------------------------------------------

def test_guid_layout_matches_folderid_desktop():
    guid = native._guid(native._FOLDERID_DESKTOP)
    assert ctypes.sizeof(native.GUID) == 16
    # Little-endian GUID byte order of {B4BFCC3A-DB2C-424C-B029-7FE99A87C641}.
    assert bytes(guid) == bytes.fromhex("3accbfb42cdb4c42b0297fe99a87c641")


@pytest.fixture()
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(native.Path, "home", classmethod(lambda cls: home))
    monkeypatch.delenv("USERPROFILE", raising=False)
    return home


def test_desktop_dir_windows_follows_onedrive_redirection(fake_home, monkeypatch):
    (fake_home / "Desktop").mkdir()  # stale local folder next to the redirected one
    onedrive = fake_home / "OneDrive" / "Desktop"
    onedrive.mkdir(parents=True)
    monkeypatch.setattr(native.sys, "platform", "win32")
    monkeypatch.setattr(native, "_known_folder_desktop", lambda: onedrive)
    monkeypatch.setattr(native, "_registry_desktop", MagicMock(side_effect=AssertionError("not needed")))
    assert native.desktop_dir() == onedrive


def test_desktop_dir_windows_falls_back_to_registry_then_home(fake_home, monkeypatch):
    onedrive = fake_home / "OneDrive" / "Desktop"
    onedrive.mkdir(parents=True)
    monkeypatch.setattr(native.sys, "platform", "win32")
    monkeypatch.setattr(native, "_known_folder_desktop", MagicMock(side_effect=OSError("no shell32")))
    monkeypatch.setattr(native, "_registry_desktop", lambda: onedrive)
    assert native.desktop_dir() == onedrive

    monkeypatch.setattr(native, "_registry_desktop", lambda: None)
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    assert native.desktop_dir() == fake_home, "never an empty path (pywebview would use HOMEPATH)"


def test_desktop_dir_off_windows(fake_home, monkeypatch):
    monkeypatch.setattr(native.sys, "platform", "darwin")
    assert native.desktop_dir() == fake_home
    (fake_home / "Desktop").mkdir()
    assert native.desktop_dir() == fake_home / "Desktop"


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 API")
def test_desktop_dir_on_windows_is_a_directory():
    assert native._known_folder_desktop().is_dir()
    assert native.desktop_dir().is_dir()


# ---------------------------------------------------------------------------
# Sleep prevention
# ---------------------------------------------------------------------------

def test_keep_system_awake_windows_sets_and_resets_execution_state(monkeypatch):
    kernel32 = MagicMock()
    kernel32.SetThreadExecutionState.return_value = native.ES_CONTINUOUS
    monkeypatch.setattr(native, "_kernel32", kernel32)
    monkeypatch.setattr(native.sys, "platform", "win32")

    assert native.keep_system_awake(True) is True
    assert native.keep_system_awake(False) is True

    flags = [c.args[0] for c in kernel32.SetThreadExecutionState.call_args_list]
    assert flags == [
        native.ES_CONTINUOUS | native.ES_SYSTEM_REQUIRED | native.ES_DISPLAY_REQUIRED,
        native.ES_CONTINUOUS,
    ]


def test_keep_system_awake_windows_failure_never_raises(monkeypatch):
    kernel32 = MagicMock()
    kernel32.SetThreadExecutionState.return_value = 0
    monkeypatch.setattr(native, "_kernel32", kernel32)
    monkeypatch.setattr(native.sys, "platform", "win32")
    assert native.keep_system_awake(True) is False

    kernel32.SetThreadExecutionState.side_effect = OSError("boom")
    assert native.keep_system_awake(True) is False


def test_keep_system_awake_darwin_uses_caffeinate_bound_to_this_process(monkeypatch):
    proc = MagicMock()
    proc.poll.return_value = None
    popen = MagicMock(return_value=proc)
    monkeypatch.setattr(native.subprocess, "Popen", popen)
    monkeypatch.setattr(native, "_caffeinate_proc", None)
    monkeypatch.setattr(native.sys, "platform", "darwin")

    assert native.keep_system_awake(True) is True
    assert native.keep_system_awake(True) is True  # no second caffeinate
    popen.assert_called_once()
    cmd = popen.call_args.args[0]
    assert cmd[0].endswith("caffeinate") and "-i" in cmd and cmd[-2:] == ["-w", str(native.os.getpid())]

    assert native.keep_system_awake(False) is True
    proc.terminate.assert_called_once_with()
    assert native._caffeinate_proc is None


def test_keep_system_awake_other_platforms_is_a_noop(monkeypatch):
    monkeypatch.setattr(native.sys, "platform", "linux")
    assert native.keep_system_awake(True) is False


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 API")
def test_keep_system_awake_real_windows_call():
    try:
        assert native.keep_system_awake(True) is True
    finally:
        assert native.keep_system_awake(False) is True
