"""Tests for desktop_logging (plan A3)."""
import logging
import sys

import desktop_logging


def _own_handlers(logger):
    return [h for h in logger.handlers if getattr(h, desktop_logging._MARKER, False)]


def test_setup_logging_without_stdout_writes_files(tmp_path, monkeypatch):
    """A windowed exe has sys.stdout None: no console handler, files still written."""
    monkeypatch.setattr(sys, "stdout", None)
    log_dir = tmp_path / "logs"

    desktop_logging.setup_logging(log_dir)

    root_handlers = _own_handlers(logging.getLogger())
    assert len(root_handlers) == 1
    assert not any(type(h) is logging.StreamHandler for h in root_handlers)
    assert root_handlers[0].maxBytes == 2 * 1024 * 1024
    assert root_handlers[0].backupCount == 5

    logging.getLogger("racetag.shell.test").info("shell line")
    logging.getLogger("uvicorn.error").info("uvicorn line")
    logging.getLogger("racetag.backend").warning("backend line")
    for handler in _own_handlers(logging.getLogger()) + _own_handlers(logging.getLogger("uvicorn")):
        handler.flush()

    shell = (log_dir / "shell.log").read_text(encoding="utf-8")
    backend = (log_dir / "backend.log").read_text(encoding="utf-8")
    assert "shell line" in shell
    assert "uvicorn line" in backend and "backend line" in backend
    # Backend loggers do not propagate into shell.log.
    assert "uvicorn line" not in shell and "backend line" not in shell


def test_setup_logging_with_stdout_adds_console(tmp_path, monkeypatch):
    import io

    stream = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stream)
    desktop_logging.setup_logging(tmp_path / "logs")
    logging.getLogger("racetag.shell.test").info("to console")
    assert "to console" in stream.getvalue()


def test_setup_logging_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "stdout", None)
    desktop_logging.setup_logging(tmp_path / "a")
    desktop_logging.setup_logging(tmp_path / "b")

    assert len(_own_handlers(logging.getLogger())) == 1
    for name in desktop_logging.BACKEND_LOGGERS:
        logger = logging.getLogger(name)
        assert len(_own_handlers(logger)) == 1
        assert logger.propagate is False
    assert all(
        h.baseFilename.startswith(str(tmp_path / "b")) for h in _own_handlers(logging.getLogger())
    )


def test_uvicorn_log_config_is_none():
    assert desktop_logging.uvicorn_log_config() is None
