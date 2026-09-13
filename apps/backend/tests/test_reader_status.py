"""Reader status protocol, backend side (PLAN-WINDOWS-NONTECHIE contract §2.1-2.6).

Covers POST/GET /reader/status, SSE reader_status publishing rules,
staleness, discovered_ip persistence, the command queue, POST /reader/discover,
POST /reader/restart, the desktop/version config fields and the cross-thread
SSE delivery fix.

Each test reloads the app module so module state (hub, storage, subscribers)
is fresh. The clock is a fake injected via ``app._monotonic``; no test sleeps
for real beyond short polling loops.
"""
from __future__ import annotations

import asyncio
import contextlib
import importlib
import logging
import threading
import time

import pytest
from fastapi.testclient import TestClient


READER_IP = "192.168.178.22"


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class FakeController:
    def __init__(self, status=None, restart_error=None, status_error=None):
        self.restart_calls = 0
        self._status = status or {
            "running": True, "pid": 4242, "restart_count": 1, "last_exit_code": None,
        }
        self._restart_error = restart_error
        self._status_error = status_error

    def restart(self) -> None:
        self.restart_calls += 1
        if self._restart_error is not None:
            raise self._restart_error

    def status(self) -> dict:
        if self._status_error is not None:
            raise self._status_error
        return dict(self._status)


def _reload_app(tmp_path, monkeypatch, env=None, stale_check_interval_s: float = 3600.0):
    monkeypatch.setenv("RACETAG_DATA_DIR", str(tmp_path))
    for var in ("RACETAG_API_KEY", "READER_IP", "RACETAG_VERSION"):
        monkeypatch.delenv(var, raising=False)
    for var, value in (env or {}).items():
        monkeypatch.setenv(var, value)
    import app as app_module
    importlib.reload(app_module)
    clock = FakeClock()
    monkeypatch.setattr(app_module, "_monotonic", clock)
    # The staleness thread would otherwise race the tests that call
    # _check_reader_stale() directly; one test below exercises the thread.
    monkeypatch.setattr(app_module, "_READER_STALE_CHECK_INTERVAL_S", stale_check_interval_s)
    return app_module, clock


@pytest.fixture()
def fresh_app(tmp_path, monkeypatch):
    app_module, clock = _reload_app(tmp_path, monkeypatch)
    with TestClient(app_module.app) as client:
        yield client, app_module, clock


@contextlib.contextmanager
def captured_frames(app_module):
    """Legacy list subscriber collecting every published SSE payload."""
    buf: list = []
    with app_module._subscribers_lock:
        app_module.subscribers.append(buf)
    try:
        yield buf
    finally:
        with app_module._subscribers_lock:
            app_module.subscribers.remove(buf)


def reader_frames(buf):
    return [f for f in buf if f.get("type") == "reader_status"]


def hb(**overrides) -> dict:
    """A complete heartbeat body as the reader-service sends it (§2.1)."""
    body = {
        "state": "active",
        "ip": READER_IP,
        "target_source": "config",
        "serial": "00179E123456",
        "antennas": [1, 2],
        "antenna_power": 250,
        "antenna_reads": {"1": 17, "2": 9},
        "last_event_at": "2026-09-13T10:00:00.000Z",
        "connected_since": "2026-09-13T09:58:00.000Z",
        "error": None,
        "consecutive_failures": 0,
        "next_retry_s": None,
        "candidates": [],
        "discovered_ip": None,
        "discovery": None,
        "reader_service_version": "0.2.0",
        "pid": 12345,
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# GET /reader/status
# ---------------------------------------------------------------------------

def test_get_status_before_any_heartbeat_is_unknown(fresh_app):
    client, _, _ = fresh_app
    resp = client.get("/reader/status")
    assert resp.status_code == 200
    assert resp.json() == {
        "state": "unknown",
        "ip": None,
        "target_source": None,
        "serial": None,
        "antennas": [],
        "antenna_power": None,
        "antenna_reads": {},
        "last_event_at": None,
        "connected_since": None,
        "error": None,
        "consecutive_failures": None,
        "next_retry_s": None,
        "candidates": [],
        "discovered_ip": None,
        "reader_service_version": None,
        "pid": None,
        "updated_at": None,
        "age_s": None,
        "supervisor": None,
    }


def test_post_status_stores_and_get_returns_it(fresh_app):
    client, _, clock = fresh_app
    body = hb(candidates=[{"ip": READER_IP, "serial": "00179E123456", "source": "arp"}])
    resp = client.post("/reader/status", json=body)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"config": {"reader_ip": None, "antenna_power": None}, "command": None}

    clock.advance(0.8)
    data = client.get("/reader/status").json()
    expected = dict(body)
    del expected["discovery"]
    for key, value in expected.items():
        assert data[key] == value, key
    assert "discovery" not in data
    assert data["age_s"] == 0.8
    assert data["updated_at"].endswith("Z")
    assert data["supervisor"] is None


def test_heartbeat_reply_carries_current_config(fresh_app):
    """Deviation 0.1: config changes reach the reader-service via the reply."""
    client, _, _ = fresh_app
    assert client.patch(
        "/config", json={"reader_ip": "192.168.178.40", "antenna_power": 280},
    ).status_code == 200
    reply = client.post("/reader/status", json=hb()).json()
    assert reply["config"] == {"reader_ip": "192.168.178.40", "antenna_power": 280}


def test_heartbeat_reply_falls_back_to_reader_ip_env(tmp_path, monkeypatch):
    app_module, _ = _reload_app(tmp_path, monkeypatch, env={"READER_IP": "10.1.2.3"})
    with TestClient(app_module.app) as client:
        reply = client.post("/reader/status", json=hb()).json()
    assert reply["config"]["reader_ip"] == "10.1.2.3"


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("body", [
    {"state": "unknown"},
    {"state": "banana"},
    {"state": 5},
    {"state": None},
    {"ip": READER_IP},
    [],
    "active",
])
def test_invalid_state_or_body_is_rejected(fresh_app, body):
    client, _, _ = fresh_app
    resp = client.post("/reader/status", json=body)
    assert resp.status_code == 422, resp.text
    assert client.get("/reader/status").json()["state"] == "unknown"


def test_minimal_heartbeat_with_only_state_is_accepted(fresh_app):
    client, _, _ = fresh_app
    resp = client.post("/reader/status", json={"state": "searching"})
    assert resp.status_code == 200, resp.text
    data = client.get("/reader/status").json()
    assert data["state"] == "searching"
    assert data["antennas"] == [] and data["candidates"] == [] and data["antenna_reads"] == {}
    assert data["consecutive_failures"] == 0


def test_malformed_fields_are_sanitised_not_rejected(fresh_app):
    client, app_module, _ = fresh_app
    body = hb(
        state="connecting",
        ip={"not": "a string"},
        target_source="moon",
        serial=["x"],
        antennas=[2, "1", "x", 0, 99, True, 2.0, 2.5, None],
        antenna_power="loud",
        antenna_reads={"1": 17, "2": "9", "x": 1, "3": -4, "4": True, "5": None},
        last_event_at=12,
        error={"a": 1},
        consecutive_failures=-3,
        next_retry_s="soon",
        candidates=[
            {"ip": READER_IP, "serial": "00179E123456", "source": "arp"},
            {"ip": "999.1.1.1", "serial": "BAD"},
            "not-a-dict",
            {"ip": READER_IP, "serial": "DUP", "source": "sweep"},
            {"ip": "10.0.0.5", "serial": None, "source": "weird"},
            {"serial": "no-ip"},
        ],
        discovered_ip="not-an-ip",
        discovery="bad",
        reader_service_version=None,
        pid="abc",
    )
    resp = client.post("/reader/status", json=body)
    assert resp.status_code == 200, resp.text
    data = client.get("/reader/status").json()
    assert data["state"] == "connecting"
    assert data["ip"] is None
    assert data["target_source"] is None
    assert data["serial"] is None
    assert data["antennas"] == [1, 2]
    assert data["antenna_power"] is None
    assert data["antenna_reads"] == {"1": 17, "2": 9}
    assert data["last_event_at"] == "12"
    assert data["error"] is None
    assert data["consecutive_failures"] == 0
    assert data["next_retry_s"] is None
    assert data["candidates"] == [
        {"ip": READER_IP, "serial": "00179E123456", "source": "arp"},
        {"ip": "10.0.0.5", "serial": None, "source": None},
    ]
    assert data["discovered_ip"] is None
    assert data["pid"] is None
    # An invalid discovered_ip must never be persisted.
    assert app_module.config_store.get_reader_ip() is None


def test_overlong_strings_are_truncated(fresh_app):
    client, _, _ = fresh_app
    assert client.post("/reader/status", json=hb(error="E" * 5000, serial="S" * 500)).status_code == 200
    data = client.get("/reader/status").json()
    assert len(data["error"]) == 500
    assert len(data["serial"]) == 64


def test_status_endpoint_requires_api_key_when_configured(tmp_path, monkeypatch):
    app_module, _ = _reload_app(tmp_path, monkeypatch, env={"RACETAG_API_KEY": "secret"})
    with TestClient(app_module.app) as client:
        assert client.post("/reader/status", json=hb()).status_code == 401
        resp = client.post("/reader/status", json=hb(), headers={"X-API-Key": "secret"})
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# SSE publishing rules (§2.2 rule 3)
# ---------------------------------------------------------------------------

def test_first_heartbeat_publishes_full_status_frame(fresh_app):
    client, app_module, _ = fresh_app
    app_module.app.state.reader_controller = FakeController()
    with captured_frames(app_module) as buf:
        client.post("/reader/status", json=hb())
    frames = reader_frames(buf)
    assert len(frames) == 1
    frame = frames[0]
    assert frame["state"] == "active"
    assert frame["ip"] == READER_IP
    assert frame["antenna_reads"] == {"1": 17, "2": 9}
    assert frame["age_s"] == 0.0
    assert frame["updated_at"] is not None
    assert frame["supervisor"]["pid"] == 4242
    assert "discovery" not in frame
    # Same shape as GET /reader/status plus the type key.
    assert set(frame) - {"type"} == set(client.get("/reader/status").json())


def test_identical_heartbeat_does_not_republish_within_refresh(fresh_app):
    client, app_module, clock = fresh_app
    with captured_frames(app_module) as buf:
        client.post("/reader/status", json=hb())
        for _ in range(2):
            clock.advance(2.0)
            client.post("/reader/status", json=hb())
    assert len(reader_frames(buf)) == 1


@pytest.mark.parametrize("field,value", [
    ("state", "lost"),
    ("ip", "192.168.178.23"),
    ("serial", "00179E999999"),
    ("antennas", [1]),
    ("error", "CONTROL connect timeout"),
    ("candidates", [{"ip": "192.168.178.30", "serial": "X1", "source": "sweep"}]),
    ("consecutive_failures", 3),
])
def test_change_of_key_field_publishes_immediately(fresh_app, field, value):
    client, app_module, clock = fresh_app
    with captured_frames(app_module) as buf:
        client.post("/reader/status", json=hb())
        clock.advance(0.1)
        client.post("/reader/status", json=hb(**{field: value}))
    frames = reader_frames(buf)
    assert len(frames) == 2
    assert frames[-1][field] == value


def test_reads_only_change_is_refreshed_after_five_seconds(fresh_app):
    client, app_module, clock = fresh_app
    with captured_frames(app_module) as buf:
        client.post("/reader/status", json=hb())
        clock.advance(2.0)
        client.post("/reader/status", json=hb(antenna_reads={"1": 20, "2": 9}))
        clock.advance(2.9)
        client.post("/reader/status", json=hb(antenna_reads={"1": 25, "2": 9},
                                              last_event_at="2026-09-13T10:00:04.000Z"))
        assert len(reader_frames(buf)) == 1
        clock.advance(0.1)  # 5.0 s since the last frame
        client.post("/reader/status", json=hb(antenna_reads={"1": 30, "2": 11},
                                              last_event_at="2026-09-13T10:00:05.000Z"))
    frames = reader_frames(buf)
    assert len(frames) == 2
    assert frames[-1]["antenna_reads"] == {"1": 30, "2": 11}
    assert frames[-1]["last_event_at"] == "2026-09-13T10:00:05.000Z"


# ---------------------------------------------------------------------------
# Staleness (§2.2 rule 4)
# ---------------------------------------------------------------------------

def test_stale_after_six_seconds_publishes_unknown_once(fresh_app):
    client, app_module, clock = fresh_app
    with captured_frames(app_module) as buf:
        client.post("/reader/status", json=hb())
        clock.advance(5.9)
        assert app_module._check_reader_stale() is False
        assert client.get("/reader/status").json()["state"] == "active"

        clock.advance(0.1)
        assert app_module._check_reader_stale() is True
        clock.advance(3.0)
        assert app_module._check_reader_stale() is False

        frames = reader_frames(buf)
        assert [f["state"] for f in frames] == ["active", "unknown"]
        # Last known details stay visible alongside the unknown state.
        assert frames[-1]["ip"] == READER_IP

        data = client.get("/reader/status").json()
        assert data["state"] == "unknown"
        assert data["age_s"] == 9.0

        # A new heartbeat revives the status and publishes immediately.
        client.post("/reader/status", json=hb())
        frames = reader_frames(buf)
        assert [f["state"] for f in frames] == ["active", "unknown", "active"]
    assert client.get("/reader/status").json()["state"] == "active"


def test_get_reports_unknown_when_stale_even_before_the_check_runs(fresh_app):
    client, _, clock = fresh_app
    client.post("/reader/status", json=hb())
    clock.advance(6.5)
    assert client.get("/reader/status").json()["state"] == "unknown"


def test_no_stale_transition_without_any_heartbeat(fresh_app):
    _, app_module, clock = fresh_app
    with captured_frames(app_module) as buf:
        clock.advance(60)
        assert app_module._check_reader_stale() is False
    assert reader_frames(buf) == []


def test_stale_thread_not_started_at_import(tmp_path, monkeypatch):
    app_module, _ = _reload_app(tmp_path, monkeypatch)
    assert app_module._reader_stale_thread is None


def test_stale_thread_runs_between_startup_and_shutdown(tmp_path, monkeypatch):
    app_module, clock = _reload_app(tmp_path, monkeypatch, stale_check_interval_s=0.02)
    with TestClient(app_module.app) as client:
        thread = app_module._reader_stale_thread
        assert thread is not None and thread.is_alive() and thread.daemon
        with captured_frames(app_module) as buf:
            client.post("/reader/status", json=hb())
            clock.advance(7.0)
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline and len(reader_frames(buf)) < 2:
                time.sleep(0.01)
            states = [f["state"] for f in reader_frames(buf)]
        assert states == ["active", "unknown"]
    assert not thread.is_alive()
    assert app_module._reader_stale_thread is None


# ---------------------------------------------------------------------------
# discovered_ip persistence (§2.2 rule 1)
# ---------------------------------------------------------------------------

def test_discovered_ip_is_persisted_before_reply_and_logged(fresh_app, caplog):
    client, app_module, _ = fresh_app
    client.patch("/config", json={"reader_ip": READER_IP})
    caplog.set_level(logging.INFO, logger="racetag.backend")

    reply = client.post("/reader/status", json=hb(
        ip="192.168.178.50", target_source="discovery", discovered_ip="192.168.178.50",
    )).json()
    assert reply["config"]["reader_ip"] == "192.168.178.50"
    assert client.get("/config").json()["reader_ip"] == "192.168.178.50"

    messages = [
        r.getMessage() for r in caplog.records
        if r.name == "racetag.backend" and "via discovery" in r.getMessage()
    ]
    assert messages == [f"reader_ip changed via discovery: {READER_IP} -> 192.168.178.50"]

    # Repeating the same discovered_ip (reader-service not yet cleared it)
    # neither re-persists nor logs again.
    client.post("/reader/status", json=hb(discovered_ip="192.168.178.50"))
    assert len([r for r in caplog.records if "via discovery" in r.getMessage()]) == 1


def test_discovered_ip_persists_when_nothing_was_configured(fresh_app):
    client, app_module, _ = fresh_app
    reply = client.post("/reader/status", json=hb(state="connecting", discovered_ip="10.0.0.7")).json()
    assert reply["config"]["reader_ip"] == "10.0.0.7"
    assert app_module.config_store.get_reader_ip() == "10.0.0.7"


def test_repeated_discovered_ip_does_not_override_operator_ip(fresh_app):
    """The reader-service repeats discovered_ip until its connection thread
    handles the echo. A PATCH saved in that gap must survive the repeats."""
    client, app_module, _ = fresh_app
    discovered = "192.168.178.30"
    first = client.post("/reader/status", json=hb(
        state="connecting", ip=discovered, target_source="discovery", discovered_ip=discovered,
    )).json()
    assert first["config"]["reader_ip"] == discovered

    assert client.patch("/config", json={"reader_ip": READER_IP}).status_code == 200

    for _ in range(3):
        reply = client.post("/reader/status", json=hb(
            state="connecting", ip=discovered, target_source="discovery", discovered_ip=discovered,
        )).json()
        assert reply["config"]["reader_ip"] == READER_IP
    assert client.get("/config").json()["reader_ip"] == READER_IP
    assert app_module.config_store.get_reader_ip() == READER_IP


def test_rediscovery_after_cleared_discovered_ip_is_persisted_again(fresh_app):
    client, _, _ = fresh_app
    discovered = "192.168.178.30"
    client.post("/reader/status", json=hb(discovered_ip=discovered))
    client.patch("/config", json={"reader_ip": READER_IP})
    # The reader-service handled the echo and cleared discovered_ip ...
    client.post("/reader/status", json=hb(discovered_ip=None))
    assert client.get("/config").json()["reader_ip"] == READER_IP
    # ... and a later discovery that lands on the same reader again counts.
    reply = client.post("/reader/status", json=hb(discovered_ip=discovered)).json()
    assert reply["config"]["reader_ip"] == discovered
    assert client.get("/config").json()["reader_ip"] == discovered


def test_same_discovered_ip_from_new_reader_service_process_is_persisted(fresh_app):
    client, _, _ = fresh_app
    discovered = "192.168.178.30"
    client.post("/reader/status", json=hb(pid=100, discovered_ip=discovered))
    client.patch("/config", json={"reader_ip": READER_IP})
    client.post("/reader/status", json=hb(pid=100, discovered_ip=discovered))
    assert client.get("/config").json()["reader_ip"] == READER_IP
    reply = client.post("/reader/status", json=hb(pid=200, discovered_ip=discovered)).json()
    assert reply["config"]["reader_ip"] == discovered


def test_discovered_ip_matching_saved_ip_still_blocks_later_repeat(fresh_app):
    """A discovery equal to the saved IP changes nothing, but its repeats must
    not overwrite an IP the operator enters afterwards either."""
    client, _, _ = fresh_app
    discovered = "192.168.178.30"
    client.patch("/config", json={"reader_ip": discovered})
    client.post("/reader/status", json=hb(discovered_ip=discovered))
    client.patch("/config", json={"reader_ip": READER_IP})
    client.post("/reader/status", json=hb(discovered_ip=discovered))
    assert client.get("/config").json()["reader_ip"] == READER_IP


# ---------------------------------------------------------------------------
# POST /reader/restart and the command queue (§2.1 reply, §2.5)
# ---------------------------------------------------------------------------

def test_restart_without_controller_queues_reconnect_delivered_once(fresh_app):
    client, _, _ = fresh_app
    resp = client.post("/reader/restart")
    assert resp.status_code == 202
    assert resp.json() == {"accepted": True, "via": "command"}
    # A second click while the first is still queued adds nothing.
    assert client.post("/reader/restart").json() == {"accepted": True, "via": "command"}

    first = client.post("/reader/status", json=hb()).json()["command"]
    assert first is not None and first["type"] == "reconnect" and isinstance(first["id"], int)
    assert client.post("/reader/status", json=hb()).json()["command"] is None

    client.post("/reader/restart")
    second = client.post("/reader/status", json=hb()).json()["command"]
    assert second["type"] == "reconnect"
    assert second["id"] > first["id"]


def test_stopped_heartbeat_does_not_consume_a_command(fresh_app):
    client, _, _ = fresh_app
    client.post("/reader/restart")
    assert client.post("/reader/status", json=hb(state="stopped")).json()["command"] is None
    command = client.post("/reader/status", json=hb(state="connecting")).json()["command"]
    assert command["type"] == "reconnect"


def test_restart_with_controller_uses_supervisor(fresh_app):
    client, app_module, _ = fresh_app
    controller = FakeController()
    app_module.app.state.reader_controller = controller
    resp = client.post("/reader/restart")
    assert resp.status_code == 202
    assert resp.json() == {"accepted": True, "via": "supervisor"}
    assert controller.restart_calls == 1
    assert client.post("/reader/status", json=hb()).json()["command"] is None


def test_restart_falls_back_to_command_when_controller_fails(fresh_app):
    client, app_module, _ = fresh_app
    controller = FakeController(restart_error=RuntimeError("boom"))
    app_module.app.state.reader_controller = controller
    resp = client.post("/reader/restart")
    assert resp.status_code == 202
    assert resp.json() == {"accepted": True, "via": "command"}
    assert controller.restart_calls == 1
    assert client.post("/reader/status", json=hb()).json()["command"]["type"] == "reconnect"


# ---------------------------------------------------------------------------
# POST /reader/discover (§2.4)
# ---------------------------------------------------------------------------

def _post_in_thread(client, path):
    result: dict = {}

    def run():
        result["resp"] = client.post(path)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, result


def _wait_for_command(client, body, timeout_s=3.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        command = client.post("/reader/status", json=body).json()["command"]
        if command is not None:
            return command
        time.sleep(0.01)
    raise AssertionError("no command delivered")


def test_discover_without_heartbeat_is_unavailable_immediately(fresh_app, monkeypatch):
    client, app_module, _ = fresh_app
    monkeypatch.setattr(app_module, "_READER_DISCOVER_TIMEOUT_S", 5.0)
    started = time.monotonic()
    resp = client.post("/reader/discover")
    assert time.monotonic() - started < 1.0
    assert resp.status_code == 200
    assert resp.json() == {"candidates": [], "error": "reader_service_unavailable"}
    # Nothing was queued for a reader-service that is not there.
    assert client.post("/reader/status", json=hb()).json()["command"] is None


def test_discover_while_reader_service_boots_waits_for_first_heartbeat(fresh_app, monkeypatch):
    """No heartbeat yet, but the supervisor reports the child as running: the
    reader-service is still starting, so the command waits for it."""
    client, app_module, _ = fresh_app
    monkeypatch.setattr(app_module, "_READER_DISCOVER_TIMEOUT_S", 5.0)
    app_module.app.state.reader_controller = FakeController()

    thread, result = _post_in_thread(client, "/reader/discover")
    try:
        hub = app_module._reader_hub
        deadline = time.monotonic() + 3.0
        while hub._discovery is None:
            if time.monotonic() > deadline:
                raise AssertionError("discover request was not queued")
            time.sleep(0.01)
        assert "resp" not in result

        # The first heartbeat of the freshly started reader-service picks it up.
        command = client.post("/reader/status", json=hb(state="searching", ip=None)).json()["command"]
        assert command is not None and command["type"] == "discover"
        candidates = [{"ip": "192.168.178.30", "serial": "00179E123456", "source": "sweep"}]
        client.post("/reader/status", json=hb(state="searching", ip=None, discovery={
            "request_id": command["id"], "candidates": candidates, "error": None,
        }))
    finally:
        thread.join(timeout=6.0)
    assert not thread.is_alive()
    assert result["resp"].status_code == 200
    assert result["resp"].json() == {"candidates": candidates, "error": None}


def test_discover_while_booting_times_out_when_no_heartbeat_arrives(fresh_app, monkeypatch):
    client, app_module, _ = fresh_app
    monkeypatch.setattr(app_module, "_READER_DISCOVER_TIMEOUT_S", 0.05)
    app_module.app.state.reader_controller = FakeController()
    assert client.post("/reader/discover").json() == {"candidates": [], "error": "timeout"}
    # The undelivered command was dropped with the last waiter.
    assert client.post("/reader/status", json=hb()).json()["command"] is None


def test_discover_without_heartbeat_and_stopped_child_is_unavailable(fresh_app, monkeypatch):
    client, app_module, _ = fresh_app
    monkeypatch.setattr(app_module, "_READER_DISCOVER_TIMEOUT_S", 5.0)
    app_module.app.state.reader_controller = FakeController(status={
        "running": False, "pid": None, "restart_count": 3, "last_exit_code": 1,
    })
    started = time.monotonic()
    assert client.post("/reader/discover").json() == {
        "candidates": [], "error": "reader_service_unavailable",
    }
    assert time.monotonic() - started < 1.0
    assert client.post("/reader/status", json=hb()).json()["command"] is None


def test_discover_when_stale_is_unavailable_even_if_child_runs(fresh_app, monkeypatch):
    client, app_module, clock = fresh_app
    monkeypatch.setattr(app_module, "_READER_DISCOVER_TIMEOUT_S", 5.0)
    app_module.app.state.reader_controller = FakeController()
    client.post("/reader/status", json=hb())
    clock.advance(6.0)
    started = time.monotonic()
    assert client.post("/reader/discover").json() == {
        "candidates": [], "error": "reader_service_unavailable",
    }
    assert time.monotonic() - started < 1.0


def test_discover_when_stale_is_unavailable(fresh_app):
    client, _, clock = fresh_app
    client.post("/reader/status", json=hb())
    clock.advance(6.0)
    assert client.post("/reader/discover").json() == {
        "candidates": [], "error": "reader_service_unavailable",
    }


def test_discover_times_out_and_drops_undelivered_command(fresh_app, monkeypatch):
    client, app_module, _ = fresh_app
    monkeypatch.setattr(app_module, "_READER_DISCOVER_TIMEOUT_S", 0.05)
    client.post("/reader/status", json=hb())
    resp = client.post("/reader/discover")
    assert resp.status_code == 200
    assert resp.json() == {"candidates": [], "error": "timeout"}
    assert client.post("/reader/status", json=hb()).json()["command"] is None


def test_discover_returns_result_from_matching_heartbeat(fresh_app, monkeypatch):
    client, app_module, _ = fresh_app
    monkeypatch.setattr(app_module, "_READER_DISCOVER_TIMEOUT_S", 5.0)
    client.post("/reader/status", json=hb())

    thread, result = _post_in_thread(client, "/reader/discover")
    try:
        command = _wait_for_command(client, hb())
        assert command["type"] == "discover"
        # Delivered once.
        assert client.post("/reader/status", json=hb()).json()["command"] is None

        # A result for another request id does not wake the waiter.
        client.post("/reader/status", json=hb(discovery={
            "request_id": command["id"] + 100,
            "candidates": [{"ip": "10.9.9.9", "serial": "WRONG", "source": "sweep"}],
            "error": None,
        }))
        candidates = [
            {"ip": READER_IP, "serial": "00179E123456", "source": "connected"},
            {"ip": "192.168.178.60", "serial": "00179E654321", "source": "sweep"},
        ]
        client.post("/reader/status", json=hb(candidates=candidates, discovery={
            "request_id": command["id"], "candidates": candidates, "error": None,
        }))
    finally:
        thread.join(timeout=6.0)
    assert not thread.is_alive()
    resp = result["resp"]
    assert resp.status_code == 200
    assert resp.json() == {"candidates": candidates, "error": None}
    assert client.get("/reader/status").json()["candidates"] == candidates


def test_concurrent_discover_requests_share_one_command(fresh_app, monkeypatch):
    client, app_module, _ = fresh_app
    monkeypatch.setattr(app_module, "_READER_DISCOVER_TIMEOUT_S", 5.0)
    client.post("/reader/status", json=hb())

    threads = [_post_in_thread(client, "/reader/discover") for _ in range(2)]
    try:
        hub = app_module._reader_hub
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            pending = hub._discovery
            if pending is not None and pending.waiters == 2:
                break
            time.sleep(0.01)
        else:
            raise AssertionError("discover requests did not both start waiting")

        command = _wait_for_command(client, hb())
        assert client.post("/reader/status", json=hb()).json()["command"] is None
        client.post("/reader/status", json=hb(discovery={
            "request_id": command["id"], "candidates": [], "error": "no reader found",
        }))
    finally:
        for thread, _ in threads:
            thread.join(timeout=6.0)
    for thread, result in threads:
        assert not thread.is_alive()
        assert result["resp"].json() == {"candidates": [], "error": "no reader found"}


# ---------------------------------------------------------------------------
# Supervisor block and /config desktop + version (§2.3, §2.6)
# ---------------------------------------------------------------------------

def test_get_status_includes_supervisor_status(fresh_app):
    client, app_module, _ = fresh_app
    app_module.app.state.reader_controller = FakeController()
    assert client.get("/reader/status").json()["supervisor"] == {
        "running": True, "pid": 4242, "restart_count": 1, "last_exit_code": None,
    }


def test_failing_supervisor_status_does_not_break_endpoints(fresh_app):
    client, app_module, _ = fresh_app
    app_module.app.state.reader_controller = FakeController(status_error=RuntimeError("x"))
    assert client.post("/reader/status", json=hb()).status_code == 200
    resp = client.get("/reader/status")
    assert resp.status_code == 200
    assert resp.json()["supervisor"] is None


def test_config_desktop_and_version_fields(fresh_app, monkeypatch):
    client, app_module, _ = fresh_app
    data = client.get("/config").json()
    assert data["desktop"] is False
    assert data["version"] is None

    monkeypatch.setenv("RACETAG_VERSION", "0.3.0")
    app_module.app.state.reader_controller = FakeController()
    data = client.get("/config").json()
    assert data["desktop"] is True
    assert data["version"] == "0.3.0"


def test_patch_config_never_restarts_the_reader(fresh_app):
    client, app_module, _ = fresh_app
    controller = FakeController()
    app_module.app.state.reader_controller = controller
    resp = client.patch("/config", json={"reader_ip": "192.168.178.77", "antenna_power": 200})
    assert resp.status_code == 200, resp.text
    assert resp.json()["desktop"] is True
    assert controller.restart_calls == 0


def test_patch_config_reader_ip_null_clears_absent_keeps(fresh_app):
    client, _, _ = fresh_app
    assert client.patch("/config", json={"reader_ip": READER_IP}).json()["reader_ip"] == READER_IP
    assert client.patch("/config", json={"total_laps": 7}).json()["reader_ip"] == READER_IP
    assert client.patch("/config", json={"reader_ip": None}).json()["reader_ip"] is None
    assert client.get("/config").json()["reader_ip"] is None


# ---------------------------------------------------------------------------
# SSE cross-thread delivery (§2.2 rule 6)
# ---------------------------------------------------------------------------

def test_publish_from_foreign_thread_reaches_stream_promptly(fresh_app):
    """A publish from a non-loop thread (sync route, staleness thread) must
    wake a /stream client that is already waiting, not only at the next
    keepalive tick."""
    _, app_module, _ = fresh_app

    async def scenario():
        response = await app_module.stream_events()
        body = response.body_iterator
        first_frame = asyncio.ensure_future(body.__anext__())
        try:
            loop = asyncio.get_running_loop()
            # Let the generator start and block inside queue.get().
            for _ in range(5):
                await asyncio.sleep(0)
            assert not first_frame.done()

            publish_delay_s = 0.1

            def publish_later():
                # Publish only once the loop is idle in its selector; a
                # publish that lands before the loop sleeps would be picked
                # up promptly even by a thread-unsafe put_nowait().
                time.sleep(publish_delay_s)
                app_module._publish({"type": "probe", "n": 1})

            started = loop.time()
            publisher = threading.Thread(target=publish_later)
            publisher.start()
            frame = await asyncio.wait_for(first_frame, timeout=2.0)
            elapsed = loop.time() - started - publish_delay_s
            publisher.join(timeout=1.0)
            return frame, elapsed
        finally:
            if not first_frame.done():
                first_frame.cancel()
                with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
                    await first_frame
            await body.aclose()

    frame, elapsed = asyncio.run(scenario())
    assert frame == 'data: {"type":"probe","n":1}\n\n'
    assert elapsed < 0.4
    # The stream's finally block unregistered its queue.
    with app_module._subscribers_lock:
        assert not any(isinstance(s, app_module._QueueSubscriber) for s in app_module.subscribers)
