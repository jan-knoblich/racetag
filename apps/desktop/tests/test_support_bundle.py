"""Tests for support_bundle (plan D6)."""
import datetime as dt
import json
import sqlite3
import zipfile

import pytest

import support_bundle


def _make_db(path, wal=True):
    conn = sqlite3.connect(str(path))
    if wal:
        conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE riders (tag TEXT, name TEXT)")
    conn.executemany("INSERT INTO riders VALUES (?, ?)", [("A1", "Anna"), ("B2", "Bernd")])
    conn.commit()
    return conn


def test_default_bundle_name():
    assert (
        support_bundle.default_bundle_name(dt.datetime(2026, 9, 13, 7, 5))
        == "Racetag-Support-20260913-0705.zip"
    )


def test_bundle_contains_logs_db_copy_and_json(tmp_path):
    data_dir = tmp_path / "data"
    log_dir = tmp_path / "logs"
    data_dir.mkdir()
    log_dir.mkdir()
    (log_dir / "shell.log").write_text("shell\n", encoding="utf-8")
    (log_dir / "backend.log.1").write_text("rotated\n", encoding="utf-8")
    (log_dir / "subdir").mkdir()
    # Keep the source connection open with uncommitted-to-main-file WAL
    # content, like the running backend does.
    live = _make_db(data_dir / "racetag.db")
    dest = tmp_path / "out" / "bundle.zip"
    try:
        result = support_bundle.build_support_bundle(
            dest,
            data_dir,
            log_dir,
            {"config": {"reader_ip": "192.168.178.22"}, "app_info": {"version": "0.1.0", "when": dt.date(2026, 9, 13)}},
        )
    finally:
        live.close()

    assert result == dest and dest.is_file()
    with zipfile.ZipFile(dest) as zf:
        names = set(zf.namelist())
        assert {"logs/shell.log", "logs/backend.log.1", "racetag.db", "config.json", "app_info.json", "notes.txt"} <= names
        assert not any(n.startswith("logs/subdir") for n in names)
        assert json.loads(zf.read("config.json")) == {"reader_ip": "192.168.178.22"}
        assert json.loads(zf.read("app_info.json"))["when"] == "2026-09-13"
        assert "All parts included." in zf.read("notes.txt").decode("utf-8")
        zf.extract("racetag.db", tmp_path / "extracted")

    copy = sqlite3.connect(str(tmp_path / "extracted" / "racetag.db"))
    try:
        rows = copy.execute("SELECT tag, name FROM riders ORDER BY tag").fetchall()
    finally:
        copy.close()
    assert rows == [("A1", "Anna"), ("B2", "Bernd")]
    assert not list(dest.parent.glob("*.part")), "temporary file must be renamed away"


def test_missing_db_is_noted(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    dest = tmp_path / "bundle.zip"
    support_bundle.build_support_bundle(dest, tmp_path / "data", log_dir, {})
    with zipfile.ZipFile(dest) as zf:
        assert "racetag.db" not in zf.namelist()
        notes = zf.read("notes.txt").decode("utf-8")
    assert "not found" in notes


def test_corrupt_db_is_noted_not_fatal(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "racetag.db").write_bytes(b"this is not a sqlite database" * 100)
    dest = tmp_path / "bundle.zip"
    support_bundle.build_support_bundle(dest, data_dir, tmp_path / "missing-logs", {"x": 1})
    with zipfile.ZipFile(dest) as zf:
        notes = zf.read("notes.txt").decode("utf-8")
        assert "x.json" in zf.namelist()
    assert "database copy failed" in notes
    assert "log directory" in notes


def test_bundle_inside_log_dir_does_not_include_itself(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "reader.log").write_text("r\n", encoding="utf-8")
    dest = log_dir / "bundle.zip"
    support_bundle.build_support_bundle(dest, tmp_path / "data", log_dir, {})
    with zipfile.ZipFile(dest) as zf:
        names = zf.namelist()
    assert "logs/reader.log" in names
    assert not any(n.endswith(".zip") or n.endswith(".part") for n in names)


def test_extra_json_names_are_sanitised(tmp_path):
    dest = tmp_path / "bundle.zip"
    support_bundle.build_support_bundle(dest, tmp_path, tmp_path / "nologs", {"../evil name": {"a": 1}})
    with zipfile.ZipFile(dest) as zf:
        assert ".._evil_name.json" in zf.namelist()


def test_unwritable_destination_raises_and_leaves_no_partial(tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(support_bundle.zipfile.ZipFile, "writestr", boom)
    dest = tmp_path / "bundle.zip"
    with pytest.raises(OSError):
        support_bundle.build_support_bundle(dest, tmp_path, tmp_path, {"a": 1})
    assert not dest.exists()
    assert not list(tmp_path.glob("*.part"))
