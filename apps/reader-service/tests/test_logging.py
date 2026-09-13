"""Reader-service logging (plan A3, contract §3.5)."""
from __future__ import annotations

import logging
import logging.handlers
import sys

import pytest

import utils


@pytest.fixture
def restore_logging(monkeypatch):
    yield
    monkeypatch.undo()
    utils.configure_logging()


def _reader_handlers():
    return logging.getLogger("reader").handlers


def _file_handlers():
    return [h for h in _reader_handlers() if isinstance(h, logging.handlers.RotatingFileHandler)]


def _console_handlers():
    return [h for h in _reader_handlers() if type(h) is logging.StreamHandler]


def test_windowed_process_without_stdout_still_logs_to_file(tmp_path, monkeypatch, restore_logging):
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setenv("RACETAG_FILE_LOG", "1")
    monkeypatch.setenv("RACETAG_LOG_DIR", str(tmp_path))

    utils.configure_logging()
    assert _console_handlers() == []
    utils.get_logger("reader.sirit").info("hello from a windowed exe")

    handlers = _file_handlers()
    assert len(handlers) == 1
    handler = handlers[0]
    assert handler.maxBytes == 2 * 1024 * 1024 and handler.backupCount == 5
    content = (tmp_path / "reader.log").read_text(encoding="utf-8")
    assert "[reader.sirit] INFO hello from a windowed exe" in content


def test_one_shared_file_handler_for_all_reader_loggers(tmp_path, monkeypatch, restore_logging):
    monkeypatch.setenv("RACETAG_FILE_LOG", "1")
    monkeypatch.setenv("RACETAG_LOG_DIR", str(tmp_path))
    utils.configure_logging()

    for name in ("reader.sirit", "reader.main", "reader.backend.http", "reader.discovery"):
        utils.get_logger(name).info("line from %s", name)
        assert logging.getLogger(name).handlers == [], f"{name} must not own handlers"
    utils.configure_logging()  # reconfiguring must replace, not add

    assert len(_file_handlers()) == 1
    assert len(_console_handlers()) == 1
    content = (tmp_path / "reader.log").read_text(encoding="utf-8")
    assert content.count("line from") == 4


def test_file_logging_can_be_disabled(tmp_path, monkeypatch, restore_logging):
    monkeypatch.setenv("RACETAG_FILE_LOG", "0")
    monkeypatch.setenv("RACETAG_LOG_DIR", str(tmp_path))
    utils.configure_logging()
    utils.get_logger("reader.sirit").info("not on disk")
    assert _file_handlers() == []
    assert not (tmp_path / "reader.log").exists()


def test_debug_raises_loggers_created_before(monkeypatch, restore_logging):
    early = utils.get_logger("reader.sirit")  # created at import time in production
    utils.configure_logging(debug=False)
    assert not early.isEnabledFor(logging.DEBUG)
    utils.configure_logging(debug=True)
    assert early.isEnabledFor(logging.DEBUG)


def test_stdout_without_working_isatty_is_tolerated(monkeypatch, restore_logging):
    class _Stream:
        def write(self, s):
            return len(s)

        def flush(self):
            pass

        def isatty(self):
            raise ValueError("I/O operation on closed file")

    monkeypatch.setattr(sys, "stdout", _Stream())
    utils.configure_logging()
    assert len(_console_handlers()) == 1
    utils.get_logger("reader.sirit").info("no crash")


def test_env_flag():
    import os
    os.environ["RACETAG_TEST_FLAG"] = " Yes "
    try:
        assert utils.env_flag("RACETAG_TEST_FLAG") is True
        os.environ["RACETAG_TEST_FLAG"] = "0"
        assert utils.env_flag("RACETAG_TEST_FLAG", True) is False
        os.environ["RACETAG_TEST_FLAG"] = ""
        assert utils.env_flag("RACETAG_TEST_FLAG", True) is True
    finally:
        del os.environ["RACETAG_TEST_FLAG"]
    assert utils.env_flag("RACETAG_TEST_FLAG") is False


def test_connect_socket_reports_errors_and_enables_keepalive():
    import socket
    from fake_sirit import reserve_port

    with pytest.raises(utils.ConnectError, match="CONTROL connection refused"):
        utils.connect_socket("127.0.0.1", reserve_port(), "CONTROL", timeout_s=0.5)

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    try:
        sock = utils.connect_socket("127.0.0.1", listener.getsockname()[1], "EVENT", timeout_s=0.5)
        try:
            assert sock.gettimeout() is None
            assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE) != 0
            # Lenient enough to ride out a short outage without closing the
            # sockets (the reader's queued passes survive), where supported.
            for name, expected in (("TCP_KEEPIDLE", 15), ("TCP_KEEPALIVE", 15), ("TCP_KEEPINTVL", 5), ("TCP_KEEPCNT", 8)):
                option = getattr(socket, name, None)
                if option is None:
                    continue
                try:
                    value = sock.getsockopt(socket.IPPROTO_TCP, option)
                except OSError:
                    continue
                assert value == expected, name
        finally:
            sock.close()
    finally:
        listener.close()


def test_colour_codes_never_reach_the_log_file(tmp_path, monkeypatch, restore_logging):
    monkeypatch.setenv("RACETAG_FILE_LOG", "1")
    monkeypatch.setenv("RACETAG_LOG_DIR", str(tmp_path))
    utils.configure_logging()
    utils.get_logger("reader.sirit").info("[EVENT] [%s] tag", utils._color("ARRIVE", utils._C.GREEN))
    content = (tmp_path / "reader.log").read_text(encoding="utf-8")
    assert "[EVENT] [ARRIVE] tag" in content
    assert "\x1b" not in content
