"""Tests for selftest.run_selftest (plan A4, contract 4.3).

The full end-to-end run is ``python app.py --selftest``; here only the
harness behaviour is checked with a fake desktop module, so no server or
child process is started.
"""
import os
import shutil
import socket
from pathlib import Path
from types import SimpleNamespace

import selftest


class _Handle:
    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True


def test_failing_step_returns_1_logs_and_isolates_env(tmp_path, monkeypatch):
    log_dir = tmp_path / "logs"
    monkeypatch.setenv("RACETAG_LOG_DIR", str(log_dir))
    monkeypatch.setenv("RACETAG_API_KEY", "secret")
    monkeypatch.setenv("READER_IP", "10.1.1.1")
    monkeypatch.setenv("RACETAG_VERSION", "placeholder")
    monkeypatch.delenv("RACETAG_VERSION")

    def broken_build():
        raise RuntimeError("backend import broke")

    fake_desktop = SimpleNamespace(
        _read_version=lambda: "0.0.1",
        _ServerHandle=_Handle,
        _build_combined_app=broken_build,
        _pick_free_port=lambda: 1,
    )

    try:
        assert selftest.run_selftest(fake_desktop) == 1

        data_dir = Path(os.environ["RACETAG_DATA_DIR"])
        assert data_dir.name == "data"
        assert data_dir.parent.name.startswith("racetag-selftest-")
        assert ".racetag" not in data_dir.parts
        assert not data_dir.exists(), "temp data dir is removed afterwards"
        assert "RACETAG_API_KEY" not in os.environ
        assert "READER_IP" not in os.environ
        assert os.environ["RACETAG_VERSION"] == "0.0.1"
        assert os.environ["RACETAG_NO_DIALOGS"] == "1"

        text = (log_dir / "selftest.log").read_text(encoding="utf-8")
        assert "FAIL build app and start server" in text
        assert "backend import broke" in text
        assert "self-test FAILED" in text
        assert "PASS" not in text
    finally:
        shutil.rmtree(Path(os.environ["RACETAG_DATA_DIR"]).parent, ignore_errors=True)


def test_closed_port_has_no_listener():
    port = selftest._closed_port()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        assert s.connect_ex(("127.0.0.1", port)) != 0
